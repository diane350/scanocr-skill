---
name: transcribing-scanned-readings
description: Use when a scanned reading (JSTOR/library PDF, book scan, photocopy) needs clean, quotable Markdown with printed page numbers and footnotes, without OCR software.
---

# Transcribing scanned readings

Your one job: a faithful, clean `.md` of the reading. A small tool does the rest (page
images, a draft from the PDF's old text, joining pages, checking notes).

**The tool**: `python3 ${CLAUDE_SKILL_DIR}/scripts/scanocr_cli.py <command>` (in a chat,
`scripts/scanocr_cli.py` in this skill's folder); below, `TOOL`. It installs PyMuPDF on
first run. Work in a scratch folder `WORK` (in the scanocr repository: `local/work/<name>`,
git-ignored; scans are copyrighted). Results go where the user can download them (`OUT`: in
a chat `/mnt/user-data/outputs`; in the repository `local/out/`).

## Steps

1. `TOOL prepare <scan.pdf or page images> WORK` (add `--resume <progress.zip>` if the user
   gives one).
   If every page comes out `todo`, the PDF has no text layer (ScanTailor removes it). Don't
   type whole pages from the images. Ask the user to OCR it on their own computer: in the
   scanocr app, File > OCR a scan… (or `python -m scanocr.transcribe ocr <scan.pdf>`;
   Google Cloud Vision; the app signs in to Google itself), then to attach the
   `<scan>_ocr.pdf` it writes, and `prepare` that: the pages come out as drafts.
2. **Each page, in order**: open `WORK/pages/page_NNNN.png` and `WORK/page_NNNN.md`
   together, then go on to the next.
   - A `draft` is pre-filled from the PDF's old text. Don't rewrite it: send only the
     corrections, which also marks it done:
     `TOOL fix WORK N <<'EOF'` then one `wrong text ==> right text` per line, then `EOF`.
     Give enough words that each wrong text occurs once on the page; `\n` is a line break
     (`masses.3 ==> masses.[^3]`, `ended. Then ==> ended.\n\nThen`). No lines: it was right.
   - A draft that starts `<!-- typed text` is the PDF's own type, not a scan: proofread it
     without opening the image (open it only for a table, or if something looks wrong).
   - A `todo` page is empty: type it, writing the whole page file with `status: done`.
   - Only if small print (notes, accents) can't be read for sure: `TOOL zoom WORK N bottom`
     (or `top`) makes a larger image of that half.
3. If the pages print **two page numbers** (a reprint inside another book: 146 and 46),
   ask the user once which they cite, and put that one in `page:`.
4. Long readings: `TOOL status WORK` shows what's left. About every 10 pages,
   `TOOL pack WORK OUT/<name>-progress.zip` and give it to the user, so a new chat can carry
   on. Every image opened stays in the conversation, so with helper agents (Claude Code)
   give each about 10 pages and this skill.
5. `TOOL build WORK OUT/<name>.md` (`--partial` for what's done so far). Fix what it
   lists, build again. It ends with a notes check over the whole `.md`: every marker has
   its note, every note its marker, every note starts `[^n]: `. Give the user the `.md`
   only once it says `Notes check passed`; on `NOT READY`, fix the page files it names.

## A page file

```
status: done
page: 47

A paragraph on one line, _italics_ as printed, a note marker[^12] where it is printed.

> A displayed (indented) quotation.

## II

[^12]: The note's text, on one line.
```

- Leave out running heads, page numbers and watermarks (the draft puts them in
  `<!-- -->` comments, which build drops). But keep a chapter's title on its first page
  as `# Title`, with its note marker if it has one (`# The Title[^*]`); if the draft put
  the title in a comment, write it out. A note with no marker is lost.
- A paragraph or note running onto the next page: just stop, and start the next page with
  the rest. Build joins it (and a word hyphenated across the break). The rest of a note
  goes under the same label: `[^12]: ...continued text`. If the page also has its own
  note 12 (numbering restarts each page), write both as `[^12]`, the continuation first:
  build joins the first to the note before and gives the second the marker. Build joins
  a paragraph whenever the page ends without closing punctuation; if it goes on after a
  full stop, start the next page with `+ `.
- Endnotes (notes at the end of the chapter or book): put the markers in the text as
  printed, and on the notes pages write each note as `[^4]: ...`, as printed. Build gives
  each note to the oldest marker of that number still without one, so numbering that
  starts again in each chapter works. Transcribe the notes pages; without them every
  marker is listed as having no note.
- Note labels as printed (`[^4]`, `[^*]`); build makes repeats unique. Every note goes
  after the text, as one line starting `[^4]: ` (with the colon); a superscript number
  in the text is always a marker, `[^4]`, never a plain `4`.
- Tables as Markdown tables. Headings as `#`/`##`. A list: one item a line (`- ...`,
  `1. ...`, `(a) ...`). Verse: every line of the quotation starts `> ` and ends with `\`
  (but the last), which keeps the line breaks.
- `page: -` for a page that has no number and is not counted (a plate between pages);
  blank for one that is counted (a chapter's first page).
- Leave out catchwords (an old book's repeat of the next page's first word at the foot).
- Every diacritic exactly as printed (Kṛṣṇa, ʿulamā), curly quotes and dashes as printed,
  `(sic)`, misspellings and old spellings kept.

## What the draft gets wrong (check every line)

- Note markers missing or wrong (`masses.3` plain, a `[^1]` that should be `[^3]`).
- Paragraph breaks: indents are guessed; section numbers (`II`) and headings come out as
  text; quotations aren't marked `>`.
- Italics are partial or wrong; small capitals are lost.
- Misreads: `rn`→`m` (subaltemity), `rt`→`n` (imponant), `die` for `the`, `S` for `8` and
  `l` for `1` in numbers, `of1799` run together, `’` as `*`, `&` as `8c`, straight quotes.
- Notes split or run together; a note's continuation at the top of a page (the draft
  labels it `[^?]:` when it can't tell whose it is: give it that note's number).
- A compound broken at a line end loses its hyphen (`twentieth-` / `century` comes out
  `twentiethcentury`) unless the page prints it whole elsewhere.
- A plain number taken for a marker, or the reverse (`no.[^3]` for "No. 3").
- Two columns are read left then right; check where a title, table or picture breaks them.
- A chapter title taken for a running head (in a comment), and its `*` read as `’`.
