"""VP width evidence (v10 B10) in a standard run, on a synthetic cache of 30 days
of one-minute data with PhysB and pool observations. Diagnostic only: the
evidence is shown beside the VP statements and kept with the plan; no
entitlement or VP figure is taken from it.

  rose     uncapped, 4 VPs: 40 minutes on each of 3 days at 3.80 cores, busy and
           queued well above its ordinary minutes.
  flat     uncapped, 4 VPs: the same minutes at its width, no busier and no more
           queued than usual.
  brief    uncapped, 4 VPs: 10 minutes at its width on one day.
  wide     uncapped, 8 VPs, never near them: fewer VPs are proposed, and the
           minutes that lay near the proposed widths are reported as observed.
  capLow   capped at 2.00 on 4 VPs: the cap lies below the VP count.
  capSame  capped at 2.00 on 2 VPs: the cap and the VP count coincide.
  moved    uncapped: its configuration changed 20 days before the end.
"""
import calendar
import csv
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import member_exposure_test as mx

FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 30
SERIAL = 'SYNWE'
LPARS = {   # LPAR: (entitlement, VPs, capped)
    'rose': (1.5, 4, 0), 'flat': (1.5, 4, 0), 'brief': (1.5, 4, 0), 'wide': (2.0, 8, 0),
    'capLow': (2.0, 4, 1), 'capSame': (2.0, 2, 1), 'moved': (1.5, 4, 0),
}


def minute_of(vm, day, minute):
    """(PhysC, PhysB, run queue) of one LPAR in one minute."""
    busy_days = day in (5, 12, 19)
    if vm in ('rose', 'flat') and busy_days and 600 <= minute < 640:
        return (3.80, 3.42, 14.0) if vm == 'rose' else (3.80, 1.33, 2.0)
    if vm == 'brief' and day == 7 and 600 <= minute < 610:
        return 3.80, 3.42, 14.0
    if vm == 'wide':
        return (1.90, 1.20, 6.0) if 600 <= minute < 660 else (0.80, 0.30, 2.0)
    if vm in ('capLow', 'capSame'):
        return (1.96, 1.70, 6.0) if (busy_days and 600 <= minute < 640) else (0.80, 0.30, 1.0)
    return 1.00, 0.35, 2.0


