# config.py
import sys
import subprocess
from pathlib import Path

SUBPROCESS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0

def get_version():
    try:
        git_process = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            check=True,
            # Pin to this repo's root so the version is not read from an unrelated git
            # repository that happens to be the process's working directory.
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=SUBPROCESS_CREATION_FLAGS
        )
        version = git_process.stdout.strip().lstrip('v')
        return version
    except (subprocess.CalledProcessError, OSError):
        return 'local-dev'

APP_VERSION = get_version()
REPO_URL = "https://github.com/yosukey/SSMM"

ENCODER_TEST_TIMEOUT_S = 15
FFPROBE_TIMEOUT_S = 15
WAVEFORM_GEN_TIMEOUT_S = 30
# Upper bound for a package-manager FFmpeg install; a hung installer must not block
# the modal progress dialog indefinitely.
FFMPEG_INSTALL_TIMEOUT_S = 600
# Indigo brand accent, rendered onto the dark waveform canvas (theme.MEDIA_BACKDROP).
WAVEFORM_COLOR = "#8b97e6"
PROCESS_START_TIMEOUT_MS = 10000
LOUDNORM_ANALYSIS_TIMEOUT_S = 600
UPDATE_CHECK_TIMEOUT_S = 5
# Keep the in-app debug log bounded during long renders.
DEBUG_LOG_MAX_LINES = 20000
# Characters of FFmpeg output kept for error reports when output is not captured.
FFMPEG_ERROR_TAIL_CHARS = 20000
# Files up to this size are fingerprinted with a full SHA-256; larger media files
# use a sampled fingerprint (size + head/middle/tail blocks) so that probing a
# multi-GB video does not stall the UI.
FULL_HASH_MAX_BYTES = 64 * 1024 * 1024
SAMPLED_HASH_BLOCK_BYTES = 4 * 1024 * 1024
FFMPEG_ENCODE_TIMEOUT_MS = 36000000


RESOLUTION_OPTIONS = ["3840x2160", "1920x1080", "1280x720", "960x540", "426x240", "1280x960", "960x720", "640x480"]
FPS_OPTIONS = [60, 30, 24, 10, 5, 3]
AUDIO_BITRATE_OPTIONS = ["256k", "192k", "160k", "128k", "96k"]
AUDIO_SAMPLE_RATE_OPTIONS = ["48000", "44100", "32000", "22050", "16000"]
AUDIO_CHANNELS_RANGE = (1, 8)

# RGB only; the alpha channel is applied separately at render time.
WATERMARK_COLOR_OPTIONS_RGB = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "green": (0, 128, 0),
}

# Rotation values are stored as the combo's userData, not the display text.
WATERMARK_ROTATION_OPTIONS = ("None", "45", "-45")

# The keys are what the project model and settings.toml store; the values are the
# (English) labels shown in the UI. Older settings files stored the labels, so the
# loader still accepts them and converts them to the keys.
ENCODING_MODES = {
    "QUALITY": "Quality (CQP/CRF)",
    "VBR": "Bitrate (VBR)",
    "CBR": "Bitrate (CBR)"
}
ENCODING_PASSES = {
    "ONE_PASS": "1-Pass",
    "TWO_PASS": "2-Pass"
}
LOUDNORM_MODES = {
    "ONE_PASS": "1-Pass (Faster)",
    "TWO_PASS": "2-Pass (Recommended)"
}

# Loudness target shared by the 1-pass filter, the 2-pass analysis and the 2-pass
# apply step (EBU R128 programme loudness).
LOUDNORM_TARGET_I = -23.0
LOUDNORM_TARGET_LRA = 7.0
LOUDNORM_TARGET_TP = -2.0

# Encoders that implement FFmpeg's generic two-pass mode (-pass 1/2 + -passlogfile).
TWO_PASS_ENCODERS = frozenset({"libx264", "libx265", "libaom-av1", "mpeg4"})
# NVENC has no -pass support, but performs an internal two-pass encode with
# -multipass, so a 2-Pass selection is mapped to that in a single run.
MULTIPASS_ENCODERS = frozenset({"h264_nvenc", "hevc_nvenc", "av1_nvenc"})

