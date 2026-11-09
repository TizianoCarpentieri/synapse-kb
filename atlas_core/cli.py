from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

from .common import AtlasError, DEFAULT_PROFILE, PROFILES, VERSION
from .index import build
from .view import explore, export, find, read, report
from .workspace import doctor, init_workspace, load_workspace, operation_lock
from . import memory

def parser():
    p = argparse.ArgumentParser(description='Synapse KB: local document evidence and agent onboarding (alpha).')
    p.add_argument('--version', action='version', version='Synapse KB ' + VERSION)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('init', aliases=['inizia'], help='Guided workspace setup; external sources stay in place.')
    c.add_argument('--workspace', type=Path, default=Path('workspaces/main'))
    c.add_argument('--source', type=Path)
    c.add_argument('--demo', action='store_true')
    c.add_argument('--empty', action='store_true', help='Create an empty documents folder without interactive prompts.')
    c.add_argument('--rules-file', type=Path)
    c.add_argument('--profile', choices=PROFILES, default=DEFAULT_PROFILE)
    c.add_argument('--exclude', action='append', default=[])
    c.add_argument('--no-build', action='store_true')
    sub.add_parser('doctor', help='Check local Python, SQLite FTS5 and optional PDF support.')
    aliases = {'prepara': ['build'], 'aggiorna': ['update'], 'esplora': ['explore'],
               'cerca': ['search'], 'leggi': ['read'], 'stato': ['status'],
               'esporta': ['export'], 'annota': ['annotate'], 'collega': ['link'],
               'dipendenze': ['dependencies'], 'appunti': ['notes'], 'grafo': ['graph']}
    for name, names in aliases.items():
        c = sub.add_parser(name, aliases=names)
        c.set_defaults(command=name)
        c.add_argument('--workspace', type=Path, help='Configured workspace; defaults to the current directory.')
        c.add_argument('--indice', '--index', type=Path)
        c.add_argument('--json', action='store_true')
        if name in ('prepara', 'aggiorna'):
            if name == 'prepara':
                c.add_argument('cartella', type=Path)
                c.add_argument('--output', type=Path)
            c.add_argument('--profilo', '--profile', choices=PROFILES)
            c.add_argument('--parole', type=int)
            c.add_argument('--rami', type=int)
            c.add_argument('--escludi', action='append', default=[])
            c.add_argument('--verifica', action='store_true')
            c.add_argument('--ricostruisci', action='store_true')
            c.add_argument('--max-mib', type=int, default=2048)
            c.add_argument('--quiet', action='store_true')
        elif name in ('esplora', 'cerca', 'leggi'):
            c.add_argument('--max-chars', type=int, default=12000 if name == 'leggi' else 1600)
            c.add_argument('--offset', type=int, default=0)
            c.add_argument('--no-graph', action='store_true', help='Disable related evidence for controlled A/B tests.')
            c.add_argument('--related-limit', type=int, default=6)
            if name == 'esplora':
                c.add_argument('riferimento', nargs='?', default='root')
                c.add_argument('--richiesta')
            elif name == 'cerca':
                c.add_argument('richiesta')
                c.add_argument('--ambito', default='root')
                c.add_argument('--limit', type=int, default=6)
                c.add_argument('--tutti', action='store_true')
                c.add_argument('--edizione')
            else:
                c.add_argument('riferimenti', nargs='+')
                c.add_argument('--verifica', action='store_true')
                c.add_argument('--vicini', type=int, default=0)
        elif name == 'esporta':
            c.add_argument('--limit', type=int, default=200)
        elif name in ('annota', 'collega'):
            if name == 'annota':
                c.add_argument('testo')
                c.add_argument('--titolo', '--title', default='Observation')
                c.add_argument('--collega', nargs='+', default=[])
                c.add_argument('--verso', nargs='+', default=[])
            else:
                c.add_argument('origine')
                c.add_argument('destinazione')
                c.add_argument('--nota', '--note', required=True)
                c.add_argument('--ancora', default='Explicit link')
            c.add_argument('--author', default='agent')
            c.add_argument('--status', choices=('proposed', 'confirmed'), default='proposed')
            c.add_argument('--tipo', choices=memory.KINDS, default='correzione_agente')
            c.add_argument('--expires', help='Expiration date YYYY-MM-DD, interpreted in UTC.')
        elif name == 'dipendenze':
            c.add_argument('riferimenti', nargs='+')
            c.add_argument('--limit', type=int, default=50)
        elif name == 'appunti':
            c.add_argument('id', nargs='?', type=int)
            c.add_argument('--revoke', type=int)
        elif name == 'grafo':
            c.add_argument('--rebuild', action='store_true')
    return p


def update(source, index, profile=DEFAULT_PROFILE, **kwargs):
    from .graph import build_dependencies
    from .visuals import export_graph
    result = build(source, output=index, profile=profile, **kwargs)
    result['dependencies'] = build_dependencies(index)
    memory.backup(index)
    result['visuals'] = export_graph(index)
    result['notes_to_review'] = sum(bool(n['stale_bindings']) for n in memory.notes(index) if n['active'])
    return result


def get_index(args):
    if args.indice:
        return args.indice.resolve()
    if args.workspace:
        return load_workspace(args.workspace)[0] / '_atlas'
    return Path('_atlas').resolve()


