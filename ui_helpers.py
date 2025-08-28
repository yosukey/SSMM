# ui_helpers.py
import base64
import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QPixmap

import config
from models import Slide
from utils import resolve_resource_path


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
    if dar_str and ':' in dar_str:
        try:
            num, den = map(int, dar_str.split(':'))
            if den > 0:
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
        font_path = resolve_resource_path("fonts/NotoSans-Regular.ttf")
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