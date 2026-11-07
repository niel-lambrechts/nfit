#!/usr/bin/env python3
# ==============================================================================
# Fixture tests for the processor-pool evidence sidecar (Increment 3d):
# --pool-capture off|optional|required and --pool-only. Raw reporter rows with
# exact timestamps and source identity, original field names, stable header,
# per-slice gzip members, explicit outcomes, CPU files never affected, an
# existing usable pool file preserved when a replacement is not complete.
# Same pool ID on two managed systems stays in two files. Synthetic data only.
# ==============================================================================
import calendar
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from influxdb import InfluxDBClient
    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False

from fake_influxdb import FakeInfluxDB, Fault  # noqa: E402

NS = 1_000_000_000
STAGER = os.environ.get('NFIT_STAGER', os.path.join(HERE, '..', 'nfit-stage-influxdb'))
DAY_NS = calendar.timegm((2026, 10, 1, 0, 0, 0)) * NS
SERIAL_OF = {'vmA': 'S1', 'vmB': 'S1', 'vmC': 'S1', 'vmD': 'S2'}


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def build_dataset():
    """Two hours from 00:00 UTC. S1: vmA and vmB report pool 0 with the legacy
    integer capacity fields only, vmC reports pool 1 with the _cores/_raw
    fields only (as observed on the live estate). S2: vmD reports pool 0, a
    different pool with the same ID. Reporters sample at different seconds."""
    pts = []
    offsets = {'vmA': 7, 'vmB': 31, 'vmC': 45, 'vmD': 12}
    for minute in range(120):
        for host, off in offsets.items():
            t = DAY_NS + minute * 60 * NS + off * NS + 123_456_789
            tags = {'host': host, 'serial_no': SERIAL_OF[host], 'os': 'AIX'}
            pts.append({'measurement': 'cpu_util', 'time_ns': t, 'tags': tags,
                        'fields': {'physical_consumed': 0.5, 'physical_busy': 0.2, 'freq_pct': 105.9}})
            pts.append({'measurement': 'kernel', 'time_ns': t, 'tags': tags, 'fields': {'run_queue': 1.0}})
            if host == 'vmC':
                pool = {'pool_id': 1, 'phys_cpus_pool': 11, 'idle_cores': 8.5, 'busy_cores': 2.4,
                        'max_capacity_cores': 11.0, 'max_capacity_raw': 1100,
                        'entitled_capacity_cores': 2.25, 'entitled_capacity_raw': 225}
            elif host == 'vmD':
                pool = {'pool_id': 0, 'phys_cpus_pool': 120, 'idle_cores': 60.0 + minute / 10,
                        'busy_cores': 59.0, 'max_capacity': 120, 'entitled_capacity': 118}
            else:
                pool = {'pool_id': 0, 'phys_cpus_pool': 40, 'idle_cores': 20.0 + minute / 100,
                        'busy_cores': 19.5, 'max_capacity': 40, 'entitled_capacity': 23}
            pool.update({'scaled_busy_cores': 19.4, 'max_cores': 40.0,
                         'global_pool_busy_cores': 30.0, 'global_pool_tot_cores': 40.002})
            pts.append({'measurement': 'processor_pool', 'time_ns': t + NS, 'tags': tags, 'fields': pool})
    return pts


def config_events(client, db_version, fields, measurement, entity_filter, start_date, end_date, verbose=False):
    """Stand-in for Phase 2 (the fake does not implement LAST() buckets)."""
    values = {'entitlement': 1.0, 'smtthreads': 8, 'processorMHz': 3000, 'processorModel': 'm',
              'processorFamily': 'f', 'desired_vcpus': 2, 'pool_id': 0, 'phys_cpus_pool': 8, 'capped': 0}
    anchor = DAY_NS // NS
    hosts = [h for h in SERIAL_OF if f"'{SERIAL_OF[h]}'" in entity_filter or f"'{h}'" in entity_filter]
    return {f: [{'ts': anchor, 'host': h, 'value': SERIAL_OF[h] if f == 'serial_no' else values[f]}
                for h in hosts] for f in fields}


