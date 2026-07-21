# models.py
import copy
import html
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

from ssmm import config


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
    normalize_loudness_mode: str = config.LOUDNORM_MODES["TWO_PASS"]
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
            # Snapshot the slide so later edits do not change the report.
            "slide": copy.deepcopy(slide),
            "preview_base64": preview_base64,
            "warnings": warnings
        }
        self.file_messages[filename]["usages"].append(usage_data)

    def has_errors(self) -> bool:
        return len(self.project_errors) > 0
    
    def assemble_html(self, theme: str = "dark", include_verdict: bool = True) -> str:
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
                    body_content.append(f'<div class="file-box-title">{tech_info_list[0]}</div>')
                    body_content.append('<div class="tech-info">')
                    for line in tech_info_list[1:]:
                        body_content.append(f"<p>{line}</p>")

                    for warning in messages.get("warnings", []):
                        body_content.append(f"<p><span class='label warning'>Warning:</span> {warning}</p>")
                    for notice in messages.get("notices", []):
                        body_content.append(f"<p><span class='label notice'>Notice:</span> {notice}</p>")
                    body_content.append('</div>')
                else:
                    # No tech info was collected (e.g. an unreadable file). Still render
                    # any warnings/notices attached to it — they are counted in the
                    # banner, so dropping them here would leave a phantom warning count.
                    body_content.append(f'<div class="file-box-title"><b>{html.escape(filename)}</b></div>')
                    file_warnings = messages.get("warnings", [])
                    file_notices = messages.get("notices", [])
                    if file_warnings or file_notices:
                        body_content.append('<div class="tech-info">')
                        for warning in file_warnings:
                            body_content.append(f"<p><span class='label warning'>Warning:</span> {warning}</p>")
                        for notice in file_notices:
                            body_content.append(f"<p><span class='label notice'>Notice:</span> {notice}</p>")
                        body_content.append('</div>')

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
            body_content.append("<p class='empty-note'>No detailed messages to report.</p>")

        # The verdict banner summarizes an actual validation run. When this report
        # is rendered as a pre-validation preview (e.g. the startup encoder matrix),
        # no validation has happened yet, so the caller suppresses the banner to
        # avoid a misleading "Validation passed" verdict.
        if include_verdict:
            # A single verdict banner at the top summarizes the whole report before
            # any detail — success / warnings / errors, with per-severity counts.
            error_count = len(self.project_errors)
            warning_count = len(self.project_warnings)
            notice_count = len(self.project_notices)
            for file_msg in self.file_messages.values():
                warning_count += len(file_msg.get("warnings", []))
                notice_count += len(file_msg.get("notices", []))
                for usage in file_msg.get("usages", []):
                    warning_count += len(usage.get("warnings", []))

            if error_count:
                verdict_class, headline = "err", "Validation failed"
                sub = f"{error_count} issue(s) must be resolved before the video can be created."
            elif warning_count:
                verdict_class, headline = "warn", "Completed with warnings"
                sub = "The video can still be created — please review the notes below first."
            else:
                verdict_class, headline = "ok", "Validation passed"
                sub = "All checks passed. You are ready to create the video."

            banner = (
                f'<div class="summary {verdict_class}">'
                f'<div class="headline">{headline}</div>'
                f'<div class="sub">{sub}</div>'
                # QTextEdit ignores inline margins, so separate the counts with
                # explicit non-breaking spaces rather than relying on margin-right.
                f'<div class="counts">'
                f'<span class="count err">Errors: {error_count}</span>'
                f'&nbsp;&nbsp;&nbsp;&nbsp;'
                f'<span class="count warn">Warnings: {warning_count}</span>'
                f'&nbsp;&nbsp;&nbsp;&nbsp;'
                f'<span class="count note">Notices: {notice_count}</span>'
                f'</div></div>'
            )
            body_content.insert(0, banner)

        # Pull colors from the central design tokens so the report matches the
        # rest of the app in both themes. Percent-formatting is used because CSS
        # literal braces make str.format awkward; the only literal '%' (width)
        # is escaped as '%%'.
        from ssmm import theme as app_theme
        t = app_theme.tokens(theme if theme in ("light", "dark") else None)

        css = """
        body { color: %(text)s; background-color: %(surface_2)s; line-height: 1.55; margin: 16px; font-size: 13px; }
        h2 { color: %(accent)s; font-size: 12px; font-weight: bold; margin: 22px 0 10px; padding-bottom: 6px; border-bottom: 1px solid %(border)s; }
        h3 { margin: 0; }
        p { margin: 5px 0; }
        hr.section-divider { border: none; border-top: 1px solid %(border_soft)s; margin: 20px 0; }
        .empty-note { color: %(text_muted)s; }

        .label { font-weight: bold; margin-right: 6px; }
        .label.error { color: %(danger)s; }
        .label.warning { color: %(warn)s; }
        .label.notice { color: %(ok)s; }
        .tech-info { margin-left: 2px; padding-bottom: 8px; color: %(text_muted)s; }

        .summary { padding: 14px 16px; border: 1px solid %(border)s; border-left: 4px solid %(accent)s; border-radius: 8px; background-color: %(surface)s; margin-bottom: 4px; }
        .summary .headline { font-size: 15px; font-weight: bold; }
        .summary .sub { color: %(text_muted)s; margin-top: 4px; }
        .summary .counts { margin-top: 10px; }
        .summary .count { font-weight: bold; margin-right: 16px; }
        .summary.ok { border-left-color: %(ok)s; background-color: %(ok_tint)s; }
        .summary.ok .headline { color: %(ok)s; }
        .summary.warn { border-left-color: %(warn)s; background-color: %(warn_tint)s; }
        .summary.warn .headline { color: %(warn)s; }
        .summary.err { border-left-color: %(danger)s; background-color: %(danger_tint)s; }
        .summary.err .headline { color: %(danger)s; }
        .count.err { color: %(danger)s; }
        .count.warn { color: %(warn)s; }
        .count.note { color: %(ok)s; }

        .info-box { background-color: %(accent_tint)s; border: 1px solid %(accent_line)s; border-left: 3px solid %(accent)s; border-radius: 6px; padding: 10px 14px; margin-top: 12px; }

        .file-box-wrapper { background-color: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; padding: 14px; margin-top: 14px; }
        .file-box-title { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid %(border_soft)s; font-size: 13px; font-weight: bold; color: %(text)s; }
        .tech-info p { color: %(text_muted)s; }

        .usages-container { margin-top: 12px; }
        .usage-item { background-color: %(surface_2)s; border: 1px solid %(border_soft)s; border-radius: 6px; padding: 12px; margin-bottom: 10px; }
        .usage-preview { padding-top: 6px; }

        .status { font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 0.9em; }
        .status.used { background-color: %(ok_tint)s; color: %(ok)s; }
        .status.unused { background-color: %(surface_2)s; color: %(text_faint)s; }

        .file-title-table { width: 100%%; border-collapse: collapse; }
        .file-title-table td { border: none; padding: 0; vertical-align: middle; }
        .file-title-status { white-space: nowrap; }

        .encoder-table { width: 100%%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }
        .encoder-table th, .encoder-table td { border: 1px solid %(border)s; padding: 7px 10px; text-align: center; }
        .encoder-table th { background-color: %(surface_2)s; color: %(text_muted)s; font-weight: bold; }
        .encoder-table td:first-child { text-align: left; font-weight: bold; color: %(text)s; }
        .supported { background-color: %(ok_tint)s; color: %(ok)s; font-weight: bold; }
        .not-supported { background-color: %(surface_2)s; color: %(text_faint)s; }
        """ % t

        html_doc = f"<!DOCTYPE html><html><head><meta charset='UTF-8'><style>{css}</style></head><body>{''.join(body_content)}</body></html>"
        return html_doc