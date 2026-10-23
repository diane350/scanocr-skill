import argparse
import sys

from . import __doc__ as DOC
from .build import build
from .pages import fix, pack, prepare, status, zoom


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
    a = ap.parse_args(argv)
    a.run(a)


if __name__ == '__main__':
    main()
