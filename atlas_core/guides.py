"""Guidance the workspace owner attaches to a folder or a document.

A guide says how to proceed inside one branch of the collection: what to
establish first, which sub-branch fits which case. Guides are stored in the
workspace (`guides/<scope>/GUIDE.md`), never in the indexed sources, so text
found in a document can never become an instruction. Every command that
returns source text also returns the guides on the way from the root to that
text, most general first.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .common import AtlasError, safe_child
from .index import connect, get_root

NAME = 'GUIDE.md'
MAX_CHARS = 6000


def folder(index):
    """The workspace's guides folder, or None for a bare index without a workspace."""
    index = Path(index)
    return index.parent / 'guides' if (index.parent / 'atlas.toml').is_file() else None


def normalize(scope):
    scope = (scope or '').replace('\\', '/').strip().strip('/')
    if scope in ('', '.', 'root'):
        return ''
    parts = PurePosixPath(scope).parts
    if any(part in ('..', '.') for part in parts) or PurePosixPath(scope).is_absolute():
        raise AtlasError('A guide scope is a folder or document path inside the collection: ' + scope)
    return '/'.join(parts)


def _file(base, scope):
    return safe_child(base, (scope + '/' if scope else '') + NAME)


def _known(db, scope):
    if not scope:
        return True
    return bool(db.execute('SELECT 1 FROM dirs WHERE path=?', (scope,)).fetchone()
                or db.execute('SELECT 1 FROM sources WHERE path=?', (scope,)).fetchone())


def _load(base, scope):
    path = _file(base, scope)
    if not path.is_file():
        return None
    text = path.read_text(encoding='utf-8-sig').strip()
    if not text:
        return None
    return {'scope': scope or 'root', 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'text': text[:MAX_CHARS], 'truncated': len(text) > MAX_CHARS}


def scope_of(index, selector):
    """Collection path of whatever a reference, path or 'root' selects."""
    from .view import resolve
    with connect(index) as db:
        node = resolve(db, selector)
        if node['sid'] is not None:
            return db.execute('SELECT path FROM sources WHERE id=?', (node['sid'],)).fetchone()[0]
        while node is not None:
            row = db.execute('SELECT path FROM dirs WHERE node=?', (node['id'],)).fetchone()
            if row:
                return '' if row[0] == '.' else row[0]
            node = db.execute('SELECT * FROM nodes WHERE id=?', (node['parent'],)).fetchone() if node['parent'] else None
    return ''


def chain(index, paths, budget=4000):
    """Guides applying to the given collection paths, root first, each once.

    `budget` bounds the characters returned; guides that do not fit are listed
    without text so the reader knows to ask for them.
    """
    base = folder(index)
    result = {'items': [], 'omitted': []}
    if base is None or not base.is_dir():
        return result
    scopes = []
    for path in paths:
        parts = PurePosixPath(path).parts if path else ()
        for depth in range(len(parts) + 1):
            scope = '/'.join(parts[:depth])
            if scope not in scopes:
                scopes.append(scope)
    used = 0
    for scope in sorted(scopes, key=lambda s: (s.count('/') + bool(s), s)):
        guide = _load(base, scope)
        if not guide:
            continue
        if used + len(guide['text']) > budget and result['items']:
            result['omitted'].append(guide['scope'])
            continue
        used += len(guide['text'])
        result['items'].append(guide)
    return result


def format_chain(data):
    if not data['items'] and not data['omitted']:
        return ''
    lines = ['[WORKSPACE GUIDANCE — written by the workspace owner for this branch, not document text. '
             'Apply it, most specific last; ask the user for anything it tells you to establish.]']
    for guide in data['items']:
        lines.append(f"--- {guide['scope']} ({guide['sha256'][:8]}) ---")
        lines.append(guide['text'] + (' […truncated: `guide ' + guide['scope'] + '`]' if guide['truncated'] else ''))
    for scope in data['omitted']:
        lines.append(f'+ further guidance for {scope}: run guide "{scope}"')
    return '\n'.join(lines) + '\n\n'


def show(index, scope='root'):
    """The guides from the root down to one scope."""
    return chain(index, [normalize(scope)], budget=10 ** 9)


def listing(index):
    base = folder(index)
    if base is None or not base.is_dir():
        return []
    result = []
    with connect(index) as db:
        for path in sorted(base.rglob(NAME)):
            scope = path.parent.relative_to(base).as_posix()
            scope = '' if scope == '.' else scope
            guide = _load(base, scope)
            if guide:
                result.append({'scope': guide['scope'], 'sha256': guide['sha256'], 'chars': len(guide['text']),
                               'file': str(path), 'matches_collection': _known(db, scope)})
    return result


def save(index, scope, text, author='owner'):
    """Write one guide. The scope must be a folder or document of the indexed collection."""
    from . import events
    base = folder(index)
    if base is None:
        raise AtlasError('Guides belong to a workspace (a folder with atlas.toml). Run init first.')
    scope, text = normalize(scope), (text or '').strip()
    if len(text) > MAX_CHARS:
        raise AtlasError(f'A guide is at most {MAX_CHARS} characters; split it across sub-folders.')
    with connect(index, write=True) as db:
        if not _known(db, scope):
            raise AtlasError('No folder or document with this path in the collection: ' + scope +
                             '. Use the relative path shown by explore.')
        root = get_root(db, index)
        path = _file(base, scope)
        if path.resolve().is_relative_to(root.resolve()):
            raise AtlasError('Guides must not be stored inside the indexed sources.')
        if text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + '\n', encoding='utf-8')
        elif path.is_file():
            path.unlink()
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest() if text else None
        events.append(db, 'guide_set' if text else 'guide_removed', {'scope': scope or 'root', 'sha256': digest}, author)
    return {'scope': scope or 'root', 'sha256': digest, 'file': str(path) if text else None,
            'applies_to': 'every search, read, explore and consult under this path'}
