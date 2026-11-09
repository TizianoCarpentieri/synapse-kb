"""Offline graph snapshots; explicit selection limits, no remote resources."""
from __future__ import annotations

from collections import defaultdict
import hashlib
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import re

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
        rows = [dict(r) for r in db.execute('SELECT src,dst,kind,anchor,cue,score,origin,relation,NULL note_id FROM dependencies UNION ALL '
                                           'SELECT src,dst,kind,anchor,cue,score,"explicit" origin,"" relation,note_id FROM manual_edges ORDER BY score DESC,src,dst')]
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
                    anchor=row['anchor'], score=row['score'], origin=row['origin'],
                    relation=row['relation'], count=1)
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

    def short(text):
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def with_structure(selected):
        """Folder and document hubs for the shown nodes, so the tree a reader knows is visible."""
        hubs, links_ = {}, []
        for node in selected:
            if node['id'].startswith('note-'):
                node['type'] = 'note'
                continue
            node['type'] = 'section' if node['id'].startswith('section-') else 'passage'
            doc, folder = 'doc-' + short(node['path']), 'folder-' + short(node['folder'])
            if doc not in hubs:
                hubs[doc] = dict(id=doc, label=Path(node['path']).name, path=node['path'], line=1, folder=node['folder'],
                                 type='document', command='python atlas.py explore "' + node['path'] + '"')
                if folder not in hubs:
                    target = 'root' if node['folder'] == 'Root' else '"' + node['folder'] + '"'
                    hubs[folder] = dict(id=folder, label=node['folder'], path=node['folder'], line=1, folder=node['folder'],
                                        type='folder', command='python atlas.py explore ' + target)
                links_.append((folder, doc))
            links_.append((doc, node['id']))
        edges_ = [dict(source=a, target=b, kind='struttura', cue='', anchor='', score=0, origin='structure',
                       relation='contains', count=1) for a, b in links_]
        return list(hubs.values()), edges_

    def bounded(nodes, links, edge_limit, node_limit):
        chosen = links[:edge_limit]
        active = {e['source'] for e in chosen} | {e['target'] for e in chosen}
        # Linked nodes first; the rest fill the documented display bound.
        selected = [dict(n) for n in nodes if n['id'] in active]
        selected += [dict(n) for n in nodes if n['id'] not in active][:max(0, node_limit - len(selected))]
        hubs, structure = with_structure(selected)
        return {'nodes': hubs + selected, 'edges': chosen + structure, 'total_nodes': len(nodes), 'total_edges': len(links)}

    payload = dict(schema='synapse-kb-graph-1', edition=edition,
                   macro=bounded(list(sections.values()) + list(note_nodes.values()), list(macro.values()), 320, 650),
                   micro=bounded(list(passages.values()) + list(note_nodes.values()), edges, 450, 950),
                   notice='Snapshot and selected candidates, not verified dependencies. Run graph after changes. '
                          'Scores are not probabilities. Folder and document links show where a text is stored, nothing more.')
    out = index / 'visuals'
    out.mkdir(exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    (out / 'graph.json').write_text(encoded + '\n', encoding='utf-8')
    template = files('atlas_core').joinpath('viewer.html').read_text(encoding='utf-8')
    # Prevent document text from terminating the script element.
    safe_json = encoded.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (out / 'graph.html').write_text(template.replace('/*__ATLAS_DATA__*/', safe_json), encoding='utf-8')
    result = export_vault(out / 'obsidian', payload)
    return result


PALETTE = ['#60a5fa', '#fbbf24', '#f472b6', '#34d399', '#c084fc', '#fb923c', '#22d3ee', '#a3e635', '#f87171', '#94a3b8']


def _file_name(text, limit=80):
    """A name Obsidian and Windows both accept; the readable title stays recognisable."""
    text = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip(' .')[:limit].strip(' .') or 'untitled'


def _tag(parts):
    cleaned = [re.sub(r'[^\w-]+', '-', part).strip('-') for part in parts]
    return '/'.join(part for part in cleaned if part)


def export_vault(vault, payload):
    """Obsidian vault mirroring the source folders: readable names, folder tags, document hubs."""
    vault.mkdir(exist_ok=True)
    manifest = vault / '.generated.json'
    previous = set(json.loads(manifest.read_text(encoding='utf-8'))) if manifest.exists() else set()
    data = payload['macro']
    nodes = [n for n in data['nodes'] if n['type'] != 'folder']
    relations = [e for e in data['edges'] if e['kind'] != 'struttura']
    names, taken = {}, set()
    for node in nodes:
        source = PurePosixPath(node['path'])
        folder = [_file_name(part) for part in source.parent.parts]
        if node['type'] == 'note':
            folder, name = ['Notes'], _file_name(f"Note {node['id'][5:]} — {node['label']}")
        elif node['type'] == 'document':
            # Not the bare file name: a note called report.md.md breaks wikilinks.
            name = _file_name(source.stem + (f' ({source.suffix[1:]})' if source.suffix else ''))
        else:
            title = node['label'] if node['label'] != node['path'] else 'text'
            name = _file_name(f'{source.stem} — {title}')
        relative = '/'.join(folder + [name])
        # Never take a name already used here, or one a person created in the vault.
        if relative.casefold() in taken or ((vault / (relative + '.md')).exists() and relative + '.md' not in previous):
            relative += ' ' + node['id'][-6:]
        taken.add(relative.casefold())
        names[node['id']] = relative
    link = lambda ident: f"[[{names[ident]}|{by_id[ident]['label']}]]"
    by_id = {n['id']: n for n in nodes}
    outgoing, incoming, children, parent = defaultdict(list), defaultdict(list), defaultdict(list), {}
    for edge in relations:
        outgoing[edge['source']].append(edge)
        incoming[edge['target']].append(edge)
    for edge in data['edges']:
        if edge['kind'] == 'struttura' and edge['source'] in by_id and edge['target'] in by_id:
            children[edge['source']].append(edge['target'])
            parent[edge['target']] = edge['source']
    generated = []
    for node in nodes:
        tag = 'Notes' if node['type'] == 'note' else _tag(PurePosixPath(node['path']).parent.parts)
        lines = ['---', 'source: ' + json.dumps(node['path'], ensure_ascii=False),
                 'atlas_id: ' + json.dumps(node['id']), 'kind: ' + node['type'],
                 'tags: ' + json.dumps([tag] if tag else [], ensure_ascii=False),
                 'aliases: ' + json.dumps([node['label']], ensure_ascii=False), '---', '# ' + node['label'], '',
                 '`' + node['command'] + '` (run from the workspace)', '']
        if node['id'] in parent:
            lines += ['Document: ' + link(parent[node['id']]), '']
        if node['type'] == 'document':
            lines += ['## Sections', ''] + ['- ' + link(child) for child in children[node['id']]] + ['']
        else:
            lines += ['Candidate links. Read the source before applying a rule.', '', '## Outgoing', '']
            lines += [f"- {link(e['target'])} — {e['kind']} / {e['origin']}: {e['cue']}" for e in outgoing[node['id']]]
            lines += ['', '## Incoming', '']
            lines += [f"- {link(e['source'])} — {e['kind']} / {e['origin']}" for e in incoming[node['id']]]
        target = safe_child(vault, names[node['id']] + '.md')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        generated.append(names[node['id']] + '.md')
    root = vault.resolve()
    for name in previous - set(generated):
        target = safe_child(vault, name)
        if target.suffix == '.md' and target.is_file():
            target.unlink()
            folder = target.parent
            while folder != root and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
    folders = sorted({n['folder'] for n in nodes})
    index = ['# Synapse KB graph snapshot', '', payload['notice'], '',
             f"Showing {len([n for n in nodes if n['type'] in ('section', 'note')])}/{data['total_nodes']} sections and notes, "
             f"{len(relations)}/{data['total_edges']} relationships.", '']
    for folder in folders:
        index += ['## ' + folder, ''] + ['- ' + link(n['id']) for n in nodes if n['folder'] == folder and n['type'] in ('document', 'note')] + ['']
    (vault / '00_INDEX.md').write_text('\n'.join(index), encoding='utf-8')
    # Colour Obsidian's own graph by source folder, unless the person already configured it.
    settings = vault / '.obsidian' / 'graph.json'
    if not settings.exists():
        settings.parent.mkdir(exist_ok=True)
        groups = [{'query': 'path:"' + (_file_name(folder) if folder != 'Notes' else 'Notes') + '"',
                   'color': {'a': 1, 'rgb': int(PALETTE[i % len(PALETTE)][1:], 16)}}
                  for i, folder in enumerate(f for f in folders if f != 'Root')]
        settings.write_text(json.dumps({'colorGroups': groups, 'showTags': False}, indent=2), encoding='utf-8')
    manifest.write_text(json.dumps(generated), encoding='utf-8')
    return {'html': str(vault.parent / 'graph.html'), 'obsidian': str(vault),
            'macro_edges_shown': len(relations), 'macro_edges_total': payload['macro']['total_edges'],
            'micro_edges_shown': sum(e['kind'] != 'struttura' for e in payload['micro']['edges']),
            'micro_edges_total': payload['micro']['total_edges']}
