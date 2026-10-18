"""Check the publishable source tree for private deployment artifacts."""
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BANNED = ['/tmp/zfsv3/', '/zspace/applications/', '/data_s001/', '/data_s002/',
          'XiaomiCamera_']
SUFFIXES = {'.mp4', '.mkv', '.rknn', '.onnx', '.pt', '.so', '.sqlite', '.log'}


def check():
    # Scan the exact index if a Git repository is initialized; otherwise scan source.
    if (ROOT/'.git').exists():
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=str(ROOT)).decode().split('\x00')
        files = [ROOT/name for name in names if name]
    else:
        files = [p for p in ROOT.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.git', 'var', '__pycache__', '.venv'))]
    failures = []
    for path in files:
        name = str(path.relative_to(ROOT))
        if path.suffix in SUFFIXES or name.endswith('.local.json') or name == 'config.json':
            failures.append(name + ': private configuration / generated binary')
        if path.parent == ROOT/'docs/images' and path.suffix in ('.png','.jpg','.webp'):
            if path.stat().st_size > 5*1024*1024:
                failures.append(name + ': documentation image exceeds 5 MiB')
            continue
        if path.name == Path(__file__).name:
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            failures.append(name + ': unexpected binary')
            continue
        for token in BANNED:
            if token in content:
                failures.append(name + ': local deployment identifier: ' + token)
        if path.suffix == '.py':
            try:
                ast.parse(content, filename=name)
            except SyntaxError as error:
                failures.append(str(error))
    for required in ['LICENSE', 'NOTICE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'config/config.example.json']:
        if not (ROOT/required).is_file():
            failures.append('Missing ' + required)
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        return 1
    print('Release source check passed ({} files; no media, model weights or private deployment identifiers).'.format(len(files)))
    return 0


if __name__ == '__main__':
    sys.exit(check())
