"""dev/pool-evidence-report (exploratory; v10 stage 3.4): the coarse pool facts of the
training period beside the held-out outcome of the cuts normal planning releases.
Run on the member-exposure fixture: a coarse sizing and its replay give the records;
the report reuses the coarse copy that run left, runs no sizing and names no LPAR."""
import csv
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
import member_exposure_test as mx

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..', 'dev', 'pool-evidence-report'))
DATES = ('2026-09-01', '2026-09-01', '2026-09-02', '2026-09-02')


def load():
    loader = importlib.machinery.SourceFileLoader('pool_evidence_report', TOOL)
    spec = importlib.util.spec_from_loader('pool_evidence_report', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class OutcomeTest(unittest.TestCase):
    def test_scored_and_unscored_cuts_are_kept_apart(self):
        tool = load()
        level = lambda m, c: {'level': 1, 'borrow': {'contested': {'minutes': m, 'coreMinutes': c}}}
        base = {'planning': True, 'pool': 7, 'population': 'none', 'cycleByPolicy': {'normal': {'entitlement': 0.5}, 'strict': {'entitlement': 1.5}}}
        records = [dict(base, later={'cycleNormal': level(10, 2.0), 'cycleStrict': level(0, 0.0), 'cycleReference': level(1, 0.5)}),
                   dict(base, later={'cycleNormal': level(0, 0.0), 'cycleStrict': level(0, 0.0)}),
                   dict(base, later={}),                                                    # a cut that the held-out period did not score
                   dict(base, cycleByPolicy={'normal': {'entitlement': 1.5}, 'strict': {'entitlement': 1.5}}),   # no cut released
                   dict(base, planning=False)]
        g = tool.outcomes(records)[('7', 'none')]
        self.assertEqual((g['lpars'], g['scoredCuts'], g['unscoredCuts'], g['exposedCuts']), (4, 2, 1, 1))
        self.assertEqual((g['scoredCores'], g['unscoredCores'], g['exposedCores']), (2.0, 1.0, 1.0))
        self.assertEqual((g['normalCoreMinutes'], g['strictCoreMinutes'], g['referenceCoreMinutes'], g['referenceScored']), (2.0, 0.0, 0.5, 1))


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class PoolEvidenceReportTest(unittest.TestCase):
    def test_report_reuses_the_coarse_copy_and_names_no_lpar(self):
        tool = load()
        pr = tool.pr
        mx.MemberExposureTests.roots = []
        root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, root)
        source = root / 'stage' / mx.SERIAL
        for name in ('nfit', 'nfit-profile'):
            os.chmod(root / name, 0o700)
        with (root / 'config.csv').open('w') as f:
            w = csv.writer(f)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm, role in [('vmA', 'AIX'), ('vmB', 'VIO Server'), ('vmS', 'PowerHA Standby')]:
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, role, 'pool', pool, ent, vp])
        case = root / '.private' / 'protection-replay' / 'X-01'
        case.mkdir(parents=True)
        pr.THIN.update(minutes=15, mode='mean')
        try:
            snapshot, replay = pr.run_case(root, case, source, DATES, pr.perl_env())
        finally:
            pr.THIN.update(minutes=0, mode='mean')
        entry = pr.anonymise('X-01', snapshot, replay, DATES)
        records = root / 'records'
        records.mkdir()
        (records / 'protection-replay.json').write_text(json.dumps({'thinMinutes': 15, 'thinMode': 'mean', 'commit': 'test', 'records': entry['records']}))
        # The tool reads the capture from <root>/<folder>/stage and works under its repository's .private.
        (root / 'dev').mkdir(exist_ok=True)
        shutil.copy(os.path.join(mx.REPO, 'dev', 'nfit-redact'), root / 'dev' / 'nfit-redact')
        (root / 'cap').symlink_to(root)
        tool.REPO = root
        pr.COHORTS['X'] = ('cap', 1) + DATES
        self.addCleanup(pr.COHORTS.pop, 'X')
        argv = sys.argv
        sys.argv = ['pool-evidence-report', '--system', 'X-01', '--records', str(records), '--capture-root', str(root), '--out', str(root / 'out')]
        try:
            self.assertEqual(tool.main(), 0)
        finally:
            sys.argv = argv
        data = json.loads((root / 'out' / 'pool-evidence.json').read_text())
        self.assertEqual((data['stage'], data['thinMinutes'], data['window']), ('reused', 15, {'start': '2026-09-01', 'end': '2026-09-01'}))
        text = (root / 'out' / 'pool-evidence.md').read_text() + json.dumps(data)
        for vm in mx.CONFIG:
            self.assertNotIn(vm, text)
        self.assertNotIn(mx.SERIAL, text)
        rows = {(r['pool'], r['population']): r for r in data['rows']}
        pool = str(mx.CONFIG['vmS'][2])
        row = next(r for (p, _), r in rows.items() if p == pool)
        facts = row['facts']
        self.assertEqual(facts['cadenceMinutes'], 15)
        self.assertEqual(facts['observations'], 96)
        self.assertGreaterEqual(facts['members'], 1)
        self.assertGreater(facts['virtualProcessors'], 0)
        self.assertIn('max', facts['allHours'])
        # The standby's cut is released by normal planning and held by strict: it is counted, scored or not.
        self.assertGreaterEqual(sum(r['scoredCuts'] + r['unscoredCuts'] for r in data['rows']), 1)
        self.assertFalse((root / '.private' / 'pool-evidence').exists())                  # the work tree is removed


if __name__ == '__main__':
    unittest.main()
