from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import sys

from .common import AtlasError, DEFAULT_PROFILE, PROFILES, VERSION
from .index import build
from .view import explore, export, find, localize, read, report
from .consult import consult
from . import guides
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
    c = sub.add_parser('doctor', help='Check the runtime; with --workspace, show setup progress and the next step.')
    c.add_argument('--workspace', type=Path)
    aliases = {'prepara': ['build'], 'aggiorna': ['update'], 'esplora': ['explore'],
               'cerca': ['search'], 'leggi': ['read'], 'stato': ['status'],
               'esporta': ['export'], 'annota': ['annotate'], 'collega': ['link'],
               'dipendenze': ['dependencies'], 'appunti': ['notes'], 'grafo': ['graph'],
               'eventi': ['events'], 'servi': ['serve'], 'consulta': ['consult'], 'guida': ['guide']}
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
        elif name in ('esplora', 'cerca', 'leggi', 'consulta'):
            c.add_argument('--max-chars', type=int, default={'leggi': 12000, 'consulta': 9000}.get(name, 1600))
            c.add_argument('--no-graph', action='store_true', help='Disable related evidence for controlled A/B tests.')
            c.add_argument('--no-guide', action='store_true', help='Omit the workspace guidance for this branch.')
            c.add_argument('--related-limit', type=int, default=8 if name == 'consulta' else 6)
            if name == 'consulta':
                c.add_argument('richiesta', help='Words of the question; one call searches, reads and adds related evidence.')
                c.add_argument('--ambito', '--scope', default='root')
                c.add_argument('--limit', type=int, default=3, help='Source texts to read in full (1–8).')
                continue
            c.add_argument('--offset', type=int, default=0)
            if name == 'esplora':
                c.add_argument('riferimento', nargs='?', default='root')
                c.add_argument('--richiesta')
            elif name == 'cerca':
                c.add_argument('richiesta')
                c.add_argument('--ambito', '--scope', default='root')
                c.add_argument('--limit', type=int, default=6)
                c.add_argument('--tutti', '--all', action='store_true')
                c.add_argument('--edizione')
            else:
                c.add_argument('riferimenti', nargs='+')
                c.add_argument('--verifica', action='store_true')
                c.add_argument('--vicini', '--neighbors', type=int, default=0)
                c.add_argument('--sezione', '--section', action='store_true',
                               help='Read the whole section that contains the passage.')
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
            c.add_argument('--review', action='store_true', help='Bindings that are not verified by identical text.')
            c.add_argument('--rebind', type=int, metavar='ID', help='A person accepts the proposed new passages of a note.')
            c.add_argument('--import', dest='import_file', type=Path, metavar='MEMORY_JSON',
                           help='Merge notes exported by another workspace.')
            c.add_argument('--keep-status', action='store_true', help='With --import: keep "confirmed" labels.')
            c.add_argument('--author', default='agent')
        elif name == 'eventi':
            c.add_argument('--since', type=int, default=0)
            c.add_argument('--limit', type=int, default=200)
            c.add_argument('--verify', '--verifica', action='store_true', help='Recompute the hash chain.')
        elif name == 'grafo':
            c.add_argument('--rebuild', action='store_true')
        elif name == 'guida':
            c.add_argument('ambito', nargs='?', default='root', help='Folder or document path in the collection; root by default.')
            c.add_argument('--set', dest='set_text', metavar='TEXT', help='Replace the guide of this scope.')
            c.add_argument('--set-file', type=Path, metavar='FILE', help='Replace the guide with the content of a text file.')
            c.add_argument('--remove', action='store_true')
            c.add_argument('--list', dest='list_all', action='store_true')
            c.add_argument('--author', default='owner')
    return p


def update(source, index, profile=DEFAULT_PROFILE, **kwargs):
    from .graph import build_dependencies
    from .visuals import export_graph
    result = build(source, output=index, profile=profile, **kwargs)
    result['dependencies'] = build_dependencies(index)
    memory.backup(index)
    result['visuals'] = export_graph(index)
    notes = [n for n in memory.notes(index) if n['active']]
    result['notes_to_review'] = sum(bool(n['stale_bindings']) for n in notes)
    result['bindings'] = dict(Counter(b['state'] for n in notes for b in n['bindings']))
    return result


