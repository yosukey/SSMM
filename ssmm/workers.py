# workers.py
from PySide6.QtCore import QObject, Signal, Slot
from pathlib import Path
from ssmm.models import ProjectModel
from ssmm.validator import ProjectValidator
from ssmm.settings_manager import SettingsManager, SettingsFileParseError

class EncoderTestWorker(QObject):
    finished = Signal(object, object)

    def __init__(self, validator):
        super().__init__()
        self.validator = validator

    def run(self):
        try:
            encoders_map, logs = self.validator.get_functional_encoders()
            self.finished.emit(encoders_map, logs)
        except Exception as e:
            # Emit a terminal signal even on failure so the owning QThread can quit.
            self.finished.emit({}, [f"[ERROR] Hardware encoder test failed: {e}"])

class ProjectSetupWorker(QObject):
    finished = Signal(ProjectModel)
    error = Signal(str, str)
    log_message = Signal(str, str)

    def __init__(self, settings_manager: SettingsManager, validator: ProjectValidator, path: Path, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.validator = validator
        self.path = path

    def run(self):
        original_logger = self.validator.log
        try:
            self.settings_manager.log_message.connect(self.log_message)

            def worker_logger(text, source='app'):
                self.log_message.emit(text, source)

            self.validator.log = worker_logger
            project_model = None

            def setup_from_toml(toml_file: Path, folder_override: Path | None) -> ProjectModel | None:
                model = self.settings_manager._load_from_file(
                    toml_file, project_folder_override=folder_override)
                if model:
                    self.validator.probe_and_cache_all_materials(model)
                    if model.slides and not model.slides[0].p_hash:
                         self.validator.compute_and_populate_pdf_details(model)
                return model

            def setup_from_folder(folder: Path) -> ProjectModel:
                model = ProjectModel(project_folder=folder)
                main_window = self.settings_manager.main_window
                main_window.initialize_project_from_pdf(model)
                main_window._automap_materials(model)
                self.validator.probe_and_cache_all_materials(model)
                self.validator.compute_and_populate_pdf_details(model)
                return model

            if self.path.is_file() and self.path.suffix == '.toml':
                project_model = setup_from_toml(self.path, None)
            elif self.path.is_file() and self.path.suffix.lower() == '.dmj':
                project_model = self.settings_manager.import_dougameijin_project(self.path)
                if project_model:
                    self.validator.probe_and_cache_all_materials(project_model)
                    self.validator.compute_and_populate_pdf_details(project_model)
            elif self.path.is_dir():
                candidate = self.path / 'settings.toml'
                if candidate.is_file():
                    try:
                        project_model = setup_from_toml(candidate, self.path)
                    except SettingsFileParseError as e:
                        self.log_message.emit(
                            f"[WARNING] settings.toml in the project folder could not be parsed "
                            f"({e}); loading the folder as a new project instead.", 'app')
                        self.validator.validated_pdf_hash = None
                        project_model = setup_from_folder(self.path)
                else:
                    project_model = setup_from_folder(self.path)
            else:
                raise ValueError("Invalid path provided to ProjectSetupWorker.")

            self.finished.emit(project_model)

        except Exception as e:
            title = e.__class__.__name__
            message = f"An error occurred during project setup: {e}"
            self.error.emit(title, message)
        finally:
            self.validator.log = original_logger
            try:
                self.settings_manager.log_message.disconnect(self.log_message)
            except (TypeError, RuntimeError):
                # disconnect() raises if the signal was never connected or already torn down.
                pass

class ValidationWorker(QObject):
    log_message = Signal(str, str)

    validation_finished = Signal(object, int, dict)
    validation_error = Signal(str)
    validation_canceled = Signal()

    def __init__(self, validator: ProjectValidator, model: ProjectModel, encoders_map: dict, parent=None):
        super().__init__(parent)
        self.validator = validator
        self.model = model
        self.encoders_map = encoders_map

    @Slot()
    def cancel(self):
        self.log_message.emit("Validation cancellation requested.", "app")
        self.validator.cancel()

    def run(self):
        try:
            def worker_logger(text, source='app'):
                self.log_message.emit(text, source)
            
            original_logger = self.validator.log
            try:
                self.validator.log = worker_logger

                self.validator.probe_and_cache_all_materials(self.model)

                self.validator.start_validation()
                messages, page_count, snapshot = self.validator.validate(self.model, self.encoders_map)
                
                if self.validator.is_canceled():
                    self.validation_canceled.emit()
                else:
                    self.validation_finished.emit(messages, page_count, snapshot)
            finally:
                self.validator.log = original_logger

        except Exception as e:
            if 'original_logger' in locals():
                self.validator.log = original_logger
            self.validation_error.emit(f"An unexpected error occurred during validation: {e}")