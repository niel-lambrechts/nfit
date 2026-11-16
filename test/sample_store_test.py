#!/usr/bin/env python3
# ==============================================================================
# Sample store (v10 milestone 1.1b): the engine's pass from the per-VM sample
# store must give exactly the output of the text pass. Each case runs the engine
# twice on one synthetic cache, once with NFIT_SAMPLE_STORE=0 (text pass) and
# once from the store, and compares the JSON objects it prints. The cache holds
# the awkward rows the text pass has rules for: non-numeric cells, short rows,
# same-second duplicates, gaps, samples outside every state, overlapping states
# and a VM without states. Also checked: the store is rebuilt when the data
# revision changes, reused when it does not, and set aside (text pass) when the
# cache has a timestamp or an order the store cannot reproduce.
# ==============================================================================
import calendar
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
FIRST = calendar.timegm((2026, 9, 3, 0, 0, 0))
DAYS = 12
END = FIRST + DAYS * 86400 - 60
DAY = 86400
VMS = {'vmA': (2.5, 4), 'vmB': (2.0, 4), 'vmC': (1.5, 3), 'vmD': (0.5, 1)}
STORE = os.path.join('.nfit.store', 'samples')


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def transform(metric, method, window, time_filter, no_weekends, profile, percentiles, filter_perc='0', growth=None):
    entry = {'metric': metric, 'method': method, 'window': str(window), 'decay': 'medium', 'filter_perc': filter_perc,
             'time_filter': time_filter, 'no_weekends': no_weekends,
             'profiles': {profile: {'percentiles': [str(p) for p in percentiles]}}}
    if growth is not None:
        entry['profiles'][profile]['enable_growth'] = growth
    key = f'{metric}:{method}:{window}:medium:{filter_perc}:{time_filter}:{no_weekends}'
    return key, entry


def manifest(exclusions=None):
    m = dict([
        transform('PhysC', 'ema', 15, 'none', 0, 'G3-95W15', [95], '15', 1),
        transform('PhysC', 'ema', 10, 'online', 1, 'O2-98W10', [98], '25', 1),
        transform('PhysC', 'ema', 5, 'batch', 0, 'B1-99W5', [99], '30', 1),
        transform('PhysC', 'sma', 15, 'none', 0, 'S3-95W15', [95], '15', 0),
        transform('PhysC', 'sma', 1, 'online', 0, 'S1-99W1', [99], '0', 0),
        transform('AbsRunQ', 'ema', 15, 'none', 0, 'G3-95W15', [80, 90]),
        transform('AbsRunQ', 'sma', 10, 'batch', 0, 'B1-99W5', [90]),
        transform('NormRunQ', 'ema', 15, 'none', 0, 'G3-95W15', [50, 90]),
        transform('NormRunQ', 'sma', 5, 'online', 1, 'O2-98W10', [50, 90]),
    ])
    if exclusions:
        m['_exclusions'] = exclusions
        m['_exclusion_fingerprint'] = 'f' * 32      # nfit-profile sends this string beside the exclusions
    return m


def state(a, b, ent, vp, smt=8, pool=0, size=64):
    return {'start_epoch': a, 'end_epoch': b, 'duration_days': int((b - a) / DAY) + 1,
            'metadata': {'proc_clock': '3800MHz', 'serial_number': 'SYNSS', 'proc_type': 'PowerPC_POWER10',
                         'proc_version': '2.0', 'max_cpu': vp, 'pool_id': pool, 'smt': smt, 'pool_cpu': size,
                         'entitlement': ent, 'capped': 0, 'virtual_cpus': vp}}


