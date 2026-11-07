#!/usr/bin/env python3
# ==============================================================================
# Regression and fault cover for nfit-stage-influxdb performance acquisition
# (chunked InfluxQL v1 over half-open time slices, exact ns timestamps).
#
# The oracle is the frozen pre-change stager (fixtures/stager-legacy/...),
# driven through the real influxdb client against fake_influxdb. The new
# acquisition must reproduce its master file byte for byte, including the
# legacy sort/merge behaviour, while chunking and slicing differently.
# ==============================================================================
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from influxdb import InfluxDBClient
    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False

from fake_influxdb import FakeInfluxDB, Fault  # noqa: E402

NS = 1_000_000_000
NEW_STAGER = os.environ.get('NFIT_STAGER', os.path.join(HERE, '..', 'nfit-stage-influxdb'))
LEGACY_STAGER = os.path.join(HERE, 'fixtures', 'stager-legacy', 'nfit-stage-influxdb.b3131f2')


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def day_ns(date_str):
    import calendar
    import time
    return calendar.timegm(time.strptime(date_str, '%Y-%m-%d')) * NS


def build_dataset():
    """Three days of one-minute data for five hosts on SER1 plus one host on
    SER2, with deliberate gaps, duplicates, sub-second times and boundary
    points. Values include integers and floats whose repr is not short."""
    pts = []
    base = day_ns('2026-10-01')
    offsets = {'vm01': 0, 'vm02': 7 * NS, 'vm03': 31 * NS + 250_000_000, 'vm04': 0, 'vm05': 59 * NS}
    hosts = [(h, 'SER1') for h in sorted(offsets)] + [('vm99', 'SER2')]
    for d in range(3):
        for minute in range(1440):
            for idx, (host, serial) in enumerate(hosts):
                t = base + d * 86400 * NS + minute * 60 * NS + offsets.get(host, 0)
                tags = {'host': host, 'serial_no': serial}
                physc = round(0.5 + 0.1 * idx + 0.001 * minute, 4)
                if host == 'vm01' and minute % 97 == 0:
                    physc = 0.1 + 0.2                       # repr 0.30000000000000004
                runq = (minute % 7) if host != 'vm04' else float(minute % 5) + 0.5
                if not (host == 'vm02' and d == 1 and 600 <= minute <= 630):
                    pts.append({'measurement': 'cpu_util', 'time_ns': t, 'tags': tags,
                                'fields': {'physical_consumed': physc}})
                if not (host == 'vm03' and d == 0 and 100 <= minute <= 110):
                    pts.append({'measurement': 'kernel', 'time_ns': t, 'tags': tags,
                                'fields': {'run_queue': runq}})
    # Exact duplicates from a second tag set of the same host.
    for minute in range(200, 205):
        t = base + 86400 * NS + minute * 60 * NS
        pts.append({'measurement': 'cpu_util', 'time_ns': t,
                    'tags': {'host': 'vm04', 'serial_no': 'SER1', 'collector': 'b'},
                    'fields': {'physical_consumed': 9.75}})
    # Same second, different sub-second time.
    t = base + 86400 * NS + 720 * 60 * NS + 59 * NS + 600_000_000
    pts.append({'measurement': 'cpu_util', 'time_ns': t, 'tags': {'host': 'vm05', 'serial_no': 'SER1'},
                'fields': {'physical_consumed': 1.5}})
    # Window boundaries (window is 2026-10-01 .. 2026-10-03 inclusive).
    for t, value in ((base - 1000, 7.0),                                   # 1 µs before start: excluded
                     (base + 3 * 86400 * NS - NS // 2, 7.1),               # 23:59:59.5 on the last day: excluded
                     (base + 3 * 86400 * NS, 7.2)):                        # next day 00:00:00: excluded
        pts.append({'measurement': 'cpu_util', 'time_ns': t, 'tags': {'host': 'vm01', 'serial_no': 'SER1'},
                    'fields': {'physical_consumed': value}})
    return pts


@unittest.skipUnless(HAVE_CLIENT, 'influxdb client package not installed')
class AcquisitionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.new = load(NEW_STAGER, 'stager_new')
        cls.new.SLICE_RETRY_DELAY_S = 0
        cls.legacy = load(LEGACY_STAGER, 'stager_legacy')
        cls.fake = FakeInfluxDB(build_dataset())
        cls.port = cls.fake.start()
        cls.client = InfluxDBClient(host='127.0.0.1', port=cls.port, database='nmon', retries=1)
        cls.start_ns = cls.new.utc_date_to_ns('2026-10-01')
        cls.end_exclusive_ns = cls.new.utc_date_to_ns('2026-10-03', '23:59:59') + 1
        cls.serial_filter = "(\"serial_no\" = 'SER1')"
        cls.host_filter = "(host = 'vm01' OR host = 'vm05')"
        cls.legacy_cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def setUp(self):
        self.fake.faults = []
        self.tmp = tempfile.mkdtemp(prefix='stager-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def run_legacy(self, entity_filter):
        if entity_filter not in self.legacy_cache:
            from datetime import datetime, timezone
            start_dt = datetime.strptime('2026-10-01T00:00:00Z', '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            end_dt = datetime.strptime('2026-10-03T23:59:59Z', '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            tf = f"time >= {int(start_dt.timestamp() * NS)} AND time <= {int(end_dt.timestamp() * NS)}"
            tmp = tempfile.mkdtemp(prefix='stager-legacy-')
            master, vms, mn, mx = self.legacy.stream_performance_data_optimized(
                self.client, 1, entity_filter, tf, tmp, types.SimpleNamespace(verbose=False))
            with open(master, 'rb') as fh:
                data = fh.read()
            shutil.rmtree(tmp, ignore_errors=True)
            self.legacy_cache[entity_filter] = (data, vms, mn, mx)
        return self.legacy_cache[entity_filter]

    def run_new(self, entity_filter, slice_hours=6, chunk_size=37):
        slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, slice_hours * 3600 * NS)
        stats = {}
        args = types.SimpleNamespace(verbose=False, chunk_size=chunk_size)
        master, vms, mn, mx = self.new.stream_performance_data_optimized(
            self.client, 'nmon', entity_filter, slices, self.tmp, args, stats_out=stats)
        with open(master, 'rb') as fh:
            data = fh.read()
        return data, vms, mn, mx, stats, slices

    def assertSameAsLegacy(self, new_result, entity_filter):
        """The new master file equals the legacy one except for the corrected
        merge defect. Legacy sorted each temporary file's header after the data,
        so it lost the earliest sample of each metric and appended the header
        as a final row. In these datasets both metrics' earliest sample shares
        one (timestamp, VM) key, which legacy therefore lost entirely."""
        data, vms, mn, mx = new_result[:4]
        l_data, l_vms, l_mn, l_mx = self.run_legacy(entity_filter)
        new_lines, old_lines = data.decode().splitlines(), l_data.decode().splitlines()
        self.assertEqual(old_lines[-1], 'timestamp,vm_name,value,value')
        self.assertEqual(new_lines[0], old_lines[0])
        first_key = ','.join(new_lines[1].split(',')[:2])
        self.assertFalse(any(r.startswith(first_key + ',') for r in old_lines), 'legacy kept the first key')
        self.assertTrue(all(new_lines[1].split(',')[2:]), 'first sample is incomplete')
        self.assertEqual(new_lines[2:], old_lines[1:-1], 'master file differs from the legacy acquisition '
                         'beyond the corrected first sample and header row')
        self.assertEqual(vms, l_vms)
        self.assertEqual(mn.strftime('%Y-%m-%d %H:%M:%S'), l_mn.strftime('%Y-%m-%d %H:%M:%S'))
        self.assertEqual(mx.strftime('%Y-%m-%d %H:%M:%S'), l_mx.strftime('%Y-%m-%d %H:%M:%S'))
        self.assertEqual(int(mn.timestamp()), int(l_mn.timestamp()))
        self.assertEqual(int(mx.timestamp()), int(l_mx.timestamp()))

    # -- pure functions --------------------------------------------------------
    def test_window_ns_matches_legacy_float_arithmetic(self):
        from datetime import datetime, timezone, timedelta
        d = datetime(2000, 1, 1, tzinfo=timezone.utc)
        for _ in range(0, 15000, 37):
            ds = d.strftime('%Y-%m-%d')
            legacy_start = int(d.timestamp() * NS)
            legacy_end = int((d + timedelta(hours=23, minutes=59, seconds=59)).timestamp() * NS)
            self.assertEqual(self.new.utc_date_to_ns(ds), legacy_start)
            self.assertEqual(self.new.utc_date_to_ns(ds, '23:59:59'), legacy_end)
            d += timedelta(days=37)

    def test_slices_cover_window_exactly(self):
        for hours in (1, 5, 6, 24, 1000):
            slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, hours * 3600 * NS)
            self.assertEqual(slices[0][0], self.start_ns)
            self.assertEqual(slices[-1][1], self.end_exclusive_ns)
            for (a_start, a_end), (b_start, _) in zip(slices, slices[1:]):
                self.assertEqual(a_end, b_start, 'gap or overlap between slices')
                self.assertLess(a_start, a_end)

    def test_timestamp_formatting_floors_nanoseconds(self):
        base = day_ns('2026-10-01')
        cases = {base: '2026-10-01 00:00:00',
                 base + 999_999_999: '2026-10-01 00:00:00',
                 base + NS: '2026-10-01 00:00:01',
                 base + 86400 * NS - 1: '2026-10-01 23:59:59'}
        for ns, expected in cases.items():
            self.assertEqual(self.new.ns_to_legacy_timestamp(ns), expected)
            self.assertEqual(self.new.ns_to_utc_datetime(ns).strftime('%Y-%m-%d %H:%M:%S'), expected)

    # -- transport format negotiation ------------------------------------------
    def test_stream_keeps_client_messagepack_negotiation(self):
        """The real client defaults to MessagePack. client.request() unpacks the
        whole body eagerly and fails on concatenated chunk objects, and JSON is
        not equivalent: Go writes the float 1.0 as 1. The stream must keep the
        client's MessagePack negotiation, decode it incrementally and yield the
        same typed points as an ordinary client query."""
        query = (f'SELECT "physical_consumed" FROM "cpu_util" '
                 f"WHERE host = 'vm01' AND time >= {self.start_ns} "
                 f'AND time < {self.start_ns + 3 * 60 * NS} GROUP BY "host"')
        points = list(self.new.iter_influxql_points(self.client, 'nmon', query, 1))
        self.assertEqual(self.fake.accept_headers[-1], 'application/x-msgpack')
        self.assertEqual([point['time'] for _, point in points],
                         [self.start_ns + minute * 60 * NS for minute in range(3)])
        self.assertTrue(all(tags['host'] == 'vm01' for tags, _ in points))
        self.assertEqual([point['physical_consumed'] for _, point in points],
                         [0.1 + 0.2, 0.501, 0.502])

        ordinary = self.client.query(query, epoch='ns')
        self.assertEqual(self.fake.accept_headers[-1], 'application/x-msgpack')
        self.assertEqual(list(ordinary.get_points()), [point for _, point in points])

    def test_whole_number_float_keeps_float_type(self):
        """A float field holding a whole number (2.0) must reach the cache as
        2.0, as the legacy MessagePack path wrote it, not as 2."""
        query = (f'SELECT "physical_consumed" FROM "cpu_util" '
                 f"WHERE host = 'vm05' AND time >= {self.start_ns} "
                 f'AND time < {self.start_ns + 2 * 3600 * NS} GROUP BY "host"')
        values = [point['physical_consumed'] for _, point in
                  self.new.iter_influxql_points(self.client, 'nmon', query, 7)]
        whole = [v for v in values if v is not None and float(v).is_integer()]
        self.assertTrue(whole, 'fixture must contain a whole-number float')
        self.assertTrue(all(isinstance(v, float) for v in whole))

    def test_json_response_is_still_accepted(self):
        """A server that answers in JSON is still read, line by line."""
        query = (f'SELECT "physical_consumed" FROM "cpu_util" '
                 f"WHERE host = 'vm01' AND time >= {self.start_ns} "
                 f'AND time < {self.start_ns + 3 * 60 * NS} GROUP BY "host"')
        saved = dict(self.client._headers)
        try:
            self.client._headers['Accept'] = 'application/json'
            points = list(self.new.iter_influxql_points(self.client, 'nmon', query, 1))
        finally:
            self.client._headers.clear()
            self.client._headers.update(saved)
        self.assertEqual(self.fake.accept_headers[-1], 'application/json')
        self.assertEqual(len(points), 3)

    # -- equivalence with the legacy acquisition -------------------------------
    def test_serial_filter_sliced_chunked_matches_legacy(self):
        result = self.run_new(self.serial_filter, slice_hours=6, chunk_size=37)
        self.assertSameAsLegacy(result, self.serial_filter)

    def test_host_filter_one_point_chunks_hourly_slices_matches_legacy(self):
        result = self.run_new(self.host_filter, slice_hours=1, chunk_size=1)
        self.assertSameAsLegacy(result, self.host_filter)

    def test_daily_slices_large_chunks_matches_legacy(self):
        result = self.run_new(self.serial_filter, slice_hours=24, chunk_size=100000)
        self.assertSameAsLegacy(result, self.serial_filter)

    def test_empty_selection_matches_legacy(self):
        empty = "(host = 'no_such_vm')"
        data, vms, mn, mx = self.run_new(empty)[:4]
        l_data, l_vms, l_mn, l_mx = self.run_legacy(empty)
        self.assertEqual(data, l_data)
        self.assertEqual((vms, mn, mx), (l_vms, l_mn, l_mx))

    # -- missing values, duplicates, boundaries ----------------------------------
    def test_counters_report_duplicates_and_same_second(self):
        stats = self.run_new(self.serial_filter)[4]
        self.assertEqual(stats['physc'].exact_duplicates, 5)
        self.assertEqual(stats['physc'].same_second, 1)
        self.assertEqual(stats['physc'].out_of_order, 0)
        self.assertEqual(stats['runq'].exact_duplicates, 0)

    def test_missing_metric_leaves_empty_column_not_lost_row(self):
        data = self.run_new(self.serial_filter)[0].decode()
        rows = [r for r in data.splitlines() if r.startswith('2026-10-02 10:1') and ',vm02,' in r]
        self.assertTrue(rows)
        for r in rows:
            ts, vm, physc, runq = r.split(',')
            self.assertEqual(physc, '')
            self.assertNotEqual(runq, '')

    def test_boundary_points(self):
        data = self.run_new(self.serial_filter)[0].decode()
        self.assertIn('2026-10-03 23:59:59,vm05,', data)        # exactly the last included second
        self.assertNotIn(',7.0,', data)                         # before start
        self.assertNotIn(',7.1,', data)                         # 23:59:59.5 on the last day
        self.assertNotIn(',7.2,', data)                         # next day
        self.assertIn('2026-10-02 00:00:00,vm01,', data)        # point on a slice boundary, once
        self.assertEqual(data.count('2026-10-02 00:00:00,vm01,'), 1)

    # -- interrupted and failing responses -------------------------------------
    def slice_match(self, measurement, slices, index):
        s, e = slices[index]
        return (f'FROM "{measurement}"', f'time >= {s} AND time < {e}')

    def test_interrupted_response_is_rolled_back_and_retried(self):
        clean = self.run_new(self.serial_filter)
        slices = clean[5]
        self.fake.faults = [Fault(self.slice_match('cpu_util', slices, 5), 'interrupt', after=3)]
        faulted = self.run_new(self.serial_filter)
        self.assertEqual(faulted[0], clean[0])
        self.assertEqual(faulted[4]['physc'].slice_retries, 1)

    def test_truncated_stream_ending_on_partial_chunk_is_retried(self):
        clean = self.run_new(self.serial_filter)
        slices = clean[5]
        self.fake.faults = [Fault(self.slice_match('kernel', slices, 2), 'partial_end', after=2)]
        faulted = self.run_new(self.serial_filter)
        self.assertEqual(faulted[0], clean[0])
        self.assertEqual(faulted[4]['runq'].slice_retries, 1)

    def test_http_500_is_retried(self):
        clean = self.run_new(self.serial_filter)
        slices = clean[5]
        self.fake.faults = [Fault(self.slice_match('cpu_util', slices, 0), 'http500')]
        faulted = self.run_new(self.serial_filter)
        self.assertEqual(faulted[0], clean[0])
        self.assertEqual(faulted[4]['physc'].slice_retries, 1)

    def test_persistent_interruption_fails_the_run(self):
        slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, 6 * 3600 * NS)
        self.fake.faults = [Fault(self.slice_match('cpu_util', slices, 4), 'interrupt', after=1, times=None)]
        with self.assertRaises(RuntimeError) as ctx:
            self.run_new(self.serial_filter)
        self.assertIn(f'after {self.new.SLICE_ATTEMPTS} attempts', str(ctx.exception))

    def test_query_error_is_not_retried(self):
        slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, 6 * 3600 * NS)
        match = self.slice_match('kernel', slices, 1)
        fault = Fault(match, 'error', times=None)
        self.fake.faults = [fault]
        with self.assertRaises(self.new.InfluxQueryError):
            self.run_new(self.serial_filter)
        self.assertEqual(fault.hits, 1)

    def test_nanosecond_times_beyond_legacy_parser(self):
        base = day_ns('2026-10-01')
        fake = FakeInfluxDB([
            {'measurement': m, 'time_ns': base + 3600 * NS + 123_456_789, 'tags': {'host': 'vmN', 'serial_no': 'S9'},
             'fields': {f: 1.0}} for m, f in (('cpu_util', 'physical_consumed'), ('kernel', 'run_queue'))
        ] + [
            {'measurement': m, 'time_ns': base + 7200 * NS + 999_999_999, 'tags': {'host': 'vmN', 'serial_no': 'S9'},
             'fields': {f: 2.0}} for m, f in (('cpu_util', 'physical_consumed'), ('kernel', 'run_queue'))
        ])
        port = fake.start()
        try:
            client = InfluxDBClient(host='127.0.0.1', port=port, database='nmon', retries=1)
            slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, 24 * 3600 * NS)
            master, vms, mn, mx = self.new.stream_performance_data_optimized(
                client, 'nmon', "(\"serial_no\" = 'S9')", slices, self.tmp,
                types.SimpleNamespace(verbose=False, chunk_size=10))
            body = open(master).read().splitlines()
            self.assertIn('2026-10-01 02:00:00,vmN,2.0,2.0', body)    # .999999999 floored
            self.assertEqual(mx.strftime('%H:%M:%S'), '02:00:00')
            self.assertEqual(mn.strftime('%H:%M:%S'), '01:00:00')    # .123456789 floored
        finally:
            fake.stop()

    def test_first_sample_kept_and_no_header_row(self):
        """Corrected merge defect: temporary files no longer carry a header, so
        the earliest sample of each metric is kept and no header-like row is
        emitted after the data (legacy output was the 02:00 row followed by
        'timestamp,vm_name,value,value')."""
        base = day_ns('2026-10-01')
        fake = FakeInfluxDB([
            {'measurement': m, 'time_ns': base + h * 3600 * NS, 'tags': {'host': 'vmK', 'serial_no': 'S8'},
             'fields': {f: float(h)}}
            for h in (1, 2) for m, f in (('cpu_util', 'physical_consumed'), ('kernel', 'run_queue'))
        ])
        port = fake.start()
        try:
            client = InfluxDBClient(host='127.0.0.1', port=port, database='nmon', retries=1)
            slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, 24 * 3600 * NS)
            master = self.new.stream_performance_data_optimized(
                client, 'nmon', "(\"serial_no\" = 'S8')", slices, self.tmp,
                types.SimpleNamespace(verbose=False, chunk_size=10))[0]
            body = open(master).read().splitlines()
            self.assertEqual(body, ['Timestamp,VMName,PhysC,RunQ',
                                    '2026-10-01 01:00:00,vmK,1.0,1.0',
                                    '2026-10-01 02:00:00,vmK,2.0,2.0'])
        finally:
            fake.stop()


    def test_first_samples_of_different_keys(self):
        """The earliest PhysC and earliest RunQ samples belong to different keys;
        both survive, each in its own correctly paired row."""
        base = day_ns('2026-10-01')
        fake = FakeInfluxDB([
            {'measurement': 'cpu_util', 'time_ns': base + 60 * NS, 'tags': {'host': 'vmA', 'serial_no': 'S7'},
             'fields': {'physical_consumed': 0.5}},
            {'measurement': 'kernel', 'time_ns': base + 120 * NS, 'tags': {'host': 'vmA', 'serial_no': 'S7'},
             'fields': {'run_queue': 3.0}},
            {'measurement': 'cpu_util', 'time_ns': base + 120 * NS, 'tags': {'host': 'vmA', 'serial_no': 'S7'},
             'fields': {'physical_consumed': 0.75}},
        ])
        port = fake.start()
        try:
            client = InfluxDBClient(host='127.0.0.1', port=port, database='nmon', retries=1)
            slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, 24 * 3600 * NS)
            master = self.new.stream_performance_data_optimized(
                client, 'nmon', "(\"serial_no\" = 'S7')", slices, self.tmp,
                types.SimpleNamespace(verbose=False, chunk_size=10))[0]
            self.assertEqual(open(master).read().splitlines(),
                             ['Timestamp,VMName,PhysC,RunQ',
                              '2026-10-01 00:01:00,vmA,0.5,',
                              '2026-10-01 00:02:00,vmA,0.75,3.0'])
        finally:
            fake.stop()

    def test_merge_pairing_is_independent_of_locale(self):
        """Under a collating locale GNU sort orders 'web-01' < 'web01' < 'Web02',
        while the merge compares keys in byte order ('Web02' < 'web-01' <
        'web01'). With a RunQ sample missing, the legacy merge then split one
        key into a PhysC-only and a RunQ-only row. The sort now runs in byte
        order, so every key appears once and stays paired."""
        probe = subprocess.run(['sort'], input='web01\nWeb02\n', capture_output=True, text=True,
                               env=dict(os.environ, LC_ALL='en_US.UTF-8'))
        if probe.stdout.splitlines()[:1] != ['web01']:
            self.skipTest('no collating en_US.UTF-8 locale for sort on this host')
        base = day_ns('2026-10-01')
        pts = []
        for minute in range(3):
            t = base + minute * 60 * NS
            for host in ('web-01', 'web01', 'Web02'):
                pts.append({'measurement': 'cpu_util', 'time_ns': t, 'tags': {'host': host, 'serial_no': 'S6'},
                            'fields': {'physical_consumed': 1.0 + minute}})
                if not (host == 'web01' and minute == 1):
                    pts.append({'measurement': 'kernel', 'time_ns': t, 'tags': {'host': host, 'serial_no': 'S6'},
                                'fields': {'run_queue': 2.0 + minute}})
        fake = FakeInfluxDB(pts)
        port = fake.start()
        saved = os.environ.get('LC_ALL')
        os.environ['LC_ALL'] = 'en_US.UTF-8'
        try:
            client = InfluxDBClient(host='127.0.0.1', port=port, database='nmon', retries=1)
            slices = self.new.build_time_slices(self.start_ns, self.end_exclusive_ns, 24 * 3600 * NS)
            master = self.new.stream_performance_data_optimized(
                client, 'nmon', "(\"serial_no\" = 'S6')", slices, self.tmp,
                types.SimpleNamespace(verbose=False, chunk_size=10))[0]
            rows = open(master).read().splitlines()[1:]
        finally:
            if saved is None:
                os.environ.pop('LC_ALL', None)
            else:
                os.environ['LC_ALL'] = saved
            fake.stop()
        keys = [','.join(r.split(',')[:2]) for r in rows]
        self.assertEqual(len(keys), 9)
        self.assertEqual(len(set(keys)), 9, 'a key was split across rows')
        self.assertEqual(keys, sorted(keys))
        self.assertIn('2026-10-01 00:01:00,web01,2.0,', rows)
        self.assertIn('2026-10-01 00:01:00,Web02,2.0,3.0', rows)


if __name__ == '__main__':
    unittest.main(verbosity=1)
