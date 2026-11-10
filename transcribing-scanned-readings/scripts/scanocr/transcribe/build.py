"""build: the done pages joined into one clean .md, with what to fix listed.

Each page starts with its {47} page mark. A paragraph or quotation broken by a page
is joined back when it plainly goes on (no closing punctuation, next part in lower
case) or when the page starts with "+ "; a word hyphenated across the break is
rejoined. A note continued on the next page (same label again, no marker there) is
joined to its first part; so is the first of two notes with one label on a page when
the page before had a note with that label. Notes whose numbering starts again are
relabelled so every label is unique. HTML comments (<!-- -->) are left out.
"""
import re
from collections import Counter

from ..markup import LOOSE_DEF_RE, NOTE_DEF_RE, NOTE_REF_RE, PAGE_MARK_RE, check_notes, page_mark, unique_label
from .pages import load_pages
from .text import continues, hyphen_vocabulary, join, number_value, page_labels

COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
# a line that starts an item of a list: - one, • two, 3. three, (a) four
LIST_RE = re.compile(r'(?:[-*•]|\d{1,3}[.)]|\([a-z0-9]{1,4}\))\s+\S')


def _as_note(line):
    """A note's line as '[^12]: text', also when written without its colon ([^12]text)."""
    if m := LOOSE_DEF_RE.match(line.strip()):
        return f'[^{m.group(1)}]: {m.group(2)}'
    return line.strip()


def blocks_of(body, vocab, said=None):
    """[(kind, text)]: 'h' heading, 'p' paragraph, 'q' quotation, 't' table, 'n' note.
    A line that starts with a marker is a note whose colon was left out ([^12]text: the text
    never starts with a superscript). Notes come last on a page, so a paragraph after them
    is the rest of the note before it, split by a blank line (said, in ``said``)."""
    out = []
    for chunk in re.split(r'\n\s*\n', COMMENT_RE.sub('', body).strip()):
        lines = [l.rstrip() for l in chunk.split('\n') if l.strip()]
        if not lines:
            continue
        first = lines[0].lstrip()
        if first.startswith('#'):
            out += [('h', l.strip()) for l in lines]
        elif first.startswith('|'):
            out.append(('t', '\n'.join(l.strip() for l in lines)))
        elif NOTE_DEF_RE.match(first) or LOOSE_DEF_RE.match(first):
            for line in lines:                   # several notes may share a chunk
                if NOTE_DEF_RE.match(line.lstrip()) or LOOSE_DEF_RE.match(line.lstrip()):
                    out.append(('n', _as_note(line)))
                elif out and out[-1][0] == 'n':
                    out[-1] = ('n', join(out[-1][1], line.strip(), vocab))
        elif out and out[-1][0] == 'n' and not first.startswith('>'):
            label = NOTE_DEF_RE.match(out[-1][1]).group(1)
            for line in lines:
                out[-1] = ('n', join(out[-1][1], line.strip(), vocab))
            if said is not None:
                said.append(f'text after the notes was joined to note [^{label}]: "{first[:40]}…"')
        else:
            kind = 'q' if first.startswith('>') else 'p'
            lines = [re.sub(r'^\s*>\s?', '', l).strip() if kind == 'q' else l.strip() for l in lines]
            items = sum(bool(LIST_RE.match(l)) for l in lines)
            text, start = '', len(out)
            for line in lines:
                if text.endswith('\\'):                  # a line of verse: its break is kept
                    text += '\n' + line
                elif text and items > 1 and LIST_RE.match(lines[0]) and LIST_RE.match(line):
                    out.append((kind, text))             # the next item of a list
                    text = line
                else:
                    text = join(text, line, vocab)
            out.append((kind, text))
    return out


def join_marked(left, right, mark, vocab):
    """``join``, with the page mark where the page break fell (after a rejoined word)."""
    joined = join(left, right, vocab)
    start = len(joined) - len(right)
    if joined[start - 1:start] == ' ':
        return f'{joined[:start]}{mark} {joined[start:]}'
    end = joined.find(' ', start)                  # after the rejoined word and its punctuation
    if end < 0:
        return f'{joined} {mark}'
    return f'{joined[:end]} {mark}{joined[end:]}'


def _plain(text):
    return ' '.join(NOTE_REF_RE.sub('', PAGE_MARK_RE.sub('', text)).replace('*', '').replace('_', '').split())


def _heading_like(text):
    """A paragraph that may be a heading build cannot tell from text: short, not ended, and
    starting with a capital or a number (II, 3. The Revolt)."""
    words = _plain(text).split()
    return 0 < len(words) <= 8 and (words[0][0].isupper() or words[0][0].isdigit())


