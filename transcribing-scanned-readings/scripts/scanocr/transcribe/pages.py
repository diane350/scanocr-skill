"""Page files: preparing them (page images, a draft from the PDF's old text layer),
reading them back, progress, and carrying them over to a later session."""
import json
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field

import pymupdf

from .text import LIGATURES, fix_old_text, join, number_value

STATUS_RE = re.compile(r'^\s*#?\s*status:\s*(\S+)', re.I)
PAGE_RE = re.compile(r'^\s*page:\s*(.*?)\s*$', re.I)
STATUSES = ('todo', 'draft', 'done')
DPI = 200
ZOOM_DPI = 300


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

def _text_lines(pdf_page):
    """Boxes of the page's text lines: (level ones, sideways ones such as a "Downloaded
    by..." watermark)."""
    level, sideways = [], []
    for b in pdf_page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            if ''.join(s['text'] for s in l['spans']).strip():
                (level if abs(l['dir'][1]) < 0.1 else sideways).append(pymupdf.Rect(l['bbox']))
    return level, sideways


def _printed_area(small, scale, ignore=()):
    """The box (in units of ``small`` / ``scale``) holding the page's ink, with a margin:
    blank margins cost as much to look at as print. ``small``: the page at low resolution;
    ``ignore``: boxes (same units) whose ink doesn't count."""
    w, h, n, data = small.width, small.height, small.n, small.samples
    skip = [pymupdf.IRect(r.x0 * scale, r.y0 * scale, r.x1 * scale + 1, r.y1 * scale + 1) for r in ignore]
    rows, cols = [0] * h, [0] * w
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * n
            if min(data[i:i + min(n, 3)]) < 200 and not any(x in range(r.x0, r.x1) and y in range(r.y0, r.y1)
                                                          for r in skip):
                rows[y] += 1
                cols[x] += 1
    ys = [y for y, c in enumerate(rows) if c > 1]
    xs = [x for x, c in enumerate(cols) if c > 1]
    if not ys or not xs:
        return pymupdf.Rect(0, 0, w / scale, h / scale)
    pad = 0.02 * w
    return pymupdf.Rect(max(0, xs[0] - pad), max(0, ys[0] - pad),
                        min(w, xs[-1] + 1 + pad), min(h, ys[-1] + 1 + pad)) / scale


def _pdf_area(pdf_page):
    """The printed area: the ink, and every level line of the old text layer (faint small
    print can be lost at low resolution)."""
    small = pdf_page.get_pixmap(dpi=36)
    level, sideways = _text_lines(pdf_page)
    area = _printed_area(small, small.width / pdf_page.rect.width, sideways)
    for r in level:
        area |= r + (-6, -6, 6, 6)
    return (area + (pdf_page.rect.x0, pdf_page.rect.y0) * 2) & pdf_page.rect


