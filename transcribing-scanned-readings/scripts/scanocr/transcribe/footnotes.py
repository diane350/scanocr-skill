"""Footnotes: notes read from footnote blocks, linked to their markers in the text.

In a footnote block each note starts with its marker (¹ ² ... or * † ‡ §), notes are
separated by a blank line, and a further paragraph without a marker belongs to the note
before it. A footnote block that starts without a marker continues the last note of the
page before (a note run on across the page break).

A marker in the text links to the note with the same marker on its page, else on the
next page. In the clean text markers become Markdown footnotes ([^3]); the id is the
printed number when it is unique in the document, else the page and number (47-3).
In the PDF each marker and its note are linked both ways.
"""
import re
from dataclasses import dataclass, field

import pymupdf as fitz

SUPERSCRIPTS = '⁰¹²³⁴⁵⁶⁷⁸⁹'
SYMBOLS = '*†‡§'
MARKER_CHARS = set(SUPERSCRIPTS + SYMBOLS)
TEXT_MARKER_RE = re.compile(rf'[{SUPERSCRIPTS}]+|(?<=\S)[{re.escape(SYMBOLS)}]+')
NOTE_START_RE = re.compile(rf'^([{SUPERSCRIPTS}]+|[{re.escape(SYMBOLS)}]+)\s*')
SYMBOL_IDS = dict(zip(SYMBOLS, 'abcd'))
BODY = ('text', 'table')


@dataclass
class Note:
    page: int                   # page index
    section: int                # section index on that page
    marker: str
    paras: list                 # paragraphs, each a list of printed lines (marker removed)
    id: str = ''
    linked: bool = False


@dataclass
class Ref:
    page: int
    section: int
    line: int
    start: int
    end: int
    marker: str
    note: Note = None


@dataclass
class Notes:
    notes: list = field(default_factory=list)
    refs: list = field(default_factory=list)


def _paragraphs(lines):
    paras = [[]]
    for line in lines:
        if line.strip():
            paras[-1].append(line)
        elif paras[-1]:
            paras.append([])
    return [p for p in paras if p]


def _read_notes(pages):
    notes, problems, last = [], [], None
    for pi, page in enumerate(pages):
        if page is None:
            last = None
            continue
        for si, s in enumerate(page.sections):
            if s.kind != 'footnote':
                continue
            for qi, para in enumerate(_paragraphs(s.lines)):
                m = NOTE_START_RE.match(para[0])
                if m:
                    last = Note(pi, si, m.group(1), [[para[0][m.end():]] + para[1:]])
                    notes.append(last)
                elif last is None:
                    problems.append(f'page {pi + 1}: footnote text with no marker and no note before it')
                elif qi == 0 and last.page < pi:
                    last.paras[-1].extend(para)         # the note runs on from the page before
                else:
                    last.paras.append(para)             # a further paragraph of the same note
    return notes, problems


def _value(marker):
    return marker.translate(str.maketrans(SUPERSCRIPTS, '0123456789'))


def link_notes(pages, labels):
    """Notes with their ids and the text markers pointing to them, plus problems
    (markers without a note, notes without a marker) for the build to report."""
    notes, problems = _read_notes(pages)
    refs = []
    for pi, page in enumerate(pages):
        for si, s in enumerate(page.sections if page else []):
            if s.kind in BODY:
                for li, line in enumerate(s.lines):
                    refs += [Ref(pi, si, li, m.start(), m.end(), m.group()) for m in TEXT_MARKER_RE.finditer(line)]
    for ref in refs:
        ref.note = next((n for n in notes if not n.linked and n.marker == ref.marker and n.page == ref.page), None) \
            or next((n for n in notes if not n.linked and n.marker == ref.marker and n.page == ref.page + 1), None)
        if ref.note:
            ref.note.linked = True
        else:
            problems.append(f'page {ref.page + 1}: marker {ref.marker} in the text has no note')
    problems += [f'page {n.page + 1}: note {n.marker} is not marked in the text' for n in notes if not n.linked]

    values = [_value(n.marker) for n in notes]
    for n, v in zip(notes, values):
        where = labels[n.page] or f'p{n.page + 1}'
        if v.isdigit():
            n.id = v if values.count(v) == 1 else f'{where}-{v}'
        else:
            n.id = f'{where}-{"".join(SYMBOL_IDS[c] for c in n.marker)}'
    return Notes(notes, [r for r in refs if r.note]), problems


def _marker_runs(page):
    """[(marker text, rect)] for every run of marker characters in the page's text layer."""
    runs = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                text, rect = '', None
                for ch in span['chars'] + [{'c': ' ', 'bbox': None}]:
                    if ch['c'] in MARKER_CHARS:
                        text += ch['c']
                        rect = fitz.Rect(ch['bbox']) if rect is None else rect | fitz.Rect(ch['bbox'])
                    elif text:
                        runs.append((text, rect))
                        text, rect = '', None
    return runs


def add_pdf_links(pdf_path, notes, section_boxes, image_sizes):
    """Link each marker to its note and back. ``section_boxes[page][section]``: the
    section's box in pixels of the page image, ``image_sizes[page]``: (width, height)."""
    doc = fitz.open(pdf_path)
    runs = {}

    def locate(pno, si, marker, nth):
        if pno not in runs:
            runs[pno] = _marker_runs(doc[pno])
        rect, (w, h) = doc[pno].rect, image_sizes[pno]
        x0, y0, x1, y1 = section_boxes[pno][si]
        pad = 0.01 * rect.height              # lines may sit in the padding around a block
        box = fitz.Rect(x0 * rect.width / w - pad, y0 * rect.height / h - pad,
                        x1 * rect.width / w + pad, y1 * rect.height / h + pad)
        hits = sorted((r for t, r in runs[pno] if t == marker and box.contains((r.tl + r.br) / 2)),
                      key=lambda r: (round(r.y0), r.x0))
        return hits[nth] if nth < len(hits) else None

    def nth_of(items, item, key):
        same = [x for x in items if key(x) == key(item)]
        return same.index(item)

    linked = 0
    for ref in notes.refs:
        n = ref.note
        at = locate(ref.page, ref.section, ref.marker,
                    nth_of(notes.refs, ref, lambda r: (r.page, r.section, r.marker)))
        to = locate(n.page, n.section, n.marker, nth_of(notes.notes, n, lambda x: (x.page, x.section, x.marker)))
        if at is None or to is None:
            continue
        doc[ref.page].insert_link({'kind': fitz.LINK_GOTO, 'from': at + (-1, -2, 1, 2), 'page': n.page,
                                   'to': fitz.Point(to.x0, to.y0), 'zoom': 0})
        doc[n.page].insert_link({'kind': fitz.LINK_GOTO, 'from': to + (-1, -2, 1, 2), 'page': ref.page,
                                 'to': fitz.Point(at.x0, at.y0), 'zoom': 0})
        linked += 1
    doc.saveIncr()
    doc.close()
    return linked
