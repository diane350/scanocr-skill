"""Text fixes shared by the clean text and the PDF text layer:
sideways JSTOR watermark, watermark lines, LaTeX superscripts -> Unicode."""
import re

SUP = str.maketrans('0123456789+-()', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾')
WATERMARK_RE = re.compile(r'(downloaded\s+(by|from)\b.*|this content downloaded from.*|all use subject to\s+https?://\S+.*)', re.I)


def fix_math_markers(text):
    # \( ^{3} \)  ->  ³     and   $^{12}$ -> ¹²
    text = re.sub(r'\\\(\s*\^\{?\s*([0-9+\-()]+)\s*\}?\s*\\\)', lambda m: m.group(1).translate(SUP), text)
    text = re.sub(r'\$\s*\^\{?\s*([0-9+\-()]+)\s*\}?\s*\$', lambda m: m.group(1).translate(SUP), text)
    text = re.sub(r'\^\{([0-9]+)\}', lambda m: m.group(1).translate(SUP), text)
    # attach to the word before, on the same line only: a footnote that starts
    # with its marker stays apart from the paragraph above it
    text = re.sub(r'[ \t]+([⁰¹²³⁴⁵⁶⁷⁸⁹]+)', r'\1', text)
    text = re.sub(r'([⁰¹²³⁴⁵⁶⁷⁸⁹]+)[ \t]{2,}', r'\1 ', text)    # one space after, not two
    text = re.sub(r'\\\((.*?)\\\)', r'\1', text)                 # any other leftover \( \)
    return text


def drop_vertical_junk(text):
    """Remove runs of 4+ tiny lines (a sideways watermark read letter-by-letter) and watermark lines."""
    lines = text.splitlines()
    keep, i = [], 0
    while i < len(lines):
        j = i
        while j < len(lines) and 0 < len(lines[j].strip()) <= 4:
            j += 1
        if j - i >= 4:
            i = j
            continue
        if not WATERMARK_RE.search(lines[i].strip()):
            keep.append(lines[i])
        i += 1
    return '\n'.join(keep)


def fix_text(text, remove_watermarks=True):
    return fix_math_markers(drop_vertical_junk(text) if remove_watermarks else text)