# Intermediate segments keep uncompressed audio so that concatenation never inserts
# AAC encoder-delay gaps; AAC is encoded once, in the final step.
INTERMEDIATE_CONTAINER = ".mov"
INTERMEDIATE_AUDIO_CODEC = "pcm_s16le"

SUPPORTED_AUDIO_FORMATS = ('.mp3', '.flac', '.aac', '.wav')
SUPPORTED_VIDEO_FORMATS = ('.mp4', '.avi', '.mov')
SUPPORTED_FORMATS = SUPPORTED_AUDIO_FORMATS + SUPPORTED_VIDEO_FORMATS

SILENT_MATERIAL_NAME = "SILENT"
UNASSIGNED_MATERIAL_NAME = "(Select Material)"

DEFAULT_SLIDE_INTERVAL = 3
DEFAULT_VIDEO_SCALE = 50

VBR_MAXRATE_MULTIPLIER = 1.5
VBR_BUFSIZE_MULTIPLIER = 2.0
CHROMA_KEY_SIMILARITY = 0.13
CHROMA_KEY_BLEND = 0.02
ENCODER_TEST_RESOLUTION = "320x240"
ENCODER_TEST_FRAMERATE = "30"
ENCODER_TEST_DURATION_S = 1

PDF_THUMBNAIL_ZOOM_FACTOR = 0.25
# Points-to-pixels scale used when rendering pages for p-hash and thumbnails.
PDF_DETAIL_RENDER_SCALE = 1.0
PDF_THUMBNAIL_HEIGHT = 300
INTERVAL_RANGE = (0, 300)
WATERMARK_OPACITY_RANGE = (0, 100)
WATERMARK_FONTSIZE_RANGE = (1, 100)
PINP_PREVIEW_UPDATE_DELAY_MS = 300
DURATION_RECALC_DELAY_MS = 500
SILENT_DURATION_RANGE = (1, 100)
PINP_SCALE_RANGE = (5, 100)
ENCODING_CRF_RANGE = (0, 51)
# mpeg4 quality is set via qscale (-q:v), range 1-31 (lower is better).
ENCODING_MPEG4_QSCALE_RANGE = (1, 31)
ENCODING_BITRATE_RANGE_KBPS = (500, 20000)
DEFAULT_CRF_VALUE = 23
DEFAULT_VBR_BITRATE = 6000
DEFAULT_CBR_BITRATE = 4000
PINP_OVERLAY_FILL_COLOR = (70, 130, 180, 150)
PINP_OVERLAY_OUTLINE_COLOR = "white"
PINP_VALIDATION_PREVIEW_BG_COLOR = "#2E2E2E"
PINP_VALIDATION_PREVIEW_OUTLINE_COLOR = '#555555'
PINP_VALIDATION_PREVIEW_WIDTH = 240
PDF_ASPECT_RATIO_TOLERANCE = 0.01
HAMMING_DISTANCE_THRESHOLD = 5

SUPPORTED_CODEC_CHECKS = {
    "MPEG-4 Part 2": ["mpeg4"],
    "H.264/MPEG-4 AVC": ["libx264", "h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"],
    "H.265/HEVC": ["libx265", "hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_videotoolbox"],
    "AV1": ["libaom-av1", "av1_nvenc", "av1_qsv", "av1_amf", "av1_videotoolbox"],
}

CODEC_MAP = {
    'H.264/MPEG-4 AVC': {
        'NVIDIA': 'h264_nvenc',
        'Intel': 'h264_qsv',
        'videotoolbox': 'h264_videotoolbox',
        'AMD': 'h264_amf'
    },
    'H.265/HEVC': {
        'NVIDIA': 'hevc_nvenc',
        'Intel': 'hevc_qsv',
        'videotoolbox': 'hevc_videotoolbox',
        'AMD': 'hevc_amf'
    },
    'AV1': {
        'NVIDIA': 'av1_nvenc',
        'Intel': 'av1_qsv',
        # No 'videotoolbox' entry: FFmpeg has no av1_videotoolbox encoder.
        'AMD': 'av1_amf'
    }
}

