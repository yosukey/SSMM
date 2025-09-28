# validator.py
import base64
import hashlib
import io
import json
import platform
import re
import subprocess
import sys
import os
from pathlib import Path
from typing import Optional, Tuple

import fitz
import imagehash
from PIL import Image

import config
from models import ProjectModel, ProjectParameters, Slide, ValidationMessages
from ui_helpers import calculate_pinp_geometry, create_pinp_preview_for_report
from utils import get_ffmpeg_path, get_ffprobe_path, get_ffmpeg_source

try:
    from capabilities import load_capabilities
except Exception:
    def load_capabilities():
        return {"EDITION": "B", "FFMPEG_INSTALL_MENU": True}

def check_encoder_functionality(encoder_name: str) -> bool:
    try:
        ffmpeg_path = str(get_ffmpeg_path())
    except FileNotFoundError:
        return False

    command_list = [
        ffmpeg_path,
        '-loglevel', 'error',
        '-f', 'lavfi',
        '-i', f'color=c=black:s={config.ENCODER_TEST_RESOLUTION}:r={config.ENCODER_TEST_FRAMERATE}',
        '-c:v', encoder_name,
        '-t', str(config.ENCODER_TEST_DURATION_S),
        '-pix_fmt', 'yuv420p',
        '-f', 'null',
        '-'
    ]

    try:
        result = subprocess.run(
            command_list,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=config.ENCODER_TEST_TIMEOUT_S,
            creationflags=config.SUBPROCESS_CREATION_FLAGS
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False

class ProjectValidator:
    def __init__(self, logger=None):
        self.info_cache = {}
        self.file_hash_cache = {}
        self._is_canceled = False
        self.log = logger if callable(logger) else lambda *args, **kwargs: None
        self.validated_pdf_path: Path | None = None
        self.validated_pdf_structure: Optional[dict] = None
        self.validated_pdf_hash: str | None = None

    def analyze_material(self, material_path: Path, slide: Slide):
        file_hash = self._get_file_hash(material_path)
        if not file_hash:
            return

        if file_hash in self.info_cache:
            cached_data = self.info_cache[file_hash]
            slide.duration = cached_data['duration']
            slide.tech_info = cached_data['tech_info']
            slide.is_video = cached_data['is_video']
            slide.audio_streams = cached_data['audio_streams']
            return

        is_video = material_path.suffix.lower() in config.SUPPORTED_VIDEO_FORMATS
        slide.is_video = is_video
        
        duration, video_info, audio_streams = self._get_media_info(material_path)
        
        slide.duration = duration
        slide.audio_streams = audio_streams

        if is_video and video_info:
            slide.tech_info = video_info
        elif not is_video and audio_streams:
            slide.tech_info = audio_streams[0]
        
        self.info_cache[file_hash] = {
            'duration': slide.duration,
            'tech_info': slide.tech_info,
            'is_video': slide.is_video,
            'audio_streams': slide.audio_streams,
        }

    def _render_pdf_page_for_preview(self, pdf_path: Path, page_num: int) -> Optional[Image.Image]:
        doc = None
        try:
            doc = fitz.open(pdf_path)
            if 0 <= page_num < doc.page_count:
                page = doc.load_page(page_num)
                pix = page.get_pixmap(alpha=False)
                return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        except Exception as e:
            self.log(f"[ERROR] Failed to render PDF page {page_num} for preview: {e}")
        finally:
            if doc: doc.close()
        return None

    def clear_cache(self):
        self.info_cache.clear()
        self.file_hash_cache.clear()
        self.validated_pdf_path = None
        self.validated_pdf_structure = None
        self.validated_pdf_hash = None
        self.log("[INFO] All validator caches have been cleared.")

    def _get_pdf_structure(self, pdf_path: Path) -> dict:
        structure = {'page_count': 0, 'page_dims': []}
        try:
            with fitz.open(pdf_path) as doc:
                structure['page_count'] = doc.page_count
                for page in doc:
                    if self.is_canceled():
                        return {}
                    structure['page_dims'].append((page.rect.width, page.rect.height))
                return structure
        except Exception as e:
            self.log(f"[ERROR] Failed to get PDF structure for {pdf_path.name}: {e}")
            return {}

    def cache_pdf_structure(self, pdf_path: Path):
        try:
            self.validated_pdf_path = pdf_path
            self.validated_pdf_structure = self._get_pdf_structure(pdf_path)
            page_count = self.validated_pdf_structure.get('page_count', 0)
            self.log(f"[INFO] PDF structure cache updated for: {pdf_path.name} ({page_count} pages)")
        except Exception as e:
            self.log(f"[ERROR] Failed to cache PDF structure: {e}")
    
    def compute_and_populate_pdf_details(self, project_model: ProjectModel):
        self.log("[INFO] Computing p-hash and thumbnails for PDF pages...")
        if not project_model.project_folder:
            self.log("[WARNING] No project folder set, cannot compute PDF details.")
            return

        pdf_path = next(project_model.project_folder.glob('*.[pP][dD][fF]'), None)
        if not pdf_path:
            self.log("[WARNING] No PDF found, cannot compute page details.")
            return

        doc = None
        try:
            doc = fitz.open(pdf_path)
            if len(project_model.slides) != doc.page_count:
                self.log(f"[ERROR] Slide count ({len(project_model.slides)}) mismatches PDF page count ({doc.page_count}). Aborting detail computation.")
                return

            for i, slide in enumerate(project_model.slides):
                page = doc.load_page(i)
                
                zoom_factor = 2.0
                matrix = fitz.Matrix(zoom_factor, zoom_factor)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                pil_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                p_hash = imagehash.phash(pil_image)
                slide.p_hash = str(p_hash)
                
                target_thumb_height = 300
                thumbnail_size = (int(target_thumb_height * pix.width / pix.height), target_thumb_height)
                thumbnail_image = pil_image.copy()
                thumbnail_image.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                
                buffer = io.BytesIO()
                thumbnail_image.save(buffer, format="PNG")
                b64_string = base64.b64encode(buffer.getvalue()).decode('utf-8')
                slide.thumbnail_b64 = b64_string

            self.log("[INFO] Successfully computed and populated PDF details.")
        except Exception as e:
            self.log(f"[ERROR] Failed during PDF detail computation: {e}")
        finally:
            if doc:
                doc.close()

    def get_pdf_details(self, pdf_path: Path) -> Tuple[Optional[dict], Optional[str]]:
        if not pdf_path or not pdf_path.exists():
            return None, f"PDF file not found at path: {pdf_path}"

        details = {
            "page_count": 0,
            "p_hashes": [],
            "thumbnails_b64": [],
        }
        doc = None
        try:
            doc = fitz.open(pdf_path)
            details["page_count"] = doc.page_count

            for page in doc:
                zoom_factor = 2.0
                matrix = fitz.Matrix(zoom_factor, zoom_factor)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                pil_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                p_hash = imagehash.phash(pil_image)
                details["p_hashes"].append(str(p_hash))

                target_thumb_height = 300
                thumbnail_size = (int(target_thumb_height * pix.width / pix.height), target_thumb_height)
                thumbnail_image = pil_image.copy()
                thumbnail_image.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)

                buffer = io.BytesIO()
                thumbnail_image.save(buffer, format="PNG")
                b64_string = base64.b64encode(buffer.getvalue()).decode('utf-8')
                details["thumbnails_b64"].append(b64_string)

            return details, None
        except Exception as e:
            error_msg = f"Could not get details for PDF {pdf_path.name}: {e}"
            self.log(f"[ERROR] {error_msg}")
            return None, error_msg
        finally:
            if doc:
                doc.close()

    def probe_and_cache_all_materials(self, project_model: ProjectModel):
        if not project_model or not project_model.project_folder:
            return

        self.log(f"[INFO] Probing all {len(project_model.available_materials)} available materials...")
        for material_name in project_model.available_materials:
            if self.is_canceled(): return
            
            mf_path = project_model.project_folder / material_name
            if not mf_path.exists():
                continue
            
            try:
                self.analyze_material(mf_path, Slide())
            except Exception as e:
                self.log(f"[WARNING] Could not process or cache file '{material_name}': {e}")

    def start_validation(self):
        self._is_canceled = False

    def cancel(self):
        self._is_canceled = True

    def is_canceled(self) -> bool:
        return self._is_canceled

    def _get_tool_version(self, tool_path: Path) -> str:
        try:
            result = subprocess.run(
                [str(tool_path), "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding='utf-8',
                errors='replace',
                creationflags=config.SUBPROCESS_CREATION_FLAGS
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.splitlines()[0].split()[2]
            return "N/A"
        except Exception:
            return "Error"

    def _get_available_encoders(self) -> set[str]:
        encoders = set()
        try:
            ffmpeg_path = str(get_ffmpeg_path())
            command_list = [ffmpeg_path, '-hide_banner', '-encoders']
            
            result = subprocess.run(
                command_list,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=config.SUBPROCESS_CREATION_FLAGS,
                timeout=config.ENCODER_TEST_TIMEOUT_S
            )
            
            if not result.stdout:
                return set()

            encoder_pattern = re.compile(r"^\s*[VAS.FXBD-]+\s+(\S+)")
            lines = result.stdout.splitlines()
            for line in lines:
                if self._is_canceled:
                    break
                match = encoder_pattern.match(line)
                if match:
                    encoders.add(match.group(1))
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError, subprocess.TimeoutExpired):
            return set()
        
        return encoders

    def get_functional_encoders(self) -> tuple[dict[str, list[str]], list[str]]:
        log_messages = ["[INFO] --- Encoder Functionality Test Start ---"]
        all_available_encoders = self._get_available_encoders()
        if not all_available_encoders:
            log_messages.append("[ERROR] Could not retrieve encoder list from FFmpeg.")

        if self._is_canceled: return {}, log_messages
        
        functional_map = {}

        sw_encoders_to_test = ['libx264', 'libx265']
        for sw_encoder in sw_encoders_to_test:
            if self._is_canceled: break
            if sw_encoder in all_available_encoders:
                log_messages.append(f"[INFO] Testing software encoder {sw_encoder}...")
                if check_encoder_functionality(sw_encoder):
                    log_messages.append(f"  -> [SUCCESS] '{sw_encoder}' is functional.")
                    for codec, encoders in config.SUPPORTED_CODEC_CHECKS.items():
                        if sw_encoder in encoders:
                            if codec not in functional_map: functional_map[codec] = []
                            functional_map[codec].append(sw_encoder)
                            break
                else:
                    log_messages.append(f"  -> [FAILED] '{sw_encoder}' is not functional.")
            else:
                 log_messages.append(f"  -> [SKIP] '{sw_encoder}' not found in 'ffmpeg -encoders' list.")
        
        if self._is_canceled: return functional_map, log_messages

        safe_sw_encoders = ['mpeg4', 'libaom-av1']
        for sw_encoder in safe_sw_encoders:
             if sw_encoder in all_available_encoders:
                for codec, encoders in config.SUPPORTED_CODEC_CHECKS.items():
                    if sw_encoder in encoders:
                        if codec not in functional_map: functional_map[codec] = []
                        if sw_encoder not in functional_map.get(codec, []):
                             functional_map[codec].append(sw_encoder)
                        break

        hw_families = ["NVIDIA", "Intel", "AMD", "videotoolbox"]
        codec_priority = ["H.264/MPEG-4 AVC", "H.265/HEVC", "AV1"]
        current_platform = sys.platform

        for hw_family in hw_families:
            if self._is_canceled: break

            if current_platform == 'darwin' and hw_family in ["NVIDIA", "Intel", "AMD"]:
                continue
            if current_platform != 'darwin' and hw_family == 'videotoolbox':
                continue

            family_failed = False
            for codec in codec_priority:
                if self._is_canceled: break
                
                hw_encoder = config.CODEC_MAP.get(codec, {}).get(hw_family)
                if not hw_encoder:
                    continue

                if hw_encoder in all_available_encoders:
                    log_messages.append(f"[INFO] Testing {hw_family} encoder for {codec}: {hw_encoder}...")
                    if check_encoder_functionality(hw_encoder):
                        log_messages.append(f"  -> [SUCCESS] '{hw_encoder}' is functional.")
                        if codec not in functional_map:
                            functional_map[codec] = []
                        functional_map[codec].append(hw_encoder)
                    else:
                        log_messages.append(f"  -> [FAILED] '{hw_encoder}' is not functional.")
                        if codec == "H.264/MPEG-4 AVC":
                            family_failed = True
                            log_messages.append(f"  -> [SKIP] H.264 failed, skipping further tests for the '{hw_family}' family.")
                else:
                    log_messages.append(f"  -> [INFO] Encoder '{hw_encoder}' is not available in the current FFmpeg build. Test skipped.")
                
                if family_failed:
                    break
        
        log_messages.append("[INFO] --- Encoder Test Finished ---")
        return functional_map, log_messages

    def validate(self, project_model: ProjectModel, available_encoders: dict) -> tuple[ValidationMessages, int, dict]:
        self.log("[INFO] Starting project validation...")
        
        messages = ValidationMessages()
        file_hashes_snapshot = {}

        if self._is_canceled: return messages, 0, file_hashes_snapshot
        self.log("[INFO] --- Phase 1/4: Checking FFmpeg installation ---")
        self._check_ffmpeg_installation(messages)
        
        if self._is_canceled: return messages, 0, file_hashes_snapshot
        self.log("[INFO] --- Phase 2/4: Analyzing PDF file ---")

        page_count = 0
        pdf_path = next(project_model.project_folder.glob('*.[pP][dD][fF]'), None) if project_model.project_folder else None

        if not pdf_path:
            msg = (
                "No PDF file found in the project folder.<br><br>"
                "<b>[Cause]</b><br>"
                "The application requires exactly one PDF file in the selected project folder to serve as the base for the slideshow.<br><br>"
                "<b>[Action]</b><br>"
                "Please add a single PDF file to your project folder and try again."
            )
            messages.add_project_error(msg)
        else:
            current_pdf_hash = self._get_file_hash(pdf_path)
            current_pdf_structure = self._get_pdf_structure(pdf_path)
            
            # Condition 1: PDF is completely unchanged (hash matches).
            if self.validated_pdf_hash and self.validated_pdf_hash == current_pdf_hash:
                self.log("[INFO] PDF file has not changed. Skipping detailed PDF analysis.")
                page_count = len(project_model.slides)
            
            # Condition 2: Minor change (hash differs, but page count is the same).
            elif not project_model.slides or (current_pdf_hash != self.validated_pdf_hash and len(project_model.slides) == current_pdf_structure.get('page_count', -1)):
                self.log("[INFO] PDF content changed without altering page structure. Updating thumbnails automatically.")
                self.compute_and_populate_pdf_details(project_model)
                msg = (
                    "The PDF file was modified, but the page structure is the same.<br><br>"
                    "<b>[Note]</b><br>"
                    "Thumbnails in the 'Slide Settings' tab have been automatically updated to reflect the changes in the PDF content."
                )
                messages.add_project_notice(msg)
                page_count = len(project_model.slides)
                self._check_pdf_file(project_model, messages, pdf_path)
            
            else:
                self.log("[INFO] PDF is being analyzed for the first time or its structure has changed significantly.")
                page_count = self._check_pdf_file(project_model, messages, pdf_path)
                
        if self._is_canceled: return messages, page_count, file_hashes_snapshot
        self.log("[INFO] --- Phase 3/4: Probing and analyzing media files ---")
        self._add_slide_information(project_model, messages)

        if pdf_path:
            self._check_warnings_and_additional_conditions(project_model, messages, pdf_path)

        if self._is_canceled: return messages, page_count, file_hashes_snapshot

        unassigned_slides = [i + 1 for i, slide in enumerate(project_model.slides) if slide.filename is None]
        if unassigned_slides:
            msg = (
                f"Material has not been assigned for slide(s): {', '.join(map(str, unassigned_slides))}.<br><br>"
                "<b>[Cause]</b><br>"
                "Every slide in the timeline must be assigned a video, an audio file, or explicitly marked as 'SILENT'.<br><br>"
                "<b>[Action]</b><br>"
                f"In the 'Slide Settings' tab, go to the specified slide row(s) and select a material from the dropdown menu. If a slide should only show the PDF page with no sound, select '{config.SILENT_MATERIAL_NAME}'."
            )
            messages.add_project_error(msg)
        
        if project_model.project_folder:
            all_formats = config.SUPPORTED_FORMATS + ('.pdf',)
            for entry in project_model.project_folder.iterdir():
                if self._is_canceled: break
                if entry.is_file() and entry.suffix.lower() in all_formats:
                    try:
                        file_hashes_snapshot[entry.name] = self._get_file_hash(entry)
                    except (IOError, OSError) as e:
                        messages.add_project_warning(f"Could not create hash for file {entry.name}: {e}")

        if self._is_canceled: return messages, page_count, file_hashes_snapshot

        if not messages.has_errors():
            self.log("[INFO] --- Phase 4/4: Checking parameter compatibility ---")
            self._validate_output_filename(project_model, messages)
            if self._is_canceled: return messages, page_count, file_hashes_snapshot
            self._check_hardware_encoder(project_model.parameters, messages, available_encoders)
            if self._is_canceled: return messages, page_count, file_hashes_snapshot
            
            self._check_parameter_compatibility(project_model.parameters, messages)
            if self._is_canceled: return messages, page_count, file_hashes_snapshot
            if project_model.parameters.export_youtube_chapters:
                self._validate_youtube_chapters(project_model, messages)
        
        if self._is_canceled: return messages, page_count, file_hashes_snapshot
        
        if not messages.has_errors() and pdf_path:
            self.cache_pdf_structure(pdf_path)
            self.validated_pdf_hash = self._get_file_hash(pdf_path)
            if self.validated_pdf_structure:
                page_count = self.validated_pdf_structure.get('page_count', 0)
                self.log(f"[INFO] PDF validation successful. Structure and file hash cache updated for {pdf_path.name} ({page_count} pages).")
            
        self._add_encoder_summary_notice(messages, available_encoders)
        
        self.log("[INFO] Validation finished.")
        return messages, page_count, file_hashes_snapshot

    def get_available_hw_options_for_codec(self, codec: str, functional_encoders_map: dict[str, list[str]]) -> set[str]:
        options = set()
        functional_encoders = functional_encoders_map.get(codec, [])

        potential_software_encoders = [
            enc for enc in config.SUPPORTED_CODEC_CHECKS.get(codec, [])
            if enc.startswith('lib') or enc == 'mpeg4'
        ]

        if set(potential_software_encoders) & set(functional_encoders):
            options.add("None")

        for enc in functional_encoders:
            if enc in config.ENCODER_TO_HARDWARE_MAP:
                options.add(config.ENCODER_TO_HARDWARE_MAP[enc])

        return options

    def _validate_output_filename(self, project_model: ProjectModel, messages: ValidationMessages):
        params = project_model.parameters
        effective_filename = params.filename_input.strip()
        
        if not effective_filename:
            if project_model.project_folder:
                effective_filename = project_model.project_folder.name
            else:
                msg = (
                    "The output filename is empty.<br><br>"
                    "<b>[Cause]</b><br>"
                    "An output filename is required. No project folder is set to use as a default name.<br><br>"
                    "<b>[Action]</b><br>"
                    "Please enter a name in the 'Filename' input box under Step 5."
                )
                messages.add_project_error(msg)
                return

        if not effective_filename:
            messages.add_project_error("The effective output filename is empty.")
            return

        control_chars_pattern = r'[\x00-\x1f\x7f]'
        if re.search(control_chars_pattern, effective_filename):
            messages.add_project_error("The filename contains invisible control characters, which are not allowed.")
            return

        for char in config.FILENAME_ILLEGAL_CHARS:
            if char in effective_filename:
                msg = (
                    f"The filename contains an illegal character: '{char}'<br><br>"
                    "<b>[Cause]</b><br>"
                    f"Operating systems do not allow the characters '{config.FILENAME_ILLEGAL_CHARS}' in filenames.<br><br>"
                    "<b>[Action]</b><br>"
                    f"Please remove the '{char}' character from the 'Filename' input box."
                )
                messages.add_project_error(msg)
                return
        
        if len(effective_filename.encode('utf-8')) > config.FILENAME_MAX_LENGTH:
            msg = (
                f"The filename is too long (max {config.FILENAME_MAX_LENGTH} bytes).<br><br>"
                "<b>[Action]</b><br>"
                "Please shorten the name in the 'Filename' input box."
            )
            messages.add_project_error(msg)
            return

        filename_base = effective_filename.split('.')[0]
        if filename_base.upper() in config.FILENAME_RESERVED_NAMES:
            msg = (
                f"The filename '{filename_base}' is a reserved system name.<br><br>"
                "<b>[Action]</b><br>"
                "Please choose a different name in the 'Filename' input box."
            )
            messages.add_project_error(msg)
            return

        if effective_filename.endswith('.') or effective_filename.endswith(' '):
            messages.add_project_error("The filename cannot end with a period or a space.")
            return

    def _add_encoder_summary_notice(self, messages: ValidationMessages, available_encoders: dict):
        try:
            ffmpeg_bin = str(get_ffmpeg_path())
        except FileNotFoundError:

            return

        caps = load_capabilities()
        src = get_ffmpeg_source()
        
        source_map = {
            "system": "System",
            "user": "User Local (~/ffmpeg-bin)",
            "bundled": "Bundled (LGPL)",
            "not_found": "Not Found"
        }
        source_label = source_map.get(src, "Unknown")

        ver_line = ""
        try:
            out = subprocess.run(
                [ffmpeg_bin, "-version"], 
                capture_output=True, 
                text=True, 
                timeout=5,
                creationflags=config.SUBPROCESS_CREATION_FLAGS
            )
            ver_line = out.stdout.splitlines()[0] if out.stdout else ""
        except Exception:
            pass

        if caps.get("EDITION") == "A":
            notice_html = [f"<b>FFmpeg Source:</b> {source_label}<br>"]
        else:
            notice_html = [f"<b>FFmpeg Path:</b> {ffmpeg_bin}<br>"]
        if ver_line:
            notice_html.append(f"<b>Version:</b> {ver_line}<br><br>")
        else:
            notice_html.append("<br>")
        
        if available_encoders:
            notice_html.append("<b>Available Encoders (functional test passed):</b>")
            
            headers = ['Codec', 'Software', 'NVIDIA', 'Intel', 'AMD', 'Apple VT']
            hw_map_keys = [None, 'NVIDIA', 'Intel', 'AMD', 'videotoolbox']
            codecs_to_display = ['H.264/MPEG-4 AVC', 'H.265/HEVC', 'AV1', 'MPEG-4 Part 2']

            table = '<table class="encoder-table"><thead><tr>'
            for header in headers:
                table += f'<th>{header}</th>'
            table += '</tr></thead><tbody>'

            for codec in codecs_to_display:
                table += f'<tr><td>{codec}</td>'
                functional_encoders = available_encoders.get(codec, [])
                
                for hw_key in hw_map_keys:
                    is_supported = False
                    if hw_key is None:
                        target_encoder = config.SOFTWARE_CODEC_MAP.get(codec)
                        if target_encoder in functional_encoders:
                            is_supported = True
                    else:
                        target_encoder = config.CODEC_MAP.get(codec, {}).get(hw_key)
                        if target_encoder in functional_encoders:
                            is_supported = True
                    
                    if is_supported:
                        table += '<td class="supported">✔</td>'
                    else:
                        table += '<td class="not-supported">❌</td>'
                table += '</tr>'

            table += '</tbody></table>'
            notice_html.append(table)
        
        messages.add_encoder_info("".join(notice_html))

    def _check_hardware_encoder(self, params: "ProjectParameters", messages: ValidationMessages, available_encoders: dict):
        selected_codec = params.codec
        hardware_encoder_name = params.hardware_encoding
        if hardware_encoder_name is not None:
            target_encoder = config.CODEC_MAP.get(selected_codec, {}).get(hardware_encoder_name)
            if not target_encoder:
                messages.add_project_error(f"Configuration error: No mapping found for codec '{selected_codec}' and hardware '{hardware_encoder_name}'.")
                return

            functional_encoders_for_codec = available_encoders.get(selected_codec, [])
            
            if target_encoder not in functional_encoders_for_codec:
                msg = (
                    f"The selected hardware encoder '{target_encoder}' is not functional.<br><br>"
                    "<b>[Cause]</b><br>"
                    "The application ran a quick test on the selected hardware encoder, and it failed to initialize. This is often caused by missing or outdated graphics drivers, or an unsupported hardware/FFmpeg combination.<br><br>"
                    "<b>[Action]</b><br>"
                    "  • Ensure your graphics drivers (NVIDIA, Intel, AMD) are up to date.<br>"
                    "  • In the 'Basic' tab, select 'None' for 'Hardware Encoding' to use reliable software encoding.<br>"
                    "  • Alternatively, try selecting a different Codec."
                )
                messages.add_project_error(msg)

    def _add_slide_information(self, project_model: ProjectModel, messages: ValidationMessages):
        used_materials = {slide.filename for slide in project_model.slides if slide.filename}
        for idx, slide in enumerate(project_model.slides):
            if self._is_canceled: return
            if slide.filename is None or slide.filename == config.SILENT_MATERIAL_NAME:
                slide.is_video = False
                continue

            mf_path = project_model.project_folder / slide.filename
            if not mf_path.exists():
                messages.add_project_error(f"Material file '{slide.filename}' not found in project folder.")
                slide.filename = None
                continue

            try:
                # Use the public method instead of the private one
                self.analyze_material(mf_path, slide)
            except Exception as e:
                messages.add_project_error(f"Failed to process file '{slide.filename}': {e}")
                continue
        
        if project_model.project_folder:
            for material_name in sorted(project_model.available_materials):
                if self._is_canceled: return
                mf_path = project_model.project_folder / material_name
                if not mf_path.exists(): continue

                try:
                    # Use the public method, which also handles caching
                    self.analyze_material(mf_path, Slide())
                    
                    file_hash = self._get_file_hash(mf_path)
                    if not file_hash or file_hash not in self.info_cache:
                        messages.add_file_warning(material_name, "Could not retrieve cached info for this file. It might be unreadable.")
                        continue

                    cached_data = self.info_cache[file_hash]
                    is_video = cached_data['is_video']
                    tech_info = cached_data['tech_info']
                    
                    tech_info_html = []
                    
                    if material_name in used_materials:
                        status_html = "<span class='status used'>✔ IN USE</span> "
                    else:
                        status_html = "<span class='status unused'>UNUSED</span> "

                    header = f"{status_html}<b>{material_name}</b> ({'Audio' if not is_video else 'PinP Video'})"
                    tech_info_html = [header]
                    if is_video:
                        w, h, codec, bitrate, fps, dar_str = tech_info.get('width'), tech_info.get('height'), tech_info.get('codec'), tech_info.get('bitrate'), tech_info.get('fps'), tech_info.get('dar')
                        rotation = tech_info.get('rotate')
                        dar_override_note, rotation_note = "", ""
                        if w > 0 and h > 0 and dar_str and ':' in dar_str and dar_str != '0:1':
                            try:
                                num, den = map(int, dar_str.split(':'))
                                if den > 0 and abs((w / h) - (num / den)) > 1e-4:
                                    dar_override_note = f" <b>(DAR override: {dar_str})</b>"
                            except (ValueError, TypeError): pass
                        if rotation and rotation != "0":
                            rotation_note = f" <b>(Rotation: {rotation}°)</b>"
                        tech_info_html.append(f"&nbsp;&nbsp;&nbsp;Video: {w}x{h}{dar_override_note}{rotation_note}, Codec: {codec}, Bitrate: {bitrate} kbps, FPS: {fps}")
                    audio_streams_info = cached_data.get('audio_streams', [])
                    if not audio_streams_info and not is_video:
                        tech_info_html.append(f"&nbsp;&nbsp;&nbsp;No audio stream found.")
                    
                    for i, audio_info in enumerate(audio_streams_info):
                        codec = audio_info.get('codec', 'N/A')
                        bitrate = audio_info.get('bitrate', 0)
                        sample_rate = audio_info.get('sample_rate', 'N/A')
                        channels = audio_info.get('channels', '?')
                        layout = audio_info.get('channel_layout', 'N/A')
                        lang = audio_info.get('language', 'unk')
                        title = audio_info.get('title', '')

                        bitrate_display = f"{bitrate} kbps" if isinstance(bitrate, int) and bitrate > 0 else bitrate
                        desc_parts = [f"lang: {lang}"] if len(audio_streams_info) > 1 else []
                        if title: desc_parts.append(f"title: {title}")
                        desc = ", ".join(desc_parts)

                        stream_prefix = f"&nbsp;&nbsp;&nbsp;Audio Stream #{i}:" if len(audio_streams_info) > 1 else "&nbsp;&nbsp;&nbsp;Audio:"
                        
                        audio_line = (f"{stream_prefix} Codec: {codec}, Bitrate: {bitrate_display}, Rate: {sample_rate} Hz, Channels: {channels} ({layout})"
                            + (f" <i>({desc})</i>" if desc else ""))
                        tech_info_html.append(audio_line)

                    messages.add_file_tech_info(material_name, tech_info_html)
                except Exception as e:
                    messages.add_file_warning(material_name, f"Could not generate report detail: {e}")

    def _get_file_hash(self, file_path: Path) -> str:
        path_str = str(file_path.resolve())
        
        try:
            mtime = os.path.getmtime(file_path)
            size = os.path.getsize(file_path)
        except OSError as e:
            self.log(f"[ERROR] Could not read metadata for {file_path.name}: {e}")
            return ""

        if path_str in self.file_hash_cache:
            cached_data = self.file_hash_cache[path_str]
            if cached_data.get('mtime') == mtime and cached_data.get('size') == size:
                return cached_data.get('hash', '')

        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(1024 * 1024), b""):
                    if self._is_canceled: return ""
                    sha256_hash.update(byte_block)
            
            file_hash = sha256_hash.hexdigest()
            self.file_hash_cache[path_str] = {
                'hash': file_hash,
                'mtime': mtime,
                'size': size
            }
            return file_hash
        except (IOError, OSError) as e:
            self.log(f"[ERROR] Could not calculate hash for {file_path.name}: {e}")
            return ""

    def _check_ffmpeg_installation(self, messages: ValidationMessages):
        try:
            ffmpeg_path = get_ffmpeg_path()
            ffprobe_path = get_ffprobe_path()

            ffmpeg_version = self._get_tool_version(ffmpeg_path)
            ffprobe_version = self._get_tool_version(ffprobe_path)

            if ffmpeg_version != "Error" and ffprobe_version != "Error" and ffmpeg_version != ffprobe_version:
                msg = (
                    f"FFmpeg version ({ffmpeg_version}) and ffprobe version ({ffprobe_version}) do not match.<br><br>"
                    "<b>[Cause]</b><br>"
                    "The application found two different versions of FFmpeg tools on your system. Using mismatched versions can lead to unexpected errors during video processing.<br><br>"
                    "<b>[Action]</b><br>"
                    "It is highly recommended to install a matching pair of ffmpeg and ffprobe from the same official build to ensure stability."
                )
                messages.add_project_warning(msg)
        except FileNotFoundError as e:
            caps = load_capabilities()
            
            base_message = f"FFmpeg was not found. This application requires a matching pair of ffmpeg and ffprobe to function."
            
            user_folder_tip = (
                "<b>[Option 1: Manual Install]</b><br>"
                "Create a folder named 'ffmpeg-bin' in your user home directory and place both the ffmpeg and ffprobe executables inside it. The application will automatically detect them."
            )

            install_menu_tip = ""
            if caps.get("FFMPEG_INSTALL_MENU", True):
                install_menu_tip = "<b>[Option 2: Automatic Install]</b><br>Use the 'Tools -> Install FFmpeg...' menu to let the application attempt a system-wide installation for you."

            full_message = "<br><br>".join(filter(None, [base_message, user_folder_tip, install_menu_tip]))
            messages.add_project_error(full_message)

    def _check_pdf_file(self, project_model: ProjectModel, messages: ValidationMessages, pdf_path: Path) -> int:
        page_count = 0
        try:
            with fitz.open(pdf_path) as doc:
                page_count = doc.page_count
                self.log(f"[INFO] PDF '{pdf_path.name}' has {page_count} pages.")
                target_width, target_height = map(int, project_model.parameters.resolution.split('x'))
                resolution_aspect_ratio = target_width / target_height
                differing_pages = []
                for page_num in range(page_count):
                    if self._is_canceled: break
                    self.log(f"Checking PDF page {page_num + 1} of {page_count} for aspect ratio...", source='verbose_app')
                    page = doc.load_page(page_num)
                    if page.rect.height == 0:
                        messages.add_project_warning(f"PDF page {page_num + 1} has zero height and its aspect ratio cannot be checked.")
                        continue
                    if abs((page.rect.width / page.rect.height) - resolution_aspect_ratio) > config.PDF_ASPECT_RATIO_TOLERANCE:
                        differing_pages.append(page_num + 1)
                if differing_pages:
                    pages_str = ', '.join(map(str, differing_pages))
                    msg = (
                        f"The aspect ratio of the PDF does not match the video resolution (on pages: {pages_str}).<br><br>"
                        "<b>[Cause]</b><br>"
                        "The shape (width-to-height ratio) of one or more PDF pages is different from the shape of the selected video resolution (e.g., a square PDF with a widescreen video setting).<br><br>"
                        "<b>[Action]</b><br>"
                        "You can either:<br>"
                        "  • Go to the 'Basic' tab and change the 'Resolution' to one that matches the PDF's aspect ratio.<br>"
                        "  • Edit the original PDF document to match your desired video aspect ratio.<br><br>"
                        "<b>[Note]</b><br>"
                        "If you proceed, black bars (padding) will be automatically added to the sides or top/bottom of the PDF images to make them fit the video frame."
                    )
                    messages.add_project_warning(msg)
        except Exception as e:
            messages.add_project_error(f"Failed to open or process PDF file: {pdf_path.name}. Error: {e}")
        return page_count

    def _check_parameter_compatibility(self, params: "ProjectParameters", messages: ValidationMessages):
        if params.hardware_encoding == 'videotoolbox' and params.encoding_mode == config.ENCODING_MODES["QUALITY"]:
            msg = (
                "Incompatible settings: Apple VideoToolbox does not support Quality (CRF/CQP) mode.<br><br>"
                "<b>[Cause]</b><br>"
                "The selected hardware encoder ('Enabled (Apple Hardware)' on the 'Basic' tab) does not work with the 'Quality (CQP/CRF)' setting on the 'Video Options' tab.<br><br>"
                "<b>[Action]</b><br>"
                "In the 'Video Options' tab, please change the 'Encoding Mode' to either 'Bitrate (VBR)' or 'Bitrate (CBR)'."
            )
            messages.add_project_error(msg)

    def _validate_youtube_chapters(self, project_model: ProjectModel, messages: ValidationMessages):
        if not any(s.chapter_title for s in project_model.slides):
            msg = (
                "YouTube chapter export is enabled, but no chapters have been set.<br><br>"
                "<b>[Cause]</b><br>"
                "The 'Export chapter file for YouTube' option is checked in the 'Video Options' tab, but no chapter titles have been entered in the 'Slide Settings' tab.<br><br>"
                "<b>[Action]</b><br>"
                "Either add chapter titles to your slides or disable the export option."
            )
            messages.add_project_error(msg)
            return

        chapters = []
        current_time = 0.0
        for i, slide in enumerate(project_model.slides):
            if slide.chapter_title:
                chapters.append({'title': slide.chapter_title, 'start_time': current_time, 'slide_num': i + 1})
            
            current_time += slide.duration
            if i < len(project_model.slides) - 1:
                current_time += slide.interval_to_next

        if not chapters or chapters[0]['start_time'] != 0.0:
            msg = (
                "YouTube chapters must start from the beginning of the video (Slide 1).<br><br>"
                "<b>[Cause]</b><br>"
                "YouTube requires the first chapter to have a timestamp of 00:00. This means Slide 1 must have a chapter title.<br><br>"
                "<b>[Action]</b><br>"
                "In the 'Slide Settings' tab, please enter a title for the 'Chapter Title' field on the row for Slide 1."
            )
            messages.add_project_error(msg)

        if len(chapters) < 3:
            msg = (
                f"YouTube requires at least 3 chapters, but only {len(chapters)} were found.<br><br>"
                "<b>[Cause]</b><br>"
                "To be considered a valid chapter list, YouTube's policy requires a minimum of three chapter entries.<br><br>"
                "<b>[Action]</b><br>"
                "Please add more chapter titles in the 'Slide Settings' tab until you have at least three."
            )
            messages.add_project_error(msg)

        for i in range(len(chapters)):
            duration = (chapters[i+1]['start_time'] if i + 1 < len(chapters) else current_time) - chapters[i]['start_time']
            if duration < 10.0:
                chap_info = chapters[i]
                msg = (
                    f"The chapter '{chap_info['title']}' (on Slide {chap_info['slide_num']}) is only {duration:.1f} seconds long.<br><br>"
                    "<b>[Cause]</b><br>"
                    "YouTube requires each chapter to be a minimum of 10 seconds long.<br><br>"
                    "<b>[Action]</b><br>"
                    "To make this chapter longer, you can increase the 'Duration' or 'Interval to Next' of the slide(s) it contains, or merge it with an adjacent chapter by removing its title."
                )
                messages.add_project_error(msg)

    def _check_warnings_and_additional_conditions(self, project_model: ProjectModel, messages: ValidationMessages, pdf_path: Optional[Path]):
        params = project_model.parameters
        output_width, output_height = map(int, params.resolution.split('x'))
        dar_override_in_use = False
        
        slides_by_filename = {}
        for i, slide in enumerate(project_model.slides):
            if slide.filename and slide.is_video:
                if slide.filename not in slides_by_filename:
                    slides_by_filename[slide.filename] = []
                slides_by_filename[slide.filename].append((i, slide))

        for filename, slide_usages in slides_by_filename.items():
            first_slide = slide_usages[0][1]
            if first_slide.tech_info.get('is_vfr'):
                messages.add_file_warning(filename, "This is a Variable Frame Rate (VFR) video. It will be automatically converted to a constant frame rate to prevent sync issues, but please check the final output carefully.")
            if first_slide.tech_info.get('is_interlaced'):
                messages.add_file_notice(filename, "This video appears to be interlaced. It will be automatically deinterlaced for smooth playback.")
            if first_slide.tech_info.get('rotate') in ["90", "270"]:
                messages.add_file_notice(filename, "This is a vertical video and will be automatically rotated to the correct orientation.")
            
            w, h, dar_str = first_slide.tech_info.get('width', 0), first_slide.tech_info.get('height', 0), first_slide.tech_info.get('dar')
            if w > 0 and h > 0 and dar_str and ':' in dar_str and dar_str != '0:1':
                try:
                    num, den = map(int, dar_str.split(':'))
                    if den > 0 and abs((w / h) - (num / den)) > 1e-4: dar_override_in_use = True
                except (ValueError, TypeError): pass

            for slide_index, slide in slide_usages:
                pinp_geometry = calculate_pinp_geometry(slide, output_width, output_height)
                if not pinp_geometry: continue
                
                usage_warnings = []
                if slide.tech_info.get('fps', 0) > (params.fps * 1.1):
                    usage_warnings.append(f"Source FPS ({slide.tech_info.get('fps')}) is higher than the output FPS ({params.fps}). This may result in less smooth motion as frames will be dropped.")
                if pinp_geometry['height'] > slide.tech_info.get('height', 0):
                    usage_warnings.append(f"This video will be upscaled from {slide.tech_info.get('height', 0)}px to {round(pinp_geometry['height'])}px height, which may reduce its visual quality.")
                if pinp_geometry['width'] > output_width:
                    usage_warnings.append(f"This video will be scaled to {round(pinp_geometry['width'])}px width, which is wider than the output frame ({output_width}px).")
                
                base_image = self._render_pdf_page_for_preview(pdf_path, slide_index) if pdf_path else None
                base64_image = create_pinp_preview_for_report(base_image, slide, output_width, output_height)
                messages.add_file_usage_summary(filename, slide_index, pinp_geometry, slide, base64_image, usage_warnings)

        if dar_override_in_use:
            messages.add_project_notice("<i>Note: Some Picture-in-Picture previews may look stretched. This is to accurately reflect the Display Aspect Ratio (DAR/SAR) metadata from the source file, which prevents distortion in the final video.</i>")

        for idx, slide in enumerate(project_model.slides[:-1]):
            if self._is_canceled: break
            if slide.interval_to_next <= 0 and slide.transition_to_next != "None":
                msg = (
                    f"Slide {idx + 1} ('{slide.filename or 'SILENT'}') has a transition with zero interval.<br><br>"
                    "<b>[Cause]</b><br>"
                    "A transition effect (e.g., 'Fade') has been selected, but the time allocated for it ('Interval to Next') is 0 seconds.<br><br>"
                    "<b>[Action]</b><br>"
                    "In the 'Slide Settings' tab for this slide, either increase the 'Interval to Next' to a value greater than 0 (e.g., 1 second) or set the 'Transition to Next' back to 'None'."
                )
                messages.add_project_error(msg)
        
        if params.codec in ['H.265/HEVC', 'AV1'] and params.hardware_encoding is None:
            msg = (
                f"Encoding with the {params.codec} codec in software mode may be significantly slower than H.264.<br><br>"
                "<b>[Note]</b><br>"
                "This is not an error. It's an advisory that video creation may take longer. Using hardware encoding, if available, can speed this up."
            )
            messages.add_project_notice(msg)

