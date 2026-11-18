#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/structural_feasibility.t
# PURPOSE:   Regression cover for the structural feasibility checks written to
#            the rationale log and flagged in the CSV VM_Comments column: pool
#            entitlement feasibility and entitlement / VP consistency. The live
#            subroutines are extracted from nfit-profile
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
    _collect_structural_findings _structural_csv_tags
    _log_structural_feasibility _resolve_vm_tier_pattern_policy
    _pool_units_text _pool_shortfall_action _rationale_wrap _log_recommended_actions
    _structural_planning_profile _lpar_role_hint _entitlement_totals _log_entitlement_totals
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
our \$verbose = 0;
} . $code . "\n1;\n";
eval $harness or die "harness eval failed: $@\n";

sub flat
{
    (my $text = shift) =~ s/\s+/ /g;
    return $text;
}

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

like($buffer, qr/^Pool and VP Checks$/m, 'section heading written');
like($buffer, qr/Pool 25 - the proposed entitlements do not fit\n\s+8\.20 cores proposed against a pool maximum of 7 \(installed today 6\.50; 4 LPARs analysed\)\./,
    'pool verdict with proposed, maximum, installed and members (2.60 + 2.83 + 1.72 configured G1 + 1.05)');
like(flat($buffer), qr/Action: defer at least 1\.20 cores of the proposed increases, or raise the pool maximum from 7 to at least 9 processing units\./,
    'shortfall action: defer the shortfall, or a whole-unit pool maximum that holds the proposals');
