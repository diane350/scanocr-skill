"""Optional: proofread a prepared reading's drafts with a model through its API, and build the
.md, without a chat. Needs an API key: Anthropic's (Claude, billed per token, apart from a
Claude subscription; ``pip install 'scanocr[claude]'``) or Google's Gemini API key from
aistudio.google.com (``pip install 'scanocr[gemini]'``; it has a free tier).

    python -m scanocr.transcribe auto <ocr'd.pdf> <out.md> [--model M] [--check]

Each draft page goes to the model with its image (a page that is typed text, not a scan, goes
as text only) and comes back as corrections, which ``apply_fixes`` applies like the skill's
fix step. A correction that doesn't match exactly once is sent back once; a page that still
fails is left as a draft. Pages with no draft (blank, or the PDF was not OCR'd) are skipped.
"""
from base64 import b64decode, b64encode
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pymupdf

from .build import build
from .pages import TYPED, apply_fixes, load_pages, page_path, parse_page, prepare
from .text import number_value
from .vision_auth import CONFIG, write_private

DEFAULT_MODEL = 'claude-sonnet-5-5'
# $ per million tokens in / out at list prices when written (Gemini: the paid tier; its free tier
# costs nothing): for the estimate only
MODELS = {'claude-sonnet-5-5': (2, 10), 'claude-opus-5-5': (4, 20), 'claude-haiku-5-5': (0.10, 0.50),
          'gemini-2.5-flash': (0.30, 2.50), 'gemini-3.1-flash-lite': (0.25, 1.50),
          'gemini-2.5-flash-lite': (0.10, 0.40)}
# which company's API a model is on: its key file, the environment variables that can hold the key
PROVIDERS = {'claude': ('anthropic-key', ('ANTHROPIC_API_KEY',), 'an Anthropic API key (console.anthropic.com)'),
             'gemini': ('gemini-key', ('GEMINI_API_KEY', 'GOOGLE_API_KEY'), 'a Gemini API key (aistudio.google.com)')}
PARALLEL = 4
# Room for the reply and, on Claude, the thinking before it (billed and counted as output): at
# 8,000 a dense page's corrections could be cut off half way.
MAX_TOKENS = 16000
SAME_FAULT = 5                    # pages failing on a fault of the service, none done: stop the run
# What a page image costs: Claude reads 28 x 28 px patches (width/28 x height/28 tokens) and scales an
# image down to at most 2,576 px on the long edge and 4,784 tokens (Anthropic's docs for models from 4.7 on);
# Gemini 768 px tiles at 258 tokens each. A page is scaled to fit before it is sent, so a big scan costs
# no more than that and stays under the 10 MB image limit.
MAX_EDGE = 2576
MAX_TOKENS_PER_IMAGE = 4784
MAX_PIXELS = MAX_TOKENS_PER_IMAGE * 28 * 28
JPEG_OVER = 7_000_000             # bytes of PNG above which the page goes as a JPEG
TEXT_CHARS_PER_TOKEN = 3.5
# Output tokens for one page, for the estimate: the corrections are about 400; Claude also
# thinks first, which is billed as output (one real 42-page run cost about 3,000 a page, three
# times the old estimate).
EXPECTED_OUT = {'claude': 3000, 'gemini': 400}