def _image_area(pix):
    k = max(0, (max(pix.width, pix.height) // 300).bit_length() - 1)      # shrink by 2**k to ~300 px
    small = pymupdf.Pixmap(pix)
    if k:
        small.shrink(k)
    return _printed_area(small, small.width / pix.width).round() & pix.irect


def _clip(pix, box):
    part = pymupdf.Pixmap(pix.colorspace, box, pix.alpha)
    part.copy(pix, box)
    return part


def _render(sources, pages_dir):
    """(page image, its PDF page or None) for each page of the sources, in order.
    The image shows only the printed area of the page."""
    out = []
    for src in sources:
        try:
            doc = pymupdf.open(src)
        except Exception:
            raise SystemExit(f'Not a PDF or image: {src}')
        for pg in doc:
            path = os.path.join(pages_dir, f'page_{len(out) + 1:04d}.png')
            if doc.is_pdf:
                pg.get_pixmap(dpi=DPI, clip=_pdf_area(pg)).save(path)
                out.append((path, pg))
            else:                                   # an image: its own resolution
                pix = pymupdf.Pixmap(src)
                _clip(pix, _image_area(pix)).save(path)
                out.append((path, None))
    return out


def _source_page(workdir, i):
    """The ``i``th page (from 1) of the job's sources: (PDF page, None) or (None, image path)."""
    with open(os.path.join(workdir, 'job.json')) as f:
        sources = json.load(f)['sources']
    for src in sources:
        doc = pymupdf.open(src)
        if i <= len(doc):
            return (doc[i - 1], None) if doc.is_pdf else (None, src)
        i -= len(doc)
    raise SystemExit(f'There is no page {i}.')


def zoom(workdir, i, part):
    """A larger image of the top or bottom half of page ``i``'s printed area, for small print
    (notes, accents). Made only when asked for, as it costs as much to look at as the page."""
    pdf_page, image = _source_page(workdir, i)
    if pdf_page is not None:
        box = _pdf_area(pdf_page)
        y = box.y0 + box.height * (0.45 if part == 'bottom' else 0)
        pix = pdf_page.get_pixmap(dpi=ZOOM_DPI, clip=pymupdf.Rect(box.x0, y, box.x1, y + box.height * 0.55))
    else:                                           # an image: nothing finer than itself
        whole = pymupdf.Pixmap(image)
        box = _image_area(whole)
        y = box.y0 + int(box.height * (0.45 if part == 'bottom' else 0))
        pix = _clip(whole, pymupdf.IRect(box.x0, y, box.x1, y + int(box.height * 0.55)) & box)
    path = os.path.join(workdir, 'pages', f'page_{i:04d}_{part}.png')
    pix.save(path)
    print(path)
    return path


def _line_text(spans, body_size, note):
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


def draft(pdf_page):
    """A draft of the page from the PDF's old text layer, or None if it has none.
    Paragraphs come from indents and short lines, italics from the type styles,
    notes from the smaller type, note markers from the notes found; lines at the
    very top or bottom (running heads, page numbers) become comments, which build
    leaves out."""
    rows = []
    for b in pdf_page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            spans = [s for s in l['spans'] if s['text']]
            if spans and ''.join(s['text'] for s in spans).strip() and abs(l['dir'][1]) < 0.1:
                rows.append((l['bbox'], spans))           # (sideways watermarks left out)
    if not rows:
        return None
    rows.sort(key=lambda r: ((r[0][1] + r[0][3]) / 2, r[0][0]))
    lines = []                                         # merge pieces on one baseline
    for box, spans in rows:
        if lines and abs((box[1] + box[3]) / 2 - (lines[-1][0][1] + lines[-1][0][3]) / 2) < 3:
            pb, ps = lines[-1]
            lines[-1] = ((min(pb[0], box[0]), pb[1], max(pb[2], box[2]), pb[3]),
                         ps + [{**spans[0], 'text': ' ' + spans[0]['text']}] + spans[1:])
        else:
            lines.append((box, spans))
    body = _median([s['size'] for _, sp in lines for s in sp for _ in s['text']])
    height = pdf_page.rect.height
    numbers, head, foot, kept = [], [], [], []
    for box, spans in lines:
        note = _median([s['size'] for s in spans]) < 0.88 * body
        text = _line_text(spans, body, note).rstrip()
        text = fix_old_text(text[:-1] + '-' if text.endswith('\xad') else text).strip()
        if not text:
            continue
        if box[3] < 0.12 * height or box[1] > 0.93 * height:
            words = text.split()
            for w in {words[0], words[-1]}:
                w = w.strip('_')
                if w.isdigit() or (number_value(w) and len(words) <= 2):
                    numbers.append(w)
            (head if box[3] < 0.12 * height else foot).append(text)
            continue
        kept.append((box, note, text))
    if not kept:
        return None
    main = [b for b, n, _ in kept if not n] or [b for b, _, _ in kept]
    left, right = _median([b[0] for b in main]), _median([b[2] for b in main])
    paras, prev = [], None
    for box, note, text in kept:
        indent = box[0] > left + 0.5 * body
        starts_note = note and re.match(r'^(\d+|[*†‡§])\s', text)
        if starts_note:
            m = re.match(r'^(\d+|[*†‡§])\s+', text)
            text = f'[^{m.group(1)}]: ' + text[m.end():]
        new = (prev is None or note != prev[0] or starts_note or (indent and not note)
               or (prev[1][2] < right - 2 * body and re.search(r'[.:?!’”]_?$', paras[-1])))
        if new:
            paras.append(text)
        else:
            paras[-1] = join(paras[-1], text)
        prev = (note, box)
    notes = [p for p in paras if re.match(r'\[\^[^\]]+\]: ', p)]
    labels = {re.match(r'\[\^([^\]]+)\]', n).group(1) for n in notes}
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
                write_page(path, 'draft', got[0], got[1])
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
    for key, label in (('draft', 'Drafts to proofread'), ('todo', 'Not started')):
        if st[key]:
            print(f'{label}: pages {_ranges(st[key])}')
    return st


def pack(workdir, zip_path):
    """The page files in one small file, to upload in a later session (prepare --resume)."""
    names = sorted(n for n in os.listdir(workdir) if re.fullmatch(r'page_\d{4}\.md', n))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(workdir, n), n)
    print(f'Progress saved: {zip_path} ({len(names)} pages)')
    return zip_path
