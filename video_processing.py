# video_processing.py
import sys
import subprocess
import tempfile
from pathlib import Path
import threading
import uuid
from PIL import Image, ImageDraw, ImageFont
import re
import json
import shutil
import shlex

from PySide6.QtCore import QObject, Signal, Slot, QProcess
import fitz

from models import ProjectModel, Slide, ProjectParameters
import config
from utils import resolve_resource_path, get_ffprobe_path, get_ffmpeg_path
from ffmpeg_builder import FFmpegCommandBuilder
from slide_processor import SlideProcessorFactory

class ProcessingCanceled(Exception):
    pass

class VideoProcessor(QObject):
    progress_updated = Signal(int)
    log_message = Signal(str, str)
    video_finished = Signal(bool, str)
    preview_finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._is_canceled = False
        self.active_processes = set()
        self.process_lock = threading.Lock()
        self.watermark_path: Path | None = None
        self._current_step = 0
        self._total_steps = 1
        self._is_verbose = False

    def cancel(self):
        self._is_canceled = True
        
        procs_to_kill = []
        with self.process_lock:
            procs_to_kill = list(self.active_processes)

        for process in procs_to_kill:
            try:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(5000)
            except Exception:
                pass
            
    @Slot(ProjectModel, bool)
    def start_video_creation(self, project_model: ProjectModel, is_verbose: bool):
        self._is_verbose = is_verbose
        success, message = self.run_video_creation(project_model)
        self.video_finished.emit(success, message)

    @Slot(ProjectModel, int, Path, bool, bool)
    def start_preview_creation(self, project_model: ProjectModel, slide_index: int, pdf_path: Path, is_verbose: bool, include_intervals: bool):
        self._is_verbose = is_verbose
        success, message = self.run_preview_creation(project_model, slide_index, pdf_path, include_intervals)
        self.preview_finished.emit(success, message)
        
    def register_process(self, process: QProcess):
        with self.process_lock:
            self.active_processes.add(process)

    def unregister_process(self, process: QProcess):
        with self.process_lock:
            self.active_processes.discard(process)

    def _run_subprocess(self, command_list: list[str], capture_output=False, timeout_sec=None):
        if self._is_canceled:
            raise ProcessingCanceled("Operation was canceled before starting the process.")

        self.log_message.emit(f"Running command: {' '.join(shlex.quote(str(arg)) for arg in command_list)}", 'app')

        process = QProcess()
        self.register_process(process)

        process.setProcessChannelMode(QProcess.MergedChannels)
        
        output_chunks = []
        
        def handle_output():
            data = process.readAll().data().decode('utf-8', errors='replace')
            if data:
                output_chunks.append(data)
                if not capture_output:
                    self.log_message.emit(data.strip(), "ffmpeg")
        
        process.readyRead.connect(handle_output)

        try:
            process.start(command_list[0], command_list[1:])

            if not process.waitForStarted(config.PROCESS_START_TIMEOUT_MS):
                raise RuntimeError("Process failed to start.")

            timeout_ms = (timeout_sec * 1000) if timeout_sec is not None else config.FFMPEG_ENCODE_TIMEOUT_MS
            finished_normally = process.waitForFinished(timeout_ms)

            handle_output()
            combined_output = "".join(output_chunks)

            if self._is_canceled:
                raise ProcessingCanceled()

            if not finished_normally:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(5000)
                raise TimeoutError(f"Process timed out after {timeout_ms / 1000} seconds.")

            exit_code = process.exitCode()
            exit_status = process.exitStatus()
            
            if exit_code != 0 or exit_status != QProcess.NormalExit:
                error_string = process.errorString()
                raise Exception(f"Command exited with status {exit_code} and error '{error_string}'.\nOutput:\n{combined_output}")

            return combined_output

        finally:
            self.unregister_process(process)

    def _create_ffmpeg_builder(self) -> FFmpegCommandBuilder:
        builder = FFmpegCommandBuilder()
        if self._is_verbose:
            builder.add_global_options('-loglevel', 'info')
        return builder

    def run_video_creation(self, project_model: ProjectModel):
        self._is_canceled = False
        self.watermark_path = None
        
        final_video_path = project_model.output_folder / f"{project_model.parameters.filename_input}.mp4"
        temp_video_path = final_video_path.with_name(final_video_path.stem + ".tmp" + final_video_path.suffix)
        output_dir = project_model.output_folder or Path(".")
        delete_temp = project_model.parameters.delete_temp_checkbox
        
        try:
            if delete_temp:
                with tempfile.TemporaryDirectory(dir=output_dir, prefix="movie-temp-", ignore_cleanup_errors=True) as temp_dir:
                    self._run_logic(project_model, Path(temp_dir), temp_video_path)
            else:
                temp_dir = tempfile.mkdtemp(dir=output_dir, prefix="movie-temp-")
                self.log_message.emit(f"Temporary files are being kept in: {temp_dir}", 'app')
                self._run_logic(project_model, Path(temp_dir), temp_video_path)

            if not self._is_canceled:
                if final_video_path.exists():
                    final_video_path.unlink()
                shutil.move(str(temp_video_path), str(final_video_path))

                if project_model.parameters.export_youtube_chapters:
                    self.log_message.emit("Generating YouTube chapter file...", 'app')
                    try:
                        self._generate_youtube_chapter_file(project_model, final_video_path)
                        self.log_message.emit("YouTube chapter file generated successfully.", 'app')
                    except Exception as chap_e:
                        self.log_message.emit(f"[ERROR] Could not generate YouTube chapter file: {chap_e}", 'app')
                
                return (True, str(final_video_path))

        except ProcessingCanceled:
            if temp_video_path.exists():
                temp_video_path.unlink()
            self.log_message.emit("Video creation was canceled by user.", 'app')
            return (False, "Canceled by user.")
        except Exception as e:
            if temp_video_path.exists():
                temp_video_path.unlink()
            self.log_message.emit(f"[ERROR] An exception occurred: {e}", 'app')
            return (False, str(e))
        finally:
            self.watermark_path = None

    def run_preview_creation(self, project_model: ProjectModel, slide_index: int, pdf_path: Path, include_intervals: bool):
        self._is_canceled = False
        self.watermark_path = None
        
        base_filename = project_model.parameters.filename_input
        if not base_filename:
            base_filename = project_model.project_folder.name
        
        random_id = uuid.uuid4().hex[:8]
        preview_filename = f"{base_filename}-preview-slide{slide_index+1}-{random_id}.mp4"
        final_preview_path = project_model.output_folder / preview_filename
        
        delete_temp = project_model.parameters.delete_temp_checkbox
        output_dir = project_model.output_folder or Path(".")

        def _run_in_temp_dir(temp_folder: Path):
            if not delete_temp:
                self.log_message.emit(f"Temporary files for preview are being kept in: {temp_folder}", 'app')

            image_paths_cache = {}
            target_width = int(project_model.parameters.resolution.split('x')[0])

            params = project_model.parameters
            if params.add_watermark and params.watermark_text:
                try:
                    width, height = map(int, params.resolution.split('x'))
                    self.watermark_path = self._generate_watermark_image(params, width, height, temp_folder)
                except Exception as e:
                        self.log_message.emit(f"[WARNING] Could not generate watermark image for preview, skipping: {e}", 'app')

            self.progress_updated.emit(10)
            
            self.log_message.emit(f"Generating main video for slide {slide_index + 1}...", 'app')
            main_slide = project_model.slides[slide_index]
            codec_option = self._resolve_codec_option(project_model.parameters.codec, project_model.parameters.hardware_encoding)
            
            with fitz.open(pdf_path) as doc:
                if slide_index not in image_paths_cache:
                    image_paths_cache[slide_index] = self._render_single_page(doc, slide_index, target_width, temp_folder)
                main_image_path = image_paths_cache[slide_index]
                
                main_video_path = temp_folder / f"slide_{slide_index+1:03d}_main.mp4"
                
                slide_info = (slide_index, main_slide, project_model, {slide_index: main_image_path}, temp_folder, codec_option)
                self._generate_single_slide_video(slide_info, output_path=main_video_path)
                self.progress_updated.emit(40)
                if self._is_canceled: raise ProcessingCanceled()

                videos_to_concat = []
                
                if include_intervals and slide_index > 0:
                    self.log_message.emit(f"Generating interval before slide {slide_index + 1}...", 'app')
                    prev_slide_model = project_model.slides[slide_index - 1]
                    if prev_slide_model.interval_to_next > 0:
                        prev_slide_index = slide_index - 1
                        if prev_slide_index not in image_paths_cache:
                            image_paths_cache[prev_slide_index] = self._render_single_page(doc, prev_slide_index, target_width, temp_folder)
                        prev_slide_frame = image_paths_cache[prev_slide_index]

                        start_frame_of_main = image_paths_cache[slide_index]

                        interval_before_path = self._create_interval_for_preview(
                            project_model, prev_slide_frame, start_frame_of_main,
                            prev_slide_model, temp_folder, codec_option, f"before_{slide_index}"
                        )
                        videos_to_concat.append(interval_before_path)
                
                videos_to_concat.append(main_video_path)
                self.progress_updated.emit(70)

                if include_intervals and slide_index < len(project_model.slides) - 1:
                    self.log_message.emit(f"Generating interval after slide {slide_index + 1}...", 'app')
                    if main_slide.interval_to_next > 0:
                        end_frame_of_main = image_paths_cache[slide_index]

                        next_slide_index = slide_index + 1
                        if next_slide_index not in image_paths_cache:
                            image_paths_cache[next_slide_index] = self._render_single_page(doc, next_slide_index, target_width, temp_folder)
                        next_slide_frame = image_paths_cache[next_slide_index]

                        interval_after_path = self._create_interval_for_preview(
                            project_model, end_frame_of_main, next_slide_frame,
                            main_slide, temp_folder, codec_option, f"after_{slide_index}"
                        )
                        videos_to_concat.append(interval_after_path)
            
            if len(videos_to_concat) > 1:
                self.log_message.emit("Concatenating preview parts...", 'app')
                concat_list_path = temp_folder / 'concat_preview_list.txt'
                with concat_list_path.open('w', encoding='utf-8') as f:
                    for video in videos_to_concat:
                        f.write(f"file '{self._sanitize_path_for_concat(str(video))}'\n")
                
                # --- Attempt 1: Fast concatenation with stream copy ---
                try:
                    self.log_message.emit("Attempting fast concatenation (stream copy)...", 'app')
                    builder_copy = self._create_ffmpeg_builder()
                    concat_command_copy = (builder_copy
                        .add_input(concat_list_path, ['-f', 'concat', '-safe', '0'])
                        .set_output(final_preview_path, ['-c', 'copy', '-movflags', '+faststart'])
                        .build())
                    self._run_subprocess(concat_command_copy)
                    self.log_message.emit("Fast concatenation successful.", 'app')

                except Exception as e:
                    # --- Attempt 2: Fallback to safer re-encoding ---
                    self.log_message.emit(f"[WARNING] Fast concatenation failed. Retrying with full re-encode... Reason: {e}", 'app')
                    
                    video_opts = self._get_video_encoding_options(project_model.parameters, pass_num=1, is_single_pass_override=True)
                    audio_opts = self._get_common_audio_options(project_model.parameters)
                    fps = project_model.parameters.fps
                    
                    builder_recode = self._create_ffmpeg_builder()
                    concat_command_recode = (builder_recode
                        .add_input(concat_list_path, ['-f', 'concat', '-safe', '0'])
                        .set_output(final_preview_path, 
                            ['-c:v', codec_option] + video_opts + 
                            audio_opts + 
                            ['-r', str(fps), '-movflags', '+faststart']
                        )
                        .build())
                    
                    self._run_subprocess(concat_command_recode)
                    self.log_message.emit("Re-encode concatenation successful.", 'app')

            else:
                shutil.move(str(main_video_path), str(final_preview_path))

            self.progress_updated.emit(100)
        
        try:
            if delete_temp:
                with tempfile.TemporaryDirectory(dir=output_dir, prefix="preview-temp-", ignore_cleanup_errors=True) as temp_dir_str:
                    _run_in_temp_dir(Path(temp_dir_str))
            else:
                temp_dir_str = tempfile.mkdtemp(dir=output_dir, prefix="preview-temp-")
                _run_in_temp_dir(Path(temp_dir_str))
            
            return (True, str(final_preview_path))

        except ProcessingCanceled:
            self.log_message.emit("Preview creation was canceled by user.", 'app')
            return (False, "Canceled by user.")
        except Exception as e:
            self.log_message.emit(f"[ERROR] An exception occurred during preview creation: {e}", 'app')
            return (False, str(e))
        finally:
            self.watermark_path = None

    def _generate_single_slide_video(self, slide_info: tuple, output_path: Path = None) -> tuple[int, Path]:
        if self._is_canceled:
            raise ProcessingCanceled()

        i = slide_info[0]
        serial = f"{i+1:03d}"
        slide_video_path = output_path if output_path else slide_info[4] / f"slide_{serial}.mp4"
        
        self.log_message.emit(f"Generating segment for slide {i+1}...", 'app')
        
        processor = SlideProcessorFactory.get_processor(self, slide_info, slide_video_path)
        processor.process()

        return i, slide_video_path

    def _create_interval_for_preview(self, model, prev_frame_path, next_frame_path, slide_with_settings, temp_dir, codec, index_suffix):
        interval_duration = slide_with_settings.interval_to_next
        transition_name = slide_with_settings.transition_to_next
        output_path = temp_dir / f"interval_preview_{index_suffix}.mp4"
        
        ffmpeg_keyword = config.TRANSITION_MAPPINGS.get(transition_name)
        if not ffmpeg_keyword:
            half_interval = interval_duration / 2
            prev_ext, next_ext = self._create_extended_videos_from_frames(model, prev_frame_path, next_frame_path, half_interval, codec, temp_dir, index_suffix)
            concat_list = temp_dir / f'concat_interval_preview_{index_suffix}.txt'
            with concat_list.open('w', encoding='utf-8') as f:
                f.write(f"file '{self._sanitize_path_for_concat(str(prev_ext))}'\n")
                f.write(f"file '{self._sanitize_path_for_concat(str(next_ext))}'\n")

            builder = self._create_ffmpeg_builder()
            concat_command = (builder.add_input(concat_list, ['-f', 'concat', '-safe', '0'])
                                     .set_output(output_path, ['-codec', 'copy'])
                                     .build())
            self._run_subprocess(concat_command)
            self._cleanup_files(prev_ext, next_ext, concat_list)
        else:
            prev_ext, next_ext = self._create_extended_videos_from_frames(model, prev_frame_path, next_frame_path, interval_duration, codec, temp_dir, index_suffix)
            filter_complex = f"[0:v][1:v]xfade=transition={ffmpeg_keyword}:duration={interval_duration}:offset=0[v];[0:a][1:a]acrossfade=d={interval_duration}:curve1=tri:curve2=tri[a]"
            
            video_opts = self._get_video_encoding_options(model.parameters, pass_num=1, is_single_pass_override=True)
            audio_opts = self._get_common_audio_options(model.parameters)
            outputs = ['-map', '[v]', '-map', '[a]', '-c:v', codec] + video_opts + audio_opts + ['-r', str(model.parameters.fps)]

            builder = self._create_ffmpeg_builder()
            command = (builder.add_input(prev_ext)
                              .add_input(next_ext)
                              .set_filter_complex(filter_complex)
                              .set_output(output_path, outputs)
                              .build())
            self._run_subprocess(command)
            self._cleanup_files(prev_ext, next_ext)

        return output_path

    def _create_extended_videos_from_frames(self, model, prev_frame, next_frame, duration, codec, temp_dir, index_suffix):
        params = model.parameters
        fps = params.fps
        res = params.resolution
        width, height = map(int, res.split('x'))

        prev_ext = temp_dir / f'prev_extended_preview_{index_suffix}.mp4'
        next_ext = temp_dir / f'next_extended_preview_{index_suffix}.mp4'
        
        video_opts = self._get_video_encoding_options(params, pass_num=1, is_single_pass_override=True)
        audio_opts = self._get_common_audio_options(params)
        
        for frame_path, video_path in [(prev_frame, prev_ext), (next_frame, next_ext)]:
            builder = self._create_ffmpeg_builder()
            builder.add_input(frame_path, ['-loop', '1'])
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi', '-t', str(duration)])

            final_video_stream = "[0:v]"
            filter_chains = [f"{final_video_stream}scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v_scaled]"]
            final_video_stream = "[v_scaled]"

            if self.watermark_path:
                watermark_input_index = 2
                builder.add_input(self.watermark_path)
                filter_chains.append(f"{final_video_stream}[{watermark_input_index}:v]overlay=x=0:y=0[v_out]")
                final_video_stream = "[v_out]"
            
            maps = ['-map', final_video_stream, '-map', '1:a']

            filters = ";".join(filter_chains)
            builder.set_filter_complex(filters)
            
            output_options = maps + ['-c:v', codec] + video_opts + audio_opts + ['-t', str(duration), '-r', str(fps)]
            builder.set_output(video_path, output_options)
            
            cmd = builder.build()
            self._run_subprocess(cmd)
            
        return prev_ext, next_ext

    def _generate_single_transition_video(self, transition_info: tuple) -> tuple[int, Path | None]:
        i, slide, prev_video, next_video, project_model, temp_folder, codec_option = transition_info

        if self._is_canceled:
            raise ProcessingCanceled()

        interval_video_path = temp_folder / f"interval_{i}.mp4"
        self.log_message.emit(f"Generating transition between slide {i+1} and {i+2}...", 'app')

        self._create_transition_video(
            project_model,
            prev_video,
            next_video,
            slide.interval_to_next,
            slide.transition_to_next,
            interval_video_path,
            codec_option,
            i 
        )

        if interval_video_path.exists():
            return i, interval_video_path
        
        return i, None

    def _setup_processing(self, params: ProjectParameters, temp_folder: Path) -> str:
        codec_option = self._resolve_codec_option(params.codec, params.hardware_encoding)
        
        if params.add_watermark and params.watermark_text:
            try:
                res_str = params.resolution
                width, height = map(int, res_str.split('x'))
                self.watermark_path = self._generate_watermark_image(params, width, height, temp_folder)
            except Exception as e:
                self.log_message.emit(f"[WARNING] Could not generate watermark image, skipping: {e}", 'app')
                self.watermark_path = None
        return codec_option

    def _render_single_page(self, doc: fitz.Document, page_num: int, target_width: int, temp_folder: Path) -> Path:
        if self._is_canceled:
            raise ProcessingCanceled()

        page = doc.load_page(page_num)
        if page.rect.width == 0:
            raise Exception(f"PDF page {page_num + 1} has zero width.")

        zoom = target_width / page.rect.width
        matrix = fitz.Matrix(zoom, zoom)
        
        pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
        
        out_path = temp_folder / f"page_{page_num + 1:03d}.png"
        pix.save(str(out_path))
        return out_path

    def _render_pdf_pages(self, project_model: ProjectModel, temp_folder: Path) -> dict[int, Path]:
        if not project_model.project_folder or not project_model.project_folder.is_dir():
            raise ValueError("Project folder is not set or is not a valid directory.")

        pdf_path = next(project_model.project_folder.glob('*.[pP][dD][fF]'), None)
        if not pdf_path:
            raise FileNotFoundError("Could not find a PDF file in the project folder.")

        self.log_message.emit("Rendering PDF pages to PNGs...", 'app')

        res_str = project_model.parameters.resolution
        target_width, _ = map(int, res_str.split('x'))
        image_paths_dict = {}

        with fitz.open(pdf_path) as doc:
            for page_num, page in enumerate(doc):
                if self._is_canceled:
                    raise ProcessingCanceled()

                if page.rect.width == 0:
                    raise Exception(f"PDF page {page_num + 1} has zero width.")

                zoom = target_width / page.rect.width
                matrix = fitz.Matrix(zoom, zoom)
                
                pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
                
                out_path = temp_folder / f"page_{page_num + 1:03d}.png"
                pix.save(str(out_path))
                image_paths_dict[page_num] = out_path

        return image_paths_dict


    def _generate_slide_videos(self, project_model: ProjectModel, image_paths_dict: dict, temp_folder: Path, codec_option: str) -> list[Path]:
        self.log_message.emit("Generating individual slide videos...", 'app')
        slide_videos_map = {}
        for i, slide in enumerate(project_model.slides):
            if self._is_canceled: raise ProcessingCanceled()
            try:
                slide_info = (i, slide, project_model, image_paths_dict, temp_folder, codec_option)
                index, slide_video_path = self._generate_single_slide_video(slide_info)
                slide_videos_map[index] = slide_video_path
                self._current_step += 1
                self.progress_updated.emit(int(self._current_step / self._total_steps * 100))
                self.log_message.emit(f"Finished segment for slide {index + 1}.", 'app')
            except ProcessingCanceled:
                raise
            except Exception as e:
                self.log_message.emit(f"[ERROR] A critical error occurred while generating slide {i + 1}: {e}", 'app')
                raise
        return [slide_videos_map[i] for i in range(len(project_model.slides))]

    def _generate_transition_videos(self, project_model: ProjectModel, slide_videos: list[Path], temp_folder: Path, codec_option: str) -> dict[int, Path]:
        self.log_message.emit("Generating transitions...", 'app')
        transition_videos_map = {}
        total_slides = len(project_model.slides)
        for i in range(total_slides - 1):
            if self._is_canceled: raise ProcessingCanceled()
            
            slide = project_model.slides[i]
            if slide.interval_to_next > 0:
                try:
                    transition_info = (i, slide, slide_videos[i], slide_videos[i+1], project_model, temp_folder, codec_option)
                    index, transition_video_path = self._generate_single_transition_video(transition_info)
                    
                    if transition_video_path:
                        transition_videos_map[index] = transition_video_path
                    self._current_step += 1
                    self.progress_updated.emit(int(self._current_step / self._total_steps * 100))
                    self.log_message.emit(f"Finished transition for slide {index + 1}.", 'app')
                except ProcessingCanceled:
                    raise
                except Exception as e:
                    self.log_message.emit(f"[ERROR] A critical error occurred while generating a transition for slide {i + 1}: {e}", 'app')
                    raise
        return transition_videos_map

    def _concatenate_videos(self, slide_videos: list[Path], transition_videos_map: dict, temp_folder: Path) -> Path:
        self.log_message.emit("Final concatenation...", 'app')
        final_video_list = []
        for i, slide_video in enumerate(slide_videos):
            final_video_list.append(slide_video)
            if i in transition_videos_map:
                final_video_list.append(transition_videos_map[i])

        concat_list_path = temp_folder / 'concat_list.txt'
        with concat_list_path.open('w', encoding='utf-8') as f:
            for video in final_video_list:
                f.write(f"file '{self._sanitize_path_for_concat(str(video))}'\n")
        
        temp_concat_video = temp_folder / "final_concat.mp4"
        
        builder_concat = FFmpegCommandBuilder()
        concat_command = (builder_concat.add_input(concat_list_path, ['-f', 'concat', '-safe', '0'])
                                        .set_output(temp_concat_video, ['-c', 'copy', '-movflags', '+faststart'])
                                        .build())
        self._run_subprocess(concat_command)
        return temp_concat_video

    def _finalize_video(self, project_model: ProjectModel, concatenated_video_path: Path, final_output_path: Path, temp_folder: Path):
        params = project_model.parameters
        video_to_process = concatenated_video_path

        if params.normalize_loudness:
            normalized_video_path = temp_folder / "normalized.mp4"
            audio_opts = self._get_common_audio_options(params)
            
            if params.normalize_loudness_mode == "1-Pass (Faster)":
                self.log_message.emit("Applying loudness normalization (1-Pass)...", 'app')
                loudnorm_filter = "loudnorm"
            else:
                self.log_message.emit("Analyzing audio for 2-Pass loudness normalization...", 'app')
                try:
                    loudnorm_params = self._get_loudnorm_params(video_to_process)
                    self.log_message.emit("Applying loudness normalization (2-Pass)...", 'app')
                    loudnorm_filter = f"loudnorm={loudnorm_params}"
                except Exception as e:
                    self.log_message.emit(f"[ERROR] Loudness analysis failed: {e}. Falling back to 1-Pass.", 'app')
                    loudnorm_filter = "loudnorm"

            builder_norm = FFmpegCommandBuilder()
            norm_command = (builder_norm.add_input(video_to_process)
                                        .set_output(normalized_video_path, ['-c:v', 'copy', '-af', loudnorm_filter] + audio_opts + ['-movflags', '+faststart'])
                                        .build())
            self._run_subprocess(norm_command)
            video_to_process = normalized_video_path
        
        if any(s.chapter_title for s in project_model.slides):
            self.log_message.emit("Embedding chapter information...", 'app')
            ffmetadata_path = temp_folder / 'ffmetadata.txt'
            self._generate_ffmetadata(project_model, ffmetadata_path)
            
            builder = self._create_ffmpeg_builder()
            command = (builder.add_input(video_to_process)
                              .add_input(ffmetadata_path)
                              .set_output(final_output_path, ['-map_metadata', '1', '-codec', 'copy', '-movflags', '+faststart'])
                              .build())
            self._run_subprocess(command)
        else:
            if final_output_path.exists(): final_output_path.unlink()
            shutil.move(str(video_to_process), str(final_output_path))
        
        self._current_step += 1
        self.progress_updated.emit(int(self._current_step / self._total_steps * 100))

    def _run_logic(self, project_model: ProjectModel, temp_folder: Path, output_video_path: Path):
        total_slides = len(project_model.slides)
        self._total_steps = total_slides + max(0, total_slides - 1) + 1  # Slides + Transitions + Final Step
        self._current_step = 0

        # Step 1: Setup
        codec_option = self._setup_processing(project_model.parameters, temp_folder)

        # Step 2: Render PDF pages
        image_paths_dict = self._render_pdf_pages(project_model, temp_folder)

        # Step 3: Generate video for each slide
        slide_videos = self._generate_slide_videos(project_model, image_paths_dict, temp_folder, codec_option)

        # Step 4: Generate transitions between slides
        transition_videos_map = self._generate_transition_videos(project_model, slide_videos, temp_folder, codec_option)

        # Step 5: Concatenate all video clips
        concatenated_video = self._concatenate_videos(slide_videos, transition_videos_map, temp_folder)

        # Step 6: Apply final processing (normalization, chapters)
        self._finalize_video(project_model, concatenated_video, output_video_path, temp_folder)
        

    def _combine_image_audio(self, model, image, audio, output, codec, slide):
        self._process_slide(model, image, output, codec, slide, audio_path=audio)

    def _combine_image_silent_audio(self, model, image, duration, output, codec, slide):
        self._process_slide(model, image, output, codec, slide, duration=duration)

    def _execute_encoding(self, builder: FFmpegCommandBuilder, output_path: Path, params: "ProjectParameters", codec: str):
        is_2pass_supported = 'videotoolbox' not in codec
        is_2pass = (params.encoding_pass == config.ENCODING_PASSES["TWO_PASS"]
                    and params.encoding_mode != config.ENCODING_MODES["QUALITY"] 
                    and is_2pass_supported)
        
        base_output_options = builder.output_options.copy()
        if is_2pass:
            pass_log_prefix = str(output_path.with_suffix(''))
            null_device = "NUL" if sys.platform == "win32" else "/dev/null"

            self.log_message.emit(f"Running 1st pass for {output_path.name}...", 'app')
            video_opts_1 = self._get_video_encoding_options(params, pass_num=1)
            
            pass1_opts = base_output_options + ['-c:v', codec] + video_opts_1 + ['-an', '-passlogfile', pass_log_prefix]
            
            builder.set_output(null_device, pass1_opts)
            self._run_subprocess(builder.build())
            if self._is_canceled: return

            self.log_message.emit(f"Running 2nd pass for {output_path.name}...", 'app')
            video_opts_2 = self._get_video_encoding_options(params, pass_num=2)
            audio_opts = self._get_common_audio_options(params)
            
            pass2_opts = base_output_options + ['-c:v', codec] + video_opts_2 + audio_opts + ['-passlogfile', pass_log_prefix]
            builder.set_output(output_path, pass2_opts)
            self._run_subprocess(builder.build())

        else:
            self.log_message.emit(f"Running 1-pass encoding for {output_path.name}...", 'app')
            video_opts = self._get_video_encoding_options(params, pass_num=1)
            audio_opts = self._get_common_audio_options(params)
            
            final_opts = base_output_options + ['-c:v', codec] + video_opts + audio_opts
            builder.set_output(output_path, final_opts)
            self._run_subprocess(builder.build())

    def _process_slide(self, model, image, output, codec, slide: Slide, audio_path=None, duration=None):
        params = model.parameters
        res = params.resolution
        fps = params.fps
        width, height = map(int, res.split('x'))

        builder = self._create_ffmpeg_builder()
        builder.add_input(image, ['-loop', '1'])
        
        final_video_stream = "[0:v]"
        watermark_input_index = -1

        if self.watermark_path:
            builder.add_input(self.watermark_path)
            watermark_input_index = len(builder.inputs) - 1

        filter_chains = [f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v_scaled]"]
        final_video_stream = "[v_scaled]"
        
        if watermark_input_index != -1:
            filter_chains.append(f"{final_video_stream}[{watermark_input_index}:v]overlay=x=0:y=0[v_out]")
            final_video_stream = "[v_out]"

        builder.set_filter_complex(";".join(filter_chains))

        maps = ['-map', final_video_stream]
        audio_input_index = -1
        
        if audio_path:
            builder.add_input(audio_path)
            audio_input_index = len(builder.inputs) - 1
            audio_duration = self._get_media_duration(audio_path)
            duration_option = ['-t', str(audio_duration)]
        else:
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi', '-t', str(duration or 1)])
            audio_input_index = len(builder.inputs) - 1
            duration_option = ['-t', str(duration or 1)]

        if slide.filename and slide.filename != config.SILENT_MATERIAL_NAME:
            if slide.audio_streams and 0 <= slide.selected_audio_stream_index < len(slide.audio_streams):
                selected_stream = slide.audio_streams[slide.selected_audio_stream_index]
                maps.extend(['-map', f"{audio_input_index}:{selected_stream['index']}"])
            else:
                maps.extend(['-map', f'{audio_input_index}:a?'])
        else:
            maps.extend(['-map', f'{audio_input_index}:a'])
            
        other_outputs = duration_option + ['-r', str(fps), '-shortest']
        builder.output_options = maps + other_outputs
        
        self._execute_encoding(builder, output, params, codec)

    def _overlay_video_on_image(self, model, image, video, output, codec, slide: Slide):
        params = model.parameters
        res = params.resolution
        fps = params.fps
        width, height = map(int, res.split('x'))
        pos_info = config.VIDEO_POSITION_MAP.get(slide.video_position, config.VIDEO_POSITION_MAP['Center'])
        
        scale_percent = slide.video_scale / 100.0
        
        video_width, video_height = slide.tech_info.get('width', 0), slide.tech_info.get('height', 0)
        dar_str = slide.tech_info.get('dar')
        
        rotation = slide.tech_info.get('rotate')
        if rotation in ["90", "270", "-90"]:
            video_width, video_height = video_height, video_width

        aspect_ratio = 16/9
        if video_height > 0:
            aspect_ratio = video_width / video_height
            if dar_str and ':' in dar_str and dar_str != '0:1':
                try:
                    num, den = map(int, dar_str.split(':'))
                    if den > 0: aspect_ratio = num / den
                except (ValueError, TypeError): pass
        
        target_pinp_height_float = height * scale_percent
        final_pinp_width_float = target_pinp_height_float * aspect_ratio

        target_pinp_height = round(target_pinp_height_float / 2) * 2
        final_pinp_width = round(final_pinp_width_float / 2) * 2

        filter_chains = [f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[bg]"]
        
        fg_stream = "[1:v]"
        
        current_stream = fg_stream

        # 1. pre-filter
        pre_filters = []
        if slide.tech_info.get('is_interlaced'):
            pre_filters.append("yadif")

        rotation = slide.tech_info.get('rotate')
        if rotation:
            transpose_map = {"90": "1", "180": "2", "270": "0", "-90": "0"}
            if rotation in transpose_map:
                pre_filters.append(f"transpose={transpose_map[rotation]}")

        if pre_filters:
            filter_chains.append(f"{current_stream}{','.join(pre_filters)}[fg_pre]")
            current_stream = "[fg_pre]"

        # 2. filter: Processing
        processing_filters = []
        if "HFlip" in slide.video_effects: processing_filters.append("hflip")
        if "VFlip" in slide.video_effects: processing_filters.append("vflip")
        if "Blur" in slide.video_effects: processing_filters.append("boxblur=5")
        if "Pixelate" in slide.video_effects: processing_filters.append("scale=iw/16:ih/16,scale=iw*16:ih*16:flags=neighbor")
        
        if processing_filters:
            filter_chains.append(f"{current_stream}{','.join(processing_filters)}[fg_proc]")
            current_stream = "[fg_proc]"

        # 3. filter: Color
        color_filters = []
        if "Grayscale" in slide.video_effects: color_filters.append("format=gray")
        elif "Sepia" in slide.video_effects: color_filters.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
        elif "Negative" in slide.video_effects: color_filters.append("negate")

        if color_filters:
            filter_chains.append(f"{current_stream}{','.join(color_filters)}[fg_color]")
            current_stream = "[fg_color]"

        # 4. filter: Shape
        shape_filters = []
        if "Circle" in slide.video_effects:
            video_w, video_h = slide.tech_info.get('width', 0), slide.tech_info.get('height', 0)
            if video_w > 0 and video_h > 0:
                radius = min(video_w, video_h) / 2
                shape_filters.append(f"format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(pow(X-W/2,2)+pow(Y-H/2,2),pow({radius},2)),255,0)'")
        elif "Chroma" in slide.video_effects:
            shape_filters.append(f"chromakey=color=green:similarity={config.CHROMA_KEY_SIMILARITY}:blend={config.CHROMA_KEY_BLEND}")
        elif "Vignette" in slide.video_effects:
            shape_filters.append("vignette=eval=frame")
        
        if shape_filters:
            filter_chains.append(f"{current_stream}{','.join(shape_filters)}[fg_shape]")
            current_stream = "[fg_shape]"
        
        fg_stream = current_stream

        filter_chains.extend([
            f"{fg_stream}scale={final_pinp_width}:{target_pinp_height}[fg_scaled]",
            f"[bg][fg_scaled]overlay=x={pos_info['x']}:y={pos_info['y']}:eof_action=pass[overlaid]"
        ])
        
        final_stream_name = "[overlaid]"
        
        builder = self._create_ffmpeg_builder()
        builder.add_input(image, ['-loop', '1', '-t', str(slide.duration)])
        builder.add_input(video, ['-noautorotate', '-vsync', 'cfr'])

        if self.watermark_path:
            builder.add_input(self.watermark_path)
            filter_chains.append(f"{final_stream_name}[2:v]overlay=x=0:y=0[out]")
            final_stream_name = "[out]"

        filters = ";".join(filter_chains)
        builder.set_filter_complex(filters)
        
        maps = ['-map', final_stream_name]

        if slide.audio_streams:
            if 0 <= slide.selected_audio_stream_index < len(slide.audio_streams):
                selected_stream = slide.audio_streams[slide.selected_audio_stream_index]
                maps.extend(['-map', f"1:{selected_stream['index']}"])
            else:
                maps.extend(['-map', '1:a?'])
        else:
            silent_audio_input_index = len(builder.inputs)
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi'])
            maps.extend(['-map', f'{silent_audio_input_index}:a'])
        
        other_opts = ['-r', str(fps), '-shortest']
        builder.output_options = maps + other_opts
        
        self._execute_encoding(builder, output, params, codec)

    def _create_transition_video(self, model, prev, next_vid, duration, trans, output_path, codec, index):
        ffmpeg_keyword = config.TRANSITION_MAPPINGS.get(trans)
        if not ffmpeg_keyword:
            self._create_simple_interval_video(model, prev, next_vid, duration, output_path, codec, index)
            return
        temp_dir = output_path.parent
        prev_ext, next_ext, prev_frame, next_frame = self._create_extended_videos(model, prev, next_vid, duration, codec, temp_dir, index)
        
        filter_complex = f"[0:v][1:v]xfade=transition={ffmpeg_keyword}:duration={duration}:offset=0[v];[0:a][1:a]acrossfade=d={duration}:curve1=tri:curve2=tri[a]"
        
        video_opts = self._get_video_encoding_options(model.parameters, pass_num=1, is_single_pass_override=True)
        audio_opts = self._get_common_audio_options(model.parameters)
        outputs = ['-map', '[v]', '-map', '[a]', '-c:v', codec] + video_opts + audio_opts + ['-r', str(model.parameters.fps)]
        
        builder = self._create_ffmpeg_builder()
        command = (builder.add_input(prev_ext)
                          .add_input(next_ext)
                          .set_filter_complex(filter_complex)
                          .set_output(output_path, outputs)
                          .build())
        self._run_subprocess(command)
        self._cleanup_files(prev_frame, next_frame, prev_ext, next_ext)

    def _create_simple_interval_video(self, model, prev_slide, next_slide, interval_duration, output_path, codec, index):
        half_interval = interval_duration / 2
        temp_dir = output_path.parent
        prev_video, next_video, prev_frame, next_frame = self._create_extended_videos(model, prev_slide, next_slide, half_interval, codec, temp_dir, index)
        
        concat_list = temp_dir / f'concat_interval_{index}.txt'
        with concat_list.open('w', encoding='utf-8') as f:
            f.write(f"file '{self._sanitize_path_for_concat(str(prev_video))}'\n")
            f.write(f"file '{self._sanitize_path_for_concat(str(next_video))}'\n")

        builder = self._create_ffmpeg_builder()
        concat_command = (builder.add_input(concat_list, ['-f', 'concat', '-safe', '0'])
                                 .set_output(output_path, ['-codec', 'copy'])
                                 .build())
        self._run_subprocess(concat_command)
        self._cleanup_files(prev_frame, next_frame, prev_video, next_video, concat_list)

    def _create_extended_videos(self, model, prev, next_vid, duration, codec, temp_dir, index):
        params = model.parameters
        fps = params.fps
        res = params.resolution
        width, height = map(int, res.split('x'))
        prev_frame = temp_dir / f'prev_frame_{index}.png'
        next_frame = temp_dir / f'next_frame_{index}.png'

        builder_prev = FFmpegCommandBuilder()
        cmd_prev = (builder_prev.add_input(prev, ['-sseof', '-1'])
                                .set_output(prev_frame, ['-update', '1', '-vframes', '1'])
                                .build())
        self._run_subprocess(cmd_prev)

        builder_next = FFmpegCommandBuilder()
        cmd_next = (builder_next.add_input(next_vid)
                                .set_output(next_frame, ['-vframes', '1', '-update', '1'])
                                .build())
        self._run_subprocess(cmd_next)
        
        prev_ext = temp_dir / f'prev_extended_{index}.mp4'
        next_ext = temp_dir / f'next_extended_{index}.mp4'
        video_opts = self._get_video_encoding_options(params, pass_num=1, is_single_pass_override=True)
        audio_opts = self._get_common_audio_options(params)
        
        for frame, video in [(prev_frame, prev_ext), (next_frame, next_ext)]:
            builder = self._create_ffmpeg_builder()
            builder.add_input(frame, ['-loop', '1'])
            builder.add_input(config.SILENT_AUDIO_SOURCE, ['-f', 'lavfi', '-t', str(duration)])
            
            filter_str = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v_out]"
            builder.set_filter_complex(filter_str)

            output_options = ['-map', '[v_out]', '-map', '1:a', '-c:v', codec] + video_opts + audio_opts + ['-t', str(duration), '-r', str(fps)]
            
            command = builder.set_output(video, output_options).build()
            self._run_subprocess(command)
            
        return prev_ext, next_ext, prev_frame, next_frame
    
    def _get_media_duration(self, media_path: Path) -> float:
        try:
            command = [
                str(get_ffprobe_path()),
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(media_path)
            ]

            output = self._run_subprocess(
                command,
                capture_output=True,
                timeout_sec=config.FFPROBE_TIMEOUT_S
            )
            
            return float(output.strip())

        except Exception as e:
            self.log_message.emit(f"Could not get media duration for '{media_path.name}': {e}", 'app')
            return 0.0
            
    def _resolve_codec_option(self, codec, hw):
        if hw is not None:
            return config.CODEC_MAP.get(codec, {}).get(hw, '')
        return config.SOFTWARE_CODEC_MAP.get(codec, 'libx264')
    
    def _get_common_audio_options(self, params: "ProjectParameters"):
        return ['-c:a', 'aac', '-b:a', params.audio_bitrate, '-ar', params.audio_sample_rate, '-ac', str(params.audio_channels)]
    
    def _get_video_encoding_options(self, params: "ProjectParameters", pass_num: int = 1, is_single_pass_override: bool = False):
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

        if codec == 'libx264':
            options.extend(['-profile:v', 'high', '-level', '4.0', '-preset', 'medium'])
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
            options.extend(['-profile:v', 'main', '-preset', 'p5'])
        elif codec == 'av1_qsv':
            options.extend(['-profile:v', 'main', '-preset', 'medium'])
        elif codec == 'av1_amf':
            options.extend(['-profile:v', 'main', '-quality', 'quality'])

        if mode == config.ENCODING_MODES["QUALITY"]:
            if 'nvenc' in codec or 'qsv' in codec or 'amf' in codec:
                options.extend(['-qp', str(value)])
            else:
                options.extend(['-crf', str(value)])
        else:
            options.extend(['-b:v', f'{value}k'])
            if mode == config.ENCODING_MODES["VBR"]:
                maxrate = int(value * config.VBR_MAXRATE_MULTIPLIER)
                bufsize = int(value * config.VBR_BUFSIZE_MULTIPLIER)
                options.extend(['-maxrate', f'{maxrate}k', '-bufsize', f'{bufsize}k'])
        
        is_2pass_supported = 'videotoolbox' not in codec
        if not is_single_pass_override and params.encoding_pass == config.ENCODING_PASSES["TWO_PASS"] and mode != config.ENCODING_MODES["QUALITY"] and is_2pass_supported:
            options.extend(['-pass', str(pass_num)])

        return options

    def _get_loudnorm_params(self, media_path: Path) -> str:
        builder = self._create_ffmpeg_builder()
        builder.global_options = [opt for opt in builder.global_options if opt not in ['-loglevel', 'info']]
        
        command = (
            builder
                .add_input(media_path)
                .set_output('-', ['-af', 'loudnorm=print_format=json', '-f', 'null'])
                .build()
        )
        
        LOUDNORM_TIMEOUT_SEC = 600
        output_str = self._run_subprocess(command, capture_output=True, timeout_sec=LOUDNORM_TIMEOUT_SEC)
        
        json_match = re.search(r'(\{[\s\S]*\})', output_str, re.MULTILINE)
        if not json_match:
            self.log_message.emit(f"[WARNING] Could not find loudnorm JSON data in FFmpeg output. Full output:\n{output_str}", 'app')
            raise ValueError("Could not find loudnorm JSON data in FFmpeg output.")
            
        stats = json.loads(json_match.group(1))

        params = (
            f"I=-23.0:LRA=7.0:TP=-2.0:"
            f"measured_I={stats['input_i']}:"
            f"measured_LRA={stats['input_lra']}:"
            f"measured_TP={stats['input_tp']}:"
            f"measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}"
        )
        return params

    def _generate_watermark_image(self, params: ProjectParameters, width: int, height: int, temp_folder: Path) -> Path:
        font_file = config.BUNDLED_FONTS.get(params.watermark_fontfamily)
        if not font_file:
            raise FileNotFoundError(f"Font definition for '{params.watermark_fontfamily}' not found.")
        
        font_path = resolve_resource_path(Path('fonts') / font_file)
        if not font_path.exists():
            raise FileNotFoundError(f"Font file not found: {font_path}")

        font_size_px = int(height * (params.watermark_fontsize / 100))
        font = ImageFont.truetype(str(font_path), font_size_px)
        
        rgba_color = config.WATERMARK_COLOR_OPTIONS_RGBA.get(params.watermark_color, (255, 255, 255))
        fill_color = (*rgba_color, int(255 * (params.watermark_opacity / 100)))

        bbox = font.getbbox(params.watermark_text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        text_offset_y = bbox[1]
        stamp_canvas_size = (text_width, text_height)
        text_draw_pos = (0, -text_offset_y)

        stamp_img = Image.new('RGBA', stamp_canvas_size, (0,0,0,0))
        draw_stamp = ImageDraw.Draw(stamp_img)
        draw_stamp.text(text_draw_pos, params.watermark_text, font=font, fill=fill_color)

        if params.watermark_rotation != "None":
            angle = -int(params.watermark_rotation)
            stamp_img = stamp_img.rotate(angle, expand=True, resample=Image.BICUBIC)

        final_image = Image.new('RGBA', (width, height), (0,0,0,0))
        stamp_w, stamp_h = stamp_img.size

        if params.watermark_tile:
            base_spacing_x = int(text_width * 0.8)
            base_spacing_y = int(text_height * 2.0)

            step_x = text_width + base_spacing_x
            step_y = text_height + base_spacing_y

            if params.watermark_rotation != "None":
                step_y = int(text_height * 1.5)

            step_x = max(1, step_x)
            step_y = max(1, step_y)
            
            for y in range(-stamp_h, height + stamp_h, step_y):
                for x in range(-stamp_w, width + stamp_w, step_x):
                    x_offset = (step_x // 2) if (y // step_y) % 2 != 0 else 0
                    final_image.paste(stamp_img, (x + x_offset, y), stamp_img)
        else:
            pos_x = (width - stamp_w) // 2
            pos_y = (height - stamp_h) // 2
            final_image.paste(stamp_img, (pos_x, pos_y), stamp_img)
        
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
        chapters = []
        current_time = 0.0
        for i, slide in enumerate(model.slides):
            if slide.chapter_title:
                chapters.append({'title': slide.chapter_title, 'start_time': current_time})
            
            current_time += slide.duration
            if i < len(model.slides) - 1:
                current_time += slide.interval_to_next
        
        if not chapters or len(chapters) < 3 or chapters[0]['start_time'] != 0.0:
            raise ValueError("Chapter data does not meet YouTube requirements. Please re-validate the project.")

        chapter_filename = video_path.with_name(f"{video_path.stem}-chapter-for-youtube.txt")
        
        with open(chapter_filename, 'w', encoding='utf-8') as f:
            for chap in chapters:
                timestamp = self._format_seconds_to_hhmmss(chap['start_time'])
                f.write(f"{timestamp} {chap['title']}\n")

    def _generate_ffmetadata(self, model: ProjectModel, path: Path):
        start_times = []
        current_time = 0.0
        for idx, slide in enumerate(model.slides):
            start_times.append(current_time)
            current_time += slide.duration
            if idx < len(model.slides) - 1:
                current_time += slide.interval_to_next
        total_duration_ms = int(current_time * 1000)
        with path.open('w', encoding='utf-8') as f:
            f.write(";FFMETADATA1\n")
            chapters = []
            for i, slide in enumerate(model.slides):
                if slide.chapter_title:
                    chapters.append({'title': slide.chapter_title, 'start_time': int(start_times[i] * 1000)})
            for i, chap in enumerate(chapters):
                start = chap['start_time']
                end = chapters[i+1]['start_time'] if i + 1 < len(chapters) else total_duration_ms
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle={chap['title']}\n")

    def _sanitize_path_for_concat(self, path_str: str) -> str:
        return path_str.replace('\\', '/').replace("'", "'\\\\''").replace('\n', '').replace('\r', '').replace(';', '\\;')

    def _cleanup_files(self, *paths):
        for p in paths:
            path_obj = Path(p)
            if path_obj.exists() and path_obj.is_file():
                try:
                    path_obj.unlink()
                except OSError as e:
                    self.log_message.emit(f"Could not delete temp file {p}: {e}", 'app')