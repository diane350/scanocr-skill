"""Clean Markdown from a scanned reading, transcribed page by page by Claude.

    python -m scanocr.transcribe prepare <scan.pdf or images...> <workdir> [--resume ZIP]
        writes pages/page_NNNN.png (the page), pages/page_NNNN_top.png and _bottom.png
        (its halves, larger, for small print) and page_NNNN.md, a page file: pre-filled
        from the PDF's old text layer (status: draft) or empty (status: todo)
    python -m scanocr.transcribe status <workdir>
    python -m scanocr.transcribe build <workdir> <out.md> [--partial]
        joins the done pages into one .md and lists anything to fix
    python -m scanocr.transcribe pack <workdir> <progress.zip>

A page file is plain Markdown for that page, under two header lines:

    status: done             todo / draft / done; only done pages are built
    page: 47                 the printed page number; blank if none shows (counted on)

    A paragraph on one line, _italics_, a note marker[^12] as printed.

    > A displayed quotation.

    [^12]: The note's text. (A note running onto the next page: the next page's file
    starts its part with the same label, [^12]: ...)

Running heads, page numbers and watermarks are left out. Paragraphs, words broken by
a hyphen and notes are joined across pages by build; "+ " at the start of a page
forces a join.
"""
from .build import build
from .pages import pack, prepare, status

__all__ = ['build', 'pack', 'prepare', 'status']
