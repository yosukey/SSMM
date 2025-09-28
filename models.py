# models.py
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


class AppState(Enum):
    CHECKING_ENCODERS = auto()
    AWAITING_PROJECT = auto()
    LOADING_PROJECT = auto()
    PROJECT_LOADED_UIPOPULATED = auto()
    PREPARE_TO_VALIDATE = auto()
    READY_TO_VALIDATE = auto()
    VALIDATING = auto()
    VALIDATED = auto()
    PROCESSING = auto()
    CANCELLING = auto()
    ERROR = auto()

@dataclass
class ProjectParameters:
    resolution: str = "1920x1080"
    fps: int = 30
    codec: str = "H.264/MPEG-4 AVC"
    hardware_encoding: Optional[str] = None
    encoding_mode: str = "Quality (CQP/CRF)"
    encoding_value: int = 23
    encoding_pass: str = "1-Pass"
    audio_bitrate: str = "160k"
    audio_sample_rate: str = "32000"
    audio_channels: int = 2
    normalize_loudness: bool = False
    normalize_loudness_mode: str = "2-Pass (Recommended)"
    add_watermark: bool = False
    watermark_text: str = ""
    watermark_opacity: int = 50
    watermark_color: str = "white"
    watermark_fontsize: int = 8
    watermark_fontfamily: str = "Noto Sans CJK JP"
    watermark_rotation: str = "None"
    watermark_tile: bool = False
    export_youtube_chapters: bool = False
    delete_temp_checkbox: bool = True
    append_duration_checkbox: bool = False
    filename_input: str = ""
    available_encoders: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Slide:
    filename: Optional[str] = None
    duration: float = 0.0
    is_video: bool = False
    tech_info: Dict[str, Any] = field(default_factory=dict)
    audio_streams: List[Dict[str, Any]] = field(default_factory=list)
    selected_audio_stream_index: int = 0
    chapter_title: str = ""
    video_position: Optional[str] = "Center"
    video_scale: int = config.DEFAULT_VIDEO_SCALE
    video_effects: list[str] = field(default_factory=list)
    interval_to_next: int = config.DEFAULT_SLIDE_INTERVAL
    transition_to_next: str = "None"
    p_hash: Optional[str] = None
    thumbnail_b64: Optional[str] = None

@dataclass
class ProjectModel:
    project_folder: Optional[Path] = None
    output_folder: Optional[Path] = None
    slides: List[Slide] = field(default_factory=list)
    parameters: ProjectParameters = field(default_factory=ProjectParameters)
    total_duration: float = 0.0
    available_materials: List[str] = field(default_factory=list)