SYSTEM = """\
You proofread one page of a scanned academic reading. You get the page image and a draft of the \
page made from OCR, in Markdown. The draft is mostly right. Compare it with the image line by \
line and reply with ONLY the corrections, one per line, as:

    wrong text ==> right text

Each wrong text must occur exactly once in the draft, so include enough neighbouring words; write \
a line break inside it or the right text as \\n. If the draft is right, reply: OK. No other words, \
no explanations, no code fences. If the page's printed number is missing or wrong at the top \
("printed page number"), a first line `page: 47` sets it (blank if no number is printed).

What the draft usually gets wrong, so check every line:
- Footnote markers: a plain digit after a word ("masses.3") must be a marker, [^3]; the note at the \
foot starts "[^3]: ". Note labels as printed ([^4], [^*]).
- Paragraph breaks (indents were guessed); headings and section numbers must be "## II"; displayed \
quotations start "> ".
- Italics must be _italic_ as printed; small capitals are lost.
- Misreads: rn for m, rt for n, "die" for "the", S for 8 and l for 1 in numbers, words run \
together, ' for ’, 8c for &.
- Notes split or run together; a note continuing from the page before. Every note at the foot \
must start "[^12]: " (with the colon) and stay one line, after the text; a note left in the text \
as "[^12]Its text" or broken into short lines is wrong.
- A chapter's title on its first page must be kept, as "# Title", with its note marker if it \
has one (# The Title[^*]). The draft may have put the title in a <!-- --> comment as if it \
were a running head: then add it as a heading above the text.
- Endnotes: where the notes are printed at the end of the chapter or book, the markers stay in \
the text as printed and each note on the notes pages starts "[^4]: ". A number at the start of a \
line of body text ("9 July") is not a note.
- A list is one item a line ("- ...", "1. ...", "(a) ..."). Verse: every line starts "> " and \
ends with a backslash, but the last. A plain number is not a marker ("No. 3", "3.5", "1857-58").
- A note that goes on from the page before is labelled "[^?]: " when the draft can't tell whose \
it is: give it that note's number if the image shows it, or leave it.
- Every diacritic exactly as printed (Kṛṣṇa, ʿulamā, Chaurī), curly quotes and dashes as printed, \
misspellings and old spellings kept. Do not modernise or correct the author.
Running heads, page numbers and watermarks are left out: they are in <!-- --> comments in the \
draft, leave those alone (except a chapter title, above)."""


class FatalError(SystemExit):
    """A problem every page would meet (a refused key, an unknown model): the run stops."""


class PageError(SystemExit):
    """One page's request failed (the service, the network, a reply that is no use): the page
    stays a draft. ``used``: the (tokens in, tokens out) it cost all the same; ``fault``: it
    was the service or network, not the page."""

    def __init__(self, message, used=(0, 0), fault=False):
        super().__init__(message)
        self.used, self.fault = used, fault


def provider(model):
    return 'gemini' if model.startswith('gemini') else 'claude'


def key_file(model):
    return CONFIG / PROVIDERS[provider(model)][0]


def env_key(model=DEFAULT_MODEL):
    """The key the environment holds for this model's company; '' if none."""
    for name in PROVIDERS[provider(model)][1]:
        if os.environ.get(name, '').strip():
            return os.environ[name].strip()
    return ''


def api_key(model=DEFAULT_MODEL):
    """The saved key (or the environment's) for the company whose model this is; '' if none."""
    if env_key(model):
        return env_key(model)
    try:
        return key_file(model).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return ''


def save_key(key, model=DEFAULT_MODEL):
    """Keep the key in this user's config folder, readable by this user only. (It is plain
    text: anyone who can read your files as you can read it.)"""
    write_private(key_file(model), key.strip())


def forget_key(model=DEFAULT_MODEL):
    key_file(model).unlink(missing_ok=True)


def ask_for(model, key):
    """The ask function for the company whose model this is."""
    return (gemini_ask if provider(model) == 'gemini' else anthropic_ask)(key)


