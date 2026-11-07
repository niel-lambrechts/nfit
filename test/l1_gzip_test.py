#!/usr/bin/env python3
"""Gzip member validation regressions; synthetic files, no InfluxDB dependency."""
import gzip
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CHECK = Path(__file__).resolve().parent.parent / 'dev' / 'l1-check'


class GzipMemberTests(unittest.TestCase):
    def count(self, data):
        # A subprocess deadline makes an infinite-loop regression fail promptly.
        with tempfile.TemporaryDirectory(prefix='l1-gzip-') as tmp:
            path = Path(tmp) / 'synthetic.gz'
            path.write_bytes(data)
            return subprocess.run(
                [sys.executable, '-c',
                 "import runpy, sys; print(runpy.run_path(sys.argv[1])['gzip_member_count'](sys.argv[2]))",
                 str(CHECK), str(path)],
                capture_output=True, text=True, timeout=10)

    def test_concatenated_members_cross_output_limit(self):
        # Both members fit in one compressed read but each needs several output
        # buffers. At EOF the remaining input belongs to the next member.
        result = self.count(gzip.compress(b'A' * (4 << 20)) +
                            gzip.compress(b'B' * (4 << 20)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '2')

    def test_small_boundary_and_incompressible_members(self):
        cases = [
            [b'A' * 100, b'B' * 100],
            [b'A' * (1 << 20), b'B' * (1 << 20)],
            [b'A' * (4 << 20)],
            [os.urandom(2 << 20), os.urandom(2 << 20)],
        ]
        for parts in cases:
            with self.subTest(lengths=[len(part) for part in parts]):
                result = self.count(b''.join(gzip.compress(part) for part in parts))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(int(result.stdout.strip()), len(parts))

    def test_truncated_second_member_is_rejected(self):
        data = gzip.compress(b'A' * (4 << 20)) + gzip.compress(b'B' * (4 << 20))[:-5]
        result = self.count(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EOFError: truncated gzip member', result.stderr)

    def test_corrupt_second_member_is_rejected(self):
        member = bytearray(gzip.compress(b'B' * (4 << 20)))
        member[-8] ^= 1  # Corrupt the CRC while leaving the gzip header intact.
        result = self.count(gzip.compress(b'A' * (4 << 20)) + member)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('zlib.error', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=1)