def build(root, serial=SERIAL, lpars=None, with_pool=True):
    lpars = lpars or LPARS
    cache = os.path.join(root, 'stage', serial)
    os.makedirs(cache)
    last = FIRST + DAYS * 86400 - 60
    header = 'Timestamp,VMName,PhysC,RunQ,PhysB'
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh, \
         gzip.open(os.path.join(cache if with_pool else root, '.nfit.cache.pool.observations.csv.gz'), 'wt') as pool:
        fh.write(header + '\n')
        pool.write(','.join(mx.HEADER) + '\n')
        for day in range(DAYS):
            lines, rows = [], []
            for minute in range(1440):
                t = FIRST + day * 86400 + minute * 60
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))
                total = 0.0
                for vm in lpars:
                    c, b, q = minute_of(vm, day, minute)
                    total += c
                    lines.append(f'{stamp},{vm},{c:.4f},{q:.4f},{b:.4f}')
                rows.append(mx.obs(t + 15, 'rose', 20, 32, round(total, 4), 20.0))
            fh.write('\n'.join(lines) + '\n')
            pool.write('\n'.join(rows) + '\n')
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: 2026-09-30 23:59:00\n'
                 f'data_schema_version: 2\ndata_columns: {header}\ndata_revision: synthetic-1\n')
    states = {}
    for vm, (ent, vp, capped) in lpars.items():
        def md(vps):
            return {'proc_clock': '3800MHz', 'serial_number': serial, 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0', 'max_cpu': 8,
                    'smt': 8, 'entitlement': ent, 'capped': capped, 'virtual_cpus': vps, 'pool_id': 20, 'pool_cpu': 32}
        if vm == 'moved':                                   # a VP removed on day 10: 20 days in the current configuration
            cut = FIRST + 10 * 86400
            states[vm] = [{'start_epoch': FIRST, 'end_epoch': cut - 60, 'duration_days': 10, 'metadata': md(vp + 1)},
                          {'start_epoch': cut, 'end_epoch': last, 'duration_days': DAYS - 10, 'metadata': md(vp)}]
        else:
            states[vm] = [{'start_epoch': FIRST, 'end_epoch': last, 'duration_days': DAYS, 'metadata': md(vp)}]
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    return cache


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class WidthEvidenceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='width-evidence-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        cache = build(cls.root)
        proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache, '--runq-perc-behavior=match'],
                              cwd=cls.root, capture_output=True, text=True, timeout=280)
        assert proc.returncode == 0, proc.stderr[-1500:]
        log, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.log')) if 'state-based' in f]
        with open(log, encoding='utf-8') as fh:
            cls.log = fh.read()
        plan, = glob.glob(os.path.join(cache, '.nfit.store', 'proposals', 'v1', 'state-based', '*.json'))
        with open(plan) as fh:
            cls.members = json.load(fh)['members']
        path, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.csv')) if 'state-based' in f and '.decisions.' not in f]
        with open(path) as fh:
            cls.rows = {r['VM']: r for r in csv.DictReader(fh)}
        # A second cache with PhysB and no pool observations: the local evidence is still reported.
        bare = build(cls.root, 'SYNWN', {'rose': LPARS['rose']}, with_pool=False)
        proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', bare, '--runq-perc-behavior=match'],
                              cwd=cls.root, capture_output=True, text=True, timeout=280)
        assert proc.returncode == 0, proc.stderr[-1500:]
        plan, = glob.glob(os.path.join(bare, '.nfit.store', 'proposals', 'v1', 'state-based', '*.json'))
        with open(plan) as fh:
            cls.bare = json.load(fh)['members']['rose']
        start = cls.log.index('\nVP Width Evidence (planning profile of each LPAR)\n')
        cls.section = re.sub(r'\s+', ' ', cls.log[start:cls.log.index('\nRecommended Actions', start)])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def evidence(self, vm):
        return self.members[vm]['widthEvidence']

    def installed(self, vm):
        row = self.evidence(vm)['widths'][0]
        self.assertEqual(row['basis'], 'installed')
        return row

    def test_busy_share_and_queue_rose_near_the_installed_width(self):
        row = self.installed('rose')
        self.assertEqual((row['support'], row['comparison'], row['near']['minutes'], row['near']['days']), ('sufficient', 'rose', 120, 3))
        self.assertEqual((row['near']['busyShare'], row['reference']['busyShare'], row['near']['runQueuePerVP'], row['reference']['runQueuePerVP']),
                         (0.9, 0.35, 3.5, 0.5))
        self.assertIn('At the installed 4 VPs: busy share and queue rose near width. 120 minutes on 3 days at 90% of it or more (0 at or above it): '
                      'busy share 90%, idle within consumed 0.38 cores a minute, run queue per installed VP 3.50, highest busy reading 3.42', self.section)
        self.assertIn('in the 43080 reference minutes: busy share 35%, run queue per installed VP 0.50. Pool in those minutes: room 120.', self.section)
        self.assertNotRegex(self.section, r'(?i)busy throughout|spare usable|unnecessary')            # a rise is reported as a rise, nothing more

    def test_no_rise_is_reported_as_no_rise(self):
        row = self.installed('flat')
        self.assertEqual((row['support'], row['comparison'], row['near']['busyShare']), ('sufficient', 'notRisen', 0.35))
        self.assertIn('At the installed 4 VPs: busy share and queue did not rise near width.', self.section)

    def test_few_minutes_near_width_are_limited_evidence_not_width_not_approached(self):
        row = self.installed('brief')
        self.assertEqual((row['support'], row['near']['minutes'], row['near']['days']), ('limited', 10, 1))
        self.assertNotIn('comparison', row)
        self.assertIn('At the installed 4 VPs: limited evidence near width. 10 minutes on 1 day', self.section)
        self.assertNotIn('not approached', self.section)

    def test_no_minutes_near_width_and_the_proposed_widths_as_observed(self):
        ev = self.evidence('wide')
        self.assertEqual(self.installed('wide')['support'], 'none')
        proposed = [r for r in ev['widths'] if r['basis'] != 'installed']
        self.assertTrue(proposed, 'fewer VPs are proposed for an LPAR that never neared its 8')
        for row in proposed:
            self.assertLess(row['width'], 8)
            if row['support'] != 'none':
                self.assertIn(f"Observed on 8 VPs: this says which minutes of the period lay near {row['width']}, not how the LPAR would run on {row['width']}.",
                              self.section)
        self.assertIn('At the installed 8 VPs: no observed minutes near width.', self.section)
        self.assertRegex(self.section, r'wide \(8 VPs, online hours \(08:00-17:00 UTC\), weekdays only, 30 days\): fewer VPs proposed \(\d+ this cycle, \d+ the full target\)')
        self.assertNotRegex(self.section, r'(?i)after the reduction|will (?:run|queue)|would be busy')

    def test_a_cap_below_the_vp_count(self):
        ev = self.evidence('capLow')
        self.assertEqual((ev['capped'], ev['cap']['relation'], ev['cap']['support'], ev['cap']['near']['minutes'], ev['cap']['near']['days']),
                         (True, 'belowWidth', 'sufficient', 120, 3))
        self.assertEqual(self.installed('capLow')['support'], 'none')                                # 1.96 cores never nears 4 VPs
        self.assertIn('At the entitlement cap of 2.00: sufficient minutes near the cap. 120 minutes on 3 days at 90% of the cap or more: busy share 87%', self.section)
        self.assertIn('The cap lies below the VP count (2.00 on 4 VPs): consumption cannot approach the VP count, so unused width is not evidence that '
                      'the workload is unconstrained. The findings for uncapped LPARs are not applied to a capped one; its figures are unchanged.', self.section)

    def test_a_cap_that_coincides_with_the_vp_count(self):
        ev = self.evidence('capSame')
        self.assertEqual((ev['cap']['relation'], ev['cap']['support']), ('coincides', 'sufficient'))
        row = self.installed('capSame')
        self.assertEqual((row['support'], 'comparison' in row), ('sufficient', False))             # observed figures, no comparison drawn
        self.assertIn('capSame (capped at 2.00, 2 VPs, all hours, 30 days) At the installed 2 VPs: sufficient minutes near width. 120 minutes on 3 days', self.section)
        self.assertIn('The cap and the VP count coincide (2.00 on 2 VPs): the observations do not separate the two limits.', self.section)
        self.assertIn('For a capped LPAR these figures are shown as observed; no comparison is drawn from them.', self.section)

    def test_without_pool_observations_the_local_evidence_is_still_reported(self):
        ev = self.bare['widthEvidence']
        row = ev['widths'][0]
        self.assertEqual((ev['status'], row['support'], row['comparison'], row['near']['minutes']), ('assessed', 'sufficient', 'rose', 120))
        self.assertEqual(row['near']['pool'], {'room': 0, 'others': 0, 'alone': 0, 'unobserved': 120})       # the pool state is unknown, and said to be
        self.assertNotIn('poolReduction', self.bare)

    def test_too_short_a_current_configuration_is_not_assessed(self):
        ev = self.evidence('moved')
        self.assertEqual((ev['status'], ev['reason']), ('notAssessed', '20 days in the current configuration, 28 needed'))
        self.assertNotIn('widths', ev)

    def test_the_boundaries_are_stated_once_and_as_provisional(self):
        self.assertIn('Near a width: consumed at 90% of it or more. Reference: consumed between 0.25 cores and 70% of it. A comparison needs 30 minutes on 2 days', self.section)
        self.assertIn('These boundaries are provisional.', self.section)
        self.assertIn('Diagnostic only: no entitlement or VP figure is taken from it or changed by it.', self.section)
        self.assertEqual(self.evidence('rose')['rule'], 'width-evidence-1')              # the boundaries are named in the record

    def test_the_csv_carries_no_width_evidence_but_the_review_sentence(self):
        for vm, row in self.rows.items():
            for column, value in row.items():
                if column != 'NFIT - Action':
                    self.assertNotIn('width evidence', (value or '').lower())
            action = row['NFIT - Action']
            self.assertEqual('The width evidence' in action, 'Review a VP increase' in action, vm)
        # Reading the plan's figures for the section leaves them as they were saved: numbers, not text.
        for vm, m in self.members.items():
            sized = m['profiles'][m['planningProfile']]
            for value in (m['installedVPs'], sized['currentCycle']['VPs'], sized['recommendedVPs']):
                self.assertIsInstance(value, (int, float), vm)
        self.assertRegex(self.section, r'LPARs by the evidence near their installed width: none observed \d+, limited \d+, sufficient \d+, not assessed 1\.')


if __name__ == '__main__':
    unittest.main()
