"""Transcription files: one per page, a section per numbered block.

    # status: done           todo (template), draft (pre-filled, not proofread) or done
    page: 47                 the printed page number, blank if the page shows none
    === 1 header ===         a block and its kind; === 2-4 table === covers blocks 2 to 4
    ...the block's lines, exactly as printed...

Kinds: text (default), header, footer, footnote, table, skip. A file without a status
line counts as done.
"""
import os
import re
import unicodedata
from dataclasses import dataclass, field

KINDS = ('text', 'header', 'footer', 'footnote', 'table', 'skip')
SECTION_RE = re.compile(r'^=== (\d+)(?:-(\d+))?(?: (\S+))? ===\s*$')
PAGE_RE = re.compile(r'^page:\s*(.*?)\s*$', re.I)
STATUS_RE = re.compile(r'^#\s*status:\s*(\S+)', re.I)
STATUSES = ('todo', 'draft', 'done')
# _italic_, also across line breaks and before a footnote marker (_Samgram_²⁶)
ITALIC_RE = re.compile(r'(?<![\w_])_(\S(?:.*?\S)?)_(?!_|(?![⁰¹²³⁴⁵⁶⁷⁸⁹])\w)', re.S)
TODO = 'TODO'
# typed or copied ligature characters become letters, so searching "final" finds "ﬁnal"
LIGATURES = str.maketrans({'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl', 'ﬅ': 'st', 'ﬆ': 'st'})
# old text layers break words with an invisible soft hyphen: at a line end it is the
# printed hyphen, anywhere else it is dropped
SOFT_HYPHEN_END_RE = re.compile('\u00ad[ \t]*$', re.M)


@dataclass
class Section:
    first: int                  # first and last block it covers
    last: int
    kind: str
    lines: list = field(default_factory=list)

    @property
    def name(self):
        return f'block {self.first}' if self.first == self.last else f'blocks {self.first}-{self.last}'


@dataclass
class Page:
    number: str = ''            # printed page number as typed ('47', 'xii'), '' if none
    status: str = 'done'
    sections: list = field(default_factory=list)
    problems: list = field(default_factory=list)


def is_part(prev, s):
    """Is ``s`` a further part of the block before it (=== 4 === then === 4 footnote ===)?"""
    return prev is not None and prev.first == prev.last == s.first == s.last


def transcript_path(workdir, i):
    return os.path.join(workdir, f'page_{i:04d}.txt')


def parse_transcript(text, n_blocks):
    """The page's sections in order, and its problems in plain words. Accents are stored
    composed (NFC: ā as one character) and soft hyphens become printed ones, so searches
    match."""
    page, cur = Page(), None
    text = unicodedata.normalize('NFC', text).translate(LIGATURES)
    text = SOFT_HYPHEN_END_RE.sub('-', text).replace('\u00ad', '')
    for line in text.splitlines():
        m = SECTION_RE.match(line.strip())
        if m:
            first = int(m.group(1))
            cur = Section(first, int(m.group(2) or first), m.group(3) or 'text')
            page.sections.append(cur)
        elif cur is not None:
            cur.lines.append(line.rstrip())
        elif m := PAGE_RE.match(line.strip()):
            page.number = m.group(1)
        elif m := STATUS_RE.match(line.strip()):
            page.status = m.group(1).lower()

    problems, covered = page.problems, set()
    if page.status not in STATUSES:
        problems.append(f'unknown status "{page.status}" (use todo, draft or done)')
    for prev, s in zip([None] + page.sections, page.sections):
        if is_part(prev, s):
            covered.discard(s.first)        # a block split into parts: repeated right after itself
        while s.lines and not s.lines[-1]:
            s.lines.pop()
        while s.lines and not s.lines[0]:
            s.lines.pop(0)
        for k in range(s.first, s.last + 1):
            if k in covered:
                problems.append(f'block {k} appears twice')
            covered.add(k)
        if s.kind not in KINDS:
            problems.append(f'{s.name}: unknown kind "{s.kind}" (use {", ".join(KINDS)})')
        elif s.last < s.first:
            problems.append(f'{s.name}: the range runs backwards')
        elif s.last > n_blocks:
            problems.append(f'there is no block {s.last} on this page (it has {n_blocks})')
        elif s.kind != 'skip' and not s.lines:
            problems.append(f'{s.name} is empty (write "=== {s.first} skip ===" if it has no text)')
        elif any(l.strip() == TODO for l in s.lines):
            problems.append(f'{s.name} still says {TODO}')
    problems += [f'block {k} is missing' for k in range(1, n_blocks + 1) if k not in covered]
    return page