def gemini_ask(key):
    """A function (model, system, messages) -> (reply, tokens in, tokens out) for the Gemini API;
    messages are in Anthropic's shape (text and base64 PNG image blocks), turned into Gemini's."""
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError:
        raise SystemExit("The Gemini client is not installed: pip install 'scanocr[gemini]'")
    client = genai.Client(api_key=key)

    def part(block):
        if isinstance(block, str):
            return types.Part.from_text(text=block)
        if block['type'] == 'image':
            return types.Part.from_bytes(data=b64decode(block['source']['data']), mime_type=block['source']['media_type'])
        return types.Part.from_text(text=block['text'])

    def ask(model, system, messages):
        contents = [types.Content(role='model' if m['role'] == 'assistant' else 'user',
                                  parts=[part(b) for b in ([m['content']] if isinstance(m['content'], str) else m['content'])])
                    for m in messages]
        try:
            r = client.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=MAX_TOKENS))
        except errors.ClientError as e:
            if e.code in (400, 401, 403) and 'API key' in str(e):
                raise FatalError('The Gemini API key was refused: check it at aistudio.google.com.')
            if e.code == 404:
                raise FatalError(f'Google does not know a model called {model}.')
            if e.code == 429:
                raise PageError(f'Gemini is over its rate or free-tier limit: {e}', fault=True)
            raise FatalError(f'Gemini refused the request: {e}')
        except errors.APIError as e:                    # its servers: this page, not the run
            raise PageError(f'Gemini could not answer: {e}', fault=True)
        u = r.usage_metadata
        used = (u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0))
        try:
            text = r.text or ''
        except ValueError:                              # a blocked reply has no text to read
            text = ''
        return text, *used
    return ask


def anthropic_ask(key):
    """A function (model, system, messages) -> (reply, tokens in, tokens out) for the Anthropic API."""
    try:
        import anthropic
    except ImportError:
        raise SystemExit("The Claude client is not installed: pip install 'scanocr[claude]'")
    client = anthropic.Anthropic(api_key=key, max_retries=5)

    def ask(model, system, messages):
        try:
            r = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system, messages=messages)
        except anthropic.AuthenticationError:
            raise FatalError('The API key was refused: check it at console.anthropic.com.')
        except anthropic.PermissionDeniedError as e:
            raise FatalError(f'The API key may not use {model}: {e.message}')
        except anthropic.NotFoundError:
            raise FatalError(f'Anthropic does not know a model called {model}.')
        except anthropic.RateLimitError:
            raise PageError('Anthropic is over its rate limit for this key (tried 6 times)', fault=True)
        except anthropic.APIStatusError as e:
            raise PageError(f'Anthropic could not answer (error {e.status_code}): {e.message}',
                            fault=e.status_code >= 500)
        except anthropic.APIConnectionError as e:
            raise PageError(f'no connection to Anthropic: {e}', fault=True)
        used = (r.usage.input_tokens, r.usage.output_tokens)
        # a reply is the corrections only when the model finished it
        if r.stop_reason == 'refusal':
            raise PageError('the model declined this page', used)
        if r.stop_reason == 'max_tokens':
            raise PageError('the reply was cut off before its end', used)
        text = ''.join(b.text for b in r.content if b.type == 'text')
        return text, *used
    return ask


def pending(workdir):
    """(drafts to proofread, pages with no draft), as page numbers."""
    drafts, empty = [], []
    for i, page in enumerate(load_pages(workdir), 1):
        if page.status == 'draft':
            drafts.append(i)
        elif page.status == 'todo':
            empty.append(i)
    return drafts, empty


def _sends_image(page):
    return TYPED not in page.body


