#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/structural_feasibility.t
# PURPOSE:   Regression cover for the structural feasibility checks written to
#            the rationale log: pool entitlement feasibility and entitlement /
#            VP consistency. The live subroutines are extracted from nfit-profile
#            and evaluated in an isolated package, so the assertions run against
#            production source, not a copy.
# USAGE:     perl test/structural_feasibility.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use Scalar::Util qw(looks_like_number);
use JSON;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;

my @names = qw(
    _assess_ent_vp_pair _assess_pool_entitlement_feasibility
    _structural_shown_value _structural_tactical_vp _describe_ent_vp_pair
    _log_structural_feasibility _resolve_vm_tier_pattern_policy
);
my $code = '';
for my $name (@names)
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}

# The production constants are read from the source as well, so a change to
# them is exercised here rather than masked by a copy.
my ($min_ent_per_vp) = $src =~ /^my \$MIN_ENT_PER_VP\s*=\s*([0-9.]+);/m or die "MIN_ENT_PER_VP not found\n";
my ($tolerance)      = $src =~ /^my \$STRUCTURAL_ENT_TOLERANCE\s*=\s*([0-9.]+);/m or die "tolerance not found\n";

my $harness = qq{
package UUT;
use Scalar::Util qw(looks_like_number);
our \$FLOAT_EPSILON = 1e-9;
our \$MIN_ENT_PER_VP = $min_ent_per_vp;
our \$STRUCTURAL_ENT_TOLERANCE = $tolerance;
our \$MANDATORY_PEAK_PROFILE_FOR_HINT = 'Peak_P-99W1';
our \$MISSION_CRITICAL_PROFILE_NAME = 'P-99W1';
} . $code . "\n1;\n";
eval $harness or die "harness eval failed: $@\n";

sub near
{
    my ($got, $want) = @_;
    return (defined $got && abs($got - $want) < 1e-6) ? 1 : 0;
}

# --- _assess_ent_vp_pair ------------------------------------------------------
my $p = UUT::_assess_ent_vp_pair({ proposed_ent => 1.05, tactical_vp => 1, current_vp => 1 });
is($p->{Status}, 'WIDTH_BELOW_ENT', 'ENT 1.05 on one tactical VP is a width mismatch');
is($p->{MinVPs}, 2, '  and needs two VPs');

is(UUT::_assess_ent_vp_pair({ proposed_ent => 1.00, tactical_vp => 1, current_vp => 1 })->{Status}, 'OK',
    'ENT 1.00 on one VP is deliverable (upper boundary)');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 1.004, tactical_vp => 1, current_vp => 1 })->{Status}, 'OK',
    'ENT shown as 1.00 in the CSV is judged as 1.00');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 1.006, tactical_vp => 1, current_vp => 1 })->{Status}, 'WIDTH_BELOW_ENT',
    'ENT shown as 1.01 needs a second VP');

is(UUT::_assess_ent_vp_pair({ proposed_ent => 0.05, tactical_vp => 1, current_vp => 1 })->{Status}, 'OK',
    'ENT 0.05 on one VP is deliverable (lower boundary)');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 0.15, tactical_vp => 3, current_vp => 3 })->{Status}, 'OK',
    'ENT 0.15 on three VPs is deliverable (floating-point boundary)');
$p = UUT::_assess_ent_vp_pair({ proposed_ent => 0.10, tactical_vp => 3, current_vp => 3 });
is($p->{Status}, 'ENT_BELOW_VP_MINIMUM', 'ENT 0.10 cannot carry three tactical VPs');
is($p->{MaxVPs}, 2, '  and carries at most two');
$p = UUT::_assess_ent_vp_pair({ proposed_ent => 0.03, tactical_vp => 1, current_vp => 1 });
is($p->{Status}, 'ENT_BELOW_VP_MINIMUM', 'ENT below the single-VP minimum is flagged');
is($p->{MaxVPs}, 0, '  with no VP count that can carry it');

is(UUT::_assess_ent_vp_pair({ proposed_ent => 2.50, tactical_vp => 3, current_vp => 2 })->{Status}, 'RAISE_VPS_FIRST',
    'valid tactical pair, too few current VPs: raise VPs first');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 0.20, tactical_vp => 2, current_vp => 8 })->{Status}, 'REDUCE_VPS_FIRST',
    'valid tactical pair, too many current VPs: reduce VPs first');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 2.50, tactical_vp => 2, current_vp => 2 })->{Status}, 'WIDTH_BELOW_ENT',
    'a tactical mismatch takes precedence over the order note');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 2.50, tactical_vp => undef, current_vp => 2 })->{Status}, 'RAISE_VPS_FIRST',
    'without a tactical value the current VPs are still judged');

