# ui_state_manager.py
from PySide6.QtCore import Qt
from models import AppState, ProjectModel, ProjectParameters

class UIStateManager:

    def __init__(self, ui, main_window):
        self.ui = ui
        self.main_window = main_window

    def update_ui_for_state(self, new_state: AppState):
        all_controls = [
            self.main_window.select_project_folder_button, self.main_window.validation_button, self.main_window.preview_button,
            self.main_window.create_video_button, self.main_window.select_output_button, self.main_window.parameters_tabs,
            self.main_window.reset_parameters_button, self.main_window.slide_table,
            self.main_window.select_all_button, self.main_window.select_video_button, self.main_window.select_audio_button,
            self.main_window.delete_temp_checkbox, self.main_window.append_duration_checkbox, self.main_window.filename_input,
            self.main_window.load_settings_action, self.main_window.save_settings_action,
            self.main_window.install_ffmpeg_action, self.main_window.show_gallery_action
        ]
        debug_controls = [self.main_window.clear_debug_button, self.main_window.export_debug_button, self.main_window.verbose_debug_checkbox]

        for control in all_controls + debug_controls:
            if control: control.setEnabled(False)

        self.main_window.cancel_button.setEnabled(False)
        self.main_window.edit_selection_button.setEnabled(False)
        self.main_window.validation_button.setText(self.main_window.tr("Check Files"))

        status_message = ""
        
        is_normalized = self.main_window.normalize_loudness_checkbox.isChecked()
        self.main_window.normalize_loudness_mode_label.setEnabled(is_normalized)
        self.main_window.normalize_loudness_mode_combo.setEnabled(is_normalized)
        
        if new_state in [AppState.AWAITING_PROJECT, AppState.PREPARE_TO_VALIDATE, AppState.READY_TO_VALIDATE, AppState.VALIDATED, AppState.ERROR]:
            for control in debug_controls:
                control.setEnabled(True)
        
        if new_state == AppState.CHECKING_ENCODERS:
            self.main_window.show_gallery_action.setEnabled(True)
            status_message = self.main_window.tr("Performing initial check of available encoders...")
        
        elif new_state == AppState.AWAITING_PROJECT:
            self.main_window.select_project_folder_button.setEnabled(True)
            self.main_window.load_settings_action.setEnabled(True)
            self.main_window.show_gallery_action.setEnabled(True)
            if hasattr(self.main_window, 'install_ffmpeg_action'):
                self.main_window.install_ffmpeg_action.setEnabled(not self.main_window.ffmpeg_installed)
            status_message = self.main_window.tr("Please select the project folder to begin.")
        
        elif new_state == AppState.LOADING_PROJECT:
            self.main_window.show_gallery_action.setEnabled(True)
            status_message = self.main_window.tr("Loading project and scanning material files...")

        elif new_state == AppState.PROJECT_LOADED_UIPOPULATED:
            status_message = self.main_window.tr("Project loaded. Verifying PDF structure...")
            self.main_window.show_gallery_action.setEnabled(True)

        elif new_state == AppState.PREPARE_TO_VALIDATE:
            self.main_window.select_project_folder_button.setEnabled(True)
            self.main_window.load_settings_action.setEnabled(True)
            self.main_window.show_gallery_action.setEnabled(True)
            self.main_window.select_output_button.setEnabled(True)
            self.main_window.slide_table.setEnabled(True)
            
            for widget in [self.main_window.select_all_button, self.main_window.select_video_button, self.main_window.select_audio_button,
                           self.main_window.delete_temp_checkbox, self.main_window.append_duration_checkbox, self.main_window.filename_input,
                           self.main_window.parameters_tabs, self.main_window.reset_parameters_button]:
                widget.setEnabled(True)
            status_message = self.main_window.tr("Please select the output folder to enable validation.")

        elif new_state == AppState.READY_TO_VALIDATE:
            self.main_window.select_project_folder_button.setEnabled(True)
            self.main_window.load_settings_action.setEnabled(True)
            self.main_window.show_gallery_action.setEnabled(True)
            self.main_window.select_output_button.setEnabled(True)
            self.main_window.slide_table.setEnabled(True)
            
            for widget in [self.main_window.select_all_button, self.main_window.select_video_button, self.main_window.select_audio_button,
                           self.main_window.delete_temp_checkbox, self.main_window.append_duration_checkbox, self.main_window.filename_input,
                           self.main_window.parameters_tabs, self.main_window.reset_parameters_button, self.main_window.validation_button]:
                widget.setEnabled(True)
            
            if self.main_window.has_validated_once:
                status_message = self.main_window.tr("Settings have changed. Please click 'Check Files' to re-validate.")
            else:
                status_message = self.main_window.tr("Ready to validate. Please click 'Check Files' to proceed.")

        elif new_state == AppState.VALIDATING:
            self.main_window.cancel_button.setEnabled(True)
            self.main_window.show_gallery_action.setEnabled(True)
            self.main_window.validation_button.setText(self.main_window.tr("Checking..."))
            status_message = self.main_window.tr("Validating project files, please wait...")

        elif new_state == AppState.VALIDATED:
            for control in all_controls:
                if control: control.setEnabled(True)
            if hasattr(self.main_window, 'install_ffmpeg_action'):
                self.main_window.install_ffmpeg_action.setEnabled(not self.main_window.ffmpeg_installed)
            status_message = self.main_window.tr("Validation successful. Ready to create video.")

        elif new_state == AppState.PROCESSING:
            self.main_window.cancel_button.setEnabled(True)
            self.main_window.show_gallery_action.setEnabled(True)
            status_message = self.main_window.tr("Processing video, please wait...")
        
        elif new_state == AppState.CANCELLING:
            self.main_window.show_gallery_action.setEnabled(True)
            status_message = self.main_window.tr("Cancelling process...")

        elif new_state == AppState.ERROR:
            status_message = self.main_window._ffmpeg_missing_message()
            self.main_window.show_gallery_action.setEnabled(True)
            if hasattr(self.main_window, 'install_ffmpeg_action'):
                self.main_window.install_ffmpeg_action.setEnabled(not self.main_window.ffmpeg_installed)
        
        self.main_window.status_label.setText(status_message)
        self.main_window.status_label.setVisible(bool(status_message))
        self.main_window.validation_warning_label.setVisible(
            self.main_window.has_validated_once and new_state in [AppState.READY_TO_VALIDATE, AppState.PREPARE_TO_VALIDATE]
        )

        self.update_selection_dependent_ui()

    def sync_model_to_ui(self, params: ProjectParameters):
        try:
            codec_to_set = params.codec
            if self.main_window.codec_combo.findText(codec_to_set) != -1:
                self.main_window.codec_combo.setCurrentText(codec_to_set)
            elif self.main_window.codec_combo.count() > 0 and self.main_window.codec_combo.itemText(0) != "Checking...":
                self.main_window.codec_combo.setCurrentIndex(0)
                codec_to_set = self.main_window.codec_combo.currentText()

            self.main_window._update_hardware_encoding_options(codec_to_set)

            hardware_to_set = params.hardware_encoding
            index = self.main_window.hardware_encoding_combo.findData(hardware_to_set)
            if index != -1:
                self.main_window.hardware_encoding_combo.setCurrentIndex(index)
            elif self.main_window.hardware_encoding_combo.count() > 0:
                self.main_window.hardware_encoding_combo.setCurrentIndex(0)

            encoding_mode_to_set = params.encoding_mode
            self.main_window.encoding_mode_combo.setCurrentText(encoding_mode_to_set)
            self.main_window.update_encoding_options()
            self.main_window.value_spin.setValue(params.encoding_value)

            self.main_window.resolution_combo.setCurrentText(params.resolution)
            self.main_window.fps_combo.setCurrentText(str(params.fps))
            self.main_window.pass_combo.setCurrentText(params.encoding_pass)
            self.main_window.audio_bitrate_combo.setCurrentText(params.audio_bitrate)
            self.main_window.audio_sample_rate_combo.setCurrentText(params.audio_sample_rate)
            self.main_window.audio_channels_combo.setCurrentIndex(
                self.main_window.audio_channels_combo.findData(params.audio_channels, Qt.UserRole, Qt.MatchExactly)
            )
            self.main_window.normalize_loudness_checkbox.setChecked(params.normalize_loudness)
            self.main_window.normalize_loudness_mode_combo.setCurrentText(params.normalize_loudness_mode)
            self.main_window.watermark_color_combo.setCurrentText(params.watermark_color)
            self.main_window.watermark_fontsize_spin.setValue(params.watermark_fontsize)
            self.main_window.watermark_fontfamily_combo.setCurrentText(params.watermark_fontfamily)
            
            rotation_index = self.main_window.watermark_rotation_combo.findData(params.watermark_rotation)
            if rotation_index != -1:
                self.main_window.watermark_rotation_combo.setCurrentIndex(rotation_index)
            self.main_window.watermark_tile_checkbox.setChecked(params.watermark_tile)
            self.main_window.add_watermark_checkbox.setChecked(params.add_watermark)
            self.main_window.watermark_text_input.setText(params.watermark_text)
            self.main_window.watermark_opacity_spin.setValue(params.watermark_opacity)
            self.main_window.watermark_tile_checkbox.setChecked(params.watermark_tile)
            self.main_window.filename_input.setText(params.filename_input)
            self.main_window.append_duration_checkbox.setChecked(params.append_duration_checkbox)
            self.main_window.delete_temp_checkbox.setChecked(params.delete_temp_checkbox)
            self.main_window.export_youtube_chapters_checkbox.setChecked(params.export_youtube_chapters)

        finally:
            pass

    def update_selection_dependent_ui(self):
        selected_rows = self.main_window.slide_table.selectionModel().selectedRows()
        
        if not self.main_window.slide_table.isEnabled() or not selected_rows:
            self.main_window.edit_selection_button.setEnabled(False)
            return

        is_only_last_slide_selected = (len(selected_rows) == 1 and 
                                       selected_rows[0].row() == self.main_window.slide_table.rowCount() - 1)

        if is_only_last_slide_selected:
            last_slide_index = selected_rows[0].row()
            last_slide = self.main_window.project_model.slides[last_slide_index]
            if not last_slide.is_video:
                self.main_window.edit_selection_button.setEnabled(False)
                return

        self.main_window.edit_selection_button.setEnabled(True)

    def update_folder_label(self, label, path_obj):
        if not path_obj:
            label.setText("")
            label.setToolTip("")
            return
        path_str = str(path_obj.resolve())
        elided_text = label.fontMetrics().elidedText(path_str, Qt.ElideMiddle, label.width())
        label.setText(elided_text)
        label.setToolTip(path_str)