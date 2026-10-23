"""Page files: preparing them (page images, a draft from the PDF's old text layer),
reading them back, progress, and carrying them over to a later session."""
import json
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field

import pymupdf

from ..markup import NOTE_DEF_RE
from .text import LIGATURES, fix_old_text, join, number_value

STATUS_RE = re.compile(r'^\s*#?\s*status:\s*(\S+)', re.I)
PAGE_RE = re.compile(r'^\s*page:\s*(.*?)\s*$', re.I)
STATUSES = ('todo', 'draft', 'done')
DPI = 200
ZOOM_DPI = 300
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
    if page.number and not number_value(page.number):
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
                    pix = (pdf_page.get_pixmap(dpi=DPI, clip=area) if area
                           else pdf_page.get_pixmap(dpi=BLANK_DPI))
                else:                                   # an image: its own resolution
                    whole = _image_pixmap(image_page)
                    area = _image_area(whole)
                    pix = _clip(whole, area) if area else _shrunk(whole, BLANK_PX)
            except READ_ERRORS as e:
                raise SystemExit(f'Could not read page {n} of {src}: {e}')
            pix.save(path)
            out.append((path, pdf_page))
    return out


def _source_page(workdir, i):
    """The ``i``th page (from 1) of the job's sources: (PDF page, None) or (None, image page)."""
    with open(os.path.join(workdir, 'job.json')) as f:
        job = json.load(f)
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
        pix = pdf_page.get_pixmap(dpi=ZOOM_DPI, clip=pymupdf.Rect(box.x0, y, box.x1, y + box.height * 0.55))
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
        if small and t.strip().isdigit() and (k or not note):
            out.append(f'[^{t.strip()}]')
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


MARKER_RE = re.compile(r'(?<=[^\s\d(\[])(\d{1,3})(?=[\s,;:.]|$)')
HEAD_ZONE, FOOT_ZONE = 0.12, 0.93       # running heads and folios sit above / below these (of the height)


def _running_lines(lines, height, body_size):
    """Which of the page's lines (box, ...) are running heads, folios and the like: at the
    very top or bottom, and apart from the text by more than its line spacing (the last line
    of a long note can sit as low as a folio)."""
    gap = 0.5 * body_size
    out = set()
    heads = [k for k, (box, *_) in enumerate(lines) if box[3] < HEAD_ZONE * height]
    for k in reversed(heads):                      # upwards from the text
        if k + 1 in out or k + 1 == len(lines) or lines[k + 1][0][1] - lines[k][0][3] > gap:
            out.add(k)
    feet = [k for k, (box, *_) in enumerate(lines) if box[1] > FOOT_ZONE * height]
    for k in feet:                                 # downwards from the text
        if k - 1 in out or k == 0 or lines[k][0][1] - lines[k - 1][0][3] > gap:
            out.add(k)
    return out


