#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/vp_target.t
# PURPOSE  : The strategic VP target (v10 B10 correction), by sub-extraction of
#            _strategic_rvcpu_envelope. The usual VP:ENT range (up to 2 VPs per
#            processing unit of entitlement) is advisory: a completed width
#            witness (peak-throughput protection, configured burst) stands
#            above it; a run-queue width goes beyond it only where the existing
#            pressure rules admit it; a one-VP LPAR on a small entitlement is
#            valid. The helper takes the entitlement as given and returns VPs
#            only: no entitlement is raised or reduced for the range.
# USAGE    : perl vp_target.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
$src =~ /^(sub _strategic_rvcpu_envelope\s*\{.*?^\})/ms or die "could not extract _strategic_rvcpu_envelope\n";
my $code = $1;
my %const;
for my $name (qw(VCPU_RATIO_MIN VCPU_RATIO_MAX VCPU_MIN_ABS VCPU_MIN_FOR_ENT_GE_0_5_LT_1 VCPU_MICRO_PARTITION_THRESHOLD VCPU_MICRO_PARTITION_ENV_MAX_FLOOR
                 MICRO_PARTITION_RUNQ_OVERRIDE_ENT_THRESH MICRO_PARTITION_RUNQ_OVERRIDE_CRED_THRESH MICRO_PARTITION_RUNQ_MAX_VP_CAP))
{
    ($const{$name}) = $src =~ /^my \$\Q$name\E\s*=\s*([0-9.]+);/m or die "$name not found\n";
}
eval "package UUT; use Scalar::Util qw(looks_like_number);\n" . join('', map { "our \$$_ = $const{$_};\n" } sort keys %const) . "$code\n1;\n"
    or die "harness eval failed: $@\n";

my $vp = sub
{
    my (%in) = @_;
    return UUT::_strategic_rvcpu_envelope({ rent_p => $in{ent}, rent_for_envelope => $in{ent}, rvcpu_raw => ($in{runq} // 1), p99w1_floor_vcpu => $in{peak},
        configured_burst_floor_vcpu => $in{burst}, runnable_density => ($in{density} // 0.5), runq_hi_threads => (($in{runq} // 1) * 8), smt => 8,
        runq_potential => $in{potential}, is_runq_pressure => ($in{pressure} ? 'True' : 'False'), is_workload_pressure => 'False',
        no_widening => $in{no_widening} });
};

# --- A completed width witness stands above the usual range --------------------------
my $r = $vp->(ent => 0.61, peak => 5);
is_deeply([ $r->{rvcpu_final}, $r->{rvcpu_max} ], [ 5, 2 ], '0.61 entitlement, peak-throughput protection 5 VPs: the target is 5; the usual range maximum (2) is reported, not applied');
is($vp->(ent => 1.20, peak => 5)->{rvcpu_final}, 5, 'the same at 1.20: no rule for one entitlement band');
is($vp->(ent => 3.20, peak => 9)->{rvcpu_final}, 9, 'and at 3.20 (usual maximum 7)');
is($vp->(ent => 0.61, burst => 4)->{rvcpu_final}, 4, 'the configured-burst floor stands above the range too');
is($vp->(ent => 0.61, peak => 5, burst => 3)->{rvcpu_final}, 5, 'the larger of the two witnesses');
is($vp->(ent => 0.61, peak => 5, no_widening => 1)->{rvcpu_final}, 5, 'the witness stands in the width-reference sizing as well');
is($vp->(ent => 3.20, peak => 2)->{rvcpu_final}, 4, 'a witness below the VPs that carry the entitlement changes nothing: 4 VPs for 3.20');

# --- A run-queue width is not a completed witness: existing qualification only -------
is($vp->(ent => 2.00, runq => 6)->{rvcpu_final}, 4, 'a run-queue width of 6 without pressure stays within the usual range (4 VPs for 2.00)');
is($vp->(ent => 2.00, runq => 6, pressure => 1, density => 2.0)->{rvcpu_final}, 4, 'with pressure at 2.00 entitlement it stays there too: the admission rule is unchanged by this correction');
is($vp->(ent => 0.61, runq => 6, peak => 5)->{rvcpu_final}, 5, 'beside a peak witness of 5, an unadmitted run-queue width of 6 does not add a sixth VP');
is($vp->(ent => 0.30, runq => 4, pressure => 1, density => 2.0)->{rvcpu_final}, 4, 'the existing admission (small entitlement, dense queue, pressure) still gives 4');
is($vp->(ent => 0.30, runq => 4, pressure => 1, density => 2.0, peak => 5)->{rvcpu_final}, 5, 'and a peak witness above it still stands');
is($vp->(ent => 0.30, runq => 4, pressure => 1, density => 2.0, potential => 'ANOMALY (DBW)')->{rvcpu_final}, 2, 'a queue anomaly is not admitted beyond the two-VP allowance of a small entitlement');

# --- One VP on a small entitlement is a valid configuration ---------------------------
is($vp->(ent => 0.10, peak => 1)->{rvcpu_final}, 1, '0.10 entitlement: 1 VP (10 VPs per processing unit is no reason for a second)');
is($vp->(ent => 0.30)->{rvcpu_final}, 1, '0.30 entitlement: 1 VP');
is($vp->(ent => 0.75, peak => 1)->{rvcpu_final}, 1, '0.75 entitlement: 1 VP');
is($vp->(ent => 0.05, peak => 1)->{rvcpu_final}, 1, 'the PowerVM minimum of 0.05 on 1 VP');

# --- VPs that carry the entitlement ----------------------------------------------------
is_deeply([ @{ $vp->(ent => 3.20) }{qw(rvcpu_final rvcpu_min)} ], [ 4, 4 ], '3.20 entitlement needs 4 VPs to be delivered');

# --- The helper sizes width only -------------------------------------------------------
is_deeply([ sort grep { /ent/i } keys %$r ], [], 'no entitlement is returned: the width is sized for the entitlement given, which nothing here changes');

done_testing();
