"""Find the blocks of text on a page image, for pages transcribed rather than OCRed.

Recursive XY-cut: split the page at blank horizontal bands (between a heading and the
body, the body and its footnotes), then at blank vertical bands (between columns, or a
sideways margin strip and the text), and repeat inside each piece. Paragraphs that touch
stay in one block: that's fine, the text layer is placed line by line inside it.
"""
import numpy as np

from .lines import ink_mask


def _runs(on):
    """[(start, end)] of the stretches where ``on`` is True."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
    return list(zip(edges[::2], edges[1::2]))


def _split(profile, min_gap):
    """Pieces of a 1-D ink profile separated by at least ``min_gap`` empty positions."""
    pieces = _runs(profile > 1)          # a pixel or two is noise, not ink
    if not pieces:
        return []
    out = [list(pieces[0])]
    for a, b in pieces[1:]:
        if a - out[-1][1] >= min_gap:
            out.append([a, b])
        else:
            out[-1][1] = b
    return out


def _cut(ink, x0, y0, x1, y1, row_gap, col_gap, out):
    region = ink[y0:y1, x0:x1]
    rows = _split(region.sum(1), row_gap)
    if len(rows) > 1:
        for a, b in rows:
            _cut(ink, x0, y0 + a, x1, y0 + b, row_gap, col_gap, out)
        return
    cols = _split(region.sum(0), col_gap)
    if len(cols) > 1:
        for a, b in cols:
            _cut(ink, x0 + a, y0, x0 + b, y1, row_gap, col_gap, out)
        return
    if rows and cols:
        out.append((x0 + cols[0][0], y0 + rows[0][0], x0 + cols[0][1], y0 + rows[0][1]))


def find_blocks(gray):
    """Text blocks on a page image, in reading order: [(x0, y0, x1, y1)] in pixels."""
    ink = ink_mask(gray)
    rows = _runs(ink.sum(1) > 1)
    heights = [b - a for a, b in rows if b - a > 2]
    lh = float(np.median(heights)) if heights else 20.0       # typical printed line height
    # rules under a running head or above footnotes are not text: leave them out, so they
    # neither glue the head to the body nor pass for a line of it
    for a, b in rows:
        if b - a <= 0.2 * lh:
            ink[a:b] = False
    # a new block needs a blank band clearly wider than the page's usual space between
    # lines (tight books: a few pixels; typescripts: half a line or more)
    text_rows = [r for r in rows if r[1] - r[0] > 0.2 * lh]
    gaps = [b[0] - a[1] for a, b in zip(text_rows, text_rows[1:])]
    row_gap = max(0.5 * lh, 1.8 * float(np.median(gaps))) if gaps else 0.5 * lh
    out = []
    _cut(ink, 0, 0, ink.shape[1], ink.shape[0], row_gap, 1.5 * lh, out)   # columns: a gutter wider than a line is high
    # specks and dust are small
    return [b for b in out if b[3] - b[1] >= 0.35 * lh and b[2] - b[0] >= 0.35 * lh]
