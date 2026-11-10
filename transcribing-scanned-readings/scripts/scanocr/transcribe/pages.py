"""Page files: preparing them (page images, a draft from the PDF's old text layer),
reading them back, progress, and carrying them over to a later session."""
import json
import os
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from ..markup import NOTE_DEF_RE
from .text import (LIGATURES, NOT_COUNTED, STAR_RE, continues, fix_old_text, hyphen_vocabulary, join,
                   number_value)

STATUS_RE = re.compile(r'^\s*#?\s*status:\s*(\S+)', re.I)
PAGE_RE = re.compile(r'^\s*page:\s*(.*?)\s*$', re.I)
STATUSES = ('todo', 'draft', 'done')
DPI = 200
ZOOM_DPI = 300
MAX_PX = 2800                           # a page image's long edge is at most this
BLANK_DPI, BLANK_PX = 50, 400           # a blank page: enough to see that it is blank
TYPED = '<!-- typed text: proofread without opening the image -->'
FIX_SEP = ' ==> '


@dataclass
class Page:
    status: str = 'todo'
    number: str = ''           # printed page number as typed ('47', 'xii'), '' if none
    body: str = ''
    problems: list = field(default_factory=list)


def page_path(workdir, i):
    return os.path.join(workdir, f'page_{i:04d}.md')


def parse_page(text):
    """A page file: 'status:' and 'page:' lines, then the page's Markdown."""
    text = unicodedata.normalize('NFC', text).translate(LIGATURES)   # findable accents, ﬁ as fi
    page, lines = Page(), text.split('\n')
    head = 0
    while head < len(lines) and head < 3:
        if m := STATUS_RE.match(lines[head]):
            page.status = m.group(1).lower()
        elif m := PAGE_RE.match(lines[head]):
            page.number = m.group(1)
        elif lines[head].strip():
            break
        head += 1
    page.body = '\n'.join(lines[head:]).strip()
    if page.status not in STATUSES:
        page.problems.append(f'status should be todo, draft or done, not {page.status!r}')
    if page.number and page.number != NOT_COUNTED and not number_value(page.number):
        page.problems.append(f'page: {page.number!r} is not a page number')
    return page


def write_page(path, status, number, body):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'status: {status}\npage: {number}\n\n{body}\n')


# -- prepare -------------------------------------------------------------------

def _text_layer_lines(pdf_page):
    """The text layer's lines, placed as the page is shown (turned by its /Rotate, as the
    text layer is in the unturned page's units). Read without the page's images, which a
    scan is: copying them out made this ~100 times slower."""
    blocks = pdf_page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']
    lines = [l for b in blocks for l in b.get('lines', [])]
    if pdf_page.rotation:
        m = pdf_page.rotation_matrix
        turn = pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)
        for l in lines:
            l['bbox'] = tuple(pymupdf.Rect(l['bbox']) * m)
            l['dir'] = tuple(pymupdf.Point(l['dir']) * turn)
    return lines


def _is_level(line):
    """Not sideways, as a "Downloaded by..." watermark up the margin is."""
    return abs(line['dir'][1]) < 0.1


def _line_boxes(pdf_page):
    """Boxes of the page's text lines: (level ones, sideways ones)."""
    level, sideways = [], []
    for l in _text_layer_lines(pdf_page):
        if ''.join(s['text'] for s in l['spans']).strip():
            (level if _is_level(l) else sideways).append(pymupdf.Rect(l['bbox']))
    return level, sideways


def _printed_area(small, scale, ignore=()):
    """The box (in units of ``small`` / ``scale``) holding the page's ink, with a margin:
    blank margins cost as much to look at as print. None if there is no ink. ``small``:
    the page at low resolution; ``ignore``: boxes (same units) whose ink doesn't count."""
    w, h, n, data = small.width, small.height, small.n, small.samples
    is_dark = bytes(int(v < 200) for v in range(256))     # for translate: a byte to 1 or 0
    ink_bits = 0                                          # dark in any colour: 1 per pixel
    for c in range(min(n - small.alpha, 3)):
        ink_bits |= int.from_bytes(data[c::n].translate(is_dark), 'big')
    ink = bytearray(ink_bits.to_bytes(w * h, 'big'))
    whole = pymupdf.IRect(0, 0, w, h)
    for r in ignore:                                # IRect rounds outwards
        box = pymupdf.IRect(r.x0 * scale, r.y0 * scale, r.x1 * scale + 1, r.y1 * scale + 1) & whole
        if box.is_empty:
            continue
        for y in range(box.y0, box.y1):
            ink[y * w + box.x0:y * w + box.x1] = bytes(box.width)
    rows = [ink.count(1, y * w, y * w + w) for y in range(h)]
    cols = [ink[x::w].count(1) for x in range(w)]
    ys = [y for y, c in enumerate(rows) if c > 1]
    xs = [x for x, c in enumerate(cols) if c > 1]
    if not ys or not xs:
        return None
    pad = 0.02 * w
    return pymupdf.Rect(max(0, xs[0] - pad), max(0, ys[0] - pad),
                        min(w, xs[-1] + 1 + pad), min(h, ys[-1] + 1 + pad)) / scale


