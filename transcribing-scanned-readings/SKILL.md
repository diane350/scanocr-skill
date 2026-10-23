---
name: transcribing-scanned-readings
description: Use when a scanned reading (JSTOR/library PDF, book scan, photocopy) needs quotable text or a searchable PDF for Zotero, with page numbers and linked footnotes, without OCR software.
---

# Transcribing scanned readings

## Overview

Claude reads each page; the bundled tool checks the transcription and builds a **clean
`.md`** (flowing paragraphs, `{47}` page marks, Markdown footnotes, tables, italics) and a
**searchable PDF** (the scan unchanged; invisible text line by line, running heads kept out
of the copyable text, printed page numbers as page labels so Zotero cites "p. 47", footnote
markers clickable both ways). Why it works this way: `references/problems-and-fixes.md`.

**The tool**: `python3 ${CLAUDE_SKILL_DIR}/scripts/scanocr_cli.py <command>` (in a chat,
`scripts/scanocr_cli.py` in this skill's folder). Below, `TOOL` means that. It installs
the PDF libraries it needs on first run.

**Folders**: work in a scratch folder (`WORK`, e.g. `/tmp/<name>`; in the scanocr
repository use `local/work/<name>`, which git ignores: scans are copyrighted). Put results
where the user can download them (`OUT`: in a chat `/mnt/user-data/outputs` if it exists;
in the repository `local/out/<name>`), then give them to the user.

## Steps

1. **Ask once**, only if the scan shows two page numbers (a reprint inside another book):
   which pagination the user cites.
2. **Prepare**: `TOOL prepare <scan.pdf> WORK`, adding `--resume <progress.zip>` if the user
   gives one. One PDF or image; combine images first:
   `doc.insert_pdf(fitz.open('pdf', fitz.open(img).convert_to_pdf()))` per image.
3. **Each page, in order**: look at `WORK/sheets/page_NNNN.png` (numbered blocks; open the
   image); for small print `WORK/crops/page_NNNN_bKK.png`. A **draft** (`# status: draft`) is
   pre-filled from the PDF's old text: correct every line against the scan by editing, not
   retyping. A **todo** page: type it. Then set `# status: done` and save before the next page.
4. **Long readings**: `TOOL status WORK` shows what's left. About every 10 pages,
   `TOOL pack WORK OUT/<name>-progress.zip` and give it to the user: a session or chat can
   end, and they continue in a new one by sharing the zip and the scan. Offer
   `TOOL build WORK OUT --partial` for chapters done so far. Where helper agents are
   available (Claude Code) and there are 30+ pages, give each helper a page range, the
   `WORK` folder and "follow the transcribing-scanned-readings skill".
5. **Build**: `TOOL build WORK OUT`. Fix what it lists. "Line breaks to check": make the
   block's lines match the scan. "Footnotes to check": a marker or note is missing or mistyped.
6. **Check and deliver**: `pdftotext -f 1 -l 2 <pdf> -` (if installed) reads like the
   pages; give the user the `.md` and PDF (and the latest progress zip). Tell them to check
   quotes against the scan.

## Transcription file

| Write | Means |
|---|---|
| `# status: todo / draft / done` | first line; only `done` pages are built |
| `page: 47` | the printed page number; blank if none shows (it's counted on) |
| `=== 3 ===` | body text: paragraphs, quotations, author line |
| `=== 1 header ===`, `=== 9 footer ===` | running heads, page numbers, footer lines |
| `=== 8 footnote ===` | notes: each starts with its marker (`¹`, `*`, `†`), blank line between notes; a note continued from the page before starts without a marker |
| `=== 4-6 table ===` | blocks 4 to 6 hold one table: a Markdown table, one row per printed row |
| `=== 4 ===` then `=== 4 footnote ===` | block 4 holds two kinds (no gap on the page): split it |
| `=== 7 skip ===` | no text: picture, stamp, sideways watermark |
| one line per printed line | same breaks; keep end-of-line hyphens (`oppres-` / `sors`) |
| `# Title`, `## II` | headings |
| `_word_`, `¹ ²`, — ‘ ’ “ ” | italics; footnote markers in the text; as printed |
| ā ṇ ś ḥ ṛ r̥ ṃ ʿ ʾ é ü ł … | every diacritic exactly as printed, never simplified (Kṛṣṇa, not Krishna) |
| `+ ` at the start of a page's first line | the paragraph continues across the break though the page before ended with a full stop |

Cover every block once, in order. Keep `(sic)`, misspellings and old spellings as printed.

## Common mistakes

- Reflowing a paragraph into one line: placement falls back to guessing line breaks.
- Dropping or simplifying diacritics: searches and quotes then miss the word.
- Leaving footnotes inside a body block: split the block, or they aren't linked.
- Marking a draft `done` without checking every line: old text layers misread. Typical
  misreads: ’ as `*` (`down*`), & as `8c` or `St`, footnote markers as plain or wrong
  digits, `rn` as `m` (`subaltemity`), `rt` as `n` (`imponant`), `die` for `the`, straight
  quotes for curly ones, words run together, `S` for `8` in dates; italics are never there; in
  footnotes, notes run together without the blank line between them.
- Typing a running head as text: it lands in the quote stream; mark it `header`.
- Losing the sentence that runs onto the next page: it's joined at build time; type it as is.
