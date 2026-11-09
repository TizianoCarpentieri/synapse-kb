"""0.2 behaviour: re-anchoring, typed relations, journal, memory merge, concurrency, MCP."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from atlas_core import events, memory, reconcile
from atlas_core.cli import main, update
from atlas_core.common import AtlasError, stem_prefix
from atlas_core.extract import extract
from atlas_core.graph import extract_phrases
from atlas_core.index import connect, reference
from atlas_core.relations import analyze
from atlas_core.view import find, read, read_bytes
from atlas_core.workspace import init_workspace, load_workspace, operation_lock
from tests.fixtures import put, zip_parts

BASE = Path(__file__).resolve().parents[1]
RULE = ('# Returns\n\n## Vega return window\n\nThe Vega return window is thirty days from delivery. '
        'The customer must present the original receipt at the service desk.\n\n'
        '## Vega shipping zones\n\nShipping zones are listed by courier and by weight class.\n')


class Workspace(unittest.TestCase):
    documents = {'policy/vega.md': RULE}

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synapse_evolution_')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'docs'
        for name, text in self.documents.items():
            put(self.source / name, text)
        self.index = self.root / 'index'
        update(self.source, self.index)

    def ref(self, path, title):
        with connect(self.index) as db:
            for row in db.execute('SELECT n.id,n.crumbs FROM nodes n JOIN sources s ON s.id=n.sid '
                                  'WHERE n.kind="passage" AND s.path=? ORDER BY n.start', (path,)):
                if json.loads(row['crumbs'])[-1:] == [title]:
                    return reference(db, row['id'])
        raise AssertionError('no passage under ' + title)

    def rewrite(self, path, change):
        file = self.source / path
        file.write_text(change(file.read_text(encoding='utf-8')), encoding='utf-8')
        update(self.source, self.index)

    def state(self):
        return memory.notes(self.index)[0]['bindings'][0]


class ReconciliationTests(Workspace):
    def setUp(self):
        super().setUp()
        self.rule = self.ref('policy/vega.md', 'Vega return window')
        memory.add_note(self.index, 'Ask for the receipt first.', 'Receipt', [self.rule])

    def test_text_shifted_by_an_insertion_is_followed(self):
        self.rewrite('policy/vega.md', lambda t: t.replace('# Returns\n', '# Returns\n\n## Preamble\n\n' + 'New opening words. ' * 40 + '\n'))
        self.assertEqual(self.state()['state'], 'relocated')
        related = memory.related(self.index, [self.ref('policy/vega.md', 'Vega return window')])
        self.assertEqual([i['binding'] for i in related['items'] if i['type'] == 'note'], ['explicit'])

    def test_rewrapped_lines_are_the_same_text(self):
        self.rewrite('policy/vega.md', lambda t: t.replace('from delivery. The customer', 'from delivery.\nThe customer'))
        state = self.state()
        self.assertEqual(state['state'], 'relocated')
        self.assertIn('whitespace', state['reason'])

    def test_changed_number_is_a_flagged_proposal_not_a_binding(self):
        self.rewrite('policy/vega.md', lambda t: t.replace('thirty days', 'fourteen days'))
        state = self.state()
        self.assertEqual(state['state'], 'drifted')
        self.assertIn('number', state['flags'])
        self.assertGreater(state['similarity'], 0.9)  # high similarity, reversed meaning
        item = next(i for i in memory.related(self.index, [state['ref']])['items'] if i['type'] == 'note')
        self.assertEqual(item['binding'], 'drifted')
        self.assertIn('WRITTEN FOR OTHER TEXT', memory.format_related(memory.related(self.index, [state['ref']])))

    def test_negation_and_modal_changes_are_named(self):
        self.rewrite('policy/vega.md', lambda t: t.replace('must present', 'may not present'))
        self.assertLessEqual({'negation', 'modality'}, set(self.state()['flags']))

    def test_same_text_under_a_renamed_heading_needs_review(self):
        self.rewrite('policy/vega.md', lambda t: t.replace('## Vega return window', '## Withdrawn: Vega return window'))
        state = self.state()
        self.assertEqual((state['state'], state['flags']), ('drifted', ['context']))

    def test_same_passage_in_another_file_needs_review(self):
        (self.source / 'archive').mkdir()
        (self.source / 'policy/vega.md').rename(self.source / 'archive/vega.md')
        update(self.source, self.index)
        state = self.state()
        self.assertEqual((state['state'], state['flags']), ('drifted', ['path']))

    def test_removed_passage_is_not_reattached(self):
        self.rewrite('policy/vega.md', lambda t: '# Returns\n\n## Vega shipping zones\n\nShipping zones are listed by courier.\n')
        state = self.state()
        self.assertEqual(state['state'], 'stale')
        self.assertIsNone(state['ref'])

    def test_duplicated_text_is_ambiguous(self):
        body = RULE.split('## Vega return window\n\n')[1].split('\n\n## Vega shipping')[0]
        self.rewrite('policy/vega.md', lambda t: t.replace('## Vega return window', '## Draft\n\n' + body + '\n\n## Final'))
        state = self.state()
        self.assertEqual(state['state'], 'stale')
        self.assertIn('ambiguous', state['flags'])

    def test_person_accepts_a_proposal_and_history_is_kept(self):
        self.rewrite('policy/vega.md', lambda t: t.replace('thirty days', 'fourteen days'))
        with self.assertRaises(AtlasError):
            memory.accept_rebind(self.index, 1, ' ')
        result = memory.accept_rebind(self.index, 1, 'Dana Reviewer')
        self.assertEqual(result['accepted'][0]['flags'], ['number'])
        note = memory.notes(self.index)[0]
        self.assertEqual(note['bindings'][0]['state'], 'exact')
        history = note['explicit_refs']['sources'][0]['history']
        self.assertEqual((history[0]['accepted_by'], history[0]['flags']), ('Dana Reviewer', ['number']))
        self.assertIn('fourteen days', note['explicit_refs']['sources'][0]['quote'])
        self.assertEqual(events.read(self.index)['events'][-1]['kind'], 'binding_accepted')
        with self.assertRaises(AtlasError):
            memory.accept_rebind(self.index, 1, 'Dana Reviewer')

    def test_review_lists_only_unverified_bindings(self):
        self.assertEqual(memory.review(self.index), [])
        self.rewrite('policy/vega.md', lambda t: t.replace('thirty days', 'fourteen days'))
        pending = memory.review(self.index)
        self.assertEqual(pending[0]['accept_command'], 'notes --rebind 1')

    def test_binding_from_an_older_release_gains_its_quote_before_the_source_changes(self):
        with connect(self.index, write=True) as db:
            refs = json.loads(db.execute('SELECT explicit_refs FROM shared_notes').fetchone()[0])
            for item in refs['sources']:
                del item['quote'], item['crumbs']
            db.execute('UPDATE shared_notes SET explicit_refs=?', (json.dumps(refs),))
        self.rewrite('policy/vega.md', lambda t: t + '\nAppendix line.\n')
        self.assertEqual(self.state()['state'], 'relocated')

    def test_similarity_is_share_of_old_words_found_in_order(self):
        old = reconcile.tokens('the window is thirty days from delivery')
        new = reconcile.tokens('intro words the window is fourteen days from delivery more words')
        score, opcodes = reconcile.recall(old, new)
        self.assertAlmostEqual(score, 6 / 7)
        self.assertEqual(reconcile.normative_delta(old, opcodes, new), ['number'])


class RelationTests(Workspace):
    documents = {
        'manuals/vega.md': RULE,
        'manuals/lyra.md': '# Lyra\n\n## Lyra battery disposal\n\nReturn used Lyra batteries to a collection point.\n',
        'updates/notice.md': ('# Notices\n\n## Engraved items\n\nNotwithstanding the Vega return window, engraved '
                              'items cannot be returned after seven days.\n\n## Recycling\n\nFor collection points see '
                              '[the Lyra manual](../manuals/lyra.md) before shipping anything.\n\n## Overview\n\n'
                              'As described in Overview, nothing changes for other products.\n'),
    }

    def test_sentence_relations_english_and_italian(self):
        cases = {
            'The refund is granted unless the seal is broken.': ('exception', 'the seal is broken'),
            'If the device is damaged, open a claim within two days.': ('condition', 'the device is damaged'),
            'Access requires approval from the project owner.': ('precondition', 'approval from the project owner'),
            'This discount applies only to business accounts in Europe.': ('scope', 'business accounts in Europe'),
            'This notice supersedes the circular of last March.': ('supersedes', 'the circular of last March'),
            'Il rimborso è ammesso salvo che il sigillo sia rotto.': ('exception', 'che il sigillo sia rotto'),
            'La procedura si applica solo ai contratti aziendali.': ('scope', 'ai contratti aziendali'),
            'La presente circolare abroga la nota precedente sui resi.': ('supersedes', 'la nota precedente sui resi'),
            'Qualora il cliente sia assente, lasciare un avviso scritto.': ('condition', 'il cliente sia assente'),
        }
        for sentence, (relation, condition) in cases.items():
            found = analyze(sentence)
            self.assertEqual((found['relation'], found['condition']), (relation, condition), sentence)
        self.assertIsNone(analyze('The technician replaces the filter every month.'))
        self.assertTrue(analyze('Engraved items cannot be returned unless defective.')['negated'])

    def edges(self):
        with connect(self.index) as db:
            return [dict(r) | {'evidence': json.loads(r['evidence'])} for r in db.execute(
                'SELECT d.*,s1.path src_path,s2.path dst_path FROM dependencies d JOIN nodes a ON a.id=d.src '
                'JOIN sources s1 ON s1.id=a.sid JOIN nodes b ON b.id=d.dst JOIN sources s2 ON s2.id=b.sid')]

    def test_cited_heading_and_link_become_reference_edges(self):
        references = [e for e in self.edges() if e['origin'] == 'reference']
        forward = {(e['src_path'], e['dst_path'], e['kind'], e['evidence'].get('resolved_by')) for e in references}
        self.assertIn(('updates/notice.md', 'manuals/vega.md', 'riferimento_esplicito', 'unique heading title'), forward)
        self.assertIn(('updates/notice.md', 'manuals/lyra.md', 'riferimento_esplicito', 'markdown link'), forward)
        # The citing sentence is an exception, so the cited rule points back at it.
        back = [e for e in references if e['src_path'] == 'manuals/vega.md' and e['dst_path'] == 'updates/notice.md']
        self.assertEqual([(e['kind'], e['relation']) for e in back], [('eccezione_deroga', 'exception')])

    def test_generic_or_same_section_title_is_not_resolved(self):
        self.assertFalse([e for e in self.edges() if e['origin'] == 'reference' and e['anchor'] == 'overview'])

    def test_evidence_span_and_hash_match_the_indexed_text(self):
        edges = self.edges()
        self.assertTrue(edges)
        with connect(self.index) as db:
            for edge in edges:
                evidence = edge['evidence']
                holder = db.execute('SELECT * FROM nodes WHERE id=?', (edge['dst'] if evidence['cue_in'] == reference(db, edge['dst']) else edge['src'],)).fetchone()
                source = db.execute('SELECT * FROM sources WHERE id=?', (holder['sid'],)).fetchone()
                a, b = evidence['sentence_bytes']
                self.assertTrue(holder['start'] <= a < b <= holder['end'])
                raw = read_bytes(db, self.index, source, a, b)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), evidence['sentence_sha256'])

    def test_related_output_carries_relation_origin_and_evidence(self):
        rule = self.ref('manuals/vega.md', 'Vega return window')
        items = [i for i in memory.related(self.index, [rule], 50)['items'] if i['type'] == 'dependency']
        exception = next(i for i in items if i['origin'] == 'reference')
        self.assertEqual((exception['relation'], exception['path']), ('exception', 'updates/notice.md'))
        self.assertIn('sentence_sha256', exception['evidence'])
        self.assertIn('reference]', memory.format_related(memory.related(self.index, [rule], 50)))


class SearchTests(Workspace):
    documents = {'a.md': '# Regole\n\nLe eccezioni ai rimborsi sono elencate qui. Returned parcels are weighed.\n'}

    def test_inflected_query_matches_and_says_so(self):
        for query in ('eccezione rimborso', 'returning parcel'):
            data = find(self.index, query, as_json=True)
            self.assertEqual(len(data['risultati']), 1, query)
            self.assertIn('VARIANTI', data['modalita'])
        self.assertEqual(find(self.index, 'eccezioni rimborsi', as_json=True)['modalita'], 'tutti i termini')

    def test_enumeration_stays_exact(self):
        self.assertEqual(find(self.index, 'eccezione rimborso', as_json=True, all_matches=True)['totale_passaggi'], 0)

    def test_stem_prefix_is_conservative(self):
        self.assertEqual([stem_prefix(w) for w in ('policies', 'eccezione', 'rule', 'iso9001', 'città')],
                         ['polic', 'eccezion', 'rule', 'iso9001', 'città'])


class JournalAndMergeTests(Workspace):
    def test_chain_records_mutations_and_detects_tampering(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        memory.add_note(self.index, 'First observation.', 'One', [rule], author='ana')
        memory.revoke(self.index, 1, 'ben')
        log = events.read(self.index)
        self.assertEqual([e['kind'] for e in log['events']], ['index_built', 'graph_built', 'note_added', 'note_revoked'])
        self.assertEqual([e['actor'] for e in log['events'][2:]], ['ana', 'ben'])
        self.assertEqual(events.read(self.index, since=3)['events'][0]['seq'], 4)
        self.assertTrue(events.verify(self.index)['intact'])
        with connect(self.index, write=True) as db:
            db.execute("UPDATE events SET actor='mallory' WHERE seq=3")
        self.assertEqual(events.verify(self.index), {'events': 2, 'intact': False, 'broken_at': 3})

    def test_failed_mutation_leaves_no_event(self):
        before = events.read(self.index)['last_seq']
        with self.assertRaises(AtlasError):
            memory.add_note(self.index, 'Never saved.', sources=['n999-bad00000'])
        self.assertEqual(events.read(self.index)['last_seq'], before)

    def other_workspace(self):
        other = self.root / 'other'
        update(self.source, other)
        return other

    def test_import_is_idempotent_content_addressed_and_revocation_wins(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        memory.add_note(self.index, 'Bound observation.', 'Bound', [rule], status='confirmed')
        memory.add_note(self.index, 'Vega return window: unanchored observation.', 'Vega return window')
        other = self.other_workspace()
        first = memory.import_notes(other, self.index / 'memory.json')
        self.assertEqual((first['imported'], first['to_review']), (2, 0))
        notes = memory.notes(other)
        self.assertEqual([n['status'] for n in notes], ['proposed', 'proposed'])  # a remote "confirmed" is only a label
        self.assertEqual(notes[0]['bindings'][0]['state'], 'exact')              # same bytes, same address
        self.assertGreater(notes[1]['linked_passages'], 0)
        self.assertEqual([n['uid'] for n in notes], [n['uid'] for n in memory.notes(self.index)])
        again = memory.import_notes(other, self.index / 'memory.json')
        self.assertEqual((again['imported'], again['already_present']), (0, 2))
        memory.revoke(self.index, 1)
        merged = memory.import_notes(other, self.index / 'memory.json')
        self.assertEqual(merged['revocations_applied'], 1)
        self.assertEqual(memory.notes(other)[0]['status'], 'revoked')
        # The other direction never resurrects: importing a live copy over a revoked note changes nothing.
        back = memory.import_notes(self.index, other / 'memory.json')
        self.assertEqual((back['imported'], back['revocations_applied']), (0, 0))
        self.assertEqual(memory.notes(self.index)[0]['status'], 'revoked')

    def test_import_into_a_workspace_with_edited_sources_goes_to_review(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        memory.add_note(self.index, 'Bound observation.', 'Bound', [rule])
        export = self.root / 'export.json'
        export.write_bytes((self.index / 'memory.json').read_bytes())
        self.rewrite('policy/vega.md', lambda t: t.replace('thirty days', 'ten days'))
        other = self.other_workspace()
        result = memory.import_notes(other, export)
        self.assertEqual(result['to_review'], 1)
        self.assertEqual(memory.notes(other)[0]['bindings'][0]['state'], 'drifted')

    def test_malformed_export_imports_nothing(self):
        bad = self.root / 'bad.json'
        bad.write_text(json.dumps({'schema': 'synapse-kb-memory-2', 'notes': [
            {'created_at': 'x', 'author': 'a', 'title': 't', 'body': 'b', 'kind': 'appunto_condiviso', 'status': 'proposed',
             'explicit_refs': {'sources': [], 'targets': [], 'auto': True}},
            {'created_at': 'x', 'author': 'a', 'title': 't', 'body': 'b', 'kind': 'nonsense', 'status': 'proposed',
             'explicit_refs': {'sources': [], 'targets': [], 'auto': True}}]}), encoding='utf-8')
        with self.assertRaises(AtlasError):
            memory.import_notes(self.index, bad)
        self.assertEqual(memory.notes(self.index), [])

    def test_automatic_links_equal_a_full_scan(self):
        put(self.source / 'b.md', '# Other\n\n## Receipts\n\nThe original receipt is scanned. Return. Window closed.\n')
        update(self.source, self.index)
        memory.add_note(self.index, 'Original receipt and the Vega return window matter; service desk too.', 'Check')
        with connect(self.index) as db:
            anchors = set(json.loads(db.execute('SELECT anchors FROM shared_notes').fetchone()[0]))
            expected = set()
            for row in db.execute('SELECT * FROM nodes WHERE kind="passage"').fetchall():
                source = db.execute('SELECT * FROM sources WHERE id=?', (row['sid'],)).fetchone()
                body = read_bytes(db, self.index, source, row['start'], row['end']).decode('utf-8')
                if anchors & set(extract_phrases(' '.join(json.loads(row['crumbs'])) + '\n' + body)):
                    expected.add(row['id'])
            linked = {r[0] for r in db.execute('SELECT node_id FROM note_links')}
        self.assertTrue(expected)
        self.assertEqual(linked, expected)


class ConcurrencyTests(Workspace):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(BASE / 'atlas.py'), *args, '--indice', str(self.index)],
                              capture_output=True, text=True, encoding='utf-8')

    def test_reading_does_not_wait_for_a_writer(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        with operation_lock(self.index):
            self.assertEqual(self.cli('read', rule).returncode, 0)
            self.assertEqual(self.cli('search', 'receipt').returncode, 0)
            blocked = subprocess.run([sys.executable, str(BASE / 'atlas.py'), 'build', str(self.source), '--output', str(self.index)],
                                     capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(blocked.returncode, 2)
            self.assertIn('writer', blocked.stderr)

    def test_reader_keeps_its_snapshot_during_an_open_write_transaction(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        with connect(self.index, write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM nodes')
            self.assertIn('thirty days', read(self.index, [rule]))
            db.rollback()

    def test_parallel_writers_queue_and_all_commit(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        results = []
        def write(number):
            results.append(self.cli('annotate', f'Observation {number}.', '--title', f'Agent {number}',
                                    '--collega', rule, '--author', f'agent-{number}'))
        threads = [threading.Thread(target=write, args=(n,)) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([r.returncode for r in results], [0] * 6, [r.stderr for r in results])
        self.assertEqual(len(memory.notes(self.index)), 6)
        self.assertTrue(events.verify(self.index)['intact'])
        self.assertEqual(sum(e['kind'] == 'note_added' for e in events.read(self.index)['events']), 6)

    def test_index_uses_write_ahead_log(self):
        with contextlib.closing(sqlite3.connect(self.index / 'atlas.sqlite')) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')


class McpTests(Workspace):
    def setUp(self):
        super().setUp()
        self.process = subprocess.Popen([sys.executable, str(BASE / 'atlas.py'), 'serve', '--indice', str(self.index)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop)
        self.next_id = 0

    def stop(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        self.process.stdout.close()
        self.process.stderr.close()

    def send(self, method, params=None, notify=False):
        message = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if not notify:
            self.next_id += 1
            message['id'] = self.next_id
        self.process.stdin.write(json.dumps(message).encode() + b'\n')
        self.process.stdin.flush()

    def receive(self):
        line = self.process.stdout.readline()
        self.assertTrue(line, self.process.stderr.read1().decode() if self.process.poll() is not None else 'no output')
        return json.loads(line)

    def request(self, method, params=None):
        self.send(method, params)
        while True:
            message = self.receive()
            if message.get('id') == self.next_id:
                return message

    def call(self, name, **arguments):
        return self.request('tools/call', {'name': name, 'arguments': arguments})['result']

    def test_handshake_tools_and_errors(self):
        hello = self.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                            'clientInfo': {'name': 'test', 'version': '0'}})['result']
        self.assertEqual(hello['protocolVersion'], '2025-06-18')
        self.assertTrue(hello['capabilities']['resources']['subscribe'])
        self.assertEqual(self.request('initialize', {'protocolVersion': '2099-01-01'})['result']['protocolVersion'], '2025-06-18')
        self.send('notifications/initialized', notify=True)
        self.assertEqual(self.request('ping')['result'], {})
        tools = {t['name']: t for t in self.request('tools/list')['result']['tools']}
        self.assertNotIn('update', tools)  # started without a workspace
        self.assertTrue(tools['read']['annotations']['readOnlyHint'])
        self.assertFalse(tools['annotate']['annotations']['readOnlyHint'])
        self.assertEqual(self.request('no/such')['error']['code'], -32601)
        self.assertEqual(self.request('tools/call', {})['error']['code'], -32602)
        self.process.stdin.write(b'not json\n')
        self.process.stdin.flush()
        self.assertEqual(self.receive()['error']['code'], -32700)

    def test_tool_results_equal_the_cli_contract(self):
        found = self.call('search', query='Vega return window')
        self.assertFalse(found['isError'])
        reference_ = found['structuredContent']['risultati'][0]['ref']
        self.assertEqual(json.loads(found['content'][0]['text']), found['structuredContent'])
        record = self.call('read', references=[reference_], neighbors=1)['structuredContent']['records'][0]
        self.assertEqual(record, read(self.index, [reference_], as_json=True, neighbors=1)[0] | {'related': record['related']})
        self.assertEqual(len(record['sha256']), 64)
        failure = self.call('read', references=['n999-deadbeef'])
        self.assertTrue(failure['isError'])
        self.assertTrue(self.call('read', references=[reference_], neighbors=9)['isError'])
        self.assertTrue(self.call('nonexistent')['isError'])
        self.assertTrue(self.call('events', verify=True)['structuredContent']['intact'])

    def test_subscription_announces_a_change_made_by_another_process(self):
        self.request('initialize', {'protocolVersion': '2025-06-18'})
        self.assertEqual(len(self.request('resources/list')['result']['resources']), 3)
        self.assertEqual(self.request('resources/subscribe', {'uri': 'synapse://nothing'})['error']['code'], -32602)
        self.assertEqual(self.request('resources/subscribe', {'uri': 'synapse://notes'})['result'], {})
        rule = self.ref('policy/vega.md', 'Vega return window')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['annotate', 'From another agent.', '--collega', rule, '--indice', str(self.index)]), 0)
        message = self.receive()
        self.assertEqual((message['method'], message['params']), ('notifications/resources/updated', {'uri': 'synapse://notes'}))
        contents = self.request('resources/read', {'uri': 'synapse://notes'})['result']['contents'][0]
        self.assertEqual(json.loads(contents['text'])['notes'][0]['body'], 'From another agent.')

    def test_write_tools_record_the_actor(self):
        rule = self.ref('policy/vega.md', 'Vega return window')
        saved = self.call('annotate', text='Check the receipt.', title='Receipt', references=[rule], author='agent-7')
        self.assertEqual(saved['structuredContent']['status'], 'proposed')
        self.assertTrue(self.call('accept_rebind', id=1, reviewer='Dana')['isError'])  # nothing drifted
        self.call('revoke_note', id=1, author='agent-7')
        self.assertEqual([e['actor'] for e in events.read(self.index, 2)['events']], ['agent-7', 'agent-7'])


class VisualTests(Workspace):
    documents = {
        'Lavoro/Progetti: 2026/piano.md': '# Piano annuale\n\nThe Vega return window is cited here; see Vega return window.\n',
        'wiki/vega.md': RULE,
        'sciolto.txt': 'Text without headings and without a folder.\n',
    }

    def vault(self):
        return self.index / 'visuals' / 'obsidian'

    def files(self):
        return sorted(p.relative_to(self.vault()).as_posix() for p in self.vault().rglob('*.md'))

    def test_vault_mirrors_folders_with_readable_names_and_tags(self):
        files = self.files()
        self.assertIn('Lavoro/Progetti 2026/piano — Piano annuale.md', files)
        self.assertIn('Lavoro/Progetti 2026/piano (md).md', files)
        self.assertIn('wiki/vega — Returns.md', files)
        self.assertIn('sciolto — text.md', files)
        self.assertFalse([f for f in files if 'section-' in f])
        note = (self.vault() / 'Lavoro/Progetti 2026/piano — Piano annuale.md').read_text(encoding='utf-8')
        self.assertIn('tags: ["Lavoro/Progetti-2026"]', note)
        self.assertIn('Document: [[Lavoro/Progetti 2026/piano (md)|piano.md]]', note)
        self.assertIn('[[wiki/vega — Returns|Returns]]', note)  # the stated reference, by readable name
        hub = (self.vault() / 'wiki/vega (md).md').read_text(encoding='utf-8')
        self.assertIn('- [[wiki/vega — Returns|Returns]]', hub)
        groups = json.loads((self.vault() / '.obsidian/graph.json').read_text(encoding='utf-8'))['colorGroups']
        self.assertEqual([g['query'] for g in groups], ['path:"Lavoro"', 'path:"wiki"'])

    def test_vault_follows_renames_and_keeps_what_a_person_wrote(self):
        from atlas_core.visuals import export_graph
        mine = self.vault() / 'wiki' / 'vega — Returns 2.md'
        mine.write_text('mine', encoding='utf-8')
        settings = self.vault() / '.obsidian' / 'graph.json'
        settings.write_text('{"mine": true}', encoding='utf-8')
        collision = self.vault() / 'wiki' / 'vega — Refunds.md'
        collision.write_text('written by a person before the heading was renamed', encoding='utf-8')
        self.rewrite('wiki/vega.md', lambda t: t.replace('# Returns', '# Refunds'))
        files = self.files()
        self.assertNotIn('wiki/vega — Returns.md', files)
        self.assertEqual(collision.read_text(encoding='utf-8'), 'written by a person before the heading was renamed')
        self.assertTrue([f for f in files if f.startswith('wiki/vega — Refunds ') and f != 'wiki/vega — Refunds.md'])
        self.assertEqual(mine.read_text(encoding='utf-8'), 'mine')
        self.assertEqual(settings.read_text(encoding='utf-8'), '{"mine": true}')
        (self.source / 'Lavoro/Progetti: 2026/piano.md').unlink()
        update(self.source, self.index)
        self.assertFalse((self.vault() / 'Lavoro').exists())
        export_graph(self.index)
        self.assertEqual(self.files(), sorted(set(self.files())))

    def test_graph_snapshot_has_folder_and_document_hubs(self):
        data = json.loads((self.index / 'visuals/graph.json').read_text(encoding='utf-8'))
        for level in ('macro', 'micro'):
            nodes = {n['id']: n for n in data[level]['nodes']}
            self.assertEqual(sorted(n['label'] for n in nodes.values() if n['type'] == 'folder'), ['Lavoro', 'Root', 'wiki'])
            self.assertEqual(sum(n['type'] == 'document' for n in nodes.values()), 3)
            structure = [e for e in data[level]['edges'] if e['kind'] == 'struttura']
            leaves = {n['id'] for n in nodes.values() if n['type'] in ('section', 'passage')}
            self.assertEqual({e['target'] for e in structure if e['target'] in leaves}, leaves)  # nothing floats alone
            self.assertTrue(all(e['source'] in nodes and e['target'] in nodes for e in data[level]['edges']))
            self.assertEqual(data[level]['total_edges'], sum(e['kind'] != 'struttura' for e in data[level]['edges']))


class DocxTableTests(unittest.TestCase):
    def test_table_row_stays_on_one_line(self):
        ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        cell = lambda text: f'<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'table.docx'
            zip_parts(path, {'word/document.xml': f'<w:document xmlns:w="{ns}"><w:body><w:p><w:r><w:t>Fees</w:t></w:r></w:p>'
                             f'<w:tbl><w:tr>{cell("Zone")}{cell("Fee")}</w:tr><w:tr>{cell("North")}{cell("12 EUR")}</w:tr></w:tbl>'
                             f'<w:p><w:r><w:t>After</w:t></w:r></w:p></w:body></w:document>'})
            warnings = []
            pieces = list(extract(path, warnings))
        self.assertEqual([p.text for p in pieces], ['Fees\n', 'Zone | Fee\n', 'North | 12 EUR\n', 'After\n'])
        self.assertEqual(pieces[2].location, 'tabella 1, riga 2')


if __name__ == '__main__':
    unittest.main()
