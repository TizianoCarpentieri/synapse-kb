from __future__ import annotations

import codecs
import errno
import fnmatch
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path

from .common import (AtlasError, SCHEMA, VERSION, PROFILES, DEFAULT_PROFILE,
                     clean, digest, json_text, keywords, safe_child, search_body, sha_file)
from .extract import extract

IGNORED = {'.git', '.hg', '.svn', '.venv', 'venv', 'node_modules', '__pycache__', '.cache',
           '.tox', '.mypy_cache', '.pytest_cache', '.deps', '.devdeps', '_atlas', '.atlas'}
DEFAULT_EXCLUDES = ['.env', '.env.*', '*.pem', '*.key', 'id_rsa', 'id_ed25519',
                    '.gitignore', '.gitattributes', '.gitmodules', '.editorconfig',
                    '*.pyz', 'AGENTS.md',
                    '*.min.js', '*.min.css', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']
SQL = '''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT UNIQUE NOT NULL,sha TEXT,size INTEGER,mtime INTEGER,
 cache TEXT,status TEXT NOT NULL,warnings TEXT NOT NULL,kind TEXT,root_node INTEGER,words INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS nodes(
 id INTEGER PRIMARY KEY AUTOINCREMENT,sid INTEGER,parent INTEGER,ord INTEGER DEFAULT 0,
 title TEXT NOT NULL DEFAULT '',label TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL,
 origin TEXT DEFAULT '',level INTEGER DEFAULT 0,start INTEGER DEFAULT 0,end INTEGER DEFAULT 0,
 line INTEGER DEFAULT 1,end_line INTEGER DEFAULT 1,crumbs TEXT DEFAULT '[]',loc TEXT DEFAULT '',
 checksum TEXT DEFAULT '',sample TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS node_parent ON nodes(parent,ord,id);
CREATE INDEX IF NOT EXISTS node_source ON nodes(sid,kind,start);
CREATE INDEX IF NOT EXISTS node_heading ON nodes(sid,level,start) WHERE kind='heading';
CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY,node INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(title,body,tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,
 payload TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL);
'''


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(index: Path, write=False):
    path = (index / 'atlas.sqlite').resolve()
    if not write and not path.is_file():
        raise AtlasError('Indice assente. Eseguire prima: atlas prepara CARTELLA')
    if write:
        db = sqlite3.connect(path, timeout=15, factory=ClosingConnection)
        # WAL: readers keep a consistent snapshot while one writer commits, so
        # consultation no longer needs the exclusive operation lock. Filesystems
        # that refuse WAL keep the rollback journal; correctness is unchanged.
        try:
            db.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError:
            pass
        db.executescript(SQL)
        db.execute('PRAGMA cache_size=-24000')
        db.execute('PRAGMA temp_store=FILE')
    else:
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    return db


def meta(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_meta(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))


def reference(db, nid, prefix='n'):
    raw = prefix + str(nid)
    binding = ''
    if prefix == 'n':
        row = db.execute('SELECT n.kind,n.start,n.end,n.checksum,s.sha,s.path FROM nodes n LEFT JOIN sources s ON s.id=n.sid WHERE n.id=?', (nid,)).fetchone()
        if row:
            binding = json_text(tuple(row)) if row['path'] is not None else meta(db, 'edition', '')
    else:
        row = db.execute('SELECT path FROM sources WHERE id=?', (nid,)).fetchone()
        binding = row[0] if row else ''
    return raw + '-' + digest((meta(db, 'identity', '') + raw + binding).encode())[:8]


def get_root(db, index):
    return (index / meta(db, 'root', '..')).resolve()


def add_node(db, **values):
    keys = list(values)
    q = ','.join('?' for _ in keys)
    return db.execute(f'INSERT INTO nodes({",".join(keys)}) VALUES({q})', tuple(values[k] for k in keys)).lastrowid


def directory(db, relative):
    row = db.execute('SELECT node FROM dirs WHERE path=?', (relative,)).fetchone()
    if row:
        return row[0]
    parent = directory(db, Path(relative).parent.as_posix()) if relative != '.' else None
    nid = add_node(db, parent=parent, title=relative if relative != '.' else 'Raccolta',
                   label=Path(relative).name if relative != '.' else 'Raccolta', kind='directory')
    db.execute('INSERT INTO dirs VALUES(?,?)', (relative, nid))
    return nid


def ignored(relative, patterns):
    return any(fnmatch.fnmatchcase(relative, p) or fnmatch.fnmatchcase(Path(relative).name, p)
               or relative.startswith(p.rstrip('/') + '/') for p in patterns)


def inventory(root, output, patterns):
    def fail(error):
        raise error
    for current, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        base = Path(current)
        allowed = []
        for name in sorted(dirs):
            p = base / name
            rel = p.relative_to(root).as_posix()
            if name in IGNORED or p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
                continue
            if p.resolve() == output or ignored(rel, patterns):
                continue
            allowed.append(name)
        dirs[:] = allowed
        for name in sorted(names):
            p = base / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink() or name == '.atlasignore' or name.startswith('~$') or ignored(rel, patterns):
                continue
            if p.resolve().is_relative_to(output):
                continue
            yield p, rel


def normalize(path, destination, warnings):
    headings = []
    repeated = Counter()
    position, line = 0, 1
    latest_location = ''
    with destination.open('wb') as out:
        for piece in extract(path, warnings):
            for candidate in set(piece.text.splitlines()):
                candidate = candidate.strip()
                if 10 <= len(candidate) <= 180 and (candidate in repeated or len(repeated) < 10000):
                    repeated[candidate] += 1
            if piece.location:
                latest_location = piece.location
            if piece.level:
                headings.append({'start': position, 'line': line, 'title': clean(piece.title, 400),
                                 'level': piece.level, 'origin': piece.origin, 'loc': latest_location})
            raw = piece.text.replace('\x00', '').encode('utf-8')
            out.write(raw)
            position += len(raw)
            line += piece.text.count('\n')
    threshold = max(5, int(sum(h['origin'] == 'pagina' for h in headings) * .8))
    boilerplate = {s for s, n in repeated.items() if n >= threshold}
    if boilerplate:
        warnings.append(f'{len(boilerplate)} righe ripetute ignorate solo nelle etichette; testo e ricerca integrali.')
    if len(headings) >= 20 and position / len(headings) < 120:
        warnings.append('Struttura molto fitta: i titoli derivano dal formato, non da una verifica del significato.')
    return headings, position, line, boilerplate


def label_sample(text, boilerplate):
    return '\n'.join(s for s in text.splitlines() if s.strip() not in boilerplate)


def chunks(stream, start, end, limit):
    """Yield exact, disjoint byte spans covering [start,end). No dropped whitespace."""
    stream.seek(start)
    remaining = end - start
    decoder = codecs.getincrementaldecoder('utf-8')()
    buf = ''
    offset = start
    while remaining or buf:
        if remaining:
            raw = stream.read(min(32768, max(1024, limit * 8), remaining))
            if not raw:
                raise AtlasError('Testo normalizzato troncato.')
            remaining -= len(raw)
            buf += decoder.decode(raw, final=remaining == 0)
        while buf:
            matches = list(re.finditer(r'\S+', buf))
            if len(matches) <= limit and len(buf) <= limit * 24 and remaining:
                break
            if len(matches) > limit:
                cut = matches[limit].start()
                minimum = cut * 2 // 3
                newline = buf.rfind('\n', minimum, cut)
                if newline >= minimum:
                    cut = newline + 1
            elif len(buf) > limit * 24:
                cut = limit * 24
            else:
                cut = len(buf)
            cut = min(cut, limit * 24)
            text, buf = buf[:cut], buf[cut:]
            data = text.encode('utf-8')
            yield offset, offset + len(data), text
            offset += len(data)
        if not remaining and not buf:
            break


def sample_span(path, start, end, width=1200):
    """Bounded stratified sample from actual underlying text, not summaries of summaries."""
    if end <= start:
        return ''
    total = end - start
    offsets = sorted(set(start + max(0, total - width) * i // 4 for i in range(5)))
    parts = []
    with path.open('rb') as stream:
        for pos in offsets:
            stream.seek(pos)
            parts.append(stream.read(min(width, end - pos)).decode('utf-8', errors='ignore'))
    return '\n'.join(parts)


def group_children(db, parent, fanout, sid=None, cache_path=None, boilerplate=(), progress=None):
    ids = [r[0] for r in db.execute('SELECT id FROM nodes WHERE parent=? ORDER BY ord,id', (parent,))]
    while len(ids) > fanout:
        new = []
        # Balance groups instead of leaving a one-item final group.
        count = math.ceil(len(ids) / fanout)
        size = math.ceil(len(ids) / count)
        for pos in range(0, len(ids), size):
            part = ids[pos:pos + size]
            slots = ','.join('?' for _ in part)
            rows = list(db.execute(f'SELECT * FROM nodes WHERE id IN ({slots}) ORDER BY ord,id', part))
            if cache_path is not None:
                sample = '\n'.join(r['title'] for r in rows) + '\n' + sample_span(cache_path, rows[0]['start'], rows[-1]['end'])
            else:
                sample = '\n'.join((r['title'] + ' ' + r['sample'][:240]) for r in rows)
            sample = label_sample(sample, boilerplate)
            label = keywords(sample, 3)
            if not label:
                label = f'Intervallo {rows[0]["line"]}–{rows[-1]["end_line"]}' if sid else 'Gruppo di risorse'
            group = add_node(db, sid=sid, parent=parent, ord=rows[0]['ord'], title='', label=label,
                             kind='group', origin='gruppo virtuale', start=rows[0]['start'],
                             end=rows[-1]['end'], line=rows[0]['line'], end_line=rows[-1]['end_line'],
                             crumbs=rows[0]['crumbs'], sample=sample[:720])
            db.execute(f'UPDATE nodes SET parent=? WHERE id IN ({slots})', [group, *part])
            new.append(group)
            if progress and len(new) % 10000 == 0:
                progress(f'Raggruppamento: {len(new)} gruppi nel livello corrente')
        ids = new


def index_source(db, sid, path, cache_path, relative_cache, config, warnings, source_hash, progress=None):
    headings, size, final_line, boilerplate = normalize(path, cache_path, warnings)
    if sha_file(path) != source_hash:
        raise AtlasError('Fonte modificata durante la preparazione; ripetere.')
    source = db.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
    old_nodes = [r[0] for r in db.execute('SELECT id FROM nodes WHERE sid=? AND kind="passage"', (sid,))]
    db.executemany('DELETE FROM search WHERE rowid=?', ((i,) for i in old_nodes))
    db.execute('DELETE FROM nodes WHERE sid=?', (sid,))
    folder = directory(db, Path(source['path']).parent.as_posix())
    root = add_node(db, sid=sid, parent=folder, kind='document', title=Path(source['path']).name,
                    label=Path(source['path']).name, start=0, end=size, end_line=final_line)
    stack = []
    section_nodes = []
    records = []
    for h in headings:
        while stack and stack[-1]['level'] >= h['level']:
            previous = stack.pop()
            db.execute('UPDATE nodes SET end=?,end_line=? WHERE id=?', (h['start'], h['line'] - 1, previous['id']))
        crumbs = [s['title'] for s in stack] + [h['title']]
        node = add_node(db, sid=sid, parent=stack[-1]['id'] if stack else root,
                        ord=h['start'], kind='heading', title=h['title'], label=h['title'], level=h['level'],
                        origin=h['origin'], start=h['start'], end=size, line=h['line'], end_line=final_line,
                        crumbs=json_text(crumbs), loc=h['loc'])
        stack.append({**h, 'id': node})
        section_nodes.append(node)
        records.append({**h, 'id': node, 'crumbs': crumbs})
    spans = []
    if not records or records[0]['start'] > 0:
        spans.append({'id': root, 'start': 0, 'line': 1, 'title': '', 'crumbs': [], 'loc': '',
                      'end': records[0]['start'] if records else size})
    for i, h in enumerate(records):
        spans.append({**h, 'end': records[i + 1]['start'] if i + 1 < len(records) else size})
    total_words = 0
    passage_count = 0
    with cache_path.open('rb') as stream:
        for span in spans:
            line = span['line']
            for start, end, text in chunks(stream, span['start'], span['end'], config['words']):
                nlines = text.count('\n')
                endline = line + nlines - int(text.endswith('\n'))
                sample = label_sample(text[:720], boilerplate)
                label = keywords(sample, 3) or 'Testo'
                node = add_node(db, sid=sid, parent=span['id'], ord=start, kind='passage',
                                label=label, start=start, end=end, line=line, end_line=max(line, endline),
                                crumbs=json_text(span['crumbs']), loc=span['loc'],
                                checksum=digest(text.encode('utf-8')), sample=sample)
                title = source['path'] + ' ' + ' '.join(span['crumbs'])
                db.execute('INSERT INTO search(rowid,title,body) VALUES(?,?,?)',
                           (node, search_body(title), search_body(text)))
                line += nlines
                total_words += len(text.split())
                passage_count += 1
                if progress and passage_count % 20000 == 0:
                    progress(f'{source["path"]}: {passage_count} passaggi')
    for node in reversed(section_nodes):
        group_children(db, node, config['fanout'], sid, cache_path, boilerplate, progress)
        row = db.execute('SELECT sample FROM nodes WHERE parent=? ORDER BY ord,id LIMIT 1', (node,)).fetchone()
        if row:
            db.execute('UPDATE nodes SET sample=? WHERE id=?', (row[0][:720], node))
    if progress and passage_count >= 20000:
        progress(f'{source["path"]}: costruzione dei raggruppamenti')
    group_children(db, root, config['fanout'], sid, cache_path, boilerplate, progress)
    status = 'ok' if size else 'vuoto'
    db.execute('UPDATE sources SET root_node=?,cache=?,words=?,status=?,warnings=? WHERE id=?',
               (root, relative_cache, total_words, status, json_text(warnings), sid))
    return passage_count


def rebuild_directories(db, fanout):
    # Remove presentation-only directory groups; source subtrees retain their IDs.
    db.execute('DELETE FROM nodes WHERE sid IS NULL AND kind="group"')
    for row in db.execute('SELECT path,node FROM dirs ORDER BY length(path) DESC').fetchall():
        if row['path'] != '.':
            parent = db.execute('SELECT node FROM dirs WHERE path=?', (Path(row['path']).parent.as_posix(),)).fetchone()
            db.execute('UPDATE nodes SET parent=? WHERE id=?', (parent[0] if parent else None, row['node']))
    for source in db.execute('SELECT path,root_node FROM sources WHERE root_node IS NOT NULL').fetchall():
        folder = directory(db, Path(source['path']).parent.as_posix())
        db.execute('UPDATE nodes SET parent=? WHERE id=?', (folder, source['root_node']))
    # Prune empty directory nodes only (never the user's directories).
    for row in db.execute('SELECT path,node FROM dirs ORDER BY length(path) DESC').fetchall():
        if row['path'] != '.' and not db.execute('SELECT 1 FROM nodes WHERE parent=? LIMIT 1', (row['node'],)).fetchone():
            db.execute('DELETE FROM nodes WHERE id=?', (row['node'],))
            db.execute('DELETE FROM dirs WHERE path=?', (row['path'],))
    for row in db.execute('SELECT node FROM dirs ORDER BY length(path) DESC').fetchall():
        group_children(db, row[0], fanout)


def _build(root: Path, output=None, profile=DEFAULT_PROFILE, words=None, fanout=None,
          excludes=(), verify=False, max_mib=2048, progress=None, force=False):
    root = root.resolve()
    if not root.is_dir():
        raise AtlasError('Cartella delle fonti assente: ' + str(root))
    output = (Path(output) if output else root / '_atlas').resolve()
    if output == root or root.is_relative_to(output):
        raise AtlasError('La cartella di output non può coincidere con le fonti né contenerle.')
    if output.exists() and not (output / 'atlas.sqlite').exists() and any(p.name not in {'.writer.lock', '.operation.lock'} for p in output.iterdir()):
        raise AtlasError('Output non vuoto e non riconosciuto come indice Atlas.')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'testi').mkdir(exist_ok=True)
    config = dict(PROFILES[profile])
    if words is not None:
        config['words'] = words
    if fanout is not None:
        config['fanout'] = fanout
    if not 40 <= config['words'] <= 2000 or not 3 <= config['fanout'] <= 64:
        raise AtlasError('Configurazione fuori intervallo: parole 40–2000; ramificazione 3–64.')
    patterns = DEFAULT_EXCLUDES + list(excludes)
    ignore_file = root / '.atlasignore'
    if ignore_file.is_file():
        patterns += [s.strip() for s in ignore_file.read_text(encoding='utf-8-sig').splitlines()
                     if s.strip() and not s.lstrip().startswith('#')]
    started = time.perf_counter()
    db = connect(output, write=True)
    counters = {'nuovi': 0, 'aggiornati': 0, 'invariati': 0, 'rimossi': 0, 'errori': 0}
    try:
        if meta(db, 'schema', SCHEMA) != SCHEMA:
            raise AtlasError('Schema indice incompatibile con questo eseguibile.')
        old_root = meta(db, 'root')
        if old_root is not None and (output / old_root).resolve() != root:
            raise AtlasError('Questo indice appartiene a un’altra cartella. Usare un output nuovo.')
        changed_config = force or meta(db, 'config') != json_text(config) or meta(db, 'version') != VERSION
        db.execute('BEGIN IMMEDIATE')
        if not meta(db, 'identity'):
            set_meta(db, 'identity', uuid.uuid4().hex)
        directory(db, '.')
        # Bindings written before quotes existed are completed while the revision
        # they point at is still indexed; afterwards nothing could recover the text.
        from .memory import capture_quotes
        capture_quotes(db, output)
        seen = set()
        for path, relative in inventory(root, output, patterns):
            seen.add(relative)
            stat = path.stat()
            old = db.execute('SELECT * FROM sources WHERE path=?', (relative,)).fetchone()
            if old and old['status'] == 'ok' and not changed_config and old['size'] == stat.st_size and old['mtime'] == stat.st_mtime_ns and not verify:
                counters['invariati'] += 1
                continue
            if progress:
                progress(relative)
            sha = sha_file(path)
            if old and old['status'] == 'ok' and not changed_config and old['sha'] == sha:
                db.execute('UPDATE sources SET size=?,mtime=? WHERE id=?', (stat.st_size, stat.st_mtime_ns, old['id']))
                counters['invariati'] += 1
                continue
            if old:
                sid = old['id']
                counters['aggiornati'] += 1
            else:
                sid = db.execute('INSERT INTO sources(path,status,warnings) VALUES(?,"nuovo","[]")', (relative,)).lastrowid
                counters['nuovi'] += 1
            db.execute('UPDATE sources SET sha=?,size=?,mtime=?,kind=? WHERE id=?',
                       (sha, stat.st_size, stat.st_mtime_ns, path.suffix.lower(), sid))
            cache = f'testi/d{sid}-{sha[:16]}-{time.time_ns():x}.txt'
            cache_path = output / cache
            warnings = []
            db.execute('SAVEPOINT source_build')
            try:
                if stat.st_size > max_mib * 1024 * 1024:
                    raise AtlasError(f'File oltre il limite di {max_mib} MiB; usare --max-mib per ampliarlo.')
                index_source(db, sid, path, cache_path, cache, config, warnings, sha, progress)
                db.execute('RELEASE source_build')
            except Exception as exc:
                if isinstance(exc, sqlite3.Error) or (isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}):
                    raise
                db.execute('ROLLBACK TO source_build')
                db.execute('RELEASE source_build')
                # Old extracted content must not remain searchable after a failed refresh.
                db.execute('DELETE FROM search WHERE rowid IN (SELECT id FROM nodes WHERE sid=? AND kind="passage")', (sid,))
                db.execute('DELETE FROM nodes WHERE sid=?', (sid,))
                folder = directory(db, Path(relative).parent.as_posix())
                node = add_node(db, sid=sid, parent=folder, kind='unavailable', title=Path(relative).name,
                                label=Path(relative).name + ' [non estratto]')
                warnings.append(f'{type(exc).__name__}: {clean(str(exc), 300)}')
                db.execute('UPDATE sources SET status="non_estratto",warnings=?,root_node=?,cache=NULL,words=0 WHERE id=?',
                           (json_text(warnings), node, sid))
                counters['errori'] += 1
        for source in db.execute('SELECT id,path FROM sources').fetchall():
            if source['path'] not in seen:
                sid = source['id']
                db.execute('DELETE FROM search WHERE rowid IN (SELECT id FROM nodes WHERE sid=? AND kind="passage")', (sid,))
                db.execute('DELETE FROM nodes WHERE sid=?', (sid,))
                db.execute('DELETE FROM sources WHERE id=?', (sid,))
                counters['rimossi'] += 1
        rebuild_directories(db, config['fanout'])
        set_meta(db, 'schema', SCHEMA)
        set_meta(db, 'version', VERSION)
        try:
            source_location = os.path.relpath(root, output).replace('\\', '/')
        except ValueError:
            source_location = str(root)
        set_meta(db, 'root', source_location)
        set_meta(db, 'config', json_text(config))
        set_meta(db, 'profile', profile)
        set_meta(db, 'built', time.strftime('%Y-%m-%dT%H:%M:%S%z'))
        set_meta(db, 'edition', time.time_ns())
        set_meta(db, 'exclusions', json_text(sorted(IGNORED) + patterns))
        from .events import append
        append(db, 'index_built', {'edition': meta(db, 'edition'), **counters})
        db.commit()
        counts = db.execute('SELECT count(*) n,coalesce(sum(words),0) words FROM sources').fetchone()
        counters.update(fonti=counts['n'], parole=counts['words'],
                        passaggi=db.execute('SELECT count(*) FROM nodes WHERE kind="passage"').fetchone()[0],
                        secondi=round(time.perf_counter() - started, 3), configurazione=config,
                        indice=str(output))
    finally:
        db.close()
    from .view import export
    export(output)
    return counters


def build(root: Path, output=None, profile=DEFAULT_PROFILE, words=None, fanout=None,
          excludes=(), verify=False, max_mib=2048, progress=None, force=False):
    """One writer through database commit and publication of the visual preview."""
    root = root.resolve()
    target = (Path(output) if output else root / '_atlas').resolve()
    if not root.is_dir() or target == root or root.is_relative_to(target):
        raise AtlasError('Cartella assente o output coincidente/contenente le fonti.')
    if target.exists() and not (target / 'atlas.sqlite').exists() and any(p.name not in {'.writer.lock', '.operation.lock'} for p in target.iterdir()):
        raise AtlasError('Output non vuoto e non riconosciuto come indice Atlas.')
    target.mkdir(parents=True, exist_ok=True)
    with (target / '.writer.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AtlasError('Preparazione già in corso su questo indice; riprovare alla fine.') from exc
        return _build(root, target, profile, words, fanout, excludes, verify, max_mib, progress, force)
