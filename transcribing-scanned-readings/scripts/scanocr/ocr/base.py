"""The interface every OCR engine implements.

An engine turns one page image into two things:
  * ``text``: the page as Markdown-ish text, the source of the clean .md
  * ``raw``:  the engine's own record of the page, with layout, saved to disk
              as soon as the page is done (this is what resume reloads)

The searchable-PDF builder never reads ``raw`` itself. It asks the engine to
turn it into ``Block``s (``parse_layout``), so a new engine only has to say
where its text sits on the page.

Fitting future engines in:
  * PaddleOCR-VL: JSON blocks with pixel boxes -> Blocks with space='pixels'.
  * Tesseract (pytesseract): TSV/hOCR words or lines with pixel boxes -> same.
  * OCRmyPDF builds its own PDF from Tesseract. Use the Tesseract engine above
    to share our pipeline, or wrap OCRmyPDF as a whole-document step later.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PageOutput:
    text: str   # page text for the clean .md
    raw: str    # engine-specific record with layout, saved per page


@dataclass
class Block:
    category: str               # e.g. 'text', 'title', 'image'
    box: list                   # [x1, y1, x2, y2]
    text: str                   # block text, engine tags removed, not yet cleaned
    space: str = 'grid999'      # 'grid999' (0-999 page grid) or 'pixels' (rendered page image)
    lines: list = None          # the printed lines, if the engine knows them (transcribed pages)


class OCREngine(ABC):
    name: str = ''              # registry key, e.g. 'unlimited'
    label: str = ''             # shown in the GUI

    def settings(self) -> dict:
        """Everything that changes the output. Part of the resume key."""
        return {}

    @abstractmethod
    def load(self) -> None:
        """Load the model. Called once, before the first page."""

    @abstractmethod
    def ocr_page(self, image_path: str, work_dir: str) -> PageOutput:
        """OCR one page image. ``work_dir`` is scratch space for this page."""

    @abstractmethod
    def parse_layout(self, raw: str) -> list:
        """Blocks with boxes from a saved ``raw`` record ([] if it has no layout)."""

    @abstractmethod
    def layout_free_text(self, raw: str) -> str:
        """Plain text from ``raw``, used when it has no usable layout."""
