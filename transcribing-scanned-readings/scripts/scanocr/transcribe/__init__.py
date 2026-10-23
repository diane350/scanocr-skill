"""Clean text and a searchable PDF from pages transcribed by Claude instead of an OCR model.

    python -m scanocr.transcribe prepare <scan.pdf or page image> <workdir> [--resume ZIP]
        renders the pages, finds the text blocks, and writes for each page:
          sheets/page_NNNN.png    the page with its blocks boxed and numbered (to read)
          crops/page_NNNN_bKK.png each block at full size (for small print)
          page_NNNN.txt           the transcription: pre-filled from the PDF's old text
                                  (status draft) or to type (status todo); kept if it exists
    python -m scanocr.transcribe status <workdir>      pages done, drafts, not started
    python -m scanocr.transcribe build <workdir> <outdir> [--partial]
        checks every transcription, then writes <outdir>/<name>.md and <name>_searchable.pdf
    python -m scanocr.transcribe pack <workdir> <progress.zip>
        the transcriptions in one file, to carry on in a later session (prepare --resume)

Transcription file (see transcript.py):
    # status: done           todo / draft / done; only done pages are built
    page: 47                 printed page number, blank if none shows
    === 3 ===                body text (headings, quotations too)
    === 1 header ===         running head, page number (also: footer): kept out of the
                             .md and of the PDF's copyable text
    === 8 footnote ===       notes, each starting with its marker (¹ or * †), a blank line between
    === 4-6 table ===        a Markdown table covering blocks 4 to 6
    === 7 skip ===           no text to keep: picture, stamp, sideways watermark
Lines are typed exactly as printed (same line breaks, end-of-line hyphens, Unicode
superscripts like ¹), a blank line between paragraphs, _underscores_ around italics.
"""
from .build import build
from .compose import compose
from .sheets import prepare
from .transcript import parse_transcript

__all__ = ['build', 'prepare', 'parse_transcript', 'compose']
