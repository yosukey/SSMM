# dougameijin_importer.py
import io
import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image

from ssmm import config
from ssmm.models import ProjectModel, ProjectParameters, Slide


# --- DougaMeijin format constants (mirror DougaMeijin/config.py) ---
DMJ_PROJECT_FILENAME = "project.json"

# DougaMeijin persists in-project audio as lossless FLAC (DougaMeijin/config.AUDIO_FILE_EXTENSION).
# DougaMeijin still reads legacy WAV projects for backward compatibility; SSMM assumes FLAC only.
DMJ_AUDIO_FILE_EXTENSION = ".flac"

# Maximum declared uncompressed archive size, to guard against zip bombs.
DMJ_MAX_TOTAL_UNCOMPRESSED_BYTES = 8 * 1024 ** 3  # 8 GiB

# DougaMeijin stores its output resolution as a label; SSMM stores "WIDTHxHEIGHT".
DMJ_RESOLUTION_MAP = {
    "1080p": "1920x1080",
    "720p": "1280x720",
}

# Reverse lookup from FFmpeg xfade keyword to SSMM's display name.
_FFMPEG_TO_DISPLAY = {kw: name for name, kw in config.TRANSITION_MAPPINGS.items() if kw}


def _map_transition(dmj_transition) -> str:
    if not dmj_transition or dmj_transition == "none":
        return "None"
    return _FFMPEG_TO_DISPLAY.get(dmj_transition, "None")


def _unique_folder(base: Path) -> Path:
    if not base.exists():
        return base
    n = 2
    while True:
        candidate = base.parent / f"{base.name}_{n}"
        if not candidate.exists():
            return candidate
        n += 1


def _check_archive_safety(zf: zipfile.ZipFile):
    total = 0
    for info in zf.infolist():
        name = info.filename
        # Reject absolute paths and parent-directory traversal.
        if name.startswith('/') or name.startswith('\\') or '..' in Path(name).parts:
            raise ValueError(f"Unsafe path in DougaMeijin archive: '{name}'.")
        total += info.file_size
        if total > DMJ_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("The DougaMeijin archive is too large to load (possible zip bomb).")


