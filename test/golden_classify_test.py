#!/usr/bin/env python3
# ==============================================================================
# Unit test for the --classify listing of dev/golden: a changed cell outside
# the intended changes is listed even when more than 20 expected current-cycle
# cells changed before it in the file.
# ==============================================================================
import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.abspath(os.path.join(HERE, '..', 'dev', 'golden'))


def _load_golden():
    loader = importlib.machinery.SourceFileLoader('golden_harness', GOLDEN)
    spec = importlib.util.spec_from_loader('golden_harness', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ClassifyCsvListing(unittest.TestCase):
    def test_unintended_cell_survives_the_listing_cap(self):
        golden = _load_golden()
        # The v10 layout: a column moved to the rationale and one added, so that
        # rVCPU_Tactical cells count as completed by the current-cycle pair.
        base = 'VM,TIER,NETT,P-99W1_rVCPU_Tactical\n' + ''.join(f'vm{i:02d},G3,0,1\n' for i in range(25)) + 'vmzz,O3,0,1\n'
        cur = ('VM,TIER,Planning Profile,P-99W1_rVCPU_Tactical\n' + ''.join(f'vm{i:02d},G3,G3-95W15,2\n' for i in range(25))
               + 'vmzz,O4,O4-90W15,1\n')
        c = golden.classify_csv(base, cur)
        self.assertEqual(c['cells'], 1)
        self.assertEqual(c['completed'], 25)
        self.assertEqual(c['cell_lines'], ['vmzz TIER: O3 -> O4'])
        self.assertEqual(len(c['completed_lines']), 20)


if __name__ == '__main__':
    unittest.main()
