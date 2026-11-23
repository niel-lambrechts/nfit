#!/usr/bin/env python3
# ==============================================================================
# One entry point (nfit-profile, v10 milestone 1.3): the pool and PhysB evidence
# is used without being asked for when the cache holds it, and never invented
# when it does not.
#   - A standard run on a cache with pool observations and PhysB writes the
#     Entitlement Cut Exposure section; --no-pool-exposure leaves it out and
#     says so; --pool-exposure gives the same section as the automatic run.
#   - --update-history on that cache writes PhysBMetrics, PoolHistory and
#     MemberExposure; --no-physb-history --no-pool-history writes none of them;
#     the explicit options give the same month as the automatic run.
#   - On a legacy cache (no pool observations, no PhysB) a standard run has no
#     exposure section and names the reason, and --update-history writes no
#     PhysB or pool block and no "unavailable" marker.
# ==============================================================================
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mx = load('member_exposure_test')
sf = load('structural_feasibility_test')
# Not compared: timestamps, and the pool revision, a digest of the fixture's gzipped pool file, whose
# header carries the second it was written (two roots built across a second boundary differ in it).
TIMESTAMP_KEYS = {'availableAt', 'updated', 'LastUpdated', 'poolRevision'}
NEW_BLOCKS = ('PoolHistory', 'MemberExposure')
NEW_MARKERS = ('PoolEvidence', 'MemberExposureEvidence', 'PhysB', 'DailyPhysBUnavailable')


def strip_times(x):
    if isinstance(x, dict):
        return {k: strip_times(v) for k, v in x.items() if k not in TIMESTAMP_KEYS}
    if isinstance(x, list):
        return [strip_times(v) for v in x]
    return x


def run(root, cache, *flags):
    p = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--nmondir', cache] + list(flags),
                       capture_output=True, text=True, timeout=280, cwd=root)
    assert p.returncode == 0, p.stderr[-1500:]
    return p


def standard_log(root, cache, *flags):
    for old in glob.glob(os.path.join(root, 'output', '*')):
        os.remove(old)
    p = run(root, cache, '--runq-perc-behavior=match', *flags)
    logs = [f for f in glob.glob(os.path.join(root, 'output', '*.log')) if 'state-based' in f]
    assert logs, os.listdir(os.path.join(root, 'output'))
    with open(logs[0], encoding='utf-8') as fh:
        return fh.read(), p.stderr


def section(text, title, until='\nRecommended Actions'):
    start = text.index('\n' + title)
    return text[start:text.index(until, start)]


def month(root, serial, key):
    with open(os.path.join(root, 'history', serial, f'nfit.hist.{key}.json')) as fh:
        return json.load(fh)[key]


def evidence(m):
    """The pool and PhysB parts of a month, without run timestamps."""
    physb = {vm: v.get('PhysBMetrics') for vm, v in m['MonthlyWorkloadAnalysis'].items()}
    return strip_times({'blocks': {k: m.get(k) for k in NEW_BLOCKS},
                        'markers': {k: m['Metadata'].get(k) for k in NEW_MARKERS}, 'physb': physb})


