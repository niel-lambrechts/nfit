#!/usr/bin/env python3
# ==============================================================================
# Fixture tests for nfit-stage-influxdb discovery and bounded probe (3b):
# schema discovery, approved-field resolution, collector field variants, type
# variants, extra series, duplicates, pool reporters and moves, capability
# values, budgets, retries, errors, pseudonymisation and re-analysis.
# All data is synthetic; the database is test/fake_influxdb.py.
# ==============================================================================
import calendar
import gzip
import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
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
STAGER = os.environ.get('NFIT_STAGER', os.path.join(HERE, '..', 'nfit-stage-influxdb'))
LEGACY_STAGER = os.path.join(HERE, 'fixtures', 'stager-legacy', 'nfit-stage-influxdb.b3131f2')
SECRET = 'f' * 64
SECRET_COMMAND = 'njmon -i dbhost -u probeuser -p NOT-A-REAL-PASSWORD'
BASE_NS = calendar.timegm((2026, 10, 1, 10, 0, 0)) * NS     # 2026-10-01T10:00:00Z
WINDOW = '2026-10-01T10:00:00Z/2026-10-01T10:30:00Z'
REAL_NAMES = ('lparalpha1', 'lparalpha2', 'lparalpha3', 'lparbeta1', 'SERALPHA', 'SERBETA', 'nmondb')


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def pseudonym(prefix, term):
    return prefix + '_' + hmac.new(SECRET.encode(), term.lower().encode(), hashlib.sha256).hexdigest()[:6]


def build_probe_dataset():
    """Thirty minutes on SERALPHA (three hosts) plus one host on SERBETA that
    the probe must exclude. Deliberate features:
      lparalpha1  complete CPU fields; pool 0 at :07
      lparalpha2  no physical_busy (collector variant); pool 0 at :31, moves to
                  pool 3 after 15 minutes; RunQ gaps
      lparalpha3  a second tag set (collector=beta) with conflicting values at
                  the same nanosecond; a sub-second point; pool 3
    """
    pts = []

    def add(measurement, host, serial, t, fields, **extra_tags):
        tags = {'host': host, 'serial_no': serial}
        tags.update(extra_tags)
        pts.append({'measurement': measurement, 'time_ns': t, 'tags': tags, 'fields': fields})

    for minute in range(30):
        t = BASE_NS + minute * 60 * NS
        # lparalpha1: PhysC 0.5 below entitlement 1.0 for 20 minutes, then 1.5 above.
        c = 0.5 if minute < 20 else 1.5
        add('cpu_util', 'lparalpha1', 'SERALPHA', t,
            {'physical_consumed': c, 'physical_busy': c / 2, 'freq_pct': 100.0, 'kern_pct': 5.0,
             'idle_pct': 40.0, 'wait_pct': 10.0, 'entitlement': 1.0})
        add('cpu_util', 'lparalpha2', 'SERALPHA', t + 7 * NS,
            {'physical_consumed': 0.2, 'freq_pct': 100.0, 'kern_pct': 2.0,
             'idle_pct': 80.0, 'wait_pct': 0.0, 'entitlement': 0.5})
        add('cpu_util', 'lparalpha3', 'SERALPHA', t + 31 * NS,
            {'physical_consumed': 2.0, 'physical_busy': 1.0, 'freq_pct': 98.5, 'kern_pct': 9.0,
             'idle_pct': 20.0, 'wait_pct': 5.0, 'entitlement': 2.0})
        add('cpu_util', 'lparbeta1', 'SERBETA', t,
            {'physical_consumed': 9.0, 'physical_busy': 4.0, 'entitlement': 9.0})
        for host, off in (('lparalpha1', 0), ('lparalpha2', 7), ('lparalpha3', 31)):
            if host == 'lparalpha2' and minute in (5, 6, 7):
                continue
            add('kernel', host, 'SERALPHA', t + off * NS, {'run_queue': minute % 4})
        pool_a2 = 0 if minute < 15 else 3
        for host, off, pool in (('lparalpha1', 7, 0), ('lparalpha2', 31, pool_a2), ('lparalpha3', 45, 3)):
            add('processor_pool', host, 'SERALPHA', t + off * NS,
                {'pool_id': pool, 'phys_cpus_pool': 16, 'idle_cores': 6.0 + (0.25 if host == 'lparalpha2' else 0),
                 'busy_cores': 9.5, 'scaled_busy_cores': 9.4, 'max_cores': 16.0,
                 'global_pool_busy_cores': 20.0, 'global_pool_tot_cores': 32.0,
                 'max_capacity_cores': 4.5 if pool == 0 else 8.0,
                 'max_capacity_raw': 450 if pool == 0 else 800,
                 'entitled_capacity_cores': 2.25, 'entitled_capacity_raw': 225,
                 'max_capacity': 4 if pool == 0 else 8})
        for host in ('lparalpha1', 'lparalpha2', 'lparalpha3'):
            add('partition_type', host, 'SERALPHA', t + NS,
                {'shared_enabled': 1, 'capped': 0,
                 'pool_util_authority': 0 if host == 'lparalpha3' else 1})
            add('identity', host, 'SERALPHA', t + 2 * NS,
                {'njmon_version': 'v80' if host == 'lparalpha1' else 'v90', 'njmon_command': SECRET_COMMAND})
    # A second series for lparalpha3, conflicting at the same nanosecond.
    for minute in range(10):
        t = BASE_NS + minute * 60 * NS + 31 * NS
        add('cpu_util', 'lparalpha3', 'SERALPHA', t,
            {'physical_consumed': 2.1, 'physical_busy': 1.1}, collector='beta')
    # Sub-second point: same UTC second as an existing lparalpha1 point.
    add('cpu_util', 'lparalpha1', 'SERALPHA', BASE_NS + 3 * 60 * NS + 400_000_000,
        {'physical_consumed': 0.5, 'physical_busy': 0.25})
    # Outside the window: must not be read.
    add('cpu_util', 'lparalpha1', 'SERALPHA', BASE_NS + 30 * 60 * NS, {'physical_consumed': 7.0})
    add('cpu_physical_total', 'lparalpha1', 'SERALPHA', BASE_NS, {'user': 1.0})
    return pts


