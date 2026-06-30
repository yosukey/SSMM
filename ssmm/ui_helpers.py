# ui_helpers.py
import base64
import io
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QPixmap

from ssmm import config
from ssmm import pdf_utils
from ssmm.models import Slide, ProjectParameters
from ssmm.utils import resolve_resource_path, get_ffmpeg_path
from ssmm.watermark import render_watermark_overlay


def generate_waveform_pixmap(audio_path: Path, width: int, height: int) -> Optional[QPixmap]:
    try:
        ffmpeg_path = get_ffmpeg_path()
    except Exception:
        return None

    cmd = [
        str(ffmpeg_path),
        '-v', 'error',
        '-i', str(audio_path),
        '-filter_complex', f'showwavespic=s={width}x{height}:colors={config.WAVEFORM_COLOR}',
        '-frames:v', '1',
        '-c:v', 'png',
        '-f', 'image2pipe',
        '-',
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=config.WAVEFORM_GEN_TIMEOUT_S,
            creationflags=config.SUBPROCESS_CREATION_FLAGS,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        pixmap = QPixmap()
        if pixmap.loadFromData(result.stdout, "PNG"):
            return pixmap
        return None
    except Exception:
        return None


def render_pdf_page_to_pixmap(pdf_path: Optional[Path], page_index: int, target_width: int) -> Optional[QPixmap]:
    if not pdf_path:
        return None
    doc = None
    try:
        doc = pdf_utils.open_pdf(pdf_path)
        if not (0 <= page_index < pdf_utils.num_pages(doc)):
            return None
        pil_image = pdf_utils.render_page_to_pil(doc, page_index, target_width=target_width)
        data = pil_image.tobytes("raw", "RGB")
        qimage = QImage(data, pil_image.width, pil_image.height, pil_image.width * 3, QImage.Format_RGB888)
        # QImage shares the `data` buffer; copy() detaches before `data` is freed.
        return QPixmap.fromImage(qimage.copy())
    except Exception:
        return None
    finally:
        pdf_utils.close_pdf(doc)


def _draw_pinp_overlay(draw: ImageDraw.ImageDraw, geometry: dict, video_effects: list[str], font: ImageFont.FreeTypeFont):
    x, y, w, h = geometry['x'], geometry['y'], geometry['w'], geometry['h']
    
    if "Circle" in video_effects:
        mask_img = Image.new("L", draw.im.size, 0)
        draw_mask = ImageDraw.Draw(mask_img)
        
        diameter = min(w, h)
        radius = diameter / 2
        center_x = x + w / 2
        center_y = y + h / 2
        circle_bounds = [center_x - radius, center_y - radius, center_x + radius, center_y + radius]
        
        draw_mask.ellipse(circle_bounds, fill=255)
        
        box = (0, 0, *draw.im.size)
        draw.im.paste(config.PINP_OVERLAY_FILL_COLOR, box, mask_img.im)
        
        draw.rectangle([x, y, x + w, y + h], outline=config.PINP_OVERLAY_OUTLINE_COLOR, width=1)
        draw.ellipse(circle_bounds, outline=config.PINP_OVERLAY_OUTLINE_COLOR, width=1)
    else:
        draw.rectangle([x, y, x + w, y + h], fill=config.PINP_OVERLAY_FILL_COLOR, outline=config.PINP_OVERLAY_OUTLINE_COLOR, width=1)

    effect_label_map = {
        "Chroma": "Chroma", "Vignette": "Vignette", "HFlip": "Flip H", 
        "VFlip": "Flip V", "Grayscale": "B&W", "Sepia": "Sepia", 
        "Negative": "Neg", "Blur": "Blur", "Pixelate": "Pixel"
    }
    
    labels_to_draw = [label for key, label in effect_label_map.items() if key in video_effects]

    if labels_to_draw:
        parts = []
        for i, label in enumerate(labels_to_draw):
            parts.append(f"[{label}]")
            if (i + 1) % 2 == 0 and (i + 1) < len(labels_to_draw):
                parts.append("\n")
            else:
                parts.append(" ")
        
        text = "".join(parts).strip()

        text_x = x + 3
        text_y = y + 1
        draw.text((text_x, text_y), text, font=font, fill="black")

def calculate_pinp_geometry(slide: Slide, output_width: int, output_height: int) -> dict | None:
    if not slide.is_video:
        return None

    video_width = slide.tech_info.get('width', 0)
    video_height = slide.tech_info.get('height', 0)
    dar_str = slide.tech_info.get('dar')

    rotation = slide.tech_info.get('rotate')
    if rotation in ["90", "270", "-90"]:
        video_width, video_height = video_height, video_width

    if video_height <= 0:
        return None

    aspect_ratio = video_width / video_height
    # Use the DAR for aspect ratio, ignoring the undefined '0:1' and zero numerators.
    if dar_str and ':' in dar_str and dar_str != '0:1':
        try:
            num, den = map(int, dar_str.split(':'))
            if num > 0 and den > 0:
                aspect_ratio = num / den
        except (ValueError, TypeError):
            pass

    scale_percent = slide.video_scale / 100.0
    final_height_float = output_height * scale_percent
    final_width_float = final_height_float * aspect_ratio

    final_width = round(final_width_float / 2) * 2
    final_height = round(final_height_float / 2) * 2

    position_key = slide.video_position
    pos_x, pos_y = 0.0, 0.0
    if position_key == 'Center':
        pos_x = (output_width - final_width) / 2
        pos_y = (output_height - final_height) / 2
    elif position_key == 'Upper Left':
        pos_x, pos_y = 0, 0
    elif position_key == 'Upper Right':
        pos_x = output_width - final_width
        pos_y = 0
    elif position_key == 'Bottom Left':
        pos_x = 0
        pos_y = output_height - final_height
    elif position_key == 'Bottom Right':
        pos_x = output_width - final_width
        pos_y = output_height - final_height

    return {
        'x': round(pos_x),
        'y': round(pos_y),
        'width': final_width,
        'height': final_height
    }

def qimage_to_pil(qimage: QImage) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QIODevice.ReadWrite)
    qimage.save(buffer, "PNG")
    pil_im = Image.open(io.BytesIO(buffer.data()))
    return pil_im.convert("RGBA")

