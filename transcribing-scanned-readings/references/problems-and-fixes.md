# Problems met and how they were solved

Each row was a real failure, mostly on Guha, *The Prose of Counter-Insurgency* (a JSTOR
scan). Check here before changing the code: most "simpler" approaches were tried.

| Symptom | Cause | Fix (where) |
|---|---|---|
| Paragraph broken at every page break | Pages written separately | Joined when the first part has no closing punctuation and the next starts lower case, or the next page starts `+ `; `{47}` mark at the break (`build.compose`) |
| `self-` / `conscious` → "selfconscious" | Every line-end hyphen was dropped | Kept for prefixes (self, non, quasi, ex, well, half, all), compounds printed whole elsewhere, or a capital next (`Lieutenant-` / `Governors`) (`text.join`) |
| "Kr̥-" / "ṣṇa" became "Kr̥- ṣṇa"; "ʿulamā-" / "ʾ" kept its hyphen | `\w` misses combining accents; ʾ has no case | Word chars include combining marks; a word goes on when the next letter isn't a capital (`text.WORD_CHAR`, `goes_on`) |
| Hindi/Urdu paragraphs always joined across pages | Sentence end knew only . ! ? | Danda । ॥ and Urdu ۔ ؟ end sentences too (`text.TERMINAL_RE`) |
| Note labels repeated (numbering restarts per page or chapter) | Markdown needs unique labels | Repeats relabelled `[^4-2]` (`build.compose`) |
| A note running onto the next page | — | Same label again with no marker on that page: joined to its first part |
| `oppres` / `sors` in drafts | The old text layer ends the line with a soft hyphen (U+00AD) | Soft hyphen at a line end becomes `-`, then `join` (`pages.draft`) |
| Every draft line its own paragraph | Indent measured from the leftmost text, which was the sideways "Downloaded by…" watermark at x = −1 | Sideways lines dropped (by text direction); margins from the median of body lines |
| Folio "146" lost from drafts | A tall-thin box filter for watermarks also caught short numbers | Filter by the line's direction, not its shape |
| A continued note joined to the wrong note | Numbering restarted, so the note had been relabelled `[^1-2]`, and its continuation was looked up as `[^1]` | Continuations looked up by the label on the page before (`build.compose`) |
| Page numbers taken from running heads ("CIVIL WAR" as CIVIL, a page number) and from the last line of a long note ("p. 23") | Any roman-looking word counted; anything near the page's top or foot was a running head | Roman numbers must be written the usual way (not CIVIL, IIII) and under 400; a running head or folio must be set apart from the text (`text.number_value`, `pages._running_lines`) |
| No draft, and print erased from the image, on a page shown turned (/Rotate 90) | The text layer is in the unturned page's units | Its lines turned with the page (`pages._text_layer_lines`) |
| Only the first page of a TIFF; photos sideways; a CMYK JPEG stopped prepare | Images were read with `Pixmap`, which reads one frame and ignores EXIF | Images opened as documents, a page each, turned upright, saved in RGB or grey (`pages._source_pages`, `_image_pixmap`) |
| Notes left in the text as `[^33]I am thinking…`, split into short paragraphs; `comprehensive.35` left plain (Sarkar p. 95, OCR layer) | The draft knew a note only by smaller type; that page's OCR type sizes made its notes almost text-sized, so they read as text with a superscript at the line start, and 35 had no note to become a marker for | Notes found by their labels too: a line starting with a superscript, or with a number after a gap below the text, starts them; every line after is a note or its rest; a new note at the next number (`pages._paragraphs`). Build reads `[^33]text` as a note and joins text after the notes to the note before (`build.blocks_of`) |
| Chapter title missing, so its `[^*]` note had no marker (Sarkar) | The title sits at the page top, set apart like a running head, so it went into a comment, which the instructions said to leave; its `*` was read as `’` | Larger type at the top is a title, kept as `# Title[^*]` (`pages._running_lines`); `*` after a word is a marker when the page has a `*` note (`pages.draft`); instructions say to keep a title found in a comment |
| Note problems listed by build but handed over anyway | The list was advice, and the app's Transcribe window never showed it | A notes check over the whole .md (`markup.check_notes`): build says `NOT READY` and exits 1; the Transcribe window shows it; the typesetter asks before saving |
| Two page numbers on a page (146 and 46) | A reprint inside an anthology | Draft lists both in a comment; the skill asks the user once |
| 100+ page books, sessions expire | Cloud containers are temporary | Drafts pre-filled from the old text; `status`; `pack` / `prepare --resume`; `build --partial` |
| About 7,000 image tokens a page (Guha) | Whole page at 200 dpi plus its two halves, always opened; the halves had the same pixels, as current models don't shrink images this size | Page image cropped to the printed area (ink and text-layer lines, not the sideways watermark): about 1,900; halves only on request (`zoom`, 300 dpi) (`pages._pdf_area`) |
| Output costly: every draft page written out in full, for a few fixes | A page file was rewritten whole | `fix` takes only the corrections (each must match once, or nothing changes) and marks the page done (`pages.fix`) |
| Images opened for PDFs that are not scans | The tool treated every text layer as an old OCR one | Visible text and no full-page picture: the draft says it is typed text, proofread without the image (`pages.typed_text`) |
| Long readings costly even so | Each image stays in the conversation, so each later step pays for it again | One step to open the image and the page file, one write per page; helper agents take about 10 pages each (SKILL.md) |

## Running in a claude.ai chat

Chat skills can't reach this repository, so `tools/package_skill.py` builds a zip with the
skill folder plus a copy of `scanocr/transcribe` in `scripts/scanocr/`.
`scripts/scanocr_cli.py` first fetches the latest published code from the public
diane350/scanocr-skill (published on every merge to main), else uses the bundled copy, or
the repository's code when run from `.claude/skills/` in the repo. Re-upload the zip only
when SKILL.md changes. The description stays under 200 characters (claude.ai's limit).
