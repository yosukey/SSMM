# main_window.py
import copy
import datetime
import json
import re
import time
from pathlib import Path
from urllib import request
from packaging.version import InvalidVersion, parse as parse_version
from typing import Optional, Callable

from PySide6.QtCore import (QItemSelection, QItemSelectionModel, QObject, Qt,
                            QThread, QTimer, QUrl, Signal, QStandardPaths)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QMovie,
                           QPalette, QTextCharFormat, QTextCursor, QShowEvent)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QDialog, QFileDialog, QGridLayout, QLabel,
                               QMenuBar, QMessageBox, QScrollArea, QStyle,
                               QVBoxLayout, QWidget)

from ssmm import app_settings
from ssmm import config
from ssmm import pdf_utils
from ssmm import project_init
from ssmm.ffmpeg_installer import FFmpegInstaller
from ssmm.media_utils import format_duration_suffix, slide_timeline
from ssmm.models import (AppState, ProjectModel, ProjectParameters, Slide,
                    ValidationMessages)
from ssmm.settings_manager import SettingsManager
from ssmm.system_info import collect_system_info
from ssmm.slide_table_manager import SlideTableManager
from ssmm.ui_dialogs import (EditSlidesDialog, InstallProgressDialog,
                        SelectSlideDialog, PageMappingDialog)
from ssmm.ui_main import Ui_MainWindow
from ssmm.ui_state_manager import UIStateManager
from ssmm.utils import get_ffmpeg_path, resolve_resource_path
from ssmm.validator import ProjectValidator
from ssmm.worker_manager import WorkerManager

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
        self.gif_label.setObjectName("gifPreview")
        self.gif_label.setAlignment(Qt.AlignCenter)
        self.gif_label.setFixedSize(200, 150)

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
    """File-like object that forwards complete lines to the debug log.

    print() writes the text and the trailing newline in separate calls, so the
    stream buffers until a newline arrives; emitting each fragment would add an
    empty time-stamped line for every print.
    """
    text_written = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""

    def write(self, text):
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.text_written.emit(line, 'app')
        return len(str(text))

    def flush(self):
        if self._buffer.strip():
            self.text_written.emit(self._buffer, 'app')
        self._buffer = ""