def draft(pdf_page):
    """A draft of the page from the PDF's old text layer, or None if it has none.
    Paragraphs come from indents and short lines, italics from the type styles,
    notes from the smaller type, note markers from the notes found; running heads and
    folios become comments, which build leaves out."""
    rows = []
    for l in _text_layer_lines(pdf_page):
        spans = [s for s in l['spans'] if s['text']]
        if spans and ''.join(s['text'] for s in spans).strip() and _is_level(l):
            rows.append((l['bbox'], spans))
    if not rows:
        return None
    rows.sort(key=lambda r: ((r[0][1] + r[0][3]) / 2, r[0][0]))
    merged = []                                        # pieces on one baseline make one line
    for box, spans in rows:
        if merged and abs((box[1] + box[3]) / 2 - (merged[-1][0][1] + merged[-1][0][3]) / 2) < 3:
            pb, ps = merged[-1]
            merged[-1] = ((min(pb[0], box[0]), pb[1], max(pb[2], box[2]), pb[3]),
                          ps + [{**spans[0], 'text': ' ' + spans[0]['text']}] + spans[1:])
        else:
            merged.append((box, spans))
    body_size = _median([s['size'] for _, sp in merged for s in sp for _ in s['text']])
    lines = []
    for box, spans in merged:
        note = _median([s['size'] for s in spans]) < 0.88 * body_size
        text = _line_text(spans, note).rstrip()
        text = fix_old_text(text[:-1] + '-' if text.endswith('\xad') else text).strip()
        if text:
            lines.append((box, note, text))
    height = pdf_page.rect.height
    running = _running_lines(lines, height, body_size)
    numbers, head, foot, kept = [], [], [], []
    for k, (box, note, text) in enumerate(lines):
        if k not in running:
            kept.append((box, note, text))
            continue
        words = text.split()
        for w in dict.fromkeys((words[0], words[-1])):    # the first and last word, once each
            w = w.strip('_')
            if w.isdigit() or (number_value(w) and len(words) <= 2):
                numbers.append(w)
        (head if box[3] < HEAD_ZONE * height else foot).append(text)
    if not kept:
        return None
    main = [b for b, n, _ in kept if not n] or [b for b, _, _ in kept]
    left, right = _median([b[0] for b in main]), _median([b[2] for b in main])
    paras, prev = [], None
    for box, note, text in kept:
        indent = box[0] > left + 0.5 * body_size
        starts_note = note and re.match(r'^(\d+|[*†‡§])\s+', text)
        if starts_note:
            text = f'[^{starts_note.group(1)}]: ' + text[starts_note.end():]
        new = (prev is None or note != prev[0] or starts_note or (indent and not note)
               or (prev[1][2] < right - 2 * body_size and re.search(r'[.:?!’”]_?$', paras[-1])))
        if new:
            paras.append(text)
        else:
            paras[-1] = join(paras[-1], text)
        prev = (note, box)
    notes = [p for p in paras if NOTE_DEF_RE.match(p)]
    labels = {NOTE_DEF_RE.match(n).group(1) for n in notes}
    body_paras = [MARKER_RE.sub(lambda m: f'[^{m.group(1)}]' if m.group(1) in labels else m.group(1), p)
                  for p in paras if p not in notes]
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
    os.makedirs(pages_dir, exist_ok=True)
    carried = {}
    if resume:
        with zipfile.ZipFile(resume) as z:
            carried = {n: z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.md')}
    rendered = _render([os.path.abspath(s) for s in sources], pages_dir)
    counts = {s: 0 for s in STATUSES}
    for i, (image, pdf_page) in enumerate(rendered, 1):
        path = page_path(workdir, i)
        name = os.path.basename(path)
        if name in carried and not os.path.exists(path):
            with open(path, 'w', encoding='utf-8') as f:
                f.write(carried[name])
        if not os.path.exists(path):
            got = draft(pdf_page) if pdf_page is not None else None
            if got:
                write_page(path, 'draft', got[0], f'{TYPED}\n\n{got[1]}' if typed_text(pdf_page) else got[1])
            else:
                write_page(path, 'todo', '', '')
        with open(path, encoding='utf-8') as f:
            st = parse_page(f.read()).status
        counts[st] = counts.get(st, 0) + 1
    with open(os.path.join(workdir, 'job.json'), 'w') as f:
        json.dump({'sources': [os.path.abspath(s) for s in sources], 'pages': len(rendered)}, f)
    print(f'{len(rendered)} pages in {workdir}: ' +
          ', '.join(f'{n} {s}' for s, n in counts.items() if n))
    return len(rendered)


# -- progress ------------------------------------------------------------------

def load_pages(workdir):
    with open(os.path.join(workdir, 'job.json')) as f:
        n = json.load(f)['pages']
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
    """Page ``i``'s draft corrected and marked done, without writing the whole page out.
    ``corrections``: lines 'wrong text ==> right text' (\\n for a line break; none if the
    draft is right). Each wrong text must occur exactly once; if any doesn't, nothing is
    changed."""
    path = page_path(workdir, i)
    with open(path, encoding='utf-8') as f:
        text = f.read()
    errors, n = [], 0
    for line in corrections.splitlines():
        if not line.strip():
            continue
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
    write_page(path, 'done', page.number, page.body.replace(TYPED, '').strip())
    print(f'{os.path.basename(path)}: {n} fixes, done')


def pack(workdir, zip_path):
    """The page files in one small file, to upload in a later session (prepare --resume)."""
    names = sorted(n for n in os.listdir(workdir) if re.fullmatch(r'page_\d{4}\.md', n))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(workdir, n), n)
    print(f'Progress saved: {zip_path} ({len(names)} pages)')
    return zip_path
