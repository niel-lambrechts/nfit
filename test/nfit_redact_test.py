#!/usr/bin/env python3
# ==============================================================================
# Unit test for the vocabulary cache of dev/nfit-redact: a run that can read
# stage/ saves the vocabulary (mode 0600); a later run that cannot read stage/
# still redacts the saved system names, and a run with neither source warns.
# Synthetic data only.
# ==============================================================================
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REDACT = os.path.abspath(os.path.join(HERE, '..', 'dev', 'nfit-redact'))
SYSTEM = 'Server-9999-ZZZ-SNQ7TEST1'


def run(root, text, secret):
    return subprocess.run(['python3', REDACT, '--root', str(root), '--secret-file', str(secret), '--allow-empty'],
                          input=text, capture_output=True, text=True)


class VocabularyCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'app'
        (self.root / 'stage' / SYSTEM).mkdir(parents=True)
        (self.root / 'stage' / SYSTEM / '.nfit.cache.states').write_text(json.dumps({'lparq1': []}))
        self.secret = self.tmp / 'priv' / 'redact.secret'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cache_written_and_used_when_stage_is_unreadable(self):
        first = run(self.root, f'path /x/{SYSTEM}/y\n', self.secret)
        self.assertNotIn(SYSTEM, first.stdout)
        cache = self.secret.parent / 'redact.vocab.json'
        self.assertTrue(cache.is_file())
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        shutil.rmtree(self.root / 'stage')
        second = run(self.root, f'path /x/{SYSTEM}/y\n', self.secret)
        self.assertNotIn(SYSTEM, second.stdout)
        self.assertEqual(first.stdout, second.stdout)
        self.assertNotIn('WARNING', second.stderr)

    def test_warns_without_stage_or_cache(self):
        shutil.rmtree(self.root / 'stage')
        res = run(self.root, 'nothing here\n', self.secret)
        self.assertIn('stage/ is not readable', res.stderr)


if __name__ == '__main__':
    unittest.main()
