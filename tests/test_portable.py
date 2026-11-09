import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures import mixed

BASE = Path(__file__).resolve().parents[1]


class PortableTests(unittest.TestCase):
    def test_offline_bundle_without_site_packages(self):
        executable = BASE / 'dist' / 'atlas.pyz'
        self.assertTrue(executable.is_file(), 'Eseguire prima python pack.py')
        with tempfile.TemporaryDirectory(prefix='atlas_portable_') as tmp:
            base = Path(tmp)
            root = mixed(base / 'Cartella sconosciuta à 文')
            tool = base / 'Strumento portatile.pyz'
            shutil.copy2(executable, tool)
            def run(*args):
                return subprocess.run([sys.executable, '-S', str(tool), *args], cwd=root,
                                      capture_output=True, text=True, encoding='utf-8', timeout=60)
            built = run('prepara', '.', '--quiet')
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertEqual(json.loads(built.stdout)['fonti'], 13)
            status = run('stato', '--json')
            missing = [s for s in json.loads(status.stdout)['fonti'] if s['status'] == 'non_estratto']
            self.assertEqual([s['path'] for s in missing], ['legacy.doc'])
            found = run('cerca', 'ORBIT 734', '--json')
            hit = json.loads(found.stdout)['risultati'][0]
            evidence = run('leggi', hit['ref'], '--vicini', '1')
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            self.assertIn('ORBIT-734', evidence.stdout)
            page = run('esplora')
            self.assertLessEqual(len(page.stdout.rstrip('\n')), 1600)
            original = root / hit['fonte']
            original.write_bytes(original.read_bytes() + b'\n% revised\n')
            stale = run('leggi', hit['ref'])
            self.assertEqual(stale.returncode, 2)
            self.assertIn('cambiata', stale.stderr)


if __name__ == '__main__':
    unittest.main()
