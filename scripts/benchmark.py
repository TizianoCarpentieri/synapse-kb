"""Reproducible measurements on a generated corpus. No model, no network.

    python scripts/benchmark.py scale --products 100 1000 5000
    python scripts/benchmark.py reconcile --cases 40
    python scripts/benchmark.py scale --engine /path/to/another/checkout   # compare releases

The corpus is synthetic and was written together with the engine: figures
describe cost and mechanical behaviour, not answer quality on real documents.
"""
import argparse
import json
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time

BASE = Path(__file__).resolve().parents[1]
TOPICS = ['return window', 'warranty claim', 'archive export', 'battery disposal', 'firmware update',
          'access badge', 'data retention', 'shipping zone', 'service contract', 'loan period',
          'calibration check', 'refund method']
SYLLABLES = 'ba be bi bo bu da de di do du ka ke ki ko ku la le li lo lu ma me mi mo mu na ne ni no nu ra re ri ro ru sa se si so su ta te ti to tu va ve vi vo vu'.split()


def vocabulary(rng, size):
    words = set()
    while len(words) < size:
        words.add(''.join(rng.choice(SYLLABLES) for _ in range(rng.choice((2, 3, 3, 4)))))
    return sorted(words)


class Corpus:
    def __init__(self, products, seed=7):
        self.rng = random.Random(seed)
        self.words = vocabulary(self.rng, 4000)
        self.weights = [1 / (rank + 1) for rank in range(len(self.words))]
        names = set()
        while len(names) < products:
            names.add(''.join(self.rng.choice(SYLLABLES) for _ in range(3)).capitalize() + self.rng.choice('xqz'))
        self.products = sorted(names)

    def filler(self, count=40):
        words = self.rng.choices(self.words, self.weights, k=count)
        return ' '.join(words).capitalize() + '.'

    def rule(self, product, topic):
        return (f'The {product} {topic} is thirty days from delivery. The customer must present the original receipt. '
                f'The item is eligible for a refund. {self.filler()}')

    def manual(self, product):
        return f'# {product} manual\n\n' + ''.join(
            f'## {product} {topic}\n\n{self.rule(product, topic)}\n\n' for topic in TOPICS[:8])

    def bulletin(self, product, number):
        a, b, c = self.rng.sample(TOPICS[:8], 3)
        text = f'# {product} bulletin\n\n'
        for topic in (a, b):
            text += (f'## {product} {topic} exception\n\nExcept for refurbished units, the {product} {topic} follows '
                     f'the standard policy. {self.filler(25)}\n\n')
        text += (f'## Bulletin {number}\n\nNotwithstanding the {product} {c}, demonstration units cannot be returned. '
                 f'See [the manual](../manuals/{product.lower()}.md) for the general rule. {self.filler(25)}\n\n')
        return text, [(a, f'{product} {a} exception'), (b, f'{product} {b} exception')], (c, f'Bulletin {number}')

    def write(self, root):
        gold = []
        for number, product in enumerate(self.products):
            slug = product.lower()
            (root / 'manuals').mkdir(parents=True, exist_ok=True)
            (root / 'updates').mkdir(parents=True, exist_ok=True)
            (root / 'manuals' / f'{slug}.md').write_text(self.manual(product), encoding='utf-8')
            text, titled, cited = self.bulletin(product, number)
            (root / 'updates' / f'{slug}-bulletin.md').write_text(text, encoding='utf-8')
            for topic, title in titled:
                gold.append(('titled_exception', f'manuals/{slug}.md', f'{product} {topic}', f'updates/{slug}-bulletin.md', title))
            gold.append(('citing_exception', f'manuals/{slug}.md', f'{product} {cited[0]}', f'updates/{slug}-bulletin.md', cited[1]))
            gold.append(('citation_forward', f'updates/{slug}-bulletin.md', cited[1], f'manuals/{slug}.md', f'{product} {cited[0]}'))
        return gold


def engine(path):
    sys.path.insert(0, str(path))
    import atlas_core
    from atlas_core import index, graph, view, memory
    return atlas_core, index, graph, view, memory


def percentiles(samples):
    ordered = sorted(samples)
    return {'p50_ms': round(statistics.median(ordered) * 1000, 2),
            'p95_ms': round(ordered[int(len(ordered) * 0.95) - 1] * 1000, 2)}


def timed(function, *args, **kwargs):
    start = time.perf_counter()
    result = function(*args, **kwargs)
    return time.perf_counter() - start, result


