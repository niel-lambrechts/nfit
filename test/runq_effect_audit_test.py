#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/runq_effect_audit_test.py
# PURPOSE  : dev/runq-effect-audit end to end on a designed work tree of three
#            LPARs: one with a high run queue at low consumption (busy share at
#            its ordinary level), one busy at its VP count, one calm. Also the
#            two research switches it uses in nfit-profile: the trace changes
#            no figure, and the run without the additive differs only where the
#            additive was applied.
# USAGE    : python3 test/runq_effect_audit_test.py
# ==============================================================================
import calendar
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 14
HEADER = 'Timestamp,VMName,PhysC,RunQ,PhysB,FreqPct'
#        name: (entitlement, VPs, office hours (PhysC, run queue, PhysB), other hours)
LPARS = {'zzqueue': (2.0, 4, (0.6, 12.0, 0.18), (0.3, 0.5, 0.09)),      # twelve runnable threads at 0.6 cores: busy share 0.30 as at night
         'zzbusy': (1.0, 2, (1.9, 14.0, 1.70), (0.4, 0.6, 0.12)),       # at its VP count and busy throughout
         'zzcalm': (2.0, 4, (0.8, 1.0, 0.25), (0.3, 0.4, 0.09))}


def perl_env():
    env = dict(os.environ, TZ='UTC', LC_ALL='C')
    shim = os.path.join(REPO, '.claude', 'perl5')
    if subprocess.run(['perl', '-MJSON', '-e1'], capture_output=True).returncode != 0 and os.path.isdir(shim):
        env['PERL5LIB'] = shim
    return env


def perl_ready():
    return shutil.which('perl') is not None and subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True, env=perl_env()).returncode == 0


