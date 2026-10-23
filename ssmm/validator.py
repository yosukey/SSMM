# validator.py
import base64
import hashlib
import html
import io
import json
import re
import subprocess
import sys
import tempfile
import os
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QCoreApplication

import imagehash
from PIL import Image

from ssmm import config
from ssmm import pdf_utils
from ssmm.models import ProjectModel, ProjectParameters, Slide, ValidationMessages
from ssmm.media_utils import (find_project_pdfs, has_dar_override, is_quarter_turn,
                              normalize_rotation, slide_timeline)
from ssmm.ui_helpers import calculate_pinp_geometry, create_pinp_preview_for_report
from ssmm.utils import get_ffmpeg_path, get_ffprobe_path, get_ffmpeg_source

try:
    from capabilities import load_capabilities
except Exception:
    def load_capabilities():
        return {"EDITION": "B", "FFMPEG_INSTALL_MENU": True}

def check_encoder_functionality(encoder_name: str) -> tuple[bool, str]:
    """Run a tiny encode to confirm the encoder actually works on this machine.

    Returns (is_functional, detail). On failure, ``detail`` carries the reason
    (ffmpeg's stderr, or a timeout/launch note) so callers can surface *why* an
    encoder failed instead of a bare "not functional". For example, an AMD GPU
    without a HEVC encode block reports
    ``CreateComponent(AMFVideoEncoderHW_HEVC) failed with error 30`` here.
    """
    try:
        ffmpeg_path = str(get_ffmpeg_path())
    except FileNotFoundError:
        return False, "ffmpeg executable not found."

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
        if result.returncode == 0:
            return True, ""
        detail = (result.stderr or "").strip() or f"ffmpeg exited with code {result.returncode}."
        return False, detail
    except subprocess.TimeoutExpired:
        return False, f"Test timed out after {config.ENCODER_TEST_TIMEOUT_S}s."
    except Exception as e:
        return False, f"Could not run encoder test: {e}"

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
        file_hash = self.get_file_hash(material_path)
        if not file_hash:
            return

        if file_hash not in self.info_cache:
            has_video_ext = material_path.suffix.lower() in config.SUPPORTED_VIDEO_FORMATS
            duration, video_info, audio_streams = self._get_media_info(material_path)

            # Classify by the streams ffprobe actually found, not by the file extension.
            # A container with a video extension but no decodable video stream (e.g. an
            # audio-only .mp4/.mov) must be treated as audio, not as a PinP video —
            # otherwise its empty tech_info later triggers 'None > 0' TypeErrors and the
            # renderer tries to overlay a non-existent video stream.
            is_video = has_video_ext and bool(video_info)
            if is_video:
                tech_info = video_info
            elif audio_streams:
                tech_info = audio_streams[0]
            else:
                tech_info = {}

            self.info_cache[file_hash] = {
                'duration': duration,
                'tech_info': tech_info,
                'is_video': is_video,
                'audio_streams': audio_streams,
            }

        cached_data = self.info_cache[file_hash]
        slide.duration = cached_data['duration']
        slide.is_video = cached_data['is_video']
        # Always hand the slide its own copies of the mutable structures so an in-place
        # edit of one slide cannot corrupt the shared cache or any sibling slide.
        slide.tech_info = dict(cached_data['tech_info'])
        slide.audio_streams = [dict(s) for s in cached_data['audio_streams']]

    def _render_pdf_page_for_preview(self, pdf_path: Path, page_num: int) -> Optional[Image.Image]:
        doc = None
        try:
            doc = pdf_utils.open_pdf(pdf_path)
            if 0 <= page_num < pdf_utils.num_pages(doc):
                return pdf_utils.render_page_to_pil(doc, page_num)
        except Exception as e:
            self.log(f"[ERROR] Failed to render PDF page {page_num} for preview: {e}")
        finally:
            pdf_utils.close_pdf(doc)
        return None

    def clear_cache(self):
        self.info_cache.clear()
        self.file_hash_cache.clear()
        self.validated_pdf_path = None
        self.validated_pdf_structure = None
        self.validated_pdf_hash = None
        self.log("[INFO] All validator caches have been cleared.")

    def get_pdf_structure(self, pdf_path: Path) -> dict:
        # Not tied to the validation cancel flag: this is also called from the UI
        # thread outside of a validation run, where a stale flag from an earlier
        # cancel must not make the PDF look empty.
        structure = {'page_count': 0, 'page_dims': []}
        doc = None
        try:
            doc = pdf_utils.open_pdf(pdf_path)
            structure['page_count'] = pdf_utils.num_pages(doc)
            for i in range(structure['page_count']):
                structure['page_dims'].append(pdf_utils.page_size(doc, i))
            return structure
        except Exception as e:
            self.log(f"[ERROR] Failed to get PDF structure for {pdf_path.name}: {e}")
            return {}
        finally:
            pdf_utils.close_pdf(doc)

    def cache_pdf_structure(self, pdf_path: Path):
        try:
            self.validated_pdf_path = pdf_path
            self.validated_pdf_structure = self.get_pdf_structure(pdf_path)
            page_count = self.validated_pdf_structure.get('page_count', 0)
            self.log(f"[INFO] PDF structure cache updated for: {pdf_path.name} ({page_count} pages)")
        except Exception as e:
            self.log(f"[ERROR] Failed to cache PDF structure: {e}")
    
    @staticmethod
    def _page_hash_and_thumbnail(doc, page_index: int) -> tuple[str, str]:
        # One render feeds both the perceptual hash and the Base64 PNG thumbnail.
        pil_image = pdf_utils.render_page_to_pil(doc, page_index, scale=config.PDF_DETAIL_RENDER_SCALE)
        p_hash = str(imagehash.phash(pil_image))
        target_h = config.PDF_THUMBNAIL_HEIGHT
        thumbnail_image = pil_image.copy()
        thumbnail_image.thumbnail((max(1, int(target_h * pil_image.width / pil_image.height)), target_h),
                                  Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        thumbnail_image.save(buffer, format="PNG")
        return p_hash, base64.b64encode(buffer.getvalue()).decode('utf-8')

    def compute_and_populate_pdf_details(self, project_model: ProjectModel):
        self.log("[INFO] Computing p-hash and thumbnails for PDF pages...")
        if not project_model.project_folder:
            self.log("[WARNING] No project folder set, cannot compute PDF details.")
            return

        pdf_files = find_project_pdfs(project_model.project_folder)
        pdf_path = pdf_files[0] if pdf_files else None
        if not pdf_path:
            self.log("[WARNING] No PDF found, cannot compute page details.")
            return

        doc = None
        try:
            doc = pdf_utils.open_pdf(pdf_path)
            pdf_page_count = pdf_utils.num_pages(doc)
            if len(project_model.slides) != pdf_page_count:
                self.log(f"[ERROR] Slide count ({len(project_model.slides)}) mismatches PDF page count ({pdf_page_count}). Aborting detail computation.")
                return

            for i, slide in enumerate(project_model.slides):
                slide.p_hash, slide.thumbnail_b64 = self._page_hash_and_thumbnail(doc, i)

            self.log("[INFO] Successfully computed and populated PDF details.")
        except Exception as e:
            self.log(f"[ERROR] Failed during PDF detail computation: {e}")
        finally:
            pdf_utils.close_pdf(doc)

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
            doc = pdf_utils.open_pdf(pdf_path)
            pdf_page_count = pdf_utils.num_pages(doc)
            details["page_count"] = pdf_page_count

            for i in range(pdf_page_count):
                p_hash, thumbnail_b64 = self._page_hash_and_thumbnail(doc, i)
                details["p_hashes"].append(p_hash)
                details["thumbnails_b64"].append(thumbnail_b64)

            return details, None
        except Exception as e:
            error_msg = f"Could not get details for PDF {pdf_path.name}: {e}"
            self.log(f"[ERROR] {error_msg}")
            return None, error_msg
        finally:
            pdf_utils.close_pdf(doc)

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
        # Clears a cancel request left over from a previous run. Called at the start
        # of every validation/project-setup run so a stale flag cannot leak into it.
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

            # Restrict the captured name to valid encoder-name characters so the
            # legend lines in ffmpeg's header (e.g. " V..... = Video") do not add the
            # junk token "=" to the set.
            encoder_pattern = re.compile(r"^\s*[VAS.FXBD-]+\s+([A-Za-z0-9_-]+)")
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
                is_functional, detail = check_encoder_functionality(sw_encoder)
                if is_functional:
                    log_messages.append(f"  -> [SUCCESS] '{sw_encoder}' is functional.")
                    for codec, encoders in config.SUPPORTED_CODEC_CHECKS.items():
                        if sw_encoder in encoders:
                            if codec not in functional_map: functional_map[codec] = []
                            functional_map[codec].append(sw_encoder)
                            break
                else:
                    log_messages.append(f"  -> [FAILED] '{sw_encoder}' is not functional.")
                    if detail:
                        log_messages.append(f"       Reason: {detail}")
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
                    is_functional, detail = check_encoder_functionality(hw_encoder)
                    if is_functional:
                        log_messages.append(f"  -> [SUCCESS] '{hw_encoder}' is functional.")
                        if codec not in functional_map:
                            functional_map[codec] = []
                        functional_map[codec].append(hw_encoder)
                    else:
                        log_messages.append(f"  -> [FAILED] '{hw_encoder}' is not functional.")
                        if detail:
                            log_messages.append(f"       Reason: {detail}")
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
        # If FFmpeg was found but no functional encoder exists, surface that the build is unusable.
        if not messages.has_errors():
            self._check_functional_encoders(messages, available_encoders)

        if self._is_canceled: return messages, 0, file_hashes_snapshot
        self.log("[INFO] --- Phase 2/4: Analyzing PDF file ---")

        page_count = 0
        # Sort so the chosen PDF is deterministic regardless of filesystem order, and
        # flag the presence of extra PDFs (a second PDF added after loading would
        # otherwise let validation, thumbnails and rendering diverge on which file to use).
        pdf_files = find_project_pdfs(project_model.project_folder)
        pdf_path = pdf_files[0] if pdf_files else None

        if len(pdf_files) > 1:
            messages.add_project_error(QCoreApplication.translate("ProjectValidator",
                "Multiple PDF files were found in the project folder.<br><br>"
                "<b>[Cause]</b><br>"
                "The application requires exactly one PDF file to serve as the base for the slideshow.<br><br>"
                "<b>[Action]</b><br>"
                "Please remove the extra PDF file(s) so that only one remains, then validate again."))

        if not pdf_path:
            msg = QCoreApplication.translate("ProjectValidator",
                "No PDF file found in the project folder.<br><br>"
                "<b>[Cause]</b><br>"
                "The application requires exactly one PDF file in the selected project folder to serve as the base for the slideshow.<br><br>"
                "<b>[Action]</b><br>"
                "Please add a single PDF file to your project folder and try again."
            )
            messages.add_project_error(msg)
        else:
            current_pdf_hash = self.get_file_hash(pdf_path)
            current_pdf_structure = self.get_pdf_structure(pdf_path)
 
            # Condition 1: PDF unchanged (hash matches).
            if self.validated_pdf_hash and self.validated_pdf_hash == current_pdf_hash:
                self.log("[INFO] PDF file has not changed. Skipping detailed PDF analysis.")
                # The aspect-ratio check compares the PDF against the selected output
                # resolution, which the user may have changed since the last validation.
                # Run it even on the unchanged-PDF fast path so the "black bars will be
                # added" warning is not silently suppressed after a resolution change.
                self._check_pdf_file(project_model, messages, pdf_path)
                page_count = len(project_model.slides)
            
            # Condition 2: Minor change (hash differs, but page count is the same).
            elif self.validated_pdf_hash and current_pdf_hash != self.validated_pdf_hash and len(project_model.slides) == current_pdf_structure.get('page_count', -1):
                self.log("[INFO] PDF content changed without altering page structure. Updating thumbnails automatically.")
                self.compute_and_populate_pdf_details(project_model)
                msg = QCoreApplication.translate("ProjectValidator",
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
            msg = QCoreApplication.translate("ProjectValidator",
                "Material has not been assigned for slide(s): {0}.<br><br>"
                "<b>[Cause]</b><br>"
                "Every slide in the timeline must be assigned a video, an audio file, or explicitly marked as 'SILENT'.<br><br>"
                "<b>[Action]</b><br>"
                "In the 'Slide Settings' tab, go to the specified slide row(s) and select a material from the dropdown menu. If a slide should only show the PDF page with no sound, select '{1}'."
            ).format(', '.join(map(str, unassigned_slides)), config.SILENT_MATERIAL_NAME)
            messages.add_project_error(msg)
        
        if project_model.project_folder:
            file_hashes_snapshot, unreadable = self.snapshot_project_files(project_model.project_folder)
            for name in unreadable:
                messages.add_project_warning(QCoreApplication.translate("ProjectValidator", "Could not create a hash for file {0}; it may be unreadable.").format(html.escape(name)))

        if self._is_canceled: return messages, page_count, file_hashes_snapshot

        if not messages.has_errors():
            self.log("[INFO] --- Phase 4/4: Checking parameter compatibility ---")
            self._validate_output_filename(project_model, messages)
            if self._is_canceled: return messages, page_count, file_hashes_snapshot
            self._check_hardware_encoder(project_model.parameters, messages, available_encoders)
            if self._is_canceled: return messages, page_count, file_hashes_snapshot
            
            self._check_parameter_compatibility(project_model.parameters, messages)
            self._check_intermediate_container_support(project_model.parameters, messages)
            if self._is_canceled: return messages, page_count, file_hashes_snapshot
            if project_model.parameters.export_youtube_chapters:
                self._validate_youtube_chapters(project_model, messages)
        
        if self._is_canceled: return messages, page_count, file_hashes_snapshot
        
        if not messages.has_errors() and pdf_path:
            self.cache_pdf_structure(pdf_path)
            self.validated_pdf_hash = self.get_file_hash(pdf_path)
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
                msg = QCoreApplication.translate("ProjectValidator",
                    "The output filename is empty.<br><br>"
                    "<b>[Cause]</b><br>"
                    "An output filename is required. No project folder is set to use as a default name.<br><br>"
                    "<b>[Action]</b><br>"
                    "Please enter a name in the 'Filename' input box under Step 5."
                )
                messages.add_project_error(msg)
                return

        if not effective_filename:
            messages.add_project_error(QCoreApplication.translate("ProjectValidator", "The effective output filename is empty."))
            return

        control_chars_pattern = r'[\x00-\x1f\x7f]'
        if re.search(control_chars_pattern, effective_filename):
            messages.add_project_error(QCoreApplication.translate("ProjectValidator", "The filename contains invisible control characters, which are not allowed."))
            return

        for char in config.FILENAME_ILLEGAL_CHARS:
            if char in effective_filename:
                msg = QCoreApplication.translate("ProjectValidator",
                    "The filename contains an illegal character: '{0}'<br><br>"
                    "<b>[Cause]</b><br>"
                    "Operating systems do not allow the characters '{1}' in filenames.<br><br>"
                    "<b>[Action]</b><br>"
                    "Please remove the '{2}' character from the 'Filename' input box."
                ).format(char, config.FILENAME_ILLEGAL_CHARS, char)
                messages.add_project_error(msg)
                return
        
        if len(effective_filename.encode('utf-8')) > config.FILENAME_MAX_LENGTH:
            msg = QCoreApplication.translate("ProjectValidator",
                "The filename is too long (max {0} bytes).<br><br>"
                "<b>[Action]</b><br>"
                "Please shorten the name in the 'Filename' input box."
            ).format(config.FILENAME_MAX_LENGTH)
            messages.add_project_error(msg)
            return

        filename_base = effective_filename.split('.')[0]
        if filename_base.upper() in config.FILENAME_RESERVED_NAMES:
            msg = QCoreApplication.translate("ProjectValidator",
                "The filename '{0}' is a reserved system name.<br><br>"
                "<b>[Action]</b><br>"
                "Please choose a different name in the 'Filename' input box."
            ).format(filename_base)
            messages.add_project_error(msg)
            return

        if effective_filename.endswith('.') or effective_filename.endswith(' '):
            messages.add_project_error(QCoreApplication.translate("ProjectValidator", "The filename cannot end with a period or a space."))
            return

    def build_encoder_status_messages(self, available_encoders: dict) -> ValidationMessages:
        """Build a ValidationMessages containing only the FFmpeg/encoder summary
        (source, version and the available-encoder matrix).

        This reuses the exact summary the full validation report shows, so it can
        be rendered in the 'Validation Result' tab as a preview right after the
        startup encoder check — before any project validation has run.
        """
        messages = ValidationMessages()
        self._add_encoder_summary_notice(messages, available_encoders)
        return messages

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
            notice_html = [f"<b>FFmpeg Path:</b> {html.escape(ffmpeg_bin)}<br>"]
        if ver_line:
            notice_html.append(f"<b>Version:</b> {html.escape(ver_line)}<br><br>")
        else:
            notice_html.append("<br>")
        
        if available_encoders:
            notice_html.append(QCoreApplication.translate("ProjectValidator", "<b>Available Encoders (functional test passed):</b>"))
            
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
                messages.add_project_error(QCoreApplication.translate("ProjectValidator", "Configuration error: No mapping found for codec '{0}' and hardware '{1}'.").format(selected_codec, hardware_encoder_name))
                return

            functional_encoders_for_codec = available_encoders.get(selected_codec, [])
            
            if target_encoder not in functional_encoders_for_codec:
                msg = QCoreApplication.translate("ProjectValidator",
                    "The selected hardware encoder '{0}' is not functional.<br><br>"
                    "<b>[Cause]</b><br>"
                    "The application ran a quick test on the selected hardware encoder, and it failed to initialize. This is often caused by missing or outdated graphics drivers, or an unsupported hardware/FFmpeg combination.<br><br>"
                    "<b>[Action]</b><br>"
                    "  • Ensure your graphics drivers (NVIDIA, Intel, AMD) are up to date.<br>"
                    "  • In the 'Basic' tab, select 'None' for 'Hardware Encoding' to use reliable software encoding.<br>"
                    "  • Alternatively, try selecting a different Codec."
                ).format(target_encoder)
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
                # Keep the assignment so it is not lost if the file is restored; the
                # error already blocks rendering.
                messages.add_project_error(QCoreApplication.translate("ProjectValidator", "Material file '{0}' not found in project folder.").format(html.escape(slide.filename)))
                continue

            try:
                # Use the public method (handles caching)
                self.analyze_material(mf_path, slide)
            except Exception as e:
                messages.add_project_error(QCoreApplication.translate("ProjectValidator", "Failed to process file '{0}': {1}").format(html.escape(slide.filename), html.escape(str(e))))
                continue
        
        if project_model.project_folder:
            for material_name in sorted(project_model.available_materials):
                if self._is_canceled: return
                mf_path = project_model.project_folder / material_name
                if not mf_path.exists(): continue

                try:
                    # Use the public method (handles caching)
                    self.analyze_material(mf_path, Slide())
                    
                    file_hash = self.get_file_hash(mf_path)
                    if not file_hash or file_hash not in self.info_cache:
                        messages.add_file_warning(material_name, QCoreApplication.translate("ProjectValidator", "Could not retrieve cached info for this file. It might be unreadable."))
                        continue

                    cached_data = self.info_cache[file_hash]
                    is_video = cached_data['is_video']
                    tech_info = cached_data['tech_info']
                    
                    tech_info_html = []
                    
                    if material_name in used_materials:
                        status_html = "<span class='status used'>✔ IN USE</span>"
                    else:
                        status_html = "<span class='status unused'>UNUSED</span>"

                    # Lead with the filename so the list reads as a list of files;
                    # the in-use/unused badge is a right-aligned attribute of each row.
                    # QTextEdit ignores flexbox/auto-margins, so the split alignment
                    # is done with a full-width table (align='right' on the status cell).
                    type_label = 'Audio' if not is_video else 'PinP Video'
                    name_html = f"<b>{html.escape(material_name)}</b> ({type_label})"
                    header = (
                        "<table class='file-title-table' width='100%'><tr>"
                        f"<td class='file-title-name'>{name_html}</td>"
                        f"<td class='file-title-status' align='right'>{status_html}</td>"
                        "</tr></table>"
                    )
                    tech_info_html = [header]
                    if is_video:
                        w, h, codec, bitrate, fps, dar_str = tech_info.get('width'), tech_info.get('height'), tech_info.get('codec'), tech_info.get('bitrate'), tech_info.get('fps'), tech_info.get('dar')
                        rotation = tech_info.get('rotate')
                        dar_override_note, rotation_note = "", ""
                        if has_dar_override(tech_info):
                            dar_override_note = f" <b>(DAR override: {html.escape(str(dar_str))})</b>"
                        if rotation:
                            rotation_note = f" <b>(Rotation: {html.escape(str(rotation))}°)</b>"
                        tech_info_html.append(f"&nbsp;&nbsp;&nbsp;Video: {w}x{h}{dar_override_note}{rotation_note}, Codec: {html.escape(str(codec))}, Bitrate: {bitrate} kbps, FPS: {fps}")
                    audio_streams_info = cached_data.get('audio_streams', [])
                    if not audio_streams_info and not is_video:
                        tech_info_html.append("&nbsp;&nbsp;&nbsp;" + QCoreApplication.translate("ProjectValidator", "No audio stream found."))
                    
                    for i, audio_info in enumerate(audio_streams_info):
                        codec = html.escape(str(audio_info.get('codec', 'N/A')))
                        bitrate = audio_info.get('bitrate', 0)
                        sample_rate = html.escape(str(audio_info.get('sample_rate', 'N/A')))
                        channels = html.escape(str(audio_info.get('channels', '?')))
                        layout = html.escape(str(audio_info.get('channel_layout', 'N/A')))
                        lang = html.escape(str(audio_info.get('language', 'unk')))
                        title = html.escape(str(audio_info.get('title', '')))

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
                    messages.add_file_warning(material_name, QCoreApplication.translate("ProjectValidator", "Could not generate report detail: {0}").format(html.escape(str(e))))

    def get_file_hash(self, file_path: Path) -> str:
        """Content fingerprint of a file, cached by (path, mtime, size).

        Files up to config.FULL_HASH_MAX_BYTES get a full SHA-256. Larger files
        (typically videos) are fingerprinted from their size plus head, middle and
        tail blocks, which detects replaced or re-encoded media while keeping the
        first probe of a multi-GB file fast enough for the UI thread. Returns ""
        when the file cannot be read.
        """
        path_str = str(file_path.resolve())

        try:
            stat = os.stat(file_path)
        except OSError as e:
            self.log(f"[ERROR] Could not read metadata for {file_path.name}: {e}")
            return ""
        mtime, size = stat.st_mtime_ns, stat.st_size

        cached_data = self.file_hash_cache.get(path_str)
        if cached_data and cached_data.get('mtime') == mtime and cached_data.get('size') == size:
            return cached_data.get('hash', '')

        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                if size <= config.FULL_HASH_MAX_BYTES:
                    for byte_block in iter(lambda: f.read(1024 * 1024), b""):
                        sha256_hash.update(byte_block)
                else:
                    block = config.SAMPLED_HASH_BLOCK_BYTES
                    sha256_hash.update(f"sampled:{size}".encode("ascii"))
                    for offset in (0, (size - block) // 2, size - block):
                        f.seek(offset)
                        sha256_hash.update(f.read(block))
            file_hash = sha256_hash.hexdigest()
        except (IOError, OSError) as e:
            self.log(f"[ERROR] Could not calculate hash for {file_path.name}: {e}")
            return ""

        self.file_hash_cache[path_str] = {'hash': file_hash, 'mtime': mtime, 'size': size}
        return file_hash

    def snapshot_project_files(self, folder: Optional[Path]) -> tuple[dict, list[str]]:
        """Fingerprints of the PDF and media files directly inside ``folder``.

        Returns (snapshot, unreadable_names). Validation stores the snapshot and the
        main window compares a fresh one before rendering, so both must come from
        this single helper.
        """
        snapshot, unreadable = {}, []
        if not folder or not Path(folder).is_dir():
            return snapshot, unreadable
        all_formats = config.SUPPORTED_FORMATS + ('.pdf',)
        for entry in sorted(Path(folder).iterdir()):
            if entry.is_file() and entry.suffix.lower() in all_formats:
                file_hash = self.get_file_hash(entry)
                if file_hash:
                    snapshot[entry.name] = file_hash
                else:
                    unreadable.append(entry.name)
        return snapshot, unreadable

    def _check_ffmpeg_installation(self, messages: ValidationMessages):
        try:
            ffmpeg_path = get_ffmpeg_path()
            ffprobe_path = get_ffprobe_path()

            ffmpeg_version = self._get_tool_version(ffmpeg_path)
            ffprobe_version = self._get_tool_version(ffprobe_path)

            if ffmpeg_version != "Error" and ffprobe_version != "Error" and ffmpeg_version != ffprobe_version:
                msg = QCoreApplication.translate("ProjectValidator",
                    "FFmpeg version ({0}) and ffprobe version ({1}) do not match.<br><br>"
                    "<b>[Cause]</b><br>"
                    "The application found two different versions of FFmpeg tools on your system. Using mismatched versions can lead to unexpected errors during video processing.<br><br>"
                    "<b>[Action]</b><br>"
                    "It is highly recommended to install a matching pair of ffmpeg and ffprobe from the same official build to ensure stability."
                ).format(ffmpeg_version, ffprobe_version)
                messages.add_project_warning(msg)
        except FileNotFoundError:
            caps = load_capabilities()
            
            base_message = QCoreApplication.translate("ProjectValidator", "FFmpeg was not found. This application requires a matching pair of ffmpeg and ffprobe to function.")

            user_folder_tip = QCoreApplication.translate("ProjectValidator",
                "<b>[Option 1: Manual Install]</b><br>"
                "Create a folder named 'ffmpeg-bin' in your user home directory and place both the ffmpeg and ffprobe executables inside it. The application will automatically detect them."
            )

            install_menu_tip = ""
            if caps.get("FFMPEG_INSTALL_MENU", True):
                install_menu_tip = QCoreApplication.translate("ProjectValidator", "<b>[Option 2: Automatic Install]</b><br>Use the 'Tools -> Install FFmpeg...' menu to let the application attempt a system-wide installation for you.")

            full_message = "<br><br>".join(filter(None, [base_message, user_folder_tip, install_menu_tip]))
            messages.add_project_error(full_message)

    def _check_functional_encoders(self, messages: ValidationMessages, available_encoders: dict):
        # An empty map means no usable encoder was found for any codec, typically a broken or incompatible FFmpeg build.
        if available_encoders:
            return

        msg = QCoreApplication.translate("ProjectValidator",
            "FFmpeg was found, but no functional video encoder could be detected.<br><br>"
            "<b>[Cause]</b><br>"
            "The application tested the detected FFmpeg build, but none of the supported encoders "
            "(such as libx264 or libx265) could be initialized. This usually means the build is "
            "incomplete or incompatible -- for example, a wrong CPU architecture, a minimal build "
            "without encoders, or an executable that lacks run permission.<br><br>"
            "<b>[Action]</b><br>"
            "  • Install a full FFmpeg build that includes the standard encoders.<br>"
            "  • On macOS/Linux, ensure the ffmpeg and ffprobe files are executable.<br>"
            "  • If you placed binaries in '~/ffmpeg-bin', verify they run correctly, then restart the application."
        )
        messages.add_project_error(msg)

    def _check_pdf_file(self, project_model: ProjectModel, messages: ValidationMessages, pdf_path: Path) -> int:
        page_count = 0
        doc = None
        try:
            doc = pdf_utils.open_pdf(pdf_path)
            page_count = pdf_utils.num_pages(doc)
            self.log(f"[INFO] PDF '{pdf_path.name}' has {page_count} pages.")
            target_width, target_height = map(int, project_model.parameters.resolution.split('x'))
            resolution_aspect_ratio = target_width / target_height
            differing_pages = []
            for page_num in range(page_count):
                if self._is_canceled: break
                self.log(f"Checking PDF page {page_num + 1} of {page_count} for aspect ratio...", source='verbose_app')
                width, height = pdf_utils.page_size(doc, page_num)
                if height == 0:
                    messages.add_project_warning(QCoreApplication.translate("ProjectValidator", "PDF page {0} has zero height and its aspect ratio cannot be checked.").format(page_num + 1))
                    continue
                if abs((width / height) - resolution_aspect_ratio) > config.PDF_ASPECT_RATIO_TOLERANCE:
                    differing_pages.append(page_num + 1)
            if differing_pages:
                pages_str = ', '.join(map(str, differing_pages))
                msg = QCoreApplication.translate("ProjectValidator",
                    "The aspect ratio of the PDF does not match the video resolution (on pages: {0}).<br><br>"
                    "<b>[Cause]</b><br>"
                    "The shape (width-to-height ratio) of one or more PDF pages is different from the shape of the selected video resolution (e.g., a square PDF with a widescreen video setting).<br><br>"
                    "<b>[Action]</b><br>"
                    "You can either:<br>"
                    "  • Go to the 'Basic' tab and change the 'Resolution' to one that matches the PDF's aspect ratio.<br>"
                    "  • Edit the original PDF document to match your desired video aspect ratio.<br><br>"
                    "<b>[Note]</b><br>"
                    "If you proceed, black bars (padding) will be automatically added to the sides or top/bottom of the PDF images to make them fit the video frame."
                ).format(pages_str)
                messages.add_project_warning(msg)
        except Exception as e:
            messages.add_project_error(QCoreApplication.translate("ProjectValidator", "Failed to open or process PDF file: {0}. Error: {1}").format(html.escape(pdf_path.name), html.escape(str(e))))
        finally:
            pdf_utils.close_pdf(doc)
        return page_count

    def _check_parameter_compatibility(self, params: "ProjectParameters", messages: ValidationMessages):
        if params.hardware_encoding == 'videotoolbox' and params.encoding_mode == "QUALITY":
            msg = QCoreApplication.translate("ProjectValidator",
                "Incompatible settings: Apple VideoToolbox does not support Quality (CRF/CQP) mode.<br><br>"
                "<b>[Cause]</b><br>"
                "The selected hardware encoder ('Enabled (Apple Hardware)' on the 'Basic' tab) does not work with the 'Quality (CQP/CRF)' setting on the 'Video Options' tab.<br><br>"
                "<b>[Action]</b><br>"
                "In the 'Video Options' tab, please change the 'Encoding Mode' to either 'Bitrate (VBR)' or 'Bitrate (CBR)'."
            )
            messages.add_project_error(msg)

        encoder = self._resolve_encoder(params)
        if (params.encoding_pass == "TWO_PASS" and params.encoding_mode != "QUALITY" and encoder
                and encoder not in config.TWO_PASS_ENCODERS and encoder not in config.MULTIPASS_ENCODERS):
            messages.add_project_notice(QCoreApplication.translate("ProjectValidator",
                "The selected encoder '{0}' does not support 2-pass encoding; it will encode in a single pass.").format(html.escape(encoder)))

    @staticmethod
    def _resolve_encoder(params: "ProjectParameters") -> Optional[str]:
        if params.hardware_encoding:
            return config.CODEC_MAP.get(params.codec, {}).get(params.hardware_encoding)
        return config.SOFTWARE_CODEC_MAP.get(params.codec)

    def _check_intermediate_container_support(self, params: "ProjectParameters", messages: ValidationMessages):
        # AV1 segments are written as MP4 with PCM audio ('ipcm'), which older FFmpeg
        # builds (before 6.0) cannot mux. Test it up front so the render does not fail
        # on its first segment.
        encoder = self._resolve_encoder(params) or ""
        if 'av1' not in encoder:
            return
        try:
            ffmpeg_path = str(get_ffmpeg_path())
        except FileNotFoundError:
            return
        with tempfile.TemporaryDirectory(prefix="ssmm-probe-") as tmp:
            probe = Path(tmp) / f"probe{config.INTERMEDIATE_CONTAINER_AV1}"
            try:
                result = subprocess.run(
                    [ffmpeg_path, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                     '-t', '0.1', '-c:a', config.INTERMEDIATE_AUDIO_CODEC, str(probe)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=config.ENCODER_TEST_TIMEOUT_S, creationflags=config.SUBPROCESS_CREATION_FLAGS)
                supported = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                supported = False
        if not supported:
            messages.add_project_error(QCoreApplication.translate("ProjectValidator",
                "The installed FFmpeg cannot write the intermediate files needed for AV1.<br><br>"
                "<b>[Cause]</b><br>"
                "AV1 segments are stored as MP4 with uncompressed audio, which requires FFmpeg 6.0 or later.<br><br>"
                "<b>[Action]</b><br>"
                "Update FFmpeg, or select a different codec in the 'Basic' tab."))

    def _validate_youtube_chapters(self, project_model: ProjectModel, messages: ValidationMessages):
        if not any(s.chapter_title for s in project_model.slides):
            msg = QCoreApplication.translate("ProjectValidator",
                "YouTube chapter export is enabled, but no chapters have been set.<br><br>"
                "<b>[Cause]</b><br>"
                "The 'Export chapter file for YouTube' option is checked in the 'Video Options' tab, but no chapter titles have been entered in the 'Slide Settings' tab.<br><br>"
                "<b>[Action]</b><br>"
                "Either add chapter titles to your slides or disable the export option."
            )
            messages.add_project_error(msg)
            return

        # Same timeline as the renderer, so the 10-second minimum is checked against
        # the actually-rendered (frame-quantized) chapter lengths.
        start_times, current_time = slide_timeline(project_model.slides, project_model.parameters.fps)
        chapters = [{'title': slide.chapter_title, 'start_time': start_times[i], 'slide_num': i + 1}
                    for i, slide in enumerate(project_model.slides) if slide.chapter_title]

        if not chapters or chapters[0]['start_time'] != 0.0:
            msg = QCoreApplication.translate("ProjectValidator",
                "YouTube chapters must start from the beginning of the video (Slide 1).<br><br>"
                "<b>[Cause]</b><br>"
                "YouTube requires the first chapter to have a timestamp of 00:00. This means Slide 1 must have a chapter title.<br><br>"
                "<b>[Action]</b><br>"
                "In the 'Slide Settings' tab, please enter a title for the 'Chapter Title' field on the row for Slide 1."
            )
            messages.add_project_error(msg)

        if len(chapters) < 3:
            msg = QCoreApplication.translate("ProjectValidator",
                "YouTube requires at least 3 chapters, but only {0} were found.<br><br>"
                "<b>[Cause]</b><br>"
                "To be considered a valid chapter list, YouTube's policy requires a minimum of three chapter entries.<br><br>"
                "<b>[Action]</b><br>"
                "Please add more chapter titles in the 'Slide Settings' tab until you have at least three."
            ).format(len(chapters))
            messages.add_project_error(msg)

        for i in range(len(chapters)):
            duration = (chapters[i+1]['start_time'] if i + 1 < len(chapters) else current_time) - chapters[i]['start_time']
            if duration < 10.0:
                chap_info = chapters[i]
                msg = QCoreApplication.translate("ProjectValidator",
                    "The chapter '{0}' (on Slide {1}) is only {2:.1f} seconds long.<br><br>"
                    "<b>[Cause]</b><br>"
                    "YouTube requires each chapter to be a minimum of 10 seconds long.<br><br>"
                    "<b>[Action]</b><br>"
                    "To make this chapter longer, you can increase the 'Duration' or 'Interval to Next' of the slide(s) it contains, or merge it with an adjacent chapter by removing its title."
                ).format(html.escape(chap_info['title']), chap_info['slide_num'], duration)
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
                messages.add_file_warning(filename, QCoreApplication.translate("ProjectValidator", "This is a Variable Frame Rate (VFR) video. It will be automatically converted to a constant frame rate to prevent sync issues, but please check the final output carefully."))
            if first_slide.tech_info.get('is_interlaced'):
                messages.add_file_notice(filename, QCoreApplication.translate("ProjectValidator", "This video appears to be interlaced. It will be automatically deinterlaced for smooth playback."))
            if is_quarter_turn(first_slide.tech_info.get('rotate')):
                messages.add_file_notice(filename, QCoreApplication.translate("ProjectValidator", "This is a vertical video and will be automatically rotated to the correct orientation."))
            
            if has_dar_override(first_slide.tech_info):
                dar_override_in_use = True

            for slide_index, slide in slide_usages:
                pinp_geometry = calculate_pinp_geometry(slide, output_width, output_height)
                if not pinp_geometry: continue
                
                usage_warnings = []
                if slide.tech_info.get('fps', 0) > (params.fps * 1.1):
                    usage_warnings.append(QCoreApplication.translate("ProjectValidator", "Source FPS ({0}) is higher than the output FPS ({1}). This may result in less smooth motion as frames will be dropped.").format(slide.tech_info.get('fps'), params.fps))
                # For a 90/270-rotated video the effective source height is the coded
                # width, so compare against that to match how the frame is displayed.
                source_height = (slide.tech_info.get('width', 0)
                                 if is_quarter_turn(slide.tech_info.get('rotate'))
                                 else slide.tech_info.get('height', 0))
                if pinp_geometry['height'] > source_height:
                    usage_warnings.append(QCoreApplication.translate("ProjectValidator", "This video will be upscaled from {0}px to {1}px height, which may reduce its visual quality.").format(source_height, round(pinp_geometry['height'])))
                if pinp_geometry['width'] > output_width:
                    usage_warnings.append(QCoreApplication.translate("ProjectValidator", "This video will be scaled to {0}px width, which is wider than the output frame ({1}px).").format(round(pinp_geometry['width']), output_width))
                
                base_image = self._render_pdf_page_for_preview(pdf_path, slide_index) if pdf_path else None
                base64_image = create_pinp_preview_for_report(base_image, slide, output_width, output_height)
                messages.add_file_usage_summary(filename, slide_index, pinp_geometry, slide, base64_image, usage_warnings)

        if dar_override_in_use:
            messages.add_project_notice(QCoreApplication.translate("ProjectValidator", "<i>Note: Some Picture-in-Picture previews may look stretched. This is to accurately reflect the Display Aspect Ratio (DAR/SAR) metadata from the source file, which prevents distortion in the final video.</i>"))

        for idx, slide in enumerate(project_model.slides[:-1]):
            if self._is_canceled: break
            if slide.interval_to_next <= 0 and slide.transition_to_next != "None":
                msg = QCoreApplication.translate("ProjectValidator",
                    "Slide {0} ('{1}') has a transition with zero interval.<br><br>"
                    "<b>[Cause]</b><br>"
                    "A transition effect (e.g., 'Fade') has been selected, but the time allocated for it ('Interval to Next') is 0 seconds.<br><br>"
                    "<b>[Action]</b><br>"
                    "In the 'Slide Settings' tab for this slide, either increase the 'Interval to Next' to a value greater than 0 (e.g., 1 second) or set the 'Transition to Next' back to 'None'."
                ).format(idx + 1, html.escape(slide.filename or 'SILENT'))
                messages.add_project_error(msg)
        
        if params.codec in ['H.265/HEVC', 'AV1'] and params.hardware_encoding is None:
            msg = QCoreApplication.translate("ProjectValidator",
                "Encoding with the {0} codec in software mode may be significantly slower than H.264.<br><br>"
                "<b>[Note]</b><br>"
                "This is not an error. It's an advisory that video creation may take longer. Using hardware encoding, if available, can speed this up."
            ).format(params.codec)
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

            streams = data.get('streams', [])
            # The container bitrate is only a safe fallback for an audio stream when the
            # file has no video (otherwise it includes the video bitrate).
            has_video_stream = any(s.get('codec_type') == 'video' for s in streams)

            video_info = {}
            audio_streams = []
            first_video_found = False

            for stream in streams:
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

                    # Use a relative tolerance so standard NTSC rates are not misreported as VFR.
                    is_vfr = avg_fps > 0 and (abs(r_fps - avg_fps) / avg_fps) > 0.01

                    field_order = stream.get('field_order')
                    is_interlaced = field_order in ['tt', 'bb', 'tb', 'bt']

                    # Stored as the clockwise display rotation ("90"/"180"/"270" or
                    # None); see media_utils.normalize_rotation for the sign rules.
                    matrix_rotation = None
                    for side_data in stream.get('side_data_list', []) or []:
                        if 'rotation' in side_data:
                            matrix_rotation = side_data['rotation']
                            break
                    rotation = normalize_rotation(matrix_rotation, (stream.get('tags') or {}).get('rotate'))

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
                        container_fallback = overall_bitrate_bps_str if not has_video_stream else None
                        bitrate_to_parse = stream_bitrate_str or container_fallback or '0'
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
        except (json.JSONDecodeError, IndexError, KeyError, ValueError, TypeError) as e:
            if isinstance(e, ValueError) and ("ffprobe failed" in str(e) or "ffprobe returned no output" in str(e)):
                raise
            raise ValueError(f"Failed to parse ffprobe JSON output for '{media_path.name}'.")