is(UUT::_assess_ent_vp_pair({ proposed_ent => undef, tactical_vp => 2, current_vp => 2 })->{Status}, 'UNKNOWN', 'no entitlement: unknown');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 'n/a', tactical_vp => 2, current_vp => 2 })->{Status}, 'UNKNOWN', 'non-numeric entitlement: unknown');
is(UUT::_assess_ent_vp_pair({ proposed_ent => 1.5 })->{Status}, 'UNKNOWN', 'no VP count at all: unknown');
is(UUT::_assess_ent_vp_pair(undef)->{Status}, 'UNKNOWN', 'no input: unknown');

# --- _assess_pool_entitlement_feasibility ----------------------------------------
# Four members with 6.50 installed in a 7-core pool: 7.75 proposed, and 7.32
# even at each member's lowest profile.
my @pool25 = (
    { vm => 'lparA', pool_id => 25, pool_cpu => 7, current_ent => 2.5, proposed_ent => 2.60, lowest_ent => 2.50, planning_profile => 'G3-95W15', tier_source => 'DEFAULT_TIER_3' },
    { vm => 'lparB', pool_id => 25, pool_cpu => 7, current_ent => 2.0, proposed_ent => 2.83, lowest_ent => 2.70, planning_profile => 'G3-95W15', tier_source => 'DEFAULT_TIER_3' },
    { vm => 'lparC', pool_id => 25, pool_cpu => 7, current_ent => 1.5, proposed_ent => 1.72, lowest_ent => 1.60, planning_profile => 'G1-99W5',  tier_source => 'CONFIGURED_TIER' },
    { vm => 'lparD', pool_id => 25, pool_cpu => 7, current_ent => 0.5, proposed_ent => 0.60, lowest_ent => 0.52, planning_profile => 'G3-95W15', tier_source => 'DEFAULT_TIER_3' },
);
my $pools = UUT::_assess_pool_entitlement_feasibility(\@pool25);
is(scalar(@$pools), 1, 'one user-defined pool assessed');
my $pool = $pools->[0];
is($pool->{Status}, 'SHORTFALL', 'proposals above the ceiling are a shortfall');
ok(near($pool->{InstalledSum}, 6.50), '  installed sum 6.50');
ok(near($pool->{ProposedSum}, 7.75), '  proposed sum 7.75');
ok(near($pool->{Shortfall}, 0.75), '  shortfall 0.75');
ok(near($pool->{LowestSum}, 7.32), '  lowest sum across profiles 7.32');
ok(near($pool->{LowestShortfall}, 0.32), '  which still leaves 0.32 short');
is($pool->{ConfiguredTierCount}, 1, '  one member on a configured TIER');
is($pool->{InstalledExceedsCeiling}, 0, '  installed entitlements are within the ceiling');
my ($row_b) = grep { $_->{Vm} eq 'lparB' } @{ $pool->{Members} };
ok(near($row_b->{Change}, 0.83) && near($row_b->{SumIfAlone}, 7.33) && $row_b->{FitsAlone} == 0,
    '  the +0.83 increase does not fit alone (7.33)');
my ($row_d) = grep { $_->{Vm} eq 'lparD' } @{ $pool->{Members} };
ok(near($row_d->{SumIfAlone}, 6.60) && $row_d->{FitsAlone} == 1, '  the +0.10 increase fits alone (6.60)');

# Fits, default pool ignored, missing pool ignored, pools ordered numerically.
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'x1', pool_id => 34, pool_cpu => 6, current_ent => 2.5, proposed_ent => 2.0, lowest_ent => 1.5 },
    { vm => 'x2', pool_id => 34, pool_cpu => 6, current_ent => 2.5, proposed_ent => 3.0, lowest_ent => 2.2 },
    { vm => 'd1', pool_id => 0,  pool_cpu => 64, current_ent => 40, proposed_ent => 90 },
    { vm => 'n1', pool_id => undef, pool_cpu => 4, current_ent => 4, proposed_ent => 9 },
    { vm => 'y1', pool_id => 7,  pool_cpu => 3, current_ent => 1.0, proposed_ent => 1.2 },
]);
is(join(',', map { $_->{PoolId} } @$pools), '7,34', 'default and unidentified pools are not assessed; order is numeric');
is($pools->[1]{Status}, 'FITS', 'a sum within the ceiling fits');
ok(near($pools->[1]{ProposedSum}, 5.0) && $pools->[1]{Shortfall} == 0 && $pools->[1]{LowestShortfall} == 0, '  with no shortfall');
ok(!defined $pools->[1]{Members}[0]{FitsAlone}, '  a reduction carries no fits-alone verdict');

