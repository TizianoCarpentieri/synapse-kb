"""Durable, attributed notes and explicit links; automatic edges stay separate."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .common import AtlasError, clean
from .index import connect, meta, reference
from .view import check_source, read_bytes, resolve
from . import events, reconcile

DEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS dependencies (
 src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL,
 anchor TEXT NOT NULL, cue TEXT NOT NULL, score REAL NOT NULL,
 relation TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'heuristic',
 evidence TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(src,dst,kind));
CREATE INDEX IF NOT EXISTS dep_src ON dependencies(src,score DESC);
CREATE TABLE IF NOT EXISTS shared_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, explicit_refs TEXT NOT NULL,
 anchors TEXT NOT NULL, author TEXT NOT NULL, status TEXT NOT NULL,
 kind TEXT NOT NULL, expires TEXT, uid TEXT);
CREATE TABLE IF NOT EXISTS note_links (
 node_id INTEGER NOT NULL, note_id INTEGER NOT NULL, anchor TEXT NOT NULL,
 kind TEXT NOT NULL, score REAL NOT NULL, PRIMARY KEY(node_id,note_id));
CREATE TABLE IF NOT EXISTS manual_edges (
 src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL,
 anchor TEXT NOT NULL, cue TEXT NOT NULL, score REAL NOT NULL,
 note_id INTEGER NOT NULL, PRIMARY KEY(src,dst,note_id));
CREATE TABLE IF NOT EXISTS binding_state (
 note_id INTEGER NOT NULL, role TEXT NOT NULL, ord INTEGER NOT NULL,
 state TEXT NOT NULL, node_id INTEGER, similarity REAL NOT NULL,
 flags TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(note_id,role,ord));
"""
KINDS = ('correzione_agente', 'eccezione_deroga', 'prerequisito_vincolo',
         'biforcazione_caso', 'ambito_applicazione', 'sostituzione_versione',
         'appunto_condiviso')
ACTIVE = ('exact', 'relocated')
# Labels are monotone when two workspaces merge: a revocation is never undone.
STATUS_RANK = {'proposed': 0, 'confirmed': 1, 'revoked': 2}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='shared_notes'").fetchone())


def ensure_schema(db):
    """Create or migrate graph and memory tables. Call outside a transaction."""
    columns = {r[1] for r in db.execute('PRAGMA table_info(dependencies)')}
    if columns and 'evidence' not in columns:
        db.execute('DROP TABLE dependencies')  # rebuildable candidates only
    db.executescript(DEP_SCHEMA)
    if 'uid' not in {r[1] for r in db.execute('PRAGMA table_info(shared_notes)')}:
        db.execute('ALTER TABLE shared_notes ADD COLUMN uid TEXT')
    for row in db.execute('SELECT * FROM shared_notes WHERE uid IS NULL').fetchall():
        db.execute('UPDATE shared_notes SET uid=? WHERE id=?', (note_uid(row), row['id']))
    db.commit()


def note_uid(note):
    """Content address of the original observation; stable across workspaces."""
    refs = note['explicit_refs'] if isinstance(note['explicit_refs'], dict) else json.loads(note['explicit_refs'])
    spans = [[b['path'], b['sha256'], b['start'], b['end'], b['checksum']]
             for role in ('sources', 'targets') for b in refs[role]]
    body = json.dumps([note['created_at'], note['author'], note['title'], note['body'], note['kind'], spans],
                      ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:32]


def binding(db, index, selector):
    node = resolve(db, selector)
    if not node or node['kind'] != 'passage':
        raise AtlasError('Use a passage reference from search/read, not a document or folder: ' + selector)
    source = check_source(db, index, node['sid'], verify=True)
    return _binding(db, index, node, source)


def _binding(db, index, node, source):
    quote = read_bytes(db, index, source, node['start'], node['end']).decode('utf-8')
    return dict(path=source['path'], sha256=source['sha'], start=node['start'],
                end=node['end'], checksum=node['checksum'], ref=reference(db, node['id']),
                quote=quote, crumbs=json.loads(node['crumbs']))


