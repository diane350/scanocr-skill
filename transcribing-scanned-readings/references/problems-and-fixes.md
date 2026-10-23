# Problems met and how they were solved

Each row was a real failure, mostly on Guha, *The Prose of Counter-Insurgency* (a JSTOR
scan). Check here before changing the code: most "simpler" approaches were tried.

(Until October 2026 the tool also made a searchable PDF and had an OCR-model route on
Colab. Both were removed so the work goes into the `.md`; see git history before PR #6.)

| Symptom | Cause | Fix (where) |
|---|---|---|
| Paragraph broken at every page break | Pages written separately | Joined when the first part has no closing punctuation and the next starts lower case, or the next page starts `+ `; `{47}` mark at the break (`build.compose`) |
| `self-` / `conscious` → "selfconscious" | Every line-end hyphen was dropped | Kept for prefixes (self, non, quasi, ex, well, half, all), compounds printed whole elsewhere, or a capital next (`Lieutenant-` / `Governors`) (`text.join`) |
| "Kr̥-" / "ṣṇa" became "Kr̥- ṣṇa"; "ʿulamā-" / "ʾ" kept its hyphen | `\w` misses combining accents; ʾ has no case | Word chars include combining marks; a word goes on when the next letter isn't a capital (`text.W`, `goes_on`) |
| Hindi/Urdu paragraphs always joined across pages | Sentence end knew only . ! ? | Danda । ॥ and Urdu ۔ ؟ end sentences too (`text.TERMINAL_RE`) |
| Note labels repeated (numbering restarts per page or chapter) | Markdown needs unique labels | Repeats relabelled `[^4-2]` (`build.compose`) |
| A note running onto the next page | — | Same label again with no marker on that page: joined to its first part |
| `oppres` / `sors` in drafts | The old text layer ends the line with a soft hyphen (U+00AD) | Soft hyphen at a line end becomes `-`, then `join` (`pages.draft`) |
| Every draft line its own paragraph | Indent measured from the leftmost text, which was the sideways "Downloaded by…" watermark at x = −1 | Sideways lines dropped (by text direction); margins from the median of body lines |
| Folio "146" lost from drafts | A tall-thin box filter for watermarks also caught short numbers | Filter by the line's direction, not its shape |
| Two page numbers on a page (146 and 46) | A reprint inside an anthology | Draft lists both in a comment; the skill asks the user once |
| 100+ page books, sessions expire | Cloud containers are temporary | Drafts pre-filled from the old text; `status`; `pack` / `prepare --resume`; `build --partial` |

## Running in a claude.ai chat

Chat skills can't reach this repository, so `tools/package_skill.py` builds a zip with the
skill folder plus a copy of `scanocr/transcribe` in `scripts/scanocr/`.
`scripts/scanocr_cli.py` first fetches the latest published code from the public
diane350/scanocr-skill (published on every merge to main), else uses the bundled copy, or
the repository's code when run from `.claude/skills/` in the repo. Re-upload the zip only
when SKILL.md changes. The description stays under 200 characters (claude.ai's limit).