@unittest.skipUnless(HAVE_CLIENT, 'influxdb client package not installed')
class ProbeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.st = load(STAGER, 'stager_probe')
        cls.st.SLICE_RETRY_DELAY_S = 0
        cls.fake = FakeInfluxDB(build_probe_dataset())
        cls.fake.field_types['cpu_util'] = {
            'entitlement': ['float'], 'freq_pct': ['float'], 'idle_pct': ['float'], 'kern_pct': ['float'],
            'physical_busy': ['float', 'integer'], 'physical_consumed': ['float'], 'wait_pct': ['float']}
        cls.port = cls.fake.start()
        cls.client = InfluxDBClient(host='127.0.0.1', port=cls.port, database='nmondb', retries=1)
        cls.st.get_db_client = lambda path: (cls.client, 1, {'DATABASE': 'nmondb'})
        cls.root = tempfile.mkdtemp(prefix='probe-root-')
        cls.secret_file = os.path.join(cls.root, 'secret')
        with open(cls.secret_file, 'w') as fh:
            fh.write(SECRET + '\n')
        cls.baseline_dir = os.path.join(cls.root, 'baseline')
        rc = cls.st.run_probe_mode(cls.args(probe=True, probe_out=cls.baseline_dir, window=[WINDOW],
                                            mgsys='SERALPHA'), cls.root, os.path.join(cls.root, 'stage'))
        assert rc == 0, rc
        with open(os.path.join(cls.baseline_dir, 'report', 'probe-report.json')) as fh:
            cls.rep = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def args(cls, **kw):
        base = dict(discover=False, probe=False, reanalyse=None, probe_out=None, window=None,
                    mgsys=None, vms=None, vmfile=None, max_rows=200_000, max_bytes=64 << 20,
                    max_seconds=900, match_tolerance=30, chunk_size=7,
                    pseudonym_secret=cls.secret_file)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def setUp(self):
        self.fake.faults = []
        self.tmp = tempfile.mkdtemp(prefix='probe-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_probe(self, **kw):
        out = os.path.join(self.tmp, 'out')
        kw.setdefault('probe', True)
        kw.setdefault('window', [WINDOW])
        kw.setdefault('mgsys', 'SERALPHA')
        rc = self.st.run_probe_mode(self.args(probe_out=out, **kw), self.root, os.path.join(self.root, 'stage'))
        rep = None
        path = os.path.join(out, 'report', 'probe-report.json')
        if os.path.exists(path):
            with open(path) as fh:
                rep = json.load(fh)
        return rc, rep, out

    # -- helpers under test ----------------------------------------------------
    def test_window_parsing_and_filters(self):
        s, e = self.st.parse_probe_window(WINDOW)
        self.assertEqual((s, e), (BASE_NS, BASE_NS + 1800 * NS))
        for bad in ('2026-10-01T10:00:00Z', '2026-10-01 10:00/2026-10-01 11:00',
                    '2026-10-01T11:00:00Z/2026-10-01T10:00:00Z'):
            with self.assertRaises(ValueError):
                self.st.parse_probe_window(bad)
        self.assertEqual(self.st.build_entity_filter(['S1'], ["o'brien"]),
                         "(\"serial_no\" = 'S1') AND (host = 'o\\'brien')")
        self.assertEqual(self.st.influxql_ident('a"b'), '"a\\"b"')

    # -- discovery -------------------------------------------------------------
    def test_discover_reads_metadata_only(self):
        before = len(self.fake.queries)
        rc, rep, _ = self.run_probe(probe=False, discover=True, window=None)
        self.assertEqual(rc, 0)
        self.assertFalse([q for q in self.fake.queries[before:] if q.startswith('SELECT')])
        self.assertEqual(rep['environment']['server_version'], '1.8.10')
        self.assertEqual(rep['retention_policies'][0]['name'], 'autogen')
        self.assertEqual(rep['queries'], [])

    def test_schema_plan_and_variants(self):
        plan, chk = self.rep['plan'], self.rep['schema_checks']
        self.assertEqual(plan['cpu_util']['missing'], ['user_pct'])
        self.assertEqual(plan['cpu_util']['multi_type'], ['physical_busy'])
        self.assertIn('physical_busy', plan['cpu_util']['selected'])
        self.assertEqual(plan['identity']['selected'], ['njmon_version'])
        self.assertEqual(plan['identity']['forbidden_present'], ['njmon_command'])
        self.assertTrue(chk['processor_pool']['pool_id_is_field'])
        self.assertFalse(chk['processor_pool']['pool_id_is_tag'])
        self.assertEqual(sorted(chk['processor_pool']['capacity_variants']),
                         ['entitled_capacity_cores', 'entitled_capacity_raw', 'max_capacity',
                          'max_capacity_cores', 'max_capacity_raw'])
        self.assertTrue(chk['cpu_util']['host_is_tag'] and chk['cpu_util']['serial_no_is_tag'])
        self.assertEqual(self.rep['schema']['cpu_physical_total']['fields'], {'user': ['float']})
        self.assertFalse(chk['partition_type'].get('pool_id_is_field', False))

    def test_identity_field_names_are_withheld_from_report(self):
        ident = self.rep['schema']['identity']
        self.assertEqual(sorted(ident['fields']), ['njmon_command', 'njmon_version'])
        self.assertEqual(ident['other_fields_withheld'], 0)
        self.assertEqual(self.rep['plan']['identity']['unapproved'], ['njmon_command'])
        self.assertIn('user', self.rep['schema']['cpu_physical_total']['fields'])   # others unchanged
        schema = {'identity': {'fields': {'njmon_version': ['string'], 'hostname': ['string'],
                                          'veth0abc_IP6': ['string']}, 'tags': ['host']}}
        shared = self.st._shareable_schema(schema)['identity']
        self.assertEqual(shared['fields'], {'njmon_version': ['string']})
        self.assertEqual(shared['other_fields_withheld'], 2)

    def test_forbidden_field_is_never_selected_or_stored(self):
        self.assertFalse([q for q in self.fake.queries if 'njmon_command' in q and q.startswith('SELECT')])
        for name in os.listdir(os.path.join(self.baseline_dir, 'private', 'raw')):
            with gzip.open(os.path.join(self.baseline_dir, 'private', 'raw', name), 'rt') as fh:
                self.assertNotIn('NOT-A-REAL-PASSWORD', fh.read())

    def test_probe_queries_are_bounded_and_grouped_by_all_tags(self):
        selects = [q for q in self.fake.queries if q.startswith('SELECT')]
        s, e = self.st.parse_probe_window(WINDOW)
        self.assertTrue(selects)
        for q in selects:
            self.assertIn(f'time >= {s} AND time < {e} GROUP BY *', q)
            self.assertIn("\"serial_no\" = 'SERALPHA'", q)
        cpu = self.rep['measurements']['cpu_util']
        self.assertEqual(cpu['hosts'], 3)                       # SERBETA excluded
        self.assertEqual(cpu['systems'], [pseudonym('sys', 'SERALPHA')])

    # -- series identity, cadence, duplicates ------------------------------------
    def test_extra_series_and_duplicates(self):
        cpu = self.rep['measurements']['cpu_util']
        self.assertEqual(cpu['hosts_with_multiple_series'], {pseudonym('vm', 'lparalpha3'): 2})
        self.assertEqual(cpu['extra_tag_keys'], {'collector': 1})
        d = cpu['duplicates']
        self.assertEqual(d['same_ns_across_series_of_host'], 10)
        self.assertEqual(d['legacy_same_second_collisions'], 1)
        self.assertEqual(d['conflicting_same_series'], 0)
        c = cpu['cadence']
        self.assertEqual(c['series_median_interval_s']['p50'], 60.0)
        self.assertEqual(c['gaps_over_1_5x_nominal'], 0)
        k = self.rep['measurements']['kernel']['cadence']
        self.assertEqual(k['gaps_over_1_5x_nominal'], 1)       # lparalpha2 minutes 5-7 missing

    def test_field_coverage_reports_collector_variant(self):
        cov = self.rep['measurements']['cpu_util']['field_coverage']
        self.assertEqual(cov['physical_busy']['hosts_without_any_value'], [pseudonym('vm', 'lparalpha2')])
        self.assertEqual(cov['physical_consumed']['types_in_data'], ['float'])
        p = self.rep['measurements']['cpu_util']['pairing']
        self.assertEqual(p['physb_missing_where_physc_present'], 30)
        self.assertEqual(p['physb_above_physc'], 0)
        self.assertEqual(p['cpu_runq_pairing']['matched_keys'], 87)   # 90 - 3 RunQ gaps

    def test_physb_ratio_and_residuals_by_regime(self):
        p = self.rep['measurements']['cpu_util']['pairing']
        below = p['physb_over_physc']['at_or_below_entitlement']
        above = p['physb_over_physc']['above_entitlement']
        self.assertEqual((below['min'], below['max']), (0.5, 0.5))
        self.assertEqual(above['n'], 10)                         # lparalpha1 minutes 20-29
        r = p['residual_physb_minus_physc_x_busy_pct']['above_entitlement']
        self.assertAlmostEqual(r['p50'], 0.75 - 1.5 * 0.5, places=4)

    # -- pools -------------------------------------------------------------------
    def test_pool_reporters_moves_and_capacity_variants(self):
        pool = self.rep['measurements']['processor_pool']['pool']
        self.assertEqual(pool['hosts_reporting_several_pools'], [pseudonym('vm', 'lparalpha2')])
        sysa = pseudonym('sys', 'SERALPHA')
        self.assertEqual(sorted(pool['pools']), [f'{sysa}/0', f'{sysa}/3'])
        p0, p3 = pool['pools'][f'{sysa}/0'], pool['pools'][f'{sysa}/3']
        self.assertEqual((p0['system'], p0['pool_id']), (sysa, 0))
        self.assertEqual(p0['reporters'], 2)
        self.assertEqual(p3['reporters'], 2)
        self.assertEqual(p0['abs_raw_div_100_minus_cores']['max'], 0.0)
        self.assertEqual(p0['distinct_config_values']['max_capacity']['values'], [4])
        self.assertEqual(p0['distinct_config_values']['max_capacity_cores']['values'], [4.5])
        pair = p0['reporter_pairs'][0]
        self.assertEqual(pair['nearest_offset_s']['n'], 15)      # only while both report pool 0
        self.assertEqual(pair['nearest_offset_s']['max'], 24.0)  # :07 against :31
        self.assertEqual(pair['abs_diff_idle_cores_within_tolerance']['max'], 0.25)
        self.assertEqual(p0['idle_plus_busy_minus_phys_cpus_pool']['min'], -0.5)

    def test_same_pool_id_on_two_systems_is_two_pools(self):
        """Pool IDs are local to a managed system: pool 0 on two systems must be
        summarised separately and their reporters never compared."""
        def rec(host, serial, t, idle, n):
            return {'window': 0, 'time': BASE_NS + t * NS, 'tags': {'host': host, 'serial_no': serial},
                    'fields': {'pool_id': 0, 'phys_cpus_pool': n, 'idle_cores': idle, 'busy_cores': 1.0}}
        recs = []
        for minute in range(5):
            recs += [rec('hosta1', 'SYSA', minute * 60, 30.0, 40), rec('hosta2', 'SYSA', minute * 60 + 5, 30.1, 40),
                     rec('hostb1', 'SYSB', minute * 60 + 2, 100.0, 120)]
        ps = self.st.Pseudonymiser(SECRET.encode())
        out = self.st._pool_analysis(recs, ps, {'match_tolerance_s': 30})
        a, b = f"{pseudonym('sys', 'SYSA')}/0", f"{pseudonym('sys', 'SYSB')}/0"
        self.assertEqual(sorted(out['pools']), sorted([a, b]))
        self.assertEqual((out['pools'][a]['reporters'], out['pools'][b]['reporters']), (2, 1))
        self.assertEqual(out['pools'][a]['distinct_config_values']['phys_cpus_pool']['values'], [40])
        self.assertEqual(out['pools'][b]['distinct_config_values']['phys_cpus_pool']['values'], [120])
        self.assertEqual(out['pools'][b]['reporter_pairs'], [])
        pair = out['pools'][a]['reporter_pairs'][0]
        self.assertEqual(pair['reporters'], sorted([pseudonym('vm', 'hosta1'), pseudonym('vm', 'hosta2')]))
        self.assertAlmostEqual(pair['abs_diff_idle_cores_within_tolerance']['max'], 0.1, places=6)
        md = self.st.render_probe_markdown({**self.rep, 'measurements': {'processor_pool': {
            **self.rep['measurements']['processor_pool'], 'pool': out}}})
        self.assertIn(f"System {pseudonym('sys', 'SYSB')}, pool 0: rows 5, reporters 1", md)

    def test_capability_and_collector_versions(self):
        pt = self.rep['measurements']['partition_type']['capability']
        self.assertEqual(pt['pool_util_authority']['hosts_by_last_value'], {'0': 1, '1': 2})
        ident = self.rep['measurements']['identity']['capability']
        self.assertEqual(ident['njmon_version']['hosts_by_last_value'], {'v80': 1, 'v90': 2})

    def test_cost_is_measured(self):
        cost = self.rep['cost']
        self.assertGreater(cost['cpu_util']['wire_bytes'], 0)
        self.assertGreater(cost['cpu_util']['raw_gzip_bytes'], 0)
        self.assertEqual(cost['kernel']['rows'], 87)
        self.assertEqual(cost['kernel']['extrapolated_90d_rows_probe_scope'], round(87 / 3 / 0.5 * 24 * 90 * 3))
        self.assertGreater(cost['l1_row_bytes']['legacy_four_columns_mean'], 30)

    # -- privacy ---------------------------------------------------------------
    def test_report_is_pseudonymised(self):
        for name in ('probe-report.json', 'probe-report.md'):
            with open(os.path.join(self.baseline_dir, 'report', name)) as fh:
                text = fh.read().lower()
            for real in REAL_NAMES:
                self.assertNotIn(real.lower(), text, f'{real} leaked into {name}')
        self.assertEqual(self.rep['target']['systems'], [pseudonym('sys', 'SERALPHA')])
        self.assertEqual(self.rep['pseudonyms'], 'given file')

    def test_leak_check_fails_closed(self):
        out = os.path.join(self.tmp, 'leak')
        shutil.copytree(self.baseline_dir, out)
        shutil.rmtree(os.path.join(out, 'report'))
        original = self.st.render_probe_markdown
        self.st.render_probe_markdown = lambda rep: 'host lparalpha2 appears here\n'
        try:
            with self.assertRaises(SystemExit):
                self.st.write_probe_report(out, self.root, self.secret_file)
        finally:
            self.st.render_probe_markdown = original
        self.assertFalse(os.path.exists(os.path.join(out, 'report')))

    def test_query_error_is_recorded_and_redacted(self):
        self.fake.faults = [Fault(('SELECT', '"kernel"'), 'error', times=None,
                                  message="field type conflict near host = 'lparalpha2'")]
        rc, rep, _ = self.run_probe()
        self.assertEqual(rc, 2)
        q = [x for x in rep['queries'] if x['measurement'] == 'kernel'][0]
        self.assertEqual(q['status'], 'failed')
        self.assertIn('<redacted>', q['error'])
        self.assertNotIn('kernel', rep['measurements'])

    # -- robustness ---------------------------------------------------------------
    def test_interrupted_query_is_retried(self):
        self.fake.faults = [Fault(('SELECT', '"cpu_util"'), 'interrupt', after=2, times=1)]
        rc, rep, _ = self.run_probe()
        self.assertEqual(rc, 0)
        q = [x for x in rep['queries'] if x['measurement'] == 'cpu_util'][0]
        self.assertEqual(q['attempts'], 2)
        self.assertEqual(rep['measurements']['cpu_util'], self.rep['measurements']['cpu_util'])

    def test_budget_stops_cleanly(self):
        rc, rep, _ = self.run_probe(max_rows=50)
        self.assertEqual(rc, 2)
        self.assertEqual(rep['budget_exceeded'], 'rows > 50')
        statuses = [q['status'] for q in rep['queries']]
        self.assertEqual(statuses[0], 'complete; budget reached')
        self.assertTrue(all(s == 'skipped: budget exceeded' for s in statuses[1:]))

    def test_reanalyse_needs_no_database(self):
        out = os.path.join(self.tmp, 'again')
        shutil.copytree(self.baseline_dir, out)
        saved = self.st.get_db_client
        self.st.get_db_client = lambda path: (_ for _ in ()).throw(AssertionError('database used'))
        try:
            rc = self.st.run_probe_mode(self.args(reanalyse=out), self.root, os.path.join(self.root, 'stage'))
        finally:
            self.st.get_db_client = saved
        self.assertEqual(rc, 0)
        with open(os.path.join(out, 'report', 'probe-report.json')) as fh:
            self.assertEqual(json.load(fh), self.rep)

    def test_output_directory_rules(self):
        busy = os.path.join(self.tmp, 'busy')
        os.makedirs(busy)
        open(os.path.join(busy, 'x'), 'w').close()
        stage = os.path.join(self.root, 'stage')
        for out in (busy, stage, os.path.join(stage, 'sub')):
            rc = self.st.run_probe_mode(self.args(probe=True, probe_out=out, window=[WINDOW], mgsys='SERALPHA'),
                                        self.root, stage)
            self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(stage))