# Tolerance: values are compared as shown in the CSV.
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'a', pool_id => 9, pool_cpu => 7, current_ent => 3.5, proposed_ent => 3.502 },
    { vm => 'b', pool_id => 9, pool_cpu => 7, current_ent => 3.5, proposed_ent => 3.502 },
]);
is($pools->[0]{Status}, 'FITS', 'a sum that shows as the ceiling fits');
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'a', pool_id => 9, pool_cpu => 7, current_ent => 3.5, proposed_ent => 3.51 },
    { vm => 'b', pool_id => 9, pool_cpu => 7, current_ent => 3.5, proposed_ent => 3.50 },
]);
ok($pools->[0]{Status} eq 'SHORTFALL' && near($pools->[0]{Shortfall}, 0.01), 'one hundredth above the ceiling is a shortfall');

# Ceiling evidence.
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'a', pool_id => 5, pool_cpu => 5, current_ent => 2, proposed_ent => 4 },
    { vm => 'b', pool_id => 5, pool_cpu => 7, current_ent => 2, proposed_ent => 4 },
]);
is($pools->[0]{Status}, 'CEILING_CONFLICT', 'members reporting different pool sizes give no verdict');
is(join('/', @{ $pools->[0]{CeilingValues} }), '5/7', '  and both sizes are reported');
$pools = UUT::_assess_pool_entitlement_feasibility([{ vm => 'a', pool_id => 5, pool_cpu => undef, current_ent => 2, proposed_ent => 4 }]);
is($pools->[0]{Status}, 'CEILING_UNAVAILABLE', 'no reported pool size gives no verdict');

# A member without a proposal keeps its entitlement; a missing entitlement is declared.
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'a', pool_id => 5, pool_cpu => 4, current_ent => 2.0, proposed_ent => undef },
    { vm => 'b', pool_id => 5, pool_cpu => 4, current_ent => 1.0, proposed_ent => 2.5 },
]);
ok($pools->[0]{Status} eq 'SHORTFALL' && near($pools->[0]{ProposedSum}, 4.5), 'a member without a proposal keeps its installed entitlement');
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'a', pool_id => 5, pool_cpu => 4, current_ent => undef, proposed_ent => 1.0 },
    { vm => 'b', pool_id => 5, pool_cpu => 4, current_ent => 1.0, proposed_ent => 2.0 },
]);
ok($pools->[0]{InstalledComplete} == 0 && !defined $pools->[0]{Members}[1]{FitsAlone},
    'an unknown installed entitlement withholds the fits-alone verdict');
$pools = UUT::_assess_pool_entitlement_feasibility([
    { vm => 'a', pool_id => 5, pool_cpu => 3, current_ent => 2.0, proposed_ent => 1.0 },
    { vm => 'b', pool_id => 5, pool_cpu => 3, current_ent => 2.0, proposed_ent => 1.0 },
]);
ok($pools->[0]{Status} eq 'FITS' && $pools->[0]{InstalledExceedsCeiling} == 1,
    'installed entitlements above the reported ceiling are flagged for verification');
is(scalar(@{ UUT::_assess_pool_entitlement_feasibility(undef) }), 0, 'no members: no pools');

# --- _log_structural_feasibility -------------------------------------------------
my @profiles = map { { name => $_, csv_output => 1 } }
    qw(Peak_P-99W1 P-99W1 G1-99W5 G2-98W10 G3-95W15 G4-90W15 O3-95W15 B3-95W15);
