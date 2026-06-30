# ui_dialogs.py
import base64
import imagehash
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QSpinBox,
                               QDialogButtonBox, QGroupBox, QFormLayout, QComboBox,
                               QCheckBox, QWidget, QHBoxLayout, QScrollArea, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QStyle, QMessageBox,
                               QSlider, QToolButton)
from PySide6.QtCore import Qt, QPoint, Slot, Signal, QUrl, QSize
from PySide6.QtGui import (QTextCursor, QPixmap, QImage, QPainter, QFont, QColor, QPen,
                           QPainterPath, QGuiApplication, QIcon, QPalette)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from ssmm import config
from ssmm.models import Slide
from ssmm.ui_main import wrap_cell_widget
from ssmm.ui_helpers import generate_waveform_pixmap

COL_NEW_THUMB = 0
COL_SOURCE_COMBO = 1
COL_SOURCE_THUMB = 2
COL_SOURCE_INFO = 3

class InstallProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Installing FFmpeg..."))
        self.setMinimumSize(600, 400)
        
        self.layout = QVBoxLayout(self)
        self.status_label = QLabel(self.tr("Starting installation..."))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        
        self.layout.addWidget(self.status_label)
        self.layout.addWidget(self.log_view)
        
        self.setModal(True)
        self._is_running = True

    def append_log(self, text):
        self.log_view.appendPlainText(text)
        self.log_view.moveCursor(QTextCursor.End)

    def closeEvent(self, event):
        if self._is_running:
            event.ignore()
        else:
            super().closeEvent(event)

