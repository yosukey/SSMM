# main_window.py
import copy
import datetime
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib import request
from packaging.version import parse as parse_version
from typing import Optional, Callable
import os
import shutil

import psutil
import fitz
import PIL
import PySide6
import toml
import imagehash

import qdarktheme
from PySide6.QtCore import (QItemSelection, QItemSelectionModel, QObject, Qt,
                            QTimer, QUrl, Signal, QStandardPaths)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QMovie, QPalette,
                           QTextCharFormat, QTextCursor, QShowEvent)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QDialog, QFileDialog, QGridLayout, QLabel,
                               QMenuBar, QMessageBox, QScrollArea, QStyle,
                               QVBoxLayout, QWidget)

import config
from ffmpeg_installer import FFmpegInstaller
from models import (AppState, ProjectModel, ProjectParameters, Slide,
                    ValidationMessages)
from settings_manager import SettingsManager
from slide_table_manager import SlideTableManager
from ui_dialogs import (EditSlidesDialog, InstallProgressDialog,
                        SelectSlideDialog, PageMappingDialog)
from ui_main import Ui_MainWindow
from ui_state_manager import UIStateManager
from utils import (bundled_ffmpeg_exists, get_ffmpeg_path, get_ffmpeg_source,
                   resolve_resource_path)
from validator import ProjectValidator
from worker_manager import WorkerManager

try:
    from capabilities import load_capabilities
except Exception:
    def load_capabilities():
        return {"EDITION": "B", "FFMPEG_INSTALL_MENU": True}

class AppStateMachine(QObject):
    state_changed = Signal(AppState, AppState)

    def __init__(self, initial_state=AppState.CHECKING_ENCODERS, debug_writer=None):
        super().__init__()
        self._state = initial_state
        self.write_debug = debug_writer if callable(debug_writer) else lambda text: None

    @property
    def state(self):
        return self._state

    def transition_to(self, new_state: AppState):
        old_state = self._state
        if old_state == new_state:
            return

        if config.LOG_STATE_TRANSITIONS:
            self.write_debug(f"[STATE_TRANSITION] From {old_state.name} to {new_state.name}")
        
        self._state = new_state
        self.state_changed.emit(old_state, new_state)

class HoverGifWidget(QWidget):
    def __init__(self, gif_path: Path, caption_text: str, parent=None):
        super().__init__(parent)
        self.gif_path = gif_path
        self.movie = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.gif_label = QLabel()
        self.gif_label.setAlignment(Qt.AlignCenter)
        self.gif_label.setFixedSize(200, 150)
        self.gif_label.setStyleSheet("border: 1px solid #555; border-radius: 4px; background-color: #3c3c3c;")

        temp_movie = QMovie(str(self.gif_path))
        if temp_movie.isValid():
            temp_movie.jumpToFrame(0)
            pixmap = temp_movie.currentPixmap()
            if not pixmap.isNull():
                self.static_pixmap = pixmap.scaled(self.gif_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.gif_label.setPixmap(self.static_pixmap)
        
        caption_label = QLabel(caption_text)
        caption_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.gif_label)
        layout.addWidget(caption_label)

    def enterEvent(self, event):
        if self.gif_path.exists() and self.movie is None:
            self.movie = QMovie(str(self.gif_path))
            self.movie.setScaledSize(self.gif_label.size())
            self.gif_label.setMovie(self.movie)
            self.movie.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
            self.gif_label.setMovie(None)
            if hasattr(self, 'static_pixmap'):
                self.gif_label.setPixmap(self.static_pixmap)
        super().leaveEvent(event)

class EmittingStream(QObject):
    text_written = Signal(str, str)
    def write(self, text):
        self.text_written.emit(str(text), 'app')
    def flush(self):
        pass

