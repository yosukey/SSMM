# ui_main.py
from PySide6.QtWidgets import (QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                               QTextEdit, QComboBox, QSpinBox, QCheckBox,
                               QSizePolicy, QFormLayout, QLineEdit, QProgressBar,
                               QTabWidget, QWidget, QTableWidget, QSpacerItem,
                               QPlainTextEdit, QAbstractItemView)
from PySide6.QtCore import Qt, Signal

class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


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
        MainWindow.validation_button.setEnabled(False)

        MainWindow.select_output_button = QPushButton(MainWindow.tr("Browse..."))
        MainWindow.output_folder_label = ClickableLabel("")
        MainWindow.output_folder_label.setObjectName("outputFolderLabel")
        MainWindow.output_folder_label.setWordWrap(False)
        MainWindow.output_folder_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        MainWindow.preview_button = QPushButton(MainWindow.tr("Create Preview"))
        MainWindow.preview_button.setEnabled(False)
        MainWindow.create_video_button = QPushButton(MainWindow.tr("Create Video"))
        MainWindow.create_video_button.setEnabled(False)

        MainWindow.cancel_button = QPushButton(MainWindow.tr("Cancel"))
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
        MainWindow.normalize_loudness_mode_combo.addItems([MainWindow.tr("1-Pass (Faster)"), MainWindow.tr("2-Pass (Recommended)")])
        
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(MainWindow.normalize_loudness_mode_combo)
        mode_layout.addStretch()
        
        audio_layout.addRow(MainWindow.normalize_loudness_mode_label, mode_layout)
        
        MainWindow.parameters_tabs.addTab(MainWindow.audio_tab, MainWindow.tr("Audio Options"))

        # Video Options Tab
        MainWindow.video_tab = QWidget()
        video_layout = QFormLayout(MainWindow.video_tab)

        # Add FPS settings to the top of this tab
        MainWindow.fps_label = QLabel(MainWindow.tr("FPS:"))
        MainWindow.fps_combo = QComboBox()
        MainWindow.fps_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        video_layout.addRow(MainWindow.fps_label, MainWindow.fps_combo)

        MainWindow.encoding_mode_label = QLabel(MainWindow.tr("Encoding Mode:"))
        MainWindow.encoding_mode_combo = QComboBox()
        video_layout.addRow(MainWindow.encoding_mode_label, MainWindow.encoding_mode_combo)
        
        MainWindow.value_label = QLabel(MainWindow.tr("Quality (0-51):"))
        MainWindow.value_spin = QSpinBox()
        video_layout.addRow(MainWindow.value_label, MainWindow.value_spin)
        
        MainWindow.pass_label = QLabel(MainWindow.tr("Passes:"))
        MainWindow.pass_combo = QComboBox()
        video_layout.addRow(MainWindow.pass_label, MainWindow.pass_combo)
        
        # Add a separator and the preview checkbox
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
        MainWindow.step1_label = QLabel(MainWindow.tr("1️⃣ Select Project Folder"))
        MainWindow.step1_label.setProperty('class', 'stepLabel')
        MainWindow.step2_label = QLabel(MainWindow.tr("2️⃣ Select Output Folder"))
        MainWindow.step2_label.setProperty('class', 'stepLabel')
        MainWindow.step3_label = QLabel(MainWindow.tr("3️⃣ Set Parameters"))
        MainWindow.step3_label.setProperty('class', 'stepLabel')
        MainWindow.reset_parameters_button = QPushButton(MainWindow.tr("Reset Defaults"))
        MainWindow.reset_parameters_button.setObjectName("resetDefaultsButton")
        MainWindow.reset_parameters_button.setToolTip(MainWindow.tr("Reset all parameters below to their default values."))
        MainWindow.reset_parameters_button.setMaximumWidth(120)
        MainWindow.step4_label = QLabel(MainWindow.tr("4️⃣ Validation"))
        MainWindow.step4_label.setProperty('class', 'stepLabel')
        MainWindow.step5_label = QLabel(MainWindow.tr("5️⃣ Create Video"))
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
        MainWindow.slide_table = QTableWidget()
        MainWindow.slide_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        MainWindow.slide_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        slide_settings_layout = QVBoxLayout()
        MainWindow.total_duration_label = QLabel(MainWindow.tr("Total Duration: 0 sec"))
        
        edit_selection_layout = QHBoxLayout()
        
        select_label = QLabel(MainWindow.tr("Select:"))
        edit_selection_layout.addWidget(select_label)
        
        MainWindow.select_all_button = QPushButton(MainWindow.tr("All"))
        MainWindow.select_all_button.setToolTip(MainWindow.tr("Select all slides"))
        
        MainWindow.select_video_button = QPushButton(MainWindow.tr("Movie"))
        MainWindow.select_video_button.setToolTip(MainWindow.tr("Select slides with video material"))

        MainWindow.select_audio_button = QPushButton(MainWindow.tr("Audio"))
        MainWindow.select_audio_button.setToolTip(MainWindow.tr("Select slides with audio-only material"))

        edit_selection_layout.addWidget(MainWindow.select_all_button)
        edit_selection_layout.addWidget(MainWindow.select_video_button)
        edit_selection_layout.addWidget(MainWindow.select_audio_button)
        
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

        licenses_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Fixed))

        # Compliance label with links
        MainWindow.ffmpeg_compliance_label = QLabel()
        MainWindow.ffmpeg_compliance_label.setTextFormat(Qt.RichText)
        MainWindow.ffmpeg_compliance_label.setOpenExternalLinks(True)
        MainWindow.ffmpeg_compliance_label.setWordWrap(True)
        licenses_layout.addWidget(MainWindow.ffmpeg_compliance_label)

        # Notice for when system FFmpeg is used
        MainWindow.system_ffmpeg_notice_label = QLabel()
        MainWindow.system_ffmpeg_notice_label.setTextFormat(Qt.RichText)
        MainWindow.system_ffmpeg_notice_label.setWordWrap(True)
        licenses_layout.addWidget(MainWindow.system_ffmpeg_notice_label)

        # Header for bundled FFmpeg license
        MainWindow.ffmpeg_license_header = QLabel(MainWindow.tr("<b>Bundled FFmpeg License (LGPL v2.1)</b>"))
        licenses_layout.addWidget(MainWindow.ffmpeg_license_header)

        # Text area for LGPL.txt
        MainWindow.ffmpeg_license_text_edit = QTextEdit()
        MainWindow.ffmpeg_license_text_edit.setReadOnly(True)
        licenses_layout.addWidget(MainWindow.ffmpeg_license_text_edit)

        # Header for bundled FFmpeg build config
        MainWindow.ffmpeg_build_config_header = QLabel(MainWindow.tr("<b>Bundled FFmpeg Build Configuration</b>"))
        licenses_layout.addWidget(MainWindow.ffmpeg_build_config_header)

        # Text area for ffmpeg_build_config.txt
        MainWindow.ffmpeg_build_config_text_edit = QTextEdit()
        MainWindow.ffmpeg_build_config_text_edit.setReadOnly(True)
        licenses_layout.addWidget(MainWindow.ffmpeg_build_config_text_edit)
        
        MainWindow.tabs.addTab(MainWindow.licenses_tab, MainWindow.tr("Licenses"))


        # Layouts
        # -- Left Pane --
        left_layout = QVBoxLayout()
        project_folder_layout = QHBoxLayout()
        project_folder_layout.addWidget(MainWindow.step1_label)
        project_folder_layout.addWidget(MainWindow.select_project_folder_button)
        left_layout.addLayout(project_folder_layout)
        left_layout.addWidget(MainWindow.project_folder_label)

        output_folder_layout = QHBoxLayout()
        output_folder_layout.addWidget(MainWindow.step2_label)
        output_folder_layout.addWidget(MainWindow.select_output_button)
        left_layout.addLayout(output_folder_layout)
        left_layout.addWidget(MainWindow.output_folder_label)

        step3_layout = QHBoxLayout()
        step3_layout.addWidget(MainWindow.step3_label)
        step3_layout.addStretch()
        step3_layout.addWidget(MainWindow.reset_parameters_button)
        left_layout.addLayout(step3_layout)
        left_layout.addWidget(MainWindow.parameters_tabs)

        validation_layout_h = QHBoxLayout()
        validation_layout_h.addWidget(MainWindow.step4_label)
        validation_layout_h.addWidget(MainWindow.validation_button)
        left_layout.addLayout(validation_layout_h)
        left_layout.addWidget(MainWindow.step5_label)
        left_layout.addWidget(MainWindow.delete_temp_checkbox)
        left_layout.addWidget(MainWindow.append_duration_checkbox)
        
        # Filename layout (Horizontal)
        filename_layout = QHBoxLayout()
        filename_layout.addWidget(QLabel(MainWindow.tr("Filename:")))
        filename_layout.addWidget(MainWindow.filename_input)
        left_layout.addLayout(filename_layout)

        button_layout = QHBoxLayout()
        button_layout.addWidget(MainWindow.preview_button)
        button_layout.addWidget(MainWindow.create_video_button)
        button_layout.addWidget(MainWindow.cancel_button)
        left_layout.addLayout(button_layout)
        left_layout.addWidget(MainWindow.progress_bar)
        
        # -- Right Pane --
        right_layout = QVBoxLayout()
        MainWindow.status_label = QLabel("")
        MainWindow.status_label.setObjectName("statusLabel")
        MainWindow.status_label.setAlignment(Qt.AlignCenter)
        MainWindow.status_label.setWordWrap(True)
        
        MainWindow.status_label.setStyleSheet("""
            QLabel#statusLabel {
                background-color: #d35400;
                color: white;
                font-weight: bold;
                padding: 5px;
                border-radius: 4px;
                min-height: 25px;
            }
        """)
        
        right_layout.addWidget(MainWindow.status_label)
        right_layout.addWidget(MainWindow.tabs)

        # -- Main Layout --
        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout, stretch=1)
        main_layout.addLayout(right_layout, stretch=4)
        MainWindow.setLayout(main_layout)

        clickable_and_warning_style = """
            QLabel#projectFolderLabel, QLabel#outputFolderLabel {
                color: #448AFF;
                text-decoration: underline;
            }
            QLabel#validationWarningLabel {
                background-color: #E67E22;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
        """
        MainWindow.setStyleSheet(MainWindow.styleSheet() + clickable_and_warning_style)