def _pdf_area(pdf_page):
    """The printed area: the ink, and every level line of the old text layer (faint small
    print can be lost at low resolution). None for a blank page."""
    small = pdf_page.get_pixmap(dpi=36)
    level, sideways = _line_boxes(pdf_page)
    boxes = [r + (-6, -6, 6, 6) for r in level]
    ink = _printed_area(small, small.width / pdf_page.rect.width, sideways)
    if ink is not None:
        boxes.append(ink)
    if not boxes:
        return None
    area = boxes[0]
    for box in boxes[1:]:
        area |= box
    return (area + (pdf_page.rect.x0, pdf_page.rect.y0) * 2) & pdf_page.rect


def _shrunk(pix, size):
    """A copy of ``pix`` shrunk by a power of 2 to about ``size`` px."""
    k = max(0, (max(pix.width, pix.height) // size).bit_length() - 1)
    small = pymupdf.Pixmap(pix)
    if k:
        small.shrink(k)
    return small


def _image_area(pix):
    """The printed area of a page image; None for a blank page."""
    small = _shrunk(pix, 300)
    area = _printed_area(small, small.width / pix.width)
    return None if area is None else area.round() & pix.irect


def _clip(pix, box):
    part = pymupdf.Pixmap(pix.colorspace, box, pix.alpha)
    part.copy(pix, box)
    return part


READ_ERRORS = (pymupdf.FileDataError, pymupdf.FileNotFoundError, pymupdf.mupdf.FzErrorBase, ValueError)


def _source_pages(src):
    """The pages of a PDF or an image file: [(PDF page, None) or (None, image page)]. An
    image file may hold several pages (a TIFF); see _image_pixmap."""
    try:
        doc = pymupdf.open(src)
        if doc.is_pdf:
            return [(pg, None) for pg in doc]
        pdf = pymupdf.open('pdf', doc.convert_to_pdf())       # a page per image, turned upright
        if not all(pg.get_images() for pg in pdf):
            raise ValueError('not an image')
        return [(None, pg) for pg in pdf]
    except READ_ERRORS:
        raise SystemExit(f'Not a PDF or image: {src}')


def _image_pixmap(image_page):
    """An image file's page at the image's own resolution: turned upright as the photo's
    EXIF says, in grey or RGB (a CMYK JPEG can't be saved as a PNG)."""
    doc = image_page.parent
    xref, _, w, h = image_page.get_images(full=True)[0][:4]
    zoom = max(w, h) / max(image_page.rect.width, image_page.rect.height)
    grey = doc.extract_image(xref)['colorspace'] == 1
    return image_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                 colorspace=pymupdf.csGRAY if grey else pymupdf.csRGB)


def _render(sources, pages_dir):
    """(page image, its PDF page or None) for each page of the sources, in order.
    The image shows only the printed area of the page (a blank page: all of it, small)."""
    out = []
    for src in sources:
        for n, (pdf_page, image_page) in enumerate(_source_pages(src), 1):
            path = os.path.join(pages_dir, f'page_{len(out) + 1:04d}.png')
            try:
                if pdf_page is not None:
                    area = _pdf_area(pdf_page)
                    # a page saved very large (ScanTailor writes 96-dpi page sizes) is not drawn larger
                    dpi = int(min(DPI, 72 * MAX_PX / max(pdf_page.rect.width, pdf_page.rect.height)))
                    pix = (pdf_page.get_pixmap(dpi=dpi, clip=area) if area
                           else pdf_page.get_pixmap(dpi=min(dpi, BLANK_DPI)))
                else:                                   # an image: its own resolution
                    whole = _image_pixmap(image_page)
                    area = _image_area(whole)
                    pix = _clip(whole, area) if area else _shrunk(whole, BLANK_PX)
            except READ_ERRORS as e:
                raise SystemExit(f'Could not read page {n} of {src}: {e}')
            pix.save(path)
            out.append((path, pdf_page))
    return out


def _job(workdir):
    """What ``prepare`` wrote down about the folder: its sources and number of pages."""
    try:
        with open(os.path.join(workdir, 'job.json')) as f:
            return json.load(f)
    except (OSError, ValueError):
        raise SystemExit(f'{workdir} is not a work folder yet: run prepare <scan> {workdir} first.')


def _source_page(workdir, i):
    """The ``i``th page (from 1) of the job's sources: (PDF page, None) or (None, image page)."""
    job = _job(workdir)
    if not 1 <= i <= job['pages']:
        raise SystemExit(f'There is no page {i}: the pages are 1 to {job["pages"]}.')
    pages = [p for src in job['sources'] for p in _source_pages(src)]
    return pages[i - 1]


def zoom(workdir, i, part):
    """A larger image of the top or bottom half of page ``i``'s printed area, for small print
    (notes, accents). Made only when asked for, as it costs as much to look at as the page."""
    pdf_page, image_page = _source_page(workdir, i)
    start = 0.45 if part == 'bottom' else 0
    if pdf_page is not None:
        box = _pdf_area(pdf_page) or pdf_page.rect
        y = box.y0 + box.height * start
        dpi = int(min(ZOOM_DPI, 72 * 1.5 * MAX_PX / max(pdf_page.rect.width, pdf_page.rect.height)))
        pix = pdf_page.get_pixmap(dpi=dpi, clip=pymupdf.Rect(box.x0, y, box.x1, y + box.height * 0.55))
    else:                                           # an image: nothing finer than itself
        whole = _image_pixmap(image_page)
        box = _image_area(whole) or whole.irect
        y = box.y0 + int(box.height * start)
        pix = _clip(whole, pymupdf.IRect(box.x0, y, box.x1, y + int(box.height * 0.55)) & box)
    path = os.path.join(workdir, 'pages', f'page_{i:04d}_{part}.png')
    pix.save(path)
    print(path)
    return path


def typed_text(pdf_page):
    """Whether the page's text layer is its own type (a PDF made from a document), not a
    scan's hidden OCR text: the text is visible, and no picture covers the page."""
    counts = [(t['type'], len(t['chars'])) for t in pdf_page.get_texttrace()]
    total = sum(n for _, n in counts)
    visible = sum(n for kind, n in counts if kind != 3)        # 3: invisible, as OCR text is
    page = abs(pdf_page.rect)
    covered = any(abs(pymupdf.Rect(im['bbox']) & pdf_page.rect) > 0.5 * page
                  for im in pdf_page.get_image_info())
    return total > 0 and visible > 0.9 * total and not covered


def _line_text(spans, note):
    """A line's text with _italics_ and note markers ([^12]) from the span styles."""
    out = []
    for k, sp in enumerate(spans):
        t = sp['text']
        if not t.strip():
            out.append(t)
            continue
        small = sp['size'] < 0.78 * max(s['size'] for s in spans)
        if small and re.fullmatch(r'\d{1,3}(?:\s*,\s*\d{1,3})*', t.strip()) and (k or not note):
            out.append(''.join(f'[^{n.strip()}]' for n in t.split(',')))      # 2,3: two markers
        elif sp['flags'] & 2:
            lead, core, tail = re.match(r'(\s*)(.*?)(\s*)$', t).groups()
            out.append(f'{lead}_{core}_{tail}')
        else:
            out.append(t)
    text = ''.join(out)
    return re.sub(r'_(\s*)_', r'\1', text)          # _one_ _two_ -> _one two_


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


# a note marker printed as plain text after a word: masses.3, Studies*
MARKER_RE = re.compile(r'(?<=[^\s\d(\[])(\d{1,3}|[*†‡§])(?=[\s,;:.)’”]|$)')
# a line that starts a note: its label as a marker (a superscript, which no line of the text
# itself starts with) or as plain text, with or without a space after it (33I am thinking)
NOTE_START_RE = re.compile(r'^(?:\[\^([^\]\s]+)\]:?\s*|(\d{1,3}|[*†‡§])(?:\s+|(?=[A-Z‘“"\'(_])))')
# ... but not a number that only follows a full stop: vol.2, p.23, Rs.5
ABBREVIATIONS = ('vol', 'vols', 'p', 'pp', 'no', 'nos', 'ch', 'chap', 'fig', 'figs', 'rs', 'art', 'sec', 'para',
                 'cf', 'f', 'ff', 'n', 'nn', 'bk', 'pt', 'col', 'cols', 'app', 'l', 'll', 'v', 'c', 'ca', 'ms', 'mss')


def _mark_plain(text, labels):
    """``text`` with the plain numbers and symbols that are note markers (their notes, ``labels``,
    are on the page) marked: masses.3 -> masses.[^3]. A decimal (3.5), a range (1914-18), a
    thousand (1,000), a date (12.3.1857) or a reference (vol.2, p.23) is not a marker."""
    def marked(m):
        label, before = m.group(1), text[:m.start()]
        if label not in labels:
            return label
        if label.isdigit():
            if not (before[-1].isalpha() or before[-1] in '.,;:!?)]’”\'"»_'):
                return label                                    # 1914–18, 24/7, £5
            if before[-1] in '.,' and (before[-2:-1].isdigit() or text[m.end():m.end() + 2][:1] == '.'
                                       and text[m.end() + 1:m.end() + 2].isdigit()):
                return label                                    # 3.5, 1,000, 12.3.1857
            word = re.search(r'([A-Za-z]+)\.$', before)
            if word and word.group(1).lower() in ABBREVIATIONS:
                return label                                    # vol.2
        return f'[^{label}]'
    return MARKER_RE.sub(marked, text)


COLUMN_GAP = 6                          # a gutter between two columns is at least this wide (points)
DROP_CAP = 1.8                          # an initial this much larger than the text's type
HEAD_ZONE, FOOT_ZONE = 0.12, 0.93       # running heads and folios sit above / below these (of the height)
TITLE_ZONE = 0.35                       # a chapter's title sits above this (of the height)
BIG = 1.2                               # type this much larger than the text's is a title or heading
NOTE_GAP = 1.5                          # a gap of this many line spacings sets the notes apart from the text


def _running_lines(lines, height, body_size):
    """Which of the page's lines (box, size, ...) are running heads, folios and the like: at
    the very top or bottom, and apart from the text by more than its line spacing (the last
    line of a long note can sit as low as a folio). A line in larger type than the text is
    not one: it is a chapter's title, which often carries a note (*)."""
    gap = 0.5 * body_size
    out = set()
    heads = [k for k, (box, size, *_) in enumerate(lines)
             if box[3] < HEAD_ZONE * height and size <= BIG * body_size]
    for k in reversed(heads):                      # upwards from the text
        if k + 1 in out or k + 1 == len(lines) or lines[k + 1][0][1] - lines[k][0][3] > gap:
            out.add(k)
    feet = [k for k, (box, *_) in enumerate(lines) if box[1] > FOOT_ZONE * height]
    for k in feet:                                 # downwards from the text
        if k - 1 in out or k == 0 or lines[k][0][1] - lines[k - 1][0][3] > gap:
            out.add(k)
    return out


def _next_label(label):
    return str(int(label) + 1) if label and label.isdigit() else None


REST_OF_NOTE = '<!-- the rest of a note from the page before: start it with that note\'s label, [^12]: -->'


def _paragraphs(kept, height, body_size, carried=None, vocab=()):
    """The kept lines (box, size, note, text) as paragraphs, headings ('# ...') and notes
    ('[^12]: ...'). The notes are found from their labels, not only from their smaller type
    (an OCR layer's type sizes are rough): a line starting with a superscript number, or with
    a number after a gap below the text, starts them, and every line from there on is a note
    or the rest of one. In the notes a new note starts at the next number, or a superscript.
    Small type above the first note is the rest of a note from the page before (``carried``:
    its label, if known)."""
    main = [b for b, _, n, _ in kept if not n] or [b for b, _, _, _ in kept]
    left, right = _median([b[0] for b in main]), _median([b[2] for b in main])
    steps = [b[3] - a[3] for (a, *_), (b, *_) in zip(kept, kept[1:]) if 0 < b[3] - a[3] < 3 * body_size]
    spacing = _median(steps) if steps else 1.2 * body_size
    paras, prev = [], None
    in_notes, sure, last = False, False, None      # sure: the notes were found by more than their size
    for j, (box, size, note, text) in enumerate(kept):
        start = NOTE_START_RE.match(text)
        label = start and (start.group(1) or start.group(2))
        marker = bool(start and start.group(1))
        gap = prev is not None and box[1] - prev[1][3] > NOTE_GAP * spacing - size
        if in_notes and not sure and not note and not marker:
            in_notes = False                       # small type that was not notes after all
        if start and in_notes:
            opens = (marker or label == _next_label(last) or not label.isdigit()
                     or not (last or '').isdigit())     # the first number after a symbol's note (*)
        elif start:
            opens = marker or note or (gap and box[1] > TITLE_ZONE * height)
            sure = marker or gap
        else:
            opens = False
        rest = (not in_notes and not start and note and prev is not None and not prev[3]
                and box[1] > TITLE_ZONE * height and _notes_follow(kept, j))
        if opens:
            in_notes, last = True, label
            text = f'[^{label}]: ' + text[start.end():]
        elif rest:                                 # the end of a note that began on the page before
            in_notes, sure, opens = True, True, True
            text = f'[^{carried}]: {text}' if carried else f'{REST_OF_NOTE}\n\n[^?]: {text}'
        big = size > BIG * body_size and not in_notes
        if big:
            text = ('# ' if box[1] < TITLE_ZONE * height else '## ') + text
        new = (prev is None or opens or (in_notes != prev[0]) or big != prev[2]
               or (not in_notes and box[0] > left + 0.5 * body_size)
               or (not in_notes and prev[1][2] < right - 2 * body_size and re.search(r'[.:?!’”]_?$', paras[-1])))
        if new:
            paras.append(text)
        else:
            paras[-1] = join(paras[-1], text, vocab)
        prev = (in_notes, box, big, note)
    return paras


def _notes_follow(kept, j):
    """Do the small-type lines from ``kept[j]`` on lead straight into a numbered note?"""
    for box, size, note, text in kept[j + 1:]:
        if not note:
            return False
        if m := NOTE_START_RE.match(text):
            return bool(m.group(1) or m.group(2))
    return False


def _gutter(pieces, top, bottom):
    """The x of the gap between two columns of text, or None: a strip no line of the text
    (between ``top`` and ``bottom``) crosses, with text on both sides of it side by side.
    A title or an abstract set across both columns may cross it."""
    body = [b for b, _ in pieces if b[1] >= top and b[3] <= bottom]
    if len(body) < 12:
        return None
    x0, x1 = min(b[0] for b in body), max(b[2] for b in body)
    rows = len({round((b[1] + b[3]) / 6) for b in body})          # lines of text, roughly
    best, run = None, None
    x = x0 + 0.25 * (x1 - x0)
    while x <= x0 + 0.75 * (x1 - x0):
        crossing = sum(b[0] < x < b[2] for b in body)
        if crossing <= 0.2 * rows:
            run = (run[0], x) if run else (x, x)
            if best is None or run[1] - run[0] > best[1] - best[0]:
                best = run
        else:
            run = None
        x += 1
    if best is None or best[1] - best[0] < COLUMN_GAP:
        return None
    g = (best[0] + best[1]) / 2
    left, right = [b for b in body if b[2] <= g], [b for b in body if b[0] >= g]
    if len(left) < 5 or len(right) < 5:
        return None
    # side by side, each column a block of full lines: not a list with a second, short column
    shared = min(max(b[3] for b in left), max(b[3] for b in right)) - max(min(b[1] for b in left), min(b[1] for b in right))
    wide = lambda col: max(b[2] for b in col) - min(b[0] for b in col)
    tall = lambda col: max(b[3] for b in col) - min(b[1] for b in col)
    if shared < 0.5 * min(tall(left), tall(right)) or min(wide(left), wide(right)) < 0.3 * (x1 - x0):
        return None
    lone = sum(1 for b in body if b[0] < g < b[2])
    return g if lone <= 0.2 * rows else None


def _side(box, gutter):
    """Which column a line is in: 0 left, 1 right, None across both (or no columns)."""
    if gutter is None or box[0] < gutter < box[2]:
        return None
    return 0 if box[2] <= gutter else 1


def _reading_order(kept, gutter):
    """The kept lines in runs to read one after the other: with two columns, the left column
    then the right, between the lines set across both."""
    runs, columns = [], ([], [])
    def flush():
        runs.extend(col[:] for col in columns if col)
        columns[0].clear(), columns[1].clear()
    for line in kept:
        side = _side(line[0], gutter)
        if side is None:
            flush()
            if runs and runs[-1] and _side(runs[-1][-1][0], gutter) is None and getattr(flush, 'across', False):
                runs[-1].append(line)
            else:
                runs.append([line])
            flush.across = True
        else:
            columns[side].append(line)
            flush.across = False
    flush()
    return runs


def _drop_capitals(kept, body_size):
    """A large initial joined to the line it starts (T / he day began -> The day began); the
    lines set in beside it are not indented."""
    out = list(kept)
    for cap in kept:
        box, size, _, text = cap
        if size < DROP_CAP * body_size or not (len(text) <= 2 and text.isalpha()):
            continue
        beside = [l for l in out if l is not cap and l[0][0] >= box[2] - 2 and l[0][1] < box[3] - 0.3 * body_size
                  and l[0][3] > box[1]]
        if not beside:
            continue
        first = min(beside, key=lambda l: l[0][1])
        for line in beside:
            k = out.index(line)
            b = line[0]
            out[k] = ((box[0], b[1], b[2], b[3]), line[1], line[2], (text if line is first else '') + line[3])
        out.remove(cap)
    return out


def draft(pdf_page, carried=None):
    """A draft of the page from the PDF's old text layer, or None if it has none.
    ``carried``: the label of the last note on the page before, which may end on this one.
    Two columns are read one after the other.
    Paragraphs come from indents and short lines, italics from the type styles, headings
    from larger type, notes from their labels and smaller type, note markers from the
    notes found; running heads and folios become comments, which build leaves out."""
    rows = []
    for l in _text_layer_lines(pdf_page):
        spans = [s for s in l['spans'] if s['text']]
        if spans and ''.join(s['text'] for s in spans).strip() and _is_level(l):
            rows.append((l['bbox'], spans))
    if not rows:
        return None
    rows.sort(key=lambda r: ((r[0][1] + r[0][3]) / 2, r[0][0]))
    height = pdf_page.rect.height
    gutter = _gutter(rows, HEAD_ZONE * height, FOOT_ZONE * height)
    in_columns = lambda box: _side(box, gutter) is not None
    centre = lambda box: (box[1] + box[3]) / 2
    level = []                                         # the pieces on one baseline, left to right (a column at a time)
    for row in rows:
        if level and centre(row[0]) - centre(level[-1][-1][0]) < 3:
            level[-1].append(row)
        else:
            level.append([row])
    rows = [r for group in level
            for r in sorted(group, key=lambda r: ((_side(r[0], gutter) or 0) if in_columns(r[0]) else 0, r[0][0]))]
    merged = []                                        # pieces on one baseline make one line
    for box, spans in rows:
        apart = in_columns(box) and merged and _side(box, gutter) != _side(merged[-1][0], gutter)   # across the gutter
        if merged and not apart:                       # a drop capital stands apart from the lines beside it
            a, b = spans[0]['size'], merged[-1][1][0]['size']
            apart = max(a, b) >= DROP_CAP * min(a, b) and len(''.join(sp['text'] for sp in (spans if a > b else merged[-1][1])).strip()) <= 2
        if merged and not apart and abs((box[1] + box[3]) / 2 - (merged[-1][0][1] + merged[-1][0][3]) / 2) < 3:
            pb, ps = merged[-1]
            merged[-1] = ((min(pb[0], box[0]), pb[1], max(pb[2], box[2]), pb[3]),
                          ps + [{**spans[0], 'text': ' ' + spans[0]['text']}] + spans[1:])
        else:
            merged.append((box, spans))
    body_size = _median([s['size'] for _, sp in merged for s in sp for _ in s['text']])
    lines = []
    for box, spans in merged:
        size = _median([s['size'] for s in spans for _ in s['text'].strip()] or [s['size'] for s in spans])
        note = size < 0.88 * body_size
        text = _line_text(spans, note).rstrip()
        text = fix_old_text(text[:-1] + '-' if text.endswith('\xad') else text, star=False).strip()
        if text:
            lines.append((box, size, note, text))
    running = _running_lines(lines, height, body_size)
    numbers, head, foot, kept = [], [], [], []
    for k, (box, size, note, text) in enumerate(lines):
        if k not in running:
            kept.append((box, size, note, text))
            continue
        words = text.split()
        for w in dict.fromkeys((words[0], words[-1])):    # the first and last word, once each
            w = w.strip('_')
            if w.isdigit() or (number_value(w) and len(words) <= 2):
                numbers.append(w)
        (head if box[3] < HEAD_ZONE * height else foot).append(STAR_RE.sub('’', text))
    if not kept:
        return None
    vocab = hyphen_vocabulary('\n'.join(text for *_, text in kept))
    paras = []
    for run in _reading_order(_drop_capitals(kept, body_size), gutter):
        more = _paragraphs(run, height, body_size, carried, vocab)
        last = next((k for k in range(len(paras) - 1, -1, -1) if not NOTE_DEF_RE.match(paras[k])), None)
        if (last is not None and more and gutter is not None and not paras[last].startswith('#')
                and not re.match(r'#|\[\^|<!--', more[0]) and continues(paras[last], more[0])
                and _side(run[0][0], gutter) == 1):
            paras[last] = join(paras[last], more.pop(0), vocab)      # a paragraph running on in the next column
        paras += more
    notes = [STAR_RE.sub('’', p) for p in paras if NOTE_DEF_RE.match(p)]
    labels = {NOTE_DEF_RE.match(n).group(1) for n in notes}
    body_paras = [STAR_RE.sub('’', _mark_plain(p, labels)) for p in paras if not NOTE_DEF_RE.match(p)]
    parts = [f'<!-- {e} -->' for e in head] + body_paras + notes + [f'<!-- {e} -->' for e in foot]
    if len(set(numbers)) > 1:
        parts.insert(0, f'<!-- page numbers printed: {", ".join(dict.fromkeys(numbers))} -->')
    return (numbers[-1] if len(set(numbers)) == 1 else ''), '\n\n'.join(parts)


def prepare(sources, workdir, resume=None):
    """Page images and a page file per page. An existing page file is kept;
    ``resume``: a ``pack`` file from an earlier session."""
    if isinstance(sources, str):
        sources = [sources]
    pages_dir = os.path.join(workdir, 'pages')
    names = sorted(os.path.basename(s) for s in sources)
    if os.path.exists(os.path.join(workdir, 'job.json')):
        before = sorted(os.path.basename(s) for s in _job(workdir).get('sources', []))
        if before != names:                 # its page files would be kept, as this reading's
            raise SystemExit(f'{workdir} holds another reading ({", ".join(before)}). Use a new folder for '
                             f'{", ".join(names)}.')
    os.makedirs(pages_dir, exist_ok=True)
    carried = {}
    if resume:
        with zipfile.ZipFile(resume) as z:
            carried = {n: z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.md')}
    rendered = _render([os.path.abspath(s) for s in sources], pages_dir)
    counts = {s: 0 for s in STATUSES}
    last_note = None
    for i, (image, pdf_page) in enumerate(rendered, 1):
        path = page_path(workdir, i)
        name = os.path.basename(path)
        if name in carried and not os.path.exists(path):
            with open(path, 'w', encoding='utf-8') as f:
                f.write(carried[name])
        if not os.path.exists(path):
            got = draft(pdf_page, last_note) if pdf_page is not None else None
            if got:
                write_page(path, 'draft', got[0], f'{TYPED}\n\n{got[1]}' if typed_text(pdf_page) else got[1])
            else:
                write_page(path, 'todo', '', '')
        with open(path, encoding='utf-8') as f:
            page = parse_page(f.read())
        defs = [m.group(1) for line in page.body.split('\n') if (m := NOTE_DEF_RE.match(line))]
        last_note = defs[-1] if defs and defs[-1] != '?' else None      # it may end on the next page
        counts[page.status] = counts.get(page.status, 0) + 1
    _settle_numbers(workdir, len(rendered))
    with open(os.path.join(workdir, 'job.json'), 'w') as f:
        json.dump({'sources': [os.path.abspath(s) for s in sources], 'pages': len(rendered)}, f)
    print(f'{len(rendered)} pages in {workdir}: ' +
          ', '.join(f'{n} {s}' for s, n in counts.items() if n))
    if counts['todo'] == len(rendered) and any(pdf_page is not None for _, pdf_page in rendered):
        print('The PDF has no text layer, so every page is empty. For drafts to proofread, add one '
              'on your computer first: python -m scanocr.transcribe ocr <scan.pdf> (Google Cloud Vision).')
    return len(rendered)


PRINTED_RE = re.compile(r'<!-- page numbers printed: (.*?) -->\n*')


def _settle_numbers(workdir, n):
    """Where a draft found several numbers in a page's head and foot (a year or a volume
    beside the folio), take the one that fits the run of the other pages' numbers. Left as it
    is when no single run stands out (a reprint that prints two page numbers throughout)."""
    pages = {}
    for i in range(1, n + 1):
        try:
            with open(page_path(workdir, i), encoding='utf-8') as f:
                pages[i] = parse_page(f.read())
        except OSError:
            pass
    step = Counter(number_value(p.number)[0] - i for i, p in pages.items()
                   if p.status == 'draft' and number_value(p.number or '') and number_value(p.number)[1] == 'arabic')
    if not step:
        return
    (offset, votes), = step.most_common(1)
    if votes < 3 or votes < 0.6 * sum(step.values()):
        return
    for i, p in pages.items():
        m = PRINTED_RE.search(p.body)
        if p.status == 'draft' and not p.number and m and str(i + offset) in m.group(1).split(', '):
            write_page(page_path(workdir, i), 'draft', str(i + offset), PRINTED_RE.sub('', p.body, count=1))


# -- progress ------------------------------------------------------------------

def load_pages(workdir):
    n = _job(workdir)['pages']
    pages = []
    for i in range(1, n + 1):
        try:
            with open(page_path(workdir, i), encoding='utf-8') as f:
                pages.append(parse_page(f.read()))
        except OSError:
            pages.append(Page())
    return pages


def _ranges(numbers):
    out, start = [], None
    for a, b in zip(numbers, numbers[1:] + [None]):
        start = start or a
        if b != a + 1:
            out.append(f'{start}' if start == a else f'{start}-{a}')
            start = None
    return ', '.join(out)


def status(workdir):
    """Prints and returns {status: [page indexes]}."""
    st = {s: [] for s in STATUSES}
    for i, page in enumerate(load_pages(workdir), 1):
        st.setdefault(page.status, []).append(i)
    n = sum(len(v) for v in st.values())
    print(f'Done: {len(st["done"])} of {n} pages')
    typed = [i for i, page in enumerate(load_pages(workdir), 1) if page.status == 'draft' and TYPED in page.body]
    for key, label in (('draft', 'Drafts to proofread'), ('todo', 'Not started')):
        if st[key]:
            print(f'{label}: pages {_ranges(st[key])}')
    if typed:
        print(f'Of the drafts, typed text (no need to open the image): pages {_ranges(typed)}')
    return st


def fix(workdir, i, corrections):
    """``apply_fixes``, then say so."""
    n = apply_fixes(workdir, i, corrections)
    print(f'{os.path.basename(page_path(workdir, i))}: {n} fixes, done')


def apply_fixes(workdir, i, corrections, number=None):
    """Page ``i``'s draft corrected and marked done, without writing the whole page out;
    returns how many corrections were made. ``corrections``: lines 'wrong text ==> right text'
    (\\n for a line break; none if the draft is right). Each wrong text must occur exactly
    once; if any doesn't, nothing is changed. ``number``: the printed page number, if it
    is to be set."""
    path = page_path(workdir, i)
    if not os.path.exists(path):
        raise SystemExit(f'There is no page {i} in {workdir}.')
    with open(path, encoding='utf-8') as f:
        text = f.read()
    start = parse_page(text)
    if start.status == 'todo' and not start.body:
        raise SystemExit(f'{os.path.basename(path)} is empty: there is no draft to correct. Type the page '
                         f'(or, for a blank page, nothing) and write the file with status: done.')
    errors, n = [], 0
    for line in corrections.splitlines():
        if not line.strip():
            continue
        if line.rstrip().endswith(FIX_SEP.rstrip()) and not line.endswith(FIX_SEP):
            line = line.rstrip() + ' '             # 'wrong text ==>' deletes it; the space after is often trimmed
        if FIX_SEP not in line:
            errors.append(f'no "{FIX_SEP.strip()}" in: {line}')
            continue
        old, new = (part.replace('\\n', '\n') for part in line.split(FIX_SEP, 1))
        found = text.count(old)
        if found != 1:
            errors.append(f'{"not found" if not found else f"found {found} times"}: {old}')
            continue
        text = text.replace(old, new)
        n += 1
    if errors:
        raise SystemExit(f'{os.path.basename(path)} not changed. Use more words so each occurs once:\n  '
                         + '\n  '.join(errors))
    page = parse_page(text)
    write_page(path, 'done', page.number if number is None else number, page.body.replace(TYPED, '').strip())
    return n


def pack(workdir, zip_path):
    """The page files in one small file, to upload in a later session (prepare --resume)."""
    _job(workdir)
    names = sorted(n for n in os.listdir(workdir) if re.fullmatch(r'page_\d{4}\.md', n))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(workdir, n), n)
    print(f'Progress saved: {zip_path} ({len(names)} pages)')
    return zip_path