def build(root):
    app = os.path.join(root, 'app')
    os.makedirs(os.path.join(app, 'etc'))
    os.makedirs(os.path.join(app, 'output'))
    for prog in ('nfit', 'nfit-profile'):
        shutil.copy(os.path.join(REPO, prog), app)
    for name in ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default', 'nfit.scenarios.cfg.default'):
        if os.path.exists(os.path.join(REPO, 'etc', name)):
            shutil.copy(os.path.join(REPO, 'etc', name), os.path.join(app, 'etc', name))
    open(os.path.join(app, 'etc', 'nfit.seasonality.cfg'), 'w').close()
    with open(os.path.join(app, 'etc', 'nfit.vms.cfg'), 'w') as fh:
        fh.write(''.join(f'[{vm}]\ntier = G3\n' for vm in LPARS))
    stage = os.path.join(root, 'case', 'stage', 'SYNRQ')
    os.makedirs(stage)
    last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - 60))
    with open(os.path.join(stage, '.nfit.cache.data'), 'w') as fh:
        fh.write(HEADER + '\n')
        for e in range(FIRST, FIRST + DAYS * 86400, 60):
            stamp, wobble = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e)), 0.02 * math.sin(e / 777.0)
            for vm, (_ent, _vps, office, rest) in LPARS.items():
                c, q, b = office if 8 <= (e // 3600) % 24 < 18 else rest
                fh.write(f'{stamp},{vm},{c + wobble:.4f},{q:.4f},{b + wobble * b / c:.4f},100.0\n')
    with open(os.path.join(stage, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {last} 23:59:00\ndata_schema_version: 2\n'
                 f'data_columns: {HEADER}\ndata_revision: rev-syn-1\ncolumn_sources: PhysC=cpu_util.physical_consumed; RunQ=kernel.run_queue; '
                 'PhysB=cpu_util.physical_busy; FreqPct=cpu_util.freq_pct\n')
    states = {vm: [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - 60, 'duration_days': DAYS,
                    'metadata': {'proc_clock': '3800MHz', 'serial_number': 'SYNRQ', 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0',
                                 'max_cpu': vps, 'pool_id': 0, 'smt': 8, 'pool_cpu': 32, 'entitlement': ent, 'capped': 0, 'virtual_cpus': vps}}]
              for vm, (ent, vps, _o, _r) in LPARS.items()}
    with open(os.path.join(stage, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(stage, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    return app, stage, last


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class RunQueueEffectAuditTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.mkdtemp(prefix='runq-effect-')
        work = os.path.join(cls.scratch, 'work')
        app, stage, last = build(work)
        # The stored standard proposal of the kept tree.
        cls.stored = subprocess.run(['perl', os.path.join(app, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', last, '--nmondir', stage],
                                    capture_output=True, text=True, timeout=300, cwd=app, env=perl_env())
        cls.run_ = subprocess.run([sys.executable, os.path.join(REPO, 'dev', 'runq-effect-audit'), '--work', work, '--out', os.path.join(cls.scratch, 'out'),
                                   '--label', 'designed'], capture_output=True, text=True, timeout=600)
        cls.result = {}
        path = os.path.join(cls.scratch, 'out', 'runq-effect-designed.json')
        if os.path.exists(path):
            with open(path) as fh:
                cls.result = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def test_the_stored_proposal_is_verified_and_reproduced_and_nothing_is_named(self):
        self.assertEqual(self.stored.returncode, 0, self.stored.stderr[-1500:])
        self.assertEqual(self.run_.returncode, 0, self.run_.stderr[-1500:] + self.run_.stdout[-1500:])
        v = self.result['verification']
        for key in ('dataRevisionMatches', 'statesRevisionMatches', 'profilerIsTheCurrentProgram', 'engineIsTheCurrentProgram', 'profilesFileUnchanged'):
            self.assertTrue(v[key], key)
        # The trace and the busy-share record change no figure: the replay equals the stored proposal.
        self.assertEqual((v['replay']['different'], v['replay']['notInReplay']), (0, 0))
        self.assertGreater(v['replay']['identical'], 0)
        self.assertTrue(v['replay']['inputRevisionMatches'])
        text = json.dumps(self.result) + self.run_.stdout
        for name in list(LPARS) + ['SYNRQ']:
            self.assertNotIn(name, text)
        self.assertIn('total hits: 0', self.run_.stdout)

    def test_high_queue_minutes_are_placed_by_regime_with_the_lpars_own_ordinary_level(self):
        regimes = self.result['highQueueByRegime']
        spare = regimes['spare.perVP']
        self.assertEqual((spare['lpars'], spare['busyShareAtOrdinaryLevel'], spare['busyShareLifted']), (1, 1, 0))
        self.assertAlmostEqual(spare['difference']['median'], 0.0, delta=0.01)        # 0.30 with the queue and without it
        self.assertAlmostEqual(spare['meanPhysC']['median'], 0.6, delta=0.01)
        self.assertAlmostEqual(spare['meanRunQ']['median'], 12.0, delta=0.01)
        self.assertAlmostEqual(spare['runQPerVP']['median'], 3.0, delta=0.01)
        self.assertAlmostEqual(spare['runQPerConsumedThreadOfSums']['median'], 2.5, delta=0.02)   # 12 / (0.6 x 8)
        self.assertEqual(regimes['spare.normalised']['lpars'], 1)
        ceiling = regimes['ceiling.perVP']
        self.assertEqual((ceiling['lpars'], ceiling['busyShareLifted']), (1, 1))        # the busy LPAR: 0.90 against 0.30
        self.assertEqual(regimes['above.perVP']['lpars'], 0)

    def test_each_rule_is_classed_from_the_stored_terms(self):
        planning = self.result['rules']['planningProfiles']
        # The queue-heavy LPAR: the additive finds no excess over the dispatch allowance ...
        self.assertEqual(planning['additive']['triggered: queue within the dispatch allowance']['count'], 1)
        self.assertEqual(planning['additive']['not triggered']['count'], 2)
        # ... the queue width sets its recommended VPs (2, where the entitlement range starts at 1) ...
        self.assertEqual(planning['width']['set the VP count'], {'count': 1, 'recommendedVPsRestingOnQueue': 1})
        # ... and its VP reduction of this cycle (4 to 2) is held by the queue signal alone.
        self.assertEqual(planning['safety']['reduction held by the run-queue signal alone'], {'count': 1, 'VPs': 2})
        self.assertEqual(planning['safetyHeldAloneBySignal'], {'queue per consumed thread': {'count': 1, 'VPs': 2}})
        self.assertEqual(planning['safety']['reduction allowed']['count'], 1)           # the calm LPAR
        cross = self.result['rules']['highQueueWithSpareVPsAgainstTheRules']
        row = cross['high queue, busy share at its ordinary level']
        self.assertEqual((row['lpars'], row['additiveApplied'], row['reductionHeldByQueueAlone'], row['VPsHeldByQueueAlone'],
                          row['ofThoseHeldAlonePublishedAtInstalledVPs']), (1, 0, 1, 2, 1))
        self.assertEqual(cross['no high-queue minutes with VPs spare']['lpars'], 2)
        self.assertEqual(self.result['rules']['byRole']['none']['lpars'], 3)

    def test_the_additive_is_measured_by_a_second_sizing_only_where_it_was_applied(self):
        # No planning profile carries the additive here; some other sized profiles do, and there it sets the target.
        effect = self.result['rules']['withoutTheAdditive']
        self.assertEqual(effect['planningProfiles']['applied'], 0)
        other = effect['otherSizedProfiles']
        self.assertGreater(other['applied'], 0)
        self.assertEqual(other['applied'], other['setTheTarget'] + other['passedThrough'])
        self.assertEqual(self.result['rules']['otherSizedProfiles']['additive']['applied']['count'], other['applied'])
        self.assertIn('runWithoutAdditive', self.result['seconds'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
