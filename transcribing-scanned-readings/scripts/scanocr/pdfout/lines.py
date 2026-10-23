"""Find printed text lines in a block of the page image and share the OCR words
out over them (moved from cells/step6, fix_v3)."""
import re

import numpy as np

from .font import text_length


# Weighing the two clues for which words sit on which line: a line about one word
# (8%) too wide or narrow costs as much as one miscounted word. Counted words are
# usually right but can be off by one on tightly set lines, so width has the final say
# when the two disagree. Tuned on the synthetic pages and a real JSTOR page.
WIDTH_TOLERANCE = 0.08


def word_gap(colink, h):
    """Narrowest blank stretch (pixels) that counts as a space between words in one line.
    Gaps inside words and spaces between them form two groups; split them where they
    separate best (Otsu), kept within 0.10-0.22 of the line height."""
    gaps, run = [], 0
    for v in colink:
        if not v:
            run += 1
        else:
            if run:
                gaps.append(run)
            run = 0
    lo, hi = max(3, 0.10 * h), max(3, 0.22 * h)
    if len(gaps) < 2:
        return hi
    g = np.array(gaps, float)
    best_t, best_v = hi, -1
    for t in np.unique(g)[1:]:
        a, b = g[g < t], g[g >= t]
        v = len(a) * len(b) * (a.mean() - b.mean()) ** 2
        if v > best_v:
            best_t, best_v = t, v
    return min(max(best_t, lo), hi)


def ink_mask(gray):
    """Printed pixels: an Otsu threshold (adapts to grey paper / faint print), isolated specks dropped."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    p_ = hist / hist.sum(); omega = np.cumsum(p_); mu = np.cumsum(p_ * np.arange(256))
    with np.errstate(divide='ignore', invalid='ignore'):
        sb = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega))
    thr = int(np.nanargmax(sb)) if np.isfinite(sb).any() else 140
    ink = gray < min(thr, 200)
    ink[1:-1, 1:-1] &= (ink[:-2, 1:-1] | ink[2:, 1:-1] | ink[1:-1, :-2] | ink[1:-1, 2:])
    return ink


def detect_lines(gray, x0, y0, x1, y1, inner_y=None):
    """Find printed text lines inside a block of the page image.
    Returns [(top, bottom, left, right, word count)] in image pixels.
    The area searched is padded so the block's own ascenders/descenders aren't cut;
    ink centred outside ``inner_y`` (the unpadded block, top and bottom) belongs to a
    neighbouring block and is dropped before it can be merged into this one's lines."""
    def inside(t, b):
        return inner_y is None or inner_y[0] <= y0 + (t + b) / 2 <= inner_y[1]

    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    ink = ink_mask(crop)
    rows = ink.sum(1).astype(float)
    if rows.max() == 0:
        return []
    on = rows > max(1.0, 0.04 * rows.max())
    runs, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append([start, i]); start = None
    if start is not None:
        runs.append([start, len(on)])
    if not runs:
        return []
    h0 = np.median([r[1] - r[0] for r in runs])
    # join runs separated by a hairline gap (broken letters, faint scans)
    joined = [runs[0]]
    for r in runs[1:]:
        if r[0] - joined[-1][1] <= max(2, 0.12 * h0):
            joined[-1][1] = r[1]
        else:
            joined.append(r)
    runs = [r for r in joined if r[1] - r[0] >= 2 and inside(*r)]
    if not runs:
        return []
    # reference line height from the taller runs (descender/accent slivers would drag a median down)
    h = np.percentile([r[1] - r[0] for r in runs], 75)
    # specks (dots, accents, superscripts) go to the nearest real line
    merged = []
    for r in runs:
        small = (r[1] - r[0]) < 0.45 * h
        if small and merged and r[0] - merged[-1][1] < 0.5 * h:
            merged[-1][1] = r[1]
        elif merged and merged[-1][1] - merged[-1][0] < 0.45 * h and r[0] - merged[-1][1] < 0.5 * h:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    h = np.median([b - t for t, b in merged])
    out = []
    for t, b in merged:
        k = int((b - t) / (1.7 * h)) + 1 if (b - t) > 1.7 * h else 1
        k = max(1, k)
        for j in range(k):
            tt, bb = t + (b - t) * j // k, t + (b - t) * (j + 1) // k
            colink = ink[tt:bb].sum(0) >= max(2, 0.06 * (bb - tt))
            cols = np.where(colink)[0]
            if len(cols) and inside(tt, bb):
                # count words: blank stretches at least as wide as this line's word gap
                seg_ = colink[cols[0]:cols[-1] + 1]
                min_gap = word_gap(seg_, h)
                words_n, gap = 1, 0
                for v in seg_:
                    if not v:
                        gap += 1
                    else:
                        if gap >= min_gap:
                            words_n += 1
                        gap = 0
                out.append((y0 + tt, y0 + bb, x0 + cols[0], x0 + cols[-1] + 1, words_n))
    return out

