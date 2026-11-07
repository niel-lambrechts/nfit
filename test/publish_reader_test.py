#!/usr/bin/env python3
# ==============================================================================
# Reader-side tests for generation publication (Increment 3e), on the real
# nfit and nfit-profile: shared lock before any L1 read, refusal while a
# publication journal is present, waiting for a publisher, and the nested
# sequence (nfit-profile holds its shared lock, a publisher is waiting for
# the exclusive lock, nfit-profile then launches nfit), which must complete
# without deadlock and without a publication between the parent's reads.
# Runs copies of the programs in a scratch root with tracked configuration
# only, on a synthetic fixture cache.
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
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
FIXTURE = os.path.join(HERE, 'fixtures', 'stage', 'SYN0002')
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default',
               'nfit.scenarios.cfg.default', 'nfit.seasonality.cfg')
ENDDATE = '2026-11-05'


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def perl_ready():
    return subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0


@unittest.skipUnless(shutil.which('perl') and perl_ready(), 'perl with JSON and List::MoreUtils needed')
class ReaderLockTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.st = load(os.path.join(REPO, 'nfit-stage-influxdb'), 'stager_reader_test')

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='reader-lock-')
        os.makedirs(os.path.join(self.root, 'etc'))
        os.makedirs(os.path.join(self.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(REPO, prog), self.root)
        for name in TRACKED_ETC:
            src = os.path.join(REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(self.root, 'etc', name))
        self.dir = os.path.join(self.root, 'stage', 'SYN0002')
        shutil.copytree(FIXTURE, self.dir)
        self.lock = os.path.join(self.dir, '.nfit.cache.publish.lock')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def nfit(self, *extra, wait=True):
        cmd = ['perl', os.path.join(self.root, 'nfit'), '--nmondir', self.dir, '--enddate', ENDDATE,
               '--profile-label', 'P99'] + list(extra)
        if not wait:
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    def profile_cmd(self, *extra):
        return [os.path.join(self.root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', ENDDATE,
                '--nmondir', self.dir] + list(extra)

    def build_generation(self, tag):
        """A work generation whose data differs from the fixture in one value."""
        work = self.st.begin_work_dir(self.dir)
        with open(os.path.join(self.dir, '.nfit.cache.data')) as fh:
            lines = fh.read().splitlines()
        cells = lines[1].split(',')
        cells[2] = tag
        lines[1] = ','.join(cells)
        with open(os.path.join(work, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        for name in ('.nfit.cache.states', '.nfit.cache.manifest', '.nfit_stage_id'):
            shutil.copy(os.path.join(self.dir, name), os.path.join(work, name))
        return work

    def publish(self, work, wait_s):
        return self.st.publish_generation(self.dir, work,
                                          replace=('.nfit.cache.data', '.nfit.cache.states',
                                                   '.nfit.cache.manifest', '.nfit_stage_id'),
                                          invalidate=[self.st.CACHE_INDEX_FILE], wait_s=wait_s)

    def lock_is_held_shared(self):
        with open(self.lock, 'a') as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True

    # -- refusal and waiting ---------------------------------------------------------
    def test_readers_refuse_an_unrecovered_publication(self):
        with open(os.path.join(self.dir, '.publish.journal'), 'w') as fh:
            fh.write('{}')
        r = self.nfit()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('interrupted publication that has not been recovered', r.stderr)
        r = subprocess.run(['perl'] + self.profile_cmd(), capture_output=True, text=True, timeout=120,
                           cwd=self.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('interrupted publication that has not been recovered', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, '.nfit.cache.idx')))   # nothing was read

    def test_nfit_waits_while_a_publication_holds_the_lock(self):
        r0 = self.nfit()
        self.assertEqual(r0.returncode, 0, r0.stderr[-500:])
        with open(self.lock, 'a') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            proc = self.nfit(wait=False)
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), 'nfit read the cache during a publication')
            fcntl.flock(fh, fcntl.LOCK_UN)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, err[-500:])
        self.assertIn('Waiting for a cache publication to finish', err)
        # Same results as the unobstructed run (key order of nfit's JSON varies).
        canon = lambda text: sorted(json.dumps(json.loads(line), sort_keys=True)
                                    for line in text.splitlines() if line.strip())
        self.assertEqual(canon(out), canon(r0.stdout))

    # -- the nested sequence -------------------------------------------------------------
    def test_nested_profile_nfit_with_a_waiting_publisher(self):
        flags = os.path.join(self.root, 'flags')
        os.makedirs(flags)
        hashes = os.path.join(flags, 'data-hashes')
        wrapper = os.path.join(self.root, 'nfit-wrapper')
        real = os.path.join(self.root, 'nfit')
        data = os.path.join(self.dir, '.nfit.cache.data')
        with open(wrapper, 'w') as fh:
            fh.write(f'''#!/bin/sh
# Stands in for nfit as launched by nfit-profile: records the data file seen by
# each launch, and holds the first analysis launch until the test says go.
case " $* " in *" --version "*) exec perl "{real}" "$@";; esac
"{sys.executable}" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "{data}" >> "{hashes}"
if [ ! -e "{flags}/launched" ]; then
  touch "{flags}/launched"
  while [ ! -e "{flags}/go" ]; do sleep 0.1; done
fi
exec perl "{real}" "$@"
''')
        os.chmod(wrapper, 0o755)
        gen1 = sha(data)
        profile = subprocess.Popen(['perl'] + self.profile_cmd('--nfit-path', wrapper), cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 60
        while not os.path.exists(os.path.join(flags, 'launched')):
            self.assertIsNone(profile.poll(), 'nfit-profile ended before launching nfit')
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
        self.assertTrue(self.lock_is_held_shared(), 'nfit-profile holds no shared lock while launching nfit')

        work = self.build_generation('7.7777')
        published = {}

        def publisher():
            try:
                self.publish(work, wait_s=120)
                published['at'] = time.monotonic()
            except Exception as exc:     # reported by the assertions below
                published['error'] = exc
        thread = threading.Thread(target=publisher)
        thread.start()
        time.sleep(1.0)
        self.assertNotIn('at', published, 'published while nfit-profile held its shared lock')
        self.assertFalse(os.path.exists(os.path.join(self.dir, '.publish.journal')))

        open(os.path.join(flags, 'go'), 'w').close()       # nfit-profile's nfit proceeds now
        out, err = profile.communicate(timeout=170)
        profile_end = time.monotonic()
        thread.join(timeout=150)
        self.assertEqual(profile.returncode, 0, err[-800:])
        self.assertNotIn('error', published, published.get('error'))
        self.assertGreaterEqual(published['at'], profile_end - 0.5)   # publication only after the reader
        with open(hashes) as fh:
            seen = fh.read().split()
        self.assertGreaterEqual(len(seen), 1)
        self.assertEqual(set(seen), {gen1}, 'nfit saw a different generation during the profile run')
        self.assertNotEqual(sha(data), gen1)                  # and the new generation is now in place


if __name__ == '__main__':
    unittest.main(verbosity=1)