class UpdateCheckWorker(QThread):
    """Queries the latest GitHub release off the GUI thread."""
    result_ready = Signal(str, str, str)  # latest version tag, release url, error message

    def __init__(self, api_url: str, parent=None):
        super().__init__(parent)
        self._api_url = api_url

    def run(self):
        try:
            req = request.Request(self._api_url, headers={'Accept': 'application/vnd.github.v3+json'})
            with request.urlopen(req, timeout=config.UPDATE_CHECK_TIMEOUT_S) as response:
                if response.status != 200:
                    raise ConnectionError(f"GitHub API returned status {response.status}")
                data = json.loads(response.read().decode('utf-8'))
            self.result_ready.emit(data.get("tag_name", "v0.0.0").lstrip('v'), data.get("html_url", ""), "")
        except Exception as e:
            self.result_ready.emit("", "", str(e))

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
        # Tracks unsaved edits to parameters or slide settings so the app can
        # confirm before discarding them (on exit or when loading another project).
        self._dirty = False
        self.validation_snapshot = {}
        self.page_count = 0
        self._is_syncing = False
        self.last_validation_messages: ValidationMessages | None = None
        
        self.parameter_update_timer = QTimer(self)
        self.parameter_update_timer.setSingleShot(True)
        self.parameter_update_timer.timeout.connect(self.on_parameter_changed)

        # Debounces watermark spinbox valueChanged so rapid arrow/typing changes
        # coalesce into a single cosmetic refresh.
        self.cosmetic_update_timer = QTimer(self)
        self.cosmetic_update_timer.setSingleShot(True)
        self.cosmetic_update_timer.timeout.connect(self.on_cosmetic_parameter_changed)
        
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
        self._pending_recent_path = None
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

        saved_geometry = app_settings.get_window_geometry()
        if saved_geometry:
            self.restoreGeometry(saved_geometry)

        self._setup_connections()
        
        self._setup_licenses_tab()
        self._setup_font_license_tab()
        self._setup_thirdparty_license_tab()
        self._setup_disclaimer_tab()
        
        self._sync_model_to_ui()

        self.on_state_changed(None, self.state_machine.state)

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        # The initial encoder check normally starts on first window activation, but a
        # window shown minimized or without focus may never receive that event and
        # would sit in CHECKING_ENCODERS with everything disabled. Schedule an
        # idempotent fallback so startup still proceeds.
        if self._is_first_activation:
            QTimer.singleShot(1500, self._ensure_initial_checks)

    def handle_focus_changed(self, focus_window):
        if self._is_first_activation and focus_window is self.windowHandle():
            self._ensure_initial_checks()

    def _ensure_initial_checks(self):
        if not self._is_first_activation:
            return
        self._is_first_activation = False
        self.perform_initial_checks()

    def perform_initial_checks(self):
        if self.ffmpeg_installed:
            self.worker_manager.start_encoder_test(self.validator)
        else:
            msg = self.ffmpeg_missing_message()
            self.write_debug(f"[FATAL] {msg}")
            QMessageBox.critical(self, self.tr("FFmpeg Not Found"), msg)
            self.state_machine.transition_to(AppState.ERROR)

    def on_transient_worker_finished(self):
        if self._next_startup_action:
            action = self._next_startup_action
            self._next_startup_action = None
            action()

    def on_transient_worker_busy(self, message: str):
        # A transient task was requested while one was already running, so the new request
        # was dropped. Its caller may have pushed a wait cursor that no finished-handler will
        # pop; restore it and tell the user. The in-flight task keeps running and manages the
        # app state itself, so we deliberately do not change the state machine here.
        QApplication.restoreOverrideCursor()
        self.write_debug(f"[WARNING] {message}")
        QMessageBox.information(self, self.tr("Please Wait"), message)

    def on_worker_thread_finished(self):
        QApplication.restoreOverrideCursor()
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(False)

    def on_encoder_test_finished(self, encoders_map, logs):
        self.write_debug("[INFO] \n--- Hardware Encoder Test Finished (Background) ---")
        for log in logs:
            self.write_debug(log)

        self.available_encoders_map = encoders_map
        self.project_model.parameters.available_encoders = self.available_encoders_map

        # Surface the available-encoder matrix in the Validation Result tab right
        # away, so the user can review encoder availability before running any
        # validation. A real validation report (if one already exists) is kept.
        self._show_encoder_status_preview()

        # Repopulating the combo boxes emits currentIndexChanged on widgets whose
        # slots mark the project dirty. Guard with _is_syncing so this programmatic
        # refill is not mistaken for a user edit; otherwise the very first
        # project-folder selection would raise a spurious "unsaved changes" prompt.
        self._is_syncing = True
        try:
            self._populate_comboboxes()
        finally:
            self._is_syncing = False
        self._sync_model_to_ui()

        # Startup population is not a user edit: the app has just reached its
        # initial state, so it must be considered clean regardless of any signals
        # the steps above may have emitted.
        self._clear_dirty()

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
            self._start_project_load(path)
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
        # FFmpeg is intentionally not bundled: a system build is used so that the
        # hardware encoders (NVENC, QSV, AMF, VideoToolbox) of the machine work.
        self.ffmpeg_notice_label.setText(
            self.tr("<b>FFmpeg</b><br>"
            "SSMM does not include FFmpeg. It uses the FFmpeg installed on this computer "
            "(a folder named 'ffmpeg-bin' in your home directory, the system PATH, or a "
            "package manager such as winget or Homebrew), so that the hardware encoders "
            "of your GPU can be used. FFmpeg is licensed separately by the "
            "<a href='https://www.ffmpeg.org/legal.html'>FFmpeg project</a>; refer to the "
            "license of the build you installed.")
        )

    def _setup_font_license_tab(self):
        try:
            font_license_path = resolve_resource_path("resources/fonts/OFL.txt")
            if font_license_path.exists():
                with open(font_license_path, 'r', encoding='utf-8') as f:
                    self.font_license_text_edit.setText(f.read())
            else:
                self.font_license_text_edit.setText(self.tr("Font license file (OFL.txt) not found in resources."))
        except Exception as e:
            self.font_license_text_edit.setText(self.tr("Error loading font license file: {0}").format(e))
            self.write_debug(f"Error loading font license: {e}")

    def _setup_thirdparty_license_tab(self):
        try:
            licenses_path = resolve_resource_path("resources/licenses/THIRD_PARTY_LICENSES.txt")
            if licenses_path.exists():
                with open(licenses_path, 'r', encoding='utf-8') as f:
                    self.thirdparty_license_text_edit.setText(f.read())
            else:
                self.thirdparty_license_text_edit.setText(self.tr(
                    "The consolidated third-party library license notices are generated "
                    "when the application is packaged for release. When running from source, "
                    "see the 'Third-Party Components & Licenses' section in README.md, or the "
                    "license files bundled with each installed package."
                ))
        except Exception as e:
            self.thirdparty_license_text_edit.setText(self.tr("Error loading third-party license file: {0}").format(e))
            self.write_debug(f"Error loading third-party licenses: {e}")

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
        self.import_dmj_action = QAction(self.tr("Import &DougaMeijin Project..."), self)
        self.import_dmj_action.triggered.connect(self.import_dougameijin_project)
        exit_action = QAction(self.tr("E&xit"), self)
        exit_action.triggered.connect(self.close)
        file_menu.addActions([self.load_settings_action, self.save_settings_action])
        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self._rebuild_recent_menu()
        file_menu.addAction(self.import_dmj_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu(self.tr("&View"))
        theme_menu = view_menu.addMenu(self.tr("Theme"))
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        theme_options = [
            ("system", self.tr("System Default")),
            ("light", self.tr("Light")),
            ("dark", self.tr("Dark")),
        ]
        active_theme = app_settings.get_theme()
        for code, label in theme_options:
            action = QAction(label, self, checkable=True)
            action.setChecked(code == active_theme)
            action.triggered.connect(lambda checked, c=code: self._change_theme(c))
            theme_group.addAction(action)
            theme_menu.addAction(action)

        language_menu = view_menu.addMenu(self.tr("Language"))
        self._active_language = app_settings.get_language()
        language_group = QActionGroup(self)
        language_group.setExclusive(True)
        # Native-name labels (English / 日本語) are intentionally not translated;
        # "System Default" is, since it describes a behavior rather than a language.
        language_options = [
            ("system", self.tr("System Default")),
            ("en", "English"),
            ("ja", "日本語"),
        ]
        for code, label in language_options:
            action = QAction(label, self, checkable=True)
            action.setChecked(code == self._active_language)
            action.triggered.connect(lambda checked, c=code: self._change_language(c))
            language_group.addAction(action)
            language_menu.addAction(action)

        tools_menu = menu_bar.addMenu(self.tr("&Tools"))
        if self.capabilities.get("FFMPEG_INSTALL_MENU", True):
            self.install_ffmpeg_action = QAction(self.tr("Install FFmpeg..."), self)
            self.install_ffmpeg_action.triggered.connect(self.prompt_install_ffmpeg)
            tools_menu.addAction(self.install_ffmpeg_action)
        self.show_gallery_action = QAction(self.tr("Transition &Gallery..."), self)
        self.show_gallery_action.triggered.connect(self.show_transition_gallery)
        tools_menu.addAction(self.show_gallery_action)
        
        help_menu = menu_bar.addMenu(self.tr("&Help"))
        
        self.check_for_updates_action = QAction(self.tr("Check for &Updates..."), self)
        self.check_for_updates_action.triggered.connect(self.check_for_updates)
        help_menu.addAction(self.check_for_updates_action)
        
        self.repository_action = QAction(self.tr("Visit Project Repository"), self)
        self.repository_action.triggered.connect(self._open_repository_url)
        help_menu.addAction(self.repository_action)
        
        return menu_bar

    def _change_theme(self, code):
        # Unlike language, theme changes apply immediately: qdarktheme only swaps
        # the application stylesheet, so no UI re-translation is needed. apply()
        # layers our accent + bespoke QSS on top and returns the concrete theme.
        from ssmm import theme as app_theme
        self.current_theme = app_theme.apply(QApplication.instance(), code)
        app_settings.set_theme(code)

        # The slide table's selection colors are hardcoded per theme and were only
        # applied once at construction; rebuild them so a runtime theme switch does
        # not leave stale (wrong-theme) highlight colors on the table.
        from ssmm.ui_main import slide_table_stylesheet
        self.slide_table.setStyleSheet(slide_table_stylesheet())

        if self.last_validation_messages:
            self.validation_results_text.setHtml(self.last_validation_messages.assemble_html(theme=self.current_theme))
        else:
            # No validation report is shown; re-render the encoder-matrix preview
            # (if any) so its colors follow the newly selected theme.
            self._show_encoder_status_preview()

    def _rebuild_recent_menu(self):
        self.recent_menu.clear()
        recent = app_settings.get_recent_projects()
        if not recent:
            placeholder = self.recent_menu.addAction(self.tr("(No Recent Projects)"))
            placeholder.setEnabled(False)
            return
        fm = self.fontMetrics()
        max_width = int(self.screen().availableGeometry().width() * 0.5) if self.screen() else 600
        for path_str in recent:
            label = fm.elidedText(path_str, Qt.ElideMiddle, max_width)
            # Parent the action to the menu so recent_menu.clear() deletes it; parenting
            # to the window would leak a QAction on every rebuild.
            action = QAction(label.replace("&", "&&"), self.recent_menu)
            action.setToolTip(path_str)
            action.triggered.connect(lambda checked, p=path_str: self._open_recent_project(p))
            self.recent_menu.addAction(action)
        self.recent_menu.addSeparator()
        clear_action = self.recent_menu.addAction(self.tr("Clear Recent List"))
        clear_action.triggered.connect(self._clear_recent_projects)

    def _clear_recent_projects(self):
        app_settings.clear_recent_projects()
        self._rebuild_recent_menu()

    def _open_recent_project(self, path_str):
        path = Path(path_str)
        if not path.exists():
            QMessageBox.warning(
                self, self.tr("Project Not Found"),
                self.tr("The project could not be found and will be removed from the recent list:\n\n{0}").format(path_str)
            )
            self._rebuild_recent_menu()
            return
        self._start_project_load(path)

    def _change_language(self, code):
        app_settings.set_language(code)
        # The effective language only changes at startup (UI strings are fixed at
        # construction), so prompt for a restart only when the selection differs
        # from the language currently in effect.
        if code == self._active_language:
            return
        QMessageBox.information(
            self, self.tr("Language"),
            self.tr("The language change will take effect after you restart the application.")
        )

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
            # currentText() feeds params.codec, so keep this placeholder English (not a real codec).
            self.codec_combo.addItem("No Encoders Found")
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
        ch_low, ch_high = config.AUDIO_CHANNELS_RANGE
        for i in range(ch_low, ch_high + 1):
            text = channel_labels.get(i, str(i))
            self.audio_channels_combo.addItem(text, userData=i)
        
        self.watermark_color_combo.clear()
        self.watermark_color_combo.addItems(config.WATERMARK_COLOR_OPTIONS_RGB.keys())
        
        self.watermark_fontfamily_combo.clear()
        self.watermark_fontfamily_combo.addItems(list(config.BUNDLED_FONTS.keys()))
        
        self.watermark_rotation_combo.clear()
        self.watermark_rotation_combo.addItem(self.tr("None"), "None")
        self.watermark_rotation_combo.addItem(self.tr("45 Degrees (Clockwise)"), "45")
        self.watermark_rotation_combo.addItem(self.tr("-45 Degrees (C-Clockwise)"), "-45")
        
        # Item data holds the parameter value (a config key); the text is only a label.
        self.encoding_mode_combo.clear()
        for key, label in config.ENCODING_MODES.items():
            self.encoding_mode_combo.addItem(label, key)

        self.pass_combo.clear()
        for key, label in config.ENCODING_PASSES.items():
            self.pass_combo.addItem(label, key)
        
        for combo in self.parameters_tabs.findChildren(QComboBox):
            self._adjust_combo_box_view_width(combo)

    def update_hardware_encoding_options(self, codec: str):
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
                    # Sits beside raw encoder codes (e.g. nvenc/qsv) and maps to
                    # userData=None; keep the label untranslated as a setting value.
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
        params.encoding_mode = self.encoding_mode_combo.currentData() or "QUALITY"
        params.encoding_value = self.value_spin.value()
        params.encoding_pass = self.pass_combo.currentData() or "ONE_PASS"
        params.audio_bitrate = self.audio_bitrate_combo.currentText()
        params.audio_sample_rate = self.audio_sample_rate_combo.currentText()
        params.audio_channels = self.audio_channels_combo.currentData()
        params.normalize_loudness = self.normalize_loudness_checkbox.isChecked()
        params.normalize_loudness_mode = self.normalize_loudness_mode_combo.currentData() or "TWO_PASS"
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
        self.worker_manager.transient_worker_busy.connect(self.on_transient_worker_busy)
        # The hardware encoder decides which encoding modes/passes are available, so it
        # has its own handler that also refreshes those options.
        self.hardware_encoding_combo.currentIndexChanged.connect(self.on_hardware_encoding_changed)
        param_widgets_on_change = [
            self.resolution_combo, self.fps_combo, self.pass_combo,
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
            self.watermark_text_input,
        ]
        # Spinboxes use valueChanged (debounced) rather than editingFinished so the
        # thumbnail refresh also fires when the value is changed via the arrow buttons
        # while the widget keeps focus (editingFinished would not fire then).
        cosmetic_spinboxes = [self.watermark_opacity_spin, self.watermark_fontsize_spin]
        cosmetic_widgets_on_toggle = [
            self.add_watermark_checkbox, self.watermark_tile_checkbox,
            self.delete_temp_checkbox, self.append_duration_checkbox,
            self.export_youtube_chapters_checkbox,
        ]

        for widget in cosmetic_widgets_on_change:
            widget.currentIndexChanged.connect(self.on_cosmetic_parameter_changed)
        for widget in cosmetic_widgets_on_finish_editing:
            widget.editingFinished.connect(self.on_cosmetic_parameter_changed)
        for widget in cosmetic_spinboxes:
            widget.valueChanged.connect(self._request_delayed_cosmetic_update)
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
            self.tr("Text Files (*.txt);;All Files (*)")
        )

        if file_path_str:
            try:
                system_info = collect_system_info(self.__version__, self.available_encoders_map)
                full_content = f"{system_info}\n\n[Log Start]\n{log_content}"
                with open(file_path_str, 'w', encoding='utf-8') as f:
                    f.write(full_content)
                QMessageBox.information(self, self.tr("Success"), self.tr("Debug log successfully exported to:\n{0}").format(file_path_str))
            except IOError as e:
                QMessageBox.critical(self, self.tr("Export Failed"), self.tr("An error occurred while writing the file:\n{0}").format(e))

    def on_codec_changed(self, new_codec: str):
        if self._is_syncing or not new_codec or new_codec == "No Encoders Found":
            return

        self.project_model.parameters.codec = new_codec

        self.update_hardware_encoding_options(new_codec)

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

        self._refresh_encoding_options_preserving_value()

    def on_hardware_encoding_changed(self):
        if self._is_syncing:
            return
        self.project_model.parameters.hardware_encoding = self.hardware_encoding_combo.currentData()
        self._refresh_encoding_options_preserving_value()

    def _refresh_encoding_options_preserving_value(self):
        # update_encoding_options() resets the value spin to a mode default. Changing
        # only the codec/encoder should not discard the user's quality/bitrate, so
        # preserve it across the refresh (clamped to the new range). If the refresh
        # forces a different mode (e.g. VideoToolbox → VBR), keep that mode's default.
        preserved_value = self.value_spin.value()
        preserved_mode = self.encoding_mode_combo.currentData()
        self.update_encoding_options()
        if self.encoding_mode_combo.currentData() == preserved_mode:
            clamped = max(self.value_spin.minimum(), min(self.value_spin.maximum(), preserved_value))
            self.value_spin.setValue(clamped)
        self._sync_ui_to_model()
        self.parameters_changed_event()

    def on_encoding_mode_changed(self):
        if self._is_syncing:
            return
        self.update_encoding_options()
        self.parameters_changed_event()

    def _request_delayed_parameter_update(self):
        if self._is_syncing:
            return
        self.parameter_update_timer.start(config.DURATION_RECALC_DELAY_MS)

    def _request_delayed_cosmetic_update(self):
        if self._is_syncing:
            return
        self.cosmetic_update_timer.start(config.PINP_PREVIEW_UPDATE_DELAY_MS)

    def on_parameter_changed(self):
        if self._is_syncing:
            return
        self._sync_ui_to_model()
        self.parameters_changed_event()

    def on_cosmetic_parameter_changed(self):
        if self._is_syncing:
            return
        self._sync_ui_to_model()
        self._mark_dirty()
        # Reflect watermark changes in the slide-list thumbnails immediately.
        self.slide_table_manager.refresh_all_thumbnails()

    def _on_preview_toggled(self, is_checked: bool):
        self.slide_table_manager.toggle_previews(is_checked)
        
    def _selected_encoder_name(self) -> str:
        selected_codec = self.codec_combo.currentText()
        hw_encoder_name = self.hardware_encoding_combo.currentData()
        if hw_encoder_name:
            return config.CODEC_MAP.get(selected_codec, {}).get(hw_encoder_name) or ""
        return config.SOFTWARE_CODEC_MAP.get(selected_codec, "")

    def update_encoding_options(self, _=None):
        mode = self.encoding_mode_combo.currentData()
        selected_codec = self.codec_combo.currentText()
        encoder_name = self._selected_encoder_name()
        is_videotoolbox = 'videotoolbox' in encoder_name
        # Only offer 2-Pass for encoders that actually implement it.
        supports_two_pass = encoder_name in config.TWO_PASS_ENCODERS or encoder_name in config.MULTIPASS_ENCODERS

        self.pass_combo.setEnabled(supports_two_pass)
        if not supports_two_pass:
            self.pass_combo.setCurrentIndex(self.pass_combo.findData("ONE_PASS"))

        if mode == "QUALITY":
            self.value_spin.setSuffix("")
            self.value_spin.setSingleStep(1)
            if selected_codec == 'MPEG-4 Part 2':
                # mpeg4 uses qscale (1-31), not CRF (0-51).
                low, high = config.ENCODING_MPEG4_QSCALE_RANGE
                self.value_label.setText(self.tr("Quality ({0}-{1}):").format(low, high))
                self.value_spin.setRange(low, high)
                self.value_spin.setValue(max(low, min(high, config.DEFAULT_CRF_VALUE)))
            else:
                self.value_label.setText(self.tr("Quality (0-51):"))
                self.value_spin.setRange(*config.ENCODING_CRF_RANGE)
                self.value_spin.setValue(config.DEFAULT_CRF_VALUE)
            if is_videotoolbox:
                self.encoding_mode_combo.blockSignals(True)
                try:
                    self.encoding_mode_combo.setCurrentIndex(self.encoding_mode_combo.findData("VBR"))
                finally:
                    self.encoding_mode_combo.blockSignals(False)
                # Re-run to reconfigure the spinbox for the VBR mode.
                self.update_encoding_options()
                return
            # Quality mode is always single pass.
            self.pass_combo.setEnabled(False)

        elif mode == "VBR":
            self.value_label.setText(self.tr("Avg Bitrate (kbps):"))
            self.value_spin.setSuffix(self.tr(" kbps"))
            self.value_spin.setRange(*config.ENCODING_BITRATE_RANGE_KBPS)
            self.value_spin.setSingleStep(100)
            self.value_spin.setValue(config.DEFAULT_VBR_BITRATE)

        elif mode == "CBR":
            self.value_label.setText(self.tr("Bitrate (kbps):"))
            self.value_spin.setSuffix(self.tr(" kbps"))
            self.value_spin.setRange(*config.ENCODING_BITRATE_RANGE_KBPS)
            self.value_spin.setSingleStep(100)
            self.value_spin.setValue(config.DEFAULT_CBR_BITRATE)

    def prompt_install_ffmpeg(self):
        reply = QMessageBox.question(self, self.tr('Install FFmpeg'), self.tr("This application requires FFmpeg to create videos.\n\nThis tool will attempt to install it using a system package manager (winget for Windows, Homebrew for macOS).\n\nNote:\n• The process may take several minutes.\n• Administrator privileges (password) may be required.\n\nDo you want to proceed with the installation?"), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.run_ffmpeg_installation()

    def run_ffmpeg_installation(self):
        # Release the previous dialog instead of leaking it (it was parented to the
        # window, so closing alone never freed it).
        if self.progress_dialog is not None:
            self.progress_dialog.deleteLater()
        self.progress_dialog = InstallProgressDialog(self)
        self.progress_dialog.show()
        self.installer_thread = FFmpegInstaller()
        self.installer_thread.log_message.connect(self.update_install_progress)
        self.installer_thread.install_finished.connect(self.on_ffmpeg_install_finished)
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
        
    def _start_project_load(self, path: Path):
        # Single entry point for all project loads (folder, .toml, .dmj). Confirm
        # first so unsaved edits to the current project aren't silently discarded.
        if not self._confirm_discard_changes():
            return
        # A transient task (e.g. the background encoder test) would swallow the setup
        # request and strand the state machine in LOADING_PROJECT. Bail out before
        # touching any state so the user can simply retry once it finishes.
        if self.worker_manager.is_transient_busy():
            self.on_transient_worker_busy(
                self.tr("A background task is still running. Please wait a moment and try again."))
            return
        # A queued parameter/cosmetic-update timer could fire after the load completes
        # and mark the freshly loaded (clean) project dirty; cancel them now.
        self.parameter_update_timer.stop()
        self.cosmetic_update_timer.stop()
        # The path is remembered so it can be added to the recent list on success.
        self._pending_recent_path = path
        self.validator.clear_cache()
        self.state_machine.transition_to(AppState.LOADING_PROJECT)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.worker_manager.start_project_setup(self.settings_manager, self.validator, path)

    def load_settings(self):
        file_to_load = self.settings_manager.prompt_for_load_path()
        if file_to_load:
            self._start_project_load(file_to_load)

    def import_dougameijin_project(self):
        file_to_load = self.settings_manager.prompt_for_dmj_path()
        if not file_to_load:
            return
        # Let the user pick where to extract, so nothing is left next to the .dmj.
        extract_dir = self.settings_manager.prompt_for_dmj_extract_dir(file_to_load)
        if not extract_dir:
            return
        self.settings_manager.pending_dmj_extract_dir = extract_dir
        self._start_project_load(file_to_load)

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
        # A freshly loaded project has not been validated yet; drop any previous
        # report and fall back to the encoder-matrix preview so the tab still shows
        # useful encoder information before 'Check Files' is run.
        self.last_validation_messages = None
        self._show_encoder_status_preview()

        # Populate the UI with the loaded data.
        self.slide_table_manager.populate_slide_table_from_model()
        self.slide_table_manager.toggle_previews(self.preview_pinp_checkbox.isChecked())

        if not self.filename_input.text() and self.project_model.project_folder:
            self.filename_input.setText(self.project_model.project_folder.name)

        # Record the successfully loaded project in the recent list. A DougaMeijin
        # import is recorded as the extracted project folder: re-opening the .dmj
        # would extract another copy and drop the edits made since the import.
        if self._pending_recent_path:
            recent_path = self._pending_recent_path
            if recent_path.suffix.lower() == '.dmj' and self.project_model.project_folder:
                recent_path = self.project_model.project_folder
            app_settings.add_recent_project(str(recent_path))
            self._pending_recent_path = None
            self._rebuild_recent_menu()

        # A freshly loaded project matches its saved state; start clean.
        self._clear_dirty()

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
        if self.settings_manager.save_project_settings(self.project_model):
            self._clear_dirty()

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

    def _rescan_available_materials(self):
        if self.project_model and self.project_model.project_folder:
            try:
                self.project_model.available_materials = project_init.scan_materials(self.project_model.project_folder)
            except Exception as e:
                self.write_debug(f"[ERROR] Error while rescanning material files: {e}")

    @staticmethod
    def _release_version(version_str: str):
        # 'git describe' yields e.g. '1.2.0-3-gabc123' for untagged commits; compare
        # on the release part only. Returns None when it is not a version at all.
        base = re.split(r'-\d+-g[0-9a-f]+$', version_str.strip())[0]
        try:
            return parse_version(base)
        except InvalidVersion:
            return None

    def check_for_updates(self):
        if self.__version__ == 'local-dev' or self._release_version(self.__version__) is None:
            self.write_debug(f"[INFO] Skipping update check for development version '{self.__version__}'.", 'app')
            QMessageBox.information(self, self.tr("Update Check Skipped"), self.tr("Running in local-dev mode. Update check skipped."))
            return
        if getattr(self, '_update_worker', None) is not None and self._update_worker.isRunning():
            return

        self.write_debug("[INFO] --- Manually checking for application updates ---", 'app')
        repo_path = config.REPO_URL.replace("https://github.com/", "")
        api_url = f"https://api.github.com/repos/{repo_path}/releases/latest"

        # The request runs on a worker thread so a slow network cannot freeze the UI.
        self.check_for_updates_action.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._update_worker = UpdateCheckWorker(api_url, self)
        self._update_worker.result_ready.connect(self._on_update_check_finished)
        self._update_worker.finished.connect(self._update_worker.deleteLater)
        self._update_worker.start()

    def _on_update_check_finished(self, latest_version_tag: str, release_url: str, error: str):
        QApplication.restoreOverrideCursor()
        self.check_for_updates_action.setEnabled(True)
        self._update_worker = None

        latest = self._release_version(latest_version_tag) if not error else None
        if error or latest is None:
            reason = error or f"unrecognized version '{latest_version_tag}'"
            self.write_debug(f"[WARNING] Could not check for updates. This may be due to being offline or a network issue. Error: {reason}", 'app')
            QMessageBox.warning(self, self.tr("Update Check Failed"), self.tr("Could not check for updates. This may be due to being offline or a network issue.\n\nError: {0}").format(reason))
            return

        self.write_debug(f"[INFO] Current version: {self.__version__}, Latest version on GitHub: {latest_version_tag}", 'app')
        if latest > self._release_version(self.__version__):
            self.write_debug(f"[INFO] New version {latest_version_tag} found!", 'app')
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
            QMessageBox.information(self, self.tr("No Updates Found"), self.tr("You are currently running the latest version ({0}).").format(self.__version__))

    def run_validation(self):
        if not self._check_and_handle_pdf_changes():
            return

        self._rescan_available_materials()
        self._sync_ui_to_model()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        
        self.state_machine.transition_to(AppState.VALIDATING)
        
        self.worker_manager.start_validation(self.validator, self.project_model, self.available_encoders_map)

    def _show_encoder_status_preview(self):
        """Render the available-encoder matrix in the 'Validation Result' tab
        before any validation has run.

        Based on the startup encoder check, this lets the user see which
        software/hardware encoders are functional without first running
        'Check Files'. An actual validation report always takes precedence, so
        the preview is skipped whenever one is already displayed.
        """
        if self.last_validation_messages is not None:
            return

        messages = self.validator.build_encoder_status_messages(self.available_encoders_map)
        if not messages.encoder_info:
            # No FFmpeg / encoder information is available yet (e.g. FFmpeg was not
            # found). Leave the tab empty rather than showing an empty report shell.
            self.validation_results_text.setHtml("")
            return

        self.validation_results_text.setHtml(
            messages.assemble_html(theme=self.current_theme, include_verdict=False)
        )

    def on_validation_finished(self, messages: ValidationMessages, page_count: int, snapshot: dict):
        if self.state_machine.state != AppState.VALIDATING:
            self.write_debug(f"[INFO] Validation result was ignored because the state was '{self.state_machine.state.name}', not 'VALIDATING'.")
            self.on_worker_thread_finished()
            # A cancel that landed after validation had already completed leaves the
            # app stuck in CANCELLING (every control, including Cancel, is disabled).
            # Recover to a usable state instead of freezing.
            if self.state_machine.state == AppState.CANCELLING:
                self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
            return

        self.on_worker_thread_finished()

        self.last_validation_messages = messages
        self.page_count = page_count
        self.validation_results_text.setHtml(messages.assemble_html(theme=self.current_theme))

        validation_had_errors = messages.has_errors()
        if not validation_had_errors:
            self.state_machine.transition_to(AppState.VALIDATED)

            self.validation_snapshot = snapshot
            self.has_validated_once = True

            self.tabs.setCurrentWidget(self.slide_settings_tab)
        else:
            self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
            QMessageBox.critical(self, self.tr("Validation Failed"), self.tr("Errors were found that prevent video creation.\nSee 'Validation Result' tab for details."))
            self.tabs.setCurrentWidget(self.validation_results_tab)
        
        self.slide_table_manager.populate_slide_table_from_model()
        self.slide_table_manager.toggle_previews(self.preview_pinp_checkbox.isChecked())

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
            # The rendered (frame-quantized) length, rounded before splitting into
            # minutes and seconds so it can never read e.g. "4m60s".
            _starts, total_duration = slide_timeline(project_model.slides, project_model.parameters.fps)
            duration_str = format_duration_suffix(total_duration)
            if duration_str != "0s":
                sanitized_filename += f"_{duration_str}"
        output_dir = project_model.output_folder if project_model.output_folder else Path(".")
        return output_dir / f"{sanitized_filename}.mp4"

    def _flush_pending_edits(self):
        # Apply edits still waiting in a debounce timer, so that an edit made just
        # before clicking Create/Preview invalidates the validation as it should.
        if self.parameter_update_timer.isActive():
            self.parameter_update_timer.stop()
            self.on_parameter_changed()
        if self.cosmetic_update_timer.isActive():
            self.cosmetic_update_timer.stop()
            self.on_cosmetic_parameter_changed()

    def run_create_video(self):
        self._flush_pending_edits()
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
        self._flush_pending_edits()
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
                pdf_path = project_init.find_single_pdf(self.project_model.project_folder)
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
             self.write_debug("[INFO] Process was canceled.", 'app')

    def _show_processing_result(self, success: bool, message: str, process_name: str):
        if success:
            elapsed_time = time.time() - self.video_creation_start_time
            formatted_time = self._format_elapsed_time(elapsed_time)
            QMessageBox.information(self, self.tr('Success'), self.tr('{0} created successfully at {1}\nTime taken: {2}').format(process_name, message, formatted_time))
        elif message != "Canceled by user.":
            # Use the translated noun as-is; lowercasing it breaks non-English text
            # and only ever produced ASCII-cased English.
            QMessageBox.critical(self, self.tr('Error'), self.tr('An error occurred during {0} creation:\n{1}').format(process_name, message))
        
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
        self.total_duration_label.setText(self.tr("Total Estimated Duration: 0s"))
        self.has_validated_once = False
        self.last_validation_messages = None
        self.validation_snapshot = {}
        # No project is loaded and nothing has been validated, so restore the
        # encoder-matrix preview instead of leaving the tab blank.
        self._show_encoder_status_preview()
        self._clear_dirty()

    def select_project_folder(self, force_folder: Path = None):
        folder_path = None
        if force_folder:
            folder_path = force_folder
        else:
            # Always start from the Desktop; the previous location is intentionally
            # not remembered for project selection.
            default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
            folder_str = QFileDialog.getExistingDirectory(self, self.tr("Select Project Folder"), dir=default_dir)
            if folder_str:
                folder_path = Path(folder_str)

        if not folder_path:
            return

        self.write_debug(f"[INFO] Project folder selected: {folder_path}")
        self._start_project_load(folder_path)

    def select_output_folder(self):
        default_dir = app_settings.get_last_dir("output", "")
        folder_str = QFileDialog.getExistingDirectory(self, self.tr("Select Output Folder"), dir=default_dir)
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

        app_settings.set_last_dir("output", folder_str)
        self.write_debug(f"[INFO] Output folder selected: {selected_folder}")
        self.project_model.output_folder = selected_folder
        self.ui_manager.update_folder_label(self.output_folder_label, self.project_model.output_folder)
        self._mark_dirty()
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
            
            agif_folder = resolve_resource_path("resources/agif")
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
            if self._dirty:
                reply = QMessageBox.question(
                    self, self.tr('Confirm Exit'),
                    self.tr("You have unsaved changes. Are you sure you want to exit?"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.No:
                    event.ignore()
                    return

        # The user has committed to closing; persist the window geometry.
        app_settings.set_window_geometry(self.saveGeometry())

        # Stop and join every background thread before the window is destroyed.
        self._join_child_threads()
        self.worker_manager.cancel_all_tasks()
        self.worker_manager.shutdown_transient_worker()

        if self.installer_thread and self.installer_thread.isRunning():
            # run() blocks in a package-manager subprocess, so quit() cannot stop it.
            # Fall back to terminate() so the QThread is not destroyed while running
            # (which would abort the process with a fatal Qt error on exit).
            self.installer_thread.quit()
            if not self.installer_thread.wait(5000):
                self.installer_thread.terminate()
                self.installer_thread.wait()

        self.worker_manager.shutdown_persistent_workers()

        event.accept()

    def _join_child_threads(self):
        # QThreads parented to this window (the update check, the media player's
        # waveform loader) are destroyed with it; destroying a running QThread aborts
        # the process, so wait for them first. Both are bounded by their own network
        # or subprocess timeouts; terminate only as a last resort on exit.
        update_worker = getattr(self, '_update_worker', None)
        if update_worker is not None:
            try:
                update_worker.result_ready.disconnect()
            except (TypeError, RuntimeError):
                pass
        for thread in self.findChildren(QThread):
            if thread.isRunning() and not thread.wait(config.CHILD_THREAD_JOIN_TIMEOUT_MS):
                thread.terminate()
                thread.wait()

    def parameters_changed_event(self, _=None):
        self._mark_dirty()
        if self.state_machine.state in [AppState.VALIDATED, AppState.READY_TO_VALIDATE, AppState.PREPARE_TO_VALIDATE]:
            if self.project_model.output_folder:
                 self.state_machine.transition_to(AppState.READY_TO_VALIDATE)
            else:
                 self.state_machine.transition_to(AppState.PREPARE_TO_VALIDATE)

    def _mark_dirty(self):
        self._dirty = True

    def _clear_dirty(self):
        self._dirty = False

    def _confirm_discard_changes(self) -> bool:
        # Returns True when it is safe to proceed (no unsaved edits, or the user
        # accepts losing them). Used before loading another project or exiting.
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self, self.tr('Unsaved Changes'),
            self.tr("You have unsaved changes that will be lost.\n\nDo you want to continue?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return reply == QMessageBox.Yes

    def write_debug(self, text, source='app'):
        color_map = {
            '[FATAL]': QColor("red"),
            '[ERROR]': QColor("red"),
            '[WARNING]': QColor("orange"),
            '[SUCCESS]': QColor("green"),
            '[DEBUG]': QColor("blue"),
            '[STATE_TRANSITION]': QColor("magenta"),
        }
        default_color = self.debug_text.palette().color(QPalette.ColorRole.Text)
        ffmpeg_color = QColor("grey")

        is_verbose_log = text.strip().startswith(('[DEBUG]', '[STATE_TRANSITION]'))
        if (source == 'ffmpeg' or is_verbose_log) and not self.verbose_debug_checkbox.isChecked():
            return

        self.debug_text.moveCursor(QTextCursor.End)
        cursor = self.debug_text.textCursor()
        char_format = QTextCharFormat()
        
        final_text = text
        log_color = default_color

        if source == 'ffmpeg':
            log_color = ffmpeg_color
        else:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            final_text = f"[{timestamp}] {text}"
            
            stripped_text = text.strip()
            for prefix, color in color_map.items():
                if stripped_text.startswith(prefix):
                    log_color = color
                    break
        
        char_format.setForeground(log_color)
        cursor.setCharFormat(char_format)
        cursor.insertText(final_text.rstrip() + '\n')

        default_format = QTextCharFormat()
        cursor.setCharFormat(default_format)

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
        # Same helper the validator used to build validation_snapshot, so both sides
        # treat unreadable files identically.
        snapshot, unreadable = self.validator.snapshot_project_files(folder)
        for name in unreadable:
            self.write_debug(f"[WARNING] Could not calculate hash for file {name}.")
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

    def ffmpeg_missing_message(self) -> str:
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
        # Returns False to signal "do not proceed"; it must not itself destroy the
        # loaded project. The load-path caller (on_state_changed) clears the project
        # on False, while the re-validation caller (run_validation) simply aborts,
        # so an accidental Cancel here no longer discards unsaved slide edits.
        if not self.project_model.project_folder or not self.project_model.project_folder.is_dir():
            return False
        try:
            pdf_path = project_init.find_single_pdf(self.project_model.project_folder)
        except (FileNotFoundError, ValueError) as e:
            QMessageBox.critical(self, self.tr("PDF Error"), str(e))
            return False

        current_pdf_hash = self.validator.get_file_hash(pdf_path)
        current_pdf_structure = self.validator.get_pdf_structure(pdf_path)
        
        # A matching hash means the PDF is byte-for-byte unchanged. Still require the
        # in-memory slide count to match the PDF's page count before skipping analysis:
        # a hand-edited or desynced TOML can carry a slide count that differs from the
        # PDF it points at, and skipping here would let that mismatch reach rendering
        # (KeyError on missing page images, or pages with no slide).
        if (self.validator.validated_pdf_hash
                and self.validator.validated_pdf_hash == current_pdf_hash
                and len(self.project_model.slides) == current_pdf_structure.get('page_count', 0)):
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
            msg_box.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
            msg_box.exec()

            if msg_box.clickedButton() == migrate_button:
                return self._migrate_slide_data(pdf_path)
            elif msg_box.clickedButton() == reinit_button:
                return self._initialize_new_project(pdf_path)
            else:
                return False

        elif self.validator.validated_pdf_hash and old_page_count > 0 and old_page_count == new_page_count:
             self.write_debug("[INFO] PDF content changed without altering page structure. Auto-updating thumbnails.")
             QApplication.setOverrideCursor(Qt.WaitCursor)
             try:
                 self.validator.compute_and_populate_pdf_details(self.project_model)
                 self.slide_table_manager.populate_slide_table_from_model()
             finally:
                 QApplication.restoreOverrideCursor()
             QMessageBox.information(self, self.tr("PDF Content Updated"),
                                     self.tr("The content of the PDF file was modified.\n"
                                     "Thumbnails have been automatically updated."))
             self.validator.validated_pdf_hash = current_pdf_hash
        else:
            if old_page_count == 0:
                return self._initialize_new_project(pdf_path)
            else:
                self.write_debug("[INFO] New project loaded. No previous PDF state to compare against.")

        return True

    def _initialize_new_project(self, pdf_path: Path) -> bool:
        # Returns success. On failure it does NOT clear the project itself; the caller
        # (via a False return from _check_and_handle_pdf_changes) decides whether to
        # clear, keeping the clear/transition policy in one place.
        self.write_debug("[INFO] Initializing new project from PDF.")
        try:
            # Read the page count before mutating the model, so a failure here leaves
            # the current slides intact rather than half-cleared.
            page_count = pdf_utils.page_count(pdf_path)
            self.project_model.slides = [Slide() for _ in range(page_count)]
            self._rescan_available_materials()

            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.validator.compute_and_populate_pdf_details(self.project_model)
                self.slide_table_manager.populate_slide_table_from_model()
            finally:
                QApplication.restoreOverrideCursor()
            return True
        except Exception as e:
            QMessageBox.critical(self, self.tr("Error Initializing Project"), self.tr("Could not process the PDF file: {0}").format(e))
            return False

    def _migrate_slide_data(self, pdf_path: Path) -> bool:
        self.write_debug("[INFO] Starting slide data migration process.")
        old_slides = copy.deepcopy(self.project_model.slides)
        
        new_pdf_details, error = self.validator.get_pdf_details(pdf_path)
        if error:
            QMessageBox.critical(self, self.tr("Migration Error"), self.tr("Could not get details from the new PDF: {0}").format(error))
            return False

        if not new_pdf_details:
            QMessageBox.critical(self, self.tr("Migration Error"), self.tr("Could not get details from the new PDF for an unknown reason."))
            return False

        if not self._start_pdf_migration(old_slides, new_pdf_details):
            # User cancelled the migration dialog; restore the slides they had and
            # let the caller decide whether to keep or clear the project.
            self.project_model.slides = old_slides
            self.slide_table_manager.populate_slide_table_from_model()
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