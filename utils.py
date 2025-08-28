# utils.py
import os
import sys
import shutil
import platform
from pathlib import Path

_ffmpeg_pair_cache: tuple[Path, Path, str] | None = None

def resolve_resource_path(relative_path: str | Path) -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path

def _find_ffmpeg_pair() -> tuple[Path, Path, str]:
    suffix = '.exe' if platform.system() == 'Windows' else ''
    ffmpeg_name = f'ffmpeg{suffix}'
    ffprobe_name = f'ffprobe{suffix}'

    # 1. Prioritize user's local ffmpeg-bin directory
    try:
        user_home = Path.home()
        user_dir = user_home / 'ffmpeg-bin'
        ffmpeg_path = user_dir / ffmpeg_name
        ffprobe_path = user_dir / ffprobe_name
        if ffmpeg_path.is_file() and ffprobe_path.is_file():
            return ffmpeg_path, ffprobe_path, 'user'
    except Exception:
        pass

    # 2. Check system's PATH using shutil.which
    ffmpeg_path_sys = shutil.which('ffmpeg')
    ffprobe_path_sys = shutil.which('ffprobe')
    if ffmpeg_path_sys and ffprobe_path_sys:
        return Path(ffmpeg_path_sys), Path(ffprobe_path_sys), 'system'

    # 3. (NEW) Explicitly check for Homebrew paths on macOS if not found in PATH
    if sys.platform == 'darwin':
        # List of potential Homebrew bin directories
        homebrew_paths = ['/opt/homebrew/bin', '/usr/local/bin']
        for h_path in homebrew_paths:
            ffmpeg_path = Path(h_path) / ffmpeg_name
            ffprobe_path = Path(h_path) / ffprobe_name
            if ffmpeg_path.is_file() and ffprobe_path.is_file():
                # If found, this is also considered a 'system' install
                return ffmpeg_path, ffprobe_path, 'system'

    # 4. Fallback to bundled executable
    bundled_dir = resolve_resource_path(Path('ffmpeg') / 'bin')
    ffmpeg_path_bundle = bundled_dir / ffmpeg_name
    ffprobe_path_bundle = bundled_dir / ffprobe_name
    if ffmpeg_path_bundle.is_file() and ffprobe_path_bundle.is_file():
        return ffmpeg_path_bundle, ffprobe_path_bundle, 'bundled'
        
    raise FileNotFoundError("Could not find a matching pair of ffmpeg and ffprobe.")

def _get_ffmpeg_pair_info() -> tuple[Path, Path, str]:
    global _ffmpeg_pair_cache
    if _ffmpeg_pair_cache is None:
        _ffmpeg_pair_cache = _find_ffmpeg_pair()
    return _ffmpeg_pair_cache

def get_ffmpeg_path() -> Path:
    ffmpeg_path, _, _ = _get_ffmpeg_pair_info()
    return ffmpeg_path

def get_ffprobe_path() -> Path:
    _, ffprobe_path, _ = _get_ffmpeg_pair_info()
    return ffprobe_path

def get_ffmpeg_source() -> str:
    try:
        _, _, source = _get_ffmpeg_pair_info()
        return source
    except FileNotFoundError:
        return 'not_found'

def bundled_ffmpeg_exists() -> bool:
    suffix = '.exe' if platform.system() == 'Windows' else ''
    bundled_path = resolve_resource_path(Path('ffmpeg') / 'bin' / f'ffmpeg{suffix}')
    return bundled_path.exists() and bundled_path.is_file()