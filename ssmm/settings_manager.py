# settings_manager.py
import toml
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox
from PySide6.QtCore import QStandardPaths, QObject, Signal
import hashlib
from dataclasses import asdict

from ssmm.models import ProjectModel, ProjectParameters, Slide
from ssmm import config
from ssmm import dougameijin_importer
from ssmm import pdf_utils

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

    def _load_from_file(self, file_path: Path) -> ProjectModel | None:
        self.log_message.emit(f"[INFO] Attempting to load project settings from: {file_path}", 'app')
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = toml.load(f)
        except (toml.TomlDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"The settings file '{file_path.name}' is not a valid TOML file or is corrupt: {e}")

        validation_info = data.get("validation_info", {})
        cached_pdf_hash = validation_info.get("pdf_file_hash")
        if cached_pdf_hash:
            self.main_window.validator.validated_pdf_hash = cached_pdf_hash
            self.log_message.emit(f"[INFO] Loaded cached PDF hash: {cached_pdf_hash[:10]}...", 'app')

        loaded_params_dict = data.get("parameters", {})

        stored_hash = loaded_params_dict.get('integrity_hash')
        if stored_hash:
            try:
                recomputed_hash = self._compute_integrity_hash(loaded_params_dict, data.get("slides", []))
                if recomputed_hash != stored_hash:
                    self.log_message.emit("[WARNING] Settings file integrity check failed; the file may have been manually edited or corrupted. Loading anyway.", 'app')
            except Exception as e:
                self.log_message.emit(f"[WARNING] Could not verify settings file integrity: {e}", 'app')

        paths = data.get("paths", {})
        project_path_str = str(file_path.parent) if paths.get("project_folder_is_self") else paths.get("project_folder")
        if not project_path_str or not Path(project_path_str).is_dir():
             raise FileNotFoundError(f"Project folder '{project_path_str}' defined in settings.toml could not be found.")

        project_dir = Path(project_path_str)
        pdf_files = list(project_dir.glob('*.[pP][dD][fF]'))
        if not pdf_files:
            raise FileNotFoundError(f"No PDF file found in the project folder '{project_dir}'.")
        if len(pdf_files) > 1:
            raise ValueError(f"Multiple PDF files found in the project folder '{project_dir}'. Please ensure there is only one PDF.")

        pdf_path = pdf_files[0]

        toml_slide_count = len(data.get("slides", []))

        try:
            pdf_page_count = pdf_utils.page_count(pdf_path)
        except Exception as e:
            raise ValueError(f"Failed to read the PDF file '{pdf_path.name}': {e}")

        if toml_slide_count != pdf_page_count:
            self.log_message.emit(f"[WARNING] Page count in TOML ({toml_slide_count}) differs from PDF ({pdf_page_count}). Migration will be required.", 'app')

        project_model = ProjectModel()
        project_model.project_folder = project_dir

        output_path_str = paths.get("output_folder")
        if output_path_str and Path(output_path_str).is_dir():
            project_model.output_folder = Path(output_path_str)

        project_model.parameters = ProjectParameters()
        for key, value in loaded_params_dict.items():
            if hasattr(project_model.parameters, key):
                setattr(project_model.parameters, key, value)
        self._coerce_and_validate_parameters(project_model.parameters)

        loaded_slides_settings = data.get("slides", [])
        
        project_model.slides = [Slide() for _ in range(toml_slide_count)]
        
        project_model.available_materials = sorted([
            p.name for p in project_model.project_folder.iterdir()
            if p.is_file() and p.suffix.lower() in config.SUPPORTED_FORMATS
        ])

        for idx, slide_settings in enumerate(loaded_slides_settings):
            if idx >= len(project_model.slides):
                break

            slide = project_model.slides[idx]
            material = slide_settings.get("material")

            if material == config.SILENT_MATERIAL_NAME:
                slide.filename = config.SILENT_MATERIAL_NAME
                slide.duration = slide_settings.get("duration", 0)
            elif material in project_model.available_materials:
                slide.filename = material
            elif material:
                # The saved material is no longer present in the project folder; leave the
                # slide unassigned (validation will flag it) but tell the user why.
                self.log_message.emit(
                    f"[WARNING] Material '{material}' referenced by slide {idx + 1} was not found "
                    f"in the project folder; the slide will be left unassigned.", 'app')

            slide.chapter_title = slide_settings.get("chapter_title", "")
            slide.interval_to_next = slide_settings.get("interval_to_next", config.DEFAULT_SLIDE_INTERVAL)
            slide.transition_to_next = slide_settings.get("transition_to_next", "None")
            slide.is_video = slide.filename is not None and slide.filename.lower().endswith(config.SUPPORTED_VIDEO_FORMATS)
            slide.selected_audio_stream_index = slide_settings.get("selected_audio_stream_index", 0)
            
            slide.p_hash = slide_settings.get("p_hash")
            slide.thumbnail_b64 = slide_settings.get("thumbnail_b64")

            if slide.is_video:
                slide.video_position = slide_settings.get("video_position", "Center")
                slide.video_scale = slide_settings.get("video_scale", config.DEFAULT_VIDEO_SCALE)
                slide.video_effects = slide_settings.get("video_effects", [])

        self.log_message.emit(f"[SUCCESS] Successfully loaded project settings.", 'app')
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

        for int_field in ('fps', 'encoding_value', 'audio_channels'):
            try:
                setattr(params, int_field, int(getattr(params, int_field)))
            except (ValueError, TypeError):
                reset(int_field, 'not an integer')

        enum_checks = {
            'resolution': config.RESOLUTION_OPTIONS,
            'fps': config.FPS_OPTIONS,
            'audio_bitrate': config.AUDIO_BITRATE_OPTIONS,
            'audio_sample_rate': config.AUDIO_SAMPLE_RATE_OPTIONS,
            'encoding_mode': list(config.ENCODING_MODES.values()),
            'encoding_pass': list(config.ENCODING_PASSES.values()),
            'normalize_loudness_mode': list(config.LOUDNORM_MODES.values()),
            'watermark_color': list(config.WATERMARK_COLOR_OPTIONS_RGB.keys()),
            'watermark_rotation': list(config.WATERMARK_ROTATION_OPTIONS),
            'watermark_fontfamily': list(config.BUNDLED_FONTS.keys()),
        }
        for field_name, allowed in enum_checks.items():
            if getattr(params, field_name) not in allowed:
                reset(field_name, 'not an allowed value')

        # Clamp audio_channels to the valid range so it maps to a selectable combo entry.
        ch_low, ch_high = config.AUDIO_CHANNELS_RANGE
        if not (ch_low <= params.audio_channels <= ch_high):
            clamped = max(ch_low, min(ch_high, params.audio_channels))
            self.log_message.emit(
                f"[WARNING] Loaded 'audio_channels'={params.audio_channels} is out of range "
                f"[{ch_low}, {ch_high}]; clamped to {clamped}.", 'app')
            params.audio_channels = clamped

        # Clamp the quality/bitrate value to the range valid for the current mode.
        if params.encoding_mode == config.ENCODING_MODES["QUALITY"]:
            low, high = config.ENCODING_CRF_RANGE
        else:
            low, high = config.ENCODING_BITRATE_RANGE_KBPS
        if not (low <= params.encoding_value <= high):
            clamped = max(low, min(high, params.encoding_value))
            self.log_message.emit(
                f"[WARNING] Loaded 'encoding_value'={params.encoding_value} is out of range "
                f"[{low}, {high}]; clamped to {clamped}.", 'app')
            params.encoding_value = clamped

    def save_project_settings(self, project_model: ProjectModel):
        if not project_model.project_folder:
            QMessageBox.warning(self.main_window, self.tr("Project Folder Not Set"), self.tr("Please select a project folder before saving settings."))
            return

        default_path = project_model.project_folder / "settings.toml"

        reply = QMessageBox.question(
            self.main_window, self.tr("Save Project"), self.tr("Save settings to the default location?\n\n({0})").format(default_path),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )

        file_path_to_save = None
        if reply == QMessageBox.Yes:
            file_path_to_save = default_path
            if file_path_to_save.exists():
                overwrite_reply = QMessageBox.question(
                    self.main_window, self.tr("Confirm Overwrite"), self.tr("'{0}' already exists. Do you want to overwrite it?").format(file_path_to_save.name),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if overwrite_reply == QMessageBox.No:
                    return
        else:
            dialog_path, _ = QFileDialog.getSaveFileName(
                self.main_window, self.tr("Save Project Settings As..."), str(project_model.project_folder),
                self.tr("Project Settings (*.toml);;All Files (*)")
            )
            if dialog_path:
                file_path_to_save = Path(dialog_path)

        if file_path_to_save:
            self._perform_save(file_path_to_save, project_model)

    def _perform_save(self, file_path: Path, project_model: ProjectModel):
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
            return

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

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(warning_comment)
                toml.dump(final_toml_data, f)

            QMessageBox.information(self.main_window, self.tr("Success"), self.tr("Project settings saved to:\n{0}").format(file_path))
        except IOError as e:
            QMessageBox.critical(self.main_window, self.tr("Error"), self.tr("Failed to save settings: {0}").format(e))