def capture_quotes(db, index):
    """Complete bindings created before quotes were stored, while their revision is still indexed."""
    if not exists(db):
        return 0
    done = 0
    for note in db.execute('SELECT id,explicit_refs FROM shared_notes').fetchall():
        refs, changed = json.loads(note['explicit_refs']), False
        for item in refs['sources'] + refs['targets']:
            if 'quote' in item:
                continue
            node_id = reconcile.exact(db, item)
            if node_id is None:
                continue
            node = db.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
            source = db.execute('SELECT * FROM sources WHERE id=?', (node['sid'],)).fetchone()
            try:
                item['quote'] = read_bytes(db, index, source, node['start'], node['end']).decode('utf-8')
            except (AtlasError, OSError):
                continue
            item['crumbs'] = json.loads(node['crumbs'])
            changed = True
            done += 1
        if changed:
            db.execute('UPDATE shared_notes SET explicit_refs=? WHERE id=?', (json.dumps(refs), note['id']))
    return done


def rebind(db, target):
    """Kept for callers of the 0.1 API: the identical revision only."""
    return reconcile.exact(db, target)


def eligible(note):
    return note['status'] != 'revoked' and (not note['expires'] or note['expires'] >= now()[:10])


def _auto_links(db, index, anchors, limit=25):
    """Passages sharing a whole lexical phrase with an unanchored note.

    Full-text search proposes candidates; each one is then checked with the
    same phrase extraction as before, so results match a full scan while only
    matching passages are read.
    """
    from .graph import extract_phrases
    wanted = set(anchors)
    ids = set()
    ordered = sorted(wanted)
    for position in range(0, len(ordered), 40):
        expression = ' OR '.join('"' + a.replace('"', '""') + '"' for a in ordered[position:position + 40])
        ids.update(r[0] for r in db.execute('SELECT rowid FROM search WHERE search MATCH ?', (expression,)))
    candidates, sources = [], {}
    for nid in sorted(ids):
        row = db.execute('SELECT * FROM nodes WHERE id=?', (nid,)).fetchone()
        if row is None:
            continue
        if row['sid'] not in sources:
            sources[row['sid']] = db.execute('SELECT * FROM sources WHERE id=?', (row['sid'],)).fetchone()
        body = read_bytes(db, index, sources[row['sid']], row['start'], row['end']).decode('utf-8')
        hits = wanted & set(extract_phrases(' '.join(json.loads(row['crumbs'])) + '\n' + body))
        if hits:
            best = max(sorted(hits), key=lambda p: (p.count(' '), len(p)))
            candidates.append((nid, best, min(90.0, 10.0 + len(best) + len(hits))))
    return sorted(candidates, key=lambda r: (-r[2], r[0]))[:limit]


