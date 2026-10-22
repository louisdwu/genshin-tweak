"""Line-ending & BOM checker — runs on Linux and Windows.

Usage:
    python _check_eol.py          # check all .bat/.cs/.manifest/.ps1/.py (recursive)
    python _check_eol.py a.cs b.bat

Rules:
    .bat/.cs/.manifest/.ps1 -> CRLF;  .cs/.ps1 additionally require UTF-8 BOM
    .py                     -> LF, no BOM

Exit code: number of files with issues (0 = all good).
"""
import glob
import os
import sys

DEFAULT_PATTERNS = ['**/*.bat', '**/*.cs', '**/*.manifest', '**/*.ps1', '**/*.py']


def check_file(fp):
    """Return (ok, msg) for one file."""
    with open(fp, 'rb') as f:
        data = f.read()
    crlf = data.count(b'\r\n')
    lf = data.count(b'\n') - crlf          # standalone LF
    cr_orphan = data.count(b'\r') - crlf   # stray CR (e.g. \r\r\n corruption)
    bom = data.startswith(b'\xef\xbb\xbf')
    ext = os.path.splitext(fp)[1].lower()

    if ext in ('.bat', '.cs', '.manifest', '.ps1'):
        want_crlf, want_bom = True, ext in ('.cs', '.ps1')
    elif ext == '.py':
        want_crlf, want_bom = False, False
    else:
        return True, 'no rules'

    msgs = []
    if cr_orphan > 0:
        msgs.append(f'{cr_orphan} orphan CR (broken line endings)')
    if want_crlf and lf > 0:
        msgs.append(f'{lf} LF-only line(s)')
    if (not want_crlf) and crlf > 0:
        msgs.append(f'{crlf} unexpected CRLF')
    if want_bom and not bom:
        msgs.append('missing UTF-8 BOM')
    if (not want_bom) and bom:
        msgs.append('unexpected BOM')

    if not msgs:
        return True, f'CRLF={crlf}, BOM={bom}'
    return False, '; '.join(msgs)


def main():
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = []
        for pat in DEFAULT_PATTERNS:
            files.extend(glob.glob(pat, recursive=True))
        files = sorted(set(f for f in files if os.path.isfile(f)))

    if not files:
        print('No files to check.')
        return 0

    errors = 0
    for fp in files:
        ok, msg = check_file(fp)
        print(('  OK   ' if ok else '  FAIL ') + f'{fp}  ({msg})')
        errors += 0 if ok else 1
    return errors


if __name__ == '__main__':
    sys.exit(main())
