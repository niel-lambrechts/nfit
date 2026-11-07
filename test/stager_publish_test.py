#!/usr/bin/env python3
# ==============================================================================
# Tests for generation publication (Increment 3e) in nfit-stage-influxdb:
# journalled swap, validation, interruption at every step and recovery
# (itself interruptible), reader-held lock timeout, stable lock file, index and
# results-cache invalidation, schema-1 revisions (same size and mtime), append
# and pool-only through the journal. Synthetic data only.
# ==============================================================================
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.join(HERE, '..')
STAGER = os.environ.get('NFIT_STAGER', os.path.join(REPO, 'nfit-stage-influxdb'))
CHECK = os.path.join(REPO, 'dev', 'l1-check')
FILES = ('.nfit.cache.data', '.nfit.cache.states', '.nfit.cache.manifest', '.nfit_stage_id')


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Interrupted(Exception):
    pass


class PublicationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.st = load(STAGER, 'stager_publish')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='publish-test-')
        self.dir = os.path.join(self.tmp, 'SYS1')
        os.makedirs(self.dir)
        self.st._PUBLISH_FAULT = None
        self.gen = {}
        self.publish(self.build('g1'))

    def tearDown(self):
        self.st._PUBLISH_FAULT = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def build(self, tag, rows=3):
        """Writes a complete schema-1 generation in the work directory and
        records its file hashes under self.gen[tag]."""
        work = self.st.begin_work_dir(self.dir)
        data = 'Timestamp,VMName,PhysC,RunQ\n' + ''.join(
            f'2026-10-01 00:0{i}:00,vm{tag},{i}.5,1.0\n' for i in range(rows))
        with open(os.path.join(work, '.nfit.cache.data'), 'w') as fh:
            fh.write(data)
        with open(os.path.join(work, '.nfit.cache.states'), 'w') as fh:
            json.dump({f'vm{tag}': []}, fh)
        with open(os.path.join(work, '.nfit_stage_id'), 'w') as fh:
            fh.write(f'stage {tag}\n')
        lines = ['cache_build_status: success', 'data_start: 2026-10-01',
                 f'data_end: 2026-10-01 00:0{rows - 1}:00']
        lines += self.st.revision_manifest_lines(os.path.join(work, '.nfit.cache.data'),
                                                 os.path.join(work, '.nfit.cache.states'))
        with open(os.path.join(work, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        self.gen[tag] = {f: sha(os.path.join(work, f)) for f in FILES}
        return work

    def publish(self, work, **kw):
        kw.setdefault('wait_s', 5)
        return self.st.publish_generation(self.dir, work, replace=FILES,
                                          invalidate=kw.pop('invalidate', [self.st.CACHE_INDEX_FILE]), **kw)

    def generation_in_place(self):
        current = {f: sha(os.path.join(self.dir, f)) for f in FILES if os.path.exists(os.path.join(self.dir, f))}
        for tag, hashes in self.gen.items():
            if current == hashes:
                return tag
        return None

    def fault_at(self, step):
        def hook(s):
            if s == step:
                raise Interrupted(step)
        self.st._PUBLISH_FAULT = hook

    def l1_check(self):
        r = subprocess.run([sys.executable, CHECK, '--pseudonym-secret', os.devnull, self.dir],
                           capture_output=True, text=True)
        return r.returncode, r.stdout

    # -- normal publication ------------------------------------------------------
    def test_publication_and_previous_generation(self):
        self.assertEqual(self.generation_in_place(), 'g1')
        self.publish(self.build('g2'))
        self.assertEqual(self.generation_in_place(), 'g2')
        prev = os.path.join(self.dir, '.previous')
        self.assertEqual({f: sha(os.path.join(prev, f)) for f in FILES}, self.gen['g1'])
        self.publish(self.build('g3'))
        self.assertEqual({f: sha(os.path.join(prev, f)) for f in FILES}, self.gen['g2'])   # one kept
        for leftover in ('.publish.journal', '.work', '.publish.journal.tmp'):
            self.assertFalse(os.path.exists(os.path.join(self.dir, leftover)))
        self.assertEqual(self.l1_check()[0], 0)

    def test_lock_file_is_stable(self):
        lock = os.path.join(self.dir, self.st.PUBLISH_LOCK_FILE)
        inode = os.stat(lock).st_ino
        self.publish(self.build('g2'))
        self.fault_at('publish:placed:.nfit.cache.states')
        with self.assertRaises(Interrupted):
            self.publish(self.build('g3'))
        self.st._PUBLISH_FAULT = None
        self.st.recover_publication(self.dir, 5)
        self.assertEqual(os.stat(lock).st_ino, inode)

    def test_index_invalidated_when_cpu_data_changes(self):
        idx = os.path.join(self.dir, '.nfit.cache.idx')
        results = os.path.join(self.dir, '.nfit.cache.results')
        for p in (idx, results):
            with open(p, 'w') as fh:
                fh.write('{}')
        self.publish(self.build('g2'))
        self.assertFalse(os.path.exists(idx))
        self.assertTrue(os.path.exists(results))        # keyed by revision; kept
        with open(idx, 'w') as fh:
            fh.write('{}')
        self.publish(self.build('g3'), invalidate=[self.st.CACHE_INDEX_FILE, self.st.RESULTS_CACHE_FILE])
        self.assertFalse(os.path.exists(results))

    def test_validation_failure_publishes_nothing(self):
        work = self.build('g2')
        with open(os.path.join(work, '.nfit.cache.data'), 'a') as fh:
            fh.write('2026-10-01 00:09:00,vmg2,9.9,1.0\n')        # data no longer matches data_revision
        with self.assertRaises(self.st.PublicationError):
            self.publish(work)
        self.assertEqual(self.generation_in_place(), 'g1')
        self.assertFalse(os.path.exists(os.path.join(self.dir, '.publish.journal')))
        self.assertEqual(self.st.recover_publication(self.dir, 5), 'discarded')
        self.assertFalse(os.path.exists(os.path.join(self.dir, '.work')))


    # -- interruption and recovery ------------------------------------------------
    STEPS_BEFORE_COMMIT = ('work_written', 'validated')
    STEPS_AFTER_COMMIT = ('journal',
                          'publish:moved:.nfit.cache.data', 'publish:placed:.nfit.cache.data',
                          'publish:moved:.nfit.cache.states', 'publish:placed:.nfit.cache.states',
                          'publish:moved:.nfit.cache.manifest', 'publish:placed:.nfit.cache.manifest',
                          'publish:moved:.nfit_stage_id', 'publish:placed:.nfit_stage_id',
                          'publish:invalidated', 'publish:journal_removed')

    def test_interruption_at_every_step(self):
        for step in self.STEPS_BEFORE_COMMIT + self.STEPS_AFTER_COMMIT:
            with self.subTest(step=step):
                self.tearDown()
                self.setUp()
                self.fault_at(step)
                with self.assertRaises(Interrupted):
                    self.publish(self.build('g2'))
                self.st._PUBLISH_FAULT = None
                journal = os.path.exists(os.path.join(self.dir, '.publish.journal'))
                committed = step in self.STEPS_AFTER_COMMIT and step != 'publish:journal_removed'
                self.assertEqual(journal, committed)
                if not journal:
                    # Never committed (or already finished): the directory holds one generation.
                    self.assertIn(self.generation_in_place(), ('g1', 'g2'))
                outcome = self.st.recover_publication(self.dir, 5)
                expected = 'g2' if step in self.STEPS_AFTER_COMMIT else 'g1'
                self.assertEqual(self.generation_in_place(), expected)
                self.assertEqual(outcome, 'completed' if committed else 'discarded')
                for leftover in ('.publish.journal', '.work'):
                    self.assertFalse(os.path.exists(os.path.join(self.dir, leftover)))
                rc, out = self.l1_check()
                self.assertEqual(rc, 0, out)

    def test_recovery_interrupted_then_completed(self):
        self.fault_at('publish:placed:.nfit.cache.data')
        with self.assertRaises(Interrupted):
            self.publish(self.build('g2'))
        self.fault_at('recover:placed:.nfit.cache.manifest')
        with self.assertRaises(Interrupted):
            self.st.recover_publication(self.dir, 5)
        self.assertTrue(os.path.exists(os.path.join(self.dir, '.publish.journal')))
        self.fault_at('recover:invalidated')
        with self.assertRaises(Interrupted):
            self.st.recover_publication(self.dir, 5)
        self.st._PUBLISH_FAULT = None
        self.assertEqual(self.st.recover_publication(self.dir, 5), 'completed')
        self.assertEqual(self.generation_in_place(), 'g2')
        self.assertEqual(self.l1_check()[0], 0)

    def test_journal_present_fails_l1_check(self):
        self.fault_at('journal')
        with self.assertRaises(Interrupted):
            self.publish(self.build('g2'))
        rc, out = self.l1_check()
        self.assertEqual(rc, 1)
        self.assertIn('interrupted publication has not been recovered', out)

    # -- readers hold the lock -------------------------------------------------------
    def test_timeout_while_a_reader_holds_the_lock(self):
        lock = os.path.join(self.dir, self.st.PUBLISH_LOCK_FILE)
        holder = subprocess.Popen([sys.executable, '-c',
                                   'import fcntl,sys,time; f=open(sys.argv[1],"a"); fcntl.flock(f, fcntl.LOCK_SH); '
                                   'print("held", flush=True); time.sleep(30)', lock],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'held')
            work = self.build('g2')
            started = time.monotonic()
            with self.assertRaises(self.st.PublicationTimeout):
                self.publish(work, wait_s=1)
            self.assertGreaterEqual(time.monotonic() - started, 1)
            self.assertEqual(self.generation_in_place(), 'g1')
            self.assertFalse(os.path.exists(os.path.join(self.dir, '.publish.journal')))
        finally:
            holder.kill()
            holder.wait()
        self.publish(self.build('g3'))                      # the reader has gone
        self.assertEqual(self.generation_in_place(), 'g3')

    # -- schema-1 identity: same size, same mtime, different content ---------------------
    def test_schema_one_identity_differs_for_same_size_and_mtime(self):
        """nfit keys L2 results by data_revision when the manifest has one. Two
        generations with the same data size and modification time but other
        content must get different identities."""
        data = os.path.join(self.dir, '.nfit.cache.data')
        size1, mtime = os.path.getsize(data), os.stat(data).st_mtime
        id1 = self.nfit_identity()
        work = self.build('g9')                               # same row count; tag keeps the size equal
        self.publish(work)
        os.utime(data, (mtime, mtime))
        self.assertEqual(os.path.getsize(data), size1)
        id2 = self.nfit_identity()
        self.assertTrue(id1['data'].startswith('rev:sha256:'))
        self.assertNotEqual(id1['data'], id2['data'])

    def nfit_identity(self):
        if not shutil.which('perl'):
            self.skipTest('perl not available')
        engine = os.environ.get('NFIT_ENGINE', os.path.join(REPO, 'nfit'))
        script = r'''
use strict; use warnings; use File::Spec;
my ($src_path, $dir) = @ARGV;
open my $fh, '<', $src_path or die; my $src = do { local $/; <$fh> }; close $fh;
my $code = '';
for my $n (qw(_read_cache_manifest_meta _l1_data_identity)) {
    $src =~ /^(sub \Q$n\E\s*\{.*?^\})/ms or die "no $n"; $code .= "$1\n";
}
eval "package UUT; use File::Spec; our \$CACHE_DATA_FILE='.nfit.cache.data'; our \$CACHE_STATES_FILE='.nfit.cache.states';"
   . "our \$CACHE_MANIFEST_FILE='.nfit.cache.manifest';\n$code\n1;" or die $@;
my $id = UUT::_l1_data_identity($dir, UUT::_read_cache_manifest_meta($dir));
print "$id->{data}\n$id->{states}\n";
'''
        r = subprocess.run(['perl', '-e', script, engine, self.dir], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data, states = r.stdout.split()
        return {'data': data, 'states': states}


import stager_pool_test as pool_fixture  # noqa: E402


@unittest.skipUnless(pool_fixture.HAVE_CLIENT, 'influxdb client package not installed')
class StagingPublicationTests(unittest.TestCase):
    """Staging paths through the journal, run with main() against the fake."""

    @classmethod
    def setUpClass(cls):
        cls.harness = pool_fixture.PoolCaptureTests
        cls.harness.setUpClass()
        cls.st = cls.harness.st

    @classmethod
    def tearDownClass(cls):
        cls.harness.tearDownClass()

    def setUp(self):
        self.h = self.harness('test_off_by_default_writes_no_pool_file_or_keys')
        self.h.setUp()
        self.st._PUBLISH_FAULT = None

    def tearDown(self):
        self.st._PUBLISH_FAULT = None
        self.h.tearDown()

    def files(self, serial='S1'):
        d = os.path.join(self.h.stage, serial)
        return {f: sha(os.path.join(d, f)) for f in FILES}

    def test_pool_only_keeps_cpu_identities_index_and_results(self):
        self.assertEqual(self.h.stage_full('--l1-schema', '2')[0], 0)
        d = os.path.join(self.h.stage, 'S1')
        for name in ('.nfit.cache.idx', '.nfit.cache.results'):
            with open(os.path.join(d, name), 'w') as fh:
                fh.write('{}')
        before = self.files()
        m_before = self.h.manifest('S1')
        rc, _, err = self.h.run_main('--mgsys', 'S1', '--pool-only')
        self.assertEqual(rc, 0, err)
        after = self.files()
        for f in ('.nfit.cache.data', '.nfit.cache.states', '.nfit_stage_id'):
            self.assertEqual(after[f], before[f])
        m_after = self.h.manifest('S1')
        self.assertEqual((m_after['data_revision'], m_after['states_revision']),
                         (m_before['data_revision'], m_before['states_revision']))
        self.assertTrue(os.path.exists(os.path.join(d, '.nfit.cache.idx')))
        self.assertTrue(os.path.exists(os.path.join(d, '.nfit.cache.results')))
        self.assertEqual(m_after['pool_capture_status'], 'complete')

    def test_full_restage_invalidates_index(self):
        self.assertEqual(self.h.stage_full()[0], 0)
        d = os.path.join(self.h.stage, 'S1')
        with open(os.path.join(d, '.nfit.cache.idx'), 'w') as fh:
            fh.write('{}')
        self.assertEqual(self.h.stage_full()[0], 0)
        self.assertFalse(os.path.exists(os.path.join(d, '.nfit.cache.idx')))
        self.assertIn('data_revision', self.h.manifest('S1'))

    def test_interrupted_append_leaves_published_files_untouched(self):
        self.assertEqual(self.h.stage_full()[0], 0)
        before = self.files()

        def hook(step):
            if step == 'validated':
                raise self.st.PublicationError('simulated interruption')
        self.st._PUBLISH_FAULT = hook
        rc, _, _ = self.h.run_main('--mgsys', 'S1,S2', '--append', '--endd', '2026-10-01')
        self.assertEqual(rc, 4)
        self.assertEqual(self.files(), before)          # the old in-place trim no longer happens
        self.st._PUBLISH_FAULT = None
        rc, _, err = self.h.run_main('--mgsys', 'S1,S2', '--append', '--endd', '2026-10-01')
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(self.h.stage, 'S1', '.work')))

    def test_interrupted_staging_is_recovered_by_the_next_run(self):
        self.assertEqual(self.h.stage_full()[0], 0)

        def hook(step):
            if step == 'publish:placed:.nfit.cache.data':
                raise self.st.PublicationError('simulated interruption')
        self.st._PUBLISH_FAULT = hook
        self.assertEqual(self.h.stage_full('--l1-schema', '2')[0], 4)
        self.assertTrue(os.path.exists(os.path.join(self.h.stage, 'S1', '.publish.journal')))
        self.st._PUBLISH_FAULT = None
        rc, _, err = self.h.run_main('--mgsys', 'S1', '--pool-only')     # any run recovers first
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(self.h.stage, 'S1', '.publish.journal')))
        self.assertEqual(self.h.manifest('S1').get('data_schema_version'), '2')   # rolled forward

    def test_cleanup_moves_pool_files_to_previous(self):
        self.assertEqual(self.h.stage_full('--pool-capture', 'optional')[0], 0)
        self.assertEqual(self.h.stage_full('--cleanup')[0], 0)
        d = os.path.join(self.h.stage, 'S1')
        self.assertFalse(os.path.exists(os.path.join(d, self.st.POOL_OBSERVATIONS_FILE)))
        self.assertTrue(os.path.exists(os.path.join(d, '.previous', self.st.POOL_OBSERVATIONS_FILE)))


if __name__ == '__main__':
    unittest.main(verbosity=1)
