"""Build the searchable PDF (moved from cells/step6).

The page still looks exactly the same; an invisible text layer is placed where
the engine found each block of text.
"""
import numpy as np
import pymupdf as fitz
from PIL import Image

from ..postprocess.textfix import WATERMARK_RE, fix_text
from .place import add_invisible_text, place_block_lines, to_page_rect


def usable_blocks(blocks, remove_watermarks=True):
    """Drop images, sideways margin strips and watermark blocks; clean the rest.
    Returns [(category, box, space, text, printed lines or None)]."""
    out = []
    for b in blocks:
        x1, y1, x2, y2 = b.box
        tall_thin = (x2 - x1) < 0.08 * max(1, (y2 - y1)) and (y2 - y1) > 0  # sideways margin strip
        watermark = tall_thin or WATERMARK_RE.search(b.text.replace('\n', ' '))
        if b.category == 'image' or (remove_watermarks and watermark):
            continue
        text = fix_text(b.text, remove_watermarks).strip()
        lines = [l for l in (fix_text(l, remove_watermarks).strip() for l in b.lines or []) if l]
        if text:
            out.append((b.category, b.box, b.space, text, lines or None))
    return out


def padded_area(box_px, img_w, img_h, pad=0.01):
    """Where to look for a block's printed lines: its box in image pixels, padded by 1% of
    the page so ascenders/descenders aren't cut. Returns (area, the block's own top/bottom)."""
    x0, y0, x1, y1 = box_px
    area = (max(0, int(x0 - pad * img_w)), max(0, int(y0 - pad * img_h)),
            min(img_w, int(x1 + pad * img_w)), min(img_h, int(y1 + pad * img_h)))
    return area, (y0, y1)


def open_source(src):
    """The original PDF, or a single image converted to a one-page PDF."""
    if src.lower().endswith('.pdf'):
        return fitz.open(src)
    doc = fitz.open()
    img = fitz.open(src)
    pdf_bytes = img.convert_to_pdf()
    doc.insert_pdf(fitz.open('pdf', pdf_bytes))
    return doc


def build_searchable_pdf(src, page_images, raws, engine, out_path, replace_old_text=True,
                         remove_watermarks=True):
    """Write ``out_path``. ``raws[i]`` is page i+1's raw engine output, or None if it
    has none. ``page_images[i]`` is the image that was OCRed. Returns a stats dict."""
    doc = open_source(src)
    placed = fallback = replaced = skipped = missing = 0
    for i, page in enumerate(doc, 1):
        raw = raws[i - 1] if i - 1 < len(raws) else None
        if raw is None:
            missing += 1
            continue
        old_text = page.get_text().strip()
        if old_text:
            if replace_old_text:
                # Remove the existing text layer (keeps the page images untouched)
                page.add_redact_annot(page.rect)
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                      graphics=fitz.PDF_REDACT_LINE_ART_NONE)
                replaced += 1
            else:
                skipped += 1
                continue
        page_img = Image.open(page_images[i - 1]).convert('L')
        img_w, img_h = page_img.size
        gray = np.array(page_img)
        sx, sy = page.rect.width / img_w, page.rect.height / img_h
        blocks = usable_blocks(engine.parse_layout(raw), remove_watermarks)
        if blocks:
            for _, box, space, text, lines in blocks:
                rect = to_page_rect(box, space, page.rect, img_w, img_h)
                if not rect:
                    continue
                px = (rect.x0 / sx, rect.y0 / sy, rect.x1 / sx, rect.y1 / sy)
                area, inner_y = padded_area(px, img_w, img_h)
                if not place_block_lines(page, gray, area, text, sx, sy, inner_y, lines):
                    add_invisible_text(page, rect, text)
                placed += 1
        else:
            # No layout boxes found: lay the whole page's text over the page
            text = fix_text(engine.layout_free_text(raw), remove_watermarks).strip()
            if text:
                add_invisible_text(page, page.rect, text)
                fallback += 1

    doc.subset_fonts()   # embed only the characters used
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return dict(placed=placed, fallback=fallback, replaced=replaced, skipped=skipped, missing=missing)


def summary(name, stats):
    """The per-document report, in plain words."""
    lines = [f'Saved {name}: {stats["placed"]} text blocks placed']
    if stats['replaced']: lines.append(f'  replaced the old text layer on {stats["replaced"]} page(s)')
    if stats['skipped']:  lines.append(f'  skipped {stats["skipped"]} page(s) that already had text')
    if stats['fallback']: lines.append(f'  {stats["fallback"]} page(s) had no layout info: text is searchable but not positioned')
    if stats['missing']:  lines.append(f'  {stats["missing"]} page(s) had no OCR output (did the OCR finish?)')
    return '\n'.join(lines)
