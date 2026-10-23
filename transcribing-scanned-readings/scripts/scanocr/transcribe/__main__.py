import argparse
import sys

from . import __doc__ as DOC
from .build import build
from .progress import pack, status
from .sheets import prepare


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m scanocr.transcribe', description=DOC,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare', help='render pages, find blocks, write transcription files')
    p.add_argument('src')
    p.add_argument('workdir')
    p.add_argument('--dpi', type=int, default=200)
    p.add_argument('--resume', metavar='ZIP', help='carry on from a progress file made by pack')
    b = sub.add_parser('build', help='check transcriptions and write the .md and searchable PDF')
    b.add_argument('workdir')
    b.add_argument('outdir')
    b.add_argument('--keep-old-text', action='store_true', help="keep the PDF's existing text layer")
    b.add_argument('--partial', action='store_true', help='build the pages that are done so far')
    s = sub.add_parser('status', help='pages done, drafts to proofread, pages not started')
    s.add_argument('workdir')
    k = sub.add_parser('pack', help='save the transcriptions in one small file for a later session')
    k.add_argument('workdir')
    k.add_argument('zip')
    a = ap.parse_args(argv)
    if a.cmd == 'prepare':
        prepare(a.src, a.workdir, a.dpi, a.resume)
    elif a.cmd == 'status':
        status(a.workdir)
    elif a.cmd == 'pack':
        pack(a.workdir, a.zip)
    elif build(a.workdir, a.outdir, not a.keep_old_text, a.partial) is None:
        sys.exit(1)


if __name__ == '__main__':
    main()