def send_size(width, height):
    """The size a page image is sent at: the long edge and the pixel count capped."""
    scale = min(1, MAX_EDGE / max(width, height), (MAX_PIXELS / (width * height)) ** 0.5)
    while True:
        size = max(1, int(width * scale)), max(1, int(height * scale))
        if -(-size[0] // 28) * -(-size[1] // 28) <= MAX_TOKENS_PER_IMAGE:       # patches round up
            return size
        scale *= 0.98


def image_tokens(model, width, height):
    width, height = send_size(width, height)
    if provider(model) == 'gemini':
        return -(-width // 768) * -(-height // 768) * 258
    return -(-width // 28) * -(-height // 28)


def page_image(path):
    """(image bytes, media type) of a page image, scaled to ``send_size``."""
    pix = pymupdf.Pixmap(path)
    size = send_size(pix.width, pix.height)
    if size != (pix.width, pix.height):
        pix = pymupdf.Pixmap(pix, *size)
    data = pix.tobytes('png')
    if len(data) > JPEG_OVER:
        pix = pymupdf.Pixmap(pymupdf.csGRAY if pix.n - pix.alpha == 1 else pymupdf.csRGB, pix)
        return pix.tobytes('jpeg', jpg_quality=90), 'image/jpeg'
    return data, 'image/png'


def estimate(workdir, model=DEFAULT_MODEL):
    """(pages, tokens in, tokens out) to proofread the drafts."""
    drafts, _ = pending(workdir)
    tokens_in = 0
    for i in drafts:
        with open(page_path(workdir, i), encoding='utf-8') as f:
            page = parse_page(f.read())
        tokens_in += int((len(SYSTEM) + len(page.body)) / TEXT_CHARS_PER_TOKEN)
        if _sends_image(page):
            pix = pymupdf.Pixmap(os.path.join(workdir, 'pages', f'page_{i:04d}.png'))
            tokens_in += image_tokens(model, pix.width, pix.height)
    return len(drafts), tokens_in, len(drafts) * EXPECTED_OUT[provider(model)]


def cost(model, tokens_in, tokens_out):
    """Dollars at list prices, or None for a model not in MODELS."""
    if model not in MODELS:
        return None
    price_in, price_out = MODELS[model]
    return (tokens_in * price_in + tokens_out * price_out) / 1e6


def parse_reply(reply):
    """(printed page number or None, corrections text) from the model's reply."""
    lines = [l for l in reply.strip().splitlines() if not l.strip().startswith('```')]
    number = None
    if lines and (m := re.match(r'^page:\s*(\S*)\s*$', lines[0], re.I)):
        number = m.group(1) if not m.group(1) or number_value(m.group(1)) else None
        lines = lines[1:]
    text = '\n'.join(lines).strip()
    return number, '' if text.upper() == 'OK' else text


def proofread_page(ask, model, workdir, i, used=None):
    """Page ``i``'s draft corrected and marked done. SystemExit if the corrections still don't
    apply after being sent back once. ``used``: [tokens in, tokens out, tries], added to as it
    goes, so what a failed page cost is known too."""
    with open(page_path(workdir, i), encoding='utf-8') as f:
        page = parse_page(f.read())
    content = [{'type': 'text', 'text': f'Printed page number: {page.number or "(none found)"}\n\n'
                                        f'Draft:\n\n{page.body.replace(TYPED, "").strip()}'}]
    if _sends_image(page):
        data, media_type = page_image(os.path.join(workdir, 'pages', f'page_{i:04d}.png'))
        content.insert(0, {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type,
                                                       'data': b64encode(data).decode()}})
    messages = [{'role': 'user', 'content': content}]
    used = [0, 0, 0] if used is None else used
    for attempt in range(2):
        try:
            reply, tin, tout = ask(model, SYSTEM, messages)
        except PageError as e:
            used[0], used[1], used[2] = used[0] + e.used[0], used[1] + e.used[1], used[2] + 1
            raise
        used[0], used[1], used[2] = used[0] + tin, used[1] + tout, used[2] + 1
        if not reply.strip():       # nothing came back (blocked, or all thinking): not "no corrections"
            raise PageError('the model sent an empty reply')
        number, corrections = parse_reply(reply)
        try:
            apply_fixes(workdir, i, corrections, number)
            return used
        except SystemExit as e:
            if attempt:
                raise
            messages += [{'role': 'assistant', 'content': reply or 'OK'},
                         {'role': 'user', 'content': f'{e}\nReply again with corrected corrections only.'}]


def run(pdf, out, workdir=None, model=DEFAULT_MODEL, check=False, log=print, ask=None, stop=None):
    """Prepare ``pdf`` (made by ``ocr``), proofread its drafts with the model, and write the
    .md to ``out``; returns the .md's path, or None for a check, a stopped run, or when nothing
    could be built. ``stop``: a threading.Event; once set, no further page is sent (pages done
    are kept, and a rerun carries on)."""
    pdf, out = Path(pdf), Path(out)
    if out.suffix.lower() == '.pdf' or (out.exists() and pdf.exists() and out.samefile(pdf)):
        raise SystemExit(f'The transcription is saved as a .md, not over a PDF ({out.name}): '
                         'give it a name ending .md.')
    workdir = str(workdir or pdf.with_name(pdf.stem + '_pages'))
    prepare(str(pdf), workdir)                    # keeps page files already there: a rerun carries on
    say, logfile = log, os.path.join(workdir, 'transcribe-log.txt')

    def log(text):                                # to the window or terminal, and to a file that outlives it
        say(text)
        with open(logfile, 'a', encoding='utf-8') as f:
            f.write(f'{datetime.now():%H:%M:%S} {text}\n')
    log(f'--- {"check" if check else "run"} with {model}: {pdf.name}')
    drafts, empty = pending(workdir)
    n, tin, tout = estimate(workdir, model)
    price = cost(model, tin, tout)
    estimate_line = (f'{n} pages to proofread with {model}: about {tin:,} tokens in, {tout:,} out'
                     + (f', roughly ${price:.2f}' if price is not None else '') + ' (list prices; an estimate).')
    if empty:
        log(f'{len(empty)} pages have no draft (blank, or the PDF has no text layer: OCR it first) and are skipped: '
            f'{", ".join(map(str, empty[:12]))}{"..." if len(empty) > 12 else ""}')
    if check:
        log(estimate_line)
        return None
    if ask is None and drafts:
        key = api_key(model)
        if not key:
            raise SystemExit(f'No API key for {model}: paste {PROVIDERS[provider(model)][2]} in the app, '
                             f'or set {PROVIDERS[provider(model)][1][0]}.')
        ask = ask_for(model, key)
    log(estimate_line)
    totals, failed, done, lock = [0, 0], [], [0], threading.Lock()

    stop, fatal, faults = stop or threading.Event(), [], [0]

    def one(i):
        if stop.is_set():
            return
        used = [0, 0, 0]
        fault = False
        try:
            proofread_page(ask, model, workdir, i, used)
            problem = None
        except FatalError as e:
            stop.set()
            fatal.append(str(e))
            return
        except PageError as e:
            problem, fault = ' '.join(str(e).split()[:24]), e.fault
        except SystemExit as e:
            problem = ' '.join(str(e).split()[:14])
        except Exception as e:      # the network, the client library: this page, not the run
            problem, fault = f'{type(e).__name__}: {" ".join(str(e).split()[:20])}', True
        with lock:
            faults[0] = faults[0] + 1 if fault else 0
            if faults[0] >= SAME_FAULT and done[0] == len(failed):      # nothing has got through
                stop.set()
                fatal.append(f'Stopped after {faults[0]} pages failed the same way: {problem}')
            totals[0] += used[0]
            totals[1] += used[1]
            done[0] += 1
            if problem:
                failed.append(i)
            spent = f'{used[0]:,} in, {used[1]:,} out, {used[2]} {"try" if used[2] == 1 else "tries"}'
            log(f'page {i}: left as a draft ({problem}); {spent}' if problem
                else f'page {i} done ({done[0]} of {len(drafts)}): {spent}')
    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(one, drafts))
    if fatal:
        raise SystemExit(fatal[0])
    if stop.is_set():
        log(f'Stopped: {done[0] - len(failed)} of {len(drafts)} pages done and kept. Run again to carry on.')
        return None
    spent = cost(model, *totals)
    log(f'Used {totals[0]:,} tokens in, {totals[1]:,} out' + (f' (about ${spent:.2f})' if spent is not None else '') + '.')
    left = failed + empty
    notes = build(workdir, str(out), partial=bool(left), log=log)
    if notes is None:
        return None
    if left:
        log(f'The .md has gaps: pages {", ".join(map(str, sorted(left)))} are not in it.')
    if notes:
        log('Fix the notes listed above (in the page files, then run again: pages done are kept) '
            'before you typeset the .md.')
    return Path(out)