def scale_one(products, engine_path):
    core, index, graph, view, memory = engine(engine_path)
    rng = random.Random(11)
    with tempfile.TemporaryDirectory(prefix='synapse_bench_') as temp:
        root, out = Path(temp) / 'docs', Path(temp) / 'index'
        corpus = Corpus(products)
        gold = corpus.write(root)
        build_s, built = timed(index.build, root, output=out)
        graph_s, edges = timed(graph.build_dependencies, out)
        with index.connect(out) as db:
            by_title = {}
            for row in db.execute('SELECT n.id,n.crumbs,s.path FROM nodes n JOIN sources s ON s.id=n.sid WHERE n.kind="passage" ORDER BY n.start'):
                by_title.setdefault((row['path'], json.loads(row['crumbs'])[-1]), index.reference(db, row['id']))
        sample = rng.sample(gold, min(300, len(gold)))
        found = {}
        related_t, search_t, read_t = [], [], []
        for kind, src_path, src_title, dst_path, dst_title in sample:
            src, dst = by_title[(src_path, src_title)], by_title[(dst_path, dst_title)]
            seconds, data = timed(memory.related, out, [src], 6)
            related_t.append(seconds)
            found.setdefault(kind, []).append(any(i.get('target_ref') == dst for i in data['items']))
            search_t.append(timed(view.find, out, src_title, as_json=True)[0])
            read_t.append(timed(view.read, out, [src], as_json=True)[0])
        rule = by_title[(sample[0][1], sample[0][2])]
        auto_t = [timed(memory.add_note, out, f'{corpus.products[n]} return window: verify the account first.', 'Check')[0] for n in range(3)]
        bound_t = [timed(memory.add_note, out, 'Bound observation.', 'Bound', [rule])[0] for _ in range(3)]
        try:
            import resource
            peak = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        except ImportError:
            peak = None
        return {'engine': core.__version__, 'products': products, 'sources': built['fonti'], 'passages': built['passaggi'],
                'index_seconds': round(build_s, 2), 'graph_seconds': round(graph_s, 2),
                'edges': edges.get('edges_total', 0), 'edges_from_explicit_references': edges.get('edges_from_explicit_references'),
                'database_mib': round((out / 'atlas.sqlite').stat().st_size / 2**20, 1), 'peak_rss_mib': peak,
                'search': percentiles(search_t), 'read': percentiles(read_t), 'related_evidence': percentiles(related_t),
                'add_note_auto_linked_ms': round(statistics.median(auto_t) * 1000, 1),
                'add_note_bound_ms': round(statistics.median(bound_t) * 1000, 1),
                'gold_target_in_first_6': {k: round(sum(v) / len(v), 3) for k, v in sorted(found.items())}}


# --- reconciliation study ----------------------------------------------------

def replace_words(corpus, text, share):
    """Swap a share of the filler words; the rule sentences stay untouched."""
    head, _, filler = text.rpartition('refund. ')
    words = filler.split()
    for position in corpus.rng.sample(range(len(words)), max(1, int(len(words) * share))):
        words[position] = corpus.rng.choice(corpus.words)
    return head + 'refund. ' + ' '.join(words)


def mutations(corpus):
    def section(text, title):
        start = text.index(f'## {title}\n\n') + len(title) + 5
        return start, text.index('\n\n', start)

    def body(change):
        def apply(text, title):
            a, b = section(text, title)
            return text[:a] + change(text[a:b]) + text[b:]
        return apply

    def near_duplicate(text, title):
        a, b = section(text, title)
        copy = replace_words(corpus, text[a:b], 0.10)
        return text[:a - len(title) - 5] + f'## {title} (legacy)\n\n{copy}' + text[b:]

    return {
        'edit_elsewhere': ('same', lambda text, title: text + '\n## Appendix\n\n' + corpus.filler(30) + '\n'),
        'text_shifted': ('same', lambda text, title: text.replace('\n\n', '\n\n## Preface\n\n' + corpus.filler(200) + '\n\n', 1)),
        'lines_rewrapped': ('same', body(lambda t: textwrap.fill(t, 60))),
        'reworded_10pct': ('benign', body(lambda t: replace_words(corpus, t, 0.10))),
        'reworded_35pct': ('benign', body(lambda t: replace_words(corpus, t, 0.35))),
        'number_changed': ('reversed', body(lambda t: t.replace('thirty days', 'fourteen days'))),
        'negation_added': ('reversed', body(lambda t: t.replace('is eligible', 'is not eligible'))),
        'modal_changed': ('reversed', body(lambda t: t.replace('must present', 'may present'))),
        'heading_renamed': ('context', lambda text, title: text.replace(f'## {title}\n', f'## Withdrawn {title}\n')),
        'section_removed': ('gone', lambda text, title: text[:section(text, title)[0] - len(title) - 5] + text[section(text, title)[1] + 2:]),
        'removed_near_copy_remains': ('gone', near_duplicate),
    }


