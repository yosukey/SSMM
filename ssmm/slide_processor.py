# slide_processor.py
from dataclasses import dataclass
from pathlib import Path

from ssmm import config
from ssmm.models import ProjectModel, Slide


@dataclass
class SlideJob:
    """Everything needed to render the segment of one slide."""
    index: int
    slide: Slide
    project_model: ProjectModel
    image_paths: dict[int, Path]
    temp_folder: Path
    codec: str


class BaseSlideProcessor:
    def __init__(self, video_processor, job: SlideJob, output_path: Path):
        self.vp = video_processor
        self.job = job
        self.slide = job.slide
        self.project_model = job.project_model
        self.codec = job.codec
        self.image_path = job.image_paths[job.index]
        self.output_path = output_path

    def process(self):
        raise NotImplementedError("Each processor must implement the 'process' method.")

class UnassignedSlideProcessor(BaseSlideProcessor):
    def process(self):
        self.vp._combine_image_silent_audio(self.project_model, self.image_path, 1, self.output_path, self.codec, self.slide)

class SilentSlideProcessor(BaseSlideProcessor):
    def process(self):
        self.vp._combine_image_silent_audio(self.project_model, self.image_path, self.slide.duration, self.output_path, self.codec, self.slide)

class AudioSlideProcessor(BaseSlideProcessor):
    def process(self):
        material_path = self.project_model.project_folder / self.slide.filename
        self.vp._combine_image_audio(self.project_model, self.image_path, material_path, self.output_path, self.codec, self.slide)

class VideoSlideProcessor(BaseSlideProcessor):
    def process(self):
        material_path = self.project_model.project_folder / self.slide.filename
        self.vp._overlay_video_on_image(self.project_model, self.image_path, material_path, self.output_path, self.codec, self.slide)

class SlideProcessorFactory:
    @staticmethod
    def get_processor(video_processor, job: SlideJob, output_path: Path):
        slide = job.slide

        if slide.filename is None:
            return UnassignedSlideProcessor(video_processor, job, output_path)
        elif slide.filename == config.SILENT_MATERIAL_NAME:
            return SilentSlideProcessor(video_processor, job, output_path)

        # Classify by the probed streams first: an audio-only .mp4/.mov is rendered
        # as an audio slide, not as a PinP video.
        if slide.is_video:
            return VideoSlideProcessor(video_processor, job, output_path)

        filename_lower = slide.filename.lower()
        if filename_lower.endswith(config.SUPPORTED_AUDIO_FORMATS) or filename_lower.endswith(config.SUPPORTED_VIDEO_FORMATS):
            return AudioSlideProcessor(video_processor, job, output_path)
        raise ValueError(f"Unknown slide type for material: {slide.filename}")
