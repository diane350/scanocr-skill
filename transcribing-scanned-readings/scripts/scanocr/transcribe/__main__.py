import argparse
import sys

from . import __doc__ as DOC
from .build import build
from .pages import fix, pack, prepare, status, zoom
from . import vision_auth
from .auto import DEFAULT_MODEL, run as auto
from .vision import ocr


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m scanocr.transcribe', description=DOC,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare', help='page images and a page file per page')
    p.add_argument('src', nargs='+', help='a PDF, or page images in order')
    p.add_argument('workdir')
    p.add_argument('--resume', metavar='ZIP', help='carry on from a progress file made by pack')
    p.set_defaults(run=lambda a: prepare(a.src, a.workdir, a.resume))
    z = sub.add_parser('zoom', help='a larger image of half a page, for small print')
    z.add_argument('workdir')
    z.add_argument('page', type=int, help='the page file number (NNNN in page_NNNN.md)')
    z.add_argument('part', choices=('top', 'bottom'))
    z.set_defaults(run=lambda a: zoom(a.workdir, a.page, a.part))
    f = sub.add_parser('fix', help='correct a draft page and mark it done (corrections on stdin)')
    f.add_argument('workdir')
    f.add_argument('page', type=int, help='the page file number (NNNN in page_NNNN.md)')
    f.set_defaults(run=lambda a: fix(a.workdir, a.page, sys.stdin.read()))
    s = sub.add_parser('status', help='pages done, drafts to proofread, pages not started')
    s.add_argument('workdir')
    s.set_defaults(run=lambda a: status(a.workdir))
    b = sub.add_parser('build', help='join the done pages into one .md')
    b.add_argument('workdir')
    b.add_argument('out')
    b.add_argument('--partial', action='store_true', help='build with pages still to do')
    b.set_defaults(run=lambda a: build(a.workdir, a.out, a.partial) or sys.exit(1))
    k = sub.add_parser('pack', help='save the page files in one small file for a later session')
    k.add_argument('workdir')
    k.add_argument('zip')
    k.set_defaults(run=lambda a: pack(a.workdir, a.zip))
    o = sub.add_parser('ocr', help='Google Cloud Vision OCR: a copy of a scan with a text layer (run on your computer)')
    o.add_argument('pdf')
    o.add_argument('-o', '--output', help='default: <pdf>_ocr.pdf')
    o.add_argument('--pages', help='e.g. 1-3,7 (1-based)')
    o.add_argument('--lang', default='en', help='comma-separated language hints')
    o.add_argument('--limit', type=int, default=900, help='stop before this many units a month')
    o.add_argument('--dry-run', action='store_true', help='show what would be sent, send nothing')
    o.add_argument('--force', action='store_true', help='go past --limit')
    o.add_argument('--sign-in', metavar='CLIENT_JSON', help='sign in to Google with a Desktop-app OAuth client first')
    o.set_defaults(run=lambda a: (a.sign_in and vision_auth.sign_in(a.sign_in)) or
                   ocr(a.pdf, a.output, a.pages, a.lang, a.limit, a.dry_run, a.force))
    t = sub.add_parser('auto', help='proofread an OCR\'d scan\'s drafts with Claude through the API and build the .md '
                                    '(optional; needs an API key)')
    t.add_argument('pdf', help='a PDF with a text layer, e.g. from ocr')
    t.add_argument('out', help='the .md to write')
    t.add_argument('--workdir', help='default: <pdf>_pages; a rerun carries on from the pages done')
    t.add_argument('--model', default=DEFAULT_MODEL)
    t.add_argument('--check', action='store_true', help='show the page count and a cost estimate, send nothing')
    t.set_defaults(run=lambda a: auto(a.pdf, a.out, a.workdir, a.model, a.check))
    a = ap.parse_args(argv)
    a.run(a)


if __name__ == '__main__':
    main()
