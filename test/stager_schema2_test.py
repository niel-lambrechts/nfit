#!/usr/bin/env python3
# ==============================================================================
# Fixture tests for L1 schema 2 staging (--l1-schema 2, Increment 3c):
# PhysB and FreqPct acquired with PhysC in one query and kept on the line of
# their CPU sample; projection onto the four legacy columns equals schema 1
# from the same implementation; blank (never zero) optional cells; unchanged
# row retention; same-second collisions counted; manifest revisions, sources
# and counts; append layout guard. All data is synthetic (fake_influxdb).
# ==============================================================================
import calendar
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from influxdb import InfluxDBClient
    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False

from fake_influxdb import FakeInfluxDB  # noqa: E402

NS = 1_000_000_000
STAGER = os.environ.get('NFIT_STAGER', os.path.join(HERE, '..', 'nfit-stage-influxdb'))
DAY_NS = calendar.timegm((2026, 10, 1, 0, 0, 0)) * NS
OPTIONAL = [('PhysB', 'physical_busy'), ('FreqPct', 'freq_pct')]


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def build_dataset():
    """Two hours on serial S1 from 00:00 UTC.
      vmA  PhysC, PhysB, freq_pct every minute at :05; at minute 30 two CPU
           samples and two RunQ samples fall in one second (.2 and .7) with
           different values; at minute 40 PhysB is 0.0; at minute 50 the
           point has PhysB and freq_pct but no PhysC (a RunQ-only row).
      vmB  PhysC and freq_pct only (collector without physical_busy); RunQ
           missing at minutes 10-12.
    """
    pts = []

    def add(m, host, t, fields):
        pts.append({'measurement': m, 'time_ns': t, 'tags': {'host': host, 'serial_no': 'S1'}, 'fields': fields})

    for minute in range(120):
        t = DAY_NS + minute * 60 * NS + 5 * NS
        if minute == 30:
            add('cpu_util', 'vmA', t + 200_000_000,
                {'physical_consumed': 1.25, 'physical_busy': 0.5, 'freq_pct': 101.0})
            add('cpu_util', 'vmA', t + 700_000_000,
                {'physical_consumed': 1.75, 'physical_busy': 0.25, 'freq_pct': 102.0})
            add('kernel', 'vmA', t + 200_000_000, {'run_queue': 3.0})
            add('kernel', 'vmA', t + 700_000_000, {'run_queue': 4.0})
        elif minute == 50:
            add('cpu_util', 'vmA', t, {'physical_busy': 0.4, 'freq_pct': 100.5})
            add('kernel', 'vmA', t, {'run_queue': 1.0})
        else:
            add('cpu_util', 'vmA', t, {'physical_consumed': 1.0 + minute / 100,
                                       'physical_busy': 0.0 if minute == 40 else 0.5 + minute / 1000,
                                       'freq_pct': 105.9})
            add('kernel', 'vmA', t, {'run_queue': float(minute % 3)})
        add('cpu_util', 'vmB', t + 2 * NS, {'physical_consumed': 0.2, 'freq_pct': 125.48})
        if not 10 <= minute <= 12:
            add('kernel', 'vmB', t + 2 * NS, {'run_queue': 1.0})
    return pts


def config_events(client, db_version, fields, measurement, entity_filter, start_date, end_date, verbose=False):
    """Stand-in for Phase 2 (the fake does not implement LAST() buckets):
    one state per host at the window start."""
    values = {'entitlement': 1.0, 'smtthreads': 8, 'processorMHz': 3000, 'processorModel': 'm',
              'processorFamily': 'f', 'desired_vcpus': 2, 'pool_id': 0, 'phys_cpus_pool': 8,
              'serial_no': 'S1', 'capped': 0}
    anchor = DAY_NS // NS
    return {f: [{'ts': anchor, 'host': h, 'value': values[f]} for h in ('vmA', 'vmB')] for f in fields}


