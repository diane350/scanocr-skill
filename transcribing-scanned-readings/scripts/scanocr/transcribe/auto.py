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
from pathlib import Path

import pymupdf

from .build import build
from .pages import TYPED, apply_fixes, load_pages, page_path, parse_page, prepare
from .text import number_value
from .vision_auth import CONFIG

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
MAX_TOKENS = 8000
# What a page image costs: Claude reads 28 x 28 px patches (width/28 x height/28 tokens) and scales an
# image down to at most 2,576 px on the long edge and 4,784 tokens (Anthropic's docs for models from 4.7 on);
# Gemini 768 px tiles at 258 tokens each. A page is scaled to fit before it is sent, so a big scan costs
# no more than that and stays under the 10 MB image limit.
MAX_EDGE = 2576
MAX_TOKENS_PER_IMAGE = 4784
MAX_PIXELS = MAX_TOKENS_PER_IMAGE * 28 * 28
JPEG_OVER = 7_000_000             # bytes of PNG above which the page goes as a JPEG
TEXT_CHARS_PER_TOKEN = 3.5
EXPECTED_OUT = 400                # corrections for one page, tokens

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
- Notes split or run together; a note continuing from the page before.
- Every diacritic exactly as printed (Kṛṣṇa, ʿulamā, Chaurī), curly quotes and dashes as printed, \
misspellings and old spellings kept. Do not modernise or correct the author.
Running heads, page numbers and watermarks are left out: they are in <!-- --> comments in the \
draft, leave those alone."""


class FatalError(SystemExit):
    """A problem every page would meet (a refused key, an unknown model): the run stops."""


def provider(model):
    return 'gemini' if model.startswith('gemini') else 'claude'


def key_file(model):
    return CONFIG / PROVIDERS[provider(model)][0]


def api_key(model=DEFAULT_MODEL):
    """The saved key (or the environment's) for the company whose model this is; '' if none."""
    for name in PROVIDERS[provider(model)][1]:
        if os.environ.get(name, '').strip():
            return os.environ[name].strip()
    return key_file(model).read_text(encoding='utf-8').strip() if key_file(model).is_file() else ''


def save_key(key, model=DEFAULT_MODEL):
    CONFIG.mkdir(parents=True, exist_ok=True)
    key_file(model).write_text(key.strip(), encoding='utf-8')
    os.chmod(key_file(model), 0o600)


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
            raise FatalError(f'Gemini refused the request: {e}')
        u = r.usage_metadata
        text = r.text or ''
        return text, u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
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
        text = ''.join(b.text for b in r.content if b.type == 'text')
        return text, r.usage.input_tokens, r.usage.output_tokens
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
    return len(drafts), tokens_in, len(drafts) * EXPECTED_OUT


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


def proofread_page(ask, model, workdir, i):
    """Page ``i``'s draft corrected and marked done; (tokens in, tokens out). SystemExit if
    the corrections still don't apply after being sent back once."""
    with open(page_path(workdir, i), encoding='utf-8') as f:
        page = parse_page(f.read())
    content = [{'type': 'text', 'text': f'Printed page number: {page.number or "(none found)"}\n\n'
                                        f'Draft:\n\n{page.body.replace(TYPED, "").strip()}'}]
    if _sends_image(page):
        data, media_type = page_image(os.path.join(workdir, 'pages', f'page_{i:04d}.png'))
        content.insert(0, {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type,
                                                       'data': b64encode(data).decode()}})
    messages = [{'role': 'user', 'content': content}]
    used_in = used_out = 0
    for attempt in range(2):
        reply, tin, tout = ask(model, SYSTEM, messages)
        used_in, used_out = used_in + tin, used_out + tout
        number, corrections = parse_reply(reply)
        try:
            apply_fixes(workdir, i, corrections, number)
            return used_in, used_out
        except SystemExit as e:
            if attempt:
                raise
            messages += [{'role': 'assistant', 'content': reply or 'OK'},
                         {'role': 'user', 'content': f'{e}\nReply again with corrected corrections only.'}]


def run(pdf, out, workdir=None, model=DEFAULT_MODEL, check=False, log=print, ask=None):
    """Prepare ``pdf`` (made by ``ocr``), proofread its drafts with the model, and write the
    .md to ``out``; returns the .md's path, or None for a check or when nothing could be built."""
    pdf = Path(pdf)
    workdir = str(workdir or pdf.with_name(pdf.stem + '_pages'))
    prepare(str(pdf), workdir)                    # keeps page files already there: a rerun carries on
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

    stop, fatal = threading.Event(), []

    def one(i):
        if stop.is_set():
            return
        try:
            tin, tout = proofread_page(ask, model, workdir, i)
            problem = None
        except FatalError as e:
            stop.set()
            fatal.append(str(e))
            return
        except SystemExit as e:
            tin = tout = 0
            problem = ' '.join(str(e).split()[:14])
        with lock:
            totals[0] += tin
            totals[1] += tout
            done[0] += 1
            if problem:
                failed.append(i)
            log(f'page {i}: left as a draft ({problem})' if problem else f'page {i} done ({done[0]} of {len(drafts)})')
    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(one, drafts))
    if fatal:
        raise SystemExit(fatal[0])
    spent = cost(model, *totals)
    log(f'Used {totals[0]:,} tokens in, {totals[1]:,} out' + (f' (about ${spent:.2f})' if spent is not None else '') + '.')
    left = failed + empty
    if not build(workdir, str(out), partial=bool(left)):
        return None
    if left:
        log(f'The .md has gaps: pages {", ".join(map(str, sorted(left)))} are not in it.')
    return Path(out)
