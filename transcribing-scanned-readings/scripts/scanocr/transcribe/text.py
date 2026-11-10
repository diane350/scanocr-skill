"""Text rules: joining broken words and sentences, printed page numbers, and cleaning
a PDF's old text layer for a draft."""
import bisect
import re

from ..markup import NOTE_REF

# a line-end hyphen after one of these is part of the word (self-conscious, non-official)
# (not ex-: a line breaks ex-ample, ex-pect far more often than it prints ex-officio)
PREFIXES = ('self', 'non', 'quasi', 'well', 'half', 'all')
TERMINAL_RE = re.compile(rf'[.!?:।॥۔؟]["”’\')\]_*]*(?:{NOTE_REF})*$')
# a word's letters, with combining accents (r̥, m̐) and apostrophes
WORD_CHAR = r"[\ẁ-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯’']"
WORD_RE = re.compile(rf"{WORD_CHAR}+")
HYPHENATED_RE = re.compile(rf"{WORD_CHAR}+(?:-{WORD_CHAR}+)+")
ROMAN = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
ROMAN_MAX = 400             # front matter ends well before; MIX or DIV in a running head is a word

LIGATURES = str.maketrans({'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl', 'ﬅ': 'st', 'ﬆ': 'st'})
# a line that starts as one: the same words inside a sentence ("the files were downloaded from
# the archive") are the author's
WATERMARK_RE = re.compile(r'^\W*(downloaded\s+(by|from)\b|this content downloaded from|'
                          r'all use subject to\s+https?://\S+)', re.I)
# common misreads in old text layers: down* (’) and 8c (&). (St was read as & too, which
# turned St Paul into & Paul in every clean text layer.) A * after a word can also be
# a note marker (a title's acknowledgements note), so the draft decides that one itself.
STAR_RE = re.compile(r'(?<=[a-z])\*(?=[\s,.;:)]|$)', re.M)
MISREADS = [(re.compile(r'(?<=\s)8c(?=\s+[A-Z])'), '&')]


def goes_on(text):
    """Does ``text`` start like the rest of a word or sentence: a letter that isn't a
    capital (lower case, or a letter without case), possibly after _ or (?"""
    t = text.lstrip('_(')
    return t[:1].isalpha() and not t[:1].isupper()


def join(left, right, vocab=()):
    """``right`` (the next line or page) appended to ``left``, rejoining a word broken by
    a hyphen (supplica- / tion) but not a compound (self- / conscious)."""
    if not left:
        return right
    if m := re.search(rf"({WORD_CHAR}+)-_?$", left):
        stem, nxt = m.group(1), WORD_RE.match(right.lstrip('_'))
        compound = f'{stem}-{nxt.group(0) if nxt else ""}'.lower()
        if goes_on(right) and compound not in vocab and stem.lower() not in PREFIXES:
            if left.endswith('-_') and right.startswith('_'):
                return left[:-2] + right[1:]        # _supplica-_ / _tion_
            return left[:-1] + right
        if left.endswith('-_') and right.startswith('_'):
            return left[:-1] + right[1:]            # _Lieutenant-_ / _Governors_
        return left + right
    if left.endswith(('—', '–')) and not left.endswith((' —', ' –')):      # word—word; "word – word" keeps its spaces
        return left + right
    return left + ' ' + right


def hyphen_vocabulary(text):
    """Hyphenated words the document prints whole somewhere (so they keep the hyphen)."""
    return {w.lower() for w in HYPHENATED_RE.findall(text)}


def continues(prev, nxt):
    """Does ``nxt`` carry on the sentence ``prev`` broke off? It does when ``prev`` has no
    closing punctuation, whatever ``nxt`` starts with (a name, a year, a quotation)."""
    return bool(nxt.strip()) and not TERMINAL_RE.search(prev.rstrip())


def fix_old_text(text, star=True):
    """An old text layer made readable: watermark lines out, ligatures and soft
    hyphens resolved, common misreads fixed. ``star``: down* read as down’ too (see
    STAR_RE)."""
    text = text.translate(LIGATURES)
    text = re.sub('­[ \t]*\n', '-\n', text).replace('­', '')
    text = '\n'.join(l for l in text.split('\n') if not WATERMARK_RE.search(l))
    for rx, rep in MISREADS:
        text = rx.sub(rep, text)
    return STAR_RE.sub('’', text) if star else text


def number_value(number):
    """(value, style) of a printed page number, or None."""
    n = number.strip()
    if n.isdigit():
        return int(n), 'arabic'
    if n and (n.islower() or n.isupper()) and all(c in ROMAN for c in n.lower()):
        vals = [ROMAN[c] for c in n.lower()]
        value = sum(-v if v < w else v for v, w in zip(vals, vals[1:] + [0]))
        style = 'roman' if n.islower() else 'ROMAN'
        if value < ROMAN_MAX and _show(value, style) == n:      # written the usual way: not CIVIL, IIII
            return value, style
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


NOT_COUNTED = '-'           # typed as a page's number: it has none and is not counted (a plate)


def page_labels(numbers):
    """Printed page numbers for every page: those typed, the rest counted on from the
    page before (or back from the page after). None where nothing can be worked out, and
    for a page typed as NOT_COUNTED, which the count passes over."""
    counted = [i for i, n in enumerate(numbers) if (n or '').strip() != NOT_COUNTED]
    out = [None] * len(numbers)
    for i, label in zip(counted, _labels([numbers[i] for i in counted])):
        out[i] = label
    return out


def _labels(numbers):
    vals = [number_value(n or '') for n in numbers]
    known = [i for i, v in enumerate(vals) if v]
    out = []
    for i, v in enumerate(vals):
        if v:
            out.append(numbers[i].strip())
            continue
        k = bisect.bisect(known, i)                 # known[k - 1] < i < known[k]
        if k:
            before = known[k - 1]
            value, style = vals[before]
            out.append(_show(value + i - before, style))
        elif known and vals[known[0]][0] - (known[0] - i) >= 1:
            after = known[0]
            value, style = vals[after]
            out.append(_show(value - (after - i), style))
        else:
            out.append(None)
    return out