def pil_to_qimage(pil_im: Image.Image) -> QImage:
    buffer = io.BytesIO()
    pil_im.save(buffer, "PNG")
    qimage = QImage()
    qimage.loadFromData(buffer.getvalue(), "PNG")
    return qimage

def _create_preview_image(base_image: Image.Image, slide: Slide, output_width: int, output_height: int) -> Image.Image:
    pinp_geometry = calculate_pinp_geometry(slide, output_width, output_height)
    if not pinp_geometry:
        return base_image

    image_with_overlay = base_image.copy()
    draw = ImageDraw.Draw(image_with_overlay, "RGBA")

    try:
        preview_h = image_with_overlay.size[1]
        font_size = max(10, int(preview_h * 0.1))
        font_path = resolve_resource_path("resources/fonts/NotoSans-Regular.ttf")
        font = ImageFont.truetype(str(font_path), font_size)
    except Exception:
        font = ImageFont.load_default()

    thumb_w, thumb_h = image_with_overlay.size
    scale_x = thumb_w / output_width
    scale_y = thumb_h / output_height
    
    scaled_geometry = {
        'x': pinp_geometry['x'] * scale_x,
        'y': pinp_geometry['y'] * scale_y,
        'w': pinp_geometry['width'] * scale_x,
        'h': pinp_geometry['height'] * scale_y
    }
    
    _draw_pinp_overlay(draw, scaled_geometry, slide.video_effects, font)
    return image_with_overlay

def superimpose_pinp_info(base_pixmap: QPixmap, slide: Slide, output_width: int, output_height: int) -> QPixmap:
    pil_image = qimage_to_pil(base_pixmap.toImage())
    pil_image_with_overlay = _create_preview_image(pil_image, slide, output_width, output_height)
    result_qimage = pil_to_qimage(pil_image_with_overlay)
    return QPixmap.fromImage(result_qimage)

def superimpose_watermark(base_pixmap: QPixmap, params: ProjectParameters) -> QPixmap:
    # Generate the overlay at the pixmap's own pixel size so it scales proportionally
    # and matches the final video. Any failure leaves the preview untouched.
    try:
        overlay = render_watermark_overlay(params, base_pixmap.width(), base_pixmap.height())
        if overlay is None:
            return base_pixmap
        pil_image = qimage_to_pil(base_pixmap.toImage())
        pil_image.alpha_composite(overlay)
        return QPixmap.fromImage(pil_to_qimage(pil_image))
    except Exception:
        return base_pixmap

def create_pinp_preview_for_report(base_slide_image: Optional[Image.Image], slide: Slide, output_width: int, output_height: int) -> str:
    try:
        preview_width = config.PINP_VALIDATION_PREVIEW_WIDTH
        
        if base_slide_image:
            base_w, base_h = base_slide_image.size
            preview_height = int(preview_width * (base_h / base_w))
            image = base_slide_image.resize((preview_width, preview_height), Image.Resampling.LANCZOS).convert("RGBA")
        else:
            preview_height = int(preview_width * (output_height / output_width))
            image = Image.new('RGBA', (preview_width, preview_height), color=config.PINP_VALIDATION_PREVIEW_BG_COLOR)
            draw = ImageDraw.Draw(image)
            draw.rectangle([0, 0, preview_width - 1, preview_height - 1], outline=config.PINP_VALIDATION_PREVIEW_OUTLINE_COLOR, width=1)

        image_with_overlay = _create_preview_image(image, slide, output_width, output_height)

        buffered = io.BytesIO()
        image_with_overlay.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
        return img_str
    except Exception:
        return ""