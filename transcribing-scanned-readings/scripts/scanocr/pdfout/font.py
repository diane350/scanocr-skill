"""The font of the invisible text layer.

Noto Sans (from pymupdf-fonts) rather than PDF's built-in Helvetica: Helvetica
can only hold Latin-1, so em dashes, curly quotes and superscripts past ³ came
out of copy-paste as '·'. The text is invisible, so the look doesn't matter;
only which characters it can hold.
"""
import pymupdf as fitz

FONT_NAME = 'notos'
_FONT = fitz.Font(FONT_NAME)
# a line of text spans (ASCENDER - DESCENDER) * fontsize: that is its highlight box
ASCENDER, DESCENDER = _FONT.ascender, _FONT.descender


def text_length(text, fontsize):
    return _FONT.text_length(text, fontsize=fontsize)
