# ui_main.py
from PySide6.QtWidgets import (QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                               QTextEdit, QComboBox, QSpinBox, QCheckBox,
                               QSizePolicy, QFormLayout, QLineEdit, QProgressBar,
                               QTabWidget, QWidget, QTableWidget, QSpacerItem,
                               QPlainTextEdit, QAbstractItemView, QHeaderView, QFrame)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPainter, QPalette

from ssmm import config
from ssmm import app_settings

# Per-theme, opaque row-selection colors for the slide table. qdarktheme leaves
# QTableView::item:selected empty, so plain item cells fall back to the style's
# solid highlight while widget-hosting cells only pick up a faint tint. That
# mismatch makes part of a selected row look harshly emphasized. Using an opaque
# background (blended from qdarktheme's own selection color over the table
# background) replaces the solid highlight so every cell shows the same gentle
# tint.
_SLIDE_SELECTION_COLORS = {
    "light": {"active": "#c1d9f9", "inactive": "#e6f0fc", "text": "#4d5157"},
    "dark":  {"active": "#1f3454", "inactive": "#161e2d", "text": "#e4e7eb"},
}


# Theme-independent portion of the slide table's stylesheet. Kept as a module
# constant so the full stylesheet can be rebuilt when the theme changes at runtime.
SLIDE_TABLE_BASE_STYLE = (
    "QTableWidget::item { padding: 0px; }"
    "QTableWidget QComboBox, QTableWidget QSpinBox,"
    " QTableWidget QLineEdit, QTableWidget QPushButton { min-height: 24px; }"
)


def _slide_table_selection_style() -> str:
    colors = _SLIDE_SELECTION_COLORS.get(
        app_settings.get_effective_theme(), _SLIDE_SELECTION_COLORS["light"]
    )
    return (
        "QTableWidget::item:selected {"
        f"background:{colors['active']};color:{colors['text']};}}"
        "QTableWidget::item:selected:!active {"
        f"background:{colors['inactive']};color:{colors['text']};}}"
    )


def slide_table_stylesheet() -> str:
    return SLIDE_TABLE_BASE_STYLE + _slide_table_selection_style()

class ClickableLabel(QLabel):
    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


def wrap_cell_widget(widget: QWidget, h_margin: int = 6, v_margin: int = 4) -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(h_margin, v_margin, h_margin, v_margin)
    layout.setAlignment(Qt.AlignVCenter)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    layout.addWidget(widget)
    return container


class WordWrapHeaderView(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setDefaultAlignment(Qt.AlignCenter | Qt.TextWordWrap)

    def sectionSizeFromContents(self, logicalIndex: int) -> QSize:
        model = self.model()
        text = "" if model is None else str(model.headerData(logicalIndex, Qt.Horizontal, Qt.DisplayRole) or "")
        width = max(self.sectionSize(logicalIndex), self.minimumSectionSize())
        rect = self.fontMetrics().boundingRect(
            0, 0, width - 10, 1000, Qt.TextWordWrap | Qt.AlignCenter, text
        )
        return QSize(width, rect.height() + 12)


class SlideTableWidget(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalHeader(WordWrapHeaderView(self))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(QPalette.PlaceholderText))
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignCenter | Qt.TextWordWrap,
                self.tr("No project loaded.\nSelect a project folder to begin."),
            )
            painter.end()


def make_step_badge(number: int) -> QLabel:
    """A small rounded, numbered badge that replaces the old emoji step markers.
    Colors come from the app QSS via the 'stepBadge' object name."""
    badge = QLabel(str(number))
    badge.setObjectName("stepBadge")
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedSize(22, 22)
    return badge


