# slide_table_manager.py
import base64
from functools import partial
from contextlib import contextmanager
from typing import Callable

from PySide6.QtWidgets import (QTableWidget, QLabel, QComboBox, QLineEdit, 
                               QSpinBox, QTableWidgetItem, QHeaderView, QMessageBox,
                               QWidget, QVBoxLayout, QPushButton, QDialog)
from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage

import config
from models import ProjectModel, Slide
from validator import ProjectValidator
from ui_helpers import calculate_pinp_geometry, superimpose_pinp_info
from ui_dialogs import EditEffectsDialog

@contextmanager
def block_signals(widget: QWidget):
    is_blocked = widget.signalsBlocked()
    widget.blockSignals(True)
    try:
        yield
    finally:
        widget.blockSignals(is_blocked)

class SlideTableManager(QObject):
    model_changed = Signal()
    log_message = Signal(str)

    COL_THUMBNAIL = 0
    COL_MATERIAL = 1
    COL_DURATION = 2
    COL_AUDIO_STREAM = 3
    COL_CHAPTER = 4
    COL_PINP_POS = 5
    COL_PINP_SCALE = 6
    COL_PINP_EFFECT = 7
    COL_INTERVAL = 8
    COL_TRANSITION = 9

    def __init__(self, table: QTableWidget, total_duration_label: QLabel, project_model: ProjectModel, validator: ProjectValidator, parent=None):
        super().__init__(parent)
        self.table = table
        self.total_duration_label = total_duration_label
        self.project_model = project_model
        self.validator = validator
        
        self.previews_enabled = True
        self.thumbnail_cache = {}
        self.timers = {}

    def _create_centered_widget(self, widget: QWidget) -> QWidget:
        container_widget = QWidget()
        layout = QVBoxLayout(container_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)
        layout.addWidget(widget)
        return container_widget

    def _populate_combo_with_tooltips(self, combo: QComboBox, items: list[str]):
        combo.clear()
        for item in items:
            combo.addItem(item)
            index = combo.model().index(combo.count() - 1, 0)
            combo.model().setData(index, item, Qt.ToolTipRole)

    def populate_slide_table_from_model(self):
        self.update_slide_info_from_cache()
        self._build_slide_table()
        self.calculate_and_display_total_duration()

    def clear_caches(self):
        self.thumbnail_cache.clear()
        for timer in self.timers.values():
            timer.stop()
        self.timers.clear()

    def toggle_previews(self, enabled: bool):
        self.previews_enabled = enabled
        for i in range(self.table.rowCount()):
            self.update_thumbnail_for_row(i)

    def update_thumbnail_for_row(self, row_index: int):
        if row_index not in self.thumbnail_cache:
            return

        slide = self.project_model.slides[row_index]
        base_pixmap = self.thumbnail_cache[row_index]
        final_pixmap = base_pixmap

        res_str = self.project_model.parameters.resolution
        output_width, output_height = map(int, res_str.split('x'))

        if self.previews_enabled and slide.is_video:
            final_pixmap = superimpose_pinp_info(base_pixmap, slide, output_width, output_height)

        thumb_label = self.table.cellWidget(row_index, self.COL_THUMBNAIL)
        if isinstance(thumb_label, QLabel):
            thumb_label.setPixmap(final_pixmap.scaledToHeight(110, Qt.SmoothTransformation))

    def _build_slide_table(self):
        with block_signals(self.table):
            self.clear_caches()
            self.table.clearContents()
            
            headers = ['Thumbnail', 'Material', 'Duration', 'Audio Stream', 'Chapter Title', 'PinP\nPosition', 'PinP Scale\n(% of Height)', 'PinP\nEffect', 'Interval\nto Next (sec)', 'Transition\nto Next']
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(len(self.project_model.slides))
            
            all_material_choices = [config.SILENT_MATERIAL_NAME] + self.project_model.available_materials

            for idx, slide in enumerate(self.project_model.slides):
                # Thumbnail (Column 0) - Modified to use Base64 cache from Slide model
                if slide.thumbnail_b64:
                    try:
                        # Decode the Base64 string and create a QPixmap
                        byte_data = base64.b64decode(slide.thumbnail_b64)
                        qimage = QImage.fromData(byte_data, "PNG")
                        pixmap = QPixmap.fromImage(qimage)
                        self.thumbnail_cache[idx] = pixmap

                        thumb_label = QLabel()
                        thumb_label.setPixmap(pixmap.scaledToHeight(110, Qt.SmoothTransformation))
                        thumb_label.setAlignment(Qt.AlignCenter)
                        self.table.setCellWidget(idx, self.COL_THUMBNAIL, thumb_label)
                    except Exception as e:
                        self.log_message.emit(f"[ERROR] Failed to load thumbnail for slide {idx+1}: {e}")
                        self.table.setItem(idx, self.COL_THUMBNAIL, QTableWidgetItem("Thumb Err"))
                else:
                    self.table.setItem(idx, self.COL_THUMBNAIL, QTableWidgetItem("No Thumb"))
                
                # Material ComboBox (Column 1)
                material_combo = QComboBox()
                if slide.filename is None:
                    current_choices = [config.UNASSIGNED_MATERIAL_NAME] + all_material_choices
                    self._populate_combo_with_tooltips(material_combo, current_choices)
                    material_combo.setCurrentIndex(0)
                else:
                    self._populate_combo_with_tooltips(material_combo, all_material_choices)
                    material_combo.setCurrentText(slide.filename)

                material_combo.currentTextChanged.connect(partial(self.on_material_changed, idx))
                
                container = self._create_centered_widget(material_combo)
                container.setToolTip(material_combo.currentText())
                material_combo.currentTextChanged.connect(container.setToolTip)
                self.table.setCellWidget(idx, self.COL_MATERIAL, container)
                
                self._update_slide_table_row_widgets(idx, slide)

                # Chapter Title (Column 4)
                chapter_edit = QLineEdit(slide.chapter_title)
                chapter_edit.editingFinished.connect(partial(self.on_table_item_changed, idx, "chapter_title", chapter_edit))
                self.table.setCellWidget(idx, self.COL_CHAPTER, chapter_edit)

                # Interval and Transition (Columns 8, 9)
                if idx < len(self.project_model.slides) - 1:
                    interval_spin = QSpinBox()
                    interval_spin.setRange(0, 300)
                    interval_spin.setValue(slide.interval_to_next)
                    interval_spin.valueChanged.connect(partial(self.on_table_item_changed, idx, "interval_to_next", interval_spin))
                    self.table.setCellWidget(idx, self.COL_INTERVAL, self._create_centered_widget(interval_spin))

                    trans_combo = QComboBox()
                    self._populate_combo_with_tooltips(trans_combo, list(config.TRANSITION_MAPPINGS.keys()))
                    trans_combo.setCurrentText(slide.transition_to_next)
                    trans_combo.currentTextChanged.connect(partial(self.on_table_item_changed, idx, "transition_to_next", trans_combo))
                    
                    trans_container = self._create_centered_widget(trans_combo)
                    trans_container.setToolTip(trans_combo.currentText())
                    trans_combo.currentTextChanged.connect(trans_container.setToolTip)
                    self.table.setCellWidget(idx, self.COL_TRANSITION, trans_container)
                else:
                    for col in [self.COL_INTERVAL, self.COL_TRANSITION]:
                        item = QTableWidgetItem("—")
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                        item.setTextAlignment(Qt.AlignCenter)
                        self.table.setItem(idx, col, item)

            header = self.table.horizontalHeader()
            col_widths = [110, 130, 70, 120, 200, 90, 80, 130, 80, 110]
            for i, width in enumerate(col_widths):
                header.setSectionResizeMode(i, QHeaderView.Interactive if i not in [self.COL_THUMBNAIL] else QHeaderView.ResizeToContents)
                if i != self.COL_THUMBNAIL: self.table.setColumnWidth(i, width)
        
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def update_slide_info_from_cache(self):
        if not self.project_model.project_folder:
            return

        for slide in self.project_model.slides:
            is_unvalidated_material = (
                slide.filename and
                slide.filename != config.SILENT_MATERIAL_NAME and
                slide.filename != config.UNASSIGNED_MATERIAL_NAME and
                slide.duration == 0
            )

            if is_unvalidated_material:
                try:
                    material_path = self.project_model.project_folder / slide.filename
                    if not material_path.exists(): continue
                    
                    file_hash = self.validator._get_file_hash(material_path)
                    if file_hash in self.validator.info_cache:
                        cached_data = self.validator.info_cache[file_hash]
                        slide.duration = cached_data.get('duration', 0.0)
                        slide.tech_info = cached_data.get('tech_info', {})
                        slide.is_video = cached_data.get('is_video', False)
                        slide.audio_streams = cached_data.get('audio_streams', [])
                except Exception as e:
                     self.log_message.emit(f"[ERROR] Could not update slide info from cache for {slide.filename}: {e}")

    def _update_effect_button_display(self, button: QPushButton, effects: list[str]):
        if not effects:
            button.setText("None")
            button.setToolTip("No effects selected.")
        elif len(effects) == 1:
            effect_name = config.VIDEO_EFFECT_MAP.get(effects[0], "Unknown")
            button.setText(effect_name)
            button.setToolTip(f"Selected effect: {effect_name}")
        else:
            button.setText(f"{len(effects)} Effects...")
            effect_names = [config.VIDEO_EFFECT_MAP.get(e, "Unknown") for e in effects]
            button.setToolTip("Selected effects:\n- " + "\n- ".join(effect_names))

    def _open_effects_dialog(self, row_idx: int):
        slide = self.project_model.slides[row_idx]
        dialog = EditEffectsDialog(slide.video_effects, self.table)
        
        if dialog.exec() == QDialog.Accepted:
            new_effects = dialog.get_selected_effects()
            if slide.video_effects != new_effects:
                
                container = self.table.cellWidget(row_idx, self.COL_PINP_EFFECT)
                if container:
                    button = container.findChild(QPushButton)
                    if button:
                        self._update_effect_button_display(button, new_effects)
                
                self.on_table_item_changed(row_idx, "video_effects", None, new_effects)

    def _update_slide_table_row_widgets(self, idx, slide):
        for col in [self.COL_DURATION, self.COL_AUDIO_STREAM, self.COL_PINP_POS, self.COL_PINP_SCALE, self.COL_PINP_EFFECT]:
            self.table.removeCellWidget(idx, col)
            self.table.setItem(idx, col, None)

        self.table.removeCellWidget(idx, self.COL_DURATION)
        if slide.filename == config.SILENT_MATERIAL_NAME:
            # Case 1: Silent material, user can set duration.
            duration_spin = QSpinBox()
            duration_spin.setRange(*config.SILENT_DURATION_RANGE)
            duration_spin.setValue(int(slide.duration))
            duration_spin.valueChanged.connect(partial(self.on_table_item_changed, idx, "duration", duration_spin))
            self.table.setCellWidget(idx, self.COL_DURATION, self._create_centered_widget(duration_spin))
        elif slide.filename is None:
            # Case 2: No material selected. Show a blank, non-editable cell.
            item = QTableWidgetItem("")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(idx, self.COL_DURATION, item)
        else:
            # Case 3: A material is selected. Show its duration or validation status.
            duration_edit = QLineEdit()
            duration_edit.setReadOnly(True)
            if not slide.tech_info and slide.duration == 0:
                # Sub-case 3a: Not yet validated.
                duration_edit.setText("Not validated")
                duration_edit.setStyleSheet("color: grey; font-style: italic;")
            else:
                # Sub-case 3b: Validated.
                duration_edit.setText(self._format_duration(slide.duration))
            self.table.setCellWidget(idx, self.COL_DURATION, duration_edit)

        # Audio Stream Selection Widget (Column 3)
        has_multiple_streams = slide.audio_streams and len(slide.audio_streams) > 1
        
        if has_multiple_streams:
            stream_combo = QComboBox()
            for i, stream_data in enumerate(slide.audio_streams):
                lang = stream_data.get('language', 'unk')
                title = stream_data.get('title', '')
                layout = stream_data.get('channel_layout', 'N/A')
                
                desc = f"#{i}: {lang} ({layout})" + (f" - {title}" if title else "")
                stream_combo.addItem(desc, i)
            
            stream_combo.setCurrentIndex(slide.selected_audio_stream_index)
            stream_combo.currentIndexChanged.connect(partial(self.on_table_item_changed, idx, "selected_audio_stream_index", stream_combo))
            
            stream_container = self._create_centered_widget(stream_combo)
            stream_container.setToolTip(stream_combo.currentText())
            stream_combo.currentTextChanged.connect(stream_container.setToolTip)
            self.table.setCellWidget(idx, self.COL_AUDIO_STREAM, stream_container)
        else:
            display_text = "—"
            if slide.audio_streams:
                display_text = "Single"
            elif slide.is_video:
                display_text = "No Audio"

            item = QTableWidgetItem(display_text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(idx, self.COL_AUDIO_STREAM, item)
        
        # PinP settings (Columns 5, 6, 7)
        if slide.is_video:
            pos_combo = QComboBox()
            self._populate_combo_with_tooltips(pos_combo, list(config.VIDEO_POSITION_MAP.keys()))
            pos_combo.setCurrentText(slide.video_position or "Center")
            pos_combo.currentTextChanged.connect(partial(self.on_table_item_changed, idx, "video_position", pos_combo))
            pos_container = self._create_centered_widget(pos_combo)
            pos_container.setToolTip(pos_combo.currentText())
            pos_combo.currentTextChanged.connect(pos_container.setToolTip)
            self.table.setCellWidget(idx, self.COL_PINP_POS, pos_container)

            scale_spin = QSpinBox()
            scale_spin.setRange(*config.PINP_SCALE_RANGE)
            scale_spin.setSuffix("%")
            scale_spin.setValue(slide.video_scale)
            scale_spin.valueChanged.connect(partial(self.on_table_item_changed, idx, "video_scale", scale_spin))
            self.table.setCellWidget(idx, self.COL_PINP_SCALE, self._create_centered_widget(scale_spin))

            effect_button = QPushButton()
            self._update_effect_button_display(effect_button, slide.video_effects)
            effect_button.clicked.connect(partial(self._open_effects_dialog, idx))
            effect_container = self._create_centered_widget(effect_button)
            self.table.setCellWidget(idx, self.COL_PINP_EFFECT, effect_container)
        else:
            for col in [self.COL_PINP_POS, self.COL_PINP_SCALE, self.COL_PINP_EFFECT]:
                item = QTableWidgetItem("—")
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(idx, col, item)

    def on_material_changed(self, row_idx, material_name):
        v_scroll_bar = self.table.verticalScrollBar()
        current_scroll_pos = v_scroll_bar.value()
        slide = self.project_model.slides[row_idx]
        if slide.filename == material_name and material_name is not None: return

        slide.duration, slide.is_video, slide.tech_info, slide.audio_streams = 0.0, False, {}, []
        slide.selected_audio_stream_index = 0
        
        if material_name == config.UNASSIGNED_MATERIAL_NAME:
            slide.filename = None
        elif material_name == config.SILENT_MATERIAL_NAME:
            slide.filename = config.SILENT_MATERIAL_NAME
            slide.duration = config.DEFAULT_SLIDE_INTERVAL
        else:
            slide.filename = material_name
            mf_path = self.project_model.project_folder / material_name
            if mf_path.exists():
                try:
                    self.validator.analyze_material(mf_path, slide)
                except Exception as e:
                    self.log_message.emit(f"[ERROR] Could not probe file {material_name}: {e}")
        
        if not slide.is_video: slide.video_effects.clear()
        
        with block_signals(self.table):
            self._update_slide_table_row_widgets(row_idx, slide)
        
        self.update_thumbnail_for_row(row_idx)
        self.calculate_and_display_total_duration()
        self.model_changed.emit()
        v_scroll_bar.setValue(current_scroll_pos)
    
    def _debounce_action(self, timer_key: str, action: Callable[[], None], delay_ms: int):
        if timer_key in self.timers:
            self.timers[timer_key].stop()
        
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(action)
        self.timers[timer_key] = timer
        timer.start(delay_ms)

    def on_table_item_changed(self, row_idx, key, widget, value_override=None):
        if row_idx >= len(self.project_model.slides):
            return

        slide = self.project_model.slides[row_idx]
        value = None

        if value_override is not None:
            value = value_override
        elif isinstance(widget, QLineEdit):
            value = widget.text()
        elif isinstance(widget, QSpinBox):
            value = widget.value()
        elif isinstance(widget, QComboBox):
            value = widget.currentData() if key == "selected_audio_stream_index" else widget.currentText()

        if value is None or getattr(slide, key, None) == value:
            return

        setattr(slide, key, value)

        if key in ["video_position", "video_scale", "video_effects"]:
            self._debounce_action(
                timer_key=f'pinp_timer_row_{row_idx}',
                action=lambda r=row_idx: self.update_thumbnail_for_row(r),
                delay_ms=config.PINP_PREVIEW_UPDATE_DELAY_MS
            )

        if key in ["duration", "interval_to_next"]:
            self._debounce_action(
                timer_key='duration_recalc_timer',
                action=self.calculate_and_display_total_duration,
                delay_ms=config.DURATION_RECALC_DELAY_MS
            )
        
        self._debounce_action(
            timer_key='model_changed_timer',
            action=self.model_changed.emit,
            delay_ms=config.DURATION_RECALC_DELAY_MS
        )

    def calculate_and_display_total_duration(self):
        needs_validation = any(s.duration == 0 for s in self.project_model.slides if s.filename and s.filename != config.SILENT_MATERIAL_NAME)
        if needs_validation:
            self.total_duration_label.setText("Total Estimated Duration: Needs validation")
            return
        
        total_duration = sum(s.duration for s in self.project_model.slides) + sum(s.interval_to_next for s in self.project_model.slides[:-1])
        self.project_model.total_duration = total_duration
        self.total_duration_label.setText(f"Total Estimated Duration: {self._format_duration(total_duration, include_msec=False)}")
    
    def apply_transition_to_all(self, transition: str):
        if not self.project_model.slides: return

        with block_signals(self.table):
            for i in range(len(self.project_model.slides) - 1):
                slide = self.project_model.slides[i]
                slide.transition_to_next = transition
            
                widget_container = self.table.cellWidget(i, self.COL_TRANSITION)
                if widget_container:
                    combo_box = widget_container.findChild(QComboBox)
                    if combo_box:
                        with block_signals(combo_box):
                            combo_box.setCurrentText(transition)
        
        QMessageBox.information(self.table, "Success", f"Transition '{transition}' has been applied to all applicable slides.")
        self.model_changed.emit()

    def apply_interval_to_all(self, interval: int):
        if not self.project_model.slides: return
  
        with block_signals(self.table):
            for i in range(len(self.project_model.slides) - 1):
                slide = self.project_model.slides[i]
                slide.interval_to_next = interval
            
                widget_container = self.table.cellWidget(i, self.COL_INTERVAL)
                if widget_container:
                    spin_box = widget_container.findChild(QSpinBox)
                    if spin_box:
                        with block_signals(spin_box):
                            spin_box.setValue(interval)
        
        self.calculate_and_display_total_duration()
        QMessageBox.information(self.table, "Success", f"Interval of {interval} seconds has been applied to all applicable slides.")
        self.model_changed.emit()
        
    def _format_duration(self, seconds, include_msec=True):
        if not isinstance(seconds, (int, float)) or seconds < 0: return ""
        if seconds == 0: return "0s"
        minutes, rem_seconds = int(seconds // 60), seconds % 60
        parts = []
        if minutes > 0: parts.append(f"{minutes}m")
        if rem_seconds > 0:
            sec_str = f"{rem_seconds:.1f}".rstrip('0').rstrip('.') if include_msec else f"{round(rem_seconds)}"
            parts.append(f"{sec_str}s")
        return " ".join(parts) if parts else (f"{minutes}m" if minutes > 0 else "0s")