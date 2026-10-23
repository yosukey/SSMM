# media_utils.py
"""Pure helpers shared by the validator, the renderer and the UI previews.

Keeping these in one module guarantees that the timeline, the PinP geometry and the
rotation handling are computed identically everywhere (validation report, slide
thumbnails, chapter math and the actual FFmpeg command lines).
"""
import math
from pathlib import Path
from typing import Optional


def quantize_duration(duration: float, fps: int) -> float:
    """Round a duration up to a whole number of frames at ``fps``."""
    if not duration or duration <= 0 or not fps or fps <= 0:
        return 0.0
    # Subtract a tiny epsilon so values that are already on the frame grid (e.g. 3.3s
    # at 30 fps -> 99.00000000000001 frames) do not gain an extra frame.
    return math.ceil(duration * fps - 1e-9) / fps


def slide_timeline(slides, fps: int) -> tuple[list[float], float]:
    """Return (start time of each slide, total duration) of the rendered timeline."""
    start_times = []
    current = 0.0
    for idx, slide in enumerate(slides):
        start_times.append(current)
        current += quantize_duration(slide.duration, fps)
        if idx < len(slides) - 1:
            current += quantize_duration(slide.interval_to_next, fps)
    return start_times, current


def normalize_rotation(display_matrix_rotation=None, rotate_tag=None) -> Optional[str]:
    """Convert ffprobe rotation metadata into a clockwise display rotation.

    ffprobe reports the display-matrix rotation counter-clockwise (a portrait phone
    clip is ``-90``), while the legacy ``rotate`` stream tag is clockwise (``90`` for
    the same clip). The result is one of ``"90"``, ``"180"``, ``"270"`` (degrees the
    coded frame must be turned clockwise to be displayed upright) or ``None``.
    """
    degrees = None
    if display_matrix_rotation is not None:
        try:
            degrees = -float(display_matrix_rotation)
        except (TypeError, ValueError):
            degrees = None
    if degrees is None and rotate_tag is not None:
        try:
            degrees = float(rotate_tag)
        except (TypeError, ValueError):
            degrees = None
    if degrees is None:
        return None
    snapped = int(round(degrees / 90.0)) * 90 % 360
    return str(snapped) if snapped else None


def is_quarter_turn(rotation: Optional[str]) -> bool:
    return rotation in ("90", "270")


def rotation_filters(rotation: Optional[str]) -> list[str]:
    """FFmpeg filters that turn the coded frame upright (input decoded with -noautorotate)."""
    if rotation == "90":
        return ["transpose=clock"]
    if rotation == "180":
        return ["hflip", "vflip"]
    if rotation == "270":
        return ["transpose=cclock"]
    return []


def _parse_ratio(ratio_str) -> Optional[float]:
    if not ratio_str or ':' not in str(ratio_str) or ratio_str == '0:1':
        return None
    try:
        num, den = map(int, str(ratio_str).split(':'))
    except (TypeError, ValueError):
        return None
    if num <= 0 or den <= 0:
        return None
    return num / den


def has_dar_override(tech_info: dict) -> bool:
    """True when the DAR metadata differs from the coded width/height ratio."""
    w, h = tech_info.get('width', 0) or 0, tech_info.get('height', 0) or 0
    dar = _parse_ratio(tech_info.get('dar'))
    return bool(w > 0 and h > 0 and dar is not None and abs((w / h) - dar) > 1e-4)


def display_dimensions(tech_info: dict) -> tuple[int, int]:
    """Width/height of the video as displayed (after rotation)."""
    w, h = tech_info.get('width', 0) or 0, tech_info.get('height', 0) or 0
    if is_quarter_turn(tech_info.get('rotate')):
        w, h = h, w
    return w, h


def display_aspect_ratio(tech_info: dict) -> Optional[float]:
    """Displayed aspect ratio, honouring DAR metadata and rotation; None if unknown."""
    w, h = display_dimensions(tech_info)
    if h <= 0:
        return None
    aspect = w / h
    dar = _parse_ratio(tech_info.get('dar'))
    if dar is not None:
        # DAR describes the coded (un-rotated) frame; invert it for a quarter turn.
        aspect = (1.0 / dar) if is_quarter_turn(tech_info.get('rotate')) else dar
    return aspect


def find_project_pdfs(folder: Optional[Path]) -> list[Path]:
    """All PDF files directly inside ``folder``, sorted for a deterministic choice."""
    if not folder or not Path(folder).is_dir():
        return []
    return sorted(Path(folder).glob('*.[pP][dD][fF]'))


def find_project_pdf(folder: Optional[Path]) -> Optional[Path]:
    pdfs = find_project_pdfs(folder)
    return pdfs[0] if pdfs else None


def format_duration_suffix(total_seconds: float) -> str:
    """Format a duration as ``"<m>m<s>s"`` / ``"<s>s"`` for output file names."""
    total = int(round(max(0.0, total_seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes}m{secs}s" if minutes > 0 else f"{secs}s"