def compose(pages, labels):
    """(markdown, problems) for parsed pages; pages not done appear as a gap."""
    vocab = hyphen_vocabulary('\n'.join(p.body for p in pages if p.status == 'done'))
    items, notes, problems = [], {}, []        # items: [kind, text]
    seen_labels = set()
    last_note = None        # the last note on the page before: (as labelled there, as labelled in the .md)
    waiting = {}            # label -> markers still without a note (endnotes), oldest first: [(key, page name)]
    found = {}              # a waiting marker's key -> its note's label in the .md
    problems += _out_of_step(pages, labels)
    for i, (page, label) in enumerate(zip(pages, labels), 1):
        name = f'page {i}' + (f' (p. {label})' if label else '')
        if page.status != 'done':
            items.append(['gap', f'[{name}: not transcribed yet]'])
            last_note = None
            continue
        problems += [f'{name}: {p}' for p in page.problems]
        said = []
        blocks = blocks_of(page.body, vocab, said)
        problems += [f'{name}: {p}' for p in said]
        defs = [(m.group(1), m.group(2)) for k, t in blocks if k == 'n' for m in [NOTE_DEF_RE.match(t)]]
        refs = [r for k, t in blocks if k != 'n' for r in NOTE_REF_RE.findall(t)]
        rename = {}
        count = Counter(lab for lab, _ in defs)
        continued = None
        for k, (lab, text) in enumerate(defs):
            # The rest of a note from the page before: only that page's last note can run
            # on, as this page's first, with no marker here (or as the first of two notes
            # with that label: the second is this page's own).
            if k == 0 and last_note and lab == last_note[0] and (lab not in refs or count[lab] > 1):
                notes[last_note[1]] = join(notes[last_note[1]], text, vocab)
                continued = last_note
                continue
            new = lab
            n = 2
            while new in seen_labels:
                new = unique_label(lab, n)
                n += 1
            seen_labels.add(new)
            notes[new] = text
            if lab in refs:
                rename[lab] = new
            elif waiting.get(lab):                   # an endnote: the oldest marker still waiting for it
                found[waiting[lab].pop(0)[0]] = new
            else:
                problems.append(f'{name}: note [^{lab}] has no marker on this page')
            last_note = (lab, new)
        if defs and continued and len(defs) == 1:
            last_note = continued
        for lab, n in count.items():
            if lab in refs and n - bool(continued and continued[0] == lab) > 1:      # (endnotes may repeat)
                problems.append(f'{name}: note [^{lab}] is defined {n} times on this page')

        def relabel(m):
            lab = m.group(1)
            if lab in rename:
                return f'[^{rename[lab]}]'
            key = f'\x00{len(found) + sum(map(len, waiting.values()))}\x00'     # its note comes on a later page
            waiting.setdefault(lab, []).append((key, name))
            return f'[^{key}]'

        mark = page_mark(label) if label else ''
        first = True
        for kind, text in blocks:
            if kind == 'n':
                continue
            text = NOTE_REF_RE.sub(relabel, text)
            forced = first and text.startswith('+ ')
            text = text.removeprefix('+ ')
            last = items[-1] if items else None
            if first and last and last[0] == kind == 'q' and last[1].endswith('\\'):     # verse: a new line
                last[1] += '\n' + (f'{mark} ' if mark else '') + text
            elif (first and last and last[0] == kind and kind in 'pq'
                    and (forced or continues(last[1], text))):
                if not forced and kind == 'p' and _heading_like(last[1]):
                    problems.append(f'{name}: the page before ends with "{_plain(last[1])}", joined to this page as '
                                    f'one paragraph. If it is a heading, write it as ## {_plain(last[1])}; if it '
                                    f'does run on, start this page with + to keep it joined without this note.')
                last[1] = join_marked(last[1], text, mark, vocab) if mark else join(last[1], text, vocab)
            elif first and mark and kind in 'ht':        # a heading or table keeps its own line
                items += [['p', mark], [kind, text]]
            elif first and mark:
                items.append([kind, f'{mark} {text}'])
            else:
                items.append([kind, text])
            first = False
        if first and mark:                           # a page with no text of its own
            items.append(['p', mark])
    for lab, markers in waiting.items():             # markers whose note never came
        for key, name in markers:
            n = 2
            while (new := unique_label(lab, n)) in seen_labels:
                n += 1
            seen_labels.add(new)
            found[key] = lab if lab not in notes else new      # not some other note that has this label
            problems.append(f'{name}: marker [^{lab}] has no note')
    out = []
    for kind, text in items:
        text = re.sub(r'\[\^(\x00\d+\x00)\]', lambda m: f'[^{found[m.group(1)]}]', text)
        out.append('> ' + text.replace('\n', '\n> ') if kind == 'q' else text)
    out += [f'[^{lab}]: {text}' for lab, text in notes.items()]
    return '\n\n'.join(out) + '\n', problems


def _out_of_step(pages, labels):
    """Problems: typed page numbers that don't follow the page before (a misread 41 after 46)."""
    out, prev, n = [], None, 0                      # n: pages counted so far
    for i, (page, label) in enumerate(zip(pages, labels), 1):
        value = number_value(label or '')
        if not value:
            continue
        n += 1
        if prev and number_value(page.number or '') and value[1] == prev[1][1] and value[0] != prev[1][0] + n - prev[3]:
            out.append(f'page {i} (p. {label}): its page number does not follow page {prev[0]}\'s ({prev[2]}); '
                       f'check both (pages left out of the scan are fine)')
        prev = (i, value, label, n)
    return out


def build(workdir, out_path, partial=False, log=print):
    """Write the .md. Returns None if it was not written (pages still to do), else what the
    notes check found in the whole .md (see ``check_notes``): an empty list means every
    marker has its note and every note its marker, so the .md can be handed over. What to
    fix is listed either way, through ``log``."""
    pages = load_pages(workdir)
    todo = [i for i, p in enumerate(pages, 1) if p.status != 'done']
    if todo and not partial:
        log(f'Not done yet: pages {", ".join(map(str, todo))}. Finish them, or build with --partial.')
        return None
    md, problems = compose(pages, page_labels([p.number for p in pages]))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    log(f'Wrote {out_path}')
    if problems:
        log('To check:\n  ' + '\n  '.join(problems))
    notes = check_notes(md)
    if notes:
        log('NOT READY: the notes check failed. Fix the page files and build again before handing '
            'the .md over:\n  ' + '\n  '.join(notes))
    else:
        log('Notes check passed: every marker has its note and every note its marker.')
    return notes
