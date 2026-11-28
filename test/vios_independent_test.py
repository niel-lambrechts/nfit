"""VIOS sizing options (v10 B17), through the real analysis on a synthetic cache of 30 days:
two VIOS of one system with unequal workloads (viosA the larger, uncapped; viosB capped), and one
ordinary LPAR. The same data under each policy.

  default       nothing set: match_larger, exactly as with vios_sizing = match_larger.
  combined      each at the sum of the two own targets.
  independent   set once in [GLOBAL]: each VIOS keeps the entitlement, VPs and action of its own
                workload, as any LPAR; the pair is kept for the report; the takeover review is an
                estimate beside the sizing and changes no figure.
  conflict      one VIOS independent, the other left at the default: match_larger applies, and the
                rationale names both settings, their sources and what applies.
"""
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import member_exposure_test as mx
import width_evidence_test as we

LPARS = {'viosA': (2.0, 4, 0), 'viosB': (1.0, 2, 1), 'app': (1.5, 4, 0)}
BASE = '[viosA]\nlpar_type = vios\n[viosB]\nlpar_type = vios\n'
CASES = {'default': BASE, 'combined': '[GLOBAL]\nvios_sizing = combined\n' + BASE, 'match_larger': '[GLOBAL]\nvios_sizing = match_larger\n' + BASE,
         'independent': '[GLOBAL]\nvios_sizing = independent\n' + BASE,
         'conflict': '[viosA]\nlpar_type = vios\nvios_sizing = independent\n[viosB]\nlpar_type = vios\n'}


