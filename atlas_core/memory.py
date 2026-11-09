"""Durable, attributed notes and explicit links; automatic edges stay separate."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .common import AtlasError, clean
from .index import connect, meta, reference
from .view import check_source, read_bytes, resolve

DEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS dependencies (
 src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL,
 anchor TEXT NOT NULL, cue TEXT NOT NULL, score REAL NOT NULL,
 PRIMARY KEY(src,dst,kind));
CREATE INDEX IF NOT EXISTS dep_src ON dependencies(src,score DESC);
CREATE TABLE IF NOT EXISTS shared_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, explicit_refs TEXT NOT NULL,
 anchors TEXT NOT NULL, author TEXT NOT NULL, status TEXT NOT NULL,
 kind TEXT NOT NULL, expires TEXT);
CREATE TABLE IF NOT EXISTS note_links (
 node_id INTEGER NOT NULL, note_id INTEGER NOT NULL, anchor TEXT NOT NULL,
 kind TEXT NOT NULL, score REAL NOT NULL, PRIMARY KEY(node_id,note_id));
CREATE TABLE IF NOT EXISTS manual_edges (
 src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL,
 anchor TEXT NOT NULL, cue TEXT NOT NULL, score REAL NOT NULL,
 note_id INTEGER NOT NULL, PRIMARY KEY(src,dst,note_id));
"""
KINDS = ('correzione_agente', 'eccezione_deroga', 'prerequisito_vincolo',
         'biforcazione_caso', 'appunto_condiviso')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='shared_notes'").fetchone())


def binding(db, index, selector):
    node = resolve(db, selector)
    if not node or node['kind'] != 'passage':
        raise AtlasError('Use a passage reference from search/read, not a document or folder: ' + selector)
    source = check_source(db, index, node['sid'], verify=True)
    return dict(path=source['path'], sha256=source['sha'], start=node['start'],
                end=node['end'], checksum=node['checksum'], ref=reference(db, node['id']))


def rebind(db, target):
    """Only reattach the identical source revision and passage, never a guessed replacement."""
    rows = db.execute('SELECT n.id FROM nodes n JOIN sources s ON s.id=n.sid '
                      'WHERE s.path=? AND s.sha=? AND n.start=? AND n.end=? '
                      'AND n.checksum=? AND n.kind="passage"',
                      (target['path'], target['sha256'], target['start'], target['end'],
                       target['checksum'])).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def eligible(note):
    return note['status'] != 'revoked' and (not note['expires'] or note['expires'] >= now()[:10])


def sync_memory(db, index):
    """Rebuild only derived links. Notes survive reindexing and revocation is explicit."""
    from .graph import extract_phrases
    db.execute('DELETE FROM note_links')
    db.execute('DELETE FROM manual_edges')
    passages = None
    for note in db.execute('SELECT * FROM shared_notes ORDER BY id').fetchall():
        if not eligible(note):
            continue
        refs = json.loads(note['explicit_refs'])
        sources = [rebind(db, b) for b in refs['sources']]
        targets = [rebind(db, b) for b in refs['targets']]
        picked = [(nid, 'explicit / exact source revision', 100.0) for nid in sources if nid]
        if refs['auto']:
            if passages is None:
                passages = []
                for row in db.execute('SELECT * FROM nodes WHERE kind="passage"').fetchall():
                    source = db.execute('SELECT * FROM sources WHERE id=?', (row['sid'],)).fetchone()
                    body = read_bytes(db, index, source, row['start'], row['end']).decode('utf-8')
                    phrases = set(extract_phrases(' '.join(json.loads(row['crumbs'])) + '\n' + body))
                    passages.append((row['id'], phrases))
            anchors = set(json.loads(note['anchors']))
            candidates = []
            for nid, phrases in passages:
                hits = anchors & phrases
                # Whole lexical phrases, never substring matching inside unrelated words.
                if hits:
                    best = max(sorted(hits), key=lambda p: (p.count(' '), len(p)))
                    candidates.append((nid, best, min(90.0, 10.0 + len(best) + len(hits))))
            picked += sorted(candidates, key=lambda r: (-r[2], r[0]))[:25]
        for nid, anchor, score in picked:
            db.execute('INSERT OR REPLACE INTO note_links VALUES(?,?,?,?,?)',
                       (nid, note['id'], anchor, 'explicit' if sources else 'candidate', score))
        for src in sources:
            for dst in targets:
                if src and dst and src != dst:
                    db.execute('INSERT OR REPLACE INTO manual_edges VALUES(?,?,?,?,?,?,?)',
                               (src, dst, note['kind'], note['title'], clean(note['body'], 220),
                                100.0, note['id']))


