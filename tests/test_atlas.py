import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from atlas_core.common import AtlasError
from atlas_core.index import build, connect, chunks
from atlas_core.view import explore, find, read, report, resolve, frontier
from tests.fixtures import mixed, put, make_pdf


class AtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='atlas_tests_')
        cls.root = mixed(Path(cls.temp.name) / 'fonti')
        cls.index = cls.root / '_atlas'
        cls.built = build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_mixed_formats_and_coverage(self):
        status = report(self.index)
        self.assertEqual(len(status['fonti']), 13)
        missing = [s for s in status['fonti'] if s['status'] == 'non_estratto']
        self.assertEqual([s['path'] for s in missing], ['legacy.doc'])
        for query in ('MARINA 582', 'ORBIT 734', 'ISOLA 826', 'QUARZO 913', 'FELCE 271', 'MIELE 648', 'NEVE 185'):
            with self.subTest(query=query):
                hits = find(self.index, query, as_json=True)['risultati']
                self.assertTrue(hits)
                evidence = read(self.index, [hits[0]['ref']])
                self.assertIn(query.replace(' ', '-'), evidence)

    def test_original_headings_and_ancestors(self):
        hit = find(self.index, 'TORNIO 419', as_json=True)['risultati'][0]
        text = read(self.index, [hit['ref']])
        self.assertIn('H1 Pianificazione > H4 Eccezioni', text)
        with connect(self.index) as db:
            headings = [r[0] for r in db.execute('SELECT title FROM nodes WHERE kind="heading"')]
        self.assertNotIn('Questo non è un titolo', headings)

    def test_word_inherited_heading(self):
        with connect(self.index) as db:
            row = db.execute('SELECT n.level FROM nodes n JOIN sources s ON s.id=n.sid WHERE s.path="porto.docx" AND n.kind="heading"').fetchone()
        self.assertEqual(row[0], 2)

    def test_code_symbol_and_components(self):
        for query in ('getCalibrationWindow', 'calibration window', 'WINDOW 592'):
            hits = find(self.index, query, as_json=True)['risultati']
            self.assertTrue(any(h['fonte'] == 'src/app.py' for h in hits))

    def test_generated_groups_cover_every_original_heading(self):
        with connect(self.index) as db:
            source = resolve(db, 'flat.md')
            shown = frontier(db, source, 2000)
            self.assertLess(len(shown), 200)
            seen = []
            for item in shown:
                rows = db.execute('WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT n.id FROM nodes n JOIN sub ON n.parent=sub.id) SELECT n.id FROM nodes n JOIN sub ON sub.id=n.id WHERE n.kind="heading"', (item['id'],))
                seen.extend(r[0] for r in rows)
            self.assertEqual(len(seen), 200)
            self.assertEqual(len(set(seen)), 200)

    def test_every_source_byte_covered_once(self):
        with connect(self.index) as db:
            for source in db.execute('SELECT * FROM sources WHERE status="ok"'):
                spans = db.execute('SELECT start,end FROM nodes WHERE sid=? AND kind="passage" ORDER BY start', (source['id'],)).fetchall()
                previous = 0
                for start, end in spans:
                    self.assertEqual(start, previous, source['path'])
                    previous = end
                self.assertEqual(previous, (self.index / source['cache']).stat().st_size)

    def test_view_and_search_budgets(self):
        for budget in (600, 1200, 2000, 4000):
            for selector in ('root', 'flat.md', 'lungo.txt'):
                text = explore(self.index, selector, budget)
                self.assertLessEqual(len(text), budget)
            self.assertLessEqual(len(find(self.index, 'Sezione risorsa', budget=budget)), budget)

    def test_continuations_reconstruct_exact_text(self):
        with connect(self.index) as db:
            source = db.execute('SELECT * FROM sources WHERE path="lungo.txt"').fetchone()
        offset = 0
        collected = ''
        while True:
            part = read(self.index, ['lungo.txt'], budget=900, offset=offset, as_json=True)[0]
            collected += part['testo']
            offset = part['continua_offset']
            if offset is None:
                break
        self.assertEqual(collected.encode(), (self.index / source['cache']).read_bytes())

    def test_batch_read(self):
        a = find(self.index, 'NEVE 185', as_json=True)['risultati'][0]['ref']
        b = find(self.index, 'QUARZO 913', as_json=True)['risultati'][0]['ref']
        result = read(self.index, [a, b])
        self.assertIn('NEVE-185', result)
        self.assertIn('QUARZO-913', result)

    def test_no_results_not_claimed_as_absence(self):
        self.assertIn('Non prova l’assenza', find(self.index, 'zzzxxyyqqv'))

    def test_html_script_excluded(self):
        self.assertFalse(find(self.index, 'INVISIBLE_SCRIPT', as_json=True)['risultati'])

    def test_formula_not_misreported_as_calculated(self):
        self.assertIn('valore memorizzato: assente', read(self.index, ['stock.xlsx']))

    def test_scan_pdf_reports_absent_text(self):
        with tempfile.TemporaryDirectory(prefix='atlas_empty_pdf_') as tmp:
            root = Path(tmp)
            make_pdf(root / 'empty.pdf', '')
            build(root)
            self.assertIn('Pagine senza testo', read(root / '_atlas', ['empty.pdf']))

    def test_incremental_update_deletion_and_stale_refs(self):
        with tempfile.TemporaryDirectory(prefix='atlas_update_') as tmp:
            root = Path(tmp)
            put(root / 'one.txt', 'old unique CARBONE-581')
            put(root / 'two.txt', 'This remains the same.')
            build(root)
            idx = root / '_atlas'
            ref = find(idx, 'CARBONE', as_json=True)['risultati'][0]['ref']
            unchanged = build(root)
            self.assertEqual(unchanged['invariati'], 2)
            put(root / 'one.txt', 'new unique ARGILLA-392 revised')
            with self.assertRaises(AtlasError):
                read(idx, [ref])
            changed = build(root)
            self.assertEqual(changed['aggiornati'], 1)
            self.assertEqual(changed['invariati'], 1)
            self.assertFalse(find(idx, 'CARBONE', as_json=True)['risultati'])
            self.assertTrue(find(idx, 'ARGILLA', as_json=True)['risultati'])
            with self.assertRaises(AtlasError):
                read(idx, [ref])
            (root / 'one.txt').unlink()
            changed = build(root)
            self.assertEqual(changed['rimossi'], 1)
            self.assertFalse(find(idx, 'ARGILLA', as_json=True)['risultati'])

    def test_same_stat_change_verified(self):
        with tempfile.TemporaryDirectory(prefix='atlas_stat_') as tmp:
            root = Path(tmp)
            path = root / 'x.txt'
            put(path, 'Valore alfa.')
            build(root)
            stat = path.stat()
            put(path, 'Valore beta.')
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaises(AtlasError):
                read(root / '_atlas', ['x.txt'], verify=True)
            build(root, verify=True)
            self.assertIn('beta', read(root / '_atlas', ['x.txt']))

    def test_derived_text_tamper(self):
        with tempfile.TemporaryDirectory(prefix='atlas_tamper_') as tmp:
            root = Path(tmp)
            put(root / 'x.txt', 'Original content untouched.')
            build(root)
            idx = root / '_atlas'
            cache = next((idx / 'testi').glob('*.txt'))
            cache.write_text('CHANGED content untouched.', encoding='utf-8')
            with self.assertRaises(AtlasError):
                read(idx, ['x.txt'])
            build(root, force=True)
            self.assertIn('Original content', read(idx, ['x.txt']))

    def test_portable_after_directory_move(self):
        with tempfile.TemporaryDirectory(prefix='atlas_move_') as tmp:
            base = Path(tmp)
            root = base / 'original'
            put(root / 'x.md', '# Moving\nSame contents MOVABLE-614.\n')
            build(root)
            moved = base / 'moved'
            shutil.copytree(root, moved)
            self.assertIn('MOVABLE-614', read(moved / '_atlas', ['x.md']))

    def test_duplicate_names_are_ambiguous(self):
        with tempfile.TemporaryDirectory(prefix='atlas_names_') as tmp:
            root = Path(tmp)
            put(root / 'a' / 'same.md', 'First')
            put(root / 'b' / 'same.md', 'Second')
            build(root)
            with self.assertRaises(AtlasError):
                read(root / '_atlas', ['same.md'])
            self.assertIn('Second', read(root / '_atlas', ['b/same.md']))

    def test_utf16_and_unknown_text_extension(self):
        with tempfile.TemporaryDirectory(prefix='atlas_encoding_') as tmp:
            root = Path(tmp)
            (root / 'unicode.txt').write_text('La città è più vicina. OMEGA-407', encoding='utf-16')
            put(root / 'config.custom', 'Custom extension ZEFIRO-912')
            build(root)
            self.assertIn('città', read(root / '_atlas', ['unicode.txt']))
            self.assertTrue(find(root / '_atlas', 'ZEFIRO', as_json=True)['risultati'])

    def test_output_guard_and_query_punctuation(self):
        with self.assertRaises(AtlasError):
            build(self.root, output=self.root)
        self.assertIsInstance(find(self.index, '" OR SELECT * !', as_json=True), dict)


if __name__ == '__main__':
    unittest.main()
