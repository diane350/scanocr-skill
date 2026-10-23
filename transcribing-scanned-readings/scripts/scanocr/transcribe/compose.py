"""The clean text (.md) from transcribed pages.

Only body text and tables go in; running heads and footers stay out. A {47} mark shows
where each printed page starts. A paragraph broken by a page (or column) break is joined
back when it plainly goes on (no closing punctuation, next part starts in lower case) or
when the next part starts with "+ ". Words broken at a line end are rejoined; real
compounds keep their hyphen.
"""
import copy
import re

LEFT_OUT = ('header', 'footer', 'skip', 'footnote')
# a line-end hyphen after one of these is part of the word (self-conscious, non-official)
PREFIXES = ('self', 'non', 'quasi', 'ex', 'well', 'half', 'all')
TERMINAL_RE = re.compile(r'[.!?।॥۔؟]["”’\')\]]*(?:[⁰¹²³⁴⁵⁶⁷⁸⁹*†‡§]+|\[\^[^\]]+\])?$')
# a word's letters, with combining accents (r̥, m̐) and apostrophes (’ ʿ ʾ are letters already)
W = r"[\w\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f’']"
HYPHENATED_RE = re.compile(rf"{W}+(?:-{W}+)+")
WORD_RE = re.compile(rf"{W}+")
HEADING_RE = re.compile(r'^#{1,6} ')
ROMAN = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}


def hyphen_vocabulary(pages):
    """Hyphenated words the document prints whole somewhere (not broken at a line end)."""
    vocab = set()
    for page in pages:
        for s in page.sections if page else []:
            for line in s.lines:
                vocab.update(w.lower() for w in HYPHENATED_RE.findall(line.removesuffix('-')))
    return vocab


def join(left, right, vocab):
    """``right`` (the start of the next printed line) appended to ``left``."""
    if not left:
        return right
    if m := re.search(rf"({W}+)-$", left):
        stem, nxt = m.group(1), WORD_RE.match(right)
        compound = f'{stem}-{nxt.group(0) if nxt else ""}'.lower()
        if goes_on(right) and compound not in vocab and stem.lower() not in PREFIXES:
            return left[:-1] + right            # supplica- / tion -> supplication
        return left + right                     # self- / conscious, anti- / British
    if left.endswith(('—', '–')):
        return left + right                     # negatively— / triggering
    return left + ' ' + right


def join_marked(left, right, mark, vocab):
    """``join``, with a page mark where the page break fell (after a rejoined word)."""
    joined = join(left, right, vocab)
    if not mark:
        return joined
    start = len(joined) - len(right)            # ``right`` is always the tail of ``joined``
    if joined[start - 1:start] == ' ':
        return f'{joined[:start]}{mark} {joined[start:]}'
    end = start + len(WORD_RE.match(right).group(0)) if WORD_RE.match(right) else start
    return f'{joined[:end]} {mark}{joined[end:]}'


def paragraphs(lines, vocab):
    paras, cur = [], ''
    for line in lines + ['']:
        line = line.strip()
        if not line:
            if cur:
                paras.append(cur)
            cur = ''
        else:
            cur = join(cur, line, vocab)
    return paras


def goes_on(text):
    """Does ``text`` start like the rest of a word or sentence: a letter that isn't a
    capital (lower case, or a letter without case such as ʾ or a Devanagari letter)?"""
    return text[:1].isalpha() and not text[:1].isupper()


def continues(prev, nxt):
    """Does ``nxt`` carry on the sentence ``prev`` broke off? (Never out of a heading.)"""
    return not HEADING_RE.match(prev) and not TERMINAL_RE.search(prev.rstrip()) and goes_on(nxt)


def _with_note_refs(pages, notes):
    """A copy of ``pages`` with each linked footnote marker written as [^id]."""
    pages = copy.deepcopy(pages)
    for r in sorted(notes.refs, key=lambda r: (r.page, r.section, r.line, -r.start)):
        lines = pages[r.page].sections[r.section].lines
        lines[r.line] = f'{lines[r.line][:r.start]}[^{r.note.id}]{lines[r.line][r.end:]}'
    return pages


