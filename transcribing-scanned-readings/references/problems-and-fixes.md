# Problems met and how they were solved

Each row was a real failure, mostly on Guha, *The Prose of Counter-Insurgency* (a JSTOR
scan). The fix is in the package, and a test keeps it fixed. Check here before changing
anything: most "simpler" approaches were tried and failed.

## Searchable PDF (the invisible text layer)

| Symptom | Cause | Fix (where) |
|---|---|---|
| Copy-paste gives `·` for — ‘ ’ “ ” ⁴ | Base-14 Helvetica holds only Latin-1; PyMuPDF swaps the rest for `·` | Noto Sans from `pymupdf-fonts`, subset on save, ~24 KB (`pdfout/font.py`) |
| Words a line off ("…enumeration of causes—of," / "say, …") | Word count per line read from gaps in the ink undercounted tight justified lines (word space ≈ 0.17 of line height, threshold was 0.22), and the count outweighed width ~300:1 | Word gap found per line by Otsu on gap widths, clamped to 0.10–0.22 × height; a line one word (8%) too wide costs as much as one miscounted word (`pdfout/lines.py`). Tuned on synthetic pages and real page 3 together: 0/24 → 24/24 lines |
| `negatively—triggering` forces a shift | One OCR word, but print breaks it after the dash | Tokens split after — / –, rejoined without a space on one line (`lines.tokens`) |
| A paragraph's last line missing or a stray line added | Block search area is padded 1% of the page; touching paragraphs (3 px apart) leaked in, or got merged into the last line as a "speck" | Ink centred outside the block's own box is dropped before merging (`detect_lines(inner_y=)`) |
| Lines out of order in Okular/Evince ("…Reason," before "Either way…") | Font size per line from each line's height: boxes overlapped; poppler sorts by position | One size per block, boxes exactly one printed line high (`place_block_lines`) |
| "0 text blocks placed" on JSTOR PDFs | Pages already had a poor text layer and were skipped | Redact the old layer (images and line art kept) (`embed.build_searchable_pdf`) |
| Last lines of paragraphs had no text, highlight "wacky" | Whole block poured into one textbox | Line-level placement: detect printed lines, share words out, stretch each line to its printed width |
| Line split in two / all lines halved / short last line counted as 4 words | Hairline gaps; descender slivers dragged the median down; gap threshold from that line's own height | Join runs across hairline gaps; reference height = 75th percentile; gap threshold from the block's typical height |
| Header words scattered (transcribed pages) | The rule under a running head counted as a text line | Thin horizontal rules left out of block finding (`pdfout/blocks.py`) |

## Clean text (.md)

| Symptom | Cause | Fix |
|---|---|---|
| `Do / wn / loa / de / d / by` inside paragraphs | Sideways "Downloaded by…" watermark read a few letters per line | Drop runs of 4+ lines of ≤4 chars, `WATERMARK_RE` lines, tall-thin blocks (`postprocess/textfix.py`); switchable |
| `\( ^{3} \)` instead of ³ | Model writes LaTeX superscripts | Convert to Unicode, attach to the word before |
| Footnote glued onto the paragraph above ("the¹ I am grateful…") | That attach step crossed newlines | Attach on the same line only; one space after a marker |
| Whole page shown as one paragraph in Markdown viewers | Blank lines between paragraphs were dropped; a single line break isn't a paragraph break in Markdown | Blank line ends a paragraph (`postprocess/clean.remove_det`) |
| `===============save results:===============` in 31 of 42 pages' text layer | `infer()` prints a log banner after its output, captured with it | Cut stdout at the banner (`ocr/unlimited.strip_log`) |

Known, not fixed (OCR-model route): short running heads stay in documents under 3 pages;
paragraphs split at page breaks; the model gives no confidence, so misreads (`[l]`→`[1]`,
a missing footnote number) pass silently. The Claude route below handles all three.

## Claude transcription route (`scanocr/transcribe/`)

