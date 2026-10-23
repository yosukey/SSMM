# worker_manager.py
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal

from ssmm.models import ProjectModel
from ssmm.video_processing import VideoProcessor
from ssmm.settings_manager import SettingsManager
from ssmm.validator import ProjectValidator
from ssmm.workers import EncoderTestWorker, ProjectSetupWorker, ValidationWorker


class WorkerManager(QObject):
    transient_worker_finished = Signal()
    transient_worker_busy = Signal(str)

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

        self.video_thread.start()

    def shutdown_persistent_workers(self):
        if self.video_thread:
            self.video_thread.quit()
            # cancel_all_tasks() should already have killed any running ffmpeg so the
            # worker slot unwinds quickly. If it still does not stop in time, fall back to
            # terminate() so the QThread is not destroyed while it is still running.
            if not self.video_thread.wait(5000):
                self.video_thread.terminate()
                self.video_thread.wait()

    def shutdown_transient_worker(self, timeout_ms: int = 5000):
        # Stop any in-flight transient worker before the owning window is destroyed.
        thread = self.current_transient_thread
        if thread and thread.isRunning():
            thread.quit()
            if not thread.wait(timeout_ms):
                thread.terminate()
                thread.wait()

    def is_transient_busy(self) -> bool:
        # True while a transient worker (project setup, validation, encoder test) is
        # still running. Callers must check this before changing app state for a new
        # transient task, since _start_transient_worker silently drops a second one.
        return bool(self.current_transient_thread and self.current_transient_thread.isRunning())

    def _start_transient_worker(self, worker_class, worker_args: tuple, signals_to_slots: dict):
        if self.is_transient_busy():
            self.transient_worker_busy.emit(
                self.tr("A process is already running. Please wait for it to complete."))
            return

        thread = QThread()
        worker = worker_class(*worker_args)
        worker.moveToThread(thread)
        self.current_transient_thread = thread
        self.current_transient_worker = worker

        terminal_signal_names = [
            'finished', 'error', 'canceled', 'validation_finished',
            'validation_error', 'validation_canceled', 'project_setup_finished',
            'project_setup_error', 'encoder_test_finished'
        ]

        for signal_name, slot_or_signal in signals_to_slots.items():
            signal = getattr(worker, signal_name)
            signal.connect(slot_or_signal)

            if signal_name in terminal_signal_names:
                signal.connect(thread.quit)

        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Capture this specific thread so a newer transient worker started before
        # this queued slot runs is not clobbered (see _clear_transient_references).
        thread.finished.connect(lambda t=thread: self._clear_transient_references(t))

        thread.start()

    def _clear_transient_references(self, finished_thread=None):
        # Runs on QThread.finished (queued to this thread). By the time it runs a
        # newer transient worker may already own current_transient_thread/worker; in
        # that case leave the newer references untouched so the new task stays
        # cancelable and tracked.
        if finished_thread is not None and finished_thread is not self.current_transient_thread:
            return

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
        worker = self.current_transient_worker
        if worker is not None and hasattr(worker, 'cancel'):
            # The worker thread's event loop is blocked inside run() for the whole
            # task, so a queued signal to cancel() would only be delivered after the
            # work already finished. cancel() merely flips a thread-safe flag, so
            # call it directly from the GUI thread (mirrors VideoProcessor.cancel()).
            try:
                worker.cancel()
            except RuntimeError:
                # The task has just finished: deleteLater() already destroyed the
                # worker before the queued reference cleanup ran. Nothing is left to
                # cancel, and raising here would abort closeEvent's thread shutdown.
                pass

        if self.video_processor:
            self.video_processor.cancel()