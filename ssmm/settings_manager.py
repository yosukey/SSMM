# settings_manager.py
import os
import toml
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox
from PySide6.QtCore import QStandardPaths, QObject, Signal
import hashlib
from dataclasses import asdict, fields
import re

from ssmm.models import ProjectModel, ProjectParameters, Slide
from ssmm import config
from ssmm import dougameijin_importer
from ssmm import pdf_utils
from ssmm.media_utils import find_project_pdfs
from ssmm.project_init import scan_materials

# Option fields whose stored value is a key of the given config mapping. Settings
# files written by older versions stored the display label instead.
_KEYED_OPTION_FIELDS = {
    'encoding_mode': config.ENCODING_MODES,
    'encoding_pass': config.ENCODING_PASSES,
    'normalize_loudness_mode': config.LOUDNORM_MODES,
}
_BOOL_FIELDS = ('normalize_loudness', 'add_watermark', 'watermark_tile', 'export_youtube_chapters',
                'delete_temp_checkbox', 'append_duration_checkbox')
_STR_FIELDS = ('watermark_text', 'filename_input')
_HARDWARE_OPTIONS = {hw for hw_map in config.CODEC_MAP.values() for hw in hw_map}


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ('true', 'yes', '1', 'on'):
        return True
    if isinstance(value, str) and value.strip().lower() in ('false', 'no', '0', 'off', ''):
        return False
    raise ValueError(value)


def _clamped_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default

class SettingsFileParseError(ValueError):
    pass