SOFTWARE_CODEC_MAP = {'MPEG-4 Part 2': 'mpeg4', 'H.264/MPEG-4 AVC': 'libx264', 'H.265/HEVC': 'libx265', 'AV1': 'libaom-av1'}

SILENT_AUDIO_SOURCE = 'anullsrc=channel_layout=stereo:sample_rate=44100'

BUNDLED_FONTS = {
    "Noto Sans CJK JP": "NotoSansCJKjp-Regular.otf",
    "Noto Sans": "NotoSans-Regular.ttf",
    "Noto Sans Arabic": "NotoSansArabic-Regular.ttf",
    "Noto Sans Devanagari": "NotoSansDevanagari-Regular.ttf",
    "Noto Sans Thai": "NotoSansThai-Regular.ttf",
}

TRANSITION_MAPPINGS = {
    "None": None,
    "Fade": "fade",
    "Fade Black": "fadeblack",
    "Fade White": "fadewhite",
    "Wipe Left": "wipeleft",
    "Wipe Right": "wiperight",
    "Wipe Up": "wipeup",
    "Wipe Down": "wipedown",
    "Slide Left": "slideleft",
    "Slide Right": "slideright",
    "Slide Up": "slideup",
    "Slide Down": "slidedown",
    "Circle Open": "circleopen",
    "Circle Close": "circleclose",
    "Circle Crop": "circlecrop",
    "Rectangle Crop": "rectcrop",
    "Radial": "radial",
    "Smooth Left": "smoothleft",
    "Smooth Right": "smoothright",
    "Smooth Up": "smoothup",
    "Smooth Down": "smoothdown",
    "Horizontal Close": "horzclose",
    "Horizontal Open": "horzopen",
    "Vertical Close": "vertclose",
    "Vertical Open": "vertopen",
    "Diagonal BL": "diagbl",
    "Diagonal BR": "diagbr",
    "Diagonal TL": "diagtl",
    "Diagonal TR": "diagtr",
    "Dissolve": "dissolve",
    "Pixelize": "pixelize"
}

VIDEO_POSITION_MAP = {
    'Center': {'x': '(main_w-overlay_w)/2', 'y': '(main_h-overlay_h)/2'},
    'Upper Left': {'x': '0', 'y': '0'},
    'Upper Right': {'x': 'main_w-overlay_w', 'y': '0'},
    'Bottom Left': {'x': '0', 'y': 'main_h-overlay_h'},
    'Bottom Right': {'x': 'main_w-overlay_w', 'y': 'main_h-overlay_h'},
}

VIDEO_EFFECT_MAP = {
    "None": "None",

    "Circle": "Circle",
    "Chroma": "Chroma key (green)",
    "Vignette": "Vignette",

    "Grayscale": "Grayscale",
    "Sepia": "Sepia",
    "Negative": "Negative",

    "HFlip": "Horizontal Flip",
    "VFlip": "Vertical Flip",
    "Blur": "Blur",
    "Pixelate": "Pixelate",
}

EFFECT_GROUPS = {
    "Shape": ["Circle", "Chroma", "Vignette"],
    "Color": ["Grayscale", "Sepia", "Negative"],
    "Processing": ["HFlip", "VFlip", "Blur", "Pixelate"]
}

ENCODER_TO_HARDWARE_MAP = {
    encoder_name: hw_name
    for codec, hw_map in CODEC_MAP.items()
    for hw_name, encoder_name in hw_map.items()
}

FILENAME_ILLEGAL_CHARS = r'<>:"/\|?*'
# Byte budget for the user's base output name. Kept well under the 255-byte
# filesystem limit so derived names still fit — the longest is the YouTube chapter
# file, "<stem>-chapter-for-youtube.txt", whose stem also carries an optional
# duration suffix (~34 bytes of overhead total).
FILENAME_MAX_LENGTH = 200
FILENAME_RESERVED_NAMES = (
    'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
)

LOG_STATE_TRANSITIONS = True