@unittest.skipUnless(mx.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class OneEntryPointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        mx.MemberExposureTests.roots = []
        make = mx.MemberExposureTests.make_root
        cls.auto, cls.explicit, cls.off = make(), make(), make()
        cls.roots = list(mx.MemberExposureTests.roots)
        cls.cache = lambda r: os.path.join(r, 'stage', mx.SERIAL)
        history = ('--update-history', '--min-history-days', '1', '--force')
        run(cls.auto, cls.cache(cls.auto), *history)
        run(cls.explicit, cls.cache(cls.explicit), *history, '--physb-history', '--pool-history')
        run(cls.off, cls.cache(cls.off), *history, '--no-physb-history', '--no-pool-history')
        cls.log_auto, cls.err_auto = standard_log(cls.auto, cls.cache(cls.auto))
        cls.log_explicit, _ = standard_log(cls.explicit, cls.cache(cls.explicit), '--pool-exposure')
        cls.log_off, cls.err_off = standard_log(cls.off, cls.cache(cls.off), '--no-pool-exposure')

        cls.legacy = tempfile.mkdtemp(prefix='entry-legacy-')
        cls.roots.append(cls.legacy)
        cls.legacy_cache, _ = sf.build_root(cls.legacy)
        for prog in ('nfit', 'nfit-profile'):
            os.chmod(os.path.join(cls.legacy, prog), 0o755)
        cls.log_legacy, cls.err_legacy = standard_log(cls.legacy, cls.legacy_cache)
        cls.legacy_history = run(cls.legacy, cls.legacy_cache, '--update-history', '--min-history-days', '1', '--force')

    @classmethod
    def tearDownClass(cls):
        for r in cls.roots:
            shutil.rmtree(r, ignore_errors=True)

    def test_standard_run_checks_cut_exposure_without_being_asked(self):
        self.assertIn('\nEntitlement Cut Exposure\n', self.log_auto)
        self.assertIn('Entitlement cut exposure:', self.err_auto)
        self.assertIn('  Entitlement Cut    For a proposed', self.log_auto)          # the terminology entry
        self.assertEqual(section(self.log_auto, 'Entitlement Cut Exposure'),
                         section(self.log_explicit, 'Entitlement Cut Exposure'))
        self.assertNotIn('was not checked', self.log_auto)
        self.assertNotIn('could not be checked', self.log_auto)

    def test_no_pool_exposure_leaves_the_section_out_and_says_so(self):
        self.assertNotIn('\nEntitlement Cut Exposure\n', self.log_off)
        self.assertNotIn('  Entitlement Cut    For a proposed', self.log_off)
        self.assertNotIn('Entitlement cut exposure:', self.err_off)
        self.assertIn('Pool competition for entitlement cuts was not checked (--no-pool-exposure).', self.log_off)

    def test_history_update_writes_pool_and_physb_evidence_without_being_asked(self):
        auto = evidence(month(self.auto, mx.SERIAL, mx.MONTH))
        self.assertTrue(auto['blocks']['PoolHistory'] and auto['blocks']['MemberExposure'])
        self.assertTrue(auto['markers']['PoolEvidence'] and auto['markers']['MemberExposureEvidence'] and auto['markers']['PhysB'])
        self.assertTrue(all(auto['physb'].values()))
        self.assertEqual(auto, evidence(month(self.explicit, mx.SERIAL, mx.MONTH)))

    def test_no_options_write_no_evidence(self):
        off = evidence(month(self.off, mx.SERIAL, mx.MONTH))
        self.assertEqual(off['blocks'], {k: None for k in NEW_BLOCKS})
        self.assertEqual(off['markers'], {k: None for k in NEW_MARKERS})
        self.assertFalse(any(off['physb'].values()))

    def test_legacy_cache_is_left_as_it_was(self):
        self.assertNotIn('\nEntitlement Cut Exposure\n', self.log_legacy)
        self.assertNotIn('  Entitlement Cut    For a proposed', self.log_legacy)
        self.assertIn('Pool competition for entitlement cuts could not be checked: the cache holds no pool observations.',
                      ' '.join(self.log_legacy.split()))
        self.assertNotIn('--physb-history', self.legacy_history.stderr)
        self.assertNotIn('--pool-history', self.legacy_history.stderr)
        key = sorted(f.split('.')[2] for f in os.listdir(os.path.join(self.legacy, 'history', sf.SERIAL))
                     if f.startswith('nfit.hist.'))[0]
        legacy = evidence(month(self.legacy, sf.SERIAL, key))
        self.assertEqual(legacy['blocks'], {k: None for k in NEW_BLOCKS})
        self.assertEqual(legacy['markers'], {k: None for k in NEW_MARKERS})
        self.assertFalse(any(legacy['physb'].values()))


if __name__ == '__main__':
    unittest.main()
