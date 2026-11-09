"""Audit Git-visible files, reporting names only; not a complete secret scanner."""
import json
from pathlib import Path
import re
import subprocess

BASE = Path(__file__).resolve().parents[1]
ALLOWED_ROOTS = {'atlas_core', 'tests', 'scripts', 'docs', 'examples', 'licenses', '.github', 'schemas'}
ALLOWED_FILES = {'README.md', 'README.it.md', 'AGENTS.md', 'LICENSE', 'NOTICE',
                 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'SECURITY.md', 'GOVERNANCE.md',
                 'CHANGELOG.md', 'pyproject.toml', 'requirements-dev.txt', 'pack.py', 'atlas.py',
                 '.gitignore', '.gitattributes', '.editorconfig', 'MANIFEST.in'}
PATTERNS = [
    ('personal absolute path', re.compile(rb'(?:[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|YOUR_USER\b)[^\s"\r\n]+|/(?:Users|home)/[A-Za-z0-9_. -]+/)')),
    ('private key', re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ('token-shaped value', re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{32,})')),
    ('unrequested attribution trailer', re.compile(rb'(?im)^\s*(?:co-authored-by|generated-by|ai-assisted-by):')),
]


def audit():
    process = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                             cwd=BASE, capture_output=True, check=True)
    names = sorted(set(n.decode('utf-8') for n in process.stdout.split(b'\0') if n))
    failures = []
    for name in names:
        path = BASE / name
        first = Path(name).parts[0]
        if first not in ALLOWED_ROOTS and name not in ALLOWED_FILES:
            failures.append({'file': name, 'reason': 'outside public allowlist'})
        if path.is_symlink():
            failures.append({'file': name, 'reason': 'symlink requires review'})
            continue
        if not path.is_file():
            continue
        if path.suffix in ('.sqlite', '.pyz', '.docx', '.pdf', '.xlsx', '.pptx'):
            failures.append({'file': name, 'reason': 'unexpected generated/source binary'})
        if path.stat().st_size > 2_000_000:
            failures.append({'file': name, 'reason': 'large file requires review'})
            continue
        data = path.read_bytes()
        for label, pattern in PATTERNS:
            if pattern.search(data):
                failures.append({'file': name, 'reason': label})
    return {'files_checked': len(names), 'failures': failures,
            'notice': 'Heuristic review of Git-visible files, not a guarantee that no private data or rights issues exist.'}


if __name__ == '__main__':
    result = audit()
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['failures']))
