"""dev/protection-replay's coarser sizing copy (--thin-minutes, --thin-mode) and the
replay that follows it.

thin_data and thin_pool on small synthetic files: point selection, interval means, a
gap that stays a gap, out-of-order input. Then one case end to end on the
member-exposure fixture: the sizing run reads the 15-minute copy, and the held-out
replay accepts its proposal and scores it on the capture as recorded.
"""
import csv
import gzip
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import member_exposure_test as mx

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..', 'dev', 'protection-replay'))


def load():
    loader = importlib.machinery.SourceFileLoader('protection_replay', TOOL)
    spec = importlib.util.spec_from_loader('protection_replay', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ThinFilesTest(unittest.TestCase):
    def setUp(self):
        self.pr = load()
        self.dir = Path(tempfile.mkdtemp(prefix='thin-'))
        self.addCleanup(shutil.rmtree, self.dir)

    def data(self, rows):
        src = self.dir / 'data'
        src.write_text('Timestamp,VMName,PhysC,RunQ,PhysB\n' + '\n'.join(rows) + '\n')
        return src

    def stamp(self, minute):
        return '2026-09-01 %02d:%02d:00' % (minute // 60, minute % 60)

    def test_point_keeps_every_nth_minute_as_recorded(self):
        src = self.data([f'{self.stamp(m)},lp1,{1 + m / 100:.4f},2.0000,0.5000' for m in range(60)])
        read, written = self.pr.thin_data(src, self.dir / 'out', 15, 'point')
        rows = (self.dir / 'out').read_text().splitlines()
        self.assertEqual((read, written), (60, 4))
        self.assertEqual([r[11:16] for r in rows[1:]], ['00:00', '00:15', '00:30', '00:45'])
        self.assertEqual(rows[2], '2026-09-01 00:15:00,lp1,1.1500,2.0000,0.5000')      # untouched

    def test_mean_averages_the_interval_and_keeps_a_gap(self):
        rows = [f'{self.stamp(m)},{vm},{c:.4f},2.0000,{b}' for m in range(45)
                for vm, c, b in (('lp1', 1.0 if m < 15 else 3.0, '0.5'), ('lp2', 2.0, ''))
                if not (vm == 'lp1' and 30 <= m < 40)]                                   # lp1 loses ten of fifteen minutes
        read, written = self.pr.thin_data(self.data(rows), self.dir / 'out', 15, 'mean')
        out = list(csv.reader((self.dir / 'out').read_text().splitlines()))[1:]
        self.assertEqual(out[0], ['2026-09-01 00:00:00', 'lp1', '1.0000', '2.0000', '0.5000'])
        self.assertEqual(out[1], ['2026-09-01 00:00:00', 'lp2', '2.0000', '2.0000', ''])   # an empty column stays empty
        self.assertEqual(out[2][:3], ['2026-09-01 00:15:00', 'lp1', '3.0000'])
        self.assertEqual([r[1] for r in out if r[0].endswith('00:30:00')], ['lp2'])       # the gap is not filled
        self.assertEqual(written, 5)

    def test_mean_refuses_rows_out_of_time_order(self):
        src = self.data([f'{self.stamp(m)},lp1,1.0,2.0,0.5' for m in (0, 1, 40, 2)])
        with self.assertRaises(RuntimeError):
            self.pr.thin_data(src, self.dir / 'out', 15, 'mean')

    def pool(self, rows):
        src = self.dir / 'pool.gz'
        with gzip.open(src, 'wt') as fh:
            fh.write(','.join(mx.HEADER) + '\n' + '\n'.join(rows) + '\n')
        return src

    def test_pool_mean_per_reporter_and_point(self):
        rows = []
        for m in range(30):
            t = mx.DAY1 + 60 * m
            rows += [mx.obs(t + 10, 'hA', 7, 4, 1.0 if m < 15 else 3.0, 20.0), mx.obs(t + 20, 'hB', 7, 4, 2.0, 20.0)]
        read, written = self.pr.thin_pool(self.pool(rows), self.dir / 'mean.gz', 15, 'mean')
        with gzip.open(self.dir / 'mean.gz', 'rt') as fh:
            out = list(csv.DictReader(fh))
        self.assertEqual((read, written), (60, 4))
        first = {r['host']: r for r in out if int(r['time_ns']) == (mx.DAY1 + 30) * mx.NS}
        self.assertEqual((float(first['hA']['busy_cores']), float(first['hB']['busy_cores'])), (1.0, 2.0))
        second = {r['host']: r for r in out if int(r['time_ns']) == (mx.DAY1 + 900 + 30) * mx.NS}
        self.assertEqual(float(second['hA']['busy_cores']), 3.0)
        self.assertEqual(second['hA']['phys_cpus_pool'], '4')                             # the size is carried, not averaged
        # The observations are written reporter by reporter, not in time order: the result is the same.
        grouped = sorted(rows, key=lambda r: r.split(',')[1])
        self.assertNotEqual(grouped, rows)
        self.assertEqual(self.pr.thin_pool(self.pool(grouped), self.dir / 'grouped.gz', 15, 'mean'), (60, 4))
        with gzip.open(self.dir / 'grouped.gz', 'rt') as fh:
            again = list(csv.DictReader(fh))
        self.assertEqual(sorted((r['time_ns'], r['host'], r['busy_cores']) for r in again),
                         sorted((r['time_ns'], r['host'], r['busy_cores']) for r in out))
        self.assertEqual(os.listdir(self.dir).count('grouped.gz'), 1)
        self.assertFalse([f for f in os.listdir(self.dir) if f.startswith('thin-pool-')])   # the day files are removed
        _, kept = self.pr.thin_pool(self.pool(rows), self.dir / 'point.gz', 15, 'point')
        self.assertEqual(kept, 4)                                                         # two reporters at minutes 0 and 15


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class ThinReplayTest(unittest.TestCase):
    def test_sizing_on_the_coarse_copy_is_replayed_on_the_capture_as_recorded(self):
        pr = load()
        mx.MemberExposureTests.roots = []
        root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, root)
        source = root / 'stage' / mx.SERIAL
        app = root                                              # nfit, nfit-profile, etc/ and output/ are here
        for name in ('nfit', 'nfit-profile'):
            os.chmod(app / name, 0o700)
        with (app / 'config.csv').open('w') as f:
            w = csv.writer(f)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm, role in [('vmA', 'AIX'), ('vmB', 'VIO Server'), ('vmS', 'PowerHA Standby')]:
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, role, 'pool', pool, ent, vp])
        case = root / 'case'
        case.mkdir()
        pr.THIN.update(minutes=15, mode='mean')
        pr.REFERENCE.update({('case-member-001', p): 0.33 for p in ('P-99W1', 'G3-95W15', 'O3-95W15', 'B3-95W15', 'G2-95W15', 'O2-95W15', 'G1-99W5', 'O1-99W5', 'B1-99W5')})
        self.addCleanup(pr.REFERENCE.clear)
        try:
            snapshot, replay = pr.run_case(app, case, source, ('2026-09-01', '2026-09-01', '2026-09-02', '2026-09-02'), pr.perl_env())
        finally:
            pr.THIN.update(minutes=0, mode='mean')
        # The coarse copy holds 96 samples of each of the four LPARs on each of the two days.
        thin_rows = (case / 'thin' / 'stage' / mx.SERIAL / '.nfit.cache.data').read_text().splitlines()
        self.assertEqual(len(thin_rows) - 1, 4 * 96 * 2)
        support = [v['cutProtection']['support'] for v in snapshot['members']['vmA']['profiles'].values() if 'cutProtection' in v]
        self.assertTrue(support and all(s['pairedMinutes'] <= 96 for s in support))
        # The replay scored the planning profile at its published figure on the one-minute capture.
        planning = snapshot['members']['vmA']['planningProfile']
        labels = {l: t for t in replay['trackers'] if t['vm'] == 'vmA' for level in t['levels'] for l in level['labels']}
        self.assertIn(planning + '/published', labels)
        self.assertGreater(labels[planning + '/published']['pairedMinutes'], 96)         # minutes, not intervals
        # Coarse evidence: this cycle's entitlement under both guarantee policies is recorded and scored,
        # with the comparison run's (--reference-records).
        by = snapshot['members']['vmA']['profiles'][planning].get('cycleByPolicy')
        self.assertTrue(by, 'the coarse sizing records both policies')
        self.assertLessEqual(by['normal']['entitlement'], by['strict']['entitlement'])
        self.assertEqual(snapshot['members']['vmA']['profiles'][planning]['currentCycle']['entitlement'], by['normal']['entitlement'])   # the default is normal planning
        values = {l: level['value'] for t in replay['trackers'] if t['vm'] == 'vmA' for level in t['levels'] for l in level['labels']}
        self.assertEqual(values[planning + '/cycleNormal'], by['normal']['entitlement'])
        self.assertEqual(values[planning + '/cycleStrict'], by['strict']['entitlement'])
        self.assertEqual(values.get(planning + '/cycleReference'), 0.33)
        # Without a data revision in the manifest the replay could not accept the proposal: refused at the start.
        manifest = source / '.nfit.cache.manifest'
        manifest.write_text('\n'.join(l for l in manifest.read_text().splitlines() if not l.startswith('data_revision')) + '\n')
        with self.assertRaises(RuntimeError):
            pr.thin_stage(root / 'case2', source, 15, 'point')
        record = pr.anonymise('X-01', snapshot, replay, ('2026-09-01', '2026-09-01', '2026-09-02', '2026-09-02'))
        rows = [r for r in record['records'] if r['planning'] and r['member'].endswith('001')]
        self.assertTrue(rows and rows[0]['later'].get('published'))


if __name__ == '__main__':
    unittest.main()
