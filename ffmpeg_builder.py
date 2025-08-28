# ffmpeg_builder.py
from pathlib import Path
from utils import get_ffmpeg_path

class FFmpegCommandBuilder:
    def __init__(self):
        self.inputs = []
        self.filter_complex = None
        self.output_path = None
        self.output_options = []
        self.global_options = ['-y', '-hide_banner']

    def add_global_options(self, *opts: str) -> 'FFmpegCommandBuilder':
        self.global_options.extend(opts)
        return self

    def add_input(self, path: str | Path, options: list[str] | None = None) -> 'FFmpegCommandBuilder':
        self.inputs.append({'path': path, 'options': options or []})
        return self

    def set_filter_complex(self, filter_string: str) -> 'FFmpegCommandBuilder':
        self.filter_complex = filter_string
        return self

    def set_output(self, path: str | Path, options: list[str] | None = None) -> 'FFmpegCommandBuilder':
        self.output_path = path
        self.output_options = options or []
        return self

    def build(self) -> list[str]:
        if not self.output_path:
            raise ValueError("Output path must be set before building the command.")

        cmd = [str(get_ffmpeg_path())]
        cmd.extend(self.global_options)

        for inp in self.inputs:
            cmd.extend(inp['options'])
            cmd.extend(['-i', str(inp['path'])])

        if self.filter_complex:
            cmd.extend(['-filter_complex', self.filter_complex])

        cmd.extend(self.output_options)
        cmd.append(str(self.output_path))
        
        return cmd