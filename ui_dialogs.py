# ui_dialogs.py
import base64
import imagehash
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QSpinBox, 
                               QDialogButtonBox, QGroupBox, QFormLayout, QComboBox, 
                               QCheckBox, QWidget, QHBoxLayout, QScrollArea, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QStyle)
from PySide6.QtCore import Qt, QPoint, Slot
from PySide6.QtGui import (QTextCursor, QPixmap, QImage, QPainter, QFont, QColor, QPen,
                           QPainterPath)

import config
from models import Slide

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
        
        old_ihashes = [imagehash.hex_to_hash(h) for h in self.old_hashes if h]
        new_ihashes = [imagehash.hex_to_hash(h) for h in self.new_hashes if h]

        for i, new_hash in enumerate(new_ihashes):
            best_match_idx = -1
            min_dist = config.HAMMING_DISTANCE_THRESHOLD + 1

            for j, old_hash in enumerate(old_ihashes):
                if j in used_old_indices:
                    continue
                
                dist = new_hash - old_hash
                if dist < min_dist:
                    min_dist = dist
                    best_match_idx = j
            
            if min_dist <= config.HAMMING_DISTANCE_THRESHOLD:
                mapping[i] = best_match_idx
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

        for i in range(self.new_pdf_info["page_count"]):

            # Column 0: New Page Thumbnail (Unchanged)
            if i in self.new_pixmaps:
                numbered_thumb = self._create_numbered_thumbnail(self.new_pixmaps[i], i + 1)
                thumb_label = QLabel()
                thumb_label.setPixmap(numbered_thumb.scaledToHeight(90, Qt.SmoothTransformation))
                thumb_label.setAlignment(Qt.AlignCenter)
                self.table.setCellWidget(i, COL_NEW_THUMB, thumb_label)

            # Column 1: Source Selection ComboBox with rich text
            combo = QComboBox()
            combo.addItem(self.tr("Unmapped (New Page)"), userData=None)
            for j, old_slide in enumerate(self.old_slides):
                material_type, material_name = self._get_material_display_info(old_slide)
                display_text = self.tr("Old #{0} [{1}]").format(j + 1, material_type)
                if material_name:
                    display_text += f" {material_name}"
                combo.addItem(display_text, userData=j)
            combo.currentIndexChanged.connect(lambda index, row=i: self._on_source_changed(row, index))
            self.table.setCellWidget(i, COL_SOURCE_COMBO, combo)
            
            # Column 2: Source Thumbnail (initially empty)
            source_thumb_label = QLabel()
            source_thumb_label.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(i, COL_SOURCE_THUMB, source_thumb_label)

            # Column 3: Source Info Label (initially empty)
            source_info_label = QLabel()
            source_info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            source_info_label.setWordWrap(True)
            self.table.setCellWidget(i, COL_SOURCE_INFO, source_info_label)
            
            # Set initial mapping value and trigger the first update
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

    @Slot(int, int)
    def _on_source_changed(self, row: int, combo_index: int):
        combo = self.table.cellWidget(row, COL_SOURCE_COMBO)
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
            combo = self.table.cellWidget(i, COL_SOURCE_COMBO)
            if combo:
                old_idx = combo.currentData()
                mapping[i] = old_idx
        return mapping


class EditEffectsDialog(QDialog):
    def __init__(self, current_effects, parent=None):
        super().__init__(parent)
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