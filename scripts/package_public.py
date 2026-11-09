"""Build an audited source ZIP without workspaces, local reports or Git internals."""
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from audit_public import audit, BASE


def package():
    result = audit()
    if result['failures']:
        raise SystemExit(json.dumps(result, indent=2))
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=BASE)
    names = sorted(set(n.decode('utf-8') for n in names.split(b'\0') if n))
    dist = BASE / 'dist'
    dist.mkdir(exist_ok=True)
    archive = dist / 'synapse-kb-public-source.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
        for name in names:
            target = BASE / name
            if not target.is_file():
                continue
            item = ZipInfo('synapse-kb/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            z.writestr(item, target.read_bytes().replace(b'\r\n', b'\n'), compresslevel=9)
    artifacts = [p for p in sorted(dist.iterdir()) if p.suffix in ('.pyz', '.whl', '.zip', '.gz')]
    (dist / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in artifacts), encoding='ascii')
    print(json.dumps({'source_archive': str(archive), 'public_files': len(names), 'artifacts': [p.name for p in artifacts]}, indent=2))


if __name__ == '__main__':
    package()