def make_card():
    """A framed 'card' container (QFrame#card) with a vertical layout, used to
    group each workflow step in the left column. Returns (frame, layout)."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(13, 12, 13, 13)
    layout.setSpacing(8)
    return frame, layout


def step_header_row(badge: QLabel, title_label: QLabel, trailing: QWidget | None = None):
    """Header row for a card: [badge] [title] ...stretch... [optional trailing]."""
    row = QHBoxLayout()
    row.setSpacing(9)
    row.addWidget(badge)
    row.addWidget(title_label)
    row.addStretch()
    if trailing is not None:
        row.addWidget(trailing)
    return row


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        title_version = f"v{MainWindow.__version__}" if MainWindow.__version__ != 'local-dev' else MainWindow.__version__
        MainWindow.setWindowTitle(f"Simple Slideshow Movie Maker {title_version}")
        MainWindow.setMinimumSize(1200, 870)

        MainWindow.select_project_folder_button = QPushButton(MainWindow.tr("Browse..."))
        MainWindow.project_folder_label = ClickableLabel("")
        MainWindow.project_folder_label.setObjectName("projectFolderLabel")
        MainWindow.project_folder_label.setWordWrap(False)
        MainWindow.project_folder_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        MainWindow.validation_button = QPushButton(MainWindow.tr("Check Files"))
        MainWindow.validation_button.setObjectName("secondaryButton")
        MainWindow.validation_button.setEnabled(False)

        MainWindow.select_output_button = QPushButton(MainWindow.tr("Browse..."))
        MainWindow.output_folder_label = ClickableLabel("")
        MainWindow.output_folder_label.setObjectName("outputFolderLabel")
        MainWindow.output_folder_label.setWordWrap(False)
        MainWindow.output_folder_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        MainWindow.preview_button = QPushButton(MainWindow.tr("Create Preview"))
        MainWindow.preview_button.setObjectName("secondaryButton")
        MainWindow.preview_button.setEnabled(False)
        MainWindow.create_video_button = QPushButton(MainWindow.tr("Create Video"))
        MainWindow.create_video_button.setObjectName("primaryButton")
        MainWindow.create_video_button.setEnabled(False)

        MainWindow.cancel_button = QPushButton(MainWindow.tr("Cancel"))
        MainWindow.cancel_button.setObjectName("ghostButton")
        MainWindow.cancel_button.setEnabled(False)

        MainWindow.parameters_tabs = QTabWidget()
        MainWindow.parameters_tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # Basic Tab
        MainWindow.basic_tab = QWidget()
        MainWindow.basic_layout = QFormLayout()
        MainWindow.resolution_label = QLabel(MainWindow.tr("Resolution:"))
        MainWindow.resolution_combo = QComboBox()
        MainWindow.resolution_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        MainWindow.basic_layout.addRow(MainWindow.resolution_label, MainWindow.resolution_combo)
        
        MainWindow.codec_label = QLabel(MainWindow.tr("Codec:"))
        MainWindow.codec_combo = QComboBox()
        MainWindow.codec_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        MainWindow.basic_layout.addRow(MainWindow.codec_label, MainWindow.codec_combo)
        MainWindow.hardware_encoding_label = QLabel(MainWindow.tr("Hardware Encoding:"))
        
        MainWindow.hardware_encoding_combo = QComboBox()
        MainWindow.hardware_encoding_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        MainWindow.basic_layout.addRow(MainWindow.hardware_encoding_label, MainWindow.hardware_encoding_combo)
        
        MainWindow.basic_tab.setLayout(MainWindow.basic_layout)
        MainWindow.parameters_tabs.addTab(MainWindow.basic_tab, MainWindow.tr("Basic"))
        
        # Audio Options Tab
        MainWindow.audio_tab = QWidget()
        audio_layout = QFormLayout(MainWindow.audio_tab)
        MainWindow.audio_bitrate_label = QLabel(MainWindow.tr("Audio Bitrate (-b:a):"))
        MainWindow.audio_bitrate_combo = QComboBox()
        MainWindow.audio_bitrate_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        audio_layout.addRow(MainWindow.audio_bitrate_label, MainWindow.audio_bitrate_combo)
        MainWindow.audio_sample_rate_label = QLabel(MainWindow.tr("Audio Sample Rate (-ar):"))
        MainWindow.audio_sample_rate_combo = QComboBox()
        MainWindow.audio_sample_rate_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        audio_layout.addRow(MainWindow.audio_sample_rate_label, MainWindow.audio_sample_rate_combo)
        MainWindow.audio_channels_label = QLabel(MainWindow.tr("Audio Channels (-ac):"))
        MainWindow.audio_channels_combo = QComboBox()
        MainWindow.audio_channels_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        audio_layout.addRow(MainWindow.audio_channels_label, MainWindow.audio_channels_combo)
        
        MainWindow.normalize_loudness_checkbox = QCheckBox(MainWindow.tr("Normalize Loudness (EBU R128)"))
        MainWindow.normalize_loudness_checkbox.setToolTip(
            MainWindow.tr("Apply EBU R128 loudness normalization to the entire video for consistent perceived volume.\n"
            "This will re-encode the final audio track.")
        )
        audio_layout.addRow(MainWindow.normalize_loudness_checkbox)

        MainWindow.normalize_loudness_mode_label = QLabel(MainWindow.tr("Mode:"))
        MainWindow.normalize_loudness_mode_combo = QComboBox()
        # The item data is the parameter value (a config.LOUDNORM_MODES key).
        for mode_key, mode_label in config.LOUDNORM_MODES.items():
            MainWindow.normalize_loudness_mode_combo.addItem(mode_label, mode_key)
        
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(MainWindow.normalize_loudness_mode_combo)
        mode_layout.addStretch()
        
        audio_layout.addRow(MainWindow.normalize_loudness_mode_label, mode_layout)
        
        MainWindow.parameters_tabs.addTab(MainWindow.audio_tab, MainWindow.tr("Audio Options"))

        # Video Options Tab
        MainWindow.video_tab = QWidget()
        video_layout = QFormLayout(MainWindow.video_tab)

        # FPS settings
        MainWindow.fps_label = QLabel(MainWindow.tr("FPS:"))
        MainWindow.fps_combo = QComboBox()
        MainWindow.fps_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        video_layout.addRow(MainWindow.fps_label, MainWindow.fps_combo)

        MainWindow.encoding_mode_label = QLabel(MainWindow.tr("Encoding Mode:"))
        MainWindow.encoding_mode_combo = QComboBox()
        video_layout.addRow(MainWindow.encoding_mode_label, MainWindow.encoding_mode_combo)
        
        MainWindow.value_label = QLabel(MainWindow.tr("Quality (0-51):"))
        MainWindow.value_spin = QSpinBox()
        # Initial range/value matching the CRF label; reconfigured at runtime by the encoding-mode handler.
        MainWindow.value_spin.setRange(0, 51)
        MainWindow.value_spin.setValue(23)
        video_layout.addRow(MainWindow.value_label, MainWindow.value_spin)
        
        MainWindow.pass_label = QLabel(MainWindow.tr("Passes:"))
        MainWindow.pass_combo = QComboBox()
        video_layout.addRow(MainWindow.pass_label, MainWindow.pass_combo)
        
        # Separator before the preview checkbox
        video_layout.addItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Fixed))
        MainWindow.preview_pinp_checkbox = QCheckBox(MainWindow.tr("Preview PinP video on thumbnails"))
        MainWindow.preview_pinp_checkbox.setChecked(True)
        video_layout.addRow(MainWindow.preview_pinp_checkbox)

        MainWindow.export_youtube_chapters_checkbox = QCheckBox(MainWindow.tr("Export chapter file for YouTube"))
        MainWindow.export_youtube_chapters_checkbox.setToolTip(
            MainWindow.tr("If enabled, a YouTube-compatible chapter file (.txt) will be created alongside the video.")
        )
        video_layout.addRow(MainWindow.export_youtube_chapters_checkbox)
        
        MainWindow.parameters_tabs.addTab(MainWindow.video_tab, MainWindow.tr("Video Options"))


        # Watermark Tab
        MainWindow.watermark_tab = QWidget()
        MainWindow.watermark_layout = QFormLayout()
        MainWindow.add_watermark_checkbox = QCheckBox(MainWindow.tr("Add Watermark Text"))
        MainWindow.watermark_layout.addRow(MainWindow.add_watermark_checkbox)
        MainWindow.watermark_text_label = QLabel(MainWindow.tr("Text:"))
        MainWindow.watermark_text_input = QLineEdit()
        MainWindow.watermark_layout.addRow(MainWindow.watermark_text_label, MainWindow.watermark_text_input)
        MainWindow.watermark_opacity_label = QLabel(MainWindow.tr("Opacity (%):"))
        MainWindow.watermark_opacity_spin = QSpinBox()
        MainWindow.watermark_opacity_spin.setRange(0, 100)
        MainWindow.watermark_opacity_spin.setSuffix(MainWindow.tr("%"))
        MainWindow.watermark_layout.addRow(MainWindow.watermark_opacity_label, MainWindow.watermark_opacity_spin)
        MainWindow.watermark_color_label = QLabel(MainWindow.tr("Color:"))
        MainWindow.watermark_color_combo = QComboBox()
        MainWindow.watermark_color_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        MainWindow.watermark_layout.addRow(MainWindow.watermark_color_label, MainWindow.watermark_color_combo)
        MainWindow.watermark_fontsize_label = QLabel(MainWindow.tr("Font Size (% of video height):"))
        MainWindow.watermark_fontsize_spin = QSpinBox()
        MainWindow.watermark_fontsize_spin.setRange(1, 100)
        MainWindow.watermark_fontsize_spin.setSuffix(MainWindow.tr("%"))
        MainWindow.watermark_layout.addRow(MainWindow.watermark_fontsize_label, MainWindow.watermark_fontsize_spin)
        MainWindow.watermark_fontfamily_label = QLabel(MainWindow.tr("Font Family:"))
        MainWindow.watermark_fontfamily_combo = QComboBox()
        MainWindow.watermark_fontfamily_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        MainWindow.watermark_layout.addRow(MainWindow.watermark_fontfamily_label, MainWindow.watermark_fontfamily_combo)
        
        MainWindow.watermark_rotation_label = QLabel(MainWindow.tr("Rotation:"))
        MainWindow.watermark_rotation_combo = QComboBox()
        MainWindow.watermark_rotation_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        MainWindow.watermark_layout.addRow(MainWindow.watermark_rotation_label, MainWindow.watermark_rotation_combo)
        
        MainWindow.watermark_tile_checkbox = QCheckBox(MainWindow.tr("Tile watermark across screen"))
        MainWindow.watermark_layout.addRow(MainWindow.watermark_tile_checkbox)

        MainWindow.watermark_tab.setLayout(MainWindow.watermark_layout)
        MainWindow.parameters_tabs.addTab(MainWindow.watermark_tab, MainWindow.tr("Watermark"))

        # Main Controls
        MainWindow.progress_bar = QProgressBar()
        MainWindow.progress_bar.setVisible(True)
        # Numbered badges replace the old emoji step markers (1️⃣…); the badge
        # carries the step number so the label holds only the title.
        MainWindow.step1_badge = make_step_badge(1)
        MainWindow.step1_label = QLabel(MainWindow.tr("Select Project Folder"))
        MainWindow.step1_label.setProperty('class', 'stepLabel')
        MainWindow.step2_badge = make_step_badge(2)
        MainWindow.step2_label = QLabel(MainWindow.tr("Select Output Folder"))
        MainWindow.step2_label.setProperty('class', 'stepLabel')
        MainWindow.step3_badge = make_step_badge(3)
        MainWindow.step3_label = QLabel(MainWindow.tr("Set Parameters"))
        MainWindow.step3_label.setProperty('class', 'stepLabel')
        MainWindow.reset_parameters_button = QPushButton(MainWindow.tr("Reset Defaults"))
        MainWindow.reset_parameters_button.setObjectName("resetDefaultsButton")
        MainWindow.reset_parameters_button.setToolTip(MainWindow.tr("Reset all parameters below to their default values."))
        MainWindow.reset_parameters_button.setMaximumWidth(120)
        MainWindow.step4_badge = make_step_badge(4)
        MainWindow.step4_label = QLabel(MainWindow.tr("Validation"))
        MainWindow.step4_label.setProperty('class', 'stepLabel')
        MainWindow.step5_badge = make_step_badge(5)
        MainWindow.step5_label = QLabel(MainWindow.tr("Create Video"))
        MainWindow.step5_label.setProperty('class', 'stepLabel')
        MainWindow.delete_temp_checkbox = QCheckBox(MainWindow.tr("Delete Temporary Files"))
        MainWindow.delete_temp_checkbox.setChecked(True)
        MainWindow.append_duration_checkbox = QCheckBox(MainWindow.tr("Append Duration to Filename"))
        MainWindow.append_duration_checkbox.setChecked(False)
        MainWindow.filename_input = QLineEdit()
        MainWindow.filename_input.setPlaceholderText(MainWindow.tr("Enter filename"))
        MainWindow.filename_input.setEnabled(False)

        # == Right-side Tabs ==
        MainWindow.tabs = QTabWidget()
        MainWindow.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # -- Tab 1: Slide Settings --
        MainWindow.slide_settings_tab = QWidget()
        MainWindow.slide_table = SlideTableWidget()
        MainWindow.slide_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        MainWindow.slide_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Use alternating row bands instead of grid lines to distinguish rows.
        MainWindow.slide_table.setShowGrid(False)
        MainWindow.slide_table.setAlternatingRowColors(True)
        MainWindow.slide_table.setStyleSheet(slide_table_stylesheet())
        
        slide_settings_layout = QVBoxLayout()
        MainWindow.total_duration_label = QLabel(MainWindow.tr("Total Estimated Duration: 0s"))
        # Emphasize the running total using the font API so it survives theme toggles.
        _dur_font = MainWindow.total_duration_label.font()
        _dur_font.setBold(True)
        _dur_font.setPointSize(_dur_font.pointSize() + 2)
        MainWindow.total_duration_label.setFont(_dur_font)

        edit_selection_layout = QHBoxLayout()

        MainWindow.select_all_button = QPushButton(MainWindow.tr("All"))
        MainWindow.select_all_button.setToolTip(MainWindow.tr("Select all slides"))

        MainWindow.select_video_button = QPushButton(MainWindow.tr("Movie"))
        MainWindow.select_video_button.setToolTip(MainWindow.tr("Select slides with video material"))

        MainWindow.select_audio_button = QPushButton(MainWindow.tr("Audio"))
        MainWindow.select_audio_button.setToolTip(MainWindow.tr("Select slides with audio-only material"))

        # Framed group holding the "Select:" caption and selection shortcut buttons.
        select_group = QFrame()
        select_group.setFrameShape(QFrame.StyledPanel)
        select_group_layout = QHBoxLayout(select_group)
        select_group_layout.setContentsMargins(8, 3, 8, 3)
        select_group_layout.setSpacing(4)
        select_group_layout.addWidget(QLabel(MainWindow.tr("Select:")))
        select_group_layout.addWidget(MainWindow.select_all_button)
        select_group_layout.addWidget(MainWindow.select_video_button)
        select_group_layout.addWidget(MainWindow.select_audio_button)
        edit_selection_layout.addWidget(select_group)

        edit_selection_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Fixed, QSizePolicy.Minimum))

        MainWindow.edit_selection_button = QPushButton(MainWindow.tr("Edit Selected Slides..."))
        MainWindow.edit_selection_button.setEnabled(False)
        MainWindow.edit_selection_button.setToolTip(MainWindow.tr("Select one or more slides to enable."))
        edit_selection_layout.addWidget(MainWindow.edit_selection_button)
        edit_selection_layout.addStretch()

        slide_settings_layout.addWidget(MainWindow.total_duration_label)
        slide_settings_layout.addLayout(edit_selection_layout)
        slide_settings_layout.addWidget(MainWindow.slide_table)
        MainWindow.slide_settings_tab.setLayout(slide_settings_layout)
        MainWindow.tabs.addTab(MainWindow.slide_settings_tab, MainWindow.tr("Slide Settings"))

        # -- Tab 2: Validation --
        MainWindow.validation_results_tab = QWidget()
        validation_layout_v = QVBoxLayout()
        MainWindow.validation_warning_label = QLabel(MainWindow.tr("Warning: Project settings have been modified. This report may be outdated. Please run 'Check Files' again to see the latest results."))
        MainWindow.validation_warning_label.setObjectName("validationWarningLabel")
        MainWindow.validation_warning_label.setWordWrap(True)
        MainWindow.validation_warning_label.setVisible(False)
        validation_layout_v.addWidget(MainWindow.validation_warning_label)
        MainWindow.validation_results_text = QTextEdit()
        MainWindow.validation_results_text.setReadOnly(True)
        validation_layout_v.addWidget(MainWindow.validation_results_text)
        MainWindow.validation_results_tab.setLayout(validation_layout_v)
        MainWindow.tabs.addTab(MainWindow.validation_results_tab, MainWindow.tr("Validation Result"))

        # -- Tab 3: Debug --
        MainWindow.debug_tab = QWidget()
        MainWindow.debug_text = QPlainTextEdit()
        MainWindow.debug_text.setReadOnly(True)
        MainWindow.debug_text.setMaximumBlockCount(config.DEBUG_LOG_MAX_LINES)
        MainWindow.debug_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        debug_button_layout = QHBoxLayout()
        MainWindow.clear_debug_button = QPushButton(MainWindow.tr("Clear"))
        MainWindow.export_debug_button = QPushButton(MainWindow.tr("Export to File..."))
        MainWindow.verbose_debug_checkbox = QCheckBox(MainWindow.tr("Verbose Logging"))
        debug_button_layout.addWidget(MainWindow.clear_debug_button)
        debug_button_layout.addWidget(MainWindow.export_debug_button)
        debug_button_layout.addWidget(MainWindow.verbose_debug_checkbox)
        debug_button_layout.addStretch()

        debug_layout = QVBoxLayout()
        debug_layout.addLayout(debug_button_layout)
        debug_layout.addWidget(MainWindow.debug_text)
        MainWindow.debug_tab.setLayout(debug_layout)
        MainWindow.tabs.addTab(MainWindow.debug_tab, MainWindow.tr("Debug"))

        # -- Tab 4: Licenses --
        MainWindow.licenses_tab = QWidget()
        licenses_layout = QVBoxLayout()
        MainWindow.licenses_tab.setLayout(licenses_layout)
        app_license_header = QLabel(MainWindow.tr("<b>Application License</b>"))
        app_license_label = QLabel()
        app_license_label.setTextFormat(Qt.RichText)
        app_license_label.setOpenExternalLinks(True)
        app_license_label.setText(
            MainWindow.tr("Author: Y. Yamazaki<br>"
            "License: <a href='https://opensource.org/licenses/MIT'>MIT License</a>")
        )
        app_license_label.setWordWrap(True)
        licenses_layout.addWidget(app_license_header)
        licenses_layout.addWidget(app_license_label)

        licenses_layout.addSpacerItem(QSpacerItem(20, 15, QSizePolicy.Minimum, QSizePolicy.Fixed))
        MainWindow.disclaimer_header = QLabel(MainWindow.tr("<b>Disclaimer</b>"))
        licenses_layout.addWidget(MainWindow.disclaimer_header)
        MainWindow.disclaimer_text_edit = QTextEdit()
        MainWindow.disclaimer_text_edit.setReadOnly(True)
        licenses_layout.addWidget(MainWindow.disclaimer_text_edit)

        font_license_header = QLabel(MainWindow.tr("<b>Font License (Noto Fonts)</b>"))
        MainWindow.font_license_text_edit = QTextEdit()
        MainWindow.font_license_text_edit.setReadOnly(True)
        licenses_layout.addWidget(font_license_header)
        licenses_layout.addWidget(MainWindow.font_license_text_edit)

        thirdparty_license_header = QLabel(MainWindow.tr("<b>Third-Party Library Licenses</b>"))
        MainWindow.thirdparty_license_text_edit = QTextEdit()
        MainWindow.thirdparty_license_text_edit.setReadOnly(True)
        licenses_layout.addWidget(thirdparty_license_header)
        licenses_layout.addWidget(MainWindow.thirdparty_license_text_edit)

        licenses_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Fixed))

        # FFmpeg is not distributed with SSMM; a system installation is used so the
        # hardware encoders of the user's GPU are available.
        MainWindow.ffmpeg_notice_label = QLabel()
        MainWindow.ffmpeg_notice_label.setTextFormat(Qt.RichText)
        MainWindow.ffmpeg_notice_label.setOpenExternalLinks(True)
        MainWindow.ffmpeg_notice_label.setWordWrap(True)
        licenses_layout.addWidget(MainWindow.ffmpeg_notice_label)

        MainWindow.tabs.addTab(MainWindow.licenses_tab, MainWindow.tr("Licenses"))


        # Layouts
        # -- Left Pane: each numbered step lives in its own framed card. --
        left_layout = QVBoxLayout()
        left_layout.setSpacing(10)

        # Step 1: project folder
        card1, card1_layout = make_card()
        card1_layout.addLayout(step_header_row(
            MainWindow.step1_badge, MainWindow.step1_label, MainWindow.select_project_folder_button))
        card1_layout.addWidget(MainWindow.project_folder_label)
        left_layout.addWidget(card1)

        # Step 2: output folder
        card2, card2_layout = make_card()
        card2_layout.addLayout(step_header_row(
            MainWindow.step2_badge, MainWindow.step2_label, MainWindow.select_output_button))
        card2_layout.addWidget(MainWindow.output_folder_label)
        left_layout.addWidget(card2)

        # Step 3: parameters (grows to fill the column)
        card3, card3_layout = make_card()
        card3_layout.addLayout(step_header_row(
            MainWindow.step3_badge, MainWindow.step3_label, MainWindow.reset_parameters_button))
        card3_layout.addWidget(MainWindow.parameters_tabs)
        left_layout.addWidget(card3, stretch=1)

        # Step 4: validation
        card4, card4_layout = make_card()
        card4_layout.addLayout(step_header_row(
            MainWindow.step4_badge, MainWindow.step4_label, MainWindow.validation_button))
        left_layout.addWidget(card4)

        # Step 5: create video
        card5, card5_layout = make_card()
        card5_layout.addLayout(step_header_row(MainWindow.step5_badge, MainWindow.step5_label))
        card5_layout.addWidget(MainWindow.delete_temp_checkbox)
        card5_layout.addWidget(MainWindow.append_duration_checkbox)

        filename_layout = QHBoxLayout()
        filename_layout.addWidget(QLabel(MainWindow.tr("Filename:")))
        filename_layout.addWidget(MainWindow.filename_input)
        card5_layout.addLayout(filename_layout)

        button_layout = QHBoxLayout()
        button_layout.addWidget(MainWindow.preview_button)
        button_layout.addWidget(MainWindow.create_video_button)
        button_layout.addWidget(MainWindow.cancel_button)
        card5_layout.addLayout(button_layout)
        card5_layout.addWidget(MainWindow.progress_bar)
        left_layout.addWidget(card5)

        # -- Right Pane --
        right_layout = QVBoxLayout()
        # Styled by the app QSS via the 'statusBar' object name; the severity
        # (info/working/success/error) is set as a dynamic property at runtime.
        MainWindow.status_label = QLabel("")
        MainWindow.status_label.setObjectName("statusBar")
        MainWindow.status_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        MainWindow.status_label.setWordWrap(True)
        MainWindow.status_label.setProperty("severity", "info")
        MainWindow.status_label.setMinimumHeight(38)

        right_layout.addWidget(MainWindow.status_label)
        right_layout.addWidget(MainWindow.tabs)

        # -- Main Layout --
        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout, stretch=1)
        main_layout.addLayout(right_layout, stretch=4)
        MainWindow.setLayout(main_layout)

        # The clickable folder-path labels and the validation warning banner are
        # styled centrally in ssmm/theme.py (build_app_qss) via their object
        # names, so no per-widget stylesheet is needed here.