#!/usr/bin/env python3
# ==============================================================================
# Non-peak baseline of the predictive_peak model (_get_true_baseline_results in
# nfit-profile). The baseline must leave out every historical peak day. The
# engine reads the system's cache, so the peak days reach it only through the
# manifest's global exclusions.
#
# The synthetic system has a conspicuous peak in the last seven days of each
# month. A seasonal run is made with an engine wrapper that records each call.
# Checked on the recorded baseline call:
#   - the manifest excludes every peak day of the completed months, and keeps
#     the event's explicit exclusions (global and per VM);
#   - the call is bounded by the analysis window;
#   - its result equals an independent control: the same engine on a copy of the
#     cache with those days' rows removed and no exclusions;
#   - its result differs from a run that excludes nothing (the test would not
#     notice the defect otherwise);
#   - a second (warm) seasonal run makes the same baseline call.
# ==============================================================================
import calendar
import glob
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default',
               'nfit.scenarios.cfg.default')
SERIAL = 'SYNPB'
FIRST = calendar.timegm((2026, 8, 10, 0, 0, 0))
DAYS = 85                                   # 2026-08-10 .. 2026-11-02
STEP = 300                                  # five-minute samples keep the run short
VMS = {'vmA': (2.0, 4), 'vmB': (1.0, 2)}
PEAK_DAYS = ([f'2026-08-{d:02d}' for d in range(25, 32)] + [f'2026-09-{d:02d}' for d in range(24, 31)]
             + [f'2026-10-{d:02d}' for d in range(25, 32)])
EXPLICIT_GLOBAL = '2026-09-10'
EXPLICIT_VM = ('vmB', '2026-10-05')
SEASONALITY = f'''[Global]
min_history_days = 21
seasonal_lookback_days = 29
interaction_policy = exclusive
aggregation_method = median

[month-end-predictive]
model                 = predictive_peak
priority              = 15
description           = "Synthetic month-end peak."
period                = monthly
day_of_period         = -1
duration_days         = 7
baseline_period_days  = 16
min_history_days      = 21
min_historical_peaks  = 3
max_historical_peaks  = 12
exclude_dates = *:{EXPLICIT_GLOBAL}:{EXPLICIT_GLOBAL}, {EXPLICIT_VM[0]}:{EXPLICIT_VM[1]}:{EXPLICIT_VM[1]}
'''
WRAPPER = '''#!/usr/bin/env python3
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
if '--manifest' in args:
    log = os.path.join(here, 'calls')
    os.makedirs(log, exist_ok=True)
    n = len(os.listdir(log))
    with open(args[args.index('--manifest') + 1]) as fh:
        manifest = json.load(fh)
    with open(os.path.join(log, f'{n:04d}.json'), 'w') as fh:
        json.dump({'args': args, 'manifest': manifest}, fh)
sys.exit(subprocess.call(['perl', os.path.join(here, 'nfit.engine')] + args))
'''


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def data_rows():
    rows = []
    for e in range(FIRST, FIRST + DAYS * 86400, STEP):
        tm = time.gmtime(e)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', tm)
        peak = time.strftime('%Y-%m-%d', tm) in PEAK_DAYS
        for vm, (ent, vp) in sorted(VMS.items()):
            base = (0.55 if 8 <= tm.tm_hour < 17 else 0.30) * ent
            c = base * (1 + 0.08 * math.sin(e / 977.0 + ord(vm[-1])))
            if peak:
                c = min(vp, c * 3.0)                                   # the conspicuous peak
            rows.append(f'{stamp},{vm},{c:.4f},{c * 2:.4f}')
    return rows


def write_cache(cache, rows):
    os.makedirs(cache)
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join(['Timestamp,VMName,PhysC,RunQ'] + rows) + '\n')
    last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - STEP))
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-08-10\ndata_end: {last} 23:55:00\n')
    states = {vm: [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - STEP, 'duration_days': DAYS,
                    'metadata': {'proc_clock': '3800MHz', 'serial_number': SERIAL, 'proc_type': 'PowerPC_POWER10',
                                 'proc_version': '2.0', 'max_cpu': vp, 'pool_id': 0, 'smt': 8, 'pool_cpu': 64,
                                 'entitlement': ent, 'capped': 0, 'virtual_cpus': vp}}]
              for vm, (ent, vp) in VMS.items()}
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')


