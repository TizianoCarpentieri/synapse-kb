"""Re-anchoring of note bindings after a source changed.

A binding stores the bound passage text (the quote). After a new revision the
engine looks for that text again. Three outcomes are distinguished on purpose:

exact      same source revision, same bytes, same coordinates.
relocated  the identical text (whitespace aside) occurs exactly once in the
           new revision of the same file, under the same heading path. Only
           this is automatic: it is an equality check, not an estimate.
drifted    the best remaining candidate: changed text, another file or another
           heading path. It is a proposal a person accepts or ignores.
stale      nothing defensible was found, or several places fit equally well.

Why no similarity threshold auto-accepts: `scripts/benchmark.py` shows that an
edit reversing a rule (a number, a negation, a modal verb) leaves the passage
more similar to the original than harmless rewording does. Similarity ranks
proposals and suppresses noise; it cannot certify that a note still applies.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import json
import re

from .common import STOP, safe_child

# Below this share of the old words found again in order, no proposal is made.
PROPOSAL_FLOOR = 0.55
# The best candidate must beat every other section by this much.
PROPOSAL_MARGIN = 0.10
MIN_QUOTE_CHARS = 24
MAX_REFLOW_BYTES = 8 * 1024 * 1024

_NUMBER_WORDS = set('''zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen
fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand
uno due tre quattro cinque sei sette otto nove dieci undici dodici tredici quattordici quindici sedici diciassette
diciotto diciannove venti trenta quaranta cinquanta sessanta settanta ottanta novanta cento mille
first second third primo secondo terzo half double meta doppio'''.split())
_NEGATIONS = set('not no never neither nor without none cannot non mai senza nessun nessuno nessuna ne nemmeno neanche'.split())
_MODALS = set('''must shall should may might can could will required mandatory optional forbidden prohibited allowed
permitted eligible ineligible deve devono dovra dovranno puo possono potra obbligatorio obbligatoria facoltativo
facoltativa vietato vietata consentito consentita ammesso ammessa'''.split())
_CONDITIONS = set('''if unless except excluding only provided until before after se salvo eccetto tranne solo soltanto
purche finche prima dopo qualora'''.split())


def tokens(text):
    return re.findall(r'[^\W_]+', text.casefold())


def recall(old, new):
    """Share of the old word sequence found again, in order, in the new text."""
    if not old:
        return 0.0, []
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    blocks = matcher.get_matching_blocks()
    return sum(b.size for b in blocks) / len(old), matcher.get_opcodes()


def normative_delta(old, opcodes, new):
    """Kinds of words that changed inside the old span. Text added around it is ignored."""
    changed = set()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal' or (tag == 'insert' and i1 in (0, len(old))):
            continue
        changed.update(old[i1:i2])
        changed.update(new[j1:j2])
    flags = []
    if any(w.isdigit() or w in _NUMBER_WORDS for w in changed):
        flags.append('number')
    if changed & _NEGATIONS:
        flags.append('negation')
    if changed & _MODALS:
        flags.append('modality')
    if changed & _CONDITIONS:
        flags.append('condition')
    return flags


def exact(db, target):
    """The identical source revision and passage, never a guessed replacement."""
    rows = db.execute('SELECT n.id FROM nodes n JOIN sources s ON s.id=n.sid '
                      'WHERE s.path=? AND s.sha=? AND n.start=? AND n.end=? '
                      'AND n.checksum=? AND n.kind="passage"',
                      (target['path'], target['sha256'], target['start'], target['end'],
                       target['checksum'])).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _passage_at(db, sid, start, end):
    """Passage holding most of the byte range [start,end) of one normalized text."""
    best, size = None, 0
    for row in db.execute('SELECT id,start,end FROM nodes WHERE sid=? AND kind="passage" AND start<? AND end>? ORDER BY start',
                          (sid, end, start)):
        overlap = min(end, row['end']) - max(start, row['start'])
        if overlap > size:
            best, size = row['id'], overlap
    return best


def _occurrences(db, index, source, needle, limit=4):
    data = safe_child(index, source['cache']).read_bytes()
    found, position = [], data.find(needle)
    while position >= 0 and len(found) < limit:
        node = _passage_at(db, source['id'], position, position + len(needle))
        if node is not None:
            found.append(node)
        position = data.find(needle, position + 1)
    return list(dict.fromkeys(found))


def _reflowed(db, index, source, quote, limit=4):
    """Same characters in the same order, only whitespace differs (re-wrapped lines)."""
    path = safe_child(index, source['cache'])
    if path.stat().st_size > MAX_REFLOW_BYTES:
        return []
    text = path.read_text(encoding='utf-8', errors='replace')
    compact, starts = [], []
    for position, char in enumerate(text):
        if not char.isspace():
            compact.append(char)
            starts.append(position)
    haystack, needle = ''.join(compact), ''.join(quote.split())
    found, position = [], haystack.find(needle)
    while position >= 0 and needle and len(found) < limit:
        a, b = starts[position], starts[position + len(needle) - 1] + 1
        start = len(text[:a].encode('utf-8'))
        node = _passage_at(db, source['id'], start, start + len(text[a:b].encode('utf-8')))
        if node is not None:
            found.append(node)
        position = haystack.find(needle, position + 1)
    return list(dict.fromkeys(found))


def _text(db, index, node_id, neighbours=0):
    from .view import read_bytes
    node = db.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
    source = db.execute('SELECT * FROM sources WHERE id=?', (node['sid'],)).fetchone()
    start, end = node['start'], node['end']
    if neighbours:
        # Passages never cross a heading, so text re-split by a different chunking
        # can only continue in a neighbour under the same heading path.
        before = db.execute('SELECT start,crumbs FROM nodes WHERE sid=? AND kind="passage" AND start<? ORDER BY start DESC LIMIT ?',
                            (node['sid'], node['start'], neighbours)).fetchall()
        after = db.execute('SELECT end,crumbs FROM nodes WHERE sid=? AND kind="passage" AND start>=? ORDER BY start LIMIT ?',
                           (node['sid'], node['end'], neighbours)).fetchall()
        for row in before:
            if row['crumbs'] != node['crumbs']:
                break
            start = row['start']
        for row in after:
            if row['crumbs'] != node['crumbs']:
                break
            end = row['end']
    return read_bytes(db, index, source, start, end).decode('utf-8', errors='replace')


def _describe(db, node_id):
    row = db.execute('SELECT n.crumbs,s.path FROM nodes n JOIN sources s ON s.id=n.sid WHERE n.id=?', (node_id,)).fetchone()
    return row['path'], json.loads(row['crumbs'])


def _candidates(db, quote, sid=None, limit=12):
    words = [w for w in dict.fromkeys(tokens(quote)) if len(w) >= 4 and w not in STOP][:24]
    if not words:
        return []
    expression = 'body: (' + ' OR '.join('"' + w + '"' for w in words) + ')'
    if sid is None:
        rows = db.execute('SELECT rowid FROM search WHERE search MATCH ? ORDER BY bm25(search) LIMIT ?', (expression, limit))
    else:
        rows = db.execute('SELECT search.rowid FROM search JOIN nodes n ON n.id=search.rowid WHERE search MATCH ? '
                          'AND n.sid=? ORDER BY bm25(search) LIMIT ?', (expression, sid, limit))
    return [r[0] for r in rows]


def resolve(db, index, binding):
    """Where a stored binding points in the current index, and how sure that is."""
    node = exact(db, binding)
    if node is not None:
        return {'state': 'exact', 'node': node, 'similarity': 1.0, 'flags': [], 'reason': 'same source revision'}
    quote = (binding.get('quote') or '').strip()
    if len(quote) < MIN_QUOTE_CHARS:
        return {'state': 'stale', 'node': None, 'similarity': 0.0, 'flags': [],
                'reason': 'no stored quote to search for' if not quote else 'quote too short to locate safely'}
    source = db.execute('SELECT * FROM sources WHERE path=? AND status="ok"', (binding['path'],)).fetchone()
    crumbs = binding.get('crumbs')
    needle = quote.encode('utf-8')
    hits = _occurrences(db, index, source, needle) if source else []
    how = 'identical text'
    if source and not hits:
        hits = _reflowed(db, index, source, quote)
        how = 'identical characters, whitespace re-wrapped'
    if len(hits) > 1 and crumbs is not None:
        same = [n for n in hits if _describe(db, n)[1] == crumbs]
        hits = same if len(same) == 1 else hits
    if len(hits) > 1:
        return {'state': 'stale', 'node': None, 'similarity': 1.0, 'flags': ['ambiguous'],
                'reason': f'identical text occurs in {len(hits)} places of the new revision'}
    if len(hits) == 1:
        path, now = _describe(db, hits[0])
        if crumbs is None or now == crumbs:
            return {'state': 'relocated', 'node': hits[0], 'similarity': 1.0, 'flags': [],
                    'reason': how + ', same file and heading path, new revision'}
        return {'state': 'drifted', 'node': hits[0], 'similarity': 1.0, 'flags': ['context'],
                'reason': 'identical text under a different heading path: ' + ' > '.join(now)}
    # Identical passage in another file: content-addressed, but a path can carry
    # authority (an archive folder, a draft), so it is proposed, never assumed.
    moved = [r[0] for r in db.execute('SELECT id FROM nodes WHERE kind="passage" AND checksum=?', (binding['checksum'],))]
    if len(moved) == 1:
        path, now = _describe(db, moved[0])
        return {'state': 'drifted', 'node': moved[0], 'similarity': 1.0, 'flags': ['path'],
                'reason': 'identical passage now in ' + path}
    old = tokens(quote)
    scored = []
    pool = (_candidates(db, quote, source['id']) if source else []) + _candidates(db, quote)
    for candidate in dict.fromkeys(pool):
        new = tokens(_text(db, index, candidate, 1))
        score, opcodes = recall(old, new)
        own, _ = recall(old, tokens(_text(db, index, candidate)))
        scored.append((round(score, 3), round(own, 3), candidate, opcodes, new))
    if not scored:
        return {'state': 'stale', 'node': None, 'similarity': 0.0, 'flags': [], 'reason': 'no similar text in the index'}
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    score, own, best, opcodes, new = scored[0]
    if score < PROPOSAL_FLOOR:
        return {'state': 'stale', 'node': None, 'similarity': score, 'flags': [],
                'reason': f'best candidate keeps {score:.0%} of the text, below the proposal floor'}
    section = _describe(db, best)
    rival = max((s for s, _, n, _, _ in scored[1:] if _describe(db, n) != section), default=0.0)
    if score - rival < PROPOSAL_MARGIN:
        return {'state': 'stale', 'node': None, 'similarity': score, 'flags': ['ambiguous'],
                'reason': f'two sections fit almost equally ({score:.0%} and {rival:.0%})'}
    flags = normative_delta(old, opcodes, new)
    if section[0] != binding['path']:
        flags.append('path')
    if crumbs is not None and section[1] != crumbs:
        flags.append('context')
    return {'state': 'drifted', 'node': best, 'similarity': score, 'flags': flags,
            'reason': f'changed text; {score:.0%} of the original wording found in ' + section[0]}