DASH_BREAK = re.compile(r'(?<=[—–])(?=\S)')


def tokens(text):
    """Words, with dash-joined words split after the dash: print can break a line
    there ('negatively—' / 'triggering'). Returns [(token, glued to the one before)]."""
    out = []
    for w in text.split():
        out.extend((part, k > 0) for k, part in enumerate(DASH_BREAK.split(w)))
    return out


def join_tokens(toks):
    return ''.join(('' if glued or not i else ' ') + t for i, (t, glued) in enumerate(toks))


def split_words_to_lines(text, widths, counts=None):
    """Distribute words over lines so each line's share of the text matches its printed width.
    Uses the text layer font's widths (not character counts) and dynamic programming for the best split."""
    toks = tokens(text)
    n = len(widths)
    if n == 0 or not toks:
        return []
    if n == 1:
        return [join_tokens(toks)]
    if len(toks) <= n:
        return [t for t, _ in toks] + [''] * (n - len(toks))
    if counts:
        sc = len(text.split()) / max(1, sum(counts))
        if not 0.85 <= sc <= 1.15:      # gap detection unreliable here; fall back to widths
            counts = None
        else:
            counts = [c * sc for c in counts]
    space = text_length(' ', 10)
    ww = [text_length(t, 10) for t, _ in toks]
    # breaks[k]: how many word spaces come before token k (glued tokens add none)
    breaks = [0]
    for _, glued in toks:
        breaks.append(breaks[-1] + (0 if glued else 1))
    total = sum(ww) + space * (breaks[-1] - 1)
    tw = float(sum(widths))
    want = [total * w / tw for w in widths]
    # prefix sums
    pre = [0.0]
    for w in ww:
        pre.append(pre[-1] + w)
    def spaces(i, j):  # word spaces inside tokens i..j-1
        return breaks[j] - breaks[i + 1]
    def seg(i, j):
        return pre[j] - pre[i] + space * spaces(i, j)
    W, INF = len(toks), float('inf')
    best = [[INF] * (W + 1) for _ in range(n + 1)]
    back = [[0] * (W + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for k in range(1, n + 1):
        for j in range(k, W - (n - k) + 1):
            for i in range(k - 1, j):
                if best[k - 1][i] == INF:
                    continue
                c = best[k - 1][i] + ((seg(i, j) - want[k - 1]) / max(want[k - 1], 1) / WIDTH_TOLERANCE) ** 2
                if counts:
                    c += (spaces(i, j) + 1 - counts[k - 1]) ** 2
                if c < best[k][j]:
                    best[k][j], back[k][j] = c, i
    cuts, j = [], W
    for k in range(n, 0, -1):
        i = back[k][j]; cuts.append((i, j)); j = i
    return [join_tokens(toks[i:j]) for i, j in reversed(cuts)]