def get_index(args):
    if args.indice:
        return args.indice.resolve()
    if args.workspace:
        return load_workspace(args.workspace)[0] / '_atlas'
    return Path('_atlas').resolve()


def run(args):
    if args.command == 'doctor':
        return doctor(args.workspace)
    if args.command in ('init', 'inizia'):
        work = init_workspace(args.workspace, args.source, args.demo, args.rules_file, args.profile, args.exclude, args.empty)
        result = {'workspace': str(work), 'next': 'Run doctor --workspace on this folder: it names the next setup step. '
                                                    'For everyday use open the folder with a tool-capable agent and read its AGENTS.md.'}
        # A tiny portable launcher preserves the configured rules workspace and avoids shell quoting.
        launch = Path(sys.argv[0]).resolve()
        if launch.is_file() and launch.suffix in ('.py', '.pyz'):
            import_path = launch if launch.suffix == '.pyz' else launch.parent
            # Works from any current directory: it names its own workspace unless the caller names one.
            (work / 'atlas.py').write_text(
                'import os, runpy, sys\n'
                'named = any(a.split("=")[0] in ("--workspace", "--indice", "--index") for a in sys.argv[1:])\n'
                'if len(sys.argv) > 1 and not named and sys.argv[1] not in ("init", "inizia", "build", "prepara", "--version", "-h", "--help"):\n'
                '    sys.argv += ["--workspace", os.path.dirname(os.path.abspath(__file__))]\n'
                'sys.path.insert(0, ' + repr(str(import_path)) + ')\nrunpy.run_path(' + repr(str(launch)) + ", run_name='__main__')\n",
                encoding='utf-8')
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
    if args.command == 'servi':
        from .mcp_server import serve
        return serve(index, args.workspace)
    return dispatch(args, index)


def mutates(a):
    return (a.command in ('annota', 'collega', 'grafo', 'esporta') or
            (a.command == 'guida' and (a.set_text is not None or a.set_file is not None or a.remove)) or
            (a.command == 'appunti' and (a.revoke is not None or a.rebind is not None or a.import_file is not None)))


def dispatch(args, index):
    """Readers run on a database snapshot; only mutations queue behind the writer lock."""
    if not mutates(args):
        return query(args, index)
    with operation_lock(index, wait=15):
        return query(args, index)