def data_rows():
    rng = random.Random(11)
    rows = []
    for e in range(FIRST, FIRST + DAYS * DAY, 60):
        tm = time.gmtime(e)
        for vm, (ent, vp) in sorted(VMS.items()) + [('ghost', (1.0, 2))]:
            if vm == 'vmB' and tm.tm_mday == 6 and tm.tm_hour < 9:
                continue                                                        # a gap
            stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e + (rng.randint(0, 2) if vm == 'vmC' else 0)))
            base = (0.9 if 8 <= tm.tm_hour < 17 else 0.4) * ent
            c = min(vp, base * (1 + 0.25 * math.sin(e / 977.0 + ord(vm[-1])) + 0.1 * rng.random()))
            if vm == 'vmD' and tm.tm_hour == 2:
                c = 1.0                                                         # ties at the peak
            cells = [f'{c:.4f}', f'{c * (1 + 3 * rng.random()):.4f}', f'{c * (0.55 + 0.4 * rng.random()):.4f}',
                     f'{95 + 10 * rng.random():.1f}']
            x = rng.random()
            if x < 0.004:
                cells[0] = 'n/a'                                                # dropped by both passes
            elif x < 0.008:
                cells[2] = ''
            elif x < 0.012:
                cells[2] = 'abc'
            elif x < 0.016:
                cells[2] = f'{c * 1.5:.4f}'                                     # busy above consumed
            elif x < 0.020:
                cells[3] = ''
            line = ','.join([stamp, vm] + cells)
            if 0.020 <= x < 0.024:
                line = ','.join([stamp, vm] + cells[:2])                        # a short row
            if 0.024 <= x < 0.028:
                rows.append(line)                                               # a same-second duplicate
            rows.append(line)
    return rows