def _get_media_info(self, media_path: Path):
        try:
            cmd = [
                str(get_ffprobe_path()),
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-show_format',
                str(media_path)
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=config.SUBPROCESS_CREATION_FLAGS,
                timeout=config.FFPROBE_TIMEOUT_S
            )

            if not result.stdout:
                raise ValueError(f"ffprobe returned no output for '{media_path.name}'. Stderr: {result.stderr.strip()}")

            if result.returncode != 0:
                raise ValueError(f"ffprobe failed for '{media_path.name}'. Stderr: {result.stderr.strip()}")
            
            data = json.loads(result.stdout)
            format_data = data.get('format', {})
            
            duration_str = format_data.get('duration')
            try:
                duration = float(duration_str)
            except (ValueError, TypeError):
                duration = 0.0
            
            overall_bitrate_bps_str = format_data.get('bit_rate')

            video_info = {}
            audio_streams = []
            first_video_found = False
            
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video' and not first_video_found:
                    first_video_found = True

                    r_frame_rate_str = stream.get('r_frame_rate', '0/1')
                    avg_frame_rate_str = stream.get('avg_frame_rate', '0/1')

                    try:
                        num_r, den_r = map(float, r_frame_rate_str.split('/'))
                        r_fps = num_r / den_r if den_r else 0.0
                    except (ValueError, ZeroDivisionError):
                        r_fps = 0.0
                    
                    try:
                        num_avg, den_avg = map(float, avg_frame_rate_str.split('/'))
                        avg_fps = num_avg / den_avg if den_avg else 0.0
                    except (ValueError, ZeroDivisionError):
                        avg_fps = 0.0

                    is_vfr = abs(r_fps - avg_fps) > 0.01

                    field_order = stream.get('field_order')
                    is_interlaced = field_order in ['tt', 'bb', 'tb', 'bt']

                    rotation = None
                    if 'side_data_list' in stream:
                        for side_data in stream['side_data_list']:
                            if 'rotation' in side_data:
                                rotation = str(side_data['rotation']).split('.')[0]
                                break
                    
                    if rotation is None:
                        stream_tags = stream.get('tags', {})
                        if stream_tags and 'rotate' in stream_tags:
                            rotation = str(stream_tags.get('rotate'))

                    bitrate_str = stream.get('bit_rate', '0')
                    video_bitrate = 0
                    try:
                        video_bitrate = int(float(bitrate_str) / 1000)
                    except (ValueError, TypeError):
                        pass

                    video_info = {
                        'width': int(stream.get('width', 0)),
                        'height': int(stream.get('height', 0)),
                        'fps': round(avg_fps, 2),
                        'bitrate': video_bitrate,
                        'codec': stream.get('codec_name', ''),
                        'dar': stream.get('display_aspect_ratio'),
                        'is_vfr': is_vfr,
                        'is_interlaced': is_interlaced,
                        'rotate': rotation,
                    }

                elif stream.get('codec_type') == 'audio':
                    tags = stream.get('tags', {})
                    lang = tags.get('language', 'unk') if tags else 'unk'
                    title = tags.get('title', '') if tags else ''
                    codec_name = stream.get('codec_name', 'N/A')
                    bitrate_val = 0

                    if codec_name.startswith('pcm'):
                        bitrate_val = 'Uncompressed (PCM)'
                    elif codec_name == 'flac':
                        bitrate_val = 'Lossless (FLAC)'
                    elif codec_name == 'alac':
                         bitrate_val = 'Lossless (ALAC)'
                    else:
                        stream_bitrate_str = stream.get('bit_rate')
                        bitrate_to_parse = stream_bitrate_str or overall_bitrate_bps_str or '0'
                        try:
                            bitrate_val = int(float(bitrate_to_parse) / 1000)
                        except (ValueError, TypeError):
                            bitrate_val = 0
                    
                    audio_streams.append({
                        'index': int(stream.get('index')),
                        'codec': codec_name,
                        'bitrate': bitrate_val,
                        'sample_rate': stream.get('sample_rate', 'N/A'),
                        'channels': stream.get('channels', 'N/A'),
                        'channel_layout': stream.get('channel_layout', 'N/A'),
                        'language': lang,
                        'title': title,
                    })

            return duration, video_info, audio_streams
        except subprocess.TimeoutExpired:
            raise ValueError(f"ffprobe timed out while analyzing '{media_path.name}'. The file may be corrupt or on a slow network drive.")
        except (json.JSONDecodeError, IndexError, KeyError, ValueError) as e:
            if isinstance(e, ValueError) and ("ffprobe failed" in str(e) or "ffprobe returned no output" in str(e)):
                raise
            raise ValueError(f"Failed to parse ffprobe JSON output for '{media_path.name}'.")