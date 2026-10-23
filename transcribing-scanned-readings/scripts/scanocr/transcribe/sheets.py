"""prepare: page images, text blocks, reading sheets and transcription templates."""
import json
import os

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from ..checkpoint import file_checksum
from ..inputs import page_images
from ..pdfout.blocks import find_blocks
from .progress import draft_template, resumed_pages
from .transcript import SECTION_RE, TODO, transcript_path


def _sheet(img, blocks, path):
    """The page with each block boxed and numbered in the margin beside it."""
    sheet = img.convert('RGB')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(16, img.height // 60))
    for k, (x0, y0, x1, y1) in enumerate(blocks, 1):
        draw.rectangle((x0 - 8, y0 - 8, x1 + 8, y1 + 8), outline=(220, 0, 0), width=3)
        label = str(k)
        lx = max(0, x0 - 14 - draw.textlength(label, font=font))
        draw.rectangle((lx - 4, y0 - 8, x0 - 10, y0 + font.size + 4), fill=(220, 0, 0))
        draw.text((lx, y0 - 6), label, fill=(255, 255, 255), font=font)
    scale = min(1.0, 1600 / sheet.height)
    sheet.resize((int(sheet.width * scale), int(sheet.height * scale))).save(path)


def _covers_all(text, n_blocks):
    found = {k for m in map(SECTION_RE.match, text.splitlines()) if m
             for k in range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1)}
    return found == set(range(1, n_blocks + 1))


def prepare(src, workdir, dpi=200, resume=None):
    """Page images, blocks, reading sheets and a transcription file per page: pre-filled
    from the PDF's old text layer when it has one (status draft), else to type (todo).
    An existing transcription is kept; ``resume``: a ``pack`` file from an earlier session."""
    src = os.path.abspath(src)
    for sub in ('pages', 'sheets', 'crops'):
        os.makedirs(os.path.join(workdir, sub), exist_ok=True)
    pages = page_images(src, os.path.join(workdir, 'pages'), dpi)
    job = {'source': src, 'sha256': file_checksum(src), 'dpi': dpi, 'pages': pages, 'blocks': []}
    old_job, carried = resumed_pages(resume) if resume else ({}, {})
    if resume and old_job.get('sha256') not in (None, job['sha256']):
        print(f'  Note: {os.path.basename(resume)} was made from a different file than {os.path.basename(src)}')
    pdf = fitz.open(src) if src.lower().endswith('.pdf') else None
    for i, page in enumerate(pages, 1):
        img = Image.open(page)
        gray = np.array(img.convert('L'))
        blocks = [[int(v) for v in b] for b in find_blocks(gray)]
        job['blocks'].append(blocks)
        _sheet(img, blocks, os.path.join(workdir, 'sheets', f'page_{i:04d}.png'))
        for k, (x0, y0, x1, y1) in enumerate(blocks, 1):
            img.crop((max(0, x0 - 6), max(0, y0 - 6), x1 + 6, y1 + 6)).save(
                os.path.join(workdir, 'crops', f'page_{i:04d}_b{k:02d}.png'))
        path = transcript_path(workdir, i)
        if i in carried:
            then, text = carried[i]
            with open(path if then == blocks else path + '.old', 'w', encoding='utf-8') as f:
                f.write(text)
            if then != blocks:
                print(f'  page {i}: the blocks changed, so its earlier transcription is now {path}.old')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                if not _covers_all(f.read(), len(blocks)):
                    os.replace(path, path + '.old')
                    print(f'  page {i}: the blocks changed, so its old transcription is now {path}.old')
        if not os.path.exists(path):
            text = draft_template(pdf[i - 1], gray, blocks, dpi / 72) if pdf else None
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text or '# status: todo\npage: \n\n'
                        + ''.join(f'=== {k} ===\n{TODO}\n\n' for k in range(1, len(blocks) + 1)))
    with open(os.path.join(workdir, 'job.json'), 'w') as f:
        json.dump(job, f, indent=1)
    print(f'{os.path.basename(src)}: {len(pages)} page(s) in {workdir}')
    for i, blocks in enumerate(job['blocks'], 1):
        print(f'  page {i}: {len(blocks)} block(s)  sheets/page_{i:04d}.png -> page_{i:04d}.txt')
    return job
