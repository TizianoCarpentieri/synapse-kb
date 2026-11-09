"""Deterministic portable archive from reviewed local source and installed pypdf."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from atlas_core import __version__

BASE = Path(__file__).resolve().parent


def package():
    import pypdf
    distribution = importlib.metadata.distribution('pypdf')
    license_path = next(distribution.locate_file(p) for p in distribution.files
                        if str(p).replace('\\', '/').endswith('/licenses/LICENSE'))
    contents = {'__main__.py': b'import sys\nif sys.version_info < (3,11):\n    raise SystemExit("Python 3.11+ required")\nfrom atlas_core.cli import main\nraise SystemExit(main())\n'}
    for path in sorted((BASE / 'atlas_core').iterdir()):
        if path.suffix in ('.py', '.html'):
            contents['atlas_core/' + path.name] = path.read_bytes().replace(b'\r\n', b'\n')
    library = Path(pypdf.__file__).parent
    for path in sorted(library.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            contents['pypdf/' + path.relative_to(library).as_posix()] = path.read_bytes()
    contents['LICENSES/pypdf.txt'] = license_path.read_bytes()
    contents['LICENSES/Synapse KB.txt'] = (BASE / 'LICENSE').read_bytes()
    contents['BUNDLE.json'] = json.dumps({'synapse-kb': __version__, 'pypdf': pypdf.__version__,
                                        'python': '3.11+', 'engine_network_required': False}, sort_keys=True).encode()
    dist = BASE / 'dist'
    dist.mkdir(exist_ok=True)
    target = dist / 'atlas.pyz'
    with ZipFile(target, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(contents.items()):
            item = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            z.writestr(item, data, compresslevel=9)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    (dist / 'SHA256SUMS').write_text(checksum + '  atlas.pyz\n', encoding='ascii')
    print(json.dumps({'artifact': 'dist/atlas.pyz', 'sha256': checksum, 'bytes': target.stat().st_size}))
    return target


if __name__ == '__main__':
    package()
