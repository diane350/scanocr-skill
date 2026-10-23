import argparse
import sys

from . import __doc__ as DOC
from .build import build
from .pages import pack, prepare, status


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m scanocr.transcribe', description=DOC,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare', help='page images and a page file per page')
    p.add_argument('src', nargs='+', help='a PDF, or page images in order')
    p.add_argument('workdir')
    p.add_argument('--resume', metavar='ZIP', help='carry on from a progress file made by pack')
    s = sub.add_parser('status', help='pages done, drafts to proofread, pages not started')
    s.add_argument('workdir')
    b = sub.add_parser('build', help='join the done pages into one .md')
    b.add_argument('workdir')
    b.add_argument('out')
    b.add_argument('--partial', action='store_true', help='build with pages still to do')
    k = sub.add_parser('pack', help='save the page files in one small file for a later session')
    k.add_argument('workdir')
    k.add_argument('zip')
    a = ap.parse_args(argv)
    if a.cmd == 'prepare':
        prepare(a.src, a.workdir, a.resume)
    elif a.cmd == 'status':
        status(a.workdir)
    elif a.cmd == 'pack':
        pack(a.workdir, a.zip)
    elif not build(a.workdir, a.out, a.partial):
        sys.exit(1)


if __name__ == '__main__':
    main()
