"""Long readings: drafts pre-filled from a PDF's old text layer, progress per page, and
carrying the transcriptions over to a later session (cloud sessions are temporary)."""
import json
import os
import zipfile

from ..pdfout.embed import padded_area
from ..pdfout.lines import detect_lines
from .compose import number_value
from .transcript import TODO, parse_transcript, transcript_path

DRAFT_NOTE = ("# Pre-filled from the PDF's old text: check every line against the scan, fix it,\n"
              "# then change the first line to: # status: done\n")


def old_text_lines(pdf_page, gray, box, scale):
    """The PDF's existing words inside ``box`` (pixels), as the printed lines found there."""
    words = [w for w in pdf_page.get_text('words')
             if box[0] <= (w[0] + w[2]) / 2 * scale <= box[2] and box[1] <= (w[1] + w[3]) / 2 * scale <= box[3]]
    if not words:
        return []
    area, inner_y = padded_area(box, gray.shape[1], gray.shape[0])
    found = detect_lines(gray, *area, inner_y) or [(box[1], box[3], box[0], box[2], 0)]
    lines = [[] for _ in found]
    for w in words:
        cy = (w[1] + w[3]) / 2 * scale
        lines[min(range(len(found)), key=lambda k: abs(cy - (found[k][0] + found[k][1]) / 2))].append(w)
    return [' '.join(w[4] for w in sorted(line, key=lambda w: w[0])) for line in lines if line]


def draft_template(pdf_page, gray, blocks, scale):
    """A pre-filled transcription, or None if the page has no old text."""
    filled = [old_text_lines(pdf_page, gray, b, scale) for b in blocks]
    if not any(filled):
        return None
    number, kinds = '', [''] * len(blocks)
    height = gray.shape[0]
    for k, (lines, b) in enumerate(zip(filled, blocks)):
        edge = b[3] < 0.12 * height or b[1] > 0.88 * height
        if not number and edge and len(lines) == 1 and number_value(lines[0]):
            number = lines[0].strip()                      # a bare number at the top or bottom
            kinds[k] = ' header' if b[1] < height / 2 else ' footer'
    sections = ''.join(f'=== {k}{kind} ===\n' + ('\n'.join(lines) or TODO) + '\n\n'
                       for k, (lines, kind) in enumerate(zip(filled, kinds), 1))
    return f'# status: draft\n{DRAFT_NOTE}page: {number}\n\n{sections}'


def status(workdir, quiet=False):
    """Pages done, drafts, not started, and pages with problems. Prints a summary."""
    with open(os.path.join(workdir, 'job.json')) as f:
        job = json.load(f)
    st = {'done': [], 'draft': [], 'todo': [], 'problems': {}}
    for i, blocks in enumerate(job['blocks'], 1):
        try:
            with open(transcript_path(workdir, i), encoding='utf-8') as f:
                page = parse_transcript(f.read(), len(blocks))
        except OSError:
            st['todo'].append(i)
            continue
        if page.status != 'done':
            st[page.status if page.status in st else 'todo'].append(i)
        elif page.problems:
            st['problems'][i] = page.problems
        else:
            st['done'].append(i)
    if not quiet:
        n = len(job['blocks'])
        print(f'Done: {len(st["done"])} of {n} pages')
        for key, label in (('draft', 'Drafts to proofread'), ('todo', 'Not started')):
            if st[key]:
                print(f'{label}: pages {_ranges(st[key])}')
        for i, problems in st['problems'].items():
            print(f'Page {i}: ' + '; '.join(problems))
    return st


def _ranges(numbers):
    out, start = [], None
    for a, b in zip(numbers, numbers[1:] + [None]):
        start = start or a
        if b != a + 1:
            out.append(f'{start}' if start == a else f'{start}-{a}')
            start = None
    return ', '.join(out)


def pack(workdir, zip_path):
    """The transcriptions and the job, in one small file to keep and upload next time."""
    names = ['job.json'] + sorted(n for n in os.listdir(workdir) if n.startswith('page_') and n.endswith('.txt'))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(workdir, n), n)
    print(f'Progress saved: {zip_path} ({len(names) - 1} page files)')
    return zip_path


def resumed_pages(zip_path):
    """{page number: (its blocks then, its transcription)} from a ``pack`` file."""
    with zipfile.ZipFile(zip_path) as z:
        job = json.loads(z.read('job.json'))
        out = {}
        for i, blocks in enumerate(job['blocks'], 1):
            name = f'page_{i:04d}.txt'
            if name in z.namelist():
                out[i] = (blocks, z.read(name).decode('utf-8'))
    return job, out
