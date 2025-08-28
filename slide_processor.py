# slide_processor.py
from pathlib import Path
import config

class BaseSlideProcessor:
    def __init__(self, video_processor, slide_info: tuple, output_path: Path):
        self.vp = video_processor
        self.i, self.slide, self.project_model, self.image_paths_dict, self.temp_folder, self.codec = slide_info
        self.image_path = self.image_paths_dict[self.i]
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
    def get_processor(video_processor, slide_info: tuple, output_path: Path):
        _, slide, _, _, _, _ = slide_info

        if slide.filename is None:
            return UnassignedSlideProcessor(video_processor, slide_info, output_path)
        elif slide.filename == config.SILENT_MATERIAL_NAME:
            return SilentSlideProcessor(video_processor, slide_info, output_path)
        elif slide.filename.lower().endswith(config.SUPPORTED_AUDIO_FORMATS):
            return AudioSlideProcessor(video_processor, slide_info, output_path)
        elif slide.is_video:
            return VideoSlideProcessor(video_processor, slide_info, output_path)
        else:
            raise ValueError(f"Unknown slide type for material: {slide.filename}")