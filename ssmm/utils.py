# utils.py
import os
import sys
import shutil
import platform
from pathlib import Path

from PySide6.QtCore import QTranslator, QLibraryInfo

from ssmm import app_settings

_ffmpeg_pair_cache: tuple[Path, Path, str] | None = None

def resolve_resource_path(relative_path: str | Path) -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = Path(sys._MEIPASS)
    else:
        # Go up two levels from this module to the repo root, where resources/ resides.
        base_path = Path(__file__).resolve().parent.parent
    return base_path / relative_path

# Caller must keep the returned translators alive; installTranslator doesn't own them.
def install_translators(app):
    installed = []
    # The display language is resolved from the user's saved preference,
    # falling back to the OS locale when set to "system".
    locale = app_settings.resolve_qlocale(app_settings.get_language())

    app_translator = QTranslator(app)
    qm_dir = str(resolve_resource_path("translations"))
    if app_translator.load(locale, "ssmm", "_", qm_dir, ".qm"):
        app.installTranslator(app_translator)
        installed.append(app_translator)

    # Qt's own strings, e.g. standard dialog buttons.
    qt_translator = QTranslator(app)
    qt_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load(locale, "qtbase", "_", qt_dir):
        app.installTranslator(qt_translator)
        installed.append(qt_translator)

    return installed

def _platform_search_dirs() -> list[Path]:
    """Well-known install locations that may be missing from the process PATH.

    SSMM does not ship FFmpeg (a system build is used so that the hardware encoders
    of the user's GPU are available), so it has to find an existing installation.
    """
    dirs: list[Path] = []
    if sys.platform == 'darwin':
        # A .app launched from Finder inherits launchd's minimal PATH, which omits
        # Homebrew (Apple silicon / Intel) and MacPorts.
        dirs += [Path('/opt/homebrew/bin'), Path('/usr/local/bin'), Path('/opt/local/bin')]
    elif sys.platform.startswith('win'):
        # winget (e.g. Gyan.FFmpeg) exposes the executables through these link
        # folders. The PATH entry it adds only reaches newly started processes, so
        # probing them lets an install made from the running app be detected
        # without a restart.
        local_appdata = os.environ.get('LOCALAPPDATA')
        if local_appdata:
            dirs.append(Path(local_appdata) / 'Microsoft' / 'WinGet' / 'Links')
        program_files = os.environ.get('ProgramFiles')
        if program_files:
            dirs.append(Path(program_files) / 'WinGet' / 'Links')
    return dirs

def _find_ffmpeg_pair() -> tuple[Path, Path, str]:
    suffix = '.exe' if platform.system() == 'Windows' else ''
    ffmpeg_name = f'ffmpeg{suffix}'
    ffprobe_name = f'ffprobe{suffix}'

    # 1. User's local ffmpeg-bin directory (explicit override).
    try:
        user_dir = Path.home() / 'ffmpeg-bin'
        ffmpeg_path = user_dir / ffmpeg_name
        ffprobe_path = user_dir / ffprobe_name
        if ffmpeg_path.is_file() and ffprobe_path.is_file():
            return ffmpeg_path, ffprobe_path, 'user'
    except Exception:
        pass

    # 2. The system PATH.
    ffmpeg_path_sys = shutil.which('ffmpeg')
    ffprobe_path_sys = shutil.which('ffprobe')
    if ffmpeg_path_sys and ffprobe_path_sys:
        return Path(ffmpeg_path_sys), Path(ffprobe_path_sys), 'system'

    # 3. Package-manager locations that may not be on PATH.
    for directory in _platform_search_dirs():
        ffmpeg_path = directory / ffmpeg_name
        ffprobe_path = directory / ffprobe_name
        if ffmpeg_path.is_file() and ffprobe_path.is_file():
            return ffmpeg_path, ffprobe_path, 'system'

    raise FileNotFoundError("Could not find a matching pair of ffmpeg and ffprobe.")

def _get_ffmpeg_pair_info() -> tuple[Path, Path, str]:
    global _ffmpeg_pair_cache
    if _ffmpeg_pair_cache is not None:
        cached_ffmpeg, cached_ffprobe, _ = _ffmpeg_pair_cache
        # Invalidate if the cached binaries have since been moved or removed, so a
        # mid-session change (installing ffmpeg, or deleting ~/ffmpeg-bin) is picked
        # up without requiring a restart.
        if cached_ffmpeg.is_file() and cached_ffprobe.is_file():
            return _ffmpeg_pair_cache
        _ffmpeg_pair_cache = None
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
