"""Turning uploads into page images (moved from cells/step4)."""
import os

import pymupdf as fitz

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff')


def pdf_to_images(pdf_path, out_dir, dpi=200):
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    paths = []
    for i, page in enumerate(doc):
        p = os.path.join(out_dir, f'page_{i+1:04d}.png')
        page.get_pixmap(matrix=mat).save(p)
        paths.append(p)
    doc.close()
    return paths


def page_images(src, out_dir, dpi=200):
    """Page images for one input file: rendered PDF pages, or the image itself."""
    ext = os.path.splitext(src)[1].lower()
    if ext == '.pdf':
        return pdf_to_images(src, out_dir, dpi)
    if ext in IMAGE_EXTS:
        return [src]
    raise ValueError(f'Not a PDF or image: {os.path.basename(src)}')
