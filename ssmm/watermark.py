# watermark.py
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ssmm import config
from ssmm.models import ProjectParameters
from ssmm.utils import resolve_resource_path


def render_watermark_overlay(params: ProjectParameters, width: int, height: int) -> Optional[Image.Image]:
    """Render the watermark as a transparent RGBA overlay of size ``(width, height)``.

    Returns the overlay image, or ``None`` when the watermark is disabled or has no
    text. The drawing logic mirrors the final-video watermark so previews match the
    exported result. Raises ``FileNotFoundError`` if the configured font is missing.
    """
    if not params.add_watermark or not params.watermark_text:
        return None

    font_file = config.BUNDLED_FONTS.get(params.watermark_fontfamily)
    if not font_file:
        raise FileNotFoundError(f"Font definition for '{params.watermark_fontfamily}' not found.")

    font_path = resolve_resource_path(Path('resources') / 'fonts' / font_file)
    if not font_path.exists():
        raise FileNotFoundError(f"Font file not found: {font_path}")

    font_size_px = int(height * (params.watermark_fontsize / 100))
    font = ImageFont.truetype(str(font_path), font_size_px)

    rgb_color = config.WATERMARK_COLOR_OPTIONS_RGB.get(params.watermark_color, (255, 255, 255))
    fill_color = (*rgb_color, int(255 * (params.watermark_opacity / 100)))

    bbox = font.getbbox(params.watermark_text)
    # Clamp to at least 1px so unrenderable text does not produce a zero-sized canvas.
    text_width = max(1, bbox[2] - bbox[0])
    text_height = max(1, bbox[3] - bbox[1])
    text_offset_y = bbox[1]
    stamp_canvas_size = (text_width, text_height)
    text_draw_pos = (0, -text_offset_y)

    stamp_img = Image.new('RGBA', stamp_canvas_size, (0, 0, 0, 0))
    draw_stamp = ImageDraw.Draw(stamp_img)
    draw_stamp.text(text_draw_pos, params.watermark_text, font=font, fill=fill_color)

    if params.watermark_rotation != "None":
        angle = -int(params.watermark_rotation)
        stamp_img = stamp_img.rotate(angle, expand=True, resample=Image.BICUBIC)

    final_image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    stamp_w, stamp_h = stamp_img.size

    if params.watermark_tile:
        base_spacing_x = int(text_width * 0.8)
        base_spacing_y = int(text_height * 2.0)

        step_x = text_width + base_spacing_x
        step_y = text_height + base_spacing_y

        if params.watermark_rotation != "None":
            step_y = int(text_height * 1.5)

        step_x = max(1, step_x)
        step_y = max(1, step_y)

        for y in range(-stamp_h, height + stamp_h, step_y):
            for x in range(-stamp_w, width + stamp_w, step_x):
                x_offset = (step_x // 2) if (y // step_y) % 2 != 0 else 0
                final_image.paste(stamp_img, (x + x_offset, y), stamp_img)
    else:
        pos_x = (width - stamp_w) // 2
        pos_y = (height - stamp_h) // 2
        final_image.paste(stamp_img, (pos_x, pos_y), stamp_img)

    return final_image