class SelectSlideDialog(QDialog):
    def __init__(self, max_slides, parent=None):
        super().__init__(parent)
        # Free the dialog once closed; deleteLater defers until the event loop,
        # so reading values right after exec() is safe.
        self.finished.connect(self.deleteLater)
        self.setWindowTitle(self.tr("Select Slide for Preview"))
        
        layout = QVBoxLayout(self)
        
        info_label = QLabel(
            self.tr("<b>Preview Limitations:</b><br>"
            "To speed up the preview, the following elements are not applied:"
            "<ul><li>Picture-in-Picture (PinP) videos from adjacent slides in transition sections.</li>"
            "<li>Audio normalization (loudnorm).</li>"
            "<li>Embedded chapter markers and YouTube chapter file export.</li></ul>"
            "These will be applied correctly in the final video.")
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        spinbox_layout = QHBoxLayout()
        spinbox_layout.addWidget(QLabel(self.tr("Select Slide Number:")))
        self.spinbox = QSpinBox()
        self.spinbox.setMinimum(1)
        self.spinbox.setMaximum(max_slides)
        spinbox_layout.addWidget(self.spinbox)
        layout.addLayout(spinbox_layout)

        self.include_intervals_checkbox = QCheckBox(self.tr("Include intervals before and after the slide"))
        self.include_intervals_checkbox.setChecked(True)
        self.include_intervals_checkbox.setToolTip(
            self.tr("If unchecked, only the selected slide's content will be rendered.\n"
            "This is faster and useful for exporting a single slide video.")
        )
        layout.addWidget(self.include_intervals_checkbox)
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
    def get_selected_slide(self):
        return self.spinbox.value()

    def get_include_intervals(self) -> bool:
        return self.include_intervals_checkbox.isChecked()

class PageMappingDialog(QDialog): 
    def __init__(self, old_slides: list[Slide], new_pdf_info: dict, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)
        self.setWindowTitle(self.tr("Map Slide Settings to New PDF Structure"))
        self.setFixedSize(850, 700)

        self.old_slides = old_slides
        self.new_pdf_info = new_pdf_info

        self.old_hashes = [s.p_hash for s in self.old_slides]
        self.new_hashes = self.new_pdf_info["p_hashes"]
        self.initial_mapping = self._create_initial_mapping()
        
        self.old_pixmaps = self._load_pixmaps_from_b64([s.thumbnail_b64 for s in self.old_slides])
        self.new_pixmaps = self._load_pixmaps_from_b64(self.new_pdf_info["thumbnails_b64"])

        main_layout = QVBoxLayout(self)
        info_label = QLabel(
            self.tr("The PDF structure has changed. Review the automatic mapping and correct any errors.\n"
            "Use the dropdown menu in each row to select the source of settings for the new page.")
        )
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        self.table = QTableWidget()
        main_layout.addWidget(self.table)
        
        self.setup_table()

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _create_numbered_thumbnail(self, base_pixmap: QPixmap, number: int) -> QPixmap:
        if base_pixmap.isNull():
            return QPixmap()

        numbered_pixmap = base_pixmap.copy()
        
        painter = QPainter(numbered_pixmap)
        
        font = QFont()
        font.setPointSizeF(50)
        font.setBold(True)
        painter.setFont(font)
        
        text = str(number)
        fm = painter.fontMetrics()
        text_rect = fm.boundingRect(text)
        
        x = (numbered_pixmap.width() - text_rect.width()) / 2
        y = (numbered_pixmap.height() - text_rect.height()) / 2 + text_rect.height()

        path = QPainterPath()
        path.addText(x, y, font, text)

        pen = QPen(Qt.black)
        pen.setWidth(4)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)
        painter.drawPath(path)
        
        painter.end()
        
        return numbered_pixmap

    def _load_pixmaps_from_b64(self, b64_list: list[str]) -> dict:
        pixmaps = {}
        for i, b64_str in enumerate(b64_list):
            if b64_str:
                try:
                    byte_data = base64.b64decode(b64_str)
                    pixmaps[i] = QPixmap.fromImage(QImage.fromData(byte_data, "PNG"))
                except Exception:
                    pass
        return pixmaps

    def _create_initial_mapping(self) -> dict:
        mapping = {}
        used_old_indices = set()

        # Pair each parsed hash with its original page index so skipped hashless
        # pages don't renumber the indices used for mapping.
        old_ihashes = [(j, imagehash.hex_to_hash(h)) for j, h in enumerate(self.old_hashes) if h]
        new_ihashes = [(i, imagehash.hex_to_hash(h)) for i, h in enumerate(self.new_hashes) if h]

        for new_idx, new_hash in new_ihashes:
            best_match_idx = -1
            min_dist = config.HAMMING_DISTANCE_THRESHOLD + 1

            for old_idx, old_hash in old_ihashes:
                if old_idx in used_old_indices:
                    continue

                dist = new_hash - old_hash
                if dist < min_dist:
                    min_dist = dist
                    best_match_idx = old_idx

            if min_dist <= config.HAMMING_DISTANCE_THRESHOLD:
                mapping[new_idx] = best_match_idx
                used_old_indices.add(best_match_idx)

        return mapping

    def _get_material_display_info(self, slide: Slide) -> tuple[str, str]:
        if slide.is_video:
            return self.tr("Movie"), slide.filename or ""
        elif slide.filename and slide.filename != config.SILENT_MATERIAL_NAME:
            return self.tr("Audio"), slide.filename
        elif slide.filename == config.SILENT_MATERIAL_NAME:
            return self.tr("Silent"), ""
        else:
            return self.tr("Unassigned"), ""


    def setup_table(self):
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([self.tr("New Page Thumbnail"), self.tr("Apply Settings From"), self.tr("Source Thumbnail"), self.tr("Source Info")])
        self.table.setRowCount(self.new_pdf_info["page_count"])
        # Direct combo references, since wrapped combos aren't returned by cellWidget().
        self.source_combos = {}

        for i in range(self.new_pdf_info["page_count"]):

            # Column 0: new page thumbnail
            if i in self.new_pixmaps:
                numbered_thumb = self._create_numbered_thumbnail(self.new_pixmaps[i], i + 1)
                thumb_label = QLabel()
                thumb_label.setPixmap(numbered_thumb.scaledToHeight(90, Qt.SmoothTransformation))
                thumb_label.setAlignment(Qt.AlignCenter)
                self.table.setCellWidget(i, COL_NEW_THUMB, thumb_label)

            # Column 1: source selection combo box
            combo = QComboBox()
            combo.addItem(self.tr("Unmapped (New Page)"), userData=None)
            for j, old_slide in enumerate(self.old_slides):
                material_type, material_name = self._get_material_display_info(old_slide)
                display_text = self.tr("Old #{0} [{1}]").format(j + 1, material_type)
                if material_name:
                    display_text += f" {material_name}"
                combo.addItem(display_text, userData=j)
            combo.currentIndexChanged.connect(lambda index, row=i: self._on_source_changed(row, index))
            self.source_combos[i] = combo
            self.table.setCellWidget(i, COL_SOURCE_COMBO, wrap_cell_widget(combo))
            
            # Column 2: source thumbnail
            source_thumb_label = QLabel()
            source_thumb_label.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(i, COL_SOURCE_THUMB, source_thumb_label)

            # Column 3: source info label
            source_info_label = QLabel()
            source_info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            source_info_label.setWordWrap(True)
            self.table.setCellWidget(i, COL_SOURCE_INFO, source_info_label)
            
            # Apply the initial mapping and trigger the first update
            mapped_old_idx = self.initial_mapping.get(i)
            if mapped_old_idx is not None:
                combo.setCurrentIndex(combo.findData(mapped_old_idx))
            else:
                self._on_source_changed(i, 0)

        # --- Column Sizing and Styling ---
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        
        self.table.setColumnWidth(COL_NEW_THUMB, 150)
        self.table.setColumnWidth(COL_SOURCE_COMBO, 250)
        self.table.setColumnWidth(COL_SOURCE_THUMB, 150)
        self.table.setColumnWidth(COL_SOURCE_INFO, 200)
        
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)

    @Slot(int, int)
    def _on_source_changed(self, row: int, combo_index: int):
        combo = self.source_combos.get(row)
        source_thumb_label = self.table.cellWidget(row, COL_SOURCE_THUMB)
        source_info_label = self.table.cellWidget(row, COL_SOURCE_INFO)
        
        if not all([combo, source_thumb_label, source_info_label]):
            return

        old_page_index = combo.itemData(combo_index)
        
        if old_page_index is not None:
            old_slide = self.old_slides[old_page_index]
            material_type, material_name = self._get_material_display_info(old_slide)
            
            # Update info text
            info_html = f"<b>{material_type}</b>"
            if material_name:
                info_html += f":<br>{material_name}"
            source_info_label.setText(info_html)

            # Update source thumbnail
            if old_page_index in self.old_pixmaps:
                numbered_thumb = self._create_numbered_thumbnail(self.old_pixmaps[old_page_index], old_page_index + 1)
                source_thumb_label.setPixmap(numbered_thumb.scaledToHeight(90, Qt.SmoothTransformation))
            else:
                source_thumb_label.clear()
        else:
            # Clear both if "Unmapped" is selected
            source_info_label.setText(self.tr("<i>(Unmapped)</i>"))
            source_thumb_label.clear()

    def get_mapping(self) -> dict:
        mapping = {}
        for i in range(self.table.rowCount()):
            combo = self.source_combos.get(i)
            if combo:
                old_idx = combo.currentData()
                mapping[i] = old_idx
        return mapping


