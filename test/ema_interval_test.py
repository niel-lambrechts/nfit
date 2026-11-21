#!/usr/bin/env python3
# ==============================================================================
# The engine's EMA at sampling intervals other than one minute. The decay
# level's weight is the weight of one sample at one minute; at another interval
# the weight is 1 - (1 - alpha) ** (interval / 60 s), so that the smoothing
# spans the same elapsed time.
#   - one minute: the reference weight itself, checked against an independent EMA;
#   - 30 seconds, 5 and 15 minutes: the engine agrees with an independent EMA at
#     the interval's weight, and the percentile stays close to the one-minute
#     figure (with a fixed weight per sample it fell by about a fifth at 15
#     minutes on this series);
#   - a gap: the sample after it carries one interval's weight, not the gap's;
#   - the paired PhysC/PhysB path smooths with the same weight as the ordinary path;
#   - SMA is untouched.
# The series is a 30-second one; coarser series are its block means. Block means
# still hide what happens inside an interval: agreement with the one-minute
# figure is approximate and is not the engine's doing.
# ==============================================================================
import math
import random
import unittest
import paired_engine_test as pe

ALPHA = {'low': 0.03, 'medium': 0.08, 'high': 0.15}
DAYS = 10


def base_series():
    """30-second PhysC with a daytime level and short bursts."""
    random.seed(11)
    out, burst = [], 0
    for i in range(DAYS * 2880):
        hour = (i // 120) % 24
        level = 0.9 if 8 <= hour < 18 else 0.35
        if burst == 0 and random.random() < (0.006 if 8 <= hour < 18 else 0.0015):
            burst = random.randint(4, 18)
        c = level * math.exp(random.gauss(0, 0.15)) + (random.uniform(1.0, 2.2) if burst else 0)
        burst -= 1 if burst else 0
        out.append(min(c, 3.9))
    return out


def blocks(series, n):
    return [sum(series[i:i + n]) / n for i in range(0, len(series) - n + 1, n)]


def ema(values, alpha, window):
    """The engine's EMA: seeded by the plain average of the first `window` samples."""
    out, prev, warm = [], None, []
    for v in values:
        if prev is None:
            warm.append(v)
            if len(warm) == window:
                prev = sum(warm) / window
                out.append(prev)
            continue
        prev = v * alpha + prev * (1 - alpha)
        out.append(prev)
    return out


def percentile(values, q):
    v = sorted(values)
    r = (len(v) - 1) * q
    lo = int(r)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (r - lo)


@unittest.skipUnless(pe.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class EmaIntervalTests(unittest.TestCase):
    # The cache builder and engine runner of the paired-engine tests, without their test cases.
    setUp, tearDown = pe.PairedEngineTests.setUp, pe.PairedEngineTests.tearDown
    cache, nfit = pe.PairedEngineTests.cache, pe.PairedEngineTests.nfit
    half_minutes = base_series()
    START = pe.T0 - 8 * 3600                                   # midnight UTC

    def rows(self, values, step, skip=()):
        return [(self.START + i * step, round(v, 4), round(v / 2, 4)) for i, v in enumerate(values) if i not in skip]

    def engine(self, rows, *flags):
        self._first, self._last = min(r[0] for r in rows), max(r[0] for r in rows)
        return self.nfit(self.cache(rows), '-p', '95', '-w', '15', *flags)[0]

    def reference(self, values, step, decay='medium', window=None):
        alpha = ALPHA[decay] if step == 60 else 1 - (1 - ALPHA[decay]) ** (step / 60)
        samples = window if window is not None else max(1, 15 // max(1, round(step / 60)))
        return percentile(ema([round(v, 4) for v in values], alpha, samples), 0.95)

    def test_one_minute_uses_the_reference_weight(self):
        values = blocks(self.half_minutes, 2)
        for decay in ('low', 'medium', 'high'):
            got = self.engine(self.rows(values, 60), '--decay', decay)['metrics']['physc']['T']['P95']
            self.assertAlmostEqual(got, self.reference(values, 60, decay), delta=0.002, msg=decay)
            self.tearDown()
            self.setUp()

    def test_other_intervals_keep_the_smoothing_in_elapsed_time(self):
        one_minute = self.reference(blocks(self.half_minutes, 2), 60)
        for step, n in ((30, 1), (300, 10), (900, 30)):
            values = blocks(self.half_minutes, n)
            got = self.engine(self.rows(values, step))['metrics']['physc']['T']['P95']
            # The arithmetic: an independent EMA at the interval's weight.
            self.assertAlmostEqual(got, self.reference(values, step), delta=0.003, msg=f'{step} s')
            # The purpose: close to the one-minute figure. A fixed weight per sample is far off at 15 minutes.
            self.assertLess(abs(got - one_minute) / one_minute, 0.05, f'{step} s: {got:.3f} against {one_minute:.3f}')
            fixed = percentile(ema(values, ALPHA['medium'], max(1, 15 // max(1, round(step / 60)))), 0.95)
            if step == 900:
                self.assertGreater((one_minute - fixed) / one_minute, 0.10, 'the old weight is what this test guards against')
            self.tearDown()
            self.setUp()

    def test_a_sample_after_a_gap_carries_one_interval(self):
        values = blocks(self.half_minutes, 30)                              # 15-minute series
        skip = set(range(400, 412))                                          # three hours missing
        got = self.engine(self.rows(values, 900, skip))['metrics']['physc']['T']['P95']
        kept = [v for i, v in enumerate(values) if i not in skip]
        self.assertAlmostEqual(got, self.reference(kept, 900), delta=0.003)

    def test_sma_is_untouched(self):
        values = blocks(self.half_minutes, 30)
        got = self.engine(self.rows(values, 900), '--avg-method', 'sma')['metrics']['physc']['T']['P95']
        self.assertAlmostEqual(got, percentile([round(v, 4) for v in values], 0.95), delta=0.002)   # a 15-minute window is one sample

    def test_paired_and_ordinary_smoothing_agree(self):
        values = blocks(self.half_minutes, 30)
        out = self.engine(self.rows(values, 900), '--physb-paired')
        ordinary = out['metrics']['physc']['T']['P95']
        paired = out['metrics']['physb']['T']
        self.assertAlmostEqual(paired['tail']['P95']['threshold'], ordinary, delta=0.003)
        self.assertEqual(out['physbProvenance']['derivationVersion'], 'paired-3')


if __name__ == '__main__':
    unittest.main()