class MainWindow(QWidget):
    __version__ = config.APP_VERSION
    
    def __init__(self, verbose_startup: bool = False, project_path_on_startup: Optional[Path] = None):
        super().__init__()
        
        self._project_path_on_startup = project_path_on_startup
        self._is_first_activation = True
        self._next_startup_action: Optional[Callable] = None
        
        QApplication.instance().focusWindowChanged.connect(self.handle_focus_changed)
        
        self.settings_manager = SettingsManager(self)
        self.project_model = ProjectModel()

        self.worker_manager = WorkerManager(self)
        self.worker_manager.setup_persistent_workers()

        self.available_encoders_map = {}
        self.has_validated_once = False
        self.validation_snapshot = {}
        self.page_count = 0
        self._is_syncing = False
        self.last_validation_messages: ValidationMessages | None = None
        
        self.parameter_update_timer = QTimer(self)
        self.parameter_update_timer.setSingleShot(True)
        self.parameter_update_timer.timeout.connect(self.on_parameter_changed)
        
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        
        self.verbose_debug_checkbox.setChecked(verbose_startup)
        
        self.ui_manager = UIStateManager(self.ui, self) 

        self.validator = ProjectValidator(logger=self.write_debug)
        
        self.state_machine = AppStateMachine(
            initial_state=AppState.CHECKING_ENCODERS,
            debug_writer=self.write_debug
        )
        
        self.installer_thread = None
        
        self.capabilities = load_capabilities()
        self.edition = self.capabilities.get("EDITION", "B")

        self.gallery_window = None
        self.progress_dialog = None
        self.ffmpeg_installed = self.check_ffmpeg_exists()
        
        self.slide_table.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.slide_table_manager = SlideTableManager(
            self.slide_table, 
            self.total_duration_label, 
            self.project_model,
            self.validator,
            self
        )
        self.slide_table_manager.log_message.connect(self.write_debug)

        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        self.current_theme = "dark" if text_color.lightness() > 128 else "light"

        self._populate_comboboxes()

        main_layout = self.layout()
        if main_layout:
            main_layout.setMenuBar(self._create_menu_bar())

        self._setup_connections()
        
        self._setup_licenses_tab()
        self._setup_font_license_tab()
        self._setup_disclaimer_tab()
        
        self._sync_model_to_ui()

        self.on_state_changed(None, self.state_machine.state)

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)

    def handle_focus_changed(self, focus_window):
         if self._is_first_activation and focus_window is self.windowHandle():
            self._is_first_activation = False
            
            self.perform_initial_checks()

    def perform_initial_checks(self):
        if self.ffmpeg_installed:
            self.worker_manager.start_encoder_test(self.validator)
        else:
            msg = self._ffmpeg_missing_message()
            self.write_debug(f"[FATAL] {msg}")
            QMessageBox.critical(self, self.tr("FFmpeg Not Found"), msg)
            self.state_machine.transition_to(AppState.ERROR)

    def on_transient_worker_finished(self):
        if self._next_startup_action:
            action = self._next_startup_action
            self._next_startup_action = None
            action()

    def on_worker_thread_finished(self):
        QApplication.restoreOverrideCursor()
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(False)

    def on_encoder_test_finished(self, encoders_map, logs):
        self.write_debug("\n--- Hardware Encoder Test Finished (Background) ---")
        for log in logs:
            self.write_debug(log)

        self.available_encoders_map = encoders_map
        self.project_model.parameters.available_encoders = self.available_encoders_map

        self._populate_comboboxes()
        self._sync_model_to_ui()

        if self._project_path_on_startup and self._project_path_on_startup.exists():
            self._next_startup_action = self._start_project_setup_from_startup_path
        else:
            self.state_machine.transition_to(AppState.AWAITING_PROJECT)
        
        self.on_worker_thread_finished()

    def _start_project_setup_from_startup_path(self):
        if not self._project_path_on_startup:
            return
            
        path = self._project_path_on_startup
        if path.exists():
            self.write_debug(f"[INFO] Starting project setup from command line argument: {path}")
            self.state_machine.transition_to(AppState.LOADING_PROJECT)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.worker_manager.start_project_setup(self.settings_manager, self.validator, path)
        else:
            self.write_debug(f"[ERROR] Path specified in command line argument not found: {path}")
            QMessageBox.warning(self, self.tr("File Not Found"), self.tr("The specified project path does not exist:\n{0}").format(path))
            self.state_machine.transition_to(AppState.AWAITING_PROJECT)

    def check_ffmpeg_exists(self) -> bool:
        try:
            get_ffmpeg_path()
            return True
        except (FileNotFoundError, ImportError):
            return False

    def _setup_licenses_tab(self):
        if not bundled_ffmpeg_exists():
            self.ffmpeg_compliance_label.setVisible(False)
            self.system_ffmpeg_notice_label.setVisible(False)
            self.ffmpeg_license_header.setVisible(False)
            self.ffmpeg_license_text_edit.setVisible(False)
            self.ffmpeg_build_config_header.setVisible(False)
            self.ffmpeg_build_config_text_edit.setVisible(False)
            return

        ffmpeg_source = get_ffmpeg_source()

        self.ffmpeg_compliance_label.setVisible(True)
        self.ffmpeg_compliance_label.setText(
            self.tr("This software uses libraries from the <a href='https://www.ffmpeg.org/'>FFmpeg</a> project licensed under the LGPLv2.1.<br><br>"
            "The distributor of this application is responsible for providing the exact corresponding source code. "
            "If you received this application with a bundled FFmpeg, you should have also received information on how to obtain the source. "
            "For reference, common FFmpeg builds and their sources can be found at sites like "
            "<a href='https://www.gyan.dev/ffmpeg/builds/'>Gyan.dev</a> or "
            "<a href='https://github.com/BtbN/FFmpeg-Builds/releases'>BtbN/FFmpeg-Builds</a>.")
        )
        
        self.ffmpeg_license_header.setVisible(True)
        self.ffmpeg_license_text_edit.setVisible(True)
        self.ffmpeg_build_config_header.setVisible(True)
        self.ffmpeg_build_config_text_edit.setVisible(True)

        try:
            lgpl_path = resolve_resource_path("ffmpeg/LGPL.txt")
            if lgpl_path.exists():
                with open(lgpl_path, 'r', encoding='utf-8') as f:
                    self.ffmpeg_license_text_edit.setText(f.read())
            else:
                self.ffmpeg_license_text_edit.setText("Bundled ffmpeg/LGPL.txt not found.")
        except Exception as e:
            self.ffmpeg_license_text_edit.setText(f"Error loading LGPL.txt: {e}")

        try:
            config_path = resolve_resource_path("ffmpeg/ffmpeg_build_config.txt")
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.ffmpeg_build_config_text_edit.setText(f.read())
            else:
                self.ffmpeg_build_config_text_edit.setText("Bundled ffmpeg/ffmpeg_build_config.txt not found.")
        except Exception as e:
            self.ffmpeg_build_config_text_edit.setText(f"Error loading ffmpeg_build_config.txt: {e}")

        if ffmpeg_source == 'system':
            self.system_ffmpeg_notice_label.setText(
                self.tr("<b>FFmpeg Status:</b><br>"
                "The system-installed version of FFmpeg is being prioritized for use.<br>"
                "For license and build configuration, please refer to the FFmpeg version installed on your system.")
            )
            self.system_ffmpeg_notice_label.setVisible(True)
        else:
            self.system_ffmpeg_notice_label.setVisible(False)

    def _setup_font_license_tab(self):
        try:
            font_license_path = resolve_resource_path("fonts/OFL.txt")
            if font_license_path.exists():
                with open(font_license_path, 'r', encoding='utf-8') as f:
                    self.font_license_text_edit.setText(f.read())
            else:
                self.font_license_text_edit.setText("Font license file (OFL.txt) not found in resources.")
        except Exception as e:
            self.font_license_text_edit.setText(f"Error loading font license file: {e}")
            self.write_debug(f"Error loading font license: {e}")

    def _setup_disclaimer_tab(self):
        disclaimer_text = self.tr(
            "This software is provided 'as is' without warranty of any kind, express or implied, "
            "including but not limited to the warranties of merchantability, fitness for a particular purpose, and noninfringement. "
            "In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, "
            "whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software "
            "or the use or other dealings in the software.\n"
            "You use this software at your own risk. The developers assume no responsibility for any loss of data or damage "
            "to your system that may result from its use. It is highly recommended to back up your data before using this application.\n\n"
            "This application assumes that you will use only materials for which you hold the rights or for which you have obtained valid permission. "
            "These materials may include PDFs, images, audio, and video."
            "You are responsible for ensuring that your use does not violate third-party copyrights. "
            "Where applicable, this includes related and neighboring rights. You must also respect rights of publicity and portrait rights, "
            "trademarks, privacy rights, and any other rights."
            "In addition, you must comply with your institution’s rules, contracts, and the terms of service of any platforms you use.\n"
            "You are solely responsible for clearing all rights and for providing any required notices when you publish or distribute videos created with this app. "
            "This includes credits, source attributions, license notices, and compliance with quotation or fair-use requirements where applicable."
            "The provider of this app does not review your materials or workflows. The provider accepts no liability for any loss or damage arising from your use. "
            "If needed, consult a qualified professional. This text does not constitute legal advice.\n"
            "Note: The app itself is licensed under the MIT License. "
            "That license applies to the app only. It does not grant or guarantee any rights in your input materials or in your exported videos."
        )
        self.disclaimer_text_edit.setText(disclaimer_text)

    def _create_menu_bar(self) -> QMenuBar:
        menu_bar = QMenuBar(self)

        file_menu = menu_bar.addMenu(self.tr("&File"))
        self.load_settings_action = QAction(self.tr("&Load Project Settings..."), self)
        self.load_settings_action.triggered.connect(self.load_settings)
        self.save_settings_action = QAction(self.tr("&Save Project Settings..."), self)
        self.save_settings_action.triggered.connect(self.save_settings)
        exit_action = QAction(self.tr("E&xit"), self)
        exit_action.triggered.connect(self.close)
        file_menu.addActions([self.load_settings_action, self.save_settings_action])
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        
        view_menu = menu_bar.addMenu(self.tr("&View"))
        self.switch_theme_action = QAction(self.tr("Switch Theme"), self)
        self.switch_theme_action.triggered.connect(self._switch_theme)
        view_menu.addAction(self.switch_theme_action)

        tools_menu = menu_bar.addMenu(self.tr("&Tools"))
        if self.capabilities.get("FFMPEG_INSTALL_MENU", True):
            self.install_ffmpeg_action = QAction(self.tr("Install FFmpeg..."), self)
            self.install_ffmpeg_action.triggered.connect(self.prompt_install_ffmpeg)
            tools_menu.addAction(self.install_ffmpeg_action)
        self.show_gallery_action = QAction(self.tr("Transition &Gallery..."), self)
        self.show_gallery_action.triggered.connect(self.show_transition_gallery)
        tools_menu.addAction(self.show_gallery_action)
        
        help_menu = menu_bar.addMenu(self.tr("&Help"))
        self.repository_action = QAction(self.tr("Visit Project Repository"), self)
        self.repository_action.triggered.connect(self._open_repository_url)
        help_menu.addAction(self.repository_action)
        
        return menu_bar

    def _switch_theme(self):
        new_theme = "light" if self.current_theme == "dark" else "dark"
        QApplication.instance().setStyleSheet(qdarktheme.load_stylesheet(new_theme))
        self.current_theme = new_theme

        if self.last_validation_messages:
            self.validation_results_text.setHtml(self.last_validation_messages.assemble_html(theme=self.current_theme))

    def _open_repository_url(self):
        url = QUrl(config.REPO_URL)
        QDesktopServices.openUrl(url)

    def _adjust_combo_box_view_width(self, combo_box: QComboBox):
        font_metrics = combo_box.fontMetrics()
        max_width = 0
        for i in range(combo_box.count()):
            text = combo_box.itemText(i)
            width = font_metrics.horizontalAdvance(text)
            if width > max_width:
                max_width = width
        
        scrollbar_width = QApplication.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        padding = 20 
        combo_box.view().setMinimumWidth(max_width + scrollbar_width + padding)

    def _populate_comboboxes(self):
        self.resolution_combo.clear()
        self.resolution_combo.addItems(config.RESOLUTION_OPTIONS)
        
        self.fps_combo.clear()
        self.fps_combo.addItems([str(fps) for fps in config.FPS_OPTIONS])
        
        self.codec_combo.blockSignals(True)
        self.codec_combo.clear()
        available_codecs = sorted(list(self.available_encoders_map.keys()))
        if available_codecs:
            self.codec_combo.addItems(available_codecs)
            self.codec_combo.setEnabled(True)
        else:
            self.codec_combo.addItem(self.tr("No Encoders Found"))
            self.codec_combo.setEnabled(False)
        self.codec_combo.blockSignals(False)

        self.audio_bitrate_combo.clear()
        self.audio_bitrate_combo.addItems(config.AUDIO_BITRATE_OPTIONS)
        
        self.audio_sample_rate_combo.clear()
        self.audio_sample_rate_combo.addItems(config.AUDIO_SAMPLE_RATE_OPTIONS)
        
        self.audio_channels_combo.clear()
        channel_labels = {
            1: self.tr("1 (Mono)"),
            2: self.tr("2 (Stereo)"),
            3: self.tr("3 (Stereo + Center)"),
            4: self.tr("4 (Quadraphonic)"),
            5: self.tr("5 (5.0 Surround)"),
            6: self.tr("6 (5.1 Surround)"),
            7: self.tr("7 (7.0 Surround)"),
            8: self.tr("8 (7.1 Surround)")
        }
        for i in range(1, 9):
            text = channel_labels.get(i, str(i))
            self.audio_channels_combo.addItem(text, userData=i)
        
        self.watermark_color_combo.clear()
        self.watermark_color_combo.addItems(config.WATERMARK_COLOR_OPTIONS_RGBA.keys())
        
        self.watermark_fontfamily_combo.clear()
        self.watermark_fontfamily_combo.addItems(list(config.BUNDLED_FONTS.keys()))
        
        self.watermark_rotation_combo.clear()
        self.watermark_rotation_combo.addItem(self.tr("None"), "None")
        self.watermark_rotation_combo.addItem(self.tr("45 Degrees (Clockwise)"), "45")
        self.watermark_rotation_combo.addItem(self.tr("-45 Degrees (C-Clockwise)"), "-45")
        
        self.encoding_mode_combo.clear()
        self.encoding_mode_combo.addItems(list(config.ENCODING_MODES.values()))
        
        self.pass_combo.clear()
        self.pass_combo.addItems(list(config.ENCODING_PASSES.values()))
        
        for combo in self.parameters_tabs.findChildren(QComboBox):
            self._adjust_combo_box_view_width(combo)

    def _update_hardware_encoding_options(self, codec: str):
        self.hardware_encoding_combo.blockSignals(True)
        self.hardware_encoding_combo.clear()
        try:
            is_initializing = self.state_machine.state == AppState.CHECKING_ENCODERS
            if not codec or is_initializing:
                self.hardware_encoding_combo.setEnabled(False)
                return

            options = self.validator.get_available_hw_options_for_codec(codec, self.available_encoders_map)
            sorted_options = sorted(list(options))

            for option in sorted_options:
                if option == "None":
                    self.hardware_encoding_combo.addItem("None", userData=None)
                elif option == "videotoolbox":
                    self.hardware_encoding_combo.addItem(self.tr("Enabled (Apple Hardware)"), userData="videotoolbox")
                else:
                    self.hardware_encoding_combo.addItem(option, userData=option)

            self.hardware_encoding_combo.setEnabled(bool(options))
            self._adjust_combo_box_view_width(self.hardware_encoding_combo)
        finally:
            self.hardware_encoding_combo.blockSignals(False)
    
    def _sync_ui_to_model(self):
        fps_text = self.fps_combo.currentText()
        params = self.project_model.parameters
        
        params.resolution = self.resolution_combo.currentText()
        params.fps = int(fps_text) if fps_text else 30
        params.codec = self.codec_combo.currentText()
        params.hardware_encoding = self.hardware_encoding_combo.currentData()
        params.encoding_mode = self.encoding_mode_combo.currentText()
        params.encoding_value = self.value_spin.value()
        params.encoding_pass = self.pass_combo.currentText()
        params.audio_bitrate = self.audio_bitrate_combo.currentText()
        params.audio_sample_rate = self.audio_sample_rate_combo.currentText()
        params.audio_channels = self.audio_channels_combo.currentData()
        params.normalize_loudness = self.normalize_loudness_checkbox.isChecked()
        params.normalize_loudness_mode = self.normalize_loudness_mode_combo.currentText()
        params.add_watermark = self.add_watermark_checkbox.isChecked()
        params.watermark_text = self.watermark_text_input.text()
        params.watermark_opacity = self.watermark_opacity_spin.value()
        params.watermark_color = self.watermark_color_combo.currentText()
        params.watermark_fontsize = self.watermark_fontsize_spin.value()
        params.watermark_fontfamily = self.watermark_fontfamily_combo.currentText()
        params.watermark_rotation = self.watermark_rotation_combo.currentData()
        params.watermark_tile = self.watermark_tile_checkbox.isChecked()
        params.delete_temp_checkbox = self.delete_temp_checkbox.isChecked()
        params.append_duration_checkbox = self.append_duration_checkbox.isChecked()
        params.filename_input = self.filename_input.text()
        params.export_youtube_chapters = self.export_youtube_chapters_checkbox.isChecked()

    def _sync_model_to_ui(self):
        self._is_syncing = True
        self.ui_manager.sync_model_to_ui(self.project_model.parameters)
        self._is_syncing = False

    def _setup_connections(self):
        self.select_project_folder_button.clicked.connect(self.select_project_folder)
        self.validation_button.clicked.connect(self.run_validation)
        self.select_output_button.clicked.connect(self.select_output_folder)
        self.preview_button.clicked.connect(self.run_preview_generation)
        self.create_video_button.clicked.connect(self.run_create_video)
        self.cancel_button.clicked.connect(self.cancel_video_creation)
        
        self.state_machine.state_changed.connect(self.on_state_changed)
        
        self.codec_combo.currentTextChanged.connect(self.on_codec_changed)
        self.encoding_mode_combo.currentIndexChanged.connect(self.on_encoding_mode_changed)
        
        self.worker_manager.progress_updated.connect(self.update_progress_bar)
        self.worker_manager.log_message.connect(self.write_debug)
        self.worker_manager.video_finished.connect(self.on_video_creation_finished)
        self.worker_manager.preview_finished.connect(self.on_preview_finished)
        
        self.worker_manager.encoder_test_finished.connect(self.on_encoder_test_finished)
        self.worker_manager.project_setup_finished.connect(self.on_project_setup_finished)
        self.worker_manager.project_setup_error.connect(self.on_project_setup_error)
        
        self.worker_manager.validation_finished.connect(self.on_validation_finished)
        self.worker_manager.validation_error.connect(self.on_validation_error)
        self.worker_manager.validation_canceled.connect(self.on_validation_canceled)
        self.worker_manager.transient_worker_finished.connect(self.on_transient_worker_finished)
        param_widgets_on_change = [
            self.resolution_combo, self.fps_combo, self.hardware_encoding_combo, self.pass_combo,
            self.audio_bitrate_combo, self.audio_sample_rate_combo, self.audio_channels_combo,
            self.normalize_loudness_mode_combo,
        ]
        param_widgets_on_finish_editing = [self.filename_input]
        param_widgets_on_toggle = [self.normalize_loudness_checkbox]

        for widget in param_widgets_on_change:
            widget.currentIndexChanged.connect(self.on_parameter_changed)
        for widget in param_widgets_on_finish_editing:
            widget.editingFinished.connect(self.on_parameter_changed)
        for widget in param_widgets_on_toggle:
            widget.toggled.connect(self.on_parameter_changed)

        self.value_spin.valueChanged.connect(self._request_delayed_parameter_update)
        
        cosmetic_widgets_on_change = [
            self.watermark_color_combo, self.watermark_fontfamily_combo, self.watermark_rotation_combo,
        ]
        cosmetic_widgets_on_finish_editing = [
            self.watermark_text_input, self.watermark_opacity_spin, self.watermark_fontsize_spin,
        ]
        cosmetic_widgets_on_toggle = [
            self.add_watermark_checkbox, self.watermark_tile_checkbox,
            self.delete_temp_checkbox, self.append_duration_checkbox,
            self.export_youtube_chapters_checkbox,
        ]

        for widget in cosmetic_widgets_on_change:
            widget.currentIndexChanged.connect(self.on_cosmetic_parameter_changed)
        for widget in cosmetic_widgets_on_finish_editing:
            widget.editingFinished.connect(self.on_cosmetic_parameter_changed)
        for widget in cosmetic_widgets_on_toggle:
            widget.toggled.connect(self.on_cosmetic_parameter_changed)
        
        self.reset_parameters_button.clicked.connect(self.confirm_reset_parameters)
        
        self.slide_table.selectionModel().selectionChanged.connect(self.ui_manager.update_selection_dependent_ui)

        self.edit_selection_button.clicked.connect(self.open_edit_selection_dialog)
        self.select_all_button.clicked.connect(self.select_all_slides)
        self.select_video_button.clicked.connect(self.select_video_slides)
        self.select_audio_button.clicked.connect(self.select_audio_slides)
        
        self.preview_pinp_checkbox.toggled.connect(self._on_preview_toggled)
        
        self.slide_table_manager.model_changed.connect(self.parameters_changed_event)
        
        self.project_folder_label.clicked.connect(self.open_project_folder)
        self.output_folder_label.clicked.connect(self.open_output_folder)
        
        self.normalize_loudness_checkbox.toggled.connect(self.normalize_loudness_mode_label.setEnabled)
        self.normalize_loudness_checkbox.toggled.connect(self.normalize_loudness_mode_combo.setEnabled)

        self.clear_debug_button.clicked.connect(self.clear_debug_log)
        self.export_debug_button.clicked.connect(self.export_debug_log)
        self.verbose_debug_checkbox.toggled.connect(self.on_verbose_toggled)

    def on_verbose_toggled(self, checked):
        config.LOG_STATE_TRANSITIONS = checked
        if checked:
            self.write_debug("[INFO] Verbose logging enabled.")
        else:
            self.write_debug("[INFO] Verbose logging disabled.")

    def clear_debug_log(self):
        self.debug_text.clear()

    def _get_system_info(self):
        def _bytes_to_gb(bytes_val):
            return round(bytes_val / (1024 ** 3), 2)

        info_lines = [
            "================== System Information ==================",
            f"App Version: {self.__version__}",
            f"Platform: {platform.platform()}",
            f"Architecture: {platform.machine()}",
            f"Python Version: {sys.version}",
        ]

        cpu_info_lines = ["---------------------- CPU -----------------------"]
        try:
            cpu_info_lines.append(f"  Model: {platform.processor()}")
            cpu_info_lines.append(f"  Physical Cores: {psutil.cpu_count(logical=False)}")
            cpu_info_lines.append(f"  Logical Cores: {psutil.cpu_count(logical=True)}")
            cpu_freq = psutil.cpu_freq()
            if cpu_freq:
                cpu_info_lines.append(f"  Max Frequency: {cpu_freq.max:.2f} Mhz")
        except Exception as e:
            cpu_info_lines.append(f"  Could not get detailed CPU info: {e}")
        info_lines.extend(cpu_info_lines)

        memory_info_lines = ["--------------------- Memory ---------------------"]
        try:
            vmem = psutil.virtual_memory()
            memory_info_lines.append(f"  Total: {_bytes_to_gb(vmem.total)} GB")
            memory_info_lines.append(f"  Available: {_bytes_to_gb(vmem.available)} GB")
            memory_info_lines.append(f"  Used: {_bytes_to_gb(vmem.used)} GB ({vmem.percent}%)")
        except Exception as e:
            memory_info_lines.append(f"  Could not get memory info: {e}")
        info_lines.extend(memory_info_lines)
        
        disk_info_lines = ["------------------ Disk Partitions -----------------"]
        try:
            partitions = psutil.disk_partitions()
            for part in partitions:
                # Skip optical drives and unformatted partitions
                if 'cdrom' in part.opts or not part.fstype:
                    continue
                disk_info_lines.append(f"  Device: {part.device} (Mount: {part.mountpoint}, FSType: {part.fstype})")
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disk_info_lines.append(f"    Total: {_bytes_to_gb(usage.total)} GB")
                    disk_info_lines.append(f"    Used: {_bytes_to_gb(usage.used)} GB ({usage.percent}%)")
                    disk_info_lines.append(f"    Free: {_bytes_to_gb(usage.free)} GB")
                except Exception:
                    disk_info_lines.append(f"    Could not retrieve usage for this partition.")
        except Exception as e:
            disk_info_lines.append(f"  Could not get disk partitions: {e}")
        info_lines.extend(disk_info_lines)

        libs_info_lines = ["----------------- Library Versions -----------------"]
        libs_to_check = {
            "PyMuPDF": fitz,
            "Pillow": PIL,
            "PySide6": PySide6,
            "toml": toml,
            "Imagehash": imagehash
        }
        for name, lib in libs_to_check.items():
            try:
                version = getattr(lib, '__version__', 'N/A')
                libs_info_lines.append(f"  {name}: {version}")
            except Exception:
                libs_info_lines.append(f"  {name}: Error getting version")
        info_lines.extend(libs_info_lines)

        info_lines.append("------------------ FFmpeg Information ------------------")
        try:
            ffmpeg_path = get_ffmpeg_path()
            
            result = subprocess.run(
                [str(ffmpeg_path), "-version"], 
                capture_output=True, text=True, timeout=5, 
                encoding='utf-8', errors='replace',
                creationflags=config.SUBPROCESS_CREATION_FLAGS
            )
            ffmpeg_version = result.stdout.splitlines()[0].strip() if result.stdout else "N/A"
            info_lines.append(f"Source: {get_ffmpeg_source()}")
            info_lines.append(f"Version: {ffmpeg_version}")
            info_lines.append(f"Path: {str(ffmpeg_path)}")
        except Exception as e:
            info_lines.append(f"Could not retrieve FFmpeg info: {e}")

        info_lines.append("----------------- Available Encoders -----------------")
        if self.available_encoders_map:
            for codec, encoders in sorted(self.available_encoders_map.items()):
                info_lines.append(f"  {codec}: {', '.join(encoders)}")
        else:
            info_lines.append("No functional encoders detected or test not run yet.")

        info_lines.append("========================================================")
        
        return "\n".join(info_lines)

    def export_debug_log(self):
        if not self.project_model.output_folder or not self.project_model.output_folder.is_dir():
            QMessageBox.warning(self, self.tr("Output Folder Not Set"), self.tr("Please select a valid output folder before exporting the debug log."))
            return

        log_content = self.debug_text.toPlainText()
        if not log_content.strip():
            QMessageBox.information(self, self.tr("Log is Empty"), self.tr("There is no content to export."))
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = self.project_model.output_folder / f"debug_log_{timestamp}.txt"
        
        file_path_str, _ = QFileDialog.getSaveFileName(
            self, 
            self.tr("Save Debug Log"), 
            str(default_filename),
            "Text Files (*.txt);;All Files (*)"
        )

        if file_path_str:
            try:
                system_info = self._get_system_info()
                full_content = f"{system_info}\n\n[Log Start]\n{log_content}"
                with open(file_path_str, 'w', encoding='utf-8') as f:
                    f.write(full_content)
                QMessageBox.information(self, self.tr("Success"), self.tr("Debug log successfully exported to:\n{0}").format(file_path_str))
            except IOError as e:
                QMessageBox.critical(self, self.tr("Export Failed"), self.tr("An error occurred while writing the file:\n{0}").format(e))

    def on_codec_changed(self, new_codec: str):
        if self._is_syncing or not new_codec or new_codec == "Checking...":
            return

        self.project_model.parameters.codec = new_codec

        self._update_hardware_encoding_options(new_codec)

        current_hw_data = self.project_model.parameters.hardware_encoding
        available_hw_data = [self.hardware_encoding_combo.itemData(i) for i in range(self.hardware_encoding_combo.count())]

        new_hw_data = current_hw_data
        if current_hw_data not in available_hw_data:
            new_hw_data = None if None in available_hw_data else (available_hw_data[0] if available_hw_data else None)
        
        self.project_model.parameters.hardware_encoding = new_hw_data
        
        self.hardware_encoding_combo.blockSignals(True)
        index_to_set = self.hardware_encoding_combo.findData(new_hw_data)
        if index_to_set != -1:
            self.hardware_encoding_combo.setCurrentIndex(index_to_set)
        self.hardware_encoding_combo.blockSignals(False)

        self.on_encoding_mode_changed()

    def on_encoding_mode_changed(self):
        if self._is_syncing:
            return
        self.update_encoding_options()
        self.parameters_changed_event()

    def _request_delayed_parameter_update(self):
        if self._is_syncing:
            return
        self.parameter_update_timer.start(config.DURATION_RECALC_DELAY_MS)

    def on_parameter_changed(self):
        if self._is_syncing:
            return
        self._sync_ui_to_model()
        self.parameters_changed_event()

    def on_cosmetic_parameter_changed(self):
        if self._is_syncing:
            return
        self._sync_ui_to_model()

    def _on_preview_toggled(self, is_checked: bool):
        self.slide_table_manager.toggle_previews(is_checked)
        
    def update_encoding_options(self, _=None):
        mode = self.encoding_mode_combo.currentText()
        selected_codec = self.codec_combo.currentText()
        hw_encoder_name = self.hardware_encoding_combo.currentData()
        
        hw_codec_name = config.CODEC_MAP.get(selected_codec, {}).get(hw_encoder_name)
        is_videotoolbox = 'videotoolbox' in (hw_codec_name or '')

        self.pass_combo.setEnabled(True)

        if mode == config.ENCODING_MODES["QUALITY"]:
            self.value_label.setText(self.tr("Quality (0-51):"))
            self.value_spin.setSuffix("")
            self.value_spin.setRange(0, 51)
            self.value_spin.setSingleStep(1)
            self.value_spin.setValue(config.DEFAULT_CRF_VALUE)
            if is_videotoolbox:
                self.encoding_mode_combo.blockSignals(True)
                try:
                    self.encoding_mode_combo.setCurrentText(config.ENCODING_MODES["VBR"])
                finally:
                    self.encoding_mode_combo.blockSignals(False)
            else:
                 self.pass_combo.setEnabled(False)
        
        elif mode == config.ENCODING_MODES["VBR"]:
            self.value_label.setText(self.tr("Avg Bitrate (kbps):"))
            self.value_spin.setSuffix(self.tr(" kbps"))
            self.value_spin.setRange(*config.ENCODING_BITRATE_RANGE_KBPS)
            self.value_spin.setSingleStep(100)
            self.value_spin.setValue(config.DEFAULT_VBR_BITRATE)
            if is_videotoolbox:
                self.pass_combo.setEnabled(False)
            
        elif mode == config.ENCODING_MODES["CBR"]:
            self.value_label.setText(self.tr("Bitrate (kbps):"))
            self.value_spin.setSuffix(self.tr(" kbps"))
            self.value_spin.setRange(*config.ENCODING_BITRATE_RANGE_KBPS)
            self.value_spin.setSingleStep(100)
            self.value_spin.setValue(config.DEFAULT_CBR_BITRATE)
            if is_videotoolbox:
                self.pass_combo.setEnabled(False)
    
    def prompt_install_ffmpeg(self):
        reply = QMessageBox.question(self, self.tr('Install FFmpeg'), self.tr("This application requires FFmpeg to create videos.\n\nThis tool will attempt to install it using a system package manager (winget for Windows, Homebrew for macOS).\n\nNote:\n• The process may take several minutes.\n• Administrator privileges (password) may be required.\n\nDo you want to proceed with the installation?"), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.run_ffmpeg_installation()

    def run_ffmpeg_installation(self):
        self.progress_dialog = InstallProgressDialog(self)
        self.progress_dialog.show()
        self.installer_thread = FFmpegInstaller()
        self.installer_thread.log_message.connect(self.update_install_progress)
        self.installer_thread.finished.connect(self.on_ffmpeg_install_finished)
        self.installer_thread.start()
        
    def update_install_progress(self, message):
        if self.progress_dialog:
            self.progress_dialog.append_log(message)

    def on_ffmpeg_install_finished(self, success, message):
        try:
            if self.progress_dialog:
                self.progress_dialog._is_running = False
                self.progress_dialog.close()
            self.ffmpeg_installed = self.check_ffmpeg_exists()
            if success and self.ffmpeg_installed:
                QMessageBox.information(self, self.tr("Success"), self.tr("FFmpeg was installed successfully!\n\nYou can now proceed to use the application."))
                self.state_machine.transition_to(AppState.AWAITING_PROJECT)
            elif success and not self.ffmpeg_installed:
                QMessageBox.warning(self, self.tr("Action Required"), self.tr("The installation process finished, but the tools could not be detected in the system's PATH.\n\nPlease restart the application for the changes to take effect."))
                self.state_machine.transition_to(AppState.ERROR)
            else:
                QMessageBox.critical(self, self.tr("Failed"), message)
                self.state_machine.transition_to(AppState.ERROR)

            if self.ffmpeg_installed:
                self.available_encoders_map = {}
                self.worker_manager.start_encoder_test(self.validator)
        finally:
            if self.installer_thread:
                self.installer_thread.quit()
                self.installer_thread.wait()
                self.installer_thread = None
        
    def load_settings(self):
        file_to_load = self.settings_manager.prompt_for_load_path()
        if file_to_load:
            self.validator.clear_cache()
            self.state_machine.transition_to(AppState.LOADING_PROJECT)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.worker_manager.start_project_setup(self.settings_manager, self.validator, file_to_load)

    def on_project_setup_finished(self, loaded_model: ProjectModel):
        if not loaded_model:
            self.on_project_setup_error(self.tr("Load Error"), self.tr("The project model could not be loaded."))
            return

        self.project_model = loaded_model
        
        self.project_model.parameters.available_encoders = self.available_encoders_map
        self.slide_table_manager.project_model = self.project_model
        
        self._sync_model_to_ui()
        self.ui_manager.update_folder_label(self.project_folder_label, self.project_model.project_folder)
        self.ui_manager.update_folder_label(self.output_folder_label, self.project_model.output_folder)
        
        self.has_validated_once = False
        self.validation_results_text.setHtml("")
        
        # First, populate the UI with the loaded data.
        self.slide_table_manager.populate_slide_table_from_model()
        self.slide_table_manager.toggle_previews(self.preview_pinp_checkbox.isChecked())

        if not self.filename_input.text() and self.project_model.project_folder:
            self.filename_input.setText(self.project_model.project_folder.name)
        
        self.state_machine.transition_to(AppState.PROJECT_LOADED_UIPOPULATED)

    def on_project_setup_error(self, title, message):
        self.write_debug(f"[ERROR] {title}: {message}")
        QMessageBox.critical(self, title, message)
        self.state_machine.transition_to(AppState.AWAITING_PROJECT)
        QApplication.restoreOverrideCursor()

    def on_state_changed(self,  old_state: AppState, new_state: AppState):
        self.ui_manager.update_ui_for_state(new_state)

        if new_state == AppState.PROJECT_LOADED_UIPOPULATED:
            QApplication.restoreOverrideCursor()

            can_proceed = self._check_and_handle_pdf_changes()
            
            if can_proceed:
                if self.project_model.output_folder:
                    self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
                else:
                    self.state_machine.transition_to(AppState.PREPARE_TO_VALIDATE)
            else:
                self._clear_project()
                if self.state_machine.state == AppState.PROJECT_LOADED_UIPOPULATED:
                     self.state_machine.transition_to(AppState.AWAITING_PROJECT)

    def save_settings(self):
        if self.state_machine.state != AppState.VALIDATED:
            QMessageBox.warning(self, 
                self.tr("Validation Required"), 
                self.tr("The project must be successfully validated before saving the settings.")
            )
            return

        self._sync_ui_to_model()
        self.settings_manager.save_project_settings(self.project_model)

    def open_project_folder(self):
        if self.project_model.project_folder:
            self._open_folder_in_os(self.project_model.project_folder)

    def open_output_folder(self):
        if self.project_model.output_folder:
            self._open_folder_in_os(self.project_model.output_folder)

    def _open_folder_in_os(self, path: Path):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        except Exception as e:
            QMessageBox.critical(self, self.tr('Error'), self.tr('Failed to open folder:\n{0}').format(e))

    def _find_single_pdf(self, folder: Path) -> Optional[Path]:
        if not folder or not folder.is_dir():
            return None
        pdfs = list(folder.glob('*.[pP][dD][fF]'))
        if not pdfs:
            raise FileNotFoundError(self.tr("No PDF file found in the selected project folder."))
        if len(pdfs) > 1:
            raise ValueError(self.tr("Multiple PDF files found. Please ensure there is only one PDF in the folder."))
        return pdfs[0]

    def _rescan_available_materials(self):
        if self.project_model and self.project_model.project_folder:
            try:
                self.project_model.available_materials = sorted([
                    p.name for p in self.project_model.project_folder.iterdir()
                    if p.is_file() and p.suffix.lower() in config.SUPPORTED_FORMATS
                ])
            except Exception as e:
                self.write_debug(f"Error while rescanning material files: {e}")

    def check_for_updates(self):
        if self.__version__ == 'local-dev':
            self.write_debug("[INFO] Skipping update check for local development version.", 'app')
            return

        self.write_debug("[INFO] --- Checking for application updates ---", 'app')
        repo_url = config.REPO_URL
        repo_path = repo_url.replace("https://github.com/", "")
        api_url = f"https://api.github.com/repos/{repo_path}/releases/latest"

        try:
            from packaging.version import parse as parse_version
        except ImportError:
            self.write_debug("[WARNING] 'packaging' library not found. Skipping update check. Please run 'pip install packaging'.", 'app')
            return

        try:
            # Access GitHub API (timeout set to 5 seconds)
            req = request.Request(api_url, headers={'Accept': 'application/vnd.github.v3+json'})
            with request.urlopen(req, timeout=5) as response:
                if response.status != 200:
                    raise ConnectionError(f"GitHub API returned status {response.status}")
                
                data = json.loads(response.read().decode('utf-8'))
                latest_version_tag = data.get("tag_name", "v0.0.0").lstrip('v')
                release_url = data.get("html_url", "")

            self.write_debug(f"[INFO] Current version: {self.__version__}, Latest version on GitHub: {latest_version_tag}", 'app')

            # Compare versions
            if parse_version(latest_version_tag) > parse_version(self.__version__):
                self.write_debug(f"[INFO] New version {latest_version_tag} found!", 'app')
                
                # Show notification to the user
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(self.tr("New Version Available"))
                msg_box.setIcon(QMessageBox.Information)
                msg_box.setText(
                    self.tr("A new version <b>{0}</b> has been released.<br><br>"
                    "You are currently running version {1}.").format(latest_version_tag, self.__version__)
                )
                msg_box.setInformativeText(
                    self.tr("It is recommended to update to the latest version.<br>"
                    "<a href='{0}'>Open Download Page</a>").format(release_url)
                )
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.exec()
            else:
                self.write_debug("[INFO] You are running the latest version.", 'app')

        except Exception as e:
            self.write_debug(f"[WARNING] Could not check for updates. This may be due to being offline or a network issue. Error: {e}", 'app')

    def run_validation(self):
        if not self._check_and_handle_pdf_changes():
            return

        self._rescan_available_materials()
        self._sync_ui_to_model()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        
        self.state_machine.transition_to(AppState.VALIDATING)
        
        self.worker_manager.start_validation(self.validator, self.project_model, self.available_encoders_map)

    def on_validation_finished(self, messages: ValidationMessages, page_count: int, snapshot: dict):
        if self.state_machine.state != AppState.VALIDATING:
            self.write_debug(f"[INFO] Validation result was ignored because the state was '{self.state_machine.state.name}', not 'VALIDATING'.")
            self.on_worker_thread_finished()
            return

        self.on_worker_thread_finished()

        self.last_validation_messages = messages
        self.page_count = page_count
        self.validation_results_text.setHtml(messages.assemble_html(theme=self.current_theme))

        validation_had_errors = messages.has_errors()
        if not validation_had_errors:
            self.state_machine.transition_to(AppState.VALIDATED)

            if not self.has_validated_once:
                self.check_for_updates()

            self.validation_snapshot = snapshot
            self.has_validated_once = True

            self.tabs.setCurrentWidget(self.slide_settings_tab)
        else:
            self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
            QMessageBox.critical(self, self.tr("Validation Failed"), self.tr("Errors were found that prevent video creation.\nSee 'Validation Result' tab for details."))
            self.tabs.setCurrentWidget(self.validation_results_tab)
        
        self.slide_table_manager.populate_slide_table_from_model()
        self.slide_table_manager.toggle_previews(self.preview_pinp_checkbox.isChecked())
        
        self.on_worker_thread_finished()

    def on_validation_error(self, error_message: str):
        QMessageBox.critical(self, self.tr("Validation Error"), error_message)
        self.write_debug(f"[ERROR] Validation thread failed: {error_message}")
        self.has_validated_once = False
        messages = ValidationMessages()
        messages.add_project_error(self.tr("Validation failed with an unexpected error: {0}").format(error_message))
        self.last_validation_messages = messages
        self.validation_results_text.setHtml(messages.assemble_html(theme=self.current_theme))
        self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
        
        self.on_worker_thread_finished()

    def on_validation_canceled(self):
        messages = ValidationMessages()
        messages.add_project_notice(self.tr("Validation was canceled by the user."))
        self.last_validation_messages = messages
        self.validation_results_text.setHtml(messages.assemble_html(theme=self.current_theme))
        self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
        
        self.on_worker_thread_finished()

    def construct_final_video_path(self, project_model: ProjectModel) -> Path:
        filename_input = project_model.parameters.filename_input
        sanitized_filename = Path(filename_input).name if filename_input.strip() else ""
        if not sanitized_filename:
            sanitized_filename = project_model.project_folder.name if project_model.project_folder else "output"
        if project_model.parameters.append_duration_checkbox:
            total_duration = sum(s.duration for s in project_model.slides) + sum(s.interval_to_next for s in project_model.slides[:-1])
            minutes = int(total_duration // 60)
            secs = int(round(total_duration % 60))
            duration_str = f"{minutes}m{secs}s" if minutes > 0 else f"{secs}s"
            if duration_str != "0s":
                sanitized_filename += f"_{duration_str}"
        output_dir = project_model.output_folder if project_model.output_folder else Path(".")
        return output_dir / f"{sanitized_filename}.mp4"

    def run_create_video(self):
        if self.state_machine.state != AppState.VALIDATED:
            QMessageBox.warning(self, self.tr('Warning'), self.tr('Validation is not successful. Please validate again.'))
            return
            
        if not self._check_project_files_changed():
            return
                
        self._sync_ui_to_model()
        final_video_path = self.construct_final_video_path(self.project_model)
        if final_video_path.exists():
            reply = QMessageBox.question(self, self.tr('Confirm Overwrite'), self.tr("The file '{0}' already exists.\nDo you want to overwrite it?").format(final_video_path.name), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return

        self.video_creation_start_time = time.time()
        self.state_machine.transition_to(AppState.PROCESSING)
        
        self.project_model.parameters.filename_input = final_video_path.stem
        
        is_verbose = self.verbose_debug_checkbox.isChecked()
        self.worker_manager.start_video_creation(self.project_model, is_verbose)

    def run_preview_generation(self):
        if self.state_machine.state != AppState.VALIDATED:
            QMessageBox.warning(self, self.tr('Warning'), self.tr('Please run a successful validation before creating a preview.'))
            return

        if not self._check_project_files_changed():
            return
    
        self._sync_ui_to_model()

        dialog = SelectSlideDialog(len(self.project_model.slides), self)
        if dialog.exec() == QDialog.Accepted:
            selected_slide_index = dialog.get_selected_slide() - 1
            include_intervals = dialog.get_include_intervals()

            try:
                pdf_path = self._find_single_pdf(self.project_model.project_folder)
            except (FileNotFoundError, ValueError) as e:
                QMessageBox.critical(self, self.tr("PDF Not Found"), str(e))
                return

            self.video_creation_start_time = time.time()
            self.progress_bar.setValue(0)
            self.state_machine.transition_to(AppState.PROCESSING)
            
            is_verbose = self.verbose_debug_checkbox.isChecked()
            self.worker_manager.start_preview_creation(
                self.project_model, selected_slide_index, pdf_path, is_verbose, include_intervals
            )

    def cancel_video_creation(self):
        self.state_machine.transition_to(AppState.CANCELLING)
        self.worker_manager.cancel_all_tasks()
        self.cancel_button.setEnabled(False)
        self.progress_bar.setValue(0)

    def _on_processing_finished(self, success: bool, message: str, process_name: str):
        is_cancelling = self.state_machine.state == AppState.CANCELLING
        
        QTimer.singleShot(0, lambda: self._show_processing_result(success, message, process_name))
        
        self.state_machine.transition_to(AppState.VALIDATED)

        if is_cancelling:
             self.write_debug("Process was canceled.", 'app')

    def _show_processing_result(self, success: bool, message: str, process_name: str):
        if success:
            elapsed_time = time.time() - self.video_creation_start_time
            formatted_time = self._format_elapsed_time(elapsed_time)
            QMessageBox.information(self, self.tr('Success'), self.tr('{0} created successfully at {1}\nTime taken: {2}').format(process_name, message, formatted_time))
        elif message != "Canceled by user.":
            QMessageBox.critical(self, self.tr('Error'), self.tr('An error occurred during {0} creation:\n{1}').format(process_name.lower(), message))
        
        self.progress_bar.setValue(0)

    def on_video_creation_finished(self, success: bool, message: str):
        self._on_processing_finished(success, message, self.tr("Video"))
    
    def on_preview_finished(self, success: bool, message: str):
        self._on_processing_finished(success, message, self.tr("Preview"))

    def _clear_project(self):
        self.write_debug("[INFO] Clearing current project state due to cancellation or reset.")
        self.project_model = ProjectModel()
        self.slide_table_manager.project_model = self.project_model
        self.slide_table_manager.populate_slide_table_from_model()
        self.ui_manager.update_folder_label(self.project_folder_label, None)
        self.ui_manager.update_folder_label(self.output_folder_label, None)
        self.filename_input.setText("")
        self.validation_results_text.setHtml("")
        self.total_duration_label.setText(self.tr("Total Estimated Duration: 0s"))
        self.has_validated_once = False
        self.last_validation_messages = None
        self.validation_snapshot = {}

    def select_project_folder(self, force_folder: Path = None):
        folder_path = None
        if force_folder:
            folder_path = force_folder
        else:
            default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
            folder_str = QFileDialog.getExistingDirectory(self, self.tr("Select Project Folder"), dir=default_dir)
            if folder_str:
                folder_path = Path(folder_str)

        if not folder_path:
            return

        self.write_debug(f"[INFO] Project folder selected: {folder_path}")
        self.validator.clear_cache()
        self.state_machine.transition_to(AppState.LOADING_PROJECT)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.worker_manager.start_project_setup(self.settings_manager, self.validator, folder_path)

    def initialize_project_from_pdf(self, project_model: ProjectModel) -> Path | None:
        if not project_model.project_folder:
            raise ValueError("Project folder is not set.")
        
        pdf_path = self._find_single_pdf(project_model.project_folder)
        if not pdf_path:
            return None

        try:
            with fitz.open(pdf_path) as doc:
                page_count = doc.page_count
            project_model.slides = [Slide() for _ in range(page_count)]
            project_model.available_materials = sorted([
                p.name for p in project_model.project_folder.iterdir()
                if p.is_file() and p.suffix.lower() in config.SUPPORTED_FORMATS
            ])
            return pdf_path
        except Exception as e:
            raise ValueError(f"Failed to read PDF file '{pdf_path.name}': {e}")
            
    def _automap_materials(self, project_model: ProjectModel):
        for material_name in project_model.available_materials:
            match = re.match(r'\[(\d{3})\]', material_name)
            if match:
                slide_index = int(match.group(1)) - 1
                if 0 <= slide_index < len(project_model.slides) and project_model.slides[slide_index].filename is None:
                    project_model.slides[slide_index].filename = material_name

    def select_output_folder(self):
        folder_str = QFileDialog.getExistingDirectory(self, self.tr("Select Output Folder"))
        if not folder_str:
            return

        selected_folder = Path(folder_str)

        if self.project_model.project_folder and selected_folder.resolve() == self.project_model.project_folder.resolve():
            QMessageBox.warning(
                self,
                self.tr("Invalid Folder Selection"),
                self.tr("The output folder cannot be the same as the project folder.\n\n"
                "Please select a different directory to avoid conflicts with source material files.")
            )
            return

        self.write_debug(f"[INFO] Output folder selected: {selected_folder}")
        self.project_model.output_folder = selected_folder
        self.ui_manager.update_folder_label(self.output_folder_label, self.project_model.output_folder)
        if self.state_machine.state == AppState.PREPARE_TO_VALIDATE:
            self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
        else:
            self.parameters_changed_event()

    def confirm_reset_parameters(self):
        reply = QMessageBox.question(self, self.tr('Confirm Reset'), self.tr("Are you sure you want to reset all parameters to their default values?"), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            current_encoders = self.project_model.parameters.available_encoders
            self.project_model.parameters = ProjectParameters()
            self.project_model.parameters.available_encoders = current_encoders
            self._sync_model_to_ui()
            self.parameters_changed_event()
            QMessageBox.information(self, self.tr("Parameters Reset"), self.tr("All parameters have been reset to their default values."))

    def show_transition_gallery(self):
        if self.gallery_window is None:
            self.gallery_window = QWidget()
            self.gallery_window.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
            self.gallery_window.setWindowTitle(self.tr("Transition Gallery"))
            self.gallery_window.setMinimumSize(700, 600)

            main_layout = QVBoxLayout(self.gallery_window)
            
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            main_layout.addWidget(scroll_area)

            container_widget = QWidget()
            grid_layout = QGridLayout(container_widget)
            grid_layout.setSpacing(10)
            scroll_area.setWidget(container_widget)
            
            agif_folder = resolve_resource_path("agif")
            col_count = 3
            row, col = 0, 0
            
            transitions = list(config.TRANSITION_MAPPINGS.keys())[1:]
            for i, name in enumerate(transitions, 1):
                gif_path = agif_folder / f"{i:02d}{name.replace(' ', '')}.gif"
                
                item_widget = HoverGifWidget(gif_path, name)
                grid_layout.addWidget(item_widget, row, col)
                
                col += 1
                if col >= col_count:
                    col = 0
                    row += 1

        self.gallery_window.show()
        self.gallery_window.raise_()
        self.gallery_window.activateWindow()

    def closeEvent(self, event):
        if self.gallery_window:
            self.gallery_window.close()

        current_state = self.state_machine.state
        is_processing = current_state in [
            AppState.PROCESSING, AppState.VALIDATING, AppState.CHECKING_ENCODERS,
            AppState.CANCELLING, AppState.LOADING_PROJECT
        ]

        if is_processing:
            reply = QMessageBox.question(
                self, self.tr('Confirm Exit'),
                self.tr("A task is currently running. Are you sure you want to exit?"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            else:
                self.worker_manager.cancel_all_tasks()
        else:
            if self.has_validated_once and current_state in [AppState.READY_TO_VALIDATE, AppState.PREPARE_TO_VALIDATE]:
                reply = QMessageBox.question(
                    self, self.tr('Confirm Exit'),
                    self.tr("You have unsaved changes. Are you sure you want to exit?"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.No:
                    event.ignore()
                    return
        
        self.worker_manager.shutdown_persistent_workers()

        event.accept()

    def parameters_changed_event(self, _=None):
        if self.state_machine.state in [AppState.VALIDATED, AppState.READY_TO_VALIDATE, AppState.PREPARE_TO_VALIDATE]:
            if self.project_model.output_folder:
                 self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
            else:
                 self.state_machine.transition_to(AppState.PREPARE_TO_VALIDATE)

    def write_debug(self, text, source='app'):
        is_verbose_app_log = (source == 'verbose_app')

        if (source == 'ffmpeg' or is_verbose_app_log) and not self.verbose_debug_checkbox.isChecked():
            return

        self.debug_text.moveCursor(QTextCursor.End)
        cursor = self.debug_text.textCursor()
        char_format = QTextCharFormat()
        
        final_text = text
        if source == 'app' or is_verbose_app_log:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            final_text = f"[{timestamp}] {text}"
            
            stripped_text = text.strip()
            if stripped_text.startswith(('[ERROR]', '[FATAL]')):
                char_format.setForeground(QColor("red"))
            elif stripped_text.startswith('[WARNING]'):
                char_format.setForeground(QColor("blue"))
            else:
                char_format.setForeground(QColor("green"))

        cursor.setCharFormat(char_format)
        cursor.insertText(final_text.rstrip() + '\n')
        cursor.setCharFormat(QTextCharFormat())

    def update_progress_bar(self, value):
        self.progress_bar.setValue(value)

    def _check_project_files_changed(self) -> bool:
        if self.project_model.project_folder:
            current_snapshot = self._gather_project_file_hashes(self.project_model.project_folder)
            if not self.validation_snapshot == current_snapshot:
                QMessageBox.warning(self, self.tr('Project Folder Changed'),
                                    self.tr("The contents of the project folder have been modified since the last validation.\n"
                                    "Please re-validate before proceeding."))
                self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
                return False
        return True

    def _gather_project_file_hashes(self, folder: Path) -> dict:
        if not folder: return {}
        snapshot = {}
        all_formats = config.SUPPORTED_FORMATS + ('.pdf',)
        for entry in folder.iterdir():
            if entry.is_file() and entry.suffix.lower() in all_formats:
                try:
                    snapshot[entry.name] = self.validator._get_file_hash(entry)
                except (IOError, OSError) as e:
                    self.write_debug(f"Could not calculate hash for file {entry.name}: {e}")
                    snapshot[entry.name] = None
        return snapshot

    def _format_elapsed_time(self, seconds):
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        if h > 0: return f"{h}h {m}m {s}s"
        if m > 0: return f"{m}m {s}s"
        return f"{s}s"
    
    def open_edit_selection_dialog(self):
        selected_rows = self.slide_table.selectionModel().selectedRows()
        if not selected_rows:
            return

        slide_info_list = []
        for r in sorted(selected_rows, key=lambda item: item.row()):
            slide_index = r.row()
            slide = self.project_model.slides[slide_index]
            slide_type = "unassigned"
            if slide.is_video:
                slide_type = "movie"
            elif slide.filename == config.SILENT_MATERIAL_NAME:
                slide_type = "silent"
            elif slide.filename is not None:
                slide_type = "audio"
            
            slide_info_list.append({"number": slide_index + 1, "type": slide_type})
        
        has_video_slides = any(s["type"] == "movie" for s in slide_info_list)
        has_non_video_slides = not all(s["type"] == "movie" for s in slide_info_list)
        is_mixed_selection = has_video_slides and has_non_video_slides

        is_only_last_slide_selected = (len(selected_rows) == 1 and 
                                       selected_rows[0].row() == self.slide_table.rowCount() - 1)
        
        dialog = EditSlidesDialog(slide_info_list, has_video_slides, is_mixed_selection, is_only_last_slide_selected, self)
        
        if dialog.exec() == QDialog.Accepted:
            changes = dialog.get_changes()
            if not changes:
                return

            self.slide_table.blockSignals(True)
            try:
                for row_item in selected_rows:
                    slide_index = row_item.row()
                    slide = self.project_model.slides[slide_index]

                    is_last_slide = (slide_index == self.slide_table.rowCount() - 1)

                    if not is_last_slide:
                        if 'transition_to_next' in changes:
                            slide.transition_to_next = changes['transition_to_next']
                        if 'interval_to_next' in changes:
                            slide.interval_to_next = changes['interval_to_next']

                    if slide.is_video:
                        if 'video_position' in changes:
                            slide.video_position = changes['video_position']
                        if 'video_scale' in changes:
                            slide.video_scale = changes['video_scale']
                        if 'video_effects' in changes:
                            slide.video_effects = changes['video_effects']
            finally:
                self.slide_table.blockSignals(False)

            self.slide_table_manager.populate_slide_table_from_model()
            self.slide_table_manager.toggle_previews(self.preview_pinp_checkbox.isChecked())
            self.parameters_changed_event()
            QMessageBox.information(self, self.tr("Success"), self.tr("Settings have been applied to the selected slides."))

    def select_all_slides(self):
        if self.slide_table.rowCount() > 0:
            self.slide_table.selectAll()

    def select_video_slides(self):
        selection_model = self.slide_table.selectionModel()
        selection_model.clear()
        
        selection = QItemSelection()
        model = self.slide_table.model()

        for i, slide in enumerate(self.project_model.slides):
            if slide.is_video:
                top_left = model.index(i, 0)
                bottom_right = model.index(i, self.slide_table.columnCount() - 1)
                selection.select(top_left, bottom_right)
        
        selection_model.select(selection, QItemSelectionModel.Select)

    def select_audio_slides(self):
        selection_model = self.slide_table.selectionModel()
        selection_model.clear()

        selection = QItemSelection()
        model = self.slide_table.model()
        
        for i, slide in enumerate(self.project_model.slides):
            is_audio_only = (
                slide.filename is not None and
                not slide.is_video and
                slide.filename.lower().endswith(config.SUPPORTED_AUDIO_FORMATS)
            )
            if is_audio_only:
                top_left = model.index(i, 0)
                bottom_right = model.index(i, self.slide_table.columnCount() - 1)
                selection.select(top_left, bottom_right)
        
        selection_model.select(selection, QItemSelectionModel.Select)

    def _ffmpeg_missing_message(self) -> str:
        base_message = self.tr("A matching pair of ffmpeg and ffprobe executables was not found. This application requires FFmpeg.")
        
        user_folder_tip = self.tr(
            "As an alternative to a system-wide installation, "
            "you can create a folder named 'ffmpeg-bin' in your user home directory "
            "and place both the ffmpeg and ffprobe executables inside it."
        )

        install_menu_tip = ""
        if self.capabilities.get("FFMPEG_INSTALL_MENU", True):
            install_menu_tip = self.tr("You can also use the 'Tools -> Install FFmpeg...' menu to install them system-wide.")

        final_message = "\n\n".join(filter(None, [base_message, user_folder_tip, install_menu_tip]))
        return final_message

    def _check_and_handle_pdf_changes(self) -> bool:
        try:
            pdf_path = self._find_single_pdf(self.project_model.project_folder)
            if not pdf_path:
                self._cancel_folder_selection()
                return False
        except (FileNotFoundError, ValueError) as e:
            QMessageBox.critical(self, self.tr("PDF Error"), str(e))
            self._cancel_folder_selection()
            return False

        current_pdf_hash = self.validator._get_file_hash(pdf_path)
        current_pdf_structure = self.validator._get_pdf_structure(pdf_path)
        
        if self.validator.validated_pdf_hash and self.validator.validated_pdf_hash == current_pdf_hash:
            self.write_debug("[INFO] PDF file has not changed. Skipping detailed PDF change analysis.")
            return True

        old_page_count = len(self.project_model.slides)
        new_page_count = current_pdf_structure.get('page_count', 0)

        if old_page_count > 0 and old_page_count != new_page_count:
            self.write_debug("[INFO] PDF page structure has changed.")
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setWindowTitle(self.tr("PDF Structure Changed"))
            msg_box.setText(
                self.tr("The number of pages in the PDF has changed from {0} to {1}.\n\n"
                "How would you like to proceed?").format(old_page_count, new_page_count)
            )
            migrate_button = msg_box.addButton(self.tr("Migrate Settings (Experimental)"), QMessageBox.ActionRole)
            reinit_button = msg_box.addButton(self.tr("Re-initialize Project"), QMessageBox.ActionRole)
            cancel_button = msg_box.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
            msg_box.exec()

            if msg_box.clickedButton() == migrate_button:
                return self._migrate_slide_data(pdf_path)
            elif msg_box.clickedButton() == reinit_button:
                self._initialize_new_project(pdf_path)
                return True
            else:
                self._cancel_folder_selection()
                return False
        
        elif old_page_count > 0 and old_page_count == new_page_count:
             self.write_debug("[INFO] PDF content changed without altering page structure. Auto-updating thumbnails.")
             self.validator.compute_and_populate_pdf_details(self.project_model)
             self.slide_table_manager.populate_slide_table_from_model()
             QMessageBox.information(self, self.tr("PDF Content Updated"),
                                     self.tr("The content of the PDF file was modified.\n"
                                     "Thumbnails have been automatically updated."))
        else:
            self._initialize_new_project(pdf_path)
        
        return True

    def _cancel_folder_selection(self):
        self.write_debug("[INFO] User cancelled the project folder operation.")
        self._clear_project()
        self.state_machine.transition_to(AppState.AWAITING_PROJECT)

    def _initialize_new_project(self, pdf_path: Path):
        self.write_debug("[INFO] Initializing new project from PDF.")
        self.project_model.slides.clear()
        
        try:
            with fitz.open(pdf_path) as doc:
                page_count = doc.page_count
            self.project_model.slides = [Slide() for _ in range(page_count)]
            self._rescan_available_materials()
            
            self.validator.compute_and_populate_pdf_details(self.project_model)
            
            self.slide_table_manager.populate_slide_table_from_model()
        except Exception as e:
            QMessageBox.critical(self, self.tr("Error Initializing Project"), f"Could not process the PDF file: {e}")
            self._cancel_folder_selection()

    def _migrate_slide_data(self, pdf_path: Path) -> bool:
        self.write_debug("[INFO] Starting slide data migration process.")
        old_slides = copy.deepcopy(self.project_model.slides)
        
        new_pdf_details, error = self.validator.get_pdf_details(pdf_path)
        if error:
            QMessageBox.critical(self, self.tr("Migration Error"), f"Could not get details from the new PDF: {error}")
            return False

        if not new_pdf_details:
            QMessageBox.critical(self, self.tr("Migration Error"), self.tr("Could not get details from the new PDF for an unknown reason."))
            return False

        if not self._start_pdf_migration(old_slides, new_pdf_details):
            # User cancelled the migration dialog, revert to old state
            self.project_model.slides = old_slides
            self.slide_table_manager.populate_slide_table_from_model()
            self._cancel_folder_selection()
            return False
            
        return True

    def _start_pdf_migration(self, old_slides: list[Slide], new_pdf_details: dict) -> bool:
        dialog = PageMappingDialog(old_slides, new_pdf_details, self)
        if dialog.exec() == QDialog.Accepted:
            mapping = dialog.get_mapping()
            self._apply_slide_migration(mapping, old_slides, new_pdf_details)
            QMessageBox.information(self, self.tr("Success"), self.tr("Slide settings have been migrated to the new PDF structure."))
            return True
        else:
            self.write_debug("[INFO] PDF migration was cancelled by the user.")
            return False

    def _apply_slide_migration(self, mapping: dict, old_slides: list[Slide], new_pdf_details: dict):
        new_page_count = new_pdf_details["page_count"]
        migrated_slides = [Slide() for _ in range(new_page_count)]

        for new_idx, old_idx in mapping.items():
            if old_idx is not None and 0 <= old_idx < len(old_slides):
                migrated_slides[new_idx] = copy.deepcopy(old_slides[old_idx])

        for i in range(new_page_count):
            migrated_slides[i].p_hash = new_pdf_details["p_hashes"][i]
            migrated_slides[i].thumbnail_b64 = new_pdf_details["thumbnails_b64"][i]
        
        self.project_model.slides = migrated_slides
        
        self.slide_table_manager.project_model = self.project_model
        self.slide_table_manager.populate_slide_table_from_model()
        self.parameters_changed_event()