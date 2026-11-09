import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from atlas_core import memory
from atlas_core.cli import main, update
from atlas_core.common import AtlasError
from atlas_core.graph import build_dependencies
from atlas_core.index import connect, reference
from atlas_core.view import find
from atlas_core.visuals import export_graph
from atlas_core.workspace import init_workspace, load_workspace, operation_lock

BASE = Path(__file__).resolve().parents[1]


class WorkspaceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='atlas_public_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = init_workspace(self.root / 'workspace', demo=True)
        self.work, self.source, self.config = load_workspace(self.work)
        self.index = self.work / '_atlas'
        update(self.source, self.index)
        with connect(self.index) as db:
            rows = db.execute('SELECT n.id,s.path,n.crumbs FROM nodes n JOIN sources s ON s.id=n.sid '
                              'WHERE n.kind="passage" ORDER BY n.id').fetchall()
            self.rule = next(reference(db, r['id']) for r in rows if r['path'] == 'manuals/orion.md' and 'Orion return window' in r['crumbs'])
            self.exception = next(reference(db, r['id']) for r in rows if r['path'] == 'updates/orion-exceptions.md' and 'Orion return window exception' in r['crumbs'])

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main([*args, '--indice', str(self.index)])
        return status, out.getvalue(), err.getvalue()

    def test_cross_file_exception_is_retrievable(self):
        data = memory.related(self.index, [self.rule], 50)
        self.assertTrue(any(x.get('target_ref') == self.exception for x in data['items']))

    def test_relation_words_do_not_link_to_meta_description(self):
        with connect(self.index) as db:
            count = db.execute('SELECT count(*) FROM dependencies d JOIN nodes n ON n.id=d.dst '
                               'JOIN sources s ON s.id=n.sid WHERE s.path="manuals/atlas-scope.md"').fetchone()[0]
        self.assertEqual(count, 0)

    def test_text_warning_is_at_start_and_json_preserves_it(self):
        memory.add_note(self.index, 'Ask the account category before deciding.', 'Scope check', [self.rule])
        status, out, err = self.cli('read', self.rule)
        self.assertEqual(status, 0, err)
        self.assertTrue(out.startswith('[RELATED EVIDENCE'))
        self.assertIn('NOTE #1', '\n'.join(out.splitlines()[:10]))
        status, out, err = self.cli('read', self.rule, '--json')
        self.assertEqual(status, 0, err)
        self.assertTrue(any(x['type'] == 'note' for x in json.loads(out)[0]['related']['items']))
        status, out, err = self.cli('search', 'Orion return window', '--json')
        self.assertEqual(status, 0, err)
        self.assertTrue(json.loads(out)['related']['items'])
        status, out, err = self.cli('explore', '--richiesta', 'Orion return window', '--json')
        self.assertEqual(status, 0, err)
        self.assertTrue(json.loads(out)['related']['items'])

    def test_no_graph_baseline_removes_only_related_evidence(self):
        status, out, err = self.cli('read', self.rule, '--no-graph')
        self.assertEqual(status, 0, err)
        self.assertNotIn('[RELATED EVIDENCE', out)
        self.assertIn('thirty days', out)

    def test_manual_links_survive_graph_and_index_rebuild(self):
        memory.add_note(self.index, 'Read the cross-file exception.', 'Manual relationship', [self.rule], [self.exception])
        build_dependencies(self.index)
        with connect(self.index) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM manual_edges').fetchone()[0], 1)
        update(self.source, self.index, force=True)
        with connect(self.index) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM manual_edges').fetchone()[0], 1)
        self.assertEqual(memory.notes(self.index)[0]['stale_bindings'], 0)

    def test_changed_source_binding_is_not_guessed(self):
        memory.add_note(self.index, 'Keep this observation.', 'Manual relationship', [self.rule], [self.exception])
        file = self.source / 'manuals/orion.md'
        file.write_text(file.read_text(encoding='utf-8') + '\nNew revision.\n', encoding='utf-8')
        update(self.source, self.index)
        self.assertEqual(memory.notes(self.index)[0]['stale_bindings'], 1)
        with connect(self.index) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM manual_edges').fetchone()[0], 0)
        self.assertEqual(len(memory.notes(self.index)), 1)

    def test_invalid_reference_aborts_note_atomically(self):
        with self.assertRaises(AtlasError):
            memory.add_note(self.index, 'Must not be saved.', sources=[self.rule, 'n999-bad00000'])
        self.assertEqual(memory.notes(self.index), [])

    def test_auto_links_and_revocation(self):
        result = memory.add_note(self.index, 'Orion return window: check the order category.', 'Orion return window')
        self.assertGreater(result['linked_passages'], 0)
        memory.revoke(self.index, result['note_id'])
        self.assertEqual(memory.notes(self.index)[0]['status'], 'revoked')
        self.assertFalse(any(x['type'] == 'note' for x in memory.related(self.index, [self.rule])['items']))

    def test_unmatched_note_is_saved_without_false_success(self):
        result = memory.add_note(self.index, 'Zygomorphic zephyr quokka', 'Unrelated')
        self.assertEqual(result['linked_passages'], 0)

    def test_expired_note_is_not_injected(self):
        memory.add_note(self.index, 'An old observation.', sources=[self.rule], expires='2001-01-01')
        self.assertFalse(any(x['type'] == 'note' for x in memory.related(self.index, [self.rule])['items']))

    def test_deleting_markdown_does_not_delete_database_note(self):
        memory.add_note(self.index, 'Durable observation.', sources=[self.rule])
        (self.index / 'appunti_condivisi.md').unlink()
        build_dependencies(self.index)
        self.assertEqual(len(memory.notes(self.index)), 1)
        memory.backup(self.index)
        self.assertTrue((self.index / 'appunti_condivisi.md').exists())

    def test_full_note_readable_and_limit_reports_omissions(self):
        text = 'A long observation. ' * 30
        memory.add_note(self.index, text, sources=[self.rule])
        status, out, err = self.cli('notes', '1', '--json')
        self.assertEqual(status, 0, err)
        self.assertEqual(json.loads(out)[0]['body'], text)
        self.assertGreater(memory.related(self.index, [self.rule], 0)['omitted'], 0)

    def test_stale_dependency_cue_is_suppressed(self):
        file = self.source / 'updates/orion-exceptions.md'
        file.write_text('# Changed\nNo old policy.\n', encoding='utf-8')
        data = memory.related(self.index, [self.rule], 50)
        self.assertTrue(data['warnings'])
        self.assertFalse(any(x.get('target_ref') == self.exception for x in data['items']))

    def test_sources_unchanged_and_no_engine_network(self):
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        with patch('socket.socket.connect', side_effect=AssertionError('Network must not be used')):
            update(self.source, self.index)
            memory.add_note(self.index, 'Orion return window check.')
            export_graph(self.index)
        after = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_existing_workspace_rules_are_never_overwritten(self):
        rules = (self.work / 'AGENTS.md').read_bytes()
        with self.assertRaises(AtlasError):
            init_workspace(self.work, demo=True)
        self.assertEqual((self.work / 'AGENTS.md').read_bytes(), rules)

    def test_external_source_and_custom_rules(self):
        custom = self.root / 'custom.md'
        custom.write_text('Answer concisely in Italian.', encoding='utf-8')
        other = init_workspace(self.root / 'custom-work', source=self.source, rules_file=custom)
        _, source, _ = load_workspace(other)
        self.assertEqual(source, self.source)
        self.assertEqual((other / 'RULES.md').read_text(encoding='utf-8'), custom.read_text(encoding='utf-8'))

    def test_html_escapes_document_script_tags(self):
        memory.add_note(self.index, '</script><script>alert(1)</script>', 'Attack string', [self.rule])
        export_graph(self.index)
        html = (self.index / 'visuals/graph.html').read_text(encoding='utf-8')
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003c/script\\u003e', html)

    def test_user_vault_note_survives_export(self):
        vault = self.index / 'visuals/obsidian'
        (vault / 'my-personal-note.md').write_text('keep', encoding='utf-8')
        export_graph(self.index)
        self.assertEqual((vault / 'my-personal-note.md').read_text(encoding='utf-8'), 'keep')

    def test_empty_workspace_noninteractive(self):
        work = init_workspace(self.root / 'empty', empty=True)
        _, source, _ = load_workspace(work)
        result = update(source, work / '_atlas')
        self.assertEqual(result['fonti'], 0)

    def test_local_cli_launcher_can_run_from_workspace(self):
        work = self.root / 'launcher-work'
        result = subprocess.run([sys.executable, str(BASE / 'atlas.py'), 'init', '--empty', '--workspace', str(work)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = subprocess.run([sys.executable, 'atlas.py', 'status'], cwd=work, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(status.returncode, 0, status.stderr)


if __name__ == '__main__':
    unittest.main()