def minute_of(vm, day, minute):
    busy = 540 <= minute < 600
    if vm == 'viosA':
        return (1.40, 1.20, 3.0) if busy else (0.90, 0.70, 2.0)
    if vm == 'viosB':
        return (0.50, 0.40, 1.0) if busy else (0.30, 0.20, 1.0)
    return 0.80, 0.50, 2.0


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class ViosIndependentTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='vios-independent-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        saved = we.minute_of
        we.minute_of = minute_of
        try:
            cache = we.build(cls.root, 'SYNVI', LPARS)
        finally:
            we.minute_of = saved
        cls.members, cls.rows, cls.log, cls.err, cls.plans, cls.csvs = {}, {}, {}, {}, {}, {}
        for case, config in CASES.items():
            with open(os.path.join(cls.root, 'etc', 'nfit.vms.cfg'), 'w') as fh:
                fh.write(config)
            shutil.rmtree(os.path.join(cache, '.nfit.store', 'proposals'), ignore_errors=True)
            for old in glob.glob(os.path.join(cls.root, 'output', '*')):
                os.remove(old)
            proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache, '--runq-perc-behavior=match'],
                                  cwd=cls.root, capture_output=True, text=True, timeout=280)
            assert proc.returncode == 0, proc.stderr[-1500:]
            cls.err[case] = proc.stderr
            plan, = glob.glob(os.path.join(cache, '.nfit.store', 'proposals', 'v1', 'state-based', '*.json'))
            with open(plan) as fh:
                cls.plans[case] = json.load(fh)
            cls.members[case] = cls.plans[case]['members']
            path, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.csv')) if 'state-based' in f and '.decisions.' not in f]
            cls.csvs[case] = os.path.join(cls.root, 'kept-%s.csv' % case)
            shutil.copy(path, cls.csvs[case])
            with open(path) as fh:
                cls.rows[case] = {r['VM']: r for r in csv.DictReader(fh)}
            log, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.log')) if 'state-based' in f]
            with open(log, encoding='utf-8') as fh:
                cls.log[case] = re.sub(r'\s+', ' ', fh.read())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def own(self, case, vm):
        m = self.members[case][vm]
        return m['profiles'][m['planningProfile']]

    def figures(self, case):
        return {vm: (r['NFIT - ENT'], r['NFIT - VCPU'], r['NFIT - Action']) for vm, r in self.rows[case].items()}

    def test_the_default_is_match_larger(self):
        self.assertEqual(self.figures('default'), self.figures('match_larger'))
        role = self.members['default']['viosA']['role']
        self.assertEqual((role['type'], role['mode']), ('viosPair', 'match_larger'))
        self.assertEqual(self.members['default']['viosA']['role']['targetEntitlement'], self.members['default']['viosB']['role']['targetEntitlement'])
        # Combined, asked for, is the sum of the two own targets and above the default.
        combined = self.members['combined']['viosA']['role']
        self.assertEqual(combined['mode'], 'combined')
        self.assertAlmostEqual(combined['targetEntitlement'], self.own('combined', 'viosA')['entitlement'] + self.own('combined', 'viosB')['entitlement'], places=2)
        self.assertGreater(combined['targetEntitlement'], role['targetEntitlement'])
        self.assertNotIn('viosPolicy', self.members['default']['viosA'])
        self.assertNotIn('Independent VIOS sizing', self.rows['default']['viosA']['NFIT - Action'])

    def test_match_larger_is_as_before_with_the_review_beside_it(self):
        a, b = (self.members['match_larger'][vm]['role'] for vm in ('viosA', 'viosB'))
        self.assertEqual((a['mode'], a['targetEntitlement']), ('match_larger', b['targetEntitlement']))
        self.assertIn('match_larger; takeover capacity not verified', self.rows['match_larger']['viosB']['NFIT - Action'])
        self.assertIn('Takeover review, viosA: carrying the work of viosB as well is estimated at', self.log['match_larger'])

    def test_independent_keeps_each_vios_on_its_own_workload(self):
        for vm in ('viosA', 'viosB'):
            m, row, p = self.members['independent'][vm], self.rows['independent'][vm], self.own('independent', vm)
            self.assertNotIn('role', m)                                               # no pair figure: sized as any LPAR
            self.assertEqual(m['viosPolicy']['mode'], 'independent')
            self.assertEqual(row['NFIT - ENT'], '%.2f' % p['currentCycle']['entitlement'])
            self.assertEqual(str(row['NFIT - VCPU']), str(p['currentCycle']['VPs']))
            self.assertEqual(float(m['poolMember']['proposed_ent']), max(p['entitlement'], 0.05 * p['recommendedVPs']))
            self.assertIn('Independent VIOS sizing (vios_sizing = independent): sized on its own workload, not for the work of', row['NFIT - Action'])
            self.assertEqual(m.get('estateRole'), 'vios')                             # still counted as a VIOS in the totals
        a, b = self.own('independent', 'viosA')['entitlement'], self.own('independent', 'viosB')['entitlement']
        self.assertGreater(a, b)                                                       # unequal own targets, kept unequal
        # The ordinary LPAR is the same under every policy.
        for case in CASES:
            self.assertEqual(self.figures(case)['app'], self.figures('default')['app'], case)
        # The own figures of the VIOS (their profile columns) are the same under every policy; only the pair figure differs.
        planning = self.members['independent']['viosA']['planningProfile']
        for case in ('default', 'combined'):
            self.assertEqual(self.rows[case]['viosA'][planning], self.rows['independent']['viosA'][planning], case)

    def test_the_takeover_review_is_an_estimate_beside_the_sizing(self):
        a, b = self.own('independent', 'viosA')['entitlement'], self.own('independent', 'viosB')['entitlement']
        review = self.members['independent']['viosA']['viosPolicy']
        self.assertAlmostEqual(review['combinedEstimate'], a + b, places=2)
        self.assertEqual((review['takeoverReview']['capped'], review['takeoverReview']['borrowing']), (0, 1))
        self.assertEqual(self.members['independent']['viosB']['viosPolicy']['takeoverReview']['capped'], 1)
        self.assertRegex(self.rows['independent']['viosA']['NFIT - Action'], r'A takeover of viosB would rely on borrowing \d\.\d\d above its entitlement \(combined estimate \d\.\d\d, not measured\); not verified\.')
        self.assertRegex(self.rows['independent']['viosB']['NFIT - Action'], r'It is capped and cannot borrow: a takeover of viosA \(combined estimate \d\.\d\d\) is not covered\.')
        log = self.log['independent']
        self.assertIn('independent (vios_sizing = independent for both): each sized on its own workload; no pair figure is applied.', log)
        self.assertRegex(log, r'Takeover review, viosA: .* \(the sum of the two own targets; not a measured peak\).* Uncapped, it would have to borrow that, which needs capacity free in its pool and on the frame at the time')
        self.assertRegex(log, r'Takeover review, viosB: .* It is capped: it cannot borrow')
        self.assertRegex(log, r'for comparison only \(illustrative; not a recommendation\): combined \d+\.\d\d, match_larger \d+\.\d\d, independent \d+\.\d\d\. In force: independent\.')

    def test_a_conflict_is_stated_and_the_more_protective_setting_applies(self):
        self.assertEqual(self.figures('conflict')['viosA'][:2], self.figures('default')['viosA'][:2])
        self.assertEqual(self.members['conflict']['viosA']['role']['mode'], 'match_larger')
        text = ('vios_sizing differs within the pair: viosA independent (its own stanza); viosB match_larger (the built-in default). '
                'match_larger applies (the more protective setting); independent sizing needs both to resolve to independent')
        self.assertIn(text, self.log['conflict'])
        self.assertIn(text, re.sub(r'\s+', ' ', self.err['conflict']))

    def test_the_acceptance_tool_reconciles_each_vios(self):
        # The tool's VIOS block, on the saved plan and CSV of each run: the policy in force, own target, full figure and this cycle's pair.
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader('pca_for_vios', os.path.join(mx.REPO, 'dev', 'planning-cycle-acceptance'))
        tool = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(tool)

        def block(case, asked):
            said, checks = [], []
            run = tool.Acceptance.__new__(tool.Acceptance)
            run.say = said.append
            run.check = lambda ok, text: checks.append((bool(ok), text))
            run.outputs = lambda reg: {'csv': self.csvs[case]}
            run.vios_asked = {'SYNVI': asked} if asked else {}
            run.vios_lines(None, 'SYNVI', {}, self.plans[case])
            return ' '.join(said), checks

        text, checks = block('independent', 'independent')
        self.assertEqual([ok for ok, _ in checks], [True, True, True], checks)
        self.assertRegex(text, r'viosA: policy independent; installed 2(\.0+)?; own target \d\.\d\d at \d VPs; full \d\.\d+; this cycle: own .* CSV \d\.\d\d at \d VPs; '
                               r'takeover estimate \d\.\d\d \(the sum of the own targets, not measured\), would borrow \d\.\d\d')
        self.assertRegex(text, r'viosB: policy independent; .*, capped: cannot borrow')
        self.assertRegex(text, r'VIOS entitlement on the frame: installed 3\.00, full \d\.\d\d, this cycle \d\.\d\d\.')
        # Under the default the pair figure is in force, and asking for independent is reported as not in force.
        text, checks = block('default', 'independent')
        self.assertIn('viosA: policy match_larger;', text)
        self.assertEqual([ok for ok, _ in checks], [False], checks)
        self.assertEqual(block('default', None)[1], [])


if __name__ == '__main__':
    unittest.main()
