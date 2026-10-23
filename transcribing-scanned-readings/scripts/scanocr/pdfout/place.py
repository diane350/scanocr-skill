"""Write invisible text over the page (moved from cells/step6)."""
import pymupdf as fitz

from .font import ASCENDER, DESCENDER, FONT_NAME, text_length
from .lines import detect_lines, split_words_to_lines


def to_page_rect(box, space, page_rect, img_w, img_h):
    """Box in the engine's coordinates -> PDF page rectangle (None if off the page)."""
    x1, y1, x2, y2 = box
    if space == 'grid999':    # model uses a 0-999 grid
        sx, sy = page_rect.width / 999, page_rect.height / 999
    else:                     # pixel coordinates of the rendered page image
        sx, sy = page_rect.width / img_w, page_rect.height / img_h
    r = fitz.Rect(x1 * sx, y1 * sy, x2 * sx, y2 * sy) & page_rect
    return r if not r.is_empty else None


def place_block_lines(page, gray, box_px, text, sx, sy, inner_y=None, lines=None):
    """Lay text line by line over the printed lines. Returns False if no lines were found.
    ``box_px`` is the padded search area, ``inner_y`` the block itself (see detect_lines).
    ``lines``: the text's printed lines, if known; used as they are when as many lines
    were found on the image, otherwise the words are shared out over the found lines."""
    x0, y0, x1, y1 = box_px
    found = detect_lines(gray, x0, y0, x1, y1, inner_y)
    if not found:
        return False
    if lines and len(lines) == len(found):
        parts = lines
    else:
        flat = ' '.join(text.split())
        parts = split_words_to_lines(flat, [f[3] - f[2] for f in found], [f[4] for f in found])
    # One font size for the whole block, sized so each line's highlight box covers one
    # printed line without reaching into the next. Overlapping or uneven boxes make PDF
    # viewers that sort text by position (poppler: Okular, Evince) jumble the order.
    heights = sorted(b - t for t, b, *_ in found)
    box_h = heights[len(heights) // 2]
    if len(found) > 1:
        pitch = sorted(found[k + 1][0] - found[k][0] for k in range(len(found) - 1))
        box_h = min(box_h, pitch[len(pitch) // 2])
    fs = max(2.0, box_h * sy / (ASCENDER - DESCENDER))
    for (t, b, l, r, _), part in zip(found, parts):
        if not part:
            continue
        left, right = l * sx, r * sx
        # centre the box on the printed line
        baseline = (t + b) / 2 * sy + (ASCENDER + DESCENDER) / 2 * fs
        natural = text_length(part, fs)
        if natural <= 0:
            continue
        stretch = (right - left) / natural
        pt = fitz.Point(left, baseline)
        page.insert_text(pt, part, fontsize=fs, fontname=FONT_NAME, render_mode=3,
                         morph=(pt, fitz.Matrix(stretch, 1)))
    return True

def add_invisible_text(page, rect, text):
    n_lines = max(1, text.count('\n') + 1)
    size = max(4, min(rect.height / n_lines * 0.8, 24))
    while size >= 2:
        if page.insert_textbox(rect, text, fontsize=size, fontname=FONT_NAME, render_mode=3) >= 0:
            return True
        size *= 0.8
    # Too long to fit the box: put it in anyway at a tiny size so it's still searchable
    page.insert_text(rect.tl + (0, 2), text.replace('\n', ' '), fontsize=1, fontname=FONT_NAME, render_mode=3)
    return False