class CommandLineTests(unittest.TestCase):
    """Argument rules, checked before any configuration is read."""

    def run_cli(self, *argv, prog=STAGER):
        return subprocess.run([sys.executable, prog] + list(argv), capture_output=True, text=True, timeout=60)

    def test_missing_target_message_is_unchanged(self):
        new, old = self.run_cli(), self.run_cli(prog=LEGACY_STAGER)
        self.assertEqual(new.returncode, old.returncode)
        self.assertEqual(new.stderr.strip().splitlines()[-1].split(': ', 1)[1],
                         old.stderr.strip().splitlines()[-1].split(': ', 1)[1])

    def test_probe_rejects_staging_options(self):
        r = self.run_cli('--probe', '--vms', 'x', '--probe-out', '/nonexistent/o', '--window', WINDOW, '--days', '3')
        self.assertEqual(r.returncode, 2)
        self.assertIn('--days cannot be combined with probe modes', r.stderr)

    def test_probe_needs_output_directory(self):
        r = self.run_cli('--discover', '--vms', 'x')
        self.assertEqual(r.returncode, 2)
        self.assertIn('--probe-out', r.stderr)

    def test_probe_options_need_a_probe_mode(self):
        r = self.run_cli('--vms', 'x', '--days', '1', '--window', WINDOW)
        self.assertEqual(r.returncode, 2)
        self.assertIn('apply only to --discover or --probe', r.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=1)