my %tiers = (lparC => 'G1');
my %map = (
    lparA => { Configuration => { entitlement => 2.5, virtual_cpus => 4, pool_id => 25, pool_cpu => 7 },
               Hinting => { AutoTier => 'G2' },
               CoreResults => { ProfileValues => { 'Peak_P-99W1' => 9, 'P-99W1' => 3.4, 'G1-99W5' => 3.0, 'G2-98W10' => 2.8, 'G3-95W15' => 2.60, 'G4-90W15' => 2.50 },
                                Profile_rVCPU_Tactical => { 'P-99W1' => 4, 'G1-99W5' => 4, 'G2-98W10' => 4, 'G3-95W15' => 4, 'G4-90W15' => 4 } } },
    lparB => { Configuration => { entitlement => 2.0, virtual_cpus => 4, pool_id => 25, pool_cpu => 7 },
               Hinting => { AutoTier => 'G3' },
               CoreResults => { ProfileValues => { 'G3-95W15' => 2.83, 'G4-90W15' => 2.70 },
                                Profile_rVCPU_Tactical => { 'G3-95W15' => 4, 'G4-90W15' => 4 } } },
    lparC => { Configuration => { entitlement => 1.5, virtual_cpus => 3, pool_id => 25, pool_cpu => 7 },
               Hinting => { AutoTier => 'O2' },
               CoreResults => { ProfileValues => { 'G1-99W5' => 1.72, 'G3-95W15' => 1.60 },
                                Profile_rVCPU_Tactical => { 'G1-99W5' => 3, 'G3-95W15' => 3 } } },
    lparD => { Configuration => { entitlement => 0.5, virtual_cpus => 1, pool_id => 25, pool_cpu => 7 },
               Hinting => { AutoTier => 'G3' },
               CoreResults => { ProfileValues => { 'P-99W1' => 1.96, 'G3-95W15' => 1.05, 'G4-90W15' => 0.52 },
                                Profile_rVCPU => { 'P-99W1' => 2, 'G3-95W15' => 2, 'G4-90W15' => 1 },
                                Profile_rVCPU_Tactical => { 'P-99W1' => 1, 'G3-95W15' => 1, 'G4-90W15' => 1 } } },
    lparE => { Configuration => { entitlement => 4.0, virtual_cpus => 16, pool_id => 0, pool_cpu => 64 },
               Hinting => { AutoTier => 'G3' },
               CoreResults => { ProfileValues => { 'G3-95W15' => 0.60 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 4 } } },
);
my @order = qw(lparA lparB lparC lparD lparE);
my $canon = JSON->new->canonical(1);
my $before = $canon->encode(\%map);

my $buffer = '';
open my $log, '>', \$buffer or die "cannot open in-memory log: $!";
my $summary = UUT::_log_structural_feasibility($log, \%map, \@order, \@profiles, \%tiers);
close $log;

is($canon->encode(\%map), $before, 'the assimilation map is left exactly as it was');
is($summary->{PoolsAssessed}, 1, 'one pool assessed');
is($summary->{PoolShortfalls}, 1, 'one pool shortfall counted');
is($summary->{WidthMismatches}, 1, 'one LPAR with a width mismatch on its planning profile');
is($summary->{OrderNotes}, 1, 'one LPAR with an order-of-change note on its planning profile');

like($buffer, qr/^Structural Feasibility Checks$/m, 'section heading written');
like($buffer, qr/Pool 25 - SHORTFALL/, 'pool verdict written');
like($buffer, qr/Pool ceiling \(Pool Size\)\s+: 7\.00 cores/, 'ceiling written');
like($buffer, qr/LPARs analysed in the pool\s+: 4 \(1 with a configured TIER, 3 on default tier 3\)/, 'tier mix written');
like($buffer, qr/Installed entitlement \(sum\)\s+: 6\.50 cores \(0\.50 below the ceiling\)/, 'installed sum written');
like($buffer, qr/Proposed entitlement \(sum\)\s+: 8\.20 cores/, 'proposed sum: 2.60 + 2.83 + 1.72 (configured G1) + 1.05');
like($buffer, qr/Shortfall\s+: 1\.20 cores/, 'shortfall written');
like($buffer, qr/Lowest sum across all profiles : 7\.32 cores \(a shortfall of 0\.32 remains/, 'lowest-profile sum written');
like($buffer, qr/lparC\s+G1-99W5\s+1\.50\s+1\.72\s+\+0\.22\s+Yes/, 'configured TIER selects the planning profile');
like($buffer, qr/lparB\s+G3-95W15 \(default\)\s+2\.00\s+2\.83\s+\+0\.83\s+No \(7\.33\)/, 'default tier 3 is labelled; fits-alone verdict written');
unlike($buffer, qr/Pool 0 /, 'the default pool is not assessed');
like($buffer, qr/lparD \(planning profile G3-95W15\)\n\s+- ENTITLEMENT\/WIDTH MISMATCH: proposed ENT 1\.05 needs at least 2 VPs; the tactical VP recommendation is 1 \(current 1, strategic rVCPU 2\)\.\n\s+Raise VPs to 2 or more before the entitlement can be applied\./,
    'width mismatch written for the planning profile');
like($buffer, qr/lparD.*\n.*\n.*\n\s+- Other profiles \(alternative plans\): entitlement\/width mismatch in P-99W1$/m,
    'the same finding in another profile is listed by name');
like($buffer, qr/lparE \(planning profile G3-95W15\)\n\s+- ORDER OF CHANGE: proposed ENT 0\.60 carries at most 12 VPs and the LPAR has 16\.\n\s+Reduce VPs \(tactical recommendation 4\) before the entitlement\./,
    'order of change written when only the current VPs are in the way');
unlike($buffer, qr/^    lparA \(planning/m, 'a consistent LPAR is not listed');

# Further renderings: a pool that fits, an unconfirmed ceiling, a shortfall
# that depends on the TIER selection, and an entitlement below the VP minimum.
my %map2 = (
    f1 => { Configuration => { entitlement => 2.5, virtual_cpus => 5, pool_id => 34, pool_cpu => 6 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 2.0 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 4 } } },
    f2 => { Configuration => { entitlement => 2.5, virtual_cpus => 5, pool_id => 34, pool_cpu => 6 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 3.0 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 5 } } },
    c1 => { Configuration => { entitlement => 1.0, virtual_cpus => 2, pool_id => 12, pool_cpu => 5 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 1.0 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 2 } } },
    c2 => { Configuration => { entitlement => 1.0, virtual_cpus => 2, pool_id => 12, pool_cpu => 7 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 1.0 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 2 } } },
    t1 => { Configuration => { entitlement => 1.0, virtual_cpus => 2, pool_id => 40, pool_cpu => 2 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 1.5, 'G4-90W15' => 0.9 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 2, 'G4-90W15' => 2 } } },
    t2 => { Configuration => { entitlement => 1.0, virtual_cpus => 2, pool_id => 40, pool_cpu => 2 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 1.5, 'G4-90W15' => 0.9 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 2, 'G4-90W15' => 2 } } },
    w1 => { Configuration => { entitlement => 4.0, virtual_cpus => 16, pool_id => 0, pool_cpu => 64 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 0.47 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 12 } } },
);
$buffer = '';
open $log, '>', \$buffer or die;
$summary = UUT::_log_structural_feasibility($log, \%map2, [qw(f1 f2 c1 c2 t1 t2 w1)], \@profiles, {});
close $log;
is($summary->{PoolsAssessed}, 3, 'three pools assessed');
is($summary->{PoolShortfalls}, 1, 'only the pool above its ceiling is a shortfall');
like($buffer, qr/Pool 34 - FITS \(among the LPARs analysed\)\n.*: 6\.00 cores\n.*\n.*: 5\.00 cores \(1\.00 below the ceiling\)\n.*: 5\.00 cores\n\n/,
    'a pool that fits is reported with its sums and no member table');
