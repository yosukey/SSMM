# pdf_utils.py
"""Thin wrapper around pypdfium2 for PDF reading and rendering.

This module centralizes every PDFium entry point so the rest of the codebase
never touches the pypdfium2 API directly. PDFium is *not* thread-safe, and SSMM
renders PDF pages from a Qt worker thread (video_processing) while the UI thread
may render thumbnails/previews. All access is therefore serialized through a
single re-entrant lock; callers must go through these helpers rather than
calling pypdfium2 directly.
"""
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Tuple

import pypdfium2 as pdfium
from PIL import Image

# PDFium is not thread-safe (not even across distinct documents). Serialize all
# access through one re-entrant lock so convenience helpers can call primitives.
_PDFIUM_LOCK = threading.RLock()


def open_pdf(pdf_path) -> pdfium.PdfDocument:
    """Open a PDF and return the document. Caller must call close_pdf()."""
    with _PDFIUM_LOCK:
        return pdfium.PdfDocument(str(pdf_path))


def close_pdf(doc: Optional[pdfium.PdfDocument]) -> None:
    if doc is None:
        return
    with _PDFIUM_LOCK:
        doc.close()


@contextmanager
def open_pdf_ctx(pdf_path):
    """Context manager yielding an open document; open and close are locked."""
    doc = open_pdf(pdf_path)
    try:
        yield doc
    finally:
        close_pdf(doc)


def num_pages(doc: pdfium.PdfDocument) -> int:
    with _PDFIUM_LOCK:
        return len(doc)


def page_size(doc: pdfium.PdfDocument, index: int) -> Tuple[float, float]:
    """Return (width, height) in PDF points for the given page index."""
    with _PDFIUM_LOCK:
        page = doc[index]
        try:
            return page.get_size()
        finally:
            page.close()


def render_page_to_pil(
    doc: pdfium.PdfDocument,
    index: int,
    *,
    scale: Optional[float] = None,
    target_width: Optional[int] = None,
) -> Image.Image:
    """Render a page to an RGB PIL image.

    Provide either ``target_width`` (pixels; the page is scaled so its rendered
    width matches) or ``scale`` (a points->pixels multiplier, where 1.0 == 72
    DPI). This mirrors the previous ``fitz.Matrix(zoom, zoom)`` behavior:
    ``zoom == target_width / page_width_in_points``.
    """
    with _PDFIUM_LOCK:
        page = doc[index]
        try:
            width, _height = page.get_size()
            if target_width is not None:
                effective_scale = (target_width / width) if width else 1.0
            elif scale is not None:
                effective_scale = scale
            else:
                effective_scale = 1.0
            bitmap = page.render(scale=effective_scale)
            # to_pil() keeps the bitmap referenced for the image's lifetime.
            return bitmap.to_pil().convert("RGB")
        finally:
            page.close()


def page_count(pdf_path) -> int:
    """Convenience: open a PDF, return its page count, and close it."""
    doc = open_pdf(pdf_path)
    try:
        return num_pages(doc)
    finally:
        close_pdf(doc)


def page_dims(pdf_path, is_canceled=None) -> Optional[list]:
    """Return a list of (width, height) point tuples for every page.

    If ``is_canceled`` is provided and returns True mid-iteration, returns None.
    """
    doc = open_pdf(pdf_path)
    try:
        dims = []
        for i in range(num_pages(doc)):
            if is_canceled is not None and is_canceled():
                return None
            dims.append(page_size(doc, i))
        return dims
    finally:
        close_pdf(doc)


def pypdfium2_version() -> str:
    try:
        from pypdfium2.version import PYPDFIUM_INFO
        return str(PYPDFIUM_INFO)
    except Exception:
        return "N/A"