like($buffer, qr/Even each LPAR's lowest profile totals 7\.32 cores, so no TIER choice fits within 7\./, 'lowest-profile sum written');
like($buffer, qr/lparC\s+G1-99W5\s+1\.50\s+1\.72\s+\+0\.22\s+Yes/, 'configured TIER selects the planning profile');
like($buffer, qr/lparB\s+G3-95W15 \(default\)\s+2\.00\s+2\.83\s+\+0\.83\s+No \(7\.33\)/, 'default tier 3 is labelled; fits-alone verdict written');
unlike($buffer, qr/Pool 0 /, 'the default pool is not assessed');
like(flat($buffer), qr/lparD \(G3-95W15\): The proposed 1\.05 cores needs at least 2 VPs \(entitlement cannot exceed the VP count\); the VP recommendation is 1 VP\. Action: Limit the entitlement to 1\.00 \(the most 1 VP can carry\), or set at least 2 VPs\./,
    'width mismatch on the planning profile, the supported option first');
like(flat($buffer), qr/lparE \(G3-95W15\): 16 VPs need at least 0\.80 cores of entitlement; the proposed 0\.60 cores can carry at most 12 VPs\. Action: Reduce VPs from 16 to 4 before lowering the entitlement to 0\.60\./,
    'order of change written when only the current VPs are in the way');
unlike($buffer, qr/Other profiles \(alternative plans\)/, 'alternative profiles are not itemised for an LPAR with a planning finding');
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
like($buffer, qr/Pool 34 - fits: 5\.00 cores proposed against a maximum of 6 \(installed today 5\.00; 2 LPARs analysed\)\.\n    Pool 40/,
    'a pool that fits is reported on one line with no member table');
like($buffer, qr/Pool 12 - no verdict: its LPARs report different maximums \(5, 7\)\.\n\s+2\.00 cores proposed .*Confirm the pool maximum on the HMC\./,
    'conflicting pool sizes are reported without a verdict');
like($buffer, qr/Pool 40 - the proposed entitlements do not fit.*?defer at least 1\.00 cores.*?to at least 3\s+processing units\.\n\s+A lower TIER choice would fit: the lowest profiles total 1\.80 cores\./s,
    'a shortfall that another TIER selection avoids is described as such');
like(flat($buffer), qr/w1 \(G3-95W15\): The proposed 0\.47 cores is too little for 12 VPs: PowerVM needs at least 0\.05 per VP, so 12 VPs need 0\.60\. Action: Reduce VPs from 16 to 12 \(the VP recommendation\) - minimum entitlement 0\.60\. If 16 VPs stay, the minimum is 0\.80\./,
    'below the VP minimum: the recommended VP count and its minimum, and the minimum if the VPs stay');
unlike($buffer, qr/Reduce VPs to 9/, 'no VP cut beyond the VP recommendation is proposed');
is($summary->{WidthMismatches}, 1, '  and is counted');

like($buffer, qr/PowerVM needs 0\.05 to 1\.00 cores of entitlement per VP\./, 'the per-VP range is stated');
like($buffer, qr/Reserved pool capacity and\n\s+LPARs outside this analysis are not counted/, 'what a fit leaves out is stated');

# --- _structural_csv_tags ---------------------------------------------------------
my $tags = UUT::_structural_csv_tags(UUT::_collect_structural_findings(\%map, \@order, \@profiles, \%tiers));
is_deeply($tags, {
    lparA => ['[POOL_SHORTFALL]'], lparB => ['[POOL_SHORTFALL]'], lparC => ['[POOL_SHORTFALL]'],
    lparD => ['[POOL_SHORTFALL]', '[VP_MISMATCH]'],
}, 'every member of the short pool is tagged; the mismatched LPAR carries both alerts; an order note is not an alert');
is($canon->encode(\%map), $before, 'collecting the findings leaves the assimilation map as it was');
$tags = UUT::_structural_csv_tags(UUT::_collect_structural_findings(\%map2, [qw(f1 f2 c1 c2 t1 t2 w1)], \@profiles, {}));
is_deeply($tags, { t1 => ['[POOL_SHORTFALL]'], t2 => ['[POOL_SHORTFALL]'], w1 => ['[VP_MISMATCH]'] },
    'a pool that fits or has an unconfirmed ceiling is not tagged; an entitlement below the VP minimum is');
is_deeply(UUT::_structural_csv_tags(UUT::_collect_structural_findings({}, [], \@profiles, {})), {}, 'empty analysis: no alerts');
is_deeply(UUT::_structural_csv_tags(undef), {}, 'no findings: no alerts');
# A finding on another profile only (planning profile consistent) is not an alert.
$tags = UUT::_structural_csv_tags(UUT::_collect_structural_findings(
    { alt => { Configuration => { entitlement => 0.5, virtual_cpus => 1, pool_id => 0, pool_cpu => 16 }, Hinting => {},
               CoreResults => { ProfileValues => { 'P-99W1' => 1.5, 'G3-95W15' => 0.8 },
                                Profile_rVCPU_Tactical => { 'P-99W1' => 1, 'G3-95W15' => 1 } } } }, ['alt'], \@profiles, {}));
is_deeply($tags, {}, 'a mismatch on an alternative profile alone is not a CSV alert');

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
like($buffer, qr/Every current-cycle pair meets this rule; NFIT - Action states the order of change\./, 'no inconsistency: stated');
ok($summary->{PoolShortfalls} == 0 && $summary->{WidthMismatches} == 0 && $summary->{OrderNotes} == 0, 'no findings counted');
is(UUT::_log_structural_feasibility(undef, {}, [], \@profiles, {})->{PoolsAssessed}, 0, 'empty analysis: nothing assessed');

# --- Minimum entitlement per VP: retained VPs and a single VP -----------------------
my ($f4, $a4) = UUT::_describe_ent_vp_pair(UUT::_assess_ent_vp_pair({ proposed_ent => 0.08, tactical_vp => 4, current_vp => 4 }), 1);
is($a4, 'Retain 4 VPs - minimum entitlement 0.20.', 'four VPs kept: the minimum is 0.05 per VP (0.20)');
my (undef, $a1) = UUT::_describe_ent_vp_pair(UUT::_assess_ent_vp_pair({ proposed_ent => 0.04, tactical_vp => 1, current_vp => 1 }), 1);
is($a1, 'Minimum configurable entitlement: 0.05.', 'one VP below the minimum: the minimum configurable entitlement');
my (undef, $a3) = UUT::_describe_ent_vp_pair(UUT::_assess_ent_vp_pair({ proposed_ent => 0.08, tactical_vp => 3, current_vp => 4 }), 1);
is($a3, 'Reduce VPs from 4 to 3 (the VP recommendation) - minimum entitlement 0.15. If 4 VPs stay, the minimum is 0.20.',
    'a VP cut only to the VP recommendation, with the minimum if the VPs stay');

# --- Recommended Actions ---------------------------------------------------------
my $structural = UUT::_log_structural_feasibility(undef, \%map, \@order, \@profiles, \%tiers);
$buffer = '';
open $log, '>', \$buffer or die;
my $n_actions = UUT::_log_recommended_actions($log, $structural, {
    Status => 'evaluated', AdvisedCuts => [
        { vm => 'x1', pool => 44, installed => 0.50, proposed => 0.12, competition => 416, full => 423 },
        { vm => 'x2', pool => 44, installed => 0.50, proposed => 0.11, competition => 398, full => 423 },
        { vm => 'x3', pool => 46, installed => 1.00, proposed => 0.42, competition => 163, full => 390 } ],
    PoolFull => { 0 => 9, 24 => 312, 44 => 423 }, FrameLow => 0 });
close $log;
is($n_actions, 5, 'actions: one pool shortfall, two VP findings, two cut reviews');
like(flat($buffer), qr/Recommended Actions \(planning profile of each LPAR\)/, 'section heading written');
like(flat($buffer), qr/1\. Pool 25: Defer at least 1\.20 cores of the proposed increases, or raise the pool maximum from 7 to at least 9 processing units\./,
    'the pool shortfall leads the list');
like(flat($buffer), qr/lparD: Limit the entitlement to 1\.00/, 'the width finding is an action');
like(flat($buffer), qr/Pool 44: review these entitlement cuts together before applying them - x1 0\.50 to 0\.12 \(416 of 423 full-pool minutes\); x2 0\.50 to 0\.11 \(398 of 423/,
    'cuts in one pool are reviewed together');
like(flat($buffer), qr/x3 \(pool 46\): review the cut from 1\.00 to 0\.42 before applying it - it removes the guarantee in 163 of the 390 minutes/,
    'a single cut is reviewed with its count and denominator');
like(flat($buffer), qr/No additional action identified by the checks run\./, 'the list is closed');
like(flat($buffer), qr/Observed \(no action\): - Pool 24 was at its maximum in 312 minutes of the period; no entitlement cut in it needs review\./,
    'a pool at its maximum without a cut is an observation, not an action');
unlike($buffer, qr/Pool 0 was at its maximum/, 'the default pool is not reported as a pool at its maximum');
$buffer = '';
open $log, '>', \$buffer or die;
is(UUT::_log_recommended_actions($log, { Findings => { Valid => 1, Pools => [], VmFindings => [] } }, undef), 0, 'nothing to do: no actions');
close $log;
like(flat($buffer), qr/No action identified by the checks run\. Pool competition for entitlement cuts was not checked \(add --pool-exposure\)\./,
    'without --pool-exposure the skipped check is named');

# v10 stage 3.1: with the current-cycle pair resolved, the check reads that
# pair. The target (0.47) against the Tactical step (12 VPs) is not a finding
# when the pair the CSV shows (1.50 entitlement on 12 VPs) can be configured;
# an invalid pair would still be.
my %map3 = (
    w2 => { Configuration => { entitlement => 4.0, virtual_cpus => 16, pool_id => 0, pool_cpu => 64 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 0.47 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 12 },
                             Profile_CurrentCycle => { 'G3-95W15' => { Ent => 1.50, VP => 12, Status => 'STAGED' } } } },
    w3 => { Configuration => { entitlement => 4.0, virtual_cpus => 16, pool_id => 0, pool_cpu => 64 }, Hinting => {},
            CoreResults => { ProfileValues => { 'G3-95W15' => 0.47 }, Profile_rVCPU_Tactical => { 'G3-95W15' => 12 },
                             Profile_CurrentCycle => { 'G3-95W15' => { Ent => 0.47, VP => 12, Status => 'STAGED' } } } },
);
my $f3 = UUT::_collect_structural_findings(\%map3, [qw(w2 w3)], \@profiles, {});
is($f3->{Summary}{WidthMismatches}, 1, 'current-cycle pair: only the invalid pair is counted');
is_deeply([ map { $_->{vm} } @{ $f3->{VmFindings} } ], ['w3'], 'current-cycle pair: the valid pair is not listed');

# --- Entitlement Totals (v10 stage 3.1) -------------------------------------------
is(UUT::_lpar_role_hint('VIO Server'), 'vios', 'role: VIO Server is a VIOS');
is(UUT::_lpar_role_hint('VIOS'), 'vios', 'role: VIOS');
is(UUT::_lpar_role_hint('PowerHA Standby'), 'standby', 'role: PowerHA standby');
is(UUT::_lpar_role_hint('AIX Production'), '', 'role: any other LPAR');
is(UUT::_lpar_role_hint(undef), '', 'role: unknown SystemType');

my %map4 = (
    a1 => { Configuration => { entitlement => 2.00 }, Hinting => { ResolvedPlanningProfile => 'G3-95W15' },
            CoreResults => { ProfileValues => { 'G3-95W15' => 1.50 }, Profile_CurrentCycle => { 'G3-95W15' => { Ent => 1.50, VP => 2, Status => 'OK' } } } },
    a2 => { Configuration => { entitlement => 0.50 }, Hinting => { ResolvedPlanningProfile => 'G3-95W15' },
            CoreResults => { ProfileValues => { 'G3-95W15' => 0.30 }, Profile_CurrentCycle => { 'G3-95W15' => { Ent => 0.30, VP => 1, Status => 'OK' } } } },
    a3 => { Configuration => { entitlement => 1.00 }, Hinting => { ResolvedPlanningProfile => 'P-99W1' },
            CoreResults => { ProfileValues => { 'P-99W1' => 1.80 }, Profile_CurrentCycle => { 'P-99W1' => { Ent => 1.20, VP => 2, Status => 'STAGED' } } } },
    a4 => { Configuration => { entitlement => 0.75 }, Hinting => { ResolvedPlanningProfile => 'G3-95W15' }, CoreResults => { ProfileValues => {} } },
    a5 => { Configuration => {}, Hinting => { ResolvedPlanningProfile => 'G3-95W15' }, CoreResults => {} },
);
my %roles4 = (a2 => 'standby', a3 => 'vios');
my $before4 = $canon->encode(\%map4);
my $t4 = UUT::_entitlement_totals(\%map4, [qw(a1 a2 a3 a4 a5)], \@profiles, {}, \%roles4);
is($canon->encode(\%map4), $before4, 'totals: the assimilation map is left exactly as it was');
my $g4 = $t4->{Groups};
is_deeply([ @{ $g4->{All} }{qw(Count Installed CurrentCycle Target)} ], [4, 4.25, 3.75, 4.35],
    'totals: all LPARs (the LPAR without a result at its installed entitlement)');
is_deeply([ @{ $g4->{ExclStandby} }{qw(Count Installed CurrentCycle Target)} ], [3, 3.75, 3.45, 4.05], 'totals: excluding standbys');
is_deeply([ @{ $g4->{ExclStandbyVios} }{qw(Count Installed CurrentCycle Target)} ], [2, 2.75, 2.25, 2.25], 'totals: excluding standbys and VIOS');
is_deeply([ @{ $g4->{Standby} }{qw(Count Installed CurrentCycle Target)} ], [1, 0.5, 0.3, 0.3], 'totals: standbys');
is_deeply([ @{ $g4->{Vios} }{qw(Count Installed CurrentCycle Target)} ], [1, 1, 1.2, 1.8], 'totals: VIOS (current cycle staged towards the target)');
is($t4->{Held}, 1, 'totals: one LPAR counted at installed');
is($t4->{NoInstalled}, 1, 'totals: one LPAR without an installed entitlement left out');

$buffer = '';
open $log, '>', \$buffer or die;
UUT::_log_entitlement_totals($log, \%map4, [qw(a1 a2 a3 a4 a5)], \@profiles, {}, \%roles4);
close $log;
like($buffer, qr/^Entitlement Totals$/m, 'totals: section heading written');
like($buffer, qr/^    All LPARs\s+4\s+4\.25\s+3\.75\s+4\.35$/m, 'totals: all LPARs row');
like($buffer, qr/^    Excluding standbys\s+3\s+3\.75\s+3\.45\s+4\.05$/m, 'totals: excluding standbys row');
like($buffer, qr/^    Excluding standbys and VIOS\s+2\s+2\.75\s+2\.25\s+2\.25$/m, 'totals: excluding standbys and VIOS row');
like($buffer, qr/^      Standbys\s+1\s+0\.50\s+0\.30\s+0\.30$/m, 'totals: standby subtotal');
like($buffer, qr/^      VIOS\s+1\s+1\.00\s+1\.20\s+1\.80$/m, 'totals: VIOS subtotal');
like(flat($buffer), qr/1 LPAR without a planning-profile result is counted at the installed entitlement/, 'totals: held LPAR named');
like(flat($buffer), qr/1 LPAR without an installed entitlement is left out\./, 'totals: unknown installed named');
like(flat($buffer), qr/The standby-share sensitivity follows once nfit\.vms\.cfg names each standby's primary \(ha_peer\)\./,
    'totals: standby-share sensitivity deferred to ha_peer');

$buffer = '';
open $log, '>', \$buffer or die;
UUT::_log_entitlement_totals($log, \%map4, [qw(a1 a2 a3 a4 a5)], \@profiles, {}, undef);
close $log;
unlike($buffer, qr/Excluding/, 'totals: without the VM configuration, standbys and VIOS are not separated');
like($buffer, qr/Standbys and VIOS are not separated: the VM configuration file was not loaded\./, 'totals: reason stated');
is(UUT::_log_entitlement_totals(undef, {}, [], \@profiles, {}, undef)->{Groups}{All}{Count}, 0, 'totals: empty analysis');

done_testing();