def reconcile_study(cases, engine_path):
    core, index, graph, view, memory = engine(engine_path)
    corpus = Corpus(max(cases, 20), seed=23)
    table, policy = {}, {'benign': [], 'reversed': [], 'gone': []}
    with tempfile.TemporaryDirectory(prefix='synapse_reconcile_') as temp:
        root, out, pristine = Path(temp) / 'docs', Path(temp) / 'index', Path(temp) / 'pristine'
        corpus.write(root)
        shutil.copytree(root, pristine)
        for name, (expectation, mutate) in mutations(corpus).items():
            shutil.rmtree(root)
            shutil.copytree(pristine, root)
            shutil.rmtree(out, ignore_errors=True)
            index.build(root, output=out)
            graph.build_dependencies(out)
            titles = []
            for product in corpus.products[:cases]:
                topic = corpus.rng.choice(TOPICS[:8])
                path, title = f'manuals/{product.lower()}.md', f'{product} {topic}'
                with index.connect(out) as db:
                    row = db.execute('SELECT n.id FROM nodes n JOIN sources s ON s.id=n.sid WHERE n.kind="passage" AND s.path=? AND n.crumbs LIKE ?',
                                     (path, '%' + json.dumps(title, ensure_ascii=False) + ']')).fetchone()
                    reference = index.reference(db, row[0])
                memory.add_note(out, 'Observation about this rule.', title, [reference])
                titles.append((path, title))
            for path, title in titles:
                file = root / path
                file.write_text(mutate(file.read_text(encoding='utf-8'), title), encoding='utf-8')
            started = time.perf_counter()
            index.build(root, output=out)
            graph.build_dependencies(out)
            elapsed = time.perf_counter() - started
            states = [n['bindings'][0] for n in memory.notes(out)]
            similarities = [s['similarity'] for s in states if s['state'] != 'exact']
            table[name] = {'cases': len(states), 'expected': expectation,
                           'followed_automatically': sum(s['state'] in ('exact', 'relocated') for s in states),
                           'proposed_for_review': sum(s['state'] == 'drifted' for s in states),
                           'left_stale': sum(s['state'] == 'stale' for s in states),
                           'median_similarity': round(statistics.median(similarities), 3) if similarities else None,
                           'proposals_with_a_named_change': sum(s['state'] == 'drifted' and bool(s['flags']) for s in states),
                           'update_seconds': round(elapsed, 2)}
            if expectation in policy:
                policy[expectation] += [s['similarity'] for s in states]
    thresholds = {}
    for threshold in (0.6, 0.7, 0.8, 0.9, 0.95):
        share = lambda group: round(sum(v >= threshold for v in policy[group]) / len(policy[group]), 3)
        thresholds[str(threshold)] = {'harmless_rewording_reattached': share('benign'),
                                      'reversed_rule_reattached': share('reversed'),
                                      'removed_passage_reattached_elsewhere': share('gone')}
    return {'engine': core.__version__, 'by_mutation': table,
            'if_a_similarity_threshold_auto_accepted': thresholds,
            'shipped_policy': 'automatic only for identical text in the same file and heading path; everything else is a proposal or stale'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('study', choices=('scale', 'reconcile', '_scale_one'))
    parser.add_argument('--products', type=int, nargs='+', default=[100, 1000, 5000])
    parser.add_argument('--cases', type=int, default=40)
    parser.add_argument('--engine', type=Path, default=BASE, help='Checkout whose atlas_core is measured.')
    args = parser.parse_args()
    if args.study == '_scale_one':
        result = scale_one(args.products[0], args.engine)
    elif args.study == 'scale':
        # One process per size, so peak memory belongs to that size alone.
        result = [json.loads(subprocess.run([sys.executable, __file__, '_scale_one', '--products', str(n), '--engine', str(args.engine)],
                                            capture_output=True, text=True, check=True).stdout) for n in args.products]
    else:
        result = reconcile_study(args.cases, args.engine)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
