"""The Markdown of a transcription, shared by build (which writes it) and the typesetter
(which reads it): {47} page marks and [^12] notes."""
import re

# where a printed page starts: {47}, {xii}, {XII}
PAGE_MARK = r'\{(?:\d+|[ivxlcdm]+|[IVXLCDM]+)\}'
PAGE_MARK_RE = re.compile(r'\{(\d+|[ivxlcdm]+|[IVXLCDM]+)\}')
# a note label as printed (12, *, †), made unique by build with -2, -3 ... (12-2)
NOTE_LABEL = r'[^\]\s]+'
NOTE_REF = rf'\[\^{NOTE_LABEL}\](?!:)'
NOTE_REF_RE = re.compile(rf'\[\^({NOTE_LABEL})\](?!:)')
NOTE_DEF_RE = re.compile(rf'^\[\^({NOTE_LABEL})\]:\s*(.*)$')
UNIQUE_RE = re.compile(r'-\d+$')


def page_mark(label):
    return f'{{{label}}}'


def unique_label(label, n):
    """The ``n``th note printed with ``label`` (n >= 2): 12-2."""
    return f'{label}-{n}'


def printed_label(label):
    """A note label as printed, without the -2 that made it unique."""
    return UNIQUE_RE.sub('', label)