like($buffer, qr/Pool 12 - CEILING UNCONFIRMED\n\s+- Pool ceiling \(Pool Size\)\s+: members report different sizes \(5\.00, 7\.00\); no verdict is given/,
    'conflicting pool sizes are reported without a verdict');
like($buffer, qr/Pool 40 - SHORTFALL.*?Shortfall\s+: 1\.00 cores\n\s+- Lowest sum across all profiles : 1\.80 cores \(within the ceiling: the shortfall depends on the TIER selection\)/s,
    'a shortfall that another TIER selection avoids is described as such');
like($buffer, qr/w1 \(planning profile G3-95W15\)\n\s+- ENTITLEMENT\/WIDTH MISMATCH: proposed ENT 0\.47 carries at most 9 VPs \(0\.05 cores per VP minimum\); the tactical VP recommendation is 12 \(current 16\)\.\n\s+Reduce VPs to 9 or fewer first, or hold the entitlement at 0\.60 or more\./,
    'an entitlement below the minimum for the tactical VPs is a mismatch, with the entitlement that would hold');
is($summary->{WidthMismatches}, 1, '  and is counted');

# No log handle: the summary is still returned and nothing is written.
$summary = UUT::_log_structural_feasibility(undef, \%map, \@order, \@profiles, \%tiers);
is($summary->{PoolShortfalls}, 1, 'summary returned without a log handle');

# Nothing to report.
$buffer = '';
open $log, '>', \$buffer or die;
$summary = UUT::_log_structural_feasibility($log, { solo => { Configuration => { entitlement => 1, virtual_cpus => 2, pool_id => 0, pool_cpu => 16 },
    Hinting => {}, CoreResults => { ProfileValues => { 'G3-95W15' => 1.2 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 2 } } } }, ['solo'], \@profiles, {});
close $log;
like($buffer, qr/No LPAR in this analysis is in a user-defined pool\./, 'no user-defined pool: stated');
like($buffer, qr/No inconsistency found\./, 'no inconsistency: stated');
ok($summary->{PoolShortfalls} == 0 && $summary->{WidthMismatches} == 0 && $summary->{OrderNotes} == 0, 'no findings counted');
is(UUT::_log_structural_feasibility(undef, {}, [], \@profiles, {})->{PoolsAssessed}, 0, 'empty analysis: nothing assessed');

done_testing();