class SettingsManager(QObject):
    log_message = Signal(str, str)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        # Destination chosen for the next DougaMeijin import, set on the main
        # thread and consumed by the background setup worker.
        self.pending_dmj_extract_dir = None

    def prompt_for_load_path(self) -> Path | None:
        if self.main_window.project_model.project_folder:
            start_dir = str(self.main_window.project_model.project_folder)
        else:
            start_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)

        default_path = Path(start_dir) / "settings.toml" if self.main_window.project_model.project_folder else None

        if default_path and default_path.exists():
            reply = QMessageBox.question(
                self.main_window, self.tr("Load Project"), self.tr("Load settings from the default location?\n\n({0})").format(default_path),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                return default_path
        
        dialog_path, _ = QFileDialog.getOpenFileName(
            self.main_window, self.tr("Load Project Settings File"), start_dir, self.tr("Project Settings (*.toml);;All Files (*)")
        )
        if dialog_path:
            return Path(dialog_path)

        return None

    def prompt_for_dmj_path(self) -> Path | None:
        if self.main_window.project_model.project_folder:
            start_dir = str(self.main_window.project_model.project_folder)
        else:
            start_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)

        dialog_path, _ = QFileDialog.getOpenFileName(
            self.main_window, self.tr("Import DougaMeijin Project File"), start_dir,
            self.tr("DougaMeijin Project (*.dmj);;All Files (*)")
        )
        if dialog_path:
            return Path(dialog_path)

        return None

    def prompt_for_dmj_extract_dir(self, dmj_path: Path) -> Path | None:
        dialog_path = QFileDialog.getExistingDirectory(
            self.main_window,
            self.tr("Select a Folder to Extract the DougaMeijin Project Into"),
            str(dmj_path.parent),
        )
        if dialog_path:
            return Path(dialog_path)

        return None

    def import_dougameijin_project(self, dmj_path: Path) -> ProjectModel | None:
        extract_dir = self.pending_dmj_extract_dir
        self.pending_dmj_extract_dir = None
        project_model = dougameijin_importer.import_project(
            dmj_path,
            extract_dir=extract_dir,
            log=lambda message, source='app': self.log_message.emit(message, source),
        )
        if project_model:
            self._coerce_and_validate_parameters(project_model.parameters)
        return project_model

    def load_from_file(self, file_path: Path, project_folder_override: Path | None = None) -> ProjectModel | None:
        self.log_message.emit(f"[INFO] Attempting to load project settings from: {file_path}", 'app')
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = toml.load(f)
        except (toml.TomlDecodeError, UnicodeDecodeError) as e:
            raise SettingsFileParseError(f"The settings file '{file_path.name}' is not a valid TOML file or is corrupt: {e}")

        # Hand-edited files may give any section or value the wrong shape; treat a
        # malformed section as absent instead of failing the whole load.
        def section(name, kind):
            value = data.get(name)
            return value if isinstance(value, kind) else kind()

        validation_info = section("validation_info", dict)
        cached_pdf_hash = validation_info.get("pdf_file_hash")
        if not isinstance(cached_pdf_hash, str):
            cached_pdf_hash = None
        if cached_pdf_hash:
            self.main_window.validator.validated_pdf_hash = cached_pdf_hash
            self.log_message.emit(f"[INFO] Loaded cached PDF hash: {cached_pdf_hash[:10]}...", 'app')

        loaded_params_dict = section("parameters", dict)

        stored_hash = loaded_params_dict.get('integrity_hash')
        if stored_hash:
            try:
                recomputed_hash = self._compute_integrity_hash(loaded_params_dict, data.get("slides", []))
                if recomputed_hash != stored_hash:
                    self.log_message.emit("[WARNING] Settings file integrity check failed; the file may have been manually edited or corrupted. Loading anyway.", 'app')
            except Exception as e:
                self.log_message.emit(f"[WARNING] Could not verify settings file integrity: {e}", 'app')

        paths = section("paths", dict)
        if project_folder_override is not None:
            project_dir = Path(project_folder_override)
            if not project_dir.is_dir():
                raise FileNotFoundError(f"Project folder '{project_dir}' could not be found.")
        else:
            project_path_str = str(file_path.parent) if paths.get("project_folder_is_self") is True else paths.get("project_folder")
            if not isinstance(project_path_str, str):
                project_path_str = None
            if not project_path_str or not Path(project_path_str).is_dir():
                 raise FileNotFoundError(f"Project folder '{project_path_str}' defined in settings.toml could not be found.")
            project_dir = Path(project_path_str)
        pdf_files = find_project_pdfs(project_dir)
        if not pdf_files:
            raise FileNotFoundError(f"No PDF file found in the project folder '{project_dir}'.")
        if len(pdf_files) > 1:
            raise ValueError(f"Multiple PDF files found in the project folder '{project_dir}'. Please ensure there is only one PDF.")

        pdf_path = pdf_files[0]

        loaded_slides_settings = [sl if isinstance(sl, dict) else {} for sl in section("slides", list)]
        toml_slide_count = len(loaded_slides_settings)

        try:
            pdf_page_count = pdf_utils.page_count(pdf_path)
        except Exception as e:
            raise ValueError(f"Failed to read the PDF file '{pdf_path.name}': {e}")

        if toml_slide_count != pdf_page_count:
            self.log_message.emit(f"[WARNING] Page count in TOML ({toml_slide_count}) differs from PDF ({pdf_page_count}). Migration will be required.", 'app')

        project_model = ProjectModel()
        project_model.project_folder = project_dir

        output_path_str = paths.get("output_folder")
        if isinstance(output_path_str, str) and output_path_str and Path(output_path_str).is_dir():
            project_model.output_folder = Path(output_path_str)

        project_model.parameters = ProjectParameters()
        for key, value in loaded_params_dict.items():
            if hasattr(project_model.parameters, key):
                setattr(project_model.parameters, key, value)
        self._coerce_and_validate_parameters(project_model.parameters)

        
        project_model.slides = [Slide() for _ in range(toml_slide_count)]
        
        project_model.available_materials = scan_materials(project_model.project_folder)

        for idx, slide_settings in enumerate(loaded_slides_settings):
            if idx >= len(project_model.slides):
                break

            slide = project_model.slides[idx]
            material = slide_settings.get("material")
            if not isinstance(material, str):
                material = None

            if material == config.SILENT_MATERIAL_NAME:
                slide.filename = config.SILENT_MATERIAL_NAME
                # Clamp to the range the duration spinbox accepts; a missing key or an
                # out-of-range hand-edited value (e.g. 0) would otherwise desync the
                # displayed duration (clamped to 1) from the model/rendered length.
                sd_low, sd_high = config.SILENT_DURATION_RANGE
                try:
                    raw_duration = float(slide_settings.get("duration", sd_low))
                except (TypeError, ValueError):
                    raw_duration = sd_low
                slide.duration = max(sd_low, min(sd_high, raw_duration))
            elif material in project_model.available_materials:
                slide.filename = material
            elif material:
                # The saved material is no longer present in the project folder; leave the
                # slide unassigned (validation will flag it) but tell the user why.
                self.log_message.emit(
                    f"[WARNING] Material '{material}' referenced by slide {idx + 1} was not found "
                    f"in the project folder; the slide will be left unassigned.", 'app')

            # Coerce hand-edited values into what the table widgets and the renderer accept.
            slide.chapter_title = str(slide_settings.get("chapter_title", "") or "")
            iv_low, iv_high = config.INTERVAL_RANGE
            slide.interval_to_next = _clamped_int(slide_settings.get("interval_to_next", config.DEFAULT_SLIDE_INTERVAL),
                                                  iv_low, iv_high, config.DEFAULT_SLIDE_INTERVAL)
            transition = slide_settings.get("transition_to_next", "None")
            slide.transition_to_next = transition if isinstance(transition, str) and transition in config.TRANSITION_MAPPINGS else "None"
            # Provisional; probing the material replaces this with the stream-based result.
            slide.is_video = slide.filename is not None and slide.filename.lower().endswith(config.SUPPORTED_VIDEO_FORMATS)
            slide.selected_audio_stream_index = _clamped_int(slide_settings.get("selected_audio_stream_index", 0), 0, 1 << 16, 0)
            
            # Invalid cached values are dropped; they are recomputed from the PDF.
            p_hash = slide_settings.get("p_hash")
            slide.p_hash = p_hash if isinstance(p_hash, str) and re.fullmatch(r"[0-9a-fA-F]+", p_hash) else None
            thumbnail = slide_settings.get("thumbnail_b64")
            slide.thumbnail_b64 = thumbnail if isinstance(thumbnail, str) and thumbnail else None

            if slide.is_video:
                position = slide_settings.get("video_position", "Center")
                slide.video_position = position if isinstance(position, str) and position in config.VIDEO_POSITION_MAP else "Center"
                # Clamp the PinP scale to its valid range and drop any effect tokens
                # that are not recognized, so a hand-edited file cannot inject values
                # the UI and renderer do not understand.
                ps_low, ps_high = config.PINP_SCALE_RANGE
                try:
                    slide.video_scale = max(ps_low, min(ps_high, int(slide_settings.get("video_scale", config.DEFAULT_VIDEO_SCALE))))
                except (TypeError, ValueError):
                    slide.video_scale = config.DEFAULT_VIDEO_SCALE
                raw_effects = slide_settings.get("video_effects", [])
                if isinstance(raw_effects, list):
                    slide.video_effects = [e for e in raw_effects if isinstance(e, str) and e in config.VIDEO_EFFECT_MAP and e != "None"]
                else:
                    slide.video_effects = []

        self.log_message.emit("[SUCCESS] Successfully loaded project settings.", 'app')
        return project_model

    @staticmethod
    def _compute_integrity_hash(params_dict: dict, slides_list: list) -> str:
        # Hash the persisted representation, excluding the thumbnail, encoder map and hash field, so it can be verified on load.
        hashable_params = {
            k: v for k, v in params_dict.items()
            if k not in ('integrity_hash', 'available_encoders')
        }
        hashable_slides = [
            {k: v for k, v in slide.items() if k != 'thumbnail_b64'}
            for slide in slides_list
        ]
        data_string = toml.dumps({'parameters': hashable_params, 'slides': hashable_slides})
        return hashlib.sha256(data_string.encode('utf-8')).hexdigest()

    def _coerce_and_validate_parameters(self, params: ProjectParameters):
        # Coerce/reset loaded values that are the wrong type or outside allowed option sets to keep the model in sync with the UI.
        defaults = ProjectParameters()

        def reset(field_name, reason):
            default_value = getattr(defaults, field_name)
            self.log_message.emit(
                f"[WARNING] Loaded setting '{field_name}'={getattr(params, field_name)!r} is invalid "
                f"({reason}); reset to default {default_value!r}.", 'app')
            setattr(params, field_name, default_value)

        # Every parameter is a single scalar (or None); a list/table in its place
        # cannot be interpreted and would break the lookups below.
        for f in fields(params):
            value = getattr(params, f.name)
            if f.name != 'available_encoders' and value is not None and not isinstance(value, (str, int, float, bool)):
                reset(f.name, 'not a single value')

        # Older files stored option labels; convert them to the keys used now.
        for field_name, options in _KEYED_OPTION_FIELDS.items():
            value = getattr(params, field_name)
            if value not in options:
                for key, label in options.items():
                    if value == label:
                        setattr(params, field_name, key)
                        break

        for bool_field in _BOOL_FIELDS:
            try:
                setattr(params, bool_field, _coerce_bool(getattr(params, bool_field)))
            except ValueError:
                reset(bool_field, 'not a boolean')

        for str_field in _STR_FIELDS:
            if not isinstance(getattr(params, str_field), str):
                reset(str_field, 'not a string')

        if params.hardware_encoding is not None and params.hardware_encoding not in _HARDWARE_OPTIONS:
            reset('hardware_encoding', 'unknown hardware encoder')

        for int_field in ('fps', 'encoding_value', 'audio_channels', 'watermark_opacity', 'watermark_fontsize'):
            try:
                setattr(params, int_field, int(getattr(params, int_field)))
            except (ValueError, TypeError):
                reset(int_field, 'not an integer')

        enum_checks = {
            'codec': list(config.SOFTWARE_CODEC_MAP.keys()),
            'resolution': config.RESOLUTION_OPTIONS,
            'fps': config.FPS_OPTIONS,
            'audio_bitrate': config.AUDIO_BITRATE_OPTIONS,
            'audio_sample_rate': config.AUDIO_SAMPLE_RATE_OPTIONS,
            'encoding_mode': list(config.ENCODING_MODES.keys()),
            'encoding_pass': list(config.ENCODING_PASSES.keys()),
            'normalize_loudness_mode': list(config.LOUDNORM_MODES.keys()),
            'watermark_color': list(config.WATERMARK_COLOR_OPTIONS_RGB.keys()),
            'watermark_rotation': list(config.WATERMARK_ROTATION_OPTIONS),
            'watermark_fontfamily': list(config.BUNDLED_FONTS.keys()),
        }
        for field_name, allowed in enum_checks.items():
            value = getattr(params, field_name)
            if value in allowed:
                continue
            # Hand-edited files often carry the right value in the wrong form, e.g.
            # audio_sample_rate = 48000 instead of "48000", or "1920X1080".
            candidate = str(value).strip()
            if field_name == 'resolution':
                candidate = candidate.lower()
            for option in allowed:
                if str(option) == candidate:
                    setattr(params, field_name, option)
                    break
            else:
                reset(field_name, 'not an allowed value')

        # Clamp audio_channels to the valid range so it maps to a selectable combo entry.
        ch_low, ch_high = config.AUDIO_CHANNELS_RANGE
        if not (ch_low <= params.audio_channels <= ch_high):
            clamped = max(ch_low, min(ch_high, params.audio_channels))
            self.log_message.emit(
                f"[WARNING] Loaded 'audio_channels'={params.audio_channels} is out of range "
                f"[{ch_low}, {ch_high}]; clamped to {clamped}.", 'app')
            params.audio_channels = clamped

        for field_name, (low, high) in (('watermark_opacity', config.WATERMARK_OPACITY_RANGE),
                                         ('watermark_fontsize', config.WATERMARK_FONTSIZE_RANGE)):
            value = getattr(params, field_name)
            if not (low <= value <= high):
                clamped = max(low, min(high, value))
                self.log_message.emit(
                    f"[WARNING] Loaded '{field_name}'={value} is out of range [{low}, {high}]; clamped to {clamped}.", 'app')
                setattr(params, field_name, clamped)

        # Clamp the quality/bitrate value to the range valid for the current mode
        # (and, in quality mode, for the codec: MPEG-4 uses qscale 1-31).
        if params.encoding_mode == "QUALITY" and params.codec == 'MPEG-4 Part 2':
            low, high = config.ENCODING_MPEG4_QSCALE_RANGE
        elif params.encoding_mode == "QUALITY":
            low, high = config.ENCODING_CRF_RANGE
        else:
            low, high = config.ENCODING_BITRATE_RANGE_KBPS
        if not (low <= params.encoding_value <= high):
            clamped = max(low, min(high, params.encoding_value))
            self.log_message.emit(
                f"[WARNING] Loaded 'encoding_value'={params.encoding_value} is out of range "
                f"[{low}, {high}]; clamped to {clamped}.", 'app')
            params.encoding_value = clamped

    def save_project_settings(self, project_model: ProjectModel) -> bool:
        if not project_model.project_folder:
            QMessageBox.warning(self.main_window, self.tr("Project Folder Not Set"), self.tr("Please select a project folder before saving settings."))
            return False

        default_path = project_model.project_folder / "settings.toml"

        reply = QMessageBox.question(
            self.main_window, self.tr("Save Project"), self.tr("Save settings to the default location?\n\n({0})").format(default_path),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )

        file_path_to_save = None
        use_save_as = False
        if reply == QMessageBox.Yes:
            if default_path.exists():
                overwrite_reply = QMessageBox.question(
                    self.main_window, self.tr("Confirm Overwrite"), self.tr("'{0}' already exists. Do you want to overwrite it?").format(default_path.name),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if overwrite_reply == QMessageBox.Yes:
                    file_path_to_save = default_path
                else:
                    # Keep the existing file untouched, but let the user save a copy
                    # elsewhere instead of silently abandoning the save.
                    use_save_as = True
            else:
                file_path_to_save = default_path
        else:
            use_save_as = True

        if use_save_as:
            dialog_path, _ = QFileDialog.getSaveFileName(
                self.main_window, self.tr("Save Project Settings As..."), str(project_model.project_folder),
                self.tr("Project Settings (*.toml);;All Files (*)")
            )
            if dialog_path:
                file_path_to_save = Path(dialog_path)

        if file_path_to_save:
            return self._perform_save(file_path_to_save, project_model)
        return False

    def _perform_save(self, file_path: Path, project_model: ProjectModel, notify: bool = True) -> bool:
        paths_data = {}
        
        if project_model.project_folder:
            if file_path.parent == project_model.project_folder:
                paths_data['project_folder_is_self'] = True
            else:
                paths_data['project_folder_is_self'] = False
                paths_data['project_folder'] = str(project_model.project_folder)
        else:
            paths_data['project_folder_is_self'] = False
            
        paths_data['output_folder'] = str(project_model.output_folder) if project_model.output_folder else ""

        slides_data = []
        for slide in project_model.slides:
            slide_entry = {
                "material": slide.filename,
                "interval_to_next": slide.interval_to_next,
                "transition_to_next": slide.transition_to_next,
                "chapter_title": slide.chapter_title,
                "selected_audio_stream_index": slide.selected_audio_stream_index,
                "p_hash": slide.p_hash,
                "thumbnail_b64": slide.thumbnail_b64,
            }
            if slide.filename == config.SILENT_MATERIAL_NAME:
                slide_entry["duration"] = slide.duration

            if slide.is_video:
                slide_entry["video_position"] = slide.video_position
                slide_entry["video_scale"] = slide.video_scale
                slide_entry["video_effects"] = slide.video_effects

            slides_data.append(slide_entry)

        params_to_save = asdict(project_model.parameters)

        if 'available_encoders' in params_to_save:
            del params_to_save['available_encoders']

        try:
            # Hash the persisted representation so the value can be verified on load.
            calculated_hash = self._compute_integrity_hash(params_to_save, slides_data)
            params_to_save['integrity_hash'] = calculated_hash
        except Exception as e:
            QMessageBox.critical(self.main_window, self.tr("Error during hash calculation"), self.tr("Could not generate integrity hash. Settings will not be saved.\n\nDetails: {0}").format(e))
            return False

        validation_data = {
            "pdf_file_hash": self.main_window.validator.validated_pdf_hash or ""
        }

        final_toml_data = {
            "config_version": self.main_window.__version__,
            "paths": paths_data,
            "validation_info": validation_data,
            "parameters": params_to_save,
            "slides": slides_data
        }

        warning_comment = (
            "# --- Project Settings File ---\n"
            "# WARNING: This file is automatically generated by the application.\n"
            "# Manual editing is not recommended as it may cause unexpected behavior or data loss.\n"
            "# Please use the application's user interface to modify project settings.\n\n"
        )

        # Serialize fully before touching the filesystem so a serialization error
        # cannot leave a half-written file behind.
        try:
            full_content = warning_comment + toml.dumps(final_toml_data)
        except (ValueError, TypeError) as e:
            QMessageBox.critical(self.main_window, self.tr("Error"), self.tr("Failed to save settings: {0}").format(e))
            return False

        # Write atomically: write to a temp file in the same directory, fsync, then
        # os.replace() into place. A crash, full disk, or write error mid-save can no
        # longer truncate or corrupt the existing settings.toml (which the loader would
        # otherwise silently discard, losing every per-slide setting).
        tmp_path = file_path.with_name(file_path.name + f".tmp-{os.getpid()}")
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(full_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(file_path))
        except OSError as e:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            QMessageBox.critical(self.main_window, self.tr("Error"), self.tr("Failed to save settings: {0}").format(e))
            return False

        if notify:
            QMessageBox.information(self.main_window, self.tr("Success"), self.tr("Project settings saved to:\n{0}").format(file_path))
        self.log_message.emit(f"[INFO] Project settings saved to: {file_path}", 'app')
        return True