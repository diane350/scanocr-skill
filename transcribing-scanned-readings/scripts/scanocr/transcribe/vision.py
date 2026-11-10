"""Google Cloud Vision OCR for a scanned PDF: a copy with an invisible text layer, so that
``prepare`` can draft each page from it. Run on your own computer, as only the OCR call goes
out to Google.

    python -m scanocr.transcribe ocr <scan.pdf> [-o out.pdf] [--pages 1-3,7] [--lang en,hi]
                                     [--limit 900] [--dry-run] [--force]

Writes ``<scan>_ocr.pdf`` (page images unchanged) and ``<scan>_ocr.txt`` (the OCR text).
Needs ``pip install 'scanocr[vision]'`` and a Google sign-in: ``gcloud auth application-default
login``, or the app's own (``--sign-in client.json``, see vision_auth). No key file.
Each page's result is kept in ``<out>_vision_cache/``, so a re-run costs nothing, and a
monthly count of pages sent (~/.local/state/scanocr/usage.json) stops the run before ``--limit`` (free tier: 1,000 units a month; check Google's current pricing).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from . import vision_auth

MAX_BYTES = 7_000_000       # raw bytes: base64 adds ~37%, which stays under the 10 MB request limit
LEDGER = Path.home() / '.local/state/scanocr/usage.json'
FONTS = ('/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
         '/usr/share/fonts/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/TTF/DejaVuSans.ttf')
NO_SPACE = 0.12             # a next word closer than this (of the line's height) may be part of the word
OPENING = '(["\'‘“«'        # a word ending like this is never followed by a space
ATTEMPTS = 4


def parse_pages(spec, n):
    """0-based page numbers for a spec like '1-3,7' (1-based); all pages if no spec."""
    if not spec or not spec.strip():
        return list(range(n))
    out = set()
    for part in spec.split(','):
        m = re.fullmatch(r'\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*', part)
        a, b = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else (0, 0)
        if not m or a < 1 or b < a:
            raise SystemExit(f'Pages: "{part.strip()}" is not a page or a range. Write them like 1-3,7.')
        out.update(range(a - 1, min(b, n)))
    if not out:
        raise SystemExit(f'Pages: "{spec.strip()}" is past the end: the scan has {n} pages.')
    return sorted(out)


def month_key():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def _ledger():
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, ValueError):
        return {}


def units_used():
    return _ledger().get(month_key(), 0)


def add_units(n):
    data = _ledger()
    data[month_key()] = data.get(month_key(), 0) + n
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data))


def encode(page):
    """(image bytes, width, height) of the page at the scan's own resolution, in grey; shrunk
    (and as JPEG) only if it would pass the request size limit."""
    images = page.get_images(full=True)
    # the scan is the page's largest picture (a stamp or logo may be listed before it)
    zoom = max(i[2] for i in images) / page.rect.width if images else 300 / 72
    while True:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False))
        for fmt, kw in (('png', {}), ('jpeg', {'jpg_quality': 92}), ('jpeg', {'jpg_quality': 85})):
            data = pix.tobytes(fmt, **kw)
            if len(data) <= MAX_BYTES:
                return data, pix.width, pix.height
        zoom *= 0.85


def _vision():
    try:
        from google.api_core import exceptions
        from google.cloud import vision
    except ImportError:
        raise SystemExit("The Vision client is not installed: pip install 'scanocr[vision]'")
    return vision, exceptions


def vision_recognizer(hints):
    """A function image bytes -> {'text': ..., 'words': [[word, x0, y0, x1, y1], ...]} that
    asks Google Cloud Vision (DOCUMENT_TEXT_DETECTION), retrying when the service is busy."""
    vision, exceptions = _vision()
    creds = vision_auth.credentials()       # the app's own sign-in, else gcloud's
    try:
        client = vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:                  # no credentials
        raise SystemExit(f'{e}\nSign in with the app (File > OCR a scan) or: gcloud auth application-default login')
    busy = (exceptions.ServiceUnavailable, exceptions.DeadlineExceeded, exceptions.InternalServerError)

    def recognize(data):
        request = vision.AnnotateImageRequest(
            image=vision.Image(content=data),
            features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
            image_context=vision.ImageContext(language_hints=hints))
        for attempt in range(ATTEMPTS):
            try:
                response = client.annotate_image(request=request)
                break
            except busy:
                if attempt == ATTEMPTS - 1:
                    raise
                time.sleep(2 ** attempt)
        if response.error.message:
            raise RuntimeError(response.error.message)
        return result_of(response.full_text_annotation)
    return recognize


def result_of(annotation):
    """A Vision full-text annotation as {'text', 'words'}: each word with its box (px)."""
    words = []
    for pg in annotation.pages:
        for block in pg.blocks:
            for para in block.paragraphs:
                for w in para.words:
                    xs = [v.x for v in w.bounding_box.vertices]
                    ys = [v.y for v in w.bounding_box.vertices]
                    words.append([''.join(s.text for s in w.symbols), min(xs), min(ys), max(xs), max(ys)])
    return {'text': annotation.text, 'words': words}


def find_font():
    """A DejaVu Sans file (it has the accents and macrons a reading needs), or None."""
    for path in FONTS:
        if os.path.exists(path):
            return path
    if shutil.which('fc-match'):
        r = subprocess.run(['fc-match', '-f', '%{file}', 'DejaVu Sans'], capture_output=True, text=True)
        if r.returncode == 0 and 'dejavu' in r.stdout.lower():
            return r.stdout
    return None


def _line_sizes(words):
    """(font size, baseline) for each word, the same for all the words of a line. A word's own
    box is as tall as its tallest letter (\"on\" is half the height of \"the\"), so sizing words
    by their boxes made the type of one line jump about and a line of short words look like
    small print (a note) to ``prepare``. Words are on one line when their centres are level."""
    order = sorted(range(len(words)), key=lambda k: (words[k][2] + words[k][4]) / 2)
    out = [None] * len(words)
    group = []

    def close():
        if not group:
            return
        heights = sorted(words[k][4] - words[k][2] for k in group)
        top = min(heights[-1], 1.4 * heights[len(heights) // 2])     # a stray tall box doesn't count
        bottoms = sorted(words[k][4] for k in group)
        baseline = bottoms[len(bottoms) // 2] - top * 0.2
        for k in group:
            out[k] = (max(top * 0.85, 2), baseline)

    for k in order:
        cy = (words[k][2] + words[k][4]) / 2
        if group:
            ref = sum((words[j][2] + words[j][4]) / 2 for j in group) / len(group)
            tall = max(words[j][4] - words[j][2] for j in group)
            if abs(cy - ref) > 0.6 * tall:
                close()
                group = []
        group.append(k)
    close()
    return out


def add_text_layer(page, result, font):
    """The OCR words as invisible text, each word placed and stretched to its box. No space is
    added after a word the next one touches on the same line when that one is punctuation (or
    this one an opening bracket or quote): it was never a separate word on the page. Two
    letter words close together (tight justified lines) keep their space."""
    sx_page, sy_page = page.rect.width / result['w'], page.rect.height / result['h']
    words = []
    for text, x0, y0, x1, y1 in result['words']:
        x0, x1, y0, y1 = x0 * sx_page, x1 * sx_page, y0 * sy_page, y1 * sy_page
        if y1 > y0 and x1 > x0:
            words.append((text, x0, y0, x1, y1))
    sizes = _line_sizes(words)
    for i, (text, x0, y0, x1, y1) in enumerate(words):
        h = y1 - y0
        size, base_y = sizes[i]
        natural = font.text_length(text, fontsize=size)
        stretch = min(max((x1 - x0) / natural, 0.3), 3) if natural > 0 else 1
        space = ' '
        if i + 1 < len(words):
            _, nx0, ny0, _, ny1 = words[i + 1]
            touches = abs((ny0 + ny1) / 2 - (y0 + y1) / 2) < h * 0.6 and 0 <= nx0 - x1 < h * NO_SPACE
            if touches and (not words[i + 1][0][:1].isalnum() or text[-1] in OPENING):
                space = ''
        base = pymupdf.Point(x0, base_y)
        writer = pymupdf.TextWriter(page.rect)
        # stretched to the box in width, and squeezed in height by the same factor: a reader
        # of the layer takes the type size from the area of the text matrix, which must stay
        # the line's size, not change from word to word
        writer.append(base, text + space, font=font, fontsize=size)
        writer.write_text(page, render_mode=3, morph=(base, pymupdf.Matrix(stretch, 1 / stretch)))


def _scan_mark(src, n):
    """What tells one scan from another: its page count and a digest of the file."""
    digest = hashlib.sha256()
    with open(src, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return {'pages': n, 'sha256': digest.hexdigest()}


def _others_cache(cache, mark):
    """Does the cache folder hold another scan's results? Its pages are named by number only,
    so a different scan saved under the same name would be given the old scan's text. A cache
    from before scans were marked is taken as this scan's."""
    try:
        return json.loads((cache / 'scan.json').read_text(encoding='utf-8')) != mark
    except (OSError, ValueError):
        return False


def ocr(src, out=None, pages=None, lang='en', limit=900, dry_run=False, force=False, recognize=None, log=print,
        stop=None):
    """OCR the pages of ``src`` into ``out`` (default: <src>_ocr.pdf) and its .txt. Returns
    the PDF's path (None for a dry run or a stopped one). ``recognize``: image bytes -> result,
    instead of Google's (for tests); ``log``: where the progress lines go; ``stop``: a
    threading.Event; once set no further page is sent (pages done stay in the cache)."""
    src = Path(src)
    out = Path(out) if out else src.with_name(src.stem + '_ocr.pdf')
    if out.resolve() == src.resolve():
        raise SystemExit('The output must be a different file from the input.')
    if out.suffix.lower() != '.pdf':
        raise SystemExit(f'The OCR\'d copy is a PDF: give it a name ending .pdf, not {out.name}.')
    cache = out.with_name(out.stem + '_vision_cache')
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise SystemExit(f'{src.name} could not be opened as a PDF: {e}')
    chosen = parse_pages(pages, len(doc))
    cached = lambda p: cache / f'page_{p + 1:04d}.json'
    mark = _scan_mark(src, len(doc))
    stale = _others_cache(cache, mark)
    if stale:
        log('The saved OCR results next to this file are from another scan: they are not used.')
    cached_ok = lambda p: not stale and cached(p).is_file()
    todo = [p for p in chosen if not cached_ok(p)]
    used = units_used()
    log(f'{len(chosen)} pages selected, {len(todo)} need OCR (the rest are cached). '
          f'Units used this month by this tool: {used}')
    if dry_run:
        for p in chosen:
            data, w, h = encode(doc[p])
            log(f'page {p + 1}: sending {w}x{h}px, {len(data) / 1e6:.2f} MB' + ('  [cached]' if p not in todo else ''))
        log(f'Dry run: would use {len(todo)} units. Nothing was sent.')
        return None
    if used + len(todo) > limit and not force:
        raise SystemExit(f'Stopping: {used} + {len(todo)} would pass your limit of {limit} a month. '
                         'Use --force to override.')
    if recognize is None and todo:
        recognize = vision_recognizer([h.strip() for h in lang.split(',') if h.strip()])
    cache.mkdir(exist_ok=True)
    if stale:
        for old in cache.glob('page_*.json'):
            old.unlink()
        stale = False
    (cache / 'scan.json').write_text(json.dumps(mark), encoding='utf-8')
    fontfile = find_font()
    if fontfile is None:
        log('WARNING: DejaVu Sans not found; accents may be lost in the text layer.')
    font = pymupdf.Font(fontfile=fontfile) if fontfile else pymupdf.Font('helv')
    texts = []
    for p in chosen:
        page = doc[p]
        if stop is not None and stop.is_set():
            log(f'Stopped: the pages done so far are kept, and a new run carries on from page {p + 1}.')
            return None
        if cached_ok(p):
            result = json.loads(cached(p).read_text())
        else:
            data, w, h = encode(page)
            result = {**recognize(data), 'w': w, 'h': h}
            cached(p).write_text(json.dumps(result))
            add_units(1)
            log(f'page {p + 1}: OCR done ({len(result["words"])} words)')
        if page.rotation:
            log(f'page {p + 1}: WARNING page rotation {page.rotation}; text may be misplaced')
        add_text_layer(page, result, font)
        texts.append(f'=== page {p + 1} ===\n{result["text"]}\n')
    doc.save(out, garbage=3, deflate=True)
    out.with_suffix('.txt').write_text('\n'.join(texts), encoding='utf-8')
    log(f'Saved {out} and {out.with_suffix(".txt")}. Units used this month: {units_used()}')
    return out
