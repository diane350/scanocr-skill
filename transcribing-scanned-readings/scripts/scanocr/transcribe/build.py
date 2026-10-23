"""build: check the transcriptions, then write the clean text and the searchable PDF."""
import json
import os
import re

import numpy as np
import pymupdf as fitz
from PIL import Image

from ..ocr.transcribed import Transcribed
from ..pdfout import build_searchable_pdf, summary
from ..pdfout.embed import padded_area
from ..pdfout.lines import detect_lines
from .compose import HEADING_RE, compose, label_rules, page_labels
from .footnotes import add_pdf_links, link_notes
from .transcript import ITALIC_RE, is_part, parse_transcript, transcript_path

# running heads and footers stay visible in the scan but out of the copyable text, so a
# copy across pages reads straight through (all but the page number: see text_layer)
IN_LAYER = ('text', 'table', 'footnote')
TABLE_RULE_RE = re.compile(r'[\s|:-]+')


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def layer_lines(section):
    """A section's printed lines for the text layer: italics, heading marks and the "+ "
    continuation mark dropped; a table's rows with their cells side by side."""
    if section.kind == 'table':
        rows = [l for l in section.lines if l.strip() and not TABLE_RULE_RE.fullmatch(l)]
        lines = [' '.join(c.strip() for c in row.strip().strip('|').split('|') if c.strip()) for row in rows]
    else:
        lines = [HEADING_RE.sub('', l) for l in section.lines if l.strip()]
        lines[:1] = [l.removeprefix('+ ') for l in lines[:1]]
    # italics can run over a line break, so they come off the lines together
    return ITALIC_RE.sub(r'\1', '\n'.join(lines)).split('\n') if lines else lines


def load(workdir, partial=False):
    """The job, every page's parsed transcription, and the problems. With ``partial``,
    pages that aren't ready (missing, not done, or with problems) are None instead."""
    with open(os.path.join(workdir, 'job.json')) as f:
        job = json.load(f)
    pages, problems = [], []
    for i, blocks in enumerate(job['blocks'], 1):
        try:
            with open(transcript_path(workdir, i), encoding='utf-8') as f:
                page = parse_transcript(f.read(), len(blocks))
        except OSError:
            pages.append(None)
            if not partial:
                problems.append(f'page {i}: transcription file is missing')
            continue
        page_problems = [f'page {i}: {p}' for p in page.problems]
        if not page.problems and page.status != 'done':
            page_problems.append(f'page {i}: not proofread yet (status: {page.status}); check every line, '
                                 'then change its first line to "# status: done"')
        if partial and page_problems:
            page = None
        else:
            problems += page_problems
        pages.append(page)
    if partial:
        pages = pages + [None] * (len(job['blocks']) - len(pages))
        problems = [p for p in problems if not p.endswith('transcription file is missing')]
    return job, pages, problems


def section_boxes(page, blocks, gray):
    """Each section's box in pixels. A block split into parts (the same number repeated,
    e.g. body then footnotes with no gap between) shares its printed lines out among the
    parts, in order, by how many lines each part has."""
    secs = page.sections
    boxes = [union(blocks[s.first - 1:s.last]) for s in secs]
    i = 0
    while i < len(secs):
        j = i
        while j + 1 < len(secs) and is_part(secs[j], secs[j + 1]):
            j += 1
        if j > i:
            box = boxes[i]
            area, inner_y = padded_area(box, gray.shape[1], gray.shape[0])
            found = detect_lines(gray, *area, inner_y)
            counts = [len(layer_lines(s)) for s in secs[i:j + 1]]
            total, start = max(1, sum(counts)), 0
            for k, n in enumerate(counts):
                end = len(found) if k == len(counts) - 1 else start + round(n * len(found) / total)
                part = found[start:end]
                top = part[0][0] if part else (found[start - 1][1] if start else box[1])
                boxes[i + k] = [box[0], top, box[2], part[-1][1] if part else top + 1]
                start = end
        i = j + 1
    return boxes


