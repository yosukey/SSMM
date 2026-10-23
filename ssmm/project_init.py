# project_init.py
"""Project initialization helpers that are safe to call from worker threads.

Nothing here touches widgets, so ProjectSetupWorker can use these instead of
calling into MainWindow from a non-GUI thread.
"""
import re
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ssmm import config
from ssmm import pdf_utils
from ssmm.media_utils import find_project_pdfs
from ssmm.models import ProjectModel, Slide

_MATERIAL_INDEX_PATTERN = re.compile(r'\[(\d{3})\]')


def scan_materials(folder: Path) -> list[str]:
    """Supported audio/video files directly inside ``folder``, sorted by name."""
    return sorted(
        p.name for p in Path(folder).iterdir()
        if p.is_file() and p.suffix.lower() in config.SUPPORTED_FORMATS
    )


def find_single_pdf(folder: Path) -> Path:
    """Return the only PDF in ``folder``; raise if there is none or more than one."""
    pdfs = find_project_pdfs(folder)
    if not pdfs:
        # The "MainWindow" context keeps the existing translations of these messages.
        raise FileNotFoundError(QCoreApplication.translate(
            "MainWindow", "No PDF file found in the selected project folder."))
    if len(pdfs) > 1:
        raise ValueError(QCoreApplication.translate(
            "MainWindow", "Multiple PDF files found. Please ensure there is only one PDF in the folder."))
    return pdfs[0]


def initialize_project_from_pdf(project_model: ProjectModel) -> Path:
    """Create one unassigned slide per PDF page and list the available materials."""
    if not project_model.project_folder:
        raise ValueError("Project folder is not set.")

    pdf_path = find_single_pdf(project_model.project_folder)
    try:
        page_count = pdf_utils.page_count(pdf_path)
    except Exception as e:
        raise ValueError(f"Failed to read PDF file '{pdf_path.name}': {e}")

    project_model.slides = [Slide() for _ in range(page_count)]
    project_model.available_materials = scan_materials(project_model.project_folder)
    return pdf_path


def automap_materials(project_model: ProjectModel) -> None:
    """Assign materials named like '[003]...' to slide 3 when it is still unassigned."""
    for material_name in project_model.available_materials:
        match = _MATERIAL_INDEX_PATTERN.match(material_name)
        if match:
            slide_index = int(match.group(1)) - 1
            if 0 <= slide_index < len(project_model.slides) and project_model.slides[slide_index].filename is None:
                project_model.slides[slide_index].filename = material_name
