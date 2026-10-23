# video_processing.py
import sys
import subprocess
import tempfile
from pathlib import Path
import threading
import uuid
import re
import json
import shlex
import platform
import os
import math
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot, QProcess

from ssmm.models import ProjectModel, Slide, ProjectParameters
from ssmm import config
from ssmm import pdf_utils
from ssmm.utils import get_ffprobe_path
from ssmm.ffmpeg_builder import FFmpegCommandBuilder
from ssmm.media_utils import (display_aspect_ratio, find_project_pdf, quantize_duration,
                              rotation_filters, slide_timeline)
from ssmm.slide_processor import SlideJob, SlideProcessorFactory
from ssmm.watermark import render_watermark_overlay

class ProcessingCanceled(Exception):
    pass

class OutputCommitError(Exception):
    """The render succeeded but the result could not be moved to its destination."""

class SleepInhibitor:
    def __init__(self, logger_func):
        self.logger = logger_func
        self.inhibitor_process = None
        self.os_type = platform.system()

        if self.os_type == "Windows":
            self.ES_CONTINUOUS = 0x80000000
            self.ES_SYSTEM_REQUIRED = 0x00000001

    def __enter__(self):
        if self.os_type == "Windows":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(
                    self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
                self.logger("[INFO] System sleep temporarily inhibited for video processing (Windows).", 'app')
            except Exception as e:
                self.logger(f"[WARNING] Could not inhibit system sleep on Windows: {e}", 'app')
        
        elif self.os_type == "Darwin":
            try:
                pid = os.getpid()
                self.inhibitor_process = subprocess.Popen(['caffeinate', '-i', '-w', str(pid)])
                self.logger(f"[INFO] System sleep temporarily inhibited via 'caffeinate' (for PID: {pid}) for video processing (macOS).", 'app')
            except Exception as e:
                self.logger(f"[WARNING] Could not inhibit system sleep on macOS using caffeinate: {e}", 'app')
        
        else:
            self.logger(f"[INFO] Sleep inhibition is not supported on this OS ({self.os_type}).", 'app')

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.os_type == "Windows":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
                self.logger("[INFO] System sleep inhibition released (Windows).", 'app')
            except Exception as e:
                self.logger(f"[WARNING] Could not release sleep inhibition on Windows: {e}", 'app')

        elif self.os_type == "Darwin" and self.inhibitor_process:
            try:
                if self.inhibitor_process.poll() is None:
                    self.inhibitor_process.terminate()
                    self.inhibitor_process.wait(timeout=2)
                self.logger("[INFO] System sleep inhibition released ('caffeinate' process).", 'app')
            except Exception as e:
                self.logger(f"[WARNING] Could not terminate caffeinate process: {e}", 'app')
            finally:
                self.inhibitor_process = None

class VideoProcessor(QObject):
    progress_updated = Signal(int)
    log_message = Signal(str, str)
    video_finished = Signal(bool, str)
    preview_finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._is_canceled = False
        self._is_canceled_lock = threading.Lock()
        self.watermark_path: Path | None = None
        self._current_step = 0
        self._total_steps = 1
        self._is_verbose = False
        self._segment_ext = config.INTERMEDIATE_CONTAINER

    def _set_canceled(self, value: bool):
        with self._is_canceled_lock:
            self._is_canceled = value

    def _get_is_canceled(self) -> bool:
        with self._is_canceled_lock:
            return self._is_canceled

    def cancel(self):
        # cancel() runs on the GUI thread, but QProcess is thread-affine and must not
        # be touched from here. Only flip the flag; the worker thread's own polling in
        # _run_subprocess notices it and kills its QProcess safely. This removes both
        # the check/start race window and the pid-reuse hazard of an os.kill-by-pid.
        self._set_canceled(True)

    @Slot(ProjectModel, bool)
    def start_video_creation(self, project_model: ProjectModel, is_verbose: bool):
        self._set_canceled(False)
        self._is_verbose = is_verbose
        # Always emit a terminal signal, even if run_video_creation raises before its
        # own try/except; otherwise the UI would stay stuck in "processing" forever.
        try:
            success, message = self.run_video_creation(project_model)
        except Exception as e:
            success, message = False, str(e)
        self.video_finished.emit(success, message)

    @Slot(ProjectModel, int, Path, bool, bool)
    def start_preview_creation(self, project_model: ProjectModel, slide_index: int, pdf_path: Path, is_verbose: bool, include_intervals: bool):
        self._set_canceled(False)
        self._is_verbose = is_verbose
        try:
            success, message = self.run_preview_creation(project_model, slide_index, pdf_path, include_intervals)
        except Exception as e:
            success, message = False, str(e)
        self.preview_finished.emit(success, message)
        
    def _run_subprocess(self, command_list: list[str], capture_output=False, timeout_sec=None):
        if self._get_is_canceled():
            raise ProcessingCanceled("Operation was canceled before starting the process.")

        self.log_message.emit(f"[DEBUG] Running command: {' '.join(shlex.quote(str(arg)) for arg in command_list)}", 'app')

        process = QProcess()
        process.setProcessChannelMode(QProcess.MergedChannels)
        
        output_chunks = []
        buffered_chars = [0]

        def handle_output():
            data = process.readAll().data().decode('utf-8', errors='replace')
            if data:
                output_chunks.append(data)
                if not capture_output:
                    self.log_message.emit(data.strip(), "ffmpeg")
                    # Only the tail is needed for an error report; keep memory bounded
                    # during long encodes that print progress for hours.
                    buffered_chars[0] += len(data)
                    if buffered_chars[0] > 2 * config.FFMPEG_ERROR_TAIL_CHARS:
                        tail = "".join(output_chunks)[-config.FFMPEG_ERROR_TAIL_CHARS:]
                        output_chunks[:] = [tail]
                        buffered_chars[0] = len(tail)
        
        process.readyRead.connect(handle_output)

        try:
            process.start(command_list[0], command_list[1:])

            if not process.waitForStarted(config.PROCESS_START_TIMEOUT_MS):
                raise RuntimeError("Process failed to start.")

            timeout_ms = (timeout_sec * 1000) if timeout_sec is not None else config.FFMPEG_ENCODE_TIMEOUT_MS
            # Poll instead of a single blocking wait so a cancel raised on the GUI
            # thread is acted on within one poll interval by killing this QProcess
            # here on the worker thread (the only thread allowed to touch it). This
            # also closes the old check/start cancel race: a process started just
            # after the entry check is killed on the very next poll.
            poll_ms = 200
            deadline = time.monotonic() + timeout_ms / 1000.0
            finished_normally = False
            while True:
                if process.waitForFinished(poll_ms):
                    finished_normally = True
                    break
                # waitForFinished() returns False both on a poll timeout AND for a
                # process that had already finished before the call, so a bare False
                # is not proof it is still running. Confirm via state() to avoid
                # spinning until the (10-hour) deadline once the process has exited.
                if process.state() == QProcess.NotRunning:
                    finished_normally = True
                    break
                handle_output()
                if self._get_is_canceled():
                    if process.state() != QProcess.NotRunning:
                        process.kill()
                        process.waitForFinished(5000)
                    raise ProcessingCanceled()
                if time.monotonic() >= deadline:
                    break

            handle_output()
            combined_output = "".join(output_chunks)

            exit_code = process.exitCode()
            exit_status = process.exitStatus()

            # Check the cancel flag before treating CrashExit as a real crash,
            # since cancelling kills the process and reports CrashExit.
            if self._get_is_canceled():
                raise ProcessingCanceled()

            if not finished_normally:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(5000)
                raise TimeoutError(f"Process timed out after {timeout_ms / 1000} seconds.")

            if exit_status == QProcess.CrashExit:
                raise Exception(f"FFmpeg process crashed unexpectedly (e.g. segmentation fault or out-of-memory kill).\nOutput:\n{combined_output}")

            if exit_code != 0 or exit_status != QProcess.NormalExit:
                error_string = process.errorString()
                raise Exception(f"Command exited with status {exit_code} and error '{error_string}'.\nOutput:\n{combined_output}")

            return combined_output

        finally:
            # Ensure the child never outlives this call (e.g. on an unexpected raise).
            if process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(5000)

    def _create_ffmpeg_builder(self) -> FFmpegCommandBuilder:
        builder = FFmpegCommandBuilder()
        if self._is_verbose:
            builder.add_global_options('-loglevel', 'info')
        return builder

    def _segment_path(self, folder: Path, stem: str) -> Path:
        return folder / f"{stem}{self._segment_ext}"

    @staticmethod
    def _intermediate_container_for(codec: str) -> str:
        return config.INTERMEDIATE_CONTAINER_AV1 if 'av1' in codec else config.INTERMEDIATE_CONTAINER

    def run_video_creation(self, project_model: ProjectModel):
        with SleepInhibitor(self.log_message.emit):
            self.watermark_path = None

            # Resolve the output directory first; output_folder may be None, and the
            # path below must use the fallback rather than dereferencing None. Make it
            # absolute: concat lists resolve relative entries against the list's own
            # folder, and Python < 3.12 keeps mkdtemp() results relative.
            output_dir = (project_model.output_folder or Path(".")).resolve()
            final_video_path = output_dir / f"{project_model.parameters.filename_input}.mp4"
            # Unique temp name so a second render (or a stale leftover) targeting the
            # same output does not clobber this in-progress file.
            temp_video_path = final_video_path.with_name(
                f"{final_video_path.stem}.tmp-{uuid.uuid4().hex[:8]}{final_video_path.suffix}")
            delete_temp = project_model.parameters.delete_temp_checkbox

            try:
                if delete_temp:
                    with tempfile.TemporaryDirectory(dir=output_dir, prefix="movie-temp-", ignore_cleanup_errors=True) as temp_dir:
                        self._run_logic(project_model, Path(temp_dir), temp_video_path)
                else:
                    temp_dir = tempfile.mkdtemp(dir=output_dir, prefix="movie-temp-")
                    self.log_message.emit(f"[INFO] Temporary files are being kept in: {temp_dir}", 'app')
                    self._run_logic(project_model, Path(temp_dir), temp_video_path)

                if self._get_is_canceled():
                    raise ProcessingCanceled()

                # Written before the video is moved into place, so the chapter list is
                # also available when that move fails and the render is kept under its
                # temporary name.
                if project_model.parameters.export_youtube_chapters:
                    self.log_message.emit("[INFO] Generating YouTube chapter file...", 'app')
                    try:
                        self._generate_youtube_chapter_file(project_model, final_video_path)
                        self.log_message.emit("[SUCCESS] YouTube chapter file generated successfully.", 'app')
                    except Exception as chap_e:
                        self.log_message.emit(f"[ERROR] Could not generate YouTube chapter file: {chap_e}", 'app')

                self._commit_output(temp_video_path, final_video_path)

                self.log_message.emit(f"[SUCCESS] Video created successfully at: {final_video_path}", 'app')
                return (True, str(final_video_path))

            except ProcessingCanceled:
                self._cleanup_files(temp_video_path)
                self.log_message.emit("[INFO] Video creation was canceled by user.", 'app')
                return (False, "Canceled by user.")
            except OutputCommitError as e:
                # The render itself succeeded; keep the finished file instead of
                # discarding hours of encoding because the destination was locked.
                self.log_message.emit(f"[ERROR] {e}", 'app')
                return (False, str(e))
            except Exception as e:
                self._cleanup_files(temp_video_path)
                self.log_message.emit(f"[ERROR] An exception occurred: {e}", 'app')
                return (False, str(e))
            finally:
                self.watermark_path = None

    @staticmethod
    def _commit_output(rendered_path: Path, final_path: Path):
        # os.replace() swaps the file in atomically and leaves the rendered file in
        # place when the destination cannot be replaced (e.g. it is open in a media
        # player on Windows).
        try:
            os.replace(str(rendered_path), str(final_path))
        except OSError as e:
            raise OutputCommitError(
                f"The video was rendered but could not be saved as '{final_path}' ({e}). "
                f"The file may be open in another application. "
                f"The rendered video has been kept at: {rendered_path}")

    def run_preview_creation(self, project_model: ProjectModel, slide_index: int, pdf_path: Path, include_intervals: bool):
        with SleepInhibitor(self.log_message.emit):
            self.watermark_path = None

            base_filename = project_model.parameters.filename_input
            if not base_filename:
                base_filename = project_model.project_folder.name

            output_dir = (project_model.output_folder or Path(".")).resolve()
            random_id = uuid.uuid4().hex[:8]
            preview_filename = f"{base_filename}-preview-slide{slide_index+1}-{random_id}.mp4"
            final_preview_path = output_dir / preview_filename

            delete_temp = project_model.parameters.delete_temp_checkbox

            def _run_in_temp_dir(temp_folder: Path):
                if not delete_temp:
                    self.log_message.emit(f"Temporary files for preview are being kept in: {temp_folder}", 'app')

                params = project_model.parameters
                target_width = int(params.resolution.split('x')[0])
                codec_option = self._setup_processing(params, temp_folder)
                self.progress_updated.emit(10)

                self.log_message.emit(f"Generating main video for slide {slide_index + 1}...", 'app')
                main_slide = project_model.slides[slide_index]
                image_paths = {}

                with pdf_utils.open_pdf_ctx(pdf_path) as doc:
                    def page_image(index: int) -> Path:
                        if index not in image_paths:
                            image_paths[index] = self._render_single_page(doc, index, target_width, temp_folder)
                        return image_paths[index]

                    main_video_path = self._segment_path(temp_folder, f"slide_{slide_index+1:03d}_main")
                    job = SlideJob(slide_index, main_slide, project_model,
                                   {slide_index: page_image(slide_index)}, temp_folder, codec_option)
                    self._generate_single_slide_video(job, output_path=main_video_path)
                    self.progress_updated.emit(40)
                    if self._get_is_canceled(): raise ProcessingCanceled()

                    videos_to_concat = []

                    # Preview intervals are built from the PDF pages, so PinP content
                    # of adjacent slides is not shown in them (see SelectSlideDialog).
                    if include_intervals and slide_index > 0:
                        prev_slide = project_model.slides[slide_index - 1]
                        if prev_slide.interval_to_next > 0:
                            self.log_message.emit(f"Generating interval before slide {slide_index + 1}...", 'app')
                            videos_to_concat.append(self._build_interval(
                                project_model, page_image(slide_index - 1), page_image(slide_index),
                                prev_slide, temp_folder, codec_option, f"preview_before_{slide_index}",
                                apply_watermark=True))

                    videos_to_concat.append(main_video_path)
                    self.progress_updated.emit(70)

                    if include_intervals and slide_index < len(project_model.slides) - 1 and main_slide.interval_to_next > 0:
                        self.log_message.emit(f"Generating interval after slide {slide_index + 1}...", 'app')
                        videos_to_concat.append(self._build_interval(
                            project_model, page_image(slide_index), page_image(slide_index + 1),
                            main_slide, temp_folder, codec_option, f"preview_after_{slide_index}",
                            apply_watermark=True))

                self.log_message.emit("Assembling preview...", 'app')
                concat_list_path = self._write_concat_list(temp_folder / 'concat_preview_list.txt', videos_to_concat)
                try:
                    builder = self._create_ffmpeg_builder()
                    builder.add_input(concat_list_path, ['-f', 'concat', '-safe', '0'])
                    builder.set_output(final_preview_path,
                                       ['-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy']
                                       + self._mp4_video_tag_options(codec_option)
                                       + self._get_final_audio_options(params)
                                       + ['-movflags', '+faststart'])
                    self._run_subprocess(builder.build())
                except ProcessingCanceled:
                    raise
                except Exception as e:
                    self.log_message.emit(f"[WARNING] Fast concatenation failed. Retrying with full re-encode... Reason: {e}", 'app')
                    video_opts = self._get_video_encoding_options(params, pass_num=1, is_single_pass_override=True)
                    builder = self._create_ffmpeg_builder()
                    builder.add_input(concat_list_path, ['-f', 'concat', '-safe', '0'])
                    builder.set_output(final_preview_path,
                                       ['-map', '0:v:0', '-map', '0:a:0', '-c:v', codec_option] + video_opts
                                       + self._mp4_video_tag_options(codec_option)
                                       + self._get_final_audio_options(params)
                                       + ['-r', str(params.fps), '-movflags', '+faststart'])
                    self._run_subprocess(builder.build())
                    self.log_message.emit("Re-encode concatenation successful.", 'app')

                self.progress_updated.emit(100)

            try:
                if delete_temp:
                    with tempfile.TemporaryDirectory(dir=output_dir, prefix="preview-temp-", ignore_cleanup_errors=True) as temp_dir_str:
                        _run_in_temp_dir(Path(temp_dir_str))
                else:
                    temp_dir_str = tempfile.mkdtemp(dir=output_dir, prefix="preview-temp-")
                    _run_in_temp_dir(Path(temp_dir_str))

                self.log_message.emit(f"[SUCCESS] Preview created successfully at: {final_preview_path}", 'app')
                return (True, str(final_preview_path))

            except ProcessingCanceled:
                # The preview is assembled straight into the output folder, so a
                # cancel/failure can leave a truncated file behind; remove it.
                self._cleanup_files(final_preview_path)
                self.log_message.emit("[INFO] Preview creation was canceled by user.", 'app')
                return (False, "Canceled by user.")
            except Exception as e:
                self._cleanup_files(final_preview_path)
                self.log_message.emit(f"[ERROR] An exception occurred during preview creation: {e}", 'app')
                return (False, str(e))
            finally:
                self.watermark_path = None

    def _generate_single_slide_video(self, job: SlideJob, output_path: Path = None) -> tuple[int, Path]:
        if self._get_is_canceled():
            raise ProcessingCanceled()

        i = job.index
        slide_video_path = output_path if output_path else self._segment_path(job.temp_folder, f"slide_{i+1:03d}")

        self.log_message.emit(f"[INFO] Generating segment for slide {i+1}...", 'app')

        processor = SlideProcessorFactory.get_processor(self, job, slide_video_path)
        processor.process()

        return i, slide_video_path

    def _setup_processing(self, params: ProjectParameters, temp_folder: Path) -> str:
        codec_option = self._resolve_codec_option(params.codec, params.hardware_encoding)
        # Every segment of this run must share one container for the concat demuxer.
        self._segment_ext = self._intermediate_container_for(codec_option)

        if params.add_watermark and params.watermark_text:
            try:
                width, height = map(int, params.resolution.split('x'))
                self.watermark_path = self._generate_watermark_image(params, width, height, temp_folder)
            except Exception as e:
                self.log_message.emit(f"[WARNING] Could not generate watermark image, skipping: {e}", 'app')
                self.watermark_path = None
        return codec_option

    def _render_single_page(self, doc: "pdf_utils.pdfium.PdfDocument", page_num: int, target_width: int, temp_folder: Path) -> Path:
        if self._get_is_canceled():
            raise ProcessingCanceled()

        width, _height = pdf_utils.page_size(doc, page_num)
        if width == 0:
            raise Exception(f"PDF page {page_num + 1} has zero width.")

        pil_image = pdf_utils.render_page_to_pil(doc, page_num, target_width=target_width)

        out_path = temp_folder / f"page_{page_num + 1:03d}.png"
        pil_image.save(str(out_path))
        return out_path

    def _render_pdf_pages(self, project_model: ProjectModel, temp_folder: Path) -> dict[int, Path]:
        if not project_model.project_folder or not project_model.project_folder.is_dir():
            raise ValueError("Project folder is not set or is not a valid directory.")

        pdf_path = find_project_pdf(project_model.project_folder)
        if not pdf_path:
            raise FileNotFoundError("Could not find a PDF file in the project folder.")

        self.log_message.emit("Rendering PDF pages to PNGs...", 'app')

        target_width, _ = map(int, project_model.parameters.resolution.split('x'))
        with pdf_utils.open_pdf_ctx(pdf_path) as doc:
            return {
                page_num: self._render_single_page(doc, page_num, target_width, temp_folder)
                for page_num in range(pdf_utils.num_pages(doc))
            }

    def _advance_progress(self):
        self._current_step += 1
        self.progress_updated.emit(int(self._current_step / self._total_steps * 100))

    def _generate_slide_videos(self, project_model: ProjectModel, image_paths_dict: dict, temp_folder: Path, codec_option: str) -> list[Path]:
        self.log_message.emit("Generating individual slide videos...", 'app')
        slide_videos = []
        for i, slide in enumerate(project_model.slides):
            if self._get_is_canceled(): raise ProcessingCanceled()
            try:
                job = SlideJob(i, slide, project_model, image_paths_dict, temp_folder, codec_option)
                _, slide_video_path = self._generate_single_slide_video(job)
                slide_videos.append(slide_video_path)
                self._advance_progress()
                self.log_message.emit(f"Finished segment for slide {i + 1}.", 'app')
            except ProcessingCanceled:
                raise
            except Exception as e:
                self.log_message.emit(f"[ERROR] A critical error occurred while generating slide {i + 1}: {e}", 'app')
                raise
        return slide_videos

    def _generate_transition_videos(self, project_model: ProjectModel, slide_videos: list[Path], temp_folder: Path, codec_option: str) -> dict[int, Path]:
        self.log_message.emit("Generating transitions...", 'app')
        transition_videos_map = {}
        for i in range(len(project_model.slides) - 1):
            if self._get_is_canceled(): raise ProcessingCanceled()

            slide = project_model.slides[i]
            if slide.interval_to_next <= 0:
                continue
            try:
                self.log_message.emit(f"[INFO] Generating transition between slide {i+1} and {i+2}...", 'app')
                output_path = self._segment_path(temp_folder, f"interval_{i}")
                self._create_transition_video(project_model, slide_videos[i], slide_videos[i + 1],
                                              slide, output_path, codec_option, i)
                transition_videos_map[i] = output_path
                self._advance_progress()
                self.log_message.emit(f"Finished transition for slide {i + 1}.", 'app')
            except ProcessingCanceled:
                raise
            except Exception as e:
                self.log_message.emit(f"[ERROR] A critical error occurred while generating a transition for slide {i + 1}: {e}", 'app')
                raise
        return transition_videos_map

    def _write_concat_list(self, list_path: Path, videos: list[Path]) -> Path:
        with list_path.open('w', encoding='utf-8') as f:
            for video in videos:
                # Absolute paths: relative entries are resolved against the list file's folder.
                f.write(f"file '{self._sanitize_path_for_concat(str(Path(video).absolute()))}'\n")
        return list_path

    def _concatenate_videos(self, slide_videos: list[Path], transition_videos_map: dict, temp_folder: Path) -> Path:
        self.log_message.emit("Final concatenation...", 'app')
        final_video_list = []
        for i, slide_video in enumerate(slide_videos):
            final_video_list.append(slide_video)
            if i in transition_videos_map:
                final_video_list.append(transition_videos_map[i])

        concat_list_path = self._write_concat_list(temp_folder / 'concat_list.txt', final_video_list)
        temp_concat_video = self._segment_path(temp_folder, "final_concat")

        builder = self._create_ffmpeg_builder()
        builder.add_input(concat_list_path, ['-f', 'concat', '-safe', '0'])
        builder.set_output(temp_concat_video, ['-c', 'copy'])
        self._run_subprocess(builder.build())
        return temp_concat_video

    def _loudnorm_filter(self, params: ProjectParameters, source: Path) -> Optional[str]:
        if not params.normalize_loudness:
            return None
        target = (f"I={config.LOUDNORM_TARGET_I}:LRA={config.LOUDNORM_TARGET_LRA}"
                  f":TP={config.LOUDNORM_TARGET_TP}")
        if params.normalize_loudness_mode == "ONE_PASS":
            self.log_message.emit("Applying loudness normalization (1-Pass)...", 'app')
            return f"loudnorm={target}"
        self.log_message.emit("Analyzing audio for 2-Pass loudness normalization...", 'app')
        try:
            measured = self._get_loudnorm_params(source)
            self.log_message.emit("Applying loudness normalization (2-Pass)...", 'app')
            return f"loudnorm={target}:{measured}:linear=true"
        except ProcessingCanceled:
            raise
        except Exception as e:
            self.log_message.emit(f"[ERROR] Loudness analysis failed: {e}. Falling back to 1-Pass.", 'app')
            return f"loudnorm={target}"

    def _finalize_video(self, project_model: ProjectModel, concatenated_video_path: Path, final_output_path: Path, temp_folder: Path, codec_option: str):
        # Single final step: copy the video stream, encode the (uncompressed)
        # intermediate audio to AAC exactly once, optionally normalize its loudness
        # and embed chapters.
        params = project_model.parameters
        builder = self._create_ffmpeg_builder()
        builder.add_input(concatenated_video_path)

        output_options = ['-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy']
        output_options += self._mp4_video_tag_options(codec_option)

        loudnorm_filter = self._loudnorm_filter(params, concatenated_video_path)
        if loudnorm_filter:
            output_options += ['-af', loudnorm_filter]
        output_options += self._get_final_audio_options(params)

        if any(s.chapter_title for s in project_model.slides):
            self.log_message.emit("Embedding chapter information...", 'app')
            ffmetadata_path = temp_folder / 'ffmetadata.txt'
            self._generate_ffmetadata(project_model, ffmetadata_path)
            builder.add_input(ffmetadata_path)
            output_options += ['-map_metadata', '1', '-map_chapters', '1']

        # Bound the result to the timeline length: dynamic (1-pass) loudnorm can
        # emit a few extra samples at the end, which would outlast the video.
        _start_times, total_duration = slide_timeline(project_model.slides, params.fps)
        if total_duration > 0:
            output_options += ['-t', str(total_duration)]
        output_options += ['-movflags', '+faststart']
        builder.set_output(final_output_path, output_options)
        self._run_subprocess(builder.build())
        self._advance_progress()

    def _run_logic(self, project_model: ProjectModel, temp_folder: Path, output_video_path: Path):
        total_slides = len(project_model.slides)
        # A transition segment is only produced (and only advances the step counter)
        # for a slide whose interval_to_next > 0. Counting every pair would leave the
        # progress bar short of 100% whenever some intervals are 0.
        num_transitions = sum(1 for s in project_model.slides[:-1] if s.interval_to_next > 0)
        self._total_steps = total_slides + num_transitions + 1  # Slides + Transitions + Final Step
        self._current_step = 0

        codec_option = self._setup_processing(project_model.parameters, temp_folder)
        image_paths_dict = self._render_pdf_pages(project_model, temp_folder)
        slide_videos = self._generate_slide_videos(project_model, image_paths_dict, temp_folder, codec_option)
        transition_videos_map = self._generate_transition_videos(project_model, slide_videos, temp_folder, codec_option)
        concatenated_video = self._concatenate_videos(slide_videos, transition_videos_map, temp_folder)
        self._finalize_video(project_model, concatenated_video, output_video_path, temp_folder, codec_option)

    def _combine_image_audio(self, model, image, audio, output, codec, slide):
        self._process_slide(model, image, output, codec, slide, audio_path=audio)

    def _combine_image_silent_audio(self, model, image, duration, output, codec, slide):
        self._process_slide(model, image, output, codec, slide, duration=duration)

    def _uses_two_pass(self, params: ProjectParameters, codec: str) -> bool:
        return (params.encoding_pass == "TWO_PASS"
                and params.encoding_mode != "QUALITY"
                and codec in config.TWO_PASS_ENCODERS)

    def _execute_encoding(self, builder: FFmpegCommandBuilder, output_path: Path, params: "ProjectParameters", codec: str):
        base_output_options = builder.output_options.copy()
        audio_opts = self._get_intermediate_audio_options(params)

        if self._uses_two_pass(params, codec):
            pass_log_prefix = str(output_path.with_suffix(''))
            null_device = "NUL" if sys.platform == "win32" else "/dev/null"

            self.log_message.emit(f"[INFO] Running 1st pass for {output_path.name}...", 'app')
            video_opts_1 = self._get_video_encoding_options(params, pass_num=1, pass_log_prefix=pass_log_prefix)
            # The 1st pass discards its output to the null device with the null muxer
            # ('-f null'), since the device has no extension for ffmpeg to infer one.
            # Audio is kept mapped (it is cheap PCM) so that filter graphs with audio
            # outputs stay fully connected.
            pass1_opts = base_output_options + ['-c:v', codec] + video_opts_1 + audio_opts + ['-f', 'null']
            builder.set_output(null_device, pass1_opts)
            self._run_subprocess(builder.build())
            if self._get_is_canceled():
                raise ProcessingCanceled()

            self.log_message.emit(f"[INFO] Running 2nd pass for {output_path.name}...", 'app')
            video_opts_2 = self._get_video_encoding_options(params, pass_num=2, pass_log_prefix=pass_log_prefix)
            pass2_opts = base_output_options + ['-c:v', codec] + video_opts_2 + audio_opts
            builder.set_output(output_path, pass2_opts)
            self._run_subprocess(builder.build())
        else:
            self.log_message.emit(f"[INFO] Running 1-pass encoding for {output_path.name}...", 'app')
            video_opts = self._get_video_encoding_options(params, pass_num=1)
            builder.set_output(output_path, base_output_options + ['-c:v', codec] + video_opts + audio_opts)
            self._run_subprocess(builder.build())

    @staticmethod
    def _audio_source_label(input_index: int, slide: Slide) -> str:
        # Address the selected stream by its absolute index; fall back to the first
        # audio stream when the probed stream list is unavailable or stale.
        if slide.audio_streams and 0 <= slide.selected_audio_stream_index < len(slide.audio_streams):
            stream = slide.audio_streams[slide.selected_audio_stream_index]
            return f"[{input_index}:{stream['index']}]"
        return f"[{input_index}:a:0]"

    def _process_slide(self, model, image, output, codec, slide: Slide, audio_path=None, duration=None):
        params = model.parameters
        fps = params.fps
        width, height = map(int, params.resolution.split('x'))

        builder = self._create_ffmpeg_builder()
        # Loop the still at the output frame rate (the image2 default is 25 fps), so
        # its frames lie on the output grid and '-t' yields exactly the quantized count.
        builder.add_input(image, ['-framerate', str(fps), '-loop', '1'])

        filter_chains = [f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v_scaled]"]
        final_video_stream = "[v_scaled]"

        if self.watermark_path:
            builder.add_input(self.watermark_path)
            watermark_input_index = len(builder.inputs) - 1
            filter_chains.append(f"{final_video_stream}[{watermark_input_index}:v]overlay=x=0:y=0[v_out]")
            final_video_stream = "[v_out]"

        if audio_path:
            builder.add_input(audio_path)
            audio_input_index = len(builder.inputs) - 1
            target_duration = self._get_media_duration(audio_path)
            if target_duration <= 0:
                # Re-probing can fail transiently; fall back to the duration measured at
                # validation. A zero would emit a '-t 0' (empty) segment that breaks concat.
                target_duration = slide.duration
            if target_duration <= 0:
                raise ValueError(f"Could not determine a valid duration for audio '{Path(audio_path).name}'.")
            audio_source = self._audio_source_label(audio_input_index, slide)
        else:
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi'])
            audio_input_index = len(builder.inputs) - 1
            target_duration = duration or 1
            audio_source = f"[{audio_input_index}:a]"

        # Pad the audio and cut every stream with the output '-t', so the segment is
        # exactly the frame-quantized length the chapter math assumes.
        filter_chains.append(f"{audio_source}apad[a_out]")
        builder.set_filter_complex(";".join(filter_chains))

        quantized_duration = quantize_duration(target_duration, fps)
        builder.output_options = ['-map', final_video_stream, '-map', '[a_out]',
                                  '-t', str(quantized_duration), '-r', str(fps)]
        self._execute_encoding(builder, output, params, codec)

    def _overlay_video_on_image(self, model, image, video, output, codec, slide: Slide):
        params = model.parameters
        fps = params.fps
        width, height = map(int, params.resolution.split('x'))
        pos_info = config.VIDEO_POSITION_MAP.get(slide.video_position, config.VIDEO_POSITION_MAP['Center'])

        aspect_ratio = display_aspect_ratio(slide.tech_info) or 16 / 9
        target_pinp_height = round(height * slide.video_scale / 100.0 / 2) * 2
        final_pinp_width = round(height * slide.video_scale / 100.0 * aspect_ratio / 2) * 2

        filter_chains = [f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[bg]"]
        current_stream = "[1:v]"

        # 1. Pre-filters: deinterlace, then turn the frame upright (the input is
        #    decoded with -noautorotate so the rotation is applied exactly once).
        pre_filters = []
        if slide.tech_info.get('is_interlaced'):
            pre_filters.append("yadif")
        pre_filters += rotation_filters(slide.tech_info.get('rotate'))
        if pre_filters:
            filter_chains.append(f"{current_stream}{','.join(pre_filters)}[fg_pre]")
            current_stream = "[fg_pre]"

        effects = slide.video_effects

        # 2. Processing
        processing_filters = []
        if "HFlip" in effects: processing_filters.append("hflip")
        if "VFlip" in effects: processing_filters.append("vflip")
        if "Blur" in effects: processing_filters.append("boxblur=5")
        if "Pixelate" in effects: processing_filters.append("scale=iw/16:ih/16,scale=iw*16:ih*16:flags=neighbor")
        if processing_filters:
            filter_chains.append(f"{current_stream}{','.join(processing_filters)}[fg_proc]")
            current_stream = "[fg_proc]"

        # 3. Color
        color_filters = []
        if "Grayscale" in effects: color_filters.append("format=gray")
        elif "Sepia" in effects: color_filters.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
        elif "Negative" in effects: color_filters.append("negate")
        if color_filters:
            filter_chains.append(f"{current_stream}{','.join(color_filters)}[fg_color]")
            current_stream = "[fg_color]"

        # 4. Shape
        shape_filters = []
        if "Circle" in effects:
            # Inscribed circle of the (already upright) frame.
            shape_filters.append("format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                                 "a='if(lt(pow(X-W/2,2)+pow(Y-H/2,2),pow(min(W,H)/2,2)),255,0)'")
        elif "Chroma" in effects:
            shape_filters.append(f"chromakey=color=green:similarity={config.CHROMA_KEY_SIMILARITY}:blend={config.CHROMA_KEY_BLEND}")
        elif "Vignette" in effects:
            shape_filters.append("vignette=eval=frame")
        if shape_filters:
            filter_chains.append(f"{current_stream}{','.join(shape_filters)}[fg_shape]")
            current_stream = "[fg_shape]"

        filter_chains.extend([
            f"{current_stream}scale={final_pinp_width}:{target_pinp_height}[fg_scaled]",
            f"[bg][fg_scaled]overlay=x={pos_info['x']}:y={pos_info['y']}:eof_action=pass[overlaid]"
        ])
        final_stream_name = "[overlaid]"

        builder = self._create_ffmpeg_builder()
        quantized_duration = quantize_duration(slide.duration, fps)
        # The background still runs at the output frame rate; at the image2 default of
        # 25 fps its last frame can end before the segment (e.g. 55 of 56 frames at 60 fps).
        builder.add_input(image, ['-framerate', str(fps), '-loop', '1', '-t', str(quantized_duration)])
        # CFR is enforced by the output '-r' below.
        builder.add_input(video, ['-noautorotate'])

        if self.watermark_path:
            builder.add_input(self.watermark_path)
            filter_chains.append(f"{final_stream_name}[2:v]overlay=x=0:y=0[out]")
            final_stream_name = "[out]"

        if slide.audio_streams:
            audio_source = self._audio_source_label(1, slide)
        else:
            silent_audio_input_index = len(builder.inputs)
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi'])
            audio_source = f"[{silent_audio_input_index}:a]"
        # Audio shorter than the video is padded with silence instead of truncating
        # the whole segment (which would shift every following chapter).
        filter_chains.append(f"{audio_source}apad[a_out]")

        builder.set_filter_complex(";".join(filter_chains))
        builder.output_options = ['-map', final_stream_name, '-map', '[a_out]',
                                  '-t', str(quantized_duration), '-r', str(fps)]
        self._execute_encoding(builder, output, params, codec)

    def _create_transition_video(self, model, prev_video, next_video, slide, output_path, codec, index):
        temp_dir = output_path.parent
        prev_frame = temp_dir / f'prev_frame_{index}.png'
        next_frame = temp_dir / f'next_frame_{index}.png'

        # Last frame of the previous segment: seek close to its end and let
        # '-update 1' keep overwriting the image, so the file ends up holding the
        # final decoded frame (a plain '-sseof' can seek before the start of
        # sub-second clips and yield no frame at all).
        prev_seek = max(0.0, self._get_media_duration(prev_video) - 1.0)
        builder_prev = self._create_ffmpeg_builder()
        builder_prev.add_input(prev_video, ['-ss', str(prev_seek)])
        builder_prev.set_output(prev_frame, ['-map', '0:v:0', '-update', '1'])
        self._run_subprocess(builder_prev.build())

        builder_next = self._create_ffmpeg_builder()
        builder_next.add_input(next_video)
        builder_next.set_output(next_frame, ['-map', '0:v:0', '-frames:v', '1', '-update', '1'])
        self._run_subprocess(builder_next.build())

        # The grabbed frames come from rendered segments that already carry the watermark.
        self._build_interval(model, prev_frame, next_frame, slide, temp_dir, codec, str(index),
                             apply_watermark=False, output_path=output_path)
        self._cleanup_files(prev_frame, next_frame)

    def _build_interval(self, model, prev_frame: Path, next_frame: Path, slide_with_settings: Slide,
                        temp_dir: Path, codec: str, suffix: str, apply_watermark: bool,
                        output_path: Optional[Path] = None) -> Path:
        """Build the interval between two slides from their boundary frames."""
        fps = model.parameters.fps
        interval_duration = quantize_duration(slide_with_settings.interval_to_next, fps)
        output_path = output_path or self._segment_path(temp_dir, f"interval_{suffix}")
        ffmpeg_keyword = config.TRANSITION_MAPPINGS.get(slide_with_settings.transition_to_next)

        if not ffmpeg_keyword:
            # Hold the previous frame for the first half and the next frame for the
            # second half. The two clips' frame counts sum exactly to the interval, so
            # the segment matches the duration the chapter/metadata math assumes.
            total_frames = round(interval_duration * fps)
            first_frames = math.ceil(total_frames / 2)
            second_frames = total_frames - first_frames
            clips = self._still_clips_from_frames(
                model, [(prev_frame, first_frames / fps), (next_frame, second_frames / fps)],
                codec, temp_dir, suffix, apply_watermark)
            concat_list = self._write_concat_list(temp_dir / f'concat_interval_{suffix}.txt', clips)
            builder = self._create_ffmpeg_builder()
            builder.add_input(concat_list, ['-f', 'concat', '-safe', '0'])
            builder.set_output(output_path, ['-c', 'copy'])
            self._run_subprocess(builder.build())
            self._cleanup_files(*clips, concat_list)
        else:
            clips = self._still_clips_from_frames(
                model, [(prev_frame, interval_duration), (next_frame, interval_duration)],
                codec, temp_dir, suffix, apply_watermark)
            # The interval's audio is silence on both sides, so instead of crossfading
            # the two silent tracks a fresh silent source is used. (With xfade and
            # acrossfade in one graph, FFmpeg drops the audio stream entirely at very
            # low frame rates such as 3-5 fps.)
            filter_complex = f"[0:v][1:v]xfade=transition={ffmpeg_keyword}:duration={interval_duration}:offset=0[v]"
            video_opts = self._get_video_encoding_options(model.parameters, pass_num=1, is_single_pass_override=True)
            outputs = (['-map', '[v]', '-map', '2:a', '-c:v', codec] + video_opts
                       + self._get_intermediate_audio_options(model.parameters)
                       + ['-t', str(interval_duration), '-r', str(fps)])
            builder = self._create_ffmpeg_builder()
            builder.add_input(clips[0]).add_input(clips[1])
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi', '-t', str(interval_duration)])
            builder.set_filter_complex(filter_complex)
            builder.set_output(output_path, outputs)
            self._run_subprocess(builder.build())
            self._cleanup_files(*clips)

        return output_path

    def _still_clips_from_frames(self, model, frame_specs: list[tuple[Path, float]], codec: str,
                                 temp_dir: Path, suffix: str, apply_watermark: bool) -> list[Path]:
        """Encode each (frame, duration) pair into a still clip with silent audio."""
        params = model.parameters
        fps = params.fps
        width, height = map(int, params.resolution.split('x'))
        video_opts = self._get_video_encoding_options(params, pass_num=1, is_single_pass_override=True)
        audio_opts = self._get_intermediate_audio_options(params)

        clips = []
        for n, (frame_path, clip_duration) in enumerate(frame_specs):
            clip_path = self._segment_path(temp_dir, f"still_{suffix}_{n}")
            quantized_duration = quantize_duration(clip_duration, fps)
            builder = self._create_ffmpeg_builder()
            builder.add_input(frame_path, ['-framerate', str(fps), '-loop', '1'])
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi', '-t', str(quantized_duration)])

            filter_chains = [f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v_scaled]"]
            final_video_stream = "[v_scaled]"
            if apply_watermark and self.watermark_path:
                builder.add_input(self.watermark_path)
                filter_chains.append(f"{final_video_stream}[2:v]overlay=x=0:y=0[v_out]")
                final_video_stream = "[v_out]"
            builder.set_filter_complex(";".join(filter_chains))

            output_options = (['-map', final_video_stream, '-map', '1:a', '-c:v', codec] + video_opts + audio_opts
                              + ['-t', str(quantized_duration), '-r', str(fps)])
            builder.set_output(clip_path, output_options)
            self._run_subprocess(builder.build())
            clips.append(clip_path)
        return clips

    def _get_media_duration(self, media_path: Path) -> float:
        try:
            command = [
                str(get_ffprobe_path()),
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(media_path)
            ]
            output = self._run_subprocess(command, capture_output=True, timeout_sec=config.FFPROBE_TIMEOUT_S)
            return float(output.strip())

        except ProcessingCanceled:
            # A cancellation raised by _run_subprocess must propagate so the whole
            # pipeline unwinds immediately instead of continuing with a bogus 0.0.
            raise
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not get media duration for '{Path(media_path).name}': {e}", 'app')
            return 0.0

    def _resolve_codec_option(self, codec, hw):
        if hw is not None:
            hw_codec = config.CODEC_MAP.get(codec, {}).get(hw)
            if hw_codec:
                return hw_codec
            # Unknown hardware/codec combination: fall back to the software encoder.
        return config.SOFTWARE_CODEC_MAP.get(codec, 'libx264')

    @staticmethod
    def _mp4_video_tag_options(codec: str) -> list[str]:
        # 'hvc1' is the HEVC sample entry Apple players (QuickTime, Safari, iOS) require.
        if codec in ('libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'):
            return ['-tag:v', 'hvc1']
        return []

    def _get_intermediate_audio_options(self, params: "ProjectParameters"):
        return ['-c:a', config.INTERMEDIATE_AUDIO_CODEC, '-ar', params.audio_sample_rate, '-ac', str(params.audio_channels)]

    def _get_final_audio_options(self, params: "ProjectParameters"):
        return ['-c:a', 'aac', '-b:a', params.audio_bitrate, '-ar', params.audio_sample_rate, '-ac', str(params.audio_channels)]

    @staticmethod
    def _quote_x265_param(value: str) -> str:
        # -x265-params is a ':'-separated list parsed with av_get_token(), so a path
        # (e.g. 'C:/...') must be single-quoted; a literal quote is written as '\''.
        return "'" + value.replace('\\', '/').replace("'", "'\\''") + "'"

    def _two_pass_options(self, codec: str, pass_num: int, pass_log_prefix: Optional[str]) -> list[str]:
        if codec == 'libx265':
            # FFmpeg's libx265 wrapper ignores the generic -pass/-passlogfile options
            # (the stats file stays empty and both passes are identical); x265 needs
            # its own pass/stats parameters.
            params = f"pass={pass_num}"
            if pass_log_prefix:
                params += f":stats={self._quote_x265_param(pass_log_prefix + '-x265.log')}"
            return ['-x265-params', params]
        options = ['-pass', str(pass_num)]
        if pass_log_prefix:
            options += ['-passlogfile', pass_log_prefix]
        return options

    def _get_video_encoding_options(self, params: "ProjectParameters", pass_num: int = 1, is_single_pass_override: bool = False,
                                    pass_log_prefix: Optional[str] = None):
        mode = params.encoding_mode
        value = params.encoding_value
        codec = self._resolve_codec_option(params.codec, params.hardware_encoding)
        fps = params.fps
        gop_size = fps * 2

        options = [
            '-pix_fmt', 'yuv420p',
            '-color_primaries', 'bt709',
            '-color_trc', 'bt709',
            '-colorspace', 'bt709',
            '-g', str(gop_size)
        ]

        # No explicit H.264 level: the encoders derive a conforming level from the
        # resolution and frame rate (a fixed 4.0 is exceeded by 1080p60 and 4K).
        if codec == 'libx264':
            options.extend(['-profile:v', 'high', '-preset', 'medium'])
        elif codec == 'h264_nvenc':
            options.extend(['-profile:v', 'high', '-preset', 'p5'])
        elif codec == 'h264_qsv':
            options.extend(['-profile:v', 'high', '-preset', 'medium'])
        elif codec == 'h264_amf':
            options.extend(['-profile:v', 'high', '-quality', 'quality'])
        elif codec == 'h264_videotoolbox':
            options.extend(['-profile:v', 'high'])

        elif codec == 'libx265':
            options.extend(['-profile:v', 'main', '-preset', 'medium'])
        elif codec == 'hevc_nvenc':
            options.extend(['-profile:v', 'main', '-preset', 'p5'])
        elif codec == 'hevc_qsv':
            options.extend(['-profile:v', 'main', '-preset', 'slow'])
        elif codec == 'hevc_amf':
            options.extend(['-profile:v', 'main', '-quality', 'quality'])
        elif codec == 'hevc_videotoolbox':
            options.extend(['-profile:v', 'main'])

        elif codec == 'libaom-av1':
            options.extend(['-profile:v', 'main', '-cpu-used', '7'])
        elif codec == 'av1_nvenc':
            # av1_nvenc has no named profile constants; omit the profile and let it default to Main.
            options.extend(['-preset', 'p5'])
        elif codec == 'av1_qsv':
            options.extend(['-profile:v', 'main', '-preset', 'medium'])
        elif codec == 'av1_amf':
            options.extend(['-profile:v', 'main', '-quality', 'quality'])

        if mode == "QUALITY":
            if 'nvenc' in codec:
                # NVENC uses -qp for constant-QP rate control.
                options.extend(['-qp', str(value)])
            elif 'qsv' in codec:
                # QSV uses -global_quality for constant quality.
                options.extend(['-global_quality', str(value)])
            elif 'amf' in codec:
                # AMF uses constant-QP rate control with per-frame-type quantisers; AV1 AMF has no B-frame QP.
                options.extend(['-rc', 'cqp', '-qp_i', str(value), '-qp_p', str(value)])
                if codec != 'av1_amf':
                    options.extend(['-qp_b', str(value)])
            elif codec == 'mpeg4':
                # The native mpeg4 encoder uses the qscale quality scale (1-31, lower is better); clamp the value into range.
                low, high = config.ENCODING_MPEG4_QSCALE_RANGE
                options.extend(['-qscale:v', str(max(low, min(high, value)))])
            elif 'videotoolbox' in codec:
                # Defensive only: the UI forces VideoToolbox into a bitrate mode and the
                # validator rejects VideoToolbox + Quality. VideoToolbox has no CRF, so
                # map the CRF (0-51, lower is better) to -q:v (1-100, higher is better).
                low, high = config.ENCODING_CRF_RANGE
                clamped = max(low, min(high, value))
                options.extend(['-q:v', str(round((high - clamped) / (high - low) * 100))])
            else:
                options.extend(['-crf', str(value)])
                if codec == 'libaom-av1':
                    # libaom treats -crf as an upper bitrate bound unless the target
                    # bitrate is 0; -b:v 0 selects true constant-quality mode.
                    options.extend(['-b:v', '0'])
        else:
            options.extend(['-b:v', f'{value}k'])
            if mode == "VBR":
                maxrate = int(value * config.VBR_MAXRATE_MULTIPLIER)
                bufsize = int(value * config.VBR_BUFSIZE_MULTIPLIER)
                options.extend(['-maxrate', f'{maxrate}k', '-bufsize', f'{bufsize}k'])

        if not is_single_pass_override and params.encoding_pass == "TWO_PASS" and mode != "QUALITY":
            if codec in config.TWO_PASS_ENCODERS:
                options.extend(self._two_pass_options(codec, pass_num, pass_log_prefix))
            elif codec in config.MULTIPASS_ENCODERS:
                options.extend(['-multipass', 'fullres'])

        return options

    def _get_loudnorm_params(self, media_path: Path) -> str:
        builder = FFmpegCommandBuilder()
        # The analysis must use the same targets as the apply step, otherwise the
        # reported target_offset refers to a different loudness target.
        target = (f"I={config.LOUDNORM_TARGET_I}:LRA={config.LOUDNORM_TARGET_LRA}"
                  f":TP={config.LOUDNORM_TARGET_TP}")
        builder.add_input(media_path)
        builder.set_output('-', ['-map', '0:a:0', '-af', f'loudnorm={target}:print_format=json', '-f', 'null'])

        output_str = self._run_subprocess(builder.build(), capture_output=True,
                                          timeout_sec=config.LOUDNORM_ANALYSIS_TIMEOUT_S)

        # The JSON block is printed last; take the final {...} so stray braces in
        # earlier log lines (e.g. metadata) cannot break the parse.
        json_matches = re.findall(r'\{[^{}]*\}', output_str)
        if not json_matches:
            self.log_message.emit(f"[WARNING] Could not find loudnorm JSON data in FFmpeg output. Full output:\n{output_str}", 'app')
            raise ValueError("Could not find loudnorm JSON data in FFmpeg output.")

        stats = json.loads(json_matches[-1])

        # Validate measured values; silent/zero-length audio yields non-finite
        # results that the caller falls back from to single-pass loudnorm.
        required_keys = ('input_i', 'input_lra', 'input_tp', 'input_thresh', 'target_offset')
        measured = {}
        for key in required_keys:
            try:
                value = float(stats[key])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"loudnorm analysis returned a missing or non-numeric '{key}'.")
            if not math.isfinite(value):
                raise ValueError(f"loudnorm analysis returned a non-finite '{key}' ({stats.get(key)}); the audio may be silent.")
            measured[key] = value

        return (
            f"measured_I={measured['input_i']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}"
        )

    def _generate_watermark_image(self, params: ProjectParameters, width: int, height: int, temp_folder: Path) -> Path:
        # Shared with the preview overlays (ssmm/watermark.py) so the exported video
        # and the slide previews render the watermark identically.
        final_image = render_watermark_overlay(params, width, height)
        if final_image is None:
            raise ValueError("Watermark is not enabled or has no text.")

        output_path = temp_folder / f"watermark_{uuid.uuid4().hex}.png"
        final_image.save(output_path, "PNG")
        return output_path

    def _format_seconds_to_hhmmss(self, total_seconds: float) -> str:
        total_seconds = int(total_seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            return f"{minutes:02}:{seconds:02}"

    def _generate_youtube_chapter_file(self, model: ProjectModel, video_path: Path):
        start_times, _total = slide_timeline(model.slides, model.parameters.fps)
        chapters = [{'title': slide.chapter_title, 'start_time': start_times[i]}
                    for i, slide in enumerate(model.slides) if slide.chapter_title]

        if len(chapters) < 3 or chapters[0]['start_time'] != 0.0:
            raise ValueError("Chapter data does not meet YouTube requirements. Please re-validate the project.")

        chapter_filename = video_path.with_name(f"{video_path.stem}-chapter-for-youtube.txt")
        with open(chapter_filename, 'w', encoding='utf-8') as f:
            for chap in chapters:
                timestamp = self._format_seconds_to_hhmmss(chap['start_time'])
                f.write(f"{timestamp} {chap['title']}\n")

    @staticmethod
    def _escape_ffmetadata(value: str) -> str:
        # Backslash-escape the ffmetadata special characters '=', ';', '#', '\' and
        # newlines. Escape the backslash first to avoid double-escaping.
        value = value.replace('\r\n', '\n').replace('\r', '\n')
        value = value.replace('\\', '\\\\')
        for ch in ('=', ';', '#'):
            value = value.replace(ch, '\\' + ch)
        return value.replace('\n', '\\\n')

    def _generate_ffmetadata(self, model: ProjectModel, path: Path):
        start_times, total_duration = slide_timeline(model.slides, model.parameters.fps)
        total_duration_ms = int(round(total_duration * 1000))
        chapters = [{'title': slide.chapter_title, 'start_time': int(round(start_times[i] * 1000))}
                    for i, slide in enumerate(model.slides) if slide.chapter_title]
        with path.open('w', encoding='utf-8') as f:
            f.write(";FFMETADATA1\n")
            for i, chap in enumerate(chapters):
                start = chap['start_time']
                end = chapters[i + 1]['start_time'] if i + 1 < len(chapters) else total_duration_ms
                title = self._escape_ffmetadata(chap['title'])
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle={title}\n")

    def _sanitize_path_for_concat(self, path_str: str) -> str:
        # Escape a path for the concat demuxer: normalise backslashes to '/',
        # escape single quotes as '\'', and strip newlines.
        return path_str.replace('\\', '/').replace("'", "'\\''").replace('\n', '').replace('\r', '')

    def _cleanup_files(self, *paths):
        for p in paths:
            path_obj = Path(p)
            if path_obj.exists() and path_obj.is_file():
                try:
                    path_obj.unlink()
                except OSError as e:
                    self.log_message.emit(f"[WARNING] Could not delete temp file {p}: {e}", 'app')