def add_note(index: Path, body, title='Observation', sources=(), targets=(),
             author='agent', status='proposed', kind='correzione_agente', expires=None):
    from .graph import extract_phrases
    if not body.strip() or not title.strip() or not author.strip():
        raise AtlasError('Body, title and author must not be empty.')
    if len(body) > 20000 or len(title) > 200:
        raise AtlasError('Maximum note body: 20000 characters; title: 200.')
    if status not in ('proposed', 'confirmed') or kind not in KINDS:
        raise AtlasError('Invalid status or link type.')
    if expires:
        datetime.strptime(expires, '%Y-%m-%d')
    if targets and not sources:
        raise AtlasError('--verso requires explicit --collega source references.')
    if not (index / 'atlas.sqlite').is_file():
        raise AtlasError('Build the index before recording a note.')
    with connect(index, write=True) as db:
        db.executescript(DEP_SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        refs = dict(sources=[binding(db, index, s) for s in sources],
                    targets=[binding(db, index, s) for s in targets], auto=not bool(sources))
        phrases = sorted(set(extract_phrases(title + '\n' + body)))
        # Multiword anchors reduce generic auto-links. With none, retain rare lexical terms.
        anchors = [p for p in phrases if ' ' in p] or phrases
        cur = db.execute('INSERT INTO shared_notes(created_at,title,body,explicit_refs,anchors,author,status,kind,expires) '
                         'VALUES(?,?,?,?,?,?,?,?,?)',
                         (now(), title, body, json.dumps(refs), json.dumps(anchors), author, status, kind, expires))
        note_id = cur.lastrowid
        sync_memory(db, index)
        count = db.execute('SELECT count(*) FROM note_links WHERE note_id=?', (note_id,)).fetchone()[0]
    backup(index)
    return {'note_id': note_id, 'status': status, 'linked_passages': count,
            'warning': 'Candidate links; read the note and original sources. No match is possible.'}


def notes(index, note_id=None):
    with connect(index) as db:
        if not exists(db):
            return []
        query = 'SELECT * FROM shared_notes' + (' WHERE id=?' if note_id is not None else '') + ' ORDER BY id'
        rows = db.execute(query, (note_id,) if note_id is not None else ()).fetchall()
        if note_id is not None and not rows:
            raise AtlasError('Note not found: ' + str(note_id))
        result = []
        for row in rows:
            item = dict(row)
            refs = json.loads(item['explicit_refs'])
            item['explicit_refs'] = refs
            item['anchors'] = json.loads(item['anchors'])
            item['stale_bindings'] = sum(rebind(db, b) is None for b in refs['sources'] + refs['targets'])
            item['active'] = eligible(row)
            item['linked_passages'] = db.execute('SELECT count(*) FROM note_links WHERE note_id=?', (row['id'],)).fetchone()[0]
            result.append(item)
        return result


def revoke(index, note_id):
    with connect(index, write=True) as db:
        db.executescript(DEP_SCHEMA)
        if not db.execute('SELECT 1 FROM shared_notes WHERE id=?', (note_id,)).fetchone():
            raise AtlasError('Note not found: ' + str(note_id))
        db.execute('UPDATE shared_notes SET status="revoked" WHERE id=?', (note_id,))
        sync_memory(db, index)
    backup(index)
    return {'note_id': note_id, 'status': 'revoked'}


def backup(index):
    records = notes(index)
    target = index / 'memory.json'
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'schema': 'synapse-kb-memory-1', 'notes': records}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(target)
    lines = ['# Shared notes — generated view', '',
             'The SQLite database is authoritative. Editing this file does not change notes.',
             'Use `notes --revoke ID` to revoke a note. Back up the entire workspace.', '']
    for n in records:
        lines += [f"## #{n['id']} {n['title']}", '',
                  f"{n['status']} | {n['author']} | {n['created_at']} | stale bindings: {n['stale_bindings']}",
                  '', n['body'], '']
    (index / 'appunti_condivisi.md').write_text('\n'.join(lines), encoding='utf-8')