| Symptom | Cause | Fix (where) |
|---|---|---|
| A lone "1831", a "Sir," on 3 pages vanished from the .md | Model-route cleanups (page-number lines, repeated-line "headers", runs of short lines) ran on Claude's text | Clean text composed directly; Claude marks furniture as `header`/`footer` (`compose.py`) |
| Copying across pages dragged "Selected Subaltern Studies 46" into quotes | Running heads were in the text layer | `header`/`footer` kept out of the layer (still visible in the scan) (`build.IN_LAYER`) |
| Zotero cited PDF page 3, not printed p. 47 | No page labels | `page:` line → PDF page labels, gaps counted on/back, roman kept (`compose.page_labels`, `label_rules`) |
| Zotero showed p. 6 on printed p. 48, despite the labels | Zotero reads page numbers off the page and drops PDF labels none of them confirm; with running heads out of the layer it took footnote markers (⁴ read as 4) as page numbers | the printed page number stays in the layer, last in copy order, where it is printed (`build.is_page_number`) |
| Copy from a left column into the right ran through the left column's notes | Layer order followed block order | Body first, footnotes last per page (`build.text_layer`); checked with pdf.js |
| Footnotes not linkable | Notes were plain paragraphs | Notes parsed by marker, run-on notes joined, `[^id]` + definitions at the end, PDF links both ways located char by char (`footnotes.py`) |
| A footnote with no gap above it sat in the body's block | XY-cut needs a blank band; footnote line height too noisy to split on (21 vs 24–32 px) | Claude splits the block: repeat its number with the second kind; lines shared out in order (`build.section_boxes`) |
| Paragraph broken at every page/column break | Pages composed separately | Joined when the first part has no closing punctuation and the next starts lower case, or the next starts `+ `; `{n}` mark at the break |
| `self-` / `conscious` → "selfconscious" | Every line-end hyphen was dropped | Kept for prefixes (self, non, quasi, ex, well, half, all), compounds printed whole elsewhere in the document, or a capital next (`Lieutenant-` / `Governors`) |
| Each typescript line a separate block | Block gap fixed at half a line; loose leading exceeds it | Gap must exceed 1.8 × the page's usual interline gap (`pdfout/blocks.py`) |
| Header split into scattered words | The rule under the running head counted as a text line | Thin rules left out of block finding |
| Two page numbers on a page (a reprint inside an anthology: 146 and 46) | Ambiguous pagination | Skill: ask the user once which they cite |
| 100+ page books: hours of typing, sessions expire | Everything retyped; cloud containers are temporary | Drafts pre-filled from the PDF's old text (proofread, don't retype); `status`; `pack` / `prepare --resume`; `build --partial`; helpers for page ranges |
| "Kr̥-" / "ṣṇa" became "Kr̥- ṣṇa"; "ʿulamā-" / "ʾ" kept its hyphen | Word pattern `\w` misses combining accents; the join rule wanted a lower-case letter, and ʾ has no case | Word chars include combining marks; a word goes on when the next letter isn't a capital (`compose.W`, `goes_on`) |
| An accent typed as a separate character, or a ﬁ ligature, made the word unfindable | Decomposed (NFD) text and ligature characters | Transcriptions normalised to NFC, ligatures spelled out (`transcript.parse_transcript`) |
| Hindi/Urdu paragraphs always joined across page breaks | Sentence-end check knew only . ! ? | Danda । ॥ and Urdu ۔ ؟ end sentences too |
| Hindi copies out as blanks from the text layer | Noto Sans has no Devanagari | Not fixed yet (user doesn't need it now): add Noto Sans Devanagari as a fallback font |

Copying across a page break in the PDF still includes that page's footnotes: they sit
between the two pages' text (the user chose to keep notes copyable). Viewers also add a
line break at every printed line; the `.md` is the source for flowing sentences.

## OCR model (Unlimited-OCR on Colab), if that route is used again

- Boxes are on a 0–999 grid (model code: `x / 999 * width`); larger values are pixels.
- `infer(save_results=True)` streams the raw output *with* boxes to stdout, saves
  `result.md` *without* them, returns `None`: capture stdout. Pin the model revision.
- Gundam mode, one page at a time, `no_repeat_ngram_size=35`, `ngram_window=128`;
  ~20–60 s a page on a free T4.
- Gradio is pinned to 6.17.3: newer needs `huggingface-hub>=1.16`, which conflicts
  with `transformers==4.57.1`.
- Private repo install fails as `git clone … exit code 128` with git's message cut off:
  check the token first through the API (valid? which account? can it see the repo?).
- A notebook open in Colab is a copy and goes stale: keep cell logic in the package
  (`scanocr/colab.py`); cell 1 reinstalls it each time.
- Colab wipes `/content` on disconnect: each page is saved to Drive as it's done,
  keyed by file checksum + DPI + model settings (`scanocr/checkpoint.py`).
- Don't binarize or heavily despeckle before OCR: it destroys faint strokes.

## How fixes were verified

Real page first: transcribe the printed lines of a real page by hand (kept in
git-ignored `local/real_pages/`, `tests/test_placement.py::test_real_page_lines`
skips without it), score "lines exactly right", reproduce the failure, then tune. Every
fix gets a synthetic test that fails on the old code. `pdftotext` (poppler) shows what a
user copying from Okular/Evince gets.

## Running in a claude.ai chat

Chat skills can't reach this repository, so `tools/package_skill.py` builds a zip with the
skill folder plus a copy of `scanocr/` in `scripts/scanocr/` (the Gradio app and Colab
helpers left out). `scripts/scanocr_cli.py` uses that copy, or the repository's code when
the skill runs from `.claude/skills/` in the repo, and pip-installs PyMuPDF 2.0,
pymupdf-fonts, numpy and Pillow if they're missing. The description stays under 200
characters (claude.ai's limit is stricter than the 1,024 of the skill spec). Chats fetch the
latest code from the public diane350/scanocr-skill (published on every merge to main);
re-upload the zip only when SKILL.md changes. `tests/test_skill_package.py` unpacks the zip
elsewhere and builds a page with it.