def sync_memory(db, index):
    """Rebuild only derived links. Notes survive reindexing and revocation is explicit."""
    db.execute('DELETE FROM note_links')
    db.execute('DELETE FROM manual_edges')
    db.execute('DELETE FROM binding_state')
    for note in db.execute('SELECT * FROM shared_notes ORDER BY id').fetchall():
        if note['status'] == 'revoked':
            continue
        refs = json.loads(note['explicit_refs'])
        resolved = {}
        for role in ('sources', 'targets'):
            resolved[role] = [reconcile.resolve(db, index, b) for b in refs[role]]
            for order, state in enumerate(resolved[role]):
                db.execute('INSERT INTO binding_state VALUES(?,?,?,?,?,?,?,?)',
                           (note['id'], role, order, state['state'], state['node'], state['similarity'],
                            json.dumps(state['flags']), state['reason']))
        if not eligible(note):
            continue
        picked = []
        for state in resolved['sources']:
            if state['state'] == 'exact':
                picked.append((state['node'], 'explicit / exact source revision', 'explicit', 100.0))
            elif state['state'] == 'relocated':
                picked.append((state['node'], 'explicit / identical text in a new revision', 'explicit', 100.0))
            elif state['state'] == 'drifted':
                # Shown, labelled and ranked below every verified link: the note was
                # written for other text and nobody has reviewed the new place yet.
                picked.append((state['node'], f"unreviewed / {state['similarity']:.0%} of the annotated text; "
                               + (', '.join(state['flags']) or 'wording') + ' changed', 'drifted', 40.0))
        if refs['auto']:
            picked += [(nid, anchor, 'candidate', score)
                       for nid, anchor, score in _auto_links(db, index, json.loads(note['anchors']))]
        for nid, anchor, kind, score in picked:
            db.execute('INSERT OR REPLACE INTO note_links VALUES(?,?,?,?,?)', (nid, note['id'], anchor, kind, score))
        for src in resolved['sources']:
            for dst in resolved['targets']:
                if src['state'] in ACTIVE and dst['state'] in ACTIVE and src['node'] != dst['node']:
                    db.execute('INSERT OR REPLACE INTO manual_edges VALUES(?,?,?,?,?,?,?)',
                               (src['node'], dst['node'], note['kind'], note['title'], clean(note['body'], 220),
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
        ensure_schema(db)
        db.execute('BEGIN IMMEDIATE')
        refs = dict(sources=[binding(db, index, s) for s in sources],
                    targets=[binding(db, index, s) for s in targets], auto=not bool(sources))
        phrases = sorted(set(extract_phrases(title + '\n' + body)))
        # Multiword anchors reduce generic auto-links. With none, retain rare lexical terms.
        anchors = [p for p in phrases if ' ' in p] or phrases
        record = dict(created_at=now(), title=title, body=body, explicit_refs=refs, author=author, kind=kind)
        uid = note_uid(record)
        cur = db.execute('INSERT INTO shared_notes(created_at,title,body,explicit_refs,anchors,author,status,kind,expires,uid) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (record['created_at'], title, body, json.dumps(refs), json.dumps(anchors), author, status, kind, expires, uid))
        note_id = cur.lastrowid
        events.append(db, 'note_added', {'note_id': note_id, 'uid': uid, 'status': status}, author)
        sync_memory(db, index)
        count = db.execute('SELECT count(*) FROM note_links WHERE note_id=?', (note_id,)).fetchone()[0]
    backup(index)
    return {'note_id': note_id, 'uid': uid, 'status': status, 'linked_passages': count,
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
        tracked = _has_states(db)
        for row in rows:
            item = dict(row)
            refs = json.loads(item['explicit_refs'])
            item['explicit_refs'] = refs
            item['anchors'] = json.loads(item['anchors'])
            states = [dict(r) | {'flags': json.loads(r['flags'])} for r in db.execute(
                'SELECT role,ord,state,node_id,similarity,flags,reason FROM binding_state WHERE note_id=? ORDER BY role,ord',
                (row['id'],))] if tracked else []
            for state in states:
                node_id = state.pop('node_id')
                state['ref'] = reference(db, node_id) if node_id else None
            item['bindings'] = states
            # Everything not verified by equality needs a person: proposals and losses alike.
            item['stale_bindings'] = (sum(s['state'] not in ACTIVE for s in states) if tracked
                                      else sum(rebind(db, b) is None for b in refs['sources'] + refs['targets']))
            item['rebind_proposals'] = sum(s['state'] == 'drifted' for s in states)
            item['active'] = eligible(row)
            item['linked_passages'] = db.execute('SELECT count(*) FROM note_links WHERE note_id=?', (row['id'],)).fetchone()[0]
            result.append(item)
        return result


def _has_states(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='binding_state'").fetchone())


def revoke(index, note_id, author='agent'):
    with connect(index, write=True) as db:
        ensure_schema(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT uid FROM shared_notes WHERE id=?', (note_id,)).fetchone()
        if not row:
            raise AtlasError('Note not found: ' + str(note_id))
        db.execute('UPDATE shared_notes SET status="revoked" WHERE id=?', (note_id,))
        events.append(db, 'note_revoked', {'note_id': note_id, 'uid': row['uid']}, author)
        sync_memory(db, index)
    backup(index)
    return {'note_id': note_id, 'status': 'revoked'}


def backup(index):
    records = notes(index)
    target = index / 'memory.json'
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'schema': 'synapse-kb-memory-2', 'notes': records}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(target)
    lines = ['# Shared notes — generated view', '',
             'The SQLite database is authoritative. Editing this file does not change notes.',
             'Use `notes --revoke ID` to revoke a note. Back up the entire workspace.', '']
    for n in records:
        lines += [f"## #{n['id']} {n['title']}", '',
                  f"{n['status']} | {n['author']} | {n['created_at']} | bindings to review: {n['stale_bindings']}",
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
        shown = set()
        for row in db.execute(f'SELECT sn.*,nl.kind link_kind,nl.anchor link_anchor FROM shared_notes sn JOIN note_links nl ON nl.note_id=sn.id '
                              f'WHERE nl.node_id IN ({slots}) ORDER BY sn.id DESC,nl.score DESC', tuple(sorted(ids))):
            if eligible(row) and row['id'] not in shown:
                shown.add(row['id'])
                records.append(dict(type='note', note_id=row['id'], title=row['title'],
                                    cue=clean(row['body'], 220), status=row['status'], author=row['author'],
                                    created_at=row['created_at'], expires=row['expires'],
                                    binding=row['link_kind'], binding_detail=row['link_anchor'],
                                    read_command=f'notes {row["id"]}'))
        query = (f'SELECT src,dst,kind,anchor,cue,score,origin,relation,evidence,NULL note_id FROM dependencies WHERE src IN ({slots}) '
                 f'UNION ALL SELECT src,dst,kind,anchor,cue,score,"explicit" origin,"" relation,"{{}}" evidence,note_id FROM manual_edges WHERE src IN ({slots}) ORDER BY score DESC,dst')
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
                                score=row['score'], status='candidate', note_id=row['note_id'],
                                relation=row['relation'], evidence=json.loads(row['evidence'])))
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
            drift = ' / WRITTEN FOR OTHER TEXT, ' + item['binding_detail'] if item.get('binding') == 'drifted' else ''
            lines.append(f"- [NOTE #{item['note_id']} / {item['status']}{drift}] {item['title']}: {item['cue']} (full text: {item['read_command']})")
        else:
            lines.append(f"- [{item['kind']} / {item['origin']}] {item['source_ref']} -> {item['target_ref']} ({item['path']}:{item['line']}): {item['cue']}")
    if data['omitted']:
        lines.append(f"+ {data['omitted']} further links: use dependencies REF --limit 1000.")
    return '\n'.join(lines) + '\n\n'


def review(index):
    """Notes whose bindings are not verified by equality, with what the engine found."""
    return [dict(note_id=n['id'], title=n['title'], status=n['status'], active=n['active'],
                 bindings=[b for b in n['bindings'] if b['state'] not in ACTIVE],
                 accept_command=f'notes --rebind {n["id"]}' if n['rebind_proposals'] else None)
            for n in notes(index) if n['status'] != 'revoked' and n['stale_bindings']]


def accept_rebind(index, note_id, author='reviewer'):
    """A person moves a note to the proposed passages. The earlier binding stays in its history."""
    if not author.strip():
        raise AtlasError('A reviewer name is required.')
    with connect(index, write=True) as db:
        ensure_schema(db)
        db.execute('BEGIN IMMEDIATE')
        note = db.execute('SELECT * FROM shared_notes WHERE id=?', (note_id,)).fetchone()
        if not note:
            raise AtlasError('Note not found: ' + str(note_id))
        refs, moved = json.loads(note['explicit_refs']), []
        for role in ('sources', 'targets'):
            for order, old in enumerate(refs[role]):
                state = reconcile.resolve(db, index, old)
                if state['state'] != 'drifted':
                    continue
                node = db.execute('SELECT * FROM nodes WHERE id=?', (state['node'],)).fetchone()
                source = check_source(db, index, node['sid'], verify=True)
                new = _binding(db, index, node, source)
                new['history'] = old.get('history', []) + [dict(
                    path=old['path'], sha256=old['sha256'], start=old['start'], end=old['end'],
                    checksum=old['checksum'], similarity=state['similarity'], flags=state['flags'],
                    accepted_by=author, accepted_at=now())]
                refs[role][order] = new
                moved.append(dict(role=role, ref=new['ref'], path=new['path'], similarity=state['similarity'], flags=state['flags']))
        if not moved:
            raise AtlasError('No rebind proposal for this note. Run notes --review.')
        db.execute('UPDATE shared_notes SET explicit_refs=? WHERE id=?', (json.dumps(refs), note_id))
        events.append(db, 'binding_accepted', {'note_id': note_id, 'uid': note['uid'], 'moved': moved}, author)
        sync_memory(db, index)
    backup(index)
    return {'note_id': note_id, 'accepted': moved, 'accepted_by': author}


def import_notes(index, path, author='import', keep_status=False):
    """Merge another workspace's memory export. Same observation, same uid, one copy.

    The union is order-independent; a revocation always wins. `confirmed` is a
    local person's label, so it arrives as `proposed` unless keep_status is set.
    Bindings are content-addressed and are resolved against this index like any other.
    """
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('schema') not in ('synapse-kb-memory-1', 'synapse-kb-memory-2') or not isinstance(data.get('notes'), list):
        raise AtlasError('Not a Synapse KB memory export.')
    added = revoked = known = 0
    with connect(index, write=True) as db:
        ensure_schema(db)
        db.execute('BEGIN IMMEDIATE')
        for item in data['notes']:
            refs = item['explicit_refs']
            if item['status'] not in STATUS_RANK or item['kind'] not in KINDS or set(refs) != {'sources', 'targets', 'auto'}:
                raise AtlasError('Malformed note in the export; nothing was imported.')
            if len(item['body']) > 20000 or len(item['title']) > 200:
                raise AtlasError('Note exceeds the size limits; nothing was imported.')
            for b in refs['sources'] + refs['targets']:
                b.pop('ref', None)  # node numbers belong to the exporting index
                b.pop('history', None)
                {b[k] for k in ('path', 'sha256', 'start', 'end', 'checksum')}
            uid = item.get('uid') or note_uid(item)
            local = db.execute('SELECT id,status FROM shared_notes WHERE uid=?', (uid,)).fetchone()
            if local:
                known += 1
                if item['status'] == 'revoked' and local['status'] != 'revoked':
                    db.execute('UPDATE shared_notes SET status="revoked" WHERE id=?', (local['id'],))
                    events.append(db, 'note_revoked', {'note_id': local['id'], 'uid': uid, 'via': 'import'}, author)
                    revoked += 1
                continue
            status = item['status'] if keep_status or item['status'] != 'confirmed' else 'proposed'
            cur = db.execute('INSERT INTO shared_notes(created_at,title,body,explicit_refs,anchors,author,status,kind,expires,uid) '
                             'VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (item['created_at'], item['title'], item['body'], json.dumps(refs),
                              json.dumps(item.get('anchors', [])), item['author'], status, item['kind'], item.get('expires'), uid))
            events.append(db, 'note_imported', {'note_id': cur.lastrowid, 'uid': uid, 'status': status}, author)
            added += 1
        sync_memory(db, index)
    backup(index)
    return {'imported': added, 'already_present': known, 'revocations_applied': revoked,
            'to_review': len(review(index))}
