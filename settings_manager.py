# settings_manager.py
import toml
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox
from PySide6.QtCore import QStandardPaths
import fitz
import hashlib
from dataclasses import asdict

from models import ProjectModel, ProjectParameters, Slide
import config

class SettingsManager:
    def __init__(self, main_window):
        self.main_window = main_window

    def prompt_for_load_path(self) -> Path | None:
        if self.main_window.project_model.project_folder:
            start_dir = str(self.main_window.project_model.project_folder)
        else:
            start_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)

        default_path = Path(start_dir) / "settings.toml" if self.main_window.project_model.project_folder else None

        if default_path and default_path.exists():
            reply = QMessageBox.question(
                self.main_window, "Load Project", f"Load settings from the default location?\n\n({default_path})",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                return default_path
        
        dialog_path, _ = QFileDialog.getOpenFileName(
            self.main_window, "Load Project Settings File", start_dir, "Project Settings (*.toml);;All Files (*)"
        )
        if dialog_path:
            return Path(dialog_path)
            
        return None

    def _load_from_file(self, file_path: Path) -> ProjectModel | None:
        self.main_window.write_debug(f"[INFO] Attempting to load project settings from: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            data = toml.load(f)

        validation_info = data.get("validation_info", {})
        cached_pdf_hash = validation_info.get("pdf_file_hash")
        if cached_pdf_hash:
            self.main_window.validator.validated_pdf_hash = cached_pdf_hash
            self.main_window.write_debug(f"[INFO] Loaded cached PDF hash: {cached_pdf_hash[:10]}...")

        loaded_params_dict = data.get("parameters", {})

        paths = data.get("paths", {})
        project_path_str = str(file_path.parent) if paths.get("project_folder_is_self") else paths.get("project_folder")
        if not project_path_str or not Path(project_path_str).is_dir():
             raise FileNotFoundError(f"Project folder '{project_path_str}' defined in settings.toml could not be found.")

        project_dir = Path(project_path_str)
        pdf_files = list(project_dir.glob('*.pdf'))
        if not pdf_files:
            raise FileNotFoundError(f"No PDF file found in the project folder '{project_dir}'.")
        if len(pdf_files) > 1:
            raise ValueError(f"Multiple PDF files found in the project folder '{project_dir}'. Please ensure there is only one PDF.")

        pdf_path = pdf_files[0]

        toml_slide_count = len(data.get("slides", []))

        try:
            with fitz.open(pdf_path) as doc:
                pdf_page_count = doc.page_count
        except Exception as e:
            raise ValueError(f"Failed to read the PDF file '{pdf_path.name}': {e}")

        if toml_slide_count != pdf_page_count:
            self.main_window.write_debug(f"[WARNING] Page count in TOML ({toml_slide_count}) differs from PDF ({pdf_page_count}). Migration will be required.")

        project_model = ProjectModel()
        project_model.project_folder = project_dir

        output_path_str = paths.get("output_folder")
        if output_path_str and Path(output_path_str).is_dir():
            project_model.output_folder = Path(output_path_str)

        project_model.parameters = ProjectParameters()
        for key, value in loaded_params_dict.items():
            if hasattr(project_model.parameters, key):
                setattr(project_model.parameters, key, value)

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

        self.main_window.write_debug(f"[INFO] Successfully loaded project settings.")
        return project_model

    def save_project_settings(self, project_model: ProjectModel):
        if not project_model.project_folder:
            QMessageBox.warning(self.main_window, "Project Folder Not Set", "Please select a project folder before saving settings.")
            return

        default_path = project_model.project_folder / "settings.toml"

        reply = QMessageBox.question(
            self.main_window, "Save Project", f"Save settings to the default location?\n\n({default_path})",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )

        file_path_to_save = None
        if reply == QMessageBox.Yes:
            file_path_to_save = default_path
            if file_path_to_save.exists():
                overwrite_reply = QMessageBox.question(
                    self.main_window, "Confirm Overwrite", f"'{file_path_to_save.name}' already exists. Do you want to overwrite it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if overwrite_reply == QMessageBox.No:
                    return
        else:
            dialog_path, _ = QFileDialog.getSaveFileName(
                self.main_window, "Save Project Settings As...", str(project_model.project_folder),
                "Project Settings (*.toml);;All Files (*)"
            )
            if dialog_path:
                file_path_to_save = Path(dialog_path)

        if file_path_to_save:
            self._perform_save(file_path_to_save, project_model)

    def _perform_save(self, file_path: Path, project_model: ProjectModel):
        paths_data = {}
        if project_model.project_folder and file_path.parent == project_model.project_folder:
            paths_data['project_folder_is_self'] = True
        else:
            paths_data['project_folder_is_self'] = False
            paths_data['project_folder'] = str(project_model.project_folder)
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

        params_for_hash = params_to_save.copy()
        if 'integrity_hash' in params_for_hash:
            del params_for_hash['integrity_hash']

        try:
            slides_for_hash = []
            for slide in project_model.slides:
                slide_dict = asdict(slide)
                slide_dict.pop('thumbnail_b64', None)
                slides_for_hash.append(slide_dict)

            data_string_for_hash = toml.dumps({'parameters': params_for_hash, 'slides': slides_for_hash})
            calculated_hash = hashlib.sha256(data_string_for_hash.encode('utf-8')).hexdigest()
            params_to_save['integrity_hash'] = calculated_hash
        except Exception as e:
            QMessageBox.critical(self.main_window, "Error during hash calculation", f"Could not generate integrity hash. Settings will not be saved.\n\nDetails: {e}")
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

            QMessageBox.information(self.main_window, "Success", f"Project settings saved to:\n{file_path}")
        except IOError as e:
            QMessageBox.critical(self.main_window, "Error", f"Failed to save settings: {e}")