def write_cache(cache, rows, revision='r1', schema2=True):
    os.makedirs(cache, exist_ok=True)
    header = 'Timestamp,VMName,PhysC,RunQ' + (',PhysB,FreqPct' if schema2 else '')
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join([header] + rows) + '\n')
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write('cache_build_status: success\n')
        if schema2:
            fh.write(f'data_schema_version: 2\ndata_columns: {header}\n')
        fh.write(f'data_revision: {revision}\nstates_revision: {revision}\n')
    states = {
        'vmA': [state(FIRST, FIRST + 4 * DAY - 60, 2.0, 4), state(FIRST + 4 * DAY, END, 2.5, 6)],
        'vmB': [state(FIRST, FIRST + 3 * DAY, 2.0, 4, pool=25, size=7), state(FIRST + 5 * DAY, END, 2.0, 4, pool=25, size=7)],
        'vmC': [state(FIRST, FIRST + 7 * DAY, 1.5, 3, smt=4), state(FIRST + 6 * DAY, END, 1.5, 4)],
        'vmD': [state(FIRST, END, 0.5, 1)],
    }
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class SampleStoreTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='sample-store-')
        shutil.copy(os.path.join(REPO, 'nfit'), cls.root)
        cls.rows = data_rows()
        cls.cache = os.path.join(cls.root, 'SYNSS')
        write_cache(cls.cache, cls.rows)
        for name, m in (('m.json', manifest()),
                        ('mx.json', manifest({'global': ['2026-09-07'], 'vm_specific': {'vmA': ['2026-09-10']}}))):
            with open(os.path.join(cls.root, name), 'w') as fh:
                json.dump(m, fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def engine(self, cache, args, store=True):
        results = os.path.join(cache, '.nfit.cache.results')
        if os.path.exists(results):
            os.remove(results)                         # never compare a cached result with itself
        env = dict(os.environ)
        env.pop('NFIT_SAMPLE_STORE', None)
        if not store:
            env['NFIT_SAMPLE_STORE'] = '0'
        p = subprocess.run(['perl', os.path.join(self.root, 'nfit'), '--nmondir', cache, '--smt', '8',
                            '--runq-avg-method', 'ema'] + args,
                           capture_output=True, text=True, timeout=280, cwd=self.root, env=env)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        objects = [json.loads(line) for line in p.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(objects, 'the engine printed no result')
        return sorted(json.dumps(o, sort_keys=True) for o in objects)

    def same(self, args, cache=None):
        cache = cache or self.cache
        text = self.engine(cache, args, store=False)
        stored = self.engine(cache, args, store=True)
        self.assertEqual(text, stored)
        return stored

    def test_state_based_with_clipping_and_paired_physb(self):
        self.same(['--manifest', 'm.json', '--enable-clipping-detection', '--physb-paired'])
        self.assertTrue(os.path.isfile(os.path.join(self.cache, STORE, 'store.json')))

    def test_decay_over_states_with_growth(self):
        self.same(['--manifest', 'm.json', '--decay-over-states', '--enable-growth-prediction'])

    def test_windowed_decay_with_growth(self):
        self.same(['--manifest', 'm.json', '--enable-windowed-decay', '--enable-growth-prediction'])

    def test_date_window_and_exclusions(self):
        self.same(['--manifest', 'mx.json', '--startdate', '2026-09-05', '--enddate', '2026-09-12',
                   '--enable-clipping-detection', '--physb-paired'])
        self.same(['--manifest', 'mx.json', '--enddate', '2026-09-11', '--decay-over-states'])

    def test_date_windows_read_only_their_days(self):
        # A single day, a window that starts before the data and one that ends
        # after it, a start alone and an end alone.
        for window in (['--startdate', '2026-09-08', '--enddate', '2026-09-08'],
                       ['--startdate', '2026-08-01', '--enddate', '2026-09-05'],
                       ['--startdate', '2026-09-12', '--enddate', '2026-12-31'],
                       ['--startdate', '2026-09-09'],
                       ['--enddate', '2026-09-06']):
            self.same(['--manifest', 'm.json', '--enable-clipping-detection', '--physb-paired'] + window)
        self.same(['--manifest', 'mx.json', '--startdate', '2026-09-06', '--enddate', '2026-09-11',
                   '--enable-windowed-decay', '--enable-growth-prediction'])
        with open(os.path.join(self.cache, STORE, 'store.json')) as fh:
            store = json.load(fh)
        self.assertEqual(store['format'], 2)
        days = store['vms']['vmA']['days']
        self.assertEqual(len(days['2026-09-03']), 7)          # first, last, row, then four column offsets
        self.assertEqual(days['2026-09-03'][2:], [0, 0, 0, 0, 0])
        self.assertGreater(days['2026-09-04'][2], 0)

    def test_vm_filters(self):
        self.same(['--manifest', 'm.json', '--vm', 'vmA,vmD', '--enable-clipping-detection'])
        self.same(['--manifest', 'm.json', '--exclude-vms', 'vmB', '--enable-windowed-decay'])

    def test_schema_1_cache(self):
        cache = os.path.join(self.root, 'SYNS1')
        write_cache(cache, [','.join(r.split(',')[:4]) for r in self.rows if r.count(',') >= 3], schema2=False)
        self.same(['--manifest', 'm.json', '--enable-clipping-detection'], cache)

    def test_store_is_reused_and_rebuilt_on_a_new_data_revision(self):
        cache = os.path.join(self.root, 'SYNRV')
        write_cache(cache, self.rows[:20000])
        args = ['--manifest', 'm.json', '--enable-clipping-detection']
        first = self.engine(cache, args)
        marker = os.path.join(cache, STORE, 'store.json')
        built = os.stat(marker).st_mtime_ns
        self.assertEqual(first, self.engine(cache, args))
        self.assertEqual(built, os.stat(marker).st_mtime_ns, 'an unchanged cache must reuse its store')
        time.sleep(0.05)
        write_cache(cache, self.rows[:30000], revision='r2')
        second = self.same(args, cache)
        self.assertNotEqual(built, os.stat(marker).st_mtime_ns, 'a new data revision must rebuild the store')
        self.assertNotEqual(first, second)

    def test_a_damaged_store_is_rebuilt(self):
        cache = os.path.join(self.root, 'SYNDM')
        write_cache(cache, self.rows[:20000])
        args = ['--manifest', 'm.json']
        first = self.engine(cache, args)
        with open(os.path.join(cache, STORE, '000001.ep'), 'ab') as fh:
            fh.write(b'\0\0\0\0')
        self.assertEqual(first, self.engine(cache, args))

    def test_text_pass_is_kept_when_the_store_cannot_reproduce_it(self):
        for label, rows in (('SYNTS', self.rows[:5000] + ['2026-9-4 00:00:00,vmA,1.0,1.0,0.5,100'] + self.rows[5000:9000]),
                            ('SYNOR', self.rows[8000:16000] + self.rows[:8000])):
            cache = os.path.join(self.root, label)
            write_cache(cache, rows)
            self.same(['--manifest', 'm.json', '--enable-clipping-detection', '--physb-paired'], cache)
            with open(os.path.join(cache, STORE, 'store.json')) as fh:
                self.assertIn('unusable', json.load(fh), label)
            self.assertEqual([f for f in os.listdir(os.path.join(cache, STORE)) if f != 'store.json'], [], label)


if __name__ == '__main__':
    unittest.main()
