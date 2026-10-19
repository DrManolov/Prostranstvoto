"""Copy the supplied DOCX by checksum into the private import directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description='Подготовка на частните DOCX източници по SHA-256, без промяна на съдържанието.')
    parser.add_argument('folder', type=Path, help='Папка с Вашите оригинални DOCX файлове.')
    args = parser.parse_args()
    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        parser.error('Папката не съществува.')
    root = Path(__file__).resolve().parent.parent
    sources = json.loads((root / 'data' / 'source-manifest.json').read_text(encoding='utf-8'))['sources']
    by_digest = {source['sha256']: source for source in sources}
    found = {}
    for path in folder.rglob('*'):
        if path.is_file() and path.suffix.lower() == '.docx':
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in by_digest:
                found[digest] = path
    target = root / 'data' / 'originals'
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    copied = 0
    for digest, path in found.items():
        destination = target / by_digest[digest]['filename']
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise SystemExit(f'Файлът вече съществува с различно съдържание; не е презаписан: {destination.name}')
            continue
        with destination.open('xb') as output:
            os.chmod(destination, 0o600)
            with path.open('rb') as original:
                shutil.copyfileobj(original, output)
        copied += 1
    present = sum((target / source['filename']).is_file() for source in sources)
    print(f'Нови копия: {copied}. Налични източници: {present}/{len(sources)}.')
    if present != len(sources):
        print('Липсват част от оригиналите. Променени документи не се приемат за първоначалните източници.')
        return 1
    print('Оригиналите са подготвени. Изградете отново web образа и стартирайте приложението според data/README.md.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