def _definitions(notes, vocab):
    out = []
    for n in notes.notes:
        texts = [''.join(paragraphs(para, vocab)) for para in n.paras]
        out.append(f'[^{n.id}]: {texts[0]}' + ''.join(f'\n\n    {t}' for t in texts[1:]))
    return out


def compose(pages, labels, notes=None):
    """The clean text of ``pages`` (parsed transcriptions; None for pages not built).
    ``labels``: printed page numbers, from ``page_labels``; ``notes``: from
    ``footnotes.link_notes``, listed at the end."""
    if notes:
        pages = _with_note_refs(pages, notes)
    vocab = hyphen_vocabulary(pages)
    items = []                                  # [kind, text, ...]: 'para', 'table', 'mark' or 'gap'
    for i, (page, label) in enumerate(zip(pages, labels)):
        if page is None:                        # not built yet (partial build)
            name = label or f'p{i + 1}'
            if items and items[-1][0] == 'gap':
                items[-1][2].append(name)
            else:
                items.append(['gap', '', [name]])
            continue
        mark = f'{{{label}}}' if label else ''
        for s in page.sections:
            if s.kind in LEFT_OUT:
                continue
            if s.kind == 'table':
                if mark:
                    items.append(['mark', mark])
                    mark = ''
                items.append(['table', '\n'.join(l for l in s.lines if l.strip())])
                continue
            for para in paragraphs(s.lines, vocab):
                forced = para.startswith('+ ')
                para = para.removeprefix('+ ')
                if items and items[-1][0] == 'para' and (forced or continues(items[-1][1], para)):
                    items[-1][1] = join_marked(items[-1][1], para, mark, vocab)
                else:
                    items.append(['para', f'{mark} {para}' if mark else para])
                mark = ''
    for item in items:
        if item[0] == 'gap':
            names = item[2]
            item[1] = (f'[page {names[0]} not transcribed yet]' if len(names) == 1 else
                       f'[pages {names[0]}–{names[-1]} not transcribed yet]')
    parts = [item[1] for item in items] + (_definitions(notes, vocab) if notes else [])
    return '\n\n'.join(parts) + '\n' if parts else ''


def number_value(number):
    """(value, style) of a printed page number, or None."""
    n = number.strip()
    if n.isdigit():
        return int(n), 'arabic'
    if n and all(c in ROMAN for c in n.lower()):
        vals = [ROMAN[c] for c in n.lower()]
        return sum(-v if v < w else v for v, w in zip(vals, vals[1:] + [0])), 'roman' if n.islower() else 'ROMAN'
    return None


def _show(value, style):
    if style == 'arabic':
        return str(value)
    out = ''
    for v, r in ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'), (90, 'xc'), (50, 'l'),
                 (40, 'xl'), (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'), (1, 'i')):
        while value >= v:
            out, value = out + r, value - v
    return out if style == 'roman' else out.upper()


def page_labels(numbers):
    """Printed page numbers for every page: those typed, the rest counted on from the
    page before (or back from the page after). None where nothing can be worked out."""
    vals = [number_value(n or '') for n in numbers]
    known = [i for i, v in enumerate(vals) if v]
    out = []
    for i, v in enumerate(vals):
        if v:
            out.append(numbers[i].strip())
            continue
        before = [k for k in known if k < i]
        after = [k for k in known if k > i]
        if before:
            value, style = vals[before[-1]]
            out.append(_show(value + i - before[-1], style))
        elif after and vals[after[0]][0] - (after[0] - i) >= 1:
            value, style = vals[after[0]]
            out.append(_show(value - (after[0] - i), style))
        else:
            out.append(None)
    return out


def label_rules(labels):
    """PDF page-label ranges (PyMuPDF ``set_page_labels``) for printed page numbers:
    a new range wherever the count or the style (1 2 3 / i ii iii) breaks."""
    styles = {'arabic': 'D', 'roman': 'r', 'ROMAN': 'R'}
    rules, prev = [], None
    for i, label in enumerate(labels):
        v = number_value(label) if label else None
        cur = (styles[v[1]], v[0]) if v else ('', None)
        if not (prev and cur[0] == prev[0] and (cur[1] is None or cur[1] == prev[1] + 1)):
            rules.append({'startpage': i, 'prefix': '', 'style': cur[0], 'firstpagenum': cur[1] or 1})
        prev = cur
    return rules if any(r['style'] for r in rules) else []