def query(a, index):
    from .visuals import export_graph
    from . import events
    if hasattr(a, 'related_limit') and not 0 <= a.related_limit <= 50:
        raise AtlasError('--related-limit must be between 0 and 50.')
    guided = lambda paths: guides.chain(index, paths) if not a.no_guide else None
    if a.command == 'consulta':
        return consult(index, a.richiesta, localize(index, [a.ambito])[0], a.limit, a.max_chars,
                       a.related_limit, not a.no_graph, not a.no_guide, a.json)
    if a.command == 'esplora':
        a.riferimento = localize(index, [a.riferimento])[0]
        if not a.richiesta:
            data = explore(index, a.riferimento, a.max_chars, a.offset, a.json)
            guidance = guided([guides.scope_of(index, a.riferimento)])
            if a.json:
                return data | {'guidance': guidance}
            return (guides.format_chain(guidance) if guidance else '') + data
        a.command, a.ambito, a.limit = 'cerca', a.riferimento, 6
        a.tutti, a.edizione = False, None
        return query(a, index)
    if a.command == 'cerca':
        a.ambito = localize(index, [a.ambito])[0]
        data = find(index, a.richiesta, a.ambito, a.limit, a.max_chars, True, a.tutti, a.offset, a.edizione)
        refs = [r['ref'] for r in data['risultati']]
        relations = memory.related(index, refs, a.related_limit) if refs and not a.no_graph else None
        guidance = guided([r['fonte'] for r in data['risultati']] + [guides.scope_of(index, a.ambito)])
        if a.json:
            data['related'], data['guidance'] = relations, guidance
            return data
        text = find(index, a.richiesta, a.ambito, a.limit, a.max_chars, False, a.tutti, a.offset, a.edizione)
        return (guides.format_chain(guidance) if guidance else '') + (memory.format_related(relations) if relations else '') + text
    if a.command == 'leggi':
        a.riferimenti = localize(index, a.riferimenti)
        data = read(index, a.riferimenti, a.max_chars, a.offset, a.verifica, True, a.vicini, a.sezione)
        guidance = guided([r['fonte'] for r in data])
        if a.json:
            given = set()
            for record in data:
                record['related'] = memory.related(index, [record['ref']], a.related_limit) if not a.no_graph else None
                # Each guide once per response, on the first record it applies to.
                own = guides.chain(index, [record['fonte']]) if guidance else None
                if own:
                    own['items'] = [g for g in own['items'] if g['scope'] not in given]
                    given.update(g['scope'] for g in own['items'])
                record['guidance'] = own
            return data
        text = read(index, a.riferimenti, a.max_chars, a.offset, a.verifica, False, a.vicini, a.sezione)
        refs = [r['ref'] for r in data]
        relations = memory.related(index, refs, a.related_limit) if refs and not a.no_graph else None
        return (guides.format_chain(guidance) if guidance else '') + (memory.format_related(relations) if relations else '') + text
    if a.command == 'guida':
        if a.list_all:
            return guides.listing(index)
        if a.set_text is not None or a.set_file is not None or a.remove:
            if sum((a.set_text is not None, a.set_file is not None, a.remove)) != 1:
                raise AtlasError('Use one of --set, --set-file or --remove.')
            text = '' if a.remove else a.set_text if a.set_text is not None else a.set_file.read_text(encoding='utf-8-sig')
            return guides.save(index, localize(index, [a.ambito])[0], text, a.author)
        data = guides.show(index, localize(index, [a.ambito])[0])
        return data if a.json else guides.format_chain(data) or 'No guidance for this scope or the folders above it.'
    if a.command == 'stato':
        result = report(index)
        result['notes'] = [{k: n[k] for k in ('id', 'status', 'active', 'stale_bindings', 'rebind_proposals', 'linked_passages')} for n in memory.notes(index)]
        result['last_event'] = events.read(index, 0, 1)['last_seq']
        result['guides'] = guides.listing(index)
        if a.json:
            return result
        return (f"{len(result['fonti'])} sources; {sum(s['status'] not in ('ok','vuoto') for s in result['fonti'])} not extracted.\n"
                f"Coverage: {index / 'copertura.md'}\nNotes: {len(result['notes'])}; "
                f"with bindings to review: {sum(bool(n['stale_bindings']) for n in result['notes'])} (notes --review).")
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
        result = memory.related(index, localize(index, a.riferimenti), a.limit)
        return result if a.json else memory.format_related(result) or 'No recorded links. This does not prove independence.'
    if a.command == 'appunti':
        if sum(x is not None for x in (a.revoke, a.rebind, a.import_file)) > 1:
            raise AtlasError('Use one of --revoke, --rebind or --import.')
        if a.revoke is not None or a.rebind is not None or a.import_file is not None:
            if a.revoke is not None:
                result = memory.revoke(index, a.revoke, a.author)
            elif a.rebind is not None:
                result = memory.accept_rebind(index, a.rebind, a.author)
            else:
                result = memory.import_notes(index, a.import_file, a.author, a.keep_status)
            try:
                export_graph(index)
            except (AtlasError, OSError) as exc:
                result['visualization_warning'] = 'Memory change saved; graph export failed: ' + str(exc)
            return result
        if a.review:
            return memory.review(index)
        return memory.notes(index, a.id)
    if a.command == 'eventi':
        if a.verify:
            return events.verify(index)
        if not 1 <= a.limit <= 1000 or a.since < 0:
            raise AtlasError('--limit must be 1–1000 and --since non-negative.')
        return events.read(index, a.since, a.limit)
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
        if result is not None:
            print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AtlasError, OSError, sqlite3.Error, ValueError, KeyError) as exc:
        print('Synapse KB: ' + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Synapse KB: interrupted. Run status and update before continuing.', file=sys.stderr)
        return 130
