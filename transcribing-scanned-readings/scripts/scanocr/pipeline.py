"""One document from upload to clean .md + searchable PDF (cells/step4-6 as functions).

Files under ``root`` keep the notebook's layout:
  work/<doc>/page_NNNN.png, work/<doc>/out_NNNN/   page images, engine scratch
  raw_tags/<doc>/page_NNNN.txt, info.json           raw engine output per page
  outputs/<doc>.md                                  page texts joined
  clean/<doc>.md, searchable/<doc>_searchable.pdf   the results
"""
import json
import os

from .inputs import page_images
from .pdfout import build_searchable_pdf, summary
from .postprocess import clean_markdown, join_pages


def _dirs(root, stem):
    d = {k: os.path.join(root, k) for k in ('outputs', 'raw_tags', 'work', 'clean', 'searchable')}
    d['doc_work'] = os.path.join(d['work'], stem)
    d['doc_raw'] = os.path.join(d['raw_tags'], stem)
    for p in d.values():
        os.makedirs(p, exist_ok=True)
    return d


def iter_ocr_document(engine, src, root, dpi=200, log=print, checkpoint=None):
    """OCR every page of ``src``, one at a time. Yields (page image paths, page
    number, PageOutput, restored): once with (pages, 0, None, False) when the pages are
    rendered, then after each page is done. ``restored`` pages come from ``checkpoint``
    (a scanocr.checkpoint.Checkpoint) instead of the engine; new pages are saved to it.
    The joined .md is written at the end."""
    stem = os.path.splitext(os.path.basename(src))[0]
    d = _dirs(root, stem)
    pages = page_images(src, d['doc_work'], dpi)
    log(f'{os.path.basename(src)}: {len(pages)} page(s)')
    yield pages, 0, None, False
    texts = []
    for i, page in enumerate(pages, 1):
        out = checkpoint.load(i) if checkpoint else None
        restored = out is not None
        if restored:
            log(f'  Page {i}/{len(pages)}: restored from saved progress')
        else:
            log(f'  OCR page {i}/{len(pages)}...')
            out = engine.ocr_page(page, os.path.join(d['doc_work'], f'out_{i:04d}'))
            if checkpoint and not checkpoint.save(i, out):
                log(f'  Could not save page {i} ({checkpoint.error}); it will be redone if this run is resumed')
        texts.append(out.text)
        with open(os.path.join(d['doc_raw'], f'page_{i:04d}.txt'), 'w', encoding='utf-8') as f:
            f.write(out.raw)
        yield pages, i, out, restored
    with open(os.path.join(d['doc_raw'], 'info.json'), 'w') as f:
        json.dump({'source': src, 'pages': pages}, f)
    out_file = os.path.join(d['outputs'], f'{stem}.md')
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(join_pages(texts))
    log(f'  Saved {out_file}')


def ocr_document(engine, src, root, dpi=200, log=print, checkpoint=None):
    """OCR every page of ``src``. Returns (page image paths, page texts, page raws)."""
    texts, raws = [], []
    for pages, i, out, _ in iter_ocr_document(engine, src, root, dpi, log, checkpoint):
        if out is not None:
            texts.append(out.text)
            raws.append(out.raw)
    return pages, texts, raws


def finish_document(engine, src, pages, texts, raws, root, replace_old_text=True,
                    log=print, remove_watermarks=True):
    """Clean text and searchable PDF from OCR results. Returns (clean .md path, PDF path)."""
    stem = os.path.splitext(os.path.basename(src))[0]
    d = _dirs(root, stem)
    raw_md = join_pages(texts)
    text = clean_markdown(raw_md, remove_watermarks)
    clean_path = os.path.join(d['clean'], f'{stem}.md')
    with open(clean_path, 'w', encoding='utf-8') as f:
        f.write(text)
    log(f'Cleaned {stem}.md ({len(raw_md)} -> {len(text)} characters)')

    pdf_path = os.path.join(d['searchable'], f'{stem}_searchable.pdf')
    stats = build_searchable_pdf(src, pages, raws, engine, pdf_path, replace_old_text, remove_watermarks)
    log(summary(os.path.basename(pdf_path), stats))
    return clean_path, pdf_path


def process_document(engine, src, root, dpi=200, replace_old_text=True, log=print,
                     remove_watermarks=True):
    pages, texts, raws = ocr_document(engine, src, root, dpi, log)
    return finish_document(engine, src, pages, texts, raws, root, replace_old_text, log,
                           remove_watermarks)
