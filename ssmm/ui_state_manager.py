# ui_state_manager.py
from PySide6.QtCore import Qt, QCoreApplication
from ssmm.models import AppState, ProjectParameters

class UIStateManager:

    def __init__(self, ui, main_window):
        self.ui = ui
        self.main_window = main_window

    # Maps each app state to a status-bar severity, which the app QSS turns into
    # a color (info = neutral, working = accent, success = green, error = red).
    _WORKING_STATES = frozenset({
        AppState.CHECKING_ENCODERS, AppState.LOADING_PROJECT,
        AppState.PROJECT_LOADED_UIPOPULATED, AppState.VALIDATING,
        AppState.PROCESSING, AppState.CANCELLING,
    })

    @classmethod
    def _severity_for_state(cls, state: AppState) -> str:
        if state == AppState.VALIDATED:
            return "success"
        if state == AppState.ERROR:
            return "error"
        if state in cls._WORKING_STATES:
            return "working"
        return "info"

    def update_ui_for_state(self, new_state: AppState):
        mw = self.main_window
        install_action = getattr(mw, 'install_ffmpeg_action', None)
        all_controls = [
            mw.select_project_folder_button, mw.validation_button, mw.preview_button,
            mw.create_video_button, mw.select_output_button, mw.parameters_tabs,
            mw.reset_parameters_button, mw.slide_table,
            mw.select_all_button, mw.select_video_button, mw.select_audio_button,
            mw.delete_temp_checkbox, mw.append_duration_checkbox, mw.filename_input,
            mw.load_settings_action, mw.save_settings_action,
            mw.import_dmj_action, install_action, mw.show_gallery_action,
            mw.cancel_button,
        ]
        project_controls = [
            mw.select_project_folder_button, mw.load_settings_action, mw.import_dmj_action,
            mw.show_gallery_action, mw.select_output_button, mw.slide_table,
            mw.select_all_button, mw.select_video_button, mw.select_audio_button,
            mw.delete_temp_checkbox, mw.append_duration_checkbox, mw.filename_input,
            mw.parameters_tabs, mw.reset_parameters_button,
        ]

        # Collect the controls that are enabled in the new state, then apply each
        # control's final state exactly once. Disabling and re-enabling a control that
        # stays enabled (e.g. the slide table on VALIDATED -> READY_TO_VALIDATE after
        # an edit) would drop its keyboard focus mid-typing.
        enabled = {mw.show_gallery_action}
        status_message = ""

        if new_state == AppState.CHECKING_ENCODERS:
            status_message = QCoreApplication.translate("MainWindow", "Performing initial check of available encoders...")

        elif new_state == AppState.AWAITING_PROJECT:
            enabled |= {mw.select_project_folder_button, mw.load_settings_action, mw.import_dmj_action}
            status_message = QCoreApplication.translate("MainWindow", "Please select the project folder to begin.")

        elif new_state == AppState.LOADING_PROJECT:
            status_message = QCoreApplication.translate("MainWindow", "Loading project and scanning material files...")

        elif new_state == AppState.PROJECT_LOADED_UIPOPULATED:
            status_message = QCoreApplication.translate("MainWindow", "Project loaded. Verifying PDF structure...")

        elif new_state == AppState.PREPARE_TO_VALIDATE:
            enabled |= set(project_controls)
            status_message = QCoreApplication.translate("MainWindow", "Please select the output folder to enable validation.")

        elif new_state == AppState.READY_TO_VALIDATE:
            enabled |= set(project_controls) | {mw.validation_button}
            if mw.has_validated_once:
                status_message = QCoreApplication.translate("MainWindow", "Settings have changed. Please click 'Check Files' to re-validate.")
            else:
                status_message = QCoreApplication.translate("MainWindow", "Ready to validate. Please click 'Check Files' to proceed.")

        elif new_state == AppState.VALIDATING:
            enabled.add(mw.cancel_button)
            status_message = QCoreApplication.translate("MainWindow", "Validating project files, please wait...")

        elif new_state == AppState.VALIDATED:
            enabled |= {c for c in all_controls if c is not None and c is not mw.cancel_button}
            status_message = QCoreApplication.translate("MainWindow", "Validation successful. Ready to create video.")

        elif new_state == AppState.PROCESSING:
            enabled.add(mw.cancel_button)
            status_message = QCoreApplication.translate("MainWindow", "Processing video, please wait...")

        elif new_state == AppState.CANCELLING:
            status_message = QCoreApplication.translate("MainWindow", "Cancelling process...")

        elif new_state == AppState.ERROR:
            status_message = mw.ffmpeg_missing_message()

        # The installer is only offered while FFmpeg is missing.
        enabled.discard(install_action)
        if install_action is not None and new_state in (AppState.AWAITING_PROJECT, AppState.VALIDATED, AppState.ERROR):
            if not mw.ffmpeg_installed:
                enabled.add(install_action)

        # Open Recent loads another project, which is only allowed while idle.
        recent_menu = getattr(mw, 'recent_menu', None)
        if recent_menu is not None:
            recent_menu.setEnabled(new_state in (AppState.AWAITING_PROJECT, AppState.PREPARE_TO_VALIDATE,
                                                 AppState.READY_TO_VALIDATE, AppState.VALIDATED))

        for control in all_controls:
            if control is not None:
                is_enabled = control in enabled
                if control.isEnabled() != is_enabled:
                    control.setEnabled(is_enabled)

        # Debug controls (clear/export log, verbose toggle) stay enabled in every
        # state, including during long renders and validation, so the user can enable
        # verbose logging or export the log of a misbehaving run.
        for control in (mw.clear_debug_button, mw.export_debug_button, mw.verbose_debug_checkbox):
            control.setEnabled(True)

        if new_state == AppState.VALIDATING:
            mw.validation_button.setText(QCoreApplication.translate("MainWindow", "Checking..."))
        else:
            mw.validation_button.setText(QCoreApplication.translate("MainWindow", "Check Files"))

        is_normalized = mw.normalize_loudness_checkbox.isChecked()
        mw.normalize_loudness_mode_label.setEnabled(is_normalized)
        mw.normalize_loudness_mode_combo.setEnabled(is_normalized)

        status_label = self.main_window.status_label
        status_label.setProperty("severity", self._severity_for_state(new_state))
        # Re-polish so the new dynamic property takes effect immediately.
        status_label.style().unpolish(status_label)
        status_label.style().polish(status_label)
        status_label.setText(status_message)
        status_label.setVisible(bool(status_message))
        self.main_window.validation_warning_label.setVisible(
            self.main_window.has_validated_once and new_state in [AppState.READY_TO_VALIDATE, AppState.PREPARE_TO_VALIDATE]
        )

        self.update_selection_dependent_ui()

    def sync_model_to_ui(self, params: ProjectParameters):
        try:
            # Before the startup encoder test the codec list only holds a placeholder;
            # nothing the UI shows then may be written back into the model.
            combo = self.main_window.codec_combo
            encoders_ready = combo.count() > 0 and combo.itemText(0) != "No Encoders Found"
            stored_codec = params.codec
            codec_to_set = params.codec
            if self.main_window.codec_combo.findText(codec_to_set) != -1:
                self.main_window.codec_combo.setCurrentText(codec_to_set)
            elif self.main_window.codec_combo.count() > 0 and self.main_window.codec_combo.itemText(0) != "No Encoders Found":
                # The stored codec has no working encoder on this machine: fall back to
                # the default codec if available (not simply the first, alphabetical
                # entry, which is the slow AV1) and say so in the log.
                default_codec = ProjectParameters().codec
                if self.main_window.codec_combo.findText(default_codec) != -1:
                    self.main_window.codec_combo.setCurrentText(default_codec)
                else:
                    self.main_window.codec_combo.setCurrentIndex(0)
                codec_to_set = self.main_window.codec_combo.currentText()
                self.main_window.write_debug(
                    f"[WARNING] Codec '{params.codec}' has no working encoder on this computer; using '{codec_to_set}'.")
                params.codec = codec_to_set

            self.main_window.update_hardware_encoding_options(codec_to_set)

            hardware_to_set = params.hardware_encoding
            index = self.main_window.hardware_encoding_combo.findData(hardware_to_set)
            if index != -1:
                self.main_window.hardware_encoding_combo.setCurrentIndex(index)
            elif self.main_window.hardware_encoding_combo.count() > 0:
                self.main_window.hardware_encoding_combo.setCurrentIndex(0)
                if encoders_ready:
                    params.hardware_encoding = self.main_window.hardware_encoding_combo.currentData()

            # Combos for keyed options carry the key as item data.
            def select_data(combo, value):
                index = combo.findData(value)
                if index != -1:
                    combo.setCurrentIndex(index)

            select_data(self.main_window.encoding_mode_combo, params.encoding_mode)
            # Set the pass before refreshing the options: the refresh forces 1-Pass
            # for encoders without two-pass support.
            select_data(self.main_window.pass_combo, params.encoding_pass)
            self.main_window.update_encoding_options()
            forced_mode = self.main_window.encoding_mode_combo.currentData()
            same_scale = (self.main_window._value_scale(stored_codec, params.encoding_mode)
                          == self.main_window._value_scale(codec_to_set, params.encoding_mode))
            if forced_mode == params.encoding_mode and same_scale:
                self.main_window.value_spin.setValue(params.encoding_value)
            elif encoders_ready:
                # The encoder cannot use the stored mode (VideoToolbox has no quality
                # mode), or a codec fallback changed the value's scale: keep the model
                # in line with what the UI now shows instead of letting the next sync
                # silently swap in the new default.
                params.encoding_mode = forced_mode
                params.encoding_value = self.main_window.value_spin.value()
            if encoders_ready:
                params.encoding_pass = self.main_window.pass_combo.currentData() or params.encoding_pass

            self.main_window.resolution_combo.setCurrentText(params.resolution)
            self.main_window.fps_combo.setCurrentText(str(params.fps))
            self.main_window.audio_bitrate_combo.setCurrentText(params.audio_bitrate)
            self.main_window.audio_sample_rate_combo.setCurrentText(params.audio_sample_rate)
            ch_index = self.main_window.audio_channels_combo.findData(params.audio_channels, Qt.UserRole, Qt.MatchExactly)
            if ch_index == -1:
                # An out-of-range value (e.g. from a hand-edited file) has no matching
                # entry; fall back to the default rather than clearing the combo to an
                # empty selection (which would store audio_channels=None in the model).
                ch_index = self.main_window.audio_channels_combo.findData(ProjectParameters().audio_channels, Qt.UserRole, Qt.MatchExactly)
            if ch_index != -1:
                self.main_window.audio_channels_combo.setCurrentIndex(ch_index)
            self.main_window.normalize_loudness_checkbox.setChecked(params.normalize_loudness)
            select_data(self.main_window.normalize_loudness_mode_combo, params.normalize_loudness_mode)
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
            self.main_window.filename_input.setText(params.filename_input)
            self.main_window.append_duration_checkbox.setChecked(params.append_duration_checkbox)
            self.main_window.delete_temp_checkbox.setChecked(params.delete_temp_checkbox)
            self.main_window.export_youtube_chapters_checkbox.setChecked(params.export_youtube_chapters)

        except Exception as e:
            # A half-synced UI is hard to diagnose silently; surface the failure.
            self.main_window.write_debug(f"[ERROR] Failed to sync parameters to the UI: {e}")

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