class EditEffectsDialog(QDialog):
    def __init__(self, current_effects, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)
        self.setWindowTitle(self.tr("Select PinP Effects"))
        main_layout = QVBoxLayout(self)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        main_layout.addWidget(self.info_label)

        self.checkboxes = {}
        self.group_boxes = {}
        
        for group_name in config.EFFECT_GROUPS.keys():
            group_box = QGroupBox(self.tr("{0} Group").format(group_name))
            if group_name in ["Shape", "Color"]:
                group_box.setTitle(self.tr("{0} (Select up to one)").format(group_name))
            else:
                group_box.setTitle(self.tr("{0} (Select any)").format(group_name))
            
            layout = QVBoxLayout()
            group_box.setLayout(layout)
            self.group_boxes[group_name] = {'box': group_box, 'layout': layout, 'keys': []}
            main_layout.addWidget(group_box)
        
        for key, name in config.VIDEO_EFFECT_MAP.items():
            if key == "None": continue
            for group, keys in config.EFFECT_GROUPS.items():
                if key in keys:
                    checkbox = QCheckBox(self.tr(name))
                    checkbox.setChecked(key in current_effects)
                    self.checkboxes[key] = checkbox
                    self.group_boxes[group]['layout'].addWidget(checkbox)
                    self.group_boxes[group]['keys'].append(key)
        
        for cb in self.checkboxes.values():
            cb.toggled.connect(self.on_checkbox_toggled)

        self.update_ui_states()
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def on_checkbox_toggled(self):
        if not self.sender().isChecked():
            self.update_ui_states()
            return

        sender_key = None
        for key, cb in self.checkboxes.items():
            if cb is self.sender():
                sender_key = key
                break
        if not sender_key: return

        for cb in self.checkboxes.values():
            cb.blockSignals(True)

        shape_group = config.EFFECT_GROUPS["Shape"]
        color_group = config.EFFECT_GROUPS["Color"]
        chroma_conflicts = color_group + ["Blur", "Pixelate"]

        if sender_key in shape_group:
            for key in shape_group:
                if key != sender_key: self.checkboxes[key].setChecked(False)
        
        if sender_key in color_group:
            for key in color_group:
                if key != sender_key: self.checkboxes[key].setChecked(False)
        
        if sender_key == "Chroma":
            for key in chroma_conflicts:
                if key in self.checkboxes: self.checkboxes[key].setChecked(False)
        elif sender_key in chroma_conflicts:
            self.checkboxes["Chroma"].setChecked(False)

        for cb in self.checkboxes.values():
            cb.blockSignals(False)

        self.update_ui_states()

    def update_ui_states(self):
        selected_count = len(self.get_selected_effects())
        if selected_count == 0:
            self.info_label.setText(self.tr("Select one or more effects."))
        elif selected_count == 1:
            self.info_label.setText(self.tr("1 effect selected."))
        else:
            self.info_label.setText(self.tr("{0} effects selected.").format(selected_count))
            
    def get_selected_effects(self):
        return [key for key, checkbox in self.checkboxes.items() if checkbox.isChecked()]