def gzip_members(path):
    with open(path, 'rb') as fh:
        data = fh.read()
    members = 0
    while data:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        d.decompress(data)
        data = d.unused_data
        members += 1
    return members


@unittest.skipUnless(HAVE_CLIENT, 'influxdb client package not installed')
class PoolCaptureTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='pool-root-')
        shutil.copy(STAGER, os.path.join(cls.root, 'nfit-stage-influxdb'))
        cls.st = load(os.path.join(cls.root, 'nfit-stage-influxdb'), 'stager_pool')
        cls.st.SLICE_RETRY_DELAY_S = 0
        cls.fake = FakeInfluxDB(build_dataset())
        cls.port = cls.fake.start()
        cls.client = InfluxDBClient(host='127.0.0.1', port=cls.port, database='nmon', retries=1)
        cls.st.get_db_client = lambda path: (cls.client, 1, {'DATABASE': 'nmon'})
        cls.st.get_config_events_by_measurement = config_events

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.fake.faults = []
        self.fake.field_types = {}
        self.tmp = tempfile.mkdtemp(prefix='pool-test-')
        self.stage = os.path.join(self.tmp, 'stage')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def run_main(self, *argv, stage=None):
        saved = sys.argv
        sys.argv = ['nfit-stage-influxdb', '--datadir', stage or self.stage, '--slice-hours', '1'] + list(argv)
        out, err = io.StringIO(), io.StringIO()
        rc = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                self.st.main()
        except SystemExit as exc:
            rc = exc.code or 0
        finally:
            sys.argv = saved
        return rc, out.getvalue(), err.getvalue()

    def stage_full(self, *extra, stage=None):
        rc, out, err = self.run_main('--mgsys', 'S1,S2', '--startd', '2026-10-01', '--endd', '2026-10-01',
                                     *extra, stage=stage)
        return rc, out, err

    def path(self, serial, name, stage=None):
        return os.path.join(stage or self.stage, serial, name)

    def manifest(self, serial, stage=None):
        return self.st.read_cache_manifest(self.path(serial, '.nfit.cache.manifest', stage))

    def manifest_lines(self, serial, stage=None):
        with open(self.path(serial, '.nfit.cache.manifest', stage)) as fh:
            return fh.read().splitlines()

    def pool_rows(self, serial, stage=None):
        with gzip.open(self.path(serial, self.st.POOL_OBSERVATIONS_FILE, stage), 'rt') as fh:
            return fh.read().splitlines()

    def cpu_bytes(self, serial, stage=None):
        out = {}
        for name in ('.nfit.cache.data', '.nfit.cache.states'):
            with open(self.path(serial, name, stage), 'rb') as fh:
                out[name] = fh.read()
        return out

    def hour_slice(self, hour):
        s = DAY_NS + hour * 3600 * NS
        return ('SELECT', '"processor_pool"', f'time >= {s} AND')

    # -- full staging runs ---------------------------------------------------------
    def test_off_by_default_writes_no_pool_file_or_keys(self):
        before = len(self.fake.queries)
        rc, _, err = self.stage_full()
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(self.path('S1', self.st.POOL_OBSERVATIONS_FILE)))
        self.assertFalse(any(k.startswith('pool_') for k in self.manifest('S1')))
        self.assertFalse([q for q in self.fake.queries[before:] if 'FROM "processor_pool"' in q and 'GROUP BY *' in q])

    def test_complete_capture_rows_header_identity_and_manifest(self):
        rc, out, err = self.stage_full('--pool-capture', 'optional')
        self.assertEqual(rc, 0, err)
        rows = self.pool_rows('S1')
        self.assertEqual(rows[0], ','.join(self.st.POOL_COLUMNS))
        self.assertEqual(rows[0].split(',')[:4], ['time_ns', 'host', 'series_id', 'pool_id'])
        self.assertEqual(len(rows) - 1, 3 * 120)
        cols = rows[0].split(',')
        recs = [dict(zip(cols, r.split(','))) for r in rows[1:]]
        a = [r for r in recs if r['host'] == 'vmA']
        c = [r for r in recs if r['host'] == 'vmC']
        self.assertEqual(int(a[0]['time_ns']), DAY_NS + 8 * NS + 123_456_789)          # exact native ns
        self.assertEqual((a[0]['max_capacity'], a[0]['entitled_capacity'], a[0]['max_capacity_cores']),
                         ('40', '23', ''))                                              # legacy kept, raw names
        self.assertEqual((c[0]['max_capacity_cores'], c[0]['max_capacity_raw'], c[0]['max_capacity']),
                         ('11.0', '1100', ''))
        with open(self.path('S1', self.st.POOL_SERIES_FILE)) as fh:
            series = json.load(fh)
        self.assertEqual(sorted(t['host'] for t in series.values()), ['vmA', 'vmB', 'vmC'])
        self.assertEqual(series[a[0]['series_id']], {'host': 'vmA', 'os': 'AIX', 'serial_no': 'S1'})
        m = self.manifest('S1')
        self.assertEqual(m['pool_capture_status'], 'complete')
        self.assertEqual(m['pool_capture_mode'], 'optional')
        self.assertEqual(m['pool_scope'], 'managed system')
        self.assertEqual(m['pool_rows'], '360')
        self.assertEqual(m['pool_reporters'], '3')
        self.assertEqual(m['pool_ids'], '0, 1')
        self.assertIn('max_capacity=240; entitled_capacity=240', m['pool_nonempty'])
        self.assertIn('max_capacity_cores=120', m['pool_nonempty'])
        self.assertIn('max_capacity=unresolved (legacy integer)', m['pool_units'])
        self.assertEqual(m['pool_revision'], 'sha256:' + hashlib.sha256(
            open(self.path('S1', self.st.POOL_OBSERVATIONS_FILE), 'rb').read()).hexdigest())
        self.assertEqual(m['pool_slices'], 'total=24; failed=0')
        self.assertIn('S1: pool capture complete, 360 observations from 3 reporter(s)', out)

    def test_same_pool_id_on_two_systems_stays_separate(self):
        rc, _, err = self.stage_full('--pool-capture', 'optional')
        self.assertEqual(rc, 0, err)
        s2 = self.pool_rows('S2')
        self.assertEqual(len(s2) - 1, 120)
        self.assertTrue(all(',vmD,' in r for r in s2[1:]))
        self.assertTrue(all(',vmD,' not in r for r in self.pool_rows('S1')))
        self.assertEqual(self.manifest('S2')['pool_ids'], '0')

    def test_one_gzip_member_per_slice_with_rows(self):
        rc, _, err = self.stage_full('--pool-capture', 'optional')
        self.assertEqual(rc, 0, err)
        self.assertEqual(gzip_members(self.path('S1', self.st.POOL_OBSERVATIONS_FILE)), 1 + 2)

    def test_cpu_files_are_identical_with_and_without_pool_capture(self):
        other = os.path.join(self.tmp, 'other')
        self.assertEqual(self.stage_full()[0], 0)
        self.assertEqual(self.stage_full('--pool-capture', 'required', stage=other)[0], 0)
        for serial in ('S1', 'S2'):
            self.assertEqual(self.cpu_bytes(serial), self.cpu_bytes(serial, other))
            strip = lambda lines: [x for x in lines if not x.startswith(('pool_', 'build_timestamp'))]
            self.assertEqual(strip(self.manifest_lines(serial)), strip(self.manifest_lines(serial, other)))


    # -- failure outcomes ------------------------------------------------------------
    def test_failed_slice_gives_partial_and_optional_run_succeeds(self):
        self.fake.faults = [Fault(self.hour_slice(1), 'interrupt', after=0, times=None)]
        rc, _, err = self.stage_full('--pool-capture', 'optional')
        self.assertEqual(rc, 0, err)
        m = self.manifest('S1')
        self.assertEqual(m['pool_capture_status'], 'partial')
        s = DAY_NS + 3600 * NS
        self.assertEqual(m['pool_slices'], f'total=24; failed=1; failed_slices=[{s}, {s + 3600 * NS})')
        self.assertEqual(len(self.pool_rows('S1')) - 1, 3 * 60)
        self.assertIn('pool capture not complete', err)

    def test_required_mode_exits_3_with_cpu_files_written(self):
        reference = os.path.join(self.tmp, 'reference')
        self.assertEqual(self.stage_full(stage=reference)[0], 0)
        self.fake.faults = [Fault(self.hour_slice(1), 'interrupt', after=0, times=None)]
        rc, _, _ = self.stage_full('--pool-capture', 'required')
        self.assertEqual(rc, 3)
        self.assertEqual(self.cpu_bytes('S1'), self.cpu_bytes('S1', reference))
        self.assertEqual(self.manifest('S1')['pool_capture_status'], 'partial')

    def test_query_error_fails_without_publishing(self):
        self.fake.faults = [Fault(('SELECT', '"processor_pool"'), 'error', times=None,
                                  message='field type conflict')]
        rc, _, err = self.stage_full('--pool-capture', 'optional')
        self.assertEqual(rc, 0, err)
        m = self.manifest('S1')
        self.assertEqual(m['pool_capture_status'], 'failed')
        self.assertEqual(m['pool_error'], 'query error: field type conflict')
        self.assertNotIn('pool_file', m)
        self.assertFalse(os.path.exists(self.path('S1', self.st.POOL_OBSERVATIONS_FILE)))
        self.assertFalse(os.path.exists(self.path('S1', self.st.POOL_OBSERVATIONS_FILE + '.work')))

    def test_measurement_absent_is_unsupported(self):
        self.fake.field_types['processor_pool'] = {}
        rc, _, err = self.stage_full('--pool-capture', 'optional')
        self.assertEqual(rc, 0, err)
        m = self.manifest('S1')
        self.assertEqual(m['pool_capture_status'], 'unsupported')
        self.assertIn('pool_id=absent from schema', m['pool_field_availability'])
        self.assertFalse(os.path.exists(self.path('S1', self.st.POOL_OBSERVATIONS_FILE)))

    def test_append_with_pool_capture_is_refused(self):
        self.assertEqual(self.stage_full()[0], 0)
        rc, _, err = self.run_main('--mgsys', 'S1,S2', '--append', '--pool-capture', 'optional')
        self.assertEqual(rc, 2)
        self.assertIn('not yet available with --append', err)

    # -- pool-only -----------------------------------------------------------------------
    def test_pool_only_adds_pool_file_without_touching_cpu_files(self):
        self.assertEqual(self.stage_full('--l1-schema', '2')[0], 0)
        before = self.cpu_bytes('S1')
        mtimes = {n: os.stat(self.path('S1', n)).st_mtime_ns for n in before}
        lines_before = self.manifest_lines('S1')
        rc, out, err = self.run_main('--mgsys', 'S1', '--pool-only')
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.cpu_bytes('S1'), before)
        self.assertEqual({n: os.stat(self.path('S1', n)).st_mtime_ns for n in before}, mtimes)
        after = self.manifest_lines('S1')
        self.assertEqual([x for x in after if not x.startswith('pool_')], lines_before)  # revisions verbatim
        m = self.manifest('S1')
        self.assertEqual((m['pool_capture_status'], m['pool_capture_mode']), ('complete', 'pool-only'))
        # The staging window: whole UTC days, ending at 23:59:59 + 1 ns.
        end = self.st.utc_date_to_ns('2026-10-01', '23:59:59') + 1
        self.assertEqual(m['pool_window_ns'], f'[{DAY_NS}, {end})')
        self.assertEqual(len(self.pool_rows('S1')) - 1, 360)
        self.assertFalse(os.path.exists(self.path('S2', self.st.POOL_OBSERVATIONS_FILE)))

    def test_pool_only_keeps_existing_file_when_replacement_fails(self):
        self.assertEqual(self.stage_full('--pool-capture', 'optional')[0], 0)
        with open(self.path('S1', self.st.POOL_OBSERVATIONS_FILE), 'rb') as fh:
            published = fh.read()
        old = [x for x in self.manifest_lines('S1') if x.startswith('pool_')]
        self.fake.faults = [Fault(('SELECT', '"processor_pool"'), 'interrupt', after=0, times=None)]
        rc, _, _ = self.run_main('--mgsys', 'S1', '--pool-only')
        self.assertEqual(rc, 3)
        with open(self.path('S1', self.st.POOL_OBSERVATIONS_FILE), 'rb') as fh:
            self.assertEqual(fh.read(), published)
        new = [x for x in self.manifest_lines('S1') if x.startswith('pool_')]
        self.assertEqual([x for x in new if not x.startswith('pool_last_attempt')], old)
        attempt = [x for x in new if x.startswith('pool_last_attempt')]
        self.assertEqual(len(attempt), 1)
        self.assertIn('failed at', attempt[0])
        self.assertIn('existing pool file kept', attempt[0])
        self.assertFalse(os.path.exists(self.path('S1', self.st.POOL_OBSERVATIONS_FILE + '.work')))

    def test_partial_replacement_does_not_replace_complete_file(self):
        self.assertEqual(self.stage_full('--pool-capture', 'optional')[0], 0)
        with open(self.path('S1', self.st.POOL_OBSERVATIONS_FILE), 'rb') as fh:
            published = fh.read()
        self.fake.faults = [Fault(self.hour_slice(0), 'interrupt', after=0, times=None)]
        rc, _, _ = self.run_main('--mgsys', 'S1', '--pool-only')
        self.assertEqual(rc, 3)
        with open(self.path('S1', self.st.POOL_OBSERVATIONS_FILE), 'rb') as fh:
            self.assertEqual(fh.read(), published)
        self.assertEqual(self.manifest('S1')['pool_capture_status'], 'complete')
        self.assertIn('partial at', self.manifest('S1')['pool_last_attempt'])

    def test_pool_only_needs_an_existing_cache_and_mgsys(self):
        rc, _, err = self.run_main('--mgsys', 'S1', '--pool-only')
        self.assertEqual(rc, 1)
        self.assertIn('no staged cache', err)
        rc, _, err = self.run_main('--vms', 'vmA', '--pool-only')
        self.assertEqual(rc, 2)
        self.assertIn('--pool-only needs --mgsys', err)
        rc, _, err = self.run_main('--mgsys', 'S1', '--pool-only', '--days', '2')
        self.assertEqual(rc, 2)

    # -- lifecycle with later CPU runs ------------------------------------------------------
    def test_later_run_without_pool_capture_keeps_pool_lines(self):
        self.assertEqual(self.stage_full('--pool-capture', 'optional')[0], 0)
        old = [x for x in self.manifest_lines('S1') if x.startswith('pool_')]
        rc, _, err = self.run_main('--mgsys', 'S1,S2', '--append', '--endd', '2026-10-01')
        self.assertEqual(rc, 0, err)
        self.assertEqual([x for x in self.manifest_lines('S1') if x.startswith('pool_')], old)

    def test_cleanup_removes_pool_files_and_lines(self):
        self.assertEqual(self.stage_full('--pool-capture', 'optional')[0], 0)
        rc, _, err = self.stage_full('--cleanup')
        self.assertEqual(rc, 0, err)
        for name in (self.st.POOL_OBSERVATIONS_FILE, self.st.POOL_SERIES_FILE):
            self.assertFalse(os.path.exists(self.path('S1', name)))
        self.assertFalse(any(x.startswith('pool_') for x in self.manifest_lines('S1')))


if __name__ == '__main__':
    unittest.main(verbosity=1)
