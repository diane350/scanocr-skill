"""The Markdown of a transcription, shared by build (which writes it) and the typesetter
(which reads it): {47} page marks and [^12] notes."""
import re
import unicodedata

# where a printed page starts: {47}, {xii}, {XII}
PAGE_MARK = r'\{(?:\d+|[ivxlcdm]+|[IVXLCDM]+)\}'
PAGE_MARK_RE = re.compile(r'\{(\d+|[ivxlcdm]+|[IVXLCDM]+)\}')
# a note label as printed (12, *, †), made unique by build with -2, -3 ... (12-2)
NOTE_LABEL = r'[^\]\s]+'
NOTE_REF = rf'\[\^{NOTE_LABEL}\](?!:)'
NOTE_REF_RE = re.compile(rf'\[\^({NOTE_LABEL})\](?!:)')
NOTE_DEF_RE = re.compile(rf'^\[\^({NOTE_LABEL})\]:\s*(.*)$')
UNIQUE_RE = re.compile(r'-\d+$')


# marks that print nothing and only get in the way: soft hyphen, zero-width space and joiners
# around Latin text, the byte-order mark
UNSEEN_RE = re.compile('[\u00ad\u200b\u2060\ufeff]')


def clean(md):
    """A .md's text as the tools read it: Unix line ends, accents joined to their letters
    (a + combining macron is ā, which a font can set), nothing unseen left in the words."""
    md = md.replace('\r\n', '\n').replace('\r', '\n')
    return unicodedata.normalize('NFC', UNSEEN_RE.sub('', md))


def read_md(path):
    """The text of a transcription file, cleaned.  ValueError, in plain words, for a file
    that is not text (a PDF picked by mistake)."""
    try:
        with open(path, encoding='utf-8-sig') as f:
            return clean(f.read())
    except UnicodeDecodeError:
        raise ValueError(f'{path} is not a text file (a transcription is a .md saved as UTF-8)') from None


def page_mark(label):
    return f'{{{label}}}'


def unique_label(label, n):
    """The ``n``th note printed with ``label`` (n >= 2): 12-2."""
    return f'{label}-{n}'


def printed_label(label):
    """A note label as printed, without the -2 that made it unique."""
    return UNIQUE_RE.sub('', label)


def title_level(label):
    """Is a note with this label one a title or author carries (*, †), not a numbered one?"""
    return not printed_label(label).isdigit()


# a note written as a marker at the start of a line, without its colon: [^33]I am thinking...
LOOSE_DEF_RE = re.compile(rf'^\[\^({NOTE_LABEL})\](?!:)\s*(\S.*)$')
# a plain number after a word, which may be a marker that was never marked: comprehensive.35
PLAIN_MARK_RE = re.compile(r'(?<=[^\s\d(\[{\-–/])(\d{1,3})(?=[\s,;:.)’”]|$)')


def check_notes(md):
    """What is wrong with the notes of a whole .md, as a list of problems (empty if all is
    well): a marker with no note, a note with no marker (a title's note too: it belongs on
    the title, # Title[^*]), a note defined twice, or a note written without its colon."""
    defined, refs, loose, problems = [], [], [], []
    for n, line in enumerate(md.splitlines(), 1):
        if m := NOTE_DEF_RE.match(line):
            defined.append(m.group(1))
            refs += NOTE_REF_RE.findall(m.group(2))
        elif m := LOOSE_DEF_RE.match(line.strip()):
            loose.append(m.group(1))
            problems.append(f'line {n}: [^{m.group(1)}] starts a line with no colon: a note written as '
                            f'a marker? Write it [^{m.group(1)}]: ... with the notes')
            refs += NOTE_REF_RE.findall(m.group(2))
        else:
            refs += NOTE_REF_RE.findall(line)
    have, used = set(defined), set(refs) - set(loose)
    for label in dict.fromkeys(defined):
        if defined.count(label) > 1:
            problems.append(f'note [^{label}] is defined {defined.count(label)} times')
    body = '\n'.join(l for l in md.splitlines() if not NOTE_DEF_RE.match(l))
    for label in dict.fromkeys(refs):
        if label not in have and label not in loose:
            problems.append(f'marker [^{label}] has no note')
    for label in dict.fromkeys(defined):
        if label not in used:
            where = ''
            if label.isdigit():
                hits = [m for m in PLAIN_MARK_RE.finditer(body) if m.group(1) == label]
                if hits:
                    where = f' (a plain {label} in "…{body[max(0, hits[0].start() - 20):hits[0].end()]}" may be it)'
            elif title_level(label):
                where = f' (a title\'s note: write the title as # Title[^{label}])'
            problems.append(f'note [^{label}] has no marker{where}')
    return problems