class EditSlidesDialog(QDialog):
    def __init__(self, slide_info_list, has_video_slides, is_mixed_selection, is_only_last_slide_selected, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

        slide_numbers = [s["number"] for s in slide_info_list]
        self.setWindowTitle(self.tr("Edit Selected Slides ({0} items)").format(len(slide_numbers)))
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout(self)

        if slide_info_list:
            numbers_group = QGroupBox(self.tr("Applying to Slides:"))
            numbers_layout = QVBoxLayout(numbers_group)
            
            color_map = {
                "movie": "#1E90FF",
                "audio": "#228B22",
                "silent": "#FFA500",
                "unassigned": "#FFA500"
            }

            html_parts = []
            for info in slide_info_list:
                color = color_map.get(info["type"], "#FFFFFF")
                html_parts.append(f'<span style="color:{color};"><b>{info["number"]}</b></span>')
            
            numbers_label = QLabel(", ".join(html_parts))
            numbers_label.setWordWrap(True)
            
            legend_html = (
                self.tr('<b>Legend:</b> <span style="color:{0};">Movie</span>, '
                '<span style="color:{1};">Audio</span>, '
                '<span style="color:{2};">Silent/Other</span>').format(
                    color_map["movie"], color_map["audio"], color_map["silent"]
                )
            )
            legend_label = QLabel(legend_html)

            scroll_area = QScrollArea()
            scroll_area.setWidget(numbers_label)
            scroll_area.setWidgetResizable(True)
            scroll_area.setStyleSheet("QScrollArea { border: 0px; background-color: transparent; }")
            scroll_area.setFixedHeight(60)

            numbers_layout.addWidget(scroll_area)
            numbers_layout.addWidget(legend_label)
            main_layout.addWidget(numbers_group)

        notes = []
        if is_mixed_selection:
            notes.append(self.tr("• Picture-in-Picture settings do not apply to Audio-only or Silent slides."))
        
        if is_only_last_slide_selected:
            notes.append(self.tr("• Transition and Interval settings do not apply to the final slide."))

        if notes:
            notes_group = QGroupBox(self.tr("Notes:"))
            notes_layout = QVBoxLayout(notes_group)
            for note_text in notes:
                label = QLabel(note_text)
                label.setWordWrap(True)
                notes_layout.addWidget(label)
            main_layout.addWidget(notes_group)

        instructions = QLabel(self.tr("Check the box to apply a new value. Unchecked settings will not be changed."))
        instructions.setWordWrap(True)
        main_layout.addWidget(instructions)

        self.widgets = {}

        self.transition_group = QGroupBox(self.tr("Transition & Interval Settings"))
        transition_form_layout = QFormLayout()
        transition_form_layout.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self._add_setting_row(transition_form_layout, "transition_to_next", self.tr("Transition:"), QComboBox(), list(config.TRANSITION_MAPPINGS.keys()), "None")
        self._add_setting_row(transition_form_layout, "interval_to_next", self.tr("Interval to Next (s):"), QSpinBox(), (0, 300), config.DEFAULT_SLIDE_INTERVAL)
        self.transition_group.setLayout(transition_form_layout)
        main_layout.addWidget(self.transition_group)

        pinp_group = QGroupBox(self.tr("Picture-in-Picture Settings"))
        pinp_form_layout = QFormLayout()
        pinp_form_layout.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self._add_setting_row(pinp_form_layout, "video_position", self.tr("Video Position:"), QComboBox(), list(config.VIDEO_POSITION_MAP.keys()), "Center")
        self._add_setting_row(pinp_form_layout, "video_scale", self.tr("Video Scale (%):"), QSpinBox(), config.PINP_SCALE_RANGE, config.DEFAULT_VIDEO_SCALE)
        
        self.effects_button = QPushButton(self.tr("Edit..."))
        self._add_setting_row(pinp_form_layout, "video_effects", self.tr("Video Effects:"), self.effects_button, is_button=True)
        self.selected_effects = []
        self.effects_button.clicked.connect(self._open_effects_dialog)

        pinp_group.setLayout(pinp_form_layout)
        main_layout.addWidget(pinp_group)
        
        pinp_group.setVisible(has_video_slides)

        if is_only_last_slide_selected:
            self.transition_group.setVisible(False)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _open_effects_dialog(self):
        dialog = EditEffectsDialog(self.selected_effects, self)
        if dialog.exec() == QDialog.Accepted:
            self.selected_effects = dialog.get_selected_effects()

    def _add_setting_row(self, layout, key, label_text, widget, items=None, default_value=None, is_button=False):
        if isinstance(widget, QComboBox):
            widget.addItems(items)
            widget.setCurrentText(str(default_value))
        elif isinstance(widget, QSpinBox):
            widget.setRange(*items)
            widget.setValue(default_value)
            
        checkbox = QCheckBox()
        widget.setEnabled(False)
        checkbox.toggled.connect(widget.setEnabled)

        self.widgets[key] = (checkbox, widget)

        label_part_widget = QWidget()
        label_part_layout = QHBoxLayout(label_part_widget)
        label_part_layout.setContentsMargins(0, 0, 0, 0)

        label_part_layout.addWidget(checkbox)
        label_part_layout.addWidget(QLabel(label_text))
        label_part_layout.addStretch()
        
        layout.addRow(label_part_widget, widget)

    def get_changes(self):
        changes = {}
        for key, (checkbox, widget) in self.widgets.items():
            if checkbox.isChecked():
                if isinstance(widget, QComboBox):
                    changes[key] = widget.currentText()
                elif isinstance(widget, QSpinBox):
                    changes[key] = widget.value()
                elif key == "video_effects":
                    changes[key] = self.selected_effects
        return changes


class SlidePreviewDialog(QDialog):
    def __init__(self, page_pixmap, slide_number, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)
        self.setWindowTitle(self.tr("Slide {n} Preview").format(n=slide_number))

        layout = QVBoxLayout(self)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        if page_pixmap and not page_pixmap.isNull():
            screen = self.screen() or QGuiApplication.primaryScreen()
            avail = screen.availableGeometry()
            max_w = int(avail.width() * 0.9)
            max_h = int(avail.height() * 0.85)
            display_pixmap = page_pixmap
            if page_pixmap.width() > max_w or page_pixmap.height() > max_h:
                display_pixmap = page_pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            image_label.setPixmap(display_pixmap)
        else:
            image_label.setText(self.tr("Preview is not available for this slide."))
        layout.addWidget(image_label)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)


