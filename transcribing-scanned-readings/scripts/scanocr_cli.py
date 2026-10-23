"""Run the scanocr transcription tool from this skill, wherever the skill is installed.

    python3 scripts/scanocr_cli.py <command> ...   (the commands of python -m scanocr.transcribe;
                                                    --help lists them)

In a claude.ai chat the skill carries its own copy of the code (scripts/scanocr/, put in
by tools/package_skill.py), but first fetches the latest published code (PUBLISHED, copied
there from scanocr's main branch on every merge), so code fixes need no new upload. If it
can't (offline, GitHub not allowed in the chat's network settings), the bundled copy is
used. In Claude Code on the scanocr repository the repository's code is used. PyMuPDF
is installed on first run if missing.
"""
import importlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))   # <repo>/.claude/skills/<skill>/scripts
SKILL = os.path.basename(os.path.dirname(HERE))
NEEDED = {'pymupdf': 'pymupdf==1.28.2'}     # as in pyproject.toml
PUBLISHED = 'https://github.com/diane350/scanocr-skill'
# '' turns fetching off (tests); a file:// URL stands in for GitHub
LATEST_URL = os.environ.get('SCANOCR_SKILL_URL', 'https://codeload.github.com/diane350/scanocr-skill/tar.gz/refs/heads/main')
CACHE = os.path.join(tempfile.gettempdir(), 'scanocr-skill-latest')
MAX_AGE = 6 * 3600                          # seconds; an older copy in CACHE is fetched again


def has_code(place):
    return os.path.exists(os.path.join(place, 'scanocr', '__init__.py'))


def code_location():
    if has_code(HERE):                          # an installed skill: the latest code, else its own
        return latest_code() or HERE
    if has_code(REPO):
        return REPO
    sys.exit('The scanocr code is missing: this skill was not packaged with tools/package_skill.py, '
             'and it is not inside the scanocr repository.')


def latest_code():
    """The published code, fetched into CACHE (again once the copy there is MAX_AGE old);
    None if it can't be had."""
    if not LATEST_URL:
        return None
    if not has_code(CACHE) or time.time() - os.path.getmtime(CACHE) > MAX_AGE:
        try:
            data = urllib.request.urlopen(LATEST_URL, timeout=20).read()
            part = CACHE + '.part'
            shutil.rmtree(part, ignore_errors=True)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                for m in tar.getmembers():          # <repo>-main/<skill>/... -> CACHE/...
                    rel = m.name.split('/', 1)[1] if '/' in m.name else ''
                    if not rel.startswith(f'{SKILL}/') or not (m.isfile() or m.isdir()):
                        continue
                    m.name = rel[len(SKILL) + 1:].removeprefix('scripts/')
                    if m.name:
                        tar.extract(m, part, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
            if not has_code(part):
                raise ValueError('no scanocr code in it')
            shutil.rmtree(CACHE, ignore_errors=True)
            os.replace(part, CACHE)
            os.utime(CACHE)
        except Exception as e:                  # offline, blocked by the chat's network settings, ...
            if not has_code(CACHE):
                print(f'Using the code bundled with the skill (the latest could not be fetched: {e}).', flush=True)
                return None
            print(f'Using the code fetched earlier (the latest could not be fetched: {e}).', flush=True)
    note_new_instructions()
    return CACHE


def note_new_instructions():
    """SKILL.md can't update itself: say so when the published one differs from this one."""
    def read(path):
        try:
            with open(path, encoding='utf-8') as f:
                return f.read()
        except OSError:
            return None
    published, mine = read(os.path.join(CACHE, 'SKILL.md')), read(os.path.join(HERE, '..', 'SKILL.md'))
    if published and mine and published != mine:
        print(f'Note: the skill\'s instructions have been updated. Tell the user to download {SKILL}.zip '
              f'from {PUBLISHED} and upload it in Customize > Skills to replace this one.', flush=True)


def ensure_libraries():
    missing = []
    for module, package in NEEDED.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if not missing:
        return
    print(f'Installing {", ".join(missing)} (first run only)...', flush=True)
    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *missing], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:])
        sys.exit('Could not install PyMuPDF. In a chat, code execution needs network access '
                 'to package managers (Settings > Capabilities); then try again.')
    importlib.invalidate_caches()


def main():
    sys.path.insert(0, code_location())
    ensure_libraries()
    from scanocr.transcribe.__main__ import main as run
    run(sys.argv[1:])


if __name__ == '__main__':
    main()
