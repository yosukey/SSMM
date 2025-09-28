# worker_manager.py
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal

from models import ProjectModel
from video_processing import VideoProcessor
from settings_manager import SettingsManager
from validator import ProjectValidator
from workers import EncoderTestWorker, ProjectSetupWorker, ValidationWorker


class WorkerManager(QObject):
    transient_worker_finished = Signal()

    validation_finished = Signal(object, int, dict)
    validation_error = Signal(str)
    validation_canceled = Signal()
    encoder_test_finished = Signal(object, object)
    project_setup_finished = Signal(ProjectModel)
    project_setup_error = Signal(str, str)
    
    progress_updated = Signal(int)
    log_message = Signal(str, str)
    video_finished = Signal(bool, str)
    preview_finished = Signal(bool, str)
    
    _start_video_creation_signal = Signal(ProjectModel, bool)
    _start_preview_creation_signal = Signal(ProjectModel, int, Path, bool, bool)
    _cancel_video_processing_signal = Signal()


    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_transient_thread = None
        self.current_transient_worker = None
        
        self.video_processor = None
        self.video_thread = None

    def setup_persistent_workers(self):
        self.video_processor = VideoProcessor()
        self.video_thread = QThread()
        self.video_processor.moveToThread(self.video_thread)

        self.video_processor.progress_updated.connect(self.progress_updated)
        self.video_processor.log_message.connect(self.log_message)
        self.video_processor.video_finished.connect(self.video_finished)
        self.video_processor.preview_finished.connect(self.preview_finished)
        
        self._start_video_creation_signal.connect(self.video_processor.start_video_creation)
        self._start_preview_creation_signal.connect(self.video_processor.start_preview_creation)
        self._cancel_video_processing_signal.connect(self.video_processor.cancel)

        self.video_thread.start()

    def shutdown_persistent_workers(self):
        if self.video_thread:
            self.video_thread.quit()
            self.video_thread.wait(3000)

    def _start_transient_worker(self, worker_class, worker_args: tuple, signals_to_slots: dict):
        if self.current_transient_thread and self.current_transient_thread.isRunning():
            return

        self.current_transient_thread = QThread()
        self.current_transient_worker = worker_class(*worker_args)
        self.current_transient_worker.moveToThread(self.current_transient_thread)
        
        terminal_signal_names = [
            'finished', 'error', 'canceled', 'validation_finished', 
            'validation_error', 'validation_canceled', 'project_setup_finished', 
            'project_setup_error', 'encoder_test_finished'
        ]

        for signal_name, slot_or_signal in signals_to_slots.items():
            signal = getattr(self.current_transient_worker, signal_name)
            signal.connect(slot_or_signal)
            
            if signal_name in terminal_signal_names:
                signal.connect(self.current_transient_thread.quit)

        self.current_transient_thread.started.connect(self.current_transient_worker.run)
        self.current_transient_thread.finished.connect(self.current_transient_worker.deleteLater)
        self.current_transient_thread.finished.connect(self.current_transient_thread.deleteLater)
        self.current_transient_thread.finished.connect(self._clear_transient_references)

        self.current_transient_thread.start()

    def _clear_transient_references(self):
        self.current_transient_thread = None
        self.current_transient_worker = None
        self.transient_worker_finished.emit()

    def start_project_setup(self, settings_manager: SettingsManager, validator: ProjectValidator, path: Path):
        self._start_transient_worker(
            worker_class=ProjectSetupWorker,
            worker_args=(settings_manager, validator, path),
            signals_to_slots={
                'finished': self.project_setup_finished,
                'error': self.project_setup_error,
                'log_message': self.log_message,
            }
        )

    def start_validation(self, validator, model, encoders_map):
        self._start_transient_worker(
            worker_class=ValidationWorker,
            worker_args=(validator, model, encoders_map),
            signals_to_slots={
                'log_message': self.log_message,
                'validation_finished': self.validation_finished,
                'validation_error': self.validation_error,
                'validation_canceled': self.validation_canceled,
            }
        )

    def start_encoder_test(self, validator):
        self._start_transient_worker(
            worker_class=EncoderTestWorker,
            worker_args=(validator,),
            signals_to_slots={ 'finished': self.encoder_test_finished }
        )

    def start_video_creation(self, model: ProjectModel, is_verbose: bool):
        self._start_video_creation_signal.emit(model, is_verbose)

    def start_preview_creation(self, model: ProjectModel, index: int, path: Path, is_verbose: bool, include_intervals: bool):
        self._start_preview_creation_signal.emit(model, index, path, is_verbose, include_intervals)

    def cancel_all_tasks(self):
        if self.current_transient_worker and hasattr(self.current_transient_worker, 'cancel'):
            self.current_transient_worker.cancel()
        
        self._cancel_video_processing_signal.emit()