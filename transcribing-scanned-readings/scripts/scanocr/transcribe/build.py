"""build: the done pages joined into one clean .md, with what to fix listed.

Each page starts with its {47} page mark. A paragraph or quotation broken by a page
is joined back when it plainly goes on (no closing punctuation, next part in lower
case) or when the page starts with "+ "; a word hyphenated across the break is
rejoined. A note continued on the next page (same label again, no marker there) is
joined to its first part. Notes whose numbering starts again are relabelled so every
label is unique. HTML comments (<!-- -->) are left out.
"""
import re

from ..markup import NOTE_DEF_RE, NOTE_REF_RE, page_mark, unique_label
from .pages import load_pages
from .text import continues, hyphen_vocabulary, join, page_labels

COMMENT_RE = re.compile(r'<!--.*?-->', re.S)


def blocks_of(body, vocab):
    """[(kind, text)]: 'h' heading, 'p' paragraph, 'q' quotation, 't' table, 'n' note."""
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
        elif NOTE_DEF_RE.match(first):
            for line in lines:                   # several notes may share a chunk
                if NOTE_DEF_RE.match(line.lstrip()):
                    out.append(('n', line.strip()))
                elif out and out[-1][0] == 'n':
                    out[-1] = ('n', join(out[-1][1], line.strip(), vocab))
        else:
            kind = 'q' if first.startswith('>') else 'p'
            text = ''
            for line in lines:
                text = join(text, re.sub(r'^\s*>\s?', '', line).strip() if kind == 'q' else line.strip(), vocab)
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


def compose(pages, labels):
    """(markdown, problems) for parsed pages; pages not done appear as a gap."""
    vocab = hyphen_vocabulary('\n'.join(p.body for p in pages if p.status == 'done'))
    items, notes, problems = [], {}, []        # items: [kind, text]
    seen_labels = set()
    running = {}            # notes on the page before, as labelled there -> as labelled in the .md
    for i, (page, label) in enumerate(zip(pages, labels), 1):
        name = f'page {i}' + (f' (p. {label})' if label else '')
        if page.status != 'done':
            items.append(['gap', f'[{name}: not transcribed yet]'])
            running = {}
            continue
        problems += [f'{name}: {p}' for p in page.problems]
        blocks = blocks_of(page.body, vocab)
        defs = [(m.group(1), m.group(2)) for k, t in blocks if k == 'n' for m in [NOTE_DEF_RE.match(t)]]
        refs = [r for k, t in blocks if k != 'n' for r in NOTE_REF_RE.findall(t)]
        rename, continued = {}, {}
        for lab, text in defs:
            if lab in running and lab not in refs:       # a note running on from the page before
                notes[running[lab]] = join(notes[running[lab]], text, vocab)
                continued[lab] = running[lab]
                continue
            new = lab
            n = 2
            while new in seen_labels:
                new = unique_label(lab, n)
                n += 1
            rename[lab] = new
            seen_labels.add(new)
            notes[new] = text
        for r in refs:
            if r not in rename:
                problems.append(f'{name}: marker [^{r}] has no note on this page')
        for lab, _ in defs:
            if lab in rename and lab not in refs:
                problems.append(f'{name}: note [^{lab}] has no marker on this page')
        running = {**continued, **rename}
        mark = page_mark(label) if label else ''
        first = True
        for kind, text in blocks:
            if kind == 'n':
                continue
            text = NOTE_REF_RE.sub(lambda m: f'[^{rename.get(m.group(1), m.group(1))}]', text)
            forced = first and text.startswith('+ ')
            text = text.removeprefix('+ ')
            last = items[-1] if items else None
            if (first and last and last[0] == kind and kind in 'pq'
                    and (forced or continues(last[1], text))):
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
    out = []
    for kind, text in items:
        out.append('> ' + text if kind == 'q' else text)
    out += [f'[^{lab}]: {text}' for lab, text in notes.items()]
    return '\n\n'.join(out) + '\n', problems


def build(workdir, out_path, partial=False):
    """Write the .md; True if it was written. Lists problems to fix either way."""
    pages = load_pages(workdir)
    todo = [i for i, p in enumerate(pages, 1) if p.status != 'done']
    if todo and not partial:
        print(f'Not done yet: pages {", ".join(map(str, todo))}. Finish them, or build with --partial.')
        return False
    md, problems = compose(pages, page_labels([p.number for p in pages]))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f'Wrote {out_path}')
    if problems:
        print('To check:\n  ' + '\n  '.join(problems))
    return True
