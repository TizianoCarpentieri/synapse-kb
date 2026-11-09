"""Deterministic retrieval checks, not an LLM answer-quality benchmark."""
import json
from pathlib import Path
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from atlas_core.cli import update
from atlas_core.index import connect, reference
from atlas_core.memory import related, add_note, revoke
from atlas_core.workspace import init_workspace, load_workspace


def evaluate():
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='synapse_evaluation_') as temp:
        work = init_workspace(Path(temp) / 'workspace', demo=True)
        work, source, _ = load_workspace(work)
        index = work / '_atlas'
        built = update(source, index)
        cases = [
            ('orion_exception', 'manuals/orion.md', 'Orion return window',
             'updates/orion-exceptions.md', 'Orion return window exception'),
            ('aurora_prerequisite', 'manuals/aurora.md', 'Aurora archive export',
             'updates/aurora-conditions.md', 'Aurora archive export prerequisite'),
        ]
        results = []
        with connect(index) as db:
            rows = db.execute('SELECT n.id,n.crumbs,s.path FROM nodes n JOIN sources s ON s.id=n.sid WHERE n.kind="passage"').fetchall()
            def ref(path, title):
                return next(reference(db, r['id']) for r in rows if r['path'] == path and json.loads(r['crumbs'])[-1:] == [title])
            refs = [(name, ref(src, heading), ref(dst, target)) for name, src, heading, dst, target in cases]
        for name, src, dst in refs:
            data = related(index, [src], limit=6)
            found = any(i.get('target_ref') == dst for i in data['items'])
            results.append({'case': name, 'expected_target_in_top_6': found})
        note = add_note(index, 'Orion return window: distinguish account category.', 'Orion return window')
        visible = any(i.get('note_id') == note['note_id'] and i['type'] == 'note'
                      for i in related(index, [refs[0][1]])['items'])
        revoke(index, note['note_id'])
        removed = not any(i.get('note_id') == note['note_id'] and i['type'] == 'note'
                          for i in related(index, [refs[0][1]])['items'])
        return {'kind': 'synthetic retrieval smoke test', 'llm_calls': 0,
                'sources': built['fonti'], 'passages': built['passaggi'],
                'heuristic_edges': built['dependencies']['edges_total'], 'cases': results,
                'automatic_note_visible': visible, 'revocation_effective': removed,
                'seconds': round(time.perf_counter() - start, 3),
                'passed': all(r['expected_target_in_top_6'] for r in results) and visible and removed,
                'does_not_measure': ['answer correctness', 'precision on unseen corpora', 'real agent adherence', 'latency at scale']}


if __name__ == '__main__':
    result = evaluate()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
