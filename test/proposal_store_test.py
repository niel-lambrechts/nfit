"""Golden-style parity for each tactical model, with a local synthetic cache."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import hashlib
import json
import shutil
import unittest

from one_entry_point_test import mx, run

loader = SourceFileLoader('golden', str(Path(__file__).resolve().parents[1] / 'dev/golden'))
spec = spec_from_loader(loader.name, loader)
golden = module_from_spec(spec)
loader.exec_module(golden)


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class ProposalStoreTests(unittest.TestCase):
    def test_model_outputs_and_scopes(self):
        mx.MemberExposureTests.roots = []
        root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, root)
        cache = root / 'stage' / mx.SERIAL
        for model, flags in golden.RUNS.items():
            for f in (root / 'output').glob('*.csv'):
                f.unlink()
            run(str(root), str(cache), *flags)
            csv_file, = (root / 'output').glob('*.csv')
            self.assertIsNone(golden.check_proposals(cache, model, csv_file.read_text()))
        base = cache / '.nfit.store/proposals/v1/state-based'
        before, = base.glob('*.json')
        self.assertEqual(before.stat().st_mode & 0o077, 0)
        flags = golden.RUNS['forensic']
        run(str(root), str(cache), *flags)
        self.assertEqual(list(base.glob('*.json')), [before])
        run(str(root), str(cache), *flags, '--vm', 'vmA')
        after, = set(base.glob('*.json')) - {before}
        subset = json.loads(after.read_bytes())
        self.assertEqual(list(subset['members']), ['vmA'])
        self.assertEqual(subset['settings']['vmFilter'], 'vmA')
        self.assertGreater(len(json.loads(before.read_bytes())['members']), 1)

    def test_current_cycle_pair_is_stored_and_schema_1_stays_readable(self):
        mx.MemberExposureTests.roots = []
        root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, root)
        cache = root / 'stage' / mx.SERIAL
        run(str(root), str(cache), *golden.RUNS['forensic'])
        base = cache / '.nfit.store/proposals/v1/state-based'
        path, = base.glob('*.json')
        snapshot = json.loads(path.read_bytes())
        self.assertEqual(snapshot['schemaVersion'], 2)
        seen = 0
        for vm, member in snapshot['members'].items():
            for name, profile in member['profiles'].items():
                pair = profile['currentCycle']
                self.assertIn(pair['status'], ('HOLD', 'NO_CHANGE', 'APPLY', 'STAGED', 'BLOCKED'))
                if pair['status'] == 'HOLD':
                    continue
                seen += 1
                # Every stored pair is one PowerVM accepts: 0.05 x VPs <= ENT <= VPs.
                self.assertLessEqual(pair['entitlement'], pair['VPs'] + 1e-9, (vm, name))
                self.assertGreaterEqual(pair['entitlement'], 0.05 * pair['VPs'] - 1e-9, (vm, name))
                # VPs are the Tactical step unless the target entitlement needs more.
                self.assertGreaterEqual(pair['VPs'], profile['tacticalVPs'], (vm, name))
                if pair['VPs'] > profile['tacticalVPs']:
                    self.assertIn('VP_WIDTH', pair['binding'], (vm, name))
        self.assertGreater(seen, 0)

        # A schema-1 snapshot (without the pair) is still accepted for a replay.
        for member in snapshot['members'].values():
            for profile in member['profiles'].values():
                del profile['currentCycle']
        snapshot['schemaVersion'] = 1
        legacy_bytes = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
        legacy = base / (hashlib.sha256(legacy_bytes).hexdigest() + '.json')
        legacy.write_bytes(legacy_bytes)
        result = run(str(root), str(cache), '--candidate-proposals', str(legacy))
        self.assertEqual(result.returncode, 0, result.stderr[-400:])


if __name__ == '__main__':
    unittest.main()