def _load_image_as_rgb(image_bytes: bytes) -> Image.Image:
    """Decode page image bytes into an opaque RGB image for PDF embedding."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        return img.convert("RGB")


def import_project(dmj_path, extract_dir=None, log=None) -> ProjectModel:
    def _log(msg, source='app'):
        if log:
            log(msg, source)

    dmj_path = Path(dmj_path)
    _log(f"[INFO] Importing DougaMeijin project: {dmj_path}")

    if not dmj_path.is_file():
        raise FileNotFoundError(f"DougaMeijin project file not found: {dmj_path}")
    if not zipfile.is_zipfile(dmj_path):
        raise ValueError(f"'{dmj_path.name}' is not a valid DougaMeijin (.dmj) project file.")

    # Extract into the user-chosen folder; fall back to the .dmj's own directory.
    parent_dir = Path(extract_dir) if extract_dir else dmj_path.parent
    dest_folder = _unique_folder(parent_dir / dmj_path.stem)
    created_folder = False

    try:
        with zipfile.ZipFile(dmj_path, 'r') as zf:
            _check_archive_safety(zf)

            try:
                project_data = json.loads(zf.read(DMJ_PROJECT_FILENAME).decode('utf-8'))
            except KeyError:
                raise ValueError(
                    f"'{dmj_path.name}' does not contain '{DMJ_PROJECT_FILENAME}'; "
                    "it may not be a DougaMeijin project file."
                )
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ValueError(f"The project metadata in '{dmj_path.name}' is corrupt: {e}")

            version = project_data.get("version", 1)
            _log(f"[INFO] DougaMeijin project version: {version}")

            pages = project_data.get("pages", [])
            if not pages:
                raise ValueError("The DougaMeijin project contains no pages.")

            names = set(zf.namelist())

            # Validate that every page's image is present before touching disk.
            for idx, page in enumerate(pages):
                image_rel = page.get("image")
                if not image_rel:
                    raise ValueError(f"Page {idx + 1} in the DougaMeijin project has no image reference.")
                if image_rel not in names:
                    raise ValueError(
                        f"Image '{image_rel}' referenced by page {idx + 1} is missing from the archive."
                    )

            dest_folder.mkdir(parents=True, exist_ok=False)
            created_folder = True
            _log(f"[INFO] Extracting DougaMeijin project to: {dest_folder}")

            global_transition = _map_transition(project_data.get("transition"))
            # A transition requires a non-zero interval to play.
            interval = 0 if global_transition == "None" else 1

            pdf_path = dest_folder / f"{dmj_path.stem}.pdf"
            slides = []
            page_images = []

            try:
                for idx, page in enumerate(pages):
                    image_rel = page["image"]
                    try:
                        page_images.append(_load_image_as_rgb(zf.read(image_rel)))
                    except Exception as e:
                        raise ValueError(
                            f"Failed to read image '{image_rel}' for page {idx + 1}: {e}"
                        )

                    slide = Slide()
                    slide.transition_to_next = global_transition
                    slide.interval_to_next = interval

                    material_name = None
                    audio_rel = page.get("audio")
                    if audio_rel and audio_rel in names:
                        ext = Path(audio_rel).suffix.lower()
                        if ext == DMJ_AUDIO_FILE_EXTENSION:
                            material_name = f"[{idx + 1:03d}]{ext}"
                            (dest_folder / material_name).write_bytes(zf.read(audio_rel))
                        else:
                            _log(f"[WARNING] Page {idx + 1} audio '{audio_rel}' is not FLAC "
                                 f"({ext}); the slide will be silent.")
                    elif audio_rel:
                        _log(f"[WARNING] Audio '{audio_rel}' referenced by page {idx + 1} is missing "
                             "from the archive; the slide will be silent.")

                    if material_name:
                        slide.filename = material_name
                        slide.is_video = material_name.lower().endswith(config.SUPPORTED_VIDEO_FORMATS)
                    else:
                        slide.filename = config.SILENT_MATERIAL_NAME
                        slide.duration = config.DEFAULT_SLIDE_INTERVAL

                    slides.append(slide)

                # Each image becomes one PDF page (matching DougaMeijin page order).
                page_images[0].save(
                    pdf_path, "PDF", save_all=True, append_images=page_images[1:]
                )
            finally:
                for img in page_images:
                    img.close()

        model = ProjectModel()
        model.project_folder = dest_folder
        model.slides = slides
        model.available_materials = sorted([
            p.name for p in dest_folder.iterdir()
            if p.is_file() and p.suffix.lower() in config.SUPPORTED_FORMATS
        ])

        params = ProjectParameters()
        dmj_resolution = project_data.get("resolution")
        if dmj_resolution in DMJ_RESOLUTION_MAP:
            params.resolution = DMJ_RESOLUTION_MAP[dmj_resolution]
        elif dmj_resolution:
            _log(f"[WARNING] Unknown DougaMeijin resolution '{dmj_resolution}'; "
                 f"using the default ({params.resolution}).")

        # Keep the frame rate only when it is one of SSMM's selectable options.
        fps = project_data.get("fps")
        if isinstance(fps, int) and fps in config.FPS_OPTIONS:
            params.fps = fps
        elif fps:
            _log(f"[INFO] DougaMeijin frame rate ({fps} fps) is not an SSMM option; "
                 f"using the default ({params.fps} fps).")

        model.parameters = params

        material_count = len(model.available_materials)
        _log(f"[SUCCESS] Imported DougaMeijin project: {len(slides)} slides, "
             f"{material_count} audio material(s).")
        return model

    except Exception:
        if created_folder and dest_folder.exists():
            shutil.rmtree(dest_folder, ignore_errors=True)
        raise
