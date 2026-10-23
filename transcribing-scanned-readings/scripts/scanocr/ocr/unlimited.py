"""baidu/Unlimited-OCR, gundam mode, one page at a time (moved from cells/step2 + step4).

What model.infer(save_results=True) does (checked in modeling_unlimitedocr.py
at the pinned revision):
  * streams the raw output, <|det|> boxes included, to stdout
  * saves <output_path>/result.md with the boxes removed
  * returns None
So the raw version with boxes has to come from captured stdout.
Boxes are on a 0-999 grid: the model's own drawing code does x / 999 * width.
"""
import contextlib
import glob
import io
import os
import re

from .base import Block, OCREngine, PageOutput

MODEL_NAME = 'baidu/Unlimited-OCR'
# Commit the user's tested Guha run used (repo last changed 2026-10-14).
# Pinned so the downloaded model code can't change under us.
MODEL_REVISION = '8249d20b2822a7d8a4c22c8d8c9069b5ac36bda3'

# infer() prints this after the streamed output; anything from here on is log, not OCR text
SAVE_BANNER = '=' * 15 + 'save results:' + '=' * 15
BLOCK_RE = re.compile(r'<\|det\|>\s*([^<\[\s]*)\s*\[\[?\s*([\d.\s,]+?)\s*\]?\]\s*<\|/det\|>(.*)')
TAG_RE = re.compile(r'<\|/?[a-z_]+\|>')


class UnlimitedOCR(OCREngine):
    name = 'unlimited'
    label = 'Unlimited-OCR (baidu, GPU)'

    # gundam mode; README settings for single images
    INFER_KWARGS = dict(
        prompt='<image>document parsing.',
        base_size=1024, image_size=640, crop_mode=True,
        max_length=8192,
        no_repeat_ngram_size=35, ngram_window=128,
        save_results=True,
    )

    def __init__(self, model=None, tokenizer=None, revision=MODEL_REVISION):
        self.model, self.tokenizer, self.revision = model, tokenizer, revision

    def settings(self):
        return {'engine': self.name, 'model': MODEL_NAME, 'revision': self.revision,
                **{k: v for k, v in self.INFER_KWARGS.items() if k != 'save_results'}}

    def load(self):
        # Hide noisy warnings (pad token, attention mask, progress bars)
        import warnings
        import torch
        import transformers
        from transformers import AutoModel, AutoTokenizer
        warnings.filterwarnings('ignore')
        transformers.logging.set_verbosity_error()
        if self.model is not None:
            return
        assert torch.cuda.is_available(), \
            'No GPU found. Go to Runtime > Change runtime type > T4 GPU, then run again.'
        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_NAME, trust_remote_code=True, revision=self.revision)
        model = AutoModel.from_pretrained(
            MODEL_NAME,
            trust_remote_code=True,
            use_safetensors=True,
            torch_dtype=torch.bfloat16,
            revision=self.revision,
        )
        self.model = model.eval().cuda()

    def ocr_page(self, image_path, work_dir):
        os.makedirs(work_dir, exist_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            result = self.model.infer(
                self.tokenizer,
                image_file=image_path,
                output_path=work_dir,
                **self.INFER_KWARGS,
            )
        printed = strip_log(buf.getvalue())
        saved =sorted(glob.glob(os.path.join(work_dir, '**', '*.mmd'), recursive=True) +
                       glob.glob(os.path.join(work_dir, '**', '*.md'), recursive=True) +
                       glob.glob(os.path.join(work_dir, '**', '*.txt'), recursive=True))
        saved_text = '\n\n'.join(open(f, encoding='utf-8').read() for f in saved)
        returned = result if isinstance(result, str) else ''
        # Raw version with layout boxes: whichever output actually contains the <|det|> tags
        raw = next((t for t in (returned, printed, saved_text) if '<|det|>' in t), returned or printed or saved_text)
        text = saved_text or returned or printed
        self._free_gpu_memory()
        return PageOutput(text=text, raw=raw)

    @staticmethod
    def _free_gpu_memory():
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def parse_layout(self, raw):
        """[Block] from the model's raw output. Tags stripped, text not yet cleaned."""
        blocks, cur = [], None
        for line in strip_log(raw).splitlines():
            m = BLOCK_RE.search(line)
            if m:
                if cur:
                    blocks.append(cur)
                nums = [float(n) for n in re.split(r'[\s,]+', m.group(2).strip()) if n]
                cat = (m.group(1) or '').strip()
                cur = [cat, nums[:4], [m.group(3).strip()]] if len(nums) >= 4 else None
            elif cur is not None:
                cur[2].append(line.strip())
        if cur:
            blocks.append(cur)
        out = []
        for cat, box, lines in blocks:
            text = TAG_RE.sub('', '\n'.join(l for l in lines if l)).strip()
            space = 'grid999' if max(box) <= 1000 else 'pixels'
            out.append(Block(cat, box, text, space))
        return out

    def layout_free_text(self, raw):
        return TAG_RE.sub('', strip_log(raw))


def strip_log(printed):
    """Drop infer()'s own log lines from captured stdout. Raw files saved by the
    notebook still contain them, so this also runs when reading those."""
    cut = printed.find(SAVE_BANNER)
    if cut >= 0:
        printed = printed[:cut]
    # printed before the output when the page image is resized directly
    return re.sub(r'\A(directly resize\n)+', '', printed)