def is_baseline_call(call):
    """The non-peak baseline: a pass without a decay model whose manifest
    excludes the last day of October for every VM."""
    args = call['args']
    excluded = (call['manifest'].get('_exclusions') or {}).get('global') or []
    return ('--enable-windowed-decay' not in args and '--decay-over-states' not in args
            and '--show-progress' in args and '--enable-clipping-detection' not in args
            and '2026-10-31' in excluded)


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class SeasonalBaselineTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = root = tempfile.mkdtemp(prefix='seasonal-baseline-')
        os.makedirs(os.path.join(root, 'etc'))
        os.makedirs(os.path.join(root, 'output'))
        shutil.copy(os.path.join(REPO, 'nfit-profile'), root)
        shutil.copy(os.path.join(REPO, 'nfit'), os.path.join(root, 'nfit.engine'))
        with open(os.path.join(root, 'nfit'), 'w') as fh:
            fh.write(WRAPPER)
        os.chmod(os.path.join(root, 'nfit'), os.stat(os.path.join(root, 'nfit')).st_mode | stat.S_IXUSR)
        for name in TRACKED_ETC:
            src = os.path.join(REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(root, 'etc', name))
        with open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(SEASONALITY)
        open(os.path.join(root, 'etc', 'nfit.vms.cfg'), 'w').close()
        cls.rows = data_rows()
        cls.cache = os.path.join(root, 'stage', SERIAL)
        write_cache(cls.cache, cls.rows)
        cls.runs = [cls.seasonal(), cls.seasonal()]                    # cold, then warm

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def seasonal(cls):
        log = os.path.join(cls.root, 'calls')
        shutil.rmtree(log, ignore_errors=True)
        p = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--seasonal', '--seasonal-scope', 'all',
                            '--runq-perc-behavior=match', '--nmondir', cls.cache],
                           capture_output=True, text=True, timeout=280, cwd=cls.root)
        calls = []
        for path in sorted(glob.glob(os.path.join(log, '*.json'))):
            with open(path) as fh:
                calls.append(json.load(fh))
        return {'rc': p.returncode, 'stderr': p.stderr, 'calls': calls}

    def baseline_call(self, run=0):
        self.assertEqual(self.runs[run]['rc'], 0, self.runs[run]['stderr'][-2000:])
        found = [c for c in self.runs[run]['calls'] if is_baseline_call(c)]
        self.assertTrue(found, 'no baseline call excluded the month-end peak days: '
                        + json.dumps([c['args'] for c in self.runs[run]['calls']])[:1500])
        return found[0]

    def engine(self, cache, manifest, args):
        results = os.path.join(cache, '.nfit.cache.results')
        if os.path.exists(results):
            os.remove(results)
        path = os.path.join(self.root, 'control-manifest.json')
        with open(path, 'w') as fh:
            json.dump(manifest, fh)
        args = list(args)
        args[args.index('--manifest') + 1] = path
        args[args.index('--nmondir') + 1] = cache
        p = subprocess.run(['perl', os.path.join(self.root, 'nfit.engine')] + args,
                           capture_output=True, text=True, timeout=280, cwd=self.root)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        out = {}
        for line in p.stdout.splitlines():
            if line.startswith('{'):
                o = json.loads(line)
                out.setdefault(o['vmName'], []).append(json.dumps(o['metrics']['physc'], sort_keys=True))
        self.assertEqual(sorted(out), sorted(VMS))
        return {vm: sorted(v) for vm, v in out.items()}

    def test_peak_days_and_explicit_exclusions_reach_the_engine(self):
        exclusions = self.baseline_call()['manifest']['_exclusions']
        missing = [d for d in PEAK_DAYS if d not in exclusions['global']]
        self.assertEqual(missing, [], 'peak days not excluded from the baseline')
        self.assertIn(EXPLICIT_GLOBAL, exclusions['global'])
        self.assertEqual(exclusions.get('vm_specific', {}).get(EXPLICIT_VM[0]), [EXPLICIT_VM[1]])
        self.assertEqual(len(exclusions['global']), len(set(exclusions['global'])))

    def test_baseline_call_is_bounded_by_the_analysis_window(self):
        args = self.baseline_call()['args']
        self.assertIn('--startdate', args)
        self.assertIn('--enddate', args)
        self.assertEqual(args[args.index('--startdate') + 1], '2026-08-10')
        self.assertEqual(args[args.index('--enddate') + 1], '2026-11-02')

    def test_result_equals_an_independently_filtered_control(self):
        call = self.baseline_call()
        exclusions = call['manifest']['_exclusions']
        as_called = self.engine(self.cache, call['manifest'], call['args'])

        plain = {k: v for k, v in call['manifest'].items() if k not in ('_exclusions', '_exclusion_fingerprint')}
        dropped = set(exclusions['global'])
        per_vm = {vm: set(days) for vm, days in exclusions.get('vm_specific', {}).items()}
        kept = [r for r in self.rows
                if r[:10] not in dropped and r[:10] not in per_vm.get(r.split(',')[1], ())]
        self.assertLess(len(kept), len(self.rows))
        control_cache = os.path.join(self.root, 'stage', 'CONTROL')
        if not os.path.isdir(control_cache):
            write_cache(control_cache, kept)
        control = self.engine(control_cache, plain, call['args'])
        self.assertEqual(as_called, control)

        unfiltered = self.engine(self.cache, plain, call['args'])
        self.assertNotEqual(as_called, unfiltered, 'the peak days made no difference: the test cannot see the defect')

    def test_warm_run_makes_the_same_baseline_call(self):
        cold, warm = self.baseline_call(0), self.baseline_call(1)
        digest = lambda c: hashlib.sha256(json.dumps(c['manifest'], sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest(cold), digest(warm))
        strip = lambda a: [x for i, x in enumerate(a) if x != '--manifest' and (i == 0 or a[i - 1] != '--manifest')]
        self.assertEqual(strip(cold['args']), strip(warm['args']))


if __name__ == '__main__':
    unittest.main()