@unittest.skipUnless(HAVE_CLIENT, 'influxdb client package not installed')
class SchemaTwoTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.st = load(STAGER, 'stager_schema2')
        cls.st.SLICE_RETRY_DELAY_S = 0
        cls.fake = FakeInfluxDB(build_dataset())
        cls.port = cls.fake.start()
        cls.client = InfluxDBClient(host='127.0.0.1', port=cls.port, database='nmon', retries=1)
        cls.slices = cls.st.build_time_slices(DAY_NS, DAY_NS + 86400 * NS, 3600 * NS)
        cls.filter = "(\"serial_no\" = 'S1')"

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='schema2-test-')
        self.fake.field_types = {}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def master(self, cpu_optional=(), chunk_size=17):
        stats = {}
        args = types.SimpleNamespace(verbose=False, chunk_size=chunk_size)
        with redirect_stdout(io.StringIO()):
            path = self.st.stream_performance_data_optimized(
                self.client, 'nmon', self.filter, self.slices, self.tmp, args,
                stats_out=stats, cpu_optional=cpu_optional)[0]
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        os.remove(path)
        return lines, stats

    # -- projection and association ---------------------------------------------
    def test_projection_equals_schema_one(self):
        one, _ = self.master()
        two, _ = self.master(OPTIONAL, chunk_size=5)
        self.assertEqual(two[0], 'Timestamp,VMName,PhysC,RunQ,PhysB,FreqPct')
        self.assertTrue(all(len(r.split(',')) == 6 for r in two))
        self.assertEqual([','.join(r.split(',')[:4]) for r in two], one)

    def test_optional_values_stay_on_their_cpu_sample(self):
        two, stats = self.master(OPTIONAL)
        collided = [r for r in two if r.startswith('2026-10-01 00:30:05,vmA,')]
        triples = sorted(tuple(r.split(',')[i] for i in (2, 4, 5)) for r in collided)
        self.assertEqual(triples, [('1.25', '0.5', '101.0'), ('1.75', '0.25', '102.0')])
        self.assertEqual(sorted(r.split(',')[3] for r in collided), ['3.0', '4.0'])
        self.assertEqual(stats['merge']['ambiguous_keys'], 1)
        self.assertEqual(stats['merge']['ambiguous_extra_rows'], 1)
        self.assertEqual(stats['physc'].same_second, 1)

    def test_missing_optional_is_blank_never_zero_and_rows_are_kept(self):
        one, _ = self.master()
        two, stats = self.master(OPTIONAL)
        self.assertEqual(len(one), len(two))
        b_rows = [r.split(',') for r in two if ',vmB,' in r]
        self.assertTrue(all(c[4] == '' and c[5] == '125.48' for c in b_rows if c[2]))
        self.assertIn('2026-10-01 00:40:05,vmA,1.4,1.0,0.0,105.9', two)      # zero stays zero
        self.assertIn('2026-10-01 00:10:07,vmB,0.2,,,125.48', two)            # RunQ missing
        self.assertIn('2026-10-01 00:50:05,vmA,,1.0,,', two)                  # no PhysC: RunQ row kept,
        self.assertEqual(stats['physc'].optional_without_primary, 1)          # optional values counted

    def test_unavailable_field_is_not_selected_and_cells_are_blank(self):
        before = len(self.fake.queries)
        two, _ = self.master([('PhysB', None), ('FreqPct', 'freq_pct')])
        cpu_queries = [q for q in self.fake.queries[before:] if '"cpu_util"' in q]
        self.assertTrue(cpu_queries)
        self.assertTrue(all('physical_busy' not in q and '"freq_pct"' in q for q in cpu_queries))
        self.assertTrue(all(r.split(',')[4] == '' for r in two[1:]))

    def test_resolve_cpu_optional_columns(self):
        sel, avail = self.st.resolve_cpu_optional_columns(self.client)
        self.assertEqual(sel, OPTIONAL)
        self.assertEqual(avail, {'PhysB': 'present in schema', 'FreqPct': 'present in schema'})
        self.fake.field_types['cpu_util'] = {'physical_consumed': ['float'], 'physical_busy': ['float', 'string']}
        sel, avail = self.st.resolve_cpu_optional_columns(self.client)
        self.assertEqual(sel, [('PhysB', None), ('FreqPct', None)])
        self.assertEqual(avail, {'PhysB': 'not selected: type float/string', 'FreqPct': 'absent from schema'})

    # -- manifest ------------------------------------------------------------------
    def test_manifest_lines(self):
        two, stats = self.master(OPTIONAL)
        data = os.path.join(self.tmp, 'data')
        states = os.path.join(self.tmp, 'states')
        with open(data, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(two) + '\n')
        with open(states, 'w') as fh:
            fh.write('{"vmA": []}')
        args = types.SimpleNamespace(slice_hours=1, chunk_size=17)
        lines = self.st.l1_schema2_manifest_lines(data, states, {'PhysB': 'present in schema',
                                                                 'FreqPct': 'present in schema'},
                                                  stats, (DAY_NS, DAY_NS + 86400 * NS), args)
        m = dict(line.split(': ', 1) for line in lines)
        self.assertEqual(m['data_schema_version'], '2')
        self.assertEqual(m['data_columns'], two[0])
        with open(data, 'rb') as fh:
            self.assertEqual(m['data_revision'], 'sha256:' + hashlib.sha256(fh.read()).hexdigest())
        with open(states, 'rb') as fh:
            self.assertEqual(m['states_revision'], 'sha256:' + hashlib.sha256(fh.read()).hexdigest())
        self.assertEqual(m['quality_rows'], str(len(two) - 1))
        nonempty = dict(kv.split('=') for kv in m['quality_nonempty'].split('; '))
        self.assertEqual(int(nonempty['PhysB']), sum(1 for r in two[1:] if r.split(',')[4]))
        self.assertEqual(int(nonempty['PhysC']), sum(1 for r in two[1:] if r.split(',')[2]))
        self.assertIn('PhysB=cpu_util.physical_busy', m['column_sources'])
        self.assertIn('RunQ=kernel.run_queue', m['column_sources'])
        self.assertIn('optional_values_without_physc=1', m['last_run_physc_counts'])
        self.assertTrue(m['last_run_ambiguous_second_keys'].startswith('1 (extra rows 1'))
        # nfit's manifest parser: key up to the first ':', value trimmed.
        for line in lines:
            self.assertRegex(line, r'^[^:]+?\s*:\s*\S')

    # -- append layout guard -----------------------------------------------------------
    def make_cache(self, serial, header, manifest_extra=''):
        d = os.path.join(self.tmp, 'stage', serial)
        os.makedirs(d)
        with open(os.path.join(d, '.nfit.cache.data'), 'w') as fh:
            fh.write(header + '\n2026-10-01 00:00:05,vmA,1.0,1.0' + (',,' if header.count(',') == 5 else '') + '\n')
        with open(os.path.join(d, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('cache_build_status: success\ndata_start: 2026-10-01\ndata_end: 2026-10-01 00:00:05\n'
                     + manifest_extra)

    def test_append_layout_conflicts(self):
        s1 = 'Timestamp,VMName,PhysC,RunQ'
        s2 = 'Timestamp,VMName,PhysC,RunQ,PhysB,FreqPct'
        self.make_cache('ONE', s1)
        self.make_cache('LEGACYNAME', 'Timestamp,VM_Name,PhysC,RunQ')
        self.make_cache('TWO', s2, f'data_schema_version: 2\ndata_columns: {s2}\n')
        self.make_cache('BADDECL', s2, 'data_schema_version: 2\ndata_columns: Timestamp,VMName,PhysC,RunQ,PhysB\n')
        stage = os.path.join(self.tmp, 'stage')
        conflicts = self.st.append_layout_conflicts
        self.assertEqual(conflicts(stage, ['ONE', 'LEGACYNAME'], 1), [])
        self.assertEqual(conflicts(stage, ['TWO'], 2), [])
        self.assertEqual([p.split(':')[0] for p in conflicts(stage, ['ONE', 'LEGACYNAME', 'BADDECL'], 2)],
                         ['BADDECL', 'LEGACYNAME', 'ONE'])
        self.assertEqual([p.split(':')[0] for p in conflicts(stage, ['TWO', 'BADDECL'], 1)], ['BADDECL', 'TWO'])
        self.assertEqual(conflicts(stage, ['ABSENT'], 2), [])


@unittest.skipUnless(HAVE_CLIENT, 'influxdb client package not installed')
class StagingRunTests(unittest.TestCase):
    """main() end to end against the fake server, with Phase 2 replaced by a
    fixed state per host: the local analogue of the live one-VM comparison."""

    @classmethod
    def setUpClass(cls):
        # main() keeps its temporary files under <script root>/tmp: run a copy
        # from a scratch root so the repository's tmp/ is never touched.
        cls.root = tempfile.mkdtemp(prefix='schema2-root-')
        shutil.copy(STAGER, os.path.join(cls.root, 'nfit-stage-influxdb'))
        cls.st = load(os.path.join(cls.root, 'nfit-stage-influxdb'), 'stager_schema2_main')
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
        self.tmp = tempfile.mkdtemp(prefix='schema2-run-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *argv):
        saved = sys.argv
        sys.argv = ['nfit-stage-influxdb'] + list(argv)
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

    def read(self, datadir, name):
        with open(os.path.join(datadir, 'S1', name), encoding='utf-8') as fh:
            return fh.read()

    def test_schema_two_projects_to_schema_one_and_states_match(self):
        d1, d2 = os.path.join(self.tmp, 's1'), os.path.join(self.tmp, 's2')
        rc1, out1, err1 = self.run_main('--vms', 'vmA,vmB', '--startd', '2026-10-01', '--endd', '2026-10-01',
                                        '--datadir', d1)
        rc2, out2, err2 = self.run_main('--vms', 'vmA,vmB', '--startd', '2026-10-01', '--endd', '2026-10-01',
                                        '--datadir', d2, '--l1-schema', '2', '--slice-hours', '1')
        self.assertEqual((rc1, rc2), (0, 0), err1 + err2)
        one = self.read(d1, '.nfit.cache.data').splitlines()
        two = self.read(d2, '.nfit.cache.data').splitlines()
        self.assertEqual(two[0], 'Timestamp,VMName,PhysC,RunQ,PhysB,FreqPct')
        self.assertEqual([','.join(r.split(',')[:4]) for r in two], one)
        self.assertEqual(self.read(d1, '.nfit.cache.states'), self.read(d2, '.nfit.cache.states'))
        m1 = self.st.read_cache_manifest(os.path.join(d1, 'S1', '.nfit.cache.manifest'))
        m2 = self.st.read_cache_manifest(os.path.join(d2, 'S1', '.nfit.cache.manifest'))
        self.assertNotIn('data_schema_version', m1)                       # still schema 1
        # 3e: schema-1 manifests carry content revisions too, so nfit's results
        # cache never matches different data by size and mtime alone.
        self.assertEqual(sorted(m1), ['build_timestamp', 'cache_build_status', 'data_end', 'data_revision',
                                      'data_start', 'staged_by', 'states_revision'])
        with open(os.path.join(d1, 'S1', '.nfit.cache.data'), 'rb') as fh:
            self.assertEqual(m1['data_revision'], 'sha256:' + hashlib.sha256(fh.read()).hexdigest())
        for key in ('cache_build_status', 'data_start', 'data_end'):
            self.assertEqual(m1[key], m2[key])
        self.assertEqual(m2['data_columns'], two[0])
        with open(os.path.join(d2, 'S1', '.nfit.cache.data'), 'rb') as fh:
            self.assertEqual(m2['data_revision'], 'sha256:' + hashlib.sha256(fh.read()).hexdigest())
        self.assertIn('L1 schema 2 optional columns: PhysB (present in schema), FreqPct (present in schema)', out2)
        self.assert_nfit_reader_accepts(os.path.join(d2, 'S1'), m2['data_revision'])

    def assert_nfit_reader_accepts(self, cache_dir, revision):
        """Runs nfit's own manifest parser, L1 layout resolver and data identity
        (extracted from the live source, as test/l1_schema.t does) on the cache
        the stager wrote."""
        if not shutil.which('perl'):
            self.skipTest('perl not available')
        engine = os.environ.get('NFIT_ENGINE', os.path.join(HERE, '..', 'nfit'))
        script = r'''
use strict; use warnings; use File::Spec;
my ($src_path, $dir) = @ARGV;
open my $fh, '<', $src_path or die; my $src = do { local $/; <$fh> }; close $fh;
my $code = '';
for my $n (qw(_read_cache_manifest_meta _resolve_l1_layout _l1_data_identity)) {
    $src =~ /^(sub \Q$n\E\s*\{.*?^\})/ms or die "no $n"; $code .= "$1\n";
}
my @g;
for my $n (qw(L1_SUPPORTED_SCHEMA_VERSIONS L1_REQUIRED_COLUMNS L1_COLUMN_ALIASES)) {
    $src =~ /^(my\s+[\@%]\Q$n\E\s*=.*?;)\s*$/m or die "no $n"; (my $x = $1) =~ s/^my /our /; push @g, $x;
}
eval "package UUT; use File::Spec; our \$VERSION='t'; our \$CACHE_MANIFEST_FILE='.nfit.cache.manifest';"
   . "our \$CACHE_DATA_FILE='.nfit.cache.data'; our \$CACHE_STATES_FILE='.nfit.cache.states';"
   . join("\n", @g) . "\n$code\n1;" or die $@;
my $meta = UUT::_read_cache_manifest_meta($dir);
open my $d, '<', "$dir/.nfit.cache.data" or die; my $h = <$d>; close $d;
my $L = UUT::_resolve_l1_layout($h, $meta, "$dir/.nfit.cache.data");
my $id = UUT::_l1_data_identity($dir, $meta);
print "schema=$L->{schema} legacy=$L->{legacy} PhysB=$L->{index}{PhysB} FreqPct=$L->{index}{FreqPct} data=$id->{data} states=$id->{states}\n";
'''
        import subprocess
        r = subprocess.run(['perl', '-e', script, engine, cache_dir], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'schema=2 legacy=0 PhysB=4 FreqPct=5 data=rev:{revision} states=rev:sha256:', r.stdout)

    def test_append_refuses_mixed_layout_before_any_query(self):
        d1 = os.path.join(self.tmp, 's1')
        rc, _, err = self.run_main('--vms', 'vmA,vmB', '--startd', '2026-10-01', '--endd', '2026-10-01',
                                   '--datadir', d1)
        self.assertEqual(rc, 0, err)
        before = len(self.fake.queries)
        data_before = self.read(d1, '.nfit.cache.data')
        rc, _, err = self.run_main('--vms', 'vmA,vmB', '--append', '--endd', '2026-10-02',
                                   '--datadir', d1, '--l1-schema', '2')
        self.assertEqual(rc, 1)
        self.assertIn('--append would mix L1 layouts', err)
        self.assertEqual(len(self.fake.queries), before)
        self.assertEqual(self.read(d1, '.nfit.cache.data'), data_before)

    def test_schema_two_append_keeps_layout_and_updates_revision(self):
        d2 = os.path.join(self.tmp, 's2')
        rc, _, err = self.run_main('--vms', 'vmA,vmB', '--startd', '2026-10-01', '--endd', '2026-10-01',
                                   '--datadir', d2, '--l1-schema', '2')
        self.assertEqual(rc, 0, err)
        rc, _, err = self.run_main('--vms', 'vmA,vmB', '--append', '--endd', '2026-10-01',
                                   '--datadir', d2, '--l1-schema', '2')
        self.assertIn(rc, (0,), err)
        rows = self.read(d2, '.nfit.cache.data').splitlines()
        self.assertEqual(rows[0], 'Timestamp,VMName,PhysC,RunQ,PhysB,FreqPct')
        self.assertTrue(all(len(r.split(',')) == 6 for r in rows))
        m2 = self.st.read_cache_manifest(os.path.join(d2, 'S1', '.nfit.cache.manifest'))
        with open(os.path.join(d2, 'S1', '.nfit.cache.data'), 'rb') as fh:
            self.assertEqual(m2['data_revision'], 'sha256:' + hashlib.sha256(fh.read()).hexdigest())
        self.assertEqual(m2['quality_rows'], str(len(rows) - 1))


if __name__ == '__main__':
    unittest.main(verbosity=1)