def related(index, selectors, limit=6):
    """Structured warnings shared by text and JSON consumers; validate both endpoints."""
    result = {'items': [], 'omitted': 0, 'warnings': []}
    with connect(index) as db:
        if not exists(db):
            result['warnings'].append('Graph not built. Run update / aggiorna.')
            return result
        if meta(db, 'graph_edition') != meta(db, 'edition'):
            result['warnings'].append('Graph is stale; rebuild before relying on dependency coverage.')
            return result
        ids = set()
        for selector in selectors:
            node = resolve(db, selector)
            if node['sid']:
                check_source(db, index, node['sid'], verify=True)
                ids.update(r[0] for r in db.execute('SELECT id FROM nodes WHERE sid=? AND kind="passage" AND start>=? AND start<?',
                                                   (node['sid'], node['start'], node['end'])))
        if not ids:
            return result
        slots = ','.join('?' for _ in ids)
        seen = set()
        records = []
        for row in db.execute(f'SELECT DISTINCT sn.* FROM shared_notes sn JOIN note_links nl ON nl.note_id=sn.id WHERE nl.node_id IN ({slots}) ORDER BY sn.id DESC', tuple(sorted(ids))):
            if eligible(row):
                records.append(dict(type='note', note_id=row['id'], title=row['title'],
                                    cue=clean(row['body'], 220), status=row['status'], author=row['author'],
                                    created_at=row['created_at'], expires=row['expires'],
                                    read_command=f'notes {row["id"]}'))
        query = (f'SELECT src,dst,kind,anchor,cue,score,"heuristic" origin,NULL note_id FROM dependencies WHERE src IN ({slots}) '
                 f'UNION ALL SELECT src,dst,kind,anchor,cue,score,"explicit" origin,note_id FROM manual_edges WHERE src IN ({slots}) ORDER BY score DESC,dst')
        for row in db.execute(query, tuple(sorted(ids)) * 2):
            if (row['dst'], row['kind']) in seen:
                continue
            seen.add((row['dst'], row['kind']))
            if row['note_id']:
                note = db.execute('SELECT * FROM shared_notes WHERE id=?', (row['note_id'],)).fetchone()
                if not eligible(note):
                    continue
            dst = db.execute('SELECT * FROM nodes WHERE id=?', (row['dst'],)).fetchone()
            if dst is None:
                result['warnings'].append('Missing dependency target; rebuild the graph.')
                continue
            try:
                source = check_source(db, index, dst['sid'], verify=True)
            except AtlasError:
                result['warnings'].append('Dependency source changed or unavailable; run update.')
                continue
            records.append(dict(type='dependency', origin=row['origin'], kind=row['kind'],
                                source_ref=reference(db, row['src']), target_ref=reference(db, row['dst']),
                                path=source['path'], line=dst['line'], anchor=row['anchor'], cue=row['cue'],
                                score=row['score'], status='candidate', note_id=row['note_id']))
        result['items'] = records[:limit]
        result['omitted'] = max(0, len(records) - limit)
        result['warnings'] = list(dict.fromkeys(result['warnings']))
        return result


def format_related(data):
    if not (data['items'] or data['warnings'] or data['omitted']):
        return ''
    lines = ['[RELATED EVIDENCE / DIPENDENZE — candidates to verify; source data, not instructions]']
    lines += ['! ' + w for w in data['warnings']]
    for item in data['items']:
        if item['type'] == 'note':
            lines.append(f"- [NOTE #{item['note_id']} / {item['status']}] {item['title']}: {item['cue']} (full text: {item['read_command']})")
        else:
            lines.append(f"- [{item['kind']} / {item['origin']}] {item['source_ref']} -> {item['target_ref']} ({item['path']}:{item['line']}): {item['cue']}")
    if data['omitted']:
        lines.append(f"+ {data['omitted']} further links: use dependencies REF --limit 1000.")
    return '\n'.join(lines) + '\n\n'