def run(args):
    if args.command == 'doctor':
        return doctor()
    if args.command in ('init', 'inizia'):
        work = init_workspace(args.workspace, args.source, args.demo, args.rules_file, args.profile, args.exclude, args.empty)
        result = {'workspace': str(work), 'next': 'Open this workspace with your tool-capable agent and read AGENTS.md.'}
        # A tiny portable launcher preserves the configured rules workspace and avoids shell quoting.
        launch = Path(sys.argv[0]).resolve()
        if launch.is_file() and launch.suffix in ('.py', '.pyz'):
            import_path = launch if launch.suffix == '.pyz' else launch.parent
            (work / 'atlas.py').write_text('import runpy, sys\nsys.path.insert(0, ' + repr(str(import_path)) + ')\nrunpy.run_path(' + repr(str(launch)) + ", run_name='__main__')\n", encoding='utf-8')
        if not args.no_build:
            work, source, config = load_workspace(work)
            with operation_lock(work / '_atlas'):
                result['build'] = update(source, work / '_atlas', config['profile'], excludes=config['excludes'])
        return result
    if args.command in ('prepara', 'aggiorna'):
        if args.command == 'prepara':
            source = args.cartella.resolve()
            index = (args.output or args.indice or source / '_atlas').resolve()
            config = {}
        else:
            work, source, config = load_workspace(args.workspace or Path.cwd())
            index = args.indice.resolve() if args.indice else work / '_atlas'
        if index == source or source.is_relative_to(index):
            raise AtlasError('The index must not contain or replace the source folder.')
        with operation_lock(index):
            return update(source, index, args.profilo or config.get('profile', DEFAULT_PROFILE),
                          words=args.parole, fanout=args.rami, excludes=config.get('excludes', []) + args.escludi,
                          verify=args.verifica, max_mib=args.max_mib, force=args.ricostruisci)
    index = get_index(args)
    if not (index / 'atlas.sqlite').is_file():
        raise AtlasError('Index missing. Run init or update; from the repository pass --workspace workspaces/main.')
    with operation_lock(index):
        return query(args, index)


def query(a, index):
    from .visuals import export_graph
    if hasattr(a, 'related_limit') and not 0 <= a.related_limit <= 50:
        raise AtlasError('--related-limit must be between 0 and 50.')
    if a.command == 'esplora':
        if not a.richiesta:
            return explore(index, a.riferimento, a.max_chars, a.offset, a.json)
        a.command, a.ambito, a.limit = 'cerca', a.riferimento, 6
        a.tutti, a.edizione = False, None
        return query(a, index)
    if a.command == 'cerca':
        data = find(index, a.richiesta, a.ambito, a.limit, a.max_chars, True, a.tutti, a.offset, a.edizione)
        refs = [r['ref'] for r in data['risultati']]
        relations = memory.related(index, refs, a.related_limit) if refs and not a.no_graph else None
        if a.json:
            data['related'] = relations
            return data
        text = find(index, a.richiesta, a.ambito, a.limit, a.max_chars, False, a.tutti, a.offset, a.edizione)
        return (memory.format_related(relations) if relations else '') + text
    if a.command == 'leggi':
        data = read(index, a.riferimenti, a.max_chars, a.offset, a.verifica, True, a.vicini)
        if a.json:
            for record in data:
                record['related'] = memory.related(index, [record['ref']], a.related_limit) if not a.no_graph else None
            return data
        text = read(index, a.riferimenti, a.max_chars, a.offset, a.verifica, False, a.vicini)
        refs = [r['ref'] for r in data]
        relations = memory.related(index, refs, a.related_limit) if refs and not a.no_graph else None
        return (memory.format_related(relations) if relations else '') + text
    if a.command == 'stato':
        result = report(index)
        result['notes'] = [{k: n[k] for k in ('id', 'status', 'active', 'stale_bindings', 'linked_passages')} for n in memory.notes(index)]
        if a.json:
            return result
        return f"{len(result['fonti'])} sources; {sum(s['status'] not in ('ok','vuoto') for s in result['fonti'])} not extracted.\nCoverage: {index / 'copertura.md'}\nNotes: {len(result['notes'])}; stale: {sum(bool(n['stale_bindings']) for n in result['notes'])}."
    if a.command == 'esporta':
        return export(index, a.limit)
    if a.command in ('annota', 'collega'):
        result = memory.add_note(index, a.testo if a.command == 'annota' else a.nota,
                                 a.titolo if a.command == 'annota' else a.ancora,
                                 a.collega if a.command == 'annota' else [a.origine],
                                 a.verso if a.command == 'annota' else [a.destinazione],
                                 a.author, a.status, a.tipo, a.expires)
        try:
            result['visuals'] = export_graph(index)
        except (AtlasError, OSError) as exc:
            result['visualization_warning'] = 'Note saved; graph export failed: ' + str(exc)
        return result
    if a.command == 'dipendenze':
        if not 1 <= a.limit <= 1000:
            raise AtlasError('--limit must be 1–1000.')
        result = memory.related(index, a.riferimenti, a.limit)
        return result if a.json else memory.format_related(result) or 'No recorded links. This does not prove independence.'
    if a.command == 'appunti':
        if a.revoke is not None:
            result = memory.revoke(index, a.revoke)
            try:
                export_graph(index)
            except (AtlasError, OSError) as exc:
                result['visualization_warning'] = 'Note revoked; graph export failed: ' + str(exc)
            return result
        return memory.notes(index, a.id)
    if a.command == 'grafo':
        if a.rebuild:
            from .graph import build_dependencies
            build_dependencies(index)
            memory.backup(index)
        return export_graph(index)
    raise AtlasError('Unknown command.')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parser().parse_args(argv)
    try:
        result = run(args)
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AtlasError, OSError, sqlite3.Error, ValueError, KeyError) as exc:
        print('Synapse KB: ' + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Synapse KB: interrupted. Run status and update before continuing.', file=sys.stderr)
        return 130
