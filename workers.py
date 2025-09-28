# workers.py
from PySide6.QtCore import QObject, Signal
from pathlib import Path
from models import ProjectModel
from validator import ProjectValidator
from settings_manager import SettingsManager

class EncoderTestWorker(QObject):
    finished = Signal(object, object)

    def __init__(self, validator):
        super().__init__()
        self.validator = validator

    def run(self):
        encoders_map, logs = self.validator.get_functional_encoders()
        self.finished.emit(encoders_map, logs)

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

            if self.path.is_file() and self.path.suffix == '.toml':
                project_model = self.settings_manager._load_from_file(self.path)
                if project_model:
                    self.validator.probe_and_cache_all_materials(project_model)
                    if project_model.slides and not project_model.slides[0].p_hash:
                         self.validator.compute_and_populate_pdf_details(project_model)
            elif self.path.is_dir():
                project_model = ProjectModel(project_folder=self.path)
                main_window = self.settings_manager.main_window
                main_window.initialize_project_from_pdf(project_model)
                main_window._automap_materials(project_model)
                self.validator.probe_and_cache_all_materials(project_model)
                self.validator.compute_and_populate_pdf_details(project_model)
            else:
                raise ValueError("Invalid path provided to ProjectSetupWorker.")

            self.finished.emit(project_model)

        except Exception as e:
            title = e.__class__.__name__
            message = f"An error occurred during project setup: {e}"
            self.error.emit(title, message)
        finally:
            self.validator.log = original_logger
            self.settings_manager.log_message.disconnect(self.log_message)

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