class ValidationMessages:
    def __init__(self):
        self.project_errors: list[str] = []
        self.project_warnings: list[str] = []
        self.project_notices: list[str] = []
        self.project_info: list[str] = []
        self.encoder_info: list[str] = []
        self.file_messages: dict[str, dict] = {}
        self.file_order: list[str] = []

    def _ensure_file_entry(self, filename: str):
        if filename not in self.file_messages:
            self.file_messages[filename] = {
                "tech_info": [],
                "warnings": [],
                "notices": [],
                "usages": []
            }
            if filename not in self.file_order:
                self.file_order.append(filename)

    def add_project_error(self, message: str):
        self.project_errors.append(message)
    
    def add_project_warning(self, message: str):
        self.project_warnings.append(message)
        
    def add_project_notice(self, message: str):
        self.project_notices.append(message)

    def add_project_info(self, message: str):
        self.project_info.append(message)

    def add_encoder_info(self, message: str):
        self.encoder_info.append(message)

    def add_file_tech_info(self, filename: str, info_list: list[str]):
        self._ensure_file_entry(filename)
        self.file_messages[filename]["tech_info"].extend(info_list)

    def add_file_warning(self, filename: str, message: str):
        self._ensure_file_entry(filename)
        self.file_messages[filename]["warnings"].append(message)
    
    def add_file_notice(self, filename: str, message: str):
        self._ensure_file_entry(filename)
        self.file_messages[filename]["notices"].append(message)
        
    def add_file_usage_summary(self, filename: str, slide_index: int, pinp_geometry: dict, slide: Slide, preview_base64: str, warnings: list[str]):
        self._ensure_file_entry(filename)
        usage_data = {
            "slide_index": slide_index,
            "pinp_geometry": pinp_geometry,
            "slide": slide,
            "preview_base64": preview_base64,
            "warnings": warnings
        }
        self.file_messages[filename]["usages"].append(usage_data)

    def has_errors(self) -> bool:
        return len(self.project_errors) > 0
    
    def assemble_html(self, theme: str = "dark") -> str:
        body_content = []
        
        has_project_messages = self.project_errors or self.project_warnings or self.project_notices
        
        if has_project_messages:
            body_content.append("<h2>Project Status</h2>")
            if self.project_errors:
                body_content.extend(f"<p><span class='label error'>Error:</span> {e}</p>" for e in self.project_errors)
            if self.project_warnings:
                body_content.extend(f"<p><span class='label warning'>Warning:</span> {w}</p>" for w in self.project_warnings)
            if self.project_notices:
                body_content.extend(f"<p><span class='label notice'>Notice:</span> {n}</p>" for n in self.project_notices)
        
        if self.project_info:
            if has_project_messages:
                body_content.append('<hr class="section-divider">')
            for info in self.project_info:
                 body_content.append(f'<div class="info-box">{info}</div>')

        if self.encoder_info:
            if has_project_messages or self.project_info:
                body_content.append('<hr class="section-divider">')
            body_content.append("<h2>FFmpeg & Encoder Status</h2>")
            for info in self.encoder_info:
                 body_content.append(f'<div class="info-box">{info}</div>')

        if self.file_messages:
            if has_project_messages or self.project_info or self.encoder_info:
                 body_content.append('<hr class="section-divider">')
            body_content.append("<h2>Media File Analysis</h2>")
            
            for filename in self.file_order:
                messages = self.file_messages[filename]
                tech_info_list = messages.get("tech_info", [])
                
                body_content.append('<div class="file-box-wrapper">')
                
                if tech_info_list:
                    body_content.append(f'<h3 class="file-box-title">{tech_info_list[0]}</h3>')
                    body_content.append('<div class="tech-info">')
                    for line in tech_info_list[1:]:
                        body_content.append(f"<p>{line}</p>")

                    for warning in messages.get("warnings", []):
                        body_content.append(f"<p><span class='label warning'>Warning:</span> {warning}</p>")
                    for notice in messages.get("notices", []):
                        body_content.append(f"<p><span class='label notice'>Notice:</span> {notice}</p>")
                    body_content.append('</div>')
                else:
                    body_content.append(f'<h3 class="file-box-title"><b>{filename}</b></h3>')

                if messages.get("usages"):
                    body_content.append('<div class="usages-container">')
                    for usage in messages["usages"]:
                        slide = usage["slide"]
                        pinp_geometry = usage["pinp_geometry"]
                        
                        text_summary = f"<p>PinP size will be {pinp_geometry['width']}x{pinp_geometry['height']}px at '{slide.video_position}'.</p>"
                        visual_summary_html = f'<div class="pinp-preview"><img src="data:image/png;base64,{usage["preview_base64"]}" /></div>' if usage["preview_base64"] else ""
                        
                        body_content.append('<div class="usage-item">')
                        body_content.append('<div class="usage-details">')
                        body_content.append(f"<p><span class='label'><b>Slide {usage['slide_index'] + 1}:</b></span></p>")
                        for warning in usage["warnings"]:
                            body_content.append(f"<p><span class='label warning'>Warning:</span> {warning}</p>")
                        body_content.append(text_summary)
                        body_content.append('</div>')
                        body_content.append(f'<div class="usage-preview">{visual_summary_html}</div>')
                        body_content.append('</div>')
                    body_content.append('</div>')

                body_content.append('</div>')

        if not body_content:
            body_content.append("<h2>Validation Successful</h2><p>No issues found. You can now proceed to create the video.</p>")

        css = """
        body { line-height: 1.6; margin: 15px; } h2 { margin-top: 20px; margin-bottom: 10px; padding-bottom: 5px; } h3 { margin-top: 0; } p { margin-top: 5px; margin-bottom: 5px; }
        hr.section-divider { border: none; margin-top: 20px; margin-bottom: 20px; } .label { font-weight: bold; margin-right: 6px; } .tech-info { margin-left: 15px; padding-bottom: 10px; }
        .status { font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-right: 10px; font-size: 0.9em; }
        .usages-container { margin-top: 15px; padding-top: 15px; }
        .usage-item { display: flex; align-items: flex-start; margin-bottom: 15px; padding: 12px; border-radius: 4px; }
        .usage-preview { flex-shrink: 0; margin-left: 15px; }
        .usage-details { flex-grow: 1; }
        .encoder-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }
        .encoder-table th, .encoder-table td { border: 1px solid; padding: 6px; text-align: center; }
        .encoder-table th { font-weight: bold; } .encoder-table td:first-child { text-align: left; font-weight: bold; }
        """

        if theme == 'light':
            css += """
            body { color: #2E2E2E; background-color: #F0F2F5; } h2 { border-bottom: 1px solid #E0E0E0; } hr.section-divider { border-top: 1px dashed #DDD; }
            .label.error { color: #D32F2F; } .label.warning { color: #0288D1; } .label.notice { color: #388E3C; }
            .info-box { background-color: #F5F5F5; border-left: 3px solid #BDBDBD; padding: 10px 15px; margin-top: 15px; }
            .file-box-wrapper { background-color: #FFFFFF; padding: 15px; border-radius: 8px; margin-top: 20px; border: 1px solid #DCDCDC; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
            .file-box-title { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #EAEAEA; font-size: 1.1em; }
            .usages-container { border-top: 1px solid #EAEAEA; }
            .usage-item { background-color: #F9F9F9; border: 1px solid #EAEAEA; }
            .status.used { background-color: #2E7D32; color: white; } .status.unused { background-color: #757575; color: white; }
            .encoder-table th, .encoder-table td { border-color: #E0E0E0; } .encoder-table th { background-color: #F5F5F5; }
            .supported { background-color: #E8F5E9; color: #1B5E20; } .not-supported { background-color: #F5F5F5; color: #BDBDBD; }
            """
        else:
            css += """
            body { color: #E0E0E0; background-color: #242526; } h2 { border-bottom: 1px solid #4A4A4A; } hr.section-divider { border-top: 1px dashed #4A4A4A; }
            .label.error { color: #F44336; } .label.warning { color: #29B6F6; } .label.notice { color: #9CCC65; }
            .info-box { background-color: #3A3B3C; border-left: 3px solid #666; padding: 10px 15px; margin-top: 15px; }
            .file-box-wrapper { background-color: #2E2E2E; padding: 15px; border-radius: 8px; margin-top: 20px; border: 1px solid #4A4A4A; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }
            .file-box-title { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #4A4A4A; font-size: 1.1em; color: #E0E0E0; }
            .usages-container { border-top: 1px solid #4A4A4A; }
            .usage-item { background-color: #3A3B3C; border: 1px solid #555555; }
            .status.used { background-color: #66BB6A; color: black; } .status.unused { background-color: #616161; color: #E0E0E0; }
            .encoder-table th, .encoder-table td { border-color: #555; } .encoder-table th { background-color: #3C3C3C; }
            .supported { background-color: #1B5E20; color: #C8E6C9; } .not-supported { background-color: #2E2E2E; color: #757575; }
            """

        html_doc = f"<!DOCTYPE html><html><head><meta charset='UTF-8'><style>{css}</style></head><body>{''.join(body_content)}</body></html>"
        return html_doc