class WaveformWidget(QWidget):
    seekRequested = Signal(float)

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._position_ratio = 0.0
        self.setMinimumHeight(120)
        self.setCursor(Qt.PointingHandCursor)
        if pixmap and not pixmap.isNull():
            self.setMinimumWidth(min(pixmap.width(), 600))

    def set_position_ratio(self, ratio: float):
        self._position_ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor("#1e1e1e"))

        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(rect.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(rect, scaled)
        else:
            painter.setPen(QPen(QColor("#888888")))
            painter.drawText(rect, Qt.AlignCenter, self.tr("Waveform not available"))

        playhead_x = int(rect.width() * self._position_ratio)
        painter.setPen(QPen(QColor("#FF5252"), 2))
        painter.drawLine(playhead_x, 0, playhead_x, rect.height())
        painter.end()

    def _emit_seek(self, x):
        if self.width() <= 0:
            return
        ratio = max(0.0, min(1.0, x / self.width()))
        # Move the playhead immediately for responsive scrubbing.
        self.set_position_ratio(ratio)
        self.seekRequested.emit(ratio)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._emit_seek(event.position().x())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._emit_seek(event.position().x())
        super().mouseMoveEvent(event)


class MediaPlayerDialog(QDialog):
    def __init__(self, material_path, is_video, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)
        self.material_path = Path(material_path)
        self.is_video = is_video
        self.setWindowTitle(self.tr("Play: {name}").format(name=self.material_path.name))
        self._slider_pressed = False

        layout = QVBoxLayout(self)

        # --- Display area ---
        self._waveform = None
        if is_video:
            self._video_widget = QVideoWidget(self)
            self._video_widget.setMinimumSize(480, 270)
            layout.addWidget(self._video_widget, stretch=1)
        else:
            waveform_pixmap = generate_waveform_pixmap(self.material_path, 600, 140)
            self._waveform = WaveformWidget(waveform_pixmap, self)
            self._waveform.seekRequested.connect(self._on_waveform_seek)
            layout.addWidget(self._waveform, stretch=1)

        # --- Player ---
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        if is_video:
            self._player.setVideoOutput(self._video_widget)
        self._player.setSource(QUrl.fromLocalFile(str(self.material_path)))

        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error)

        # --- Controls ---
        controls = QHBoxLayout()
        self._play_button = QToolButton(self)
        self._play_button.setIcon(self._themed_standard_icon(QStyle.SP_MediaPlay))
        self._play_button.setToolTip(self.tr("Play/Pause"))
        self._play_button.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_button)

        # Video uses a slider to seek; audio seeks via the waveform itself,
        # so no separate slider is added.
        if is_video:
            self._slider = QSlider(Qt.Horizontal, self)
            self._slider.setRange(0, 0)
            self._slider.sliderPressed.connect(self._on_slider_pressed)
            self._slider.sliderReleased.connect(self._on_slider_released)
            self._slider.sliderMoved.connect(self._player.setPosition)
            controls.addWidget(self._slider, stretch=1)
        else:
            self._slider = None
            controls.addStretch(1)

        self._time_label = QLabel("00:00 / 00:00", self)
        controls.addWidget(self._time_label)
        layout.addLayout(controls)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Auto-start playback.
        self._player.play()

    def _themed_standard_icon(self, standard_pixmap) -> QIcon:
        # Recolor the standard media icon to the palette text color so it stays
        # legible against either the dark or light theme.
        base_icon = self.style().standardIcon(standard_pixmap)
        pixmap = base_icon.pixmap(QSize(32, 32))
        if pixmap.isNull():
            return base_icon

        color = self.palette().color(QPalette.WindowText)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _format_time(ms: int) -> str:
        total_seconds = max(0, ms) // 1000
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _update_time_label(self):
        self._time_label.setText(
            f"{self._format_time(self._player.position())} / {self._format_time(self._player.duration())}"
        )

    @Slot(int)
    def _on_duration_changed(self, duration):
        if self._slider is not None:
            self._slider.setRange(0, duration)
        self._update_time_label()

    @Slot(int)
    def _on_position_changed(self, position):
        if self._slider is not None and not self._slider_pressed:
            self._slider.setValue(position)
        self._update_time_label()
        if self._waveform is not None:
            duration = self._player.duration()
            ratio = (position / duration) if duration > 0 else 0.0
            self._waveform.set_position_ratio(ratio)

    @Slot()
    def _on_slider_pressed(self):
        self._slider_pressed = True

    @Slot()
    def _on_slider_released(self):
        self._slider_pressed = False
        self._player.setPosition(self._slider.value())

    @Slot(float)
    def _on_waveform_seek(self, ratio):
        duration = self._player.duration()
        if duration > 0:
            self._player.setPosition(int(duration * ratio))

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state):
        icon = QStyle.SP_MediaPause if state == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self._play_button.setIcon(self._themed_standard_icon(icon))

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self._player.stop()
            self._player.setPosition(0)
            if self._slider is not None:
                self._slider.setValue(0)
            if self._waveform is not None:
                self._waveform.set_position_ratio(0.0)

    @Slot(QMediaPlayer.Error, str)
    def _on_error(self, error, error_string):
        if error != QMediaPlayer.NoError:
            QMessageBox.warning(
                self,
                self.tr("Playback Error"),
                self.tr("Could not play the material:\n{error}").format(error=error_string),
            )

    def _cleanup(self):
        self._player.stop()
        self._player.setSource(QUrl())

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)

    def reject(self):
        self._cleanup()
        super().reject()
