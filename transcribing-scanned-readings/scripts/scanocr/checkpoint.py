"""Save each page's OCR result as soon as it is done, so a disconnect loses at most one page.

Layout, under ``base`` (Google Drive's MyDrive/OCR in Colab, or a local folder):
  <doc>/progress-<id>/job.json         what this run is: file name, checksum, settings
  <doc>/progress-<id>/page_NNNN.json   {"text": ..., "raw": ...} for each finished page
  <doc>/<doc>.md, <doc>_searchable.pdf, <doc>_results.zip   copied there when done

<id> comes from the file's contents and the settings that change the OCR output (DPI,
engine, model revision and its settings). The same file with the same settings resumes;
a different file with the same name, or another DPI, gets its own progress folder.
Options that only affect the finishing step (watermarks, old text layer) are not part
of it: finishing reruns from the saved pages in seconds.
"""
import hashlib
import json
import os
import shutil

from .ocr import PageOutput


def file_checksum(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path, data):
    """Write whole or not at all: a half-written file never looks like a finished page."""
    tmp = path + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


class Checkpoint:
    def __init__(self, base, src, engine, dpi):
        self.stem = os.path.splitext(os.path.basename(src))[0]
        self.doc_dir = os.path.join(base, self.stem)
        checksum = file_checksum(src)
        settings = {'dpi': int(dpi), **engine.settings()}
        key = hashlib.sha256(json.dumps([checksum, settings], sort_keys=True).encode()).hexdigest()[:12]
        self.dir = os.path.join(self.doc_dir, f'progress-{key}')
        os.makedirs(self.dir, exist_ok=True)
        job = os.path.join(self.dir, 'job.json')
        if not os.path.exists(job):
            _write_json(job, {'file': os.path.basename(src), 'sha256': checksum, 'settings': settings})

    def _page(self, i):
        return os.path.join(self.dir, f'page_{i:04d}.json')

    def load(self, i):
        """Page i's saved result, or None if it isn't (completely) saved."""
        try:
            with open(self._page(i), encoding='utf-8') as f:
                d = json.load(f)
            return PageOutput(text=d['text'], raw=d['raw'])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, i, out):
        """Returns False (reason in .error) if the page couldn't be saved, e.g. Drive is
        full or briefly unreachable: the run goes on, that page just isn't kept."""
        try:
            _write_json(self._page(i), {'text': out.text, 'raw': out.raw})
            return True
        except OSError as e:
            self.error = str(e)
            return False

    def done_pages(self, n):
        return [i for i in range(1, n + 1) if self.load(i) is not None]

    def keep_results(self, *paths):
        """Copy the finished files next to the progress folder."""
        for p in paths:
            shutil.copy(p, os.path.join(self.doc_dir, os.path.basename(p)))
        return self.doc_dir
