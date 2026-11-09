"""Offline graph snapshots; explicit selection limits, no remote resources."""
from __future__ import annotations

from collections import defaultdict
import hashlib
from importlib.resources import files
import json
from pathlib import Path

from .common import AtlasError, clean, safe_child
from .index import connect, meta, reference
from .memory import eligible, exists
from .view import check_source


def export_graph(index: Path):
    with connect(index) as db:
        if not exists(db) or meta(db, 'graph_edition') != meta(db, 'edition'):
            raise AtlasError('Graph missing or stale. Run update or graph --rebuild.')
        sources = {s['id']: dict(s) for s in db.execute('SELECT * FROM sources')}
        for sid, source in sources.items():
            if source['status'] in ('ok', 'vuoto'):
                check_source(db, index, sid, verify=True)
        passages, sections = {}, {}
        for row in db.execute('SELECT * FROM nodes WHERE kind="passage" ORDER BY id'):
            src = sources[row['sid']]
            crumbs = json.loads(row['crumbs'])
            section_key = src['path'] + '::' + (crumbs[0] if crumbs else Path(src['path']).stem)
            section_id = 'section-' + hashlib.sha256(section_key.encode()).hexdigest()[:16]
            folder = Path(src['path']).parts[0] if len(Path(src['path']).parts) > 1 else 'Root'
            item = dict(id=reference(db, row['id']), label=' > '.join(crumbs) or row['label'],
                        path=src['path'], line=row['line'], folder=folder, section=section_id,
                        command='python atlas.py read ' + reference(db, row['id']) + ' --vicini 1')
            passages[row['id']] = item
            sections.setdefault(section_id, dict(item, id=section_id, label=crumbs[0] if crumbs else src['path']))
        rows = [dict(r) for r in db.execute('SELECT *,"heuristic" origin,NULL note_id FROM dependencies UNION ALL '
                                           'SELECT src,dst,kind,anchor,cue,score,"explicit" origin,note_id FROM manual_edges ORDER BY score DESC,src,dst')]
        active_notes = {r['id']: dict(r) for r in db.execute('SELECT * FROM shared_notes ORDER BY id') if eligible(r)}
        rows = [r for r in rows if not r['note_id'] or r['note_id'] in active_notes]
        links = [dict(r) for r in db.execute('SELECT * FROM note_links ORDER BY note_id,node_id') if r['note_id'] in active_notes]
        edition = meta(db, 'edition')
    edges = []
    macro = {}
    for row in rows:
        src, dst = passages.get(row['src']), passages.get(row['dst'])
        if not src or not dst:
            continue
        edge = dict(source=src['id'], target=dst['id'], kind=row['kind'], cue=row['cue'],
                    anchor=row['anchor'], score=row['score'], origin=row['origin'], count=1)
        edges.append(edge)
        if src['section'] != dst['section']:
            key = (src['section'], dst['section'], row['kind'])
            if key not in macro:
                macro[key] = dict(edge, source=src['section'], target=dst['section'])
            else:
                macro[key]['count'] += 1
    note_nodes = {}
    for note in active_notes.values():
        nid = 'note-' + str(note['id'])
        note_nodes[nid] = dict(id=nid, label=note['title'], path='appunti_condivisi.md', line=1,
                               folder='Notes', command='python atlas.py notes ' + str(note['id']),
                               status=note['status'], author=note['author'])
    for row in links:
        src = passages.get(row['node_id'])
        if not src:
            continue
        note = active_notes[row['note_id']]
        edge = dict(source=src['id'], target='note-' + str(note['id']), kind='appunto_condiviso',
                    cue=clean(note['body'], 220), anchor=row['anchor'], score=row['score'],
                    origin=note['status'] + ' / ' + row['kind'], count=1)
        edges.insert(0, edge)
        key = (src['section'], edge['target'], edge['kind'])
        macro.setdefault(key, dict(edge, source=src['section']))

    def bounded(nodes, links, edge_limit, node_limit):
        chosen = links[:edge_limit]
        active = {e['source'] for e in chosen} | {e['target'] for e in chosen}
        # Keep isolated nodes visible up to the documented display bound.
        selected = [n for n in nodes if n['id'] in active]
        selected += [n for n in nodes if n['id'] not in active][:max(0, node_limit - len(selected))]
        return {'nodes': selected, 'edges': chosen, 'total_nodes': len(nodes), 'total_edges': len(links)}

    payload = dict(schema='synapse-kb-graph-1', edition=edition,
                   macro=bounded(list(sections.values()) + list(note_nodes.values()), list(macro.values()), 320, 650),
                   micro=bounded(list(passages.values()) + list(note_nodes.values()), edges, 450, 950),
                   notice='Snapshot and selected candidates, not verified dependencies. Run graph after changes. Scores are not probabilities.')
    out = index / 'visuals'
    out.mkdir(exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    (out / 'graph.json').write_text(encoded + '\n', encoding='utf-8')
    template = files('atlas_core').joinpath('viewer.html').read_text(encoding='utf-8')
    # Prevent document text from terminating the script element.
    safe_json = encoded.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (out / 'graph.html').write_text(template.replace('/*__ATLAS_DATA__*/', safe_json), encoding='utf-8')
    vault = out / 'obsidian'
    vault.mkdir(exist_ok=True)
    manifest = vault / '.generated.json'
    previous = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    generated = []
    data = payload['macro']
    node_map = {n['id']: n for n in data['nodes']}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in data['edges']:
        outgoing[edge['source']].append(edge)
        incoming[edge['target']].append(edge)
    for node in data['nodes']:
        name = node['id'] + '.md'
        lines = ['---', 'source: ' + json.dumps(node['path'], ensure_ascii=False),
                 'atlas_id: ' + json.dumps(node['id']), '---', '# ' + node['label'], '',
                 '`' + node['command'] + '` (run from the workspace)', '',
                 'Candidate links. Read the source before applying a rule.', '', '## Outgoing', '']
        for edge in outgoing[node['id']]:
            lines.append(f"- [[{edge['target']}]] — {edge['kind']} / {edge['origin']}: {edge['cue']}")
        lines += ['', '## Incoming', '']
        for edge in incoming[node['id']]:
            lines.append(f"- [[{edge['source']}]] — {edge['kind']} / {edge['origin']}")
        (vault / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        generated.append(name)
    for name in set(previous) - set(generated):
        target = safe_child(vault, name)
        if target.parent == vault.resolve() and target.name.startswith(('section-', 'note-')) and target.suffix == '.md':
            target.unlink(missing_ok=True)
    (vault / '00_INDEX.md').write_text('# Synapse KB graph snapshot\n\n' + payload['notice'] + '\n\n' +
        f"Showing {len(data['nodes'])}/{data['total_nodes']} nodes and {len(data['edges'])}/{data['total_edges']} edges.\n\n" +
        '\n'.join(f"- [[{n['id']}]] — {n['label']}" for n in data['nodes']) + '\n', encoding='utf-8')
    manifest.write_text(json.dumps(generated), encoding='utf-8')
    return {'html': str(out / 'graph.html'), 'obsidian': str(vault),
            'macro_edges_shown': len(payload['macro']['edges']), 'macro_edges_total': payload['macro']['total_edges'],
            'micro_edges_shown': len(payload['micro']['edges']), 'micro_edges_total': payload['micro']['total_edges']}
