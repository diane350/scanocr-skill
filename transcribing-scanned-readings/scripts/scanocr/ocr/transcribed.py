"""Pages transcribed by a person or by Claude instead of a model (see scanocr.transcribe).

Not in ENGINES: it can't OCR a page by itself; its pages come from transcription files.
raw = JSON {"blocks": [{"category": "text" | "header", "box": [x0, y0, x1, y1] in pixels
of the rendered page image, "lines": [the printed lines, italics marks removed]}]}
"""
import json

from .base import Block, OCREngine


def join_lines(lines):
    """Printed lines as one run of text: a word broken after a dash stays joined."""
    out = ''
    for line in lines:
        out += line if not out or out.endswith(('—', '–')) else ' ' + line
    return out


class Transcribed(OCREngine):
    name = 'transcribed'
    label = 'Transcribed by Claude'

    def load(self):
        pass

    def ocr_page(self, image_path, work_dir):
        raise NotImplementedError('Transcribed pages come from transcription files: see scanocr.transcribe')

    def parse_layout(self, raw):
        return [Block(b['category'], b['box'], join_lines(b['lines']), 'pixels', b['lines'])
                for b in json.loads(raw)['blocks']]

    def layout_free_text(self, raw):
        return '\n'.join(l for b in json.loads(raw)['blocks'] for l in b['lines'])
