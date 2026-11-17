#!/usr/bin/env python3
# ==============================================================================
# Shadow decision table and estate summary (v10 milestone 2.5).
#   - A --candidate-test run writes output/...decisions.<date>.csv beside its
#     CSV: one row per LPAR, whose figures agree with the rationale sections
#     (the cut exposure, the pool reconciliation's budgets, the pressure remedy).
#   - A VP-width remedy whose busy work at the planning level is under half the
#     VPs is marked weak, in the table and the rationale.
#   - A cache without pool observations gives a table of LPARs not assessed,
#     with the reason.
#   - dev/estate-impact combines the tables and names the system not assessed
#     and the expected system without a table.
# ==============================================================================
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from one_entry_point_test import mx, run, sf

REPO = Path(__file__).resolve().parent.parent


def table(root):
    path, = (Path(root) / 'output').glob('*.decisions.*.csv')
    with path.open(newline='') as fh:
        return path, {r['vm']: r for r in csv.DictReader(fh)}


def log_text(root):
    log, = [p for p in (Path(root) / 'output').glob('*.log') if 'state-based' in p.name]
    return log.read_text()


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class ShadowDecisionsTest(unittest.TestCase):

    def make(self, ent=None):
        mx.MemberExposureTests.roots = []
        root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, root)
        cache = root / 'stage' / mx.SERIAL
        if ent:
            f = cache / '.nfit.cache.states'
            states = json.loads(f.read_bytes())
            for vm, value in ent.items():
                states[vm][0]['metadata']['entitlement'] = value
            f.write_text(json.dumps(states))
        cfg = root / 'vms.csv'
        with cfg.open('w') as fh:
            w = csv.writer(fh)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm in ('vmA', 'vmB', 'vmS'):
                e, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, 'AIX', 'pool', pool, (ent or {}).get(vm, e), vp])
        run(str(root), str(cache), '--config', str(cfg), '--candidate-test', '--runq-perc-behavior=match')
        return root

    def test_rows_agree_with_the_rationale(self):
        root = self.make()
        path, rows = table(root)
        self.assertRegex(path.name, r'^nfit-profile\.SYNEX\.state-based\.decisions\.\d{8}.*\.csv$')
        self.assertEqual(sorted(rows), ['vmA', 'vmB', 'vmS', 'vmX'])
        self.assertEqual(rows['vmX']['status'], 'not assessed: role not known in the VM configuration')
        text = ' '.join(log_text(root).split())
        for vm in ('vmA', 'vmB', 'vmS'):
            r = rows[vm]
            self.assertEqual(r['status'], 'assessed')
            self.assertEqual((r['model'], r['system'], r['replayStart'], r['replayEnd']), ('state-based', 'SYNEX', '2026-09-01', '2026-09-02'))
            change = float(r['proposedEntitlement']) - float(r['installedEntitlement'])
            self.assertAlmostEqual(float(r['change']), change, places=2)
        # Pool reconciliation: the budget B line names the same entitlements.
        b_line = re.search(r'B: vmA (\S+) \([^)]*\), vmB (\S+) \(', text)
        self.assertEqual((rows['vmA']['budgetB'], rows['vmB']['budgetB']), b_line.groups())
        self.assertIn(f"Exposed together: all 2: {rows['vmA']['poolAllExposedMinutes']};", text)
        # Candidate test at the planning-profile proposal.
        line = re.search(r'vmB pool 7, all hours.*?\n?\s*' + re.escape(rows['vmB']['proposedEntitlement']) +
                         r' G3-95W15/proposal[^|]*\| Above (\d+)/[^|]*\| Full (\d+)/', log_text(root).replace('\n', ' '))
        self.assertEqual((rows['vmB']['aboveMinutes'], rows['vmB']['fullMinutesAbove']), line.groups())
        self.assertEqual(rows['vmB']['remedy'], 'within its guarantee at the planning level')

    def test_weak_vp_evidence_is_marked(self):
        root = self.make(ent={'vmS': 0.2})
        _, rows = table(root)
        r = rows['vmS']
        self.assertEqual((r['remedy'], r['evidence']), ('review the VP width and the pool together', 'weak'))
        self.assertLess(float(r['planningLevel']), 0.5 * int(r['installedVPs']))
        self.assertIn('Evidence: weak - PhysC reached its 2 VPs, but busy work at the planning level is 0.30 cores.',
                      ' '.join(log_text(root).split()))

    def test_cache_without_pool_observations_and_the_estate_summary(self):
        tactical = self.make()
        legacy = Path(tempfile.mkdtemp(prefix='decisions-legacy-'))
        self.addCleanup(shutil.rmtree, legacy)
        cache, _ = sf.build_root(str(legacy))
        for prog in ('nfit', 'nfit-profile'):
            os.chmod(legacy / prog, 0o755)
        run(str(legacy), cache, '--candidate-test', '--runq-perc-behavior=match')
        _, rows = table(legacy)
        self.assertTrue(rows)
        self.assertEqual({r['status'] for r in rows.values()}, {'not assessed: the cache holds no pool observations'})

        combined = Path(tempfile.mkdtemp(prefix='estate-'))
        self.addCleanup(shutil.rmtree, combined)
        for root in (tactical, legacy):
            src, _ = table(root)
            shutil.copy(src, combined / src.name)
        p = subprocess.run([str(REPO / 'dev' / 'estate-impact'), '--output-dir', str(combined), '--expect', f'SYNEX,{sf.SERIAL},SYNZZ',
                            '--out', str(combined / 'estate.csv')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        self.assertIn('Tables: 2 system(s)', out)
        self.assertIn('Expected but without a table: SYNZZ.', out)
        self.assertIn(f'Systems not assessed: {sf.SERIAL} (state-based): the cache holds no pool observations.', out)
        _, tactical_rows = table(tactical)
        cuts = [r for r in tactical_rows.values() if r['status'] == 'assessed' and float(r['change']) < -0.005]
        released = sum(-float(r['change']) for r in cuts)
        self.assertIn(f'{len(cuts)} cut(s); guarantee released at the proposals: {released:.2f} cores.', out)
        within_b = sum(max(0.0, float(r['installedEntitlement']) - float(r['budgetB'])) for r in cuts if r['budgetB'])
        self.assertIn(f'Released within budget B: {within_b:.2f} cores', out)
        with (combined / 'estate.csv').open(newline='') as fh:
            self.assertEqual(len(list(csv.DictReader(fh))), len(tactical_rows) + len(rows))


if __name__ == '__main__':
    unittest.main()