def is_page_number(section, label):
    """Is ``section`` the printed page number (a header holding just the page's label)?"""
    return section.kind == 'header' and bool(label) and ' '.join(layer_lines(section)).strip() == label


def text_layer(page, gray, boxes, i, mismatched, label=''):
    layer = []
    # body text first, footnotes next, the printed page number last: copying from one
    # column or page into the next doesn't run through the notes (viewers like Zotero copy
    # in this order). The page number is in the layer because Zotero reads page numbers off
    # the page and ignores the PDF's page labels when none on the page agrees with them.
    order = {'footnote': 1, 'header': 2}
    for s, box in sorted(zip(page.sections, boxes), key=lambda sb: order.get(sb[0].kind, 0)):
        if s.kind not in IN_LAYER and not is_page_number(s, label):
            continue
        lines = layer_lines(s)
        layer.append({'category': s.kind, 'box': box, 'lines': lines})
        area, inner_y = padded_area(box, gray.shape[1], gray.shape[0])
        found = len(detect_lines(gray, *area, inner_y))
        if found != len(lines):
            mismatched.append(f'page {i} {s.name}: {len(lines)} line(s) typed, {found} found on the scan')
    return json.dumps({'blocks': layer}, ensure_ascii=False)


def build(workdir, outdir, replace_old_text=True, partial=False):
    """Write the .md and the searchable PDF. ``partial``: build the pages that are done
    (the others keep their old text and show as a gap in the .md)."""
    job, pages, problems = load(workdir, partial)
    if problems:
        print('Not built yet. Fix these in the transcription files, then build again:')
        print('\n'.join(f'  {p}' for p in problems))
        return None
    if partial and not any(pages):
        print('No page is done yet: nothing to build.')
        return None
    labels = page_labels([p.number if p else '' for p in pages])
    notes, note_problems = link_notes(pages, labels)
    mismatched = []
    raws, boxes = [], []
    for i, (page, image, blocks) in enumerate(zip(pages, job['pages'], job['blocks']), 1):
        if page is None:
            raws.append(None)
            boxes.append([])
            continue
        gray = np.array(Image.open(image).convert('L'))
        boxes.append(section_boxes(page, blocks, gray))
        raws.append(text_layer(page, gray, boxes[-1], i, mismatched, labels[i - 1]))

    stem = os.path.splitext(os.path.basename(job['source']))[0]
    os.makedirs(outdir, exist_ok=True)
    md = os.path.join(outdir, f'{stem}.md')
    with open(md, 'w', encoding='utf-8') as f:
        f.write(compose(pages, labels, notes))
    pdf = os.path.join(outdir, f'{stem}_searchable.pdf')
    stats = build_searchable_pdf(job['source'], job['pages'], raws, Transcribed(), pdf, replace_old_text,
                                 remove_watermarks=False)    # Claude marks watermarks as skip
    print(summary(os.path.basename(pdf), stats))
    sizes = [Image.open(image).size for image in job['pages']]
    linked = add_pdf_links(pdf, notes, boxes, sizes)
    if rules := label_rules(labels):
        with fitz.open(pdf) as doc:                  # Zotero shows and cites these numbers
            doc.set_page_labels(rules)
            doc.saveIncr()
    if notes.notes:
        print(f'Footnotes: {len(notes.notes)} found, {linked} linked both ways in the PDF')
    if note_problems:
        print('Footnotes to check:')
        print('\n'.join(f'  {p}' for p in note_problems))
    if mismatched:
        print('Line breaks to check (those blocks were placed by width instead of by your lines):')
        print('\n'.join(f'  {m}' for m in mismatched))
    if partial:
        print(f'Partial build: {sum(1 for p in pages if p)} of {len(pages)} pages')
    print(f'Done:\n  {md}\n  {pdf}')
    return md, pdf
