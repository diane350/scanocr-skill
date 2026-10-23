"""Clean text for quoting (moved from cells/step5):
- strips the model's layout tags (<|det|>...<|/det|>) and coordinates
- drops image placeholders and empty blocks
- removes headers/footers that repeat on most pages, and bare page numbers
- removes stuck "loop" lines the model sometimes repeats
- joins words split by a hyphen at a line break
"""
import re
from collections import Counter

from .textfix import fix_text

DET_RE = re.compile(r'<\|det\|>([^<\s]+)(?:\s*\[[^\]]*\])?\s*<\|/det\|>(.*)', re.DOTALL)
TAG_RE = re.compile(r'<\|/?[a-z_]+\|>')
PAGE_NUM_RE = re.compile(r'^\s*(page\s*)?[-–—]?\s*\d{1,4}\s*[-–—]?\s*(of\s*\d+)?\s*$', re.I)
PAGE_SEP = '\n\n---\n\n'


def remove_det(raw):
    blocks, cur = [], None
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:          # a blank line ends a paragraph
            if cur:
                blocks.append(cur)
                cur = None
            continue
        m = DET_RE.match(line)
        if m:
            category, content = m.group(1).strip(), m.group(2).strip()
            if cur is not None:
                blocks.append(cur)
            cur = None if category == 'image' else ([content] if content else [])
            continue
        if cur is None:
            cur = []
        cur.append(line)
    if cur is not None:
        blocks.append(cur)
    return '\n\n'.join('\n'.join(b) for b in blocks if b).strip()


def clean_page(text, remove_watermarks=True):
    text = remove_det(text)
    text = TAG_RE.sub('', text)
    text = fix_text(text, remove_watermarks)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)   # image links
    out, prev, repeats = [], None, 0
    for line in text.splitlines():
        s = line.strip()
        if PAGE_NUM_RE.match(s):
            continue
        if s and s == prev:          # same line over and over = model got stuck
            repeats += 1
            if repeats >= 1:
                continue
        else:
            repeats = 0
        prev = s
        out.append(line)
    return '\n'.join(out)


def join_pages(texts):
    """The per-document raw .md: pages marked with <!-- Page N --> and joined by ---."""
    return PAGE_SEP.join(f'<!-- Page {i} -->\n\n{t.strip()}' for i, t in enumerate(texts, 1))


def clean_markdown(raw, remove_watermarks=True):
    """Clean a whole document's raw .md (as written by join_pages)."""
    pages = [re.sub(r'<!-- Page \d+ -->', '', p).strip() for p in raw.split(PAGE_SEP)]
    pages = [clean_page(p, remove_watermarks) for p in pages]

    # Headers/footers: short lines that show up on at least half the pages (3+ pages)
    if len(pages) >= 3:
        counts = Counter()
        for p in pages:
            counts.update({l.strip() for l in p.splitlines() if 0 < len(l.strip()) <= 80})
        boiler = {l for l, c in counts.items() if c >= max(3, len(pages) // 2)}
        pages = ['\n'.join(l for l in p.splitlines() if l.strip() not in boiler) for p in pages]

    text = '\n\n'.join(p for p in pages if p.strip())
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)     # rejoin hyphenated words
    text = re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'
    return text
