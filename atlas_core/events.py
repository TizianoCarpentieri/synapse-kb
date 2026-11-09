"""Append-only, hash-chained journal of index and memory mutations.

The chain makes accidental or naive edits detectable and gives every consumer
(CLI, MCP subscriptions, another workspace) one ordered cursor. It is not a
signature: whoever can write the database can rewrite the whole chain.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from .index import connect


def _digest(prev, ts, actor, kind, payload):
    body = json.dumps([prev, ts, actor, kind, payload], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def append(db, kind, payload, actor='engine'):
    """Add one event inside the caller's transaction; it commits or rolls back with it."""
    row = db.execute('SELECT hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
    prev = row[0] if row else ''
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    cur = db.execute('INSERT INTO events(ts,actor,kind,payload,prev,hash) VALUES(?,?,?,?,?,?)',
                     (ts, actor, kind, text, prev, _digest(prev, ts, actor, kind, text)))
    return cur.lastrowid


def _has_table(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone())


def head(db):
    """(last sequence number, last hash); (0, '') for an empty or pre-journal index."""
    if not _has_table(db):
        return 0, ''
    row = db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
    return (row[0], row[1]) if row else (0, '')


def read(index, since=0, limit=200):
    with connect(index) as db:
        if not _has_table(db):
            return {'events': [], 'last_seq': 0, 'more': False}
        rows = db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?', (since, limit + 1)).fetchall()
        last = head(db)[0]
    items = [dict(r) | {'payload': json.loads(r['payload'])} for r in rows[:limit]]
    return {'events': items, 'last_seq': last, 'more': len(rows) > limit}


def verify(index):
    """Recompute the whole chain. Reports the first broken sequence number, if any."""
    with connect(index) as db:
        if not _has_table(db):
            return {'events': 0, 'intact': True, 'broken_at': None}
        prev, count = '', 0
        for row in db.execute('SELECT * FROM events ORDER BY seq'):
            if row['prev'] != prev or row['hash'] != _digest(prev, row['ts'], row['actor'], row['kind'], row['payload']):
                return {'events': count, 'intact': False, 'broken_at': row['seq']}
            prev, count = row['hash'], count + 1
    return {'events': count, 'intact': True, 'broken_at': None}
