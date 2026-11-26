#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/stager_states_test.py
# PURPOSE  : The configuration-state timeline nfit-stage-influxdb builds for a
#            VM (D-025).
#            - the last state ends at the VM's own last sample, not at the last
#              sample of the whole capture: an LPAR that left the managed system
#              no longer shows a current configuration there;
#            - earlier states and state boundaries are as before;
#            - a VM without a sample in the run keeps the capture end;
#            - the last sample of each VM is tracked over every metric acquired;
#            - in an append run a VM without new samples is not merged, so its
#              states are not extended by a carried-forward configuration value.
# USAGE    : python3 test/stager_states_test.py
# ==============================================================================
import importlib.machinery
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STAGER = os.environ.get('NFIT_STAGER', os.path.join(HERE, '..', 'nfit-stage-influxdb'))

try:
    loader = importlib.machinery.SourceFileLoader('stager_states_uut', STAGER)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    S = importlib.util.module_from_spec(spec)
    loader.exec_module(S)
    READY = True
except ImportError:                                  # the InfluxDB client is not installed here
    READY = False

DAY = 86400
T0 = 1_785_000_000                                   # an arbitrary capture start


def events(*changes):
    """Configuration events from (day offset, {key: value}) pairs."""
    return [{'ts': T0 + int(day * DAY), 'key': key, 'value': value} for day, change in changes for key, value in change.items()]


@unittest.skipUnless(READY, 'the stager cannot be loaded (InfluxDB client not installed)')
class StateTimelineTests(unittest.TestCase):

    def test_the_last_state_ends_at_the_last_sample_of_the_vm(self):
        capture_end = T0 + 90 * DAY
        left = T0 + 42 * DAY + 600
        states = S.build_vm_states(events((0, {'entitlement': 2.0, 'virtual_cpus': 4, 'pool_id': 0, 'pool_cpu': 120, 'serial_number': 'F1'}),
                                          (10, {'entitlement': 3.0})), capture_end, left)
        self.assertEqual([(s['start_epoch'], s['end_epoch'], s['metadata']['entitlement']) for s in states],
                         [(T0, T0 + 10 * DAY - 1, 2.0), (T0 + 10 * DAY, left, 3.0)])
        self.assertIn('max_capacity', states[-1]['metadata'])

    def test_a_vm_sampled_to_the_end_is_as_before(self):
        capture_end = T0 + 90 * DAY
        ev = events((0, {'entitlement': 2.0, 'virtual_cpus': 4, 'pool_id': 0, 'pool_cpu': 120, 'serial_number': 'F1'}))
        self.assertEqual(S.build_vm_states(ev, capture_end, capture_end)[-1]['end_epoch'], capture_end)
        self.assertEqual(S.build_vm_states(ev, capture_end, capture_end + 30)[-1]['end_epoch'], capture_end)     # never past the capture
        self.assertEqual(S.build_vm_states(ev, capture_end)[-1]['end_epoch'], capture_end)                       # no sample: not known
        self.assertEqual(S.build_vm_states([], capture_end, capture_end), [])

    def test_a_state_never_ends_before_it_starts(self):
        capture_end = T0 + 90 * DAY
        states = S.build_vm_states(events((0, {'entitlement': 2.0}), (50, {'entitlement': 3.0})), capture_end, T0 + 40 * DAY)
        self.assertEqual((states[-1]['start_epoch'], states[-1]['end_epoch']), (T0 + 50 * DAY, T0 + 50 * DAY))

    def test_the_last_sample_of_each_vm_over_all_metrics(self):
        physc, runq = S.AcquisitionStats(), S.AcquisitionStats()
        physc.last_ns_by_vm = {'a': (T0 + 5) * S.NS_PER_SECOND + 7, 'b': (T0 + 9) * S.NS_PER_SECOND}
        runq.last_ns_by_vm = {'a': (T0 + 8) * S.NS_PER_SECOND, 'c': (T0 + 1) * S.NS_PER_SECOND}
        self.assertEqual(S.last_sample_epochs({'physc': physc, 'runq': runq, 'merge': {'ambiguous_keys': 0}}),
                         {'a': T0 + 8, 'b': T0 + 9, 'c': T0 + 1})
        whole = S.AcquisitionStats()
        whole.merge(physc)
        whole.merge(runq)
        self.assertEqual(whole.last_ns_by_vm['a'], (T0 + 8) * S.NS_PER_SECOND)                                    # slices merge by the later sample

    def test_an_append_does_not_extend_a_vm_without_new_samples(self):
        meta = {'entitlement': 2.0, 'serial_number': 'F1'}
        existing = {'gone': [{'start_epoch': T0, 'end_epoch': T0 + 42 * DAY, 'metadata': dict(meta)}],
                    'here': [{'start_epoch': T0, 'end_epoch': T0 + 90 * DAY, 'metadata': dict(meta)}]}
        # The append run: a carried-forward configuration value gives 'gone' a state, but it has no sample.
        incoming = {'gone': [{'start_epoch': T0 + 90 * DAY, 'end_epoch': T0 + 91 * DAY, 'metadata': dict(meta)}],
                    'here': [{'start_epoch': T0 + 90 * DAY, 'end_epoch': T0 + 91 * DAY, 'metadata': dict(meta)}]}
        sampled = {'here': T0 + 91 * DAY}
        merged = S._merge_config_states(existing, {vm: st for vm, st in incoming.items() if vm in sampled})
        self.assertEqual(merged['gone'], existing['gone'])
        self.assertEqual(merged['here'][-1]['end_epoch'], T0 + 91 * DAY)


if __name__ == '__main__':
    unittest.main(verbosity=1)
