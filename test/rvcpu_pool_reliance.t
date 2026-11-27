#!/usr/bin/perl
# Regression cover for the event-time pool-reliance diagnostic.
# Extracts the live subroutines from nfit-profile.pl and evals them into an
# isolated package, so the assertions run against production source, not a copy.
use strict; use warnings;
use Scalar::Util qw(looks_like_number);
use List::Util qw(min max);

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
local $/; my $src = <$fh>; close $fh;

my @names = qw(
    _assess_pool_reliance _resolve_pool_evidence _resolve_dispatch_budget
    _classify_vcpu_reduction_safety _compute_entbounded_and_tactical _assert_budget_invariant
);
my $code = '';
for my $name (@names) {
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms
        or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}

# Globals the extracted subroutines reference (values here are test scaffolding
# for the pool-dependence flag, not the production thresholds).
my $harness = q{
package UUT;
use Scalar::Util qw(looks_like_number);
use List::Util qw(min max);
our $FLOAT_EPSILON = 1e-9;
our %DISPATCH_BUDGET_BY_TIER = (
    'P'=>0.05,'O1'=>0.15,'G1'=>0.15,'B1'=>0.15,'O2'=>0.35,'G2'=>0.35,'B2'=>0.35,
    'O3'=>0.60,'G3'=>0.60,'B3'=>0.60,'O4'=>1.00,'G4'=>1.00,'B4'=>1.00,
);
our $VCPU_REDUCTION_BLOCK_REACH_RATIO   = 0.95;
our $VCPU_REDUCTION_CAUTION_REACH_RATIO = 0.80;
our $MIN_MEANINGFUL_ADJUSTMENT_CORES    = 0.10;
our $VCPU_REDUCTION_CAUTION_PEAK_PAE    = 0.25;
our $VCPU_REDUCTION_CAUTION_PEAK_AAE    = 0.25;
our %VCPU_TIER_STAGE1_STEP_FRACTION   = (1 => 0.25, 2 => 0.35, 3 => 0.50, 4 => 0.50);
our $VCPU_TIER_STAGE1_STEP_MIN_VP     = 1;
our $VCPU_REDUCTION_CAUTION_MAX_STEP_VP = 1;
} . $code . "\n1;\n";
eval $harness or die "harness eval failed: $@\n";

my ($tests, $fail) = (0, 0);
sub ok { my ($c, $m) = @_; $tests++; $c ? print "ok  - $m\n"
                                        : do { $fail++; print "NOT OK - $m\n" }; }

# --- _assess_pool_reliance -------------------------------------------------
my $r = UUT::_assess_pool_reliance({applicable_demand=>9.2, base_planning_demand=>5.0,
        proposed_entitlement=>6.0, reachable=>12.0, is_capped=>0, dispatch_budget=>0.60});
ok($r->{PoolRelianceAssessment} eq 'WITHIN_ALLOWANCE', 'transient within-allowance');
ok($r->{PoolRelianceClass} eq 'TRANSIENT_EVENT',       'transient class (baseline <= ent)');
ok(abs($r->{PoolReliancePct} - 0.53) < 0.005,          'reliance ~53%');

$r = UUT::_assess_pool_reliance({applicable_demand=>9.2, base_planning_demand=>5.0,
        proposed_entitlement=>6.0, reachable=>12.0, is_capped=>0, dispatch_budget=>0.05});
ok($r->{PoolRelianceAssessment} eq 'EXCEEDS_ALLOWANCE', 'exceeds allowance at tier-P budget');

$r = UUT::_assess_pool_reliance({applicable_demand=>9.2, base_planning_demand=>7.5,
        proposed_entitlement=>6.0, reachable=>12.0, is_capped=>0, dispatch_budget=>0.60});
ok($r->{PoolRelianceClass} eq 'STRUCTURAL_BASELINE', 'structural class (baseline > ent)');

$r = UUT::_assess_pool_reliance({applicable_demand=>5.0, base_planning_demand=>4.0,
        proposed_entitlement=>6.0, reachable=>12.0, is_capped=>0, dispatch_budget=>0.60});
ok($r->{PoolRelianceClass} eq 'NONE' && $r->{PoolRelianceAssessment} eq 'WITHIN_ALLOWANCE',
   'no lean -> NONE / within');

$r = UUT::_assess_pool_reliance({applicable_demand=>9.2, base_planning_demand=>5.0,
        proposed_entitlement=>6.0, reachable=>6.0, is_capped=>1, dispatch_budget=>0.60});
ok($r->{PoolRelianceAssessment} eq 'NOT_APPLICABLE' && $r->{PoolRelianceClass} eq 'NONE',
   'capped -> not applicable');

$r = UUT::_assess_pool_reliance({applicable_demand=>13.0, base_planning_demand=>5.0,
        proposed_entitlement=>6.0, reachable=>12.0, is_capped=>0, dispatch_budget=>0.60});
ok($r->{PoolRelianceAssessment} eq 'INFEASIBLE_WIDTH', 'demand > reachable -> infeasible width');

$r = UUT::_assess_pool_reliance({proposed_entitlement=>6.0, reachable=>12.0,
        is_capped=>0, dispatch_budget=>0.60});
ok($r->{PoolRelianceAssessment} eq 'NOT_APPLICABLE', 'missing demand -> graceful N/A');

# --- point-2 precondition: pool-dependent downward candidate flags reliance --
# A downward Tactical carrying this flag is forced to at least CAUTION in
# _compute_entbounded_and_tactical (the CLEAR->CAUTION guard).
my $cls = UUT::_classify_vcpu_reduction_safety({
    evidence=>{}, current_vp=>8, current_entitlement=>4.0, pool_cpu=>16,
    is_capped=>0, peak_physc=>3.0, guarantee_demand=>6.0});
ok($cls->{depends_on_unmeasured_pool} == 1,
   'uncapped + unmeasured pool + demand > ent sets depends_on_unmeasured_pool');

my $cls2 = UUT::_classify_vcpu_reduction_safety({
    evidence=>{}, current_vp=>8, current_entitlement=>4.0, pool_cpu=>16,
    is_capped=>1, peak_physc=>3.0, guarantee_demand=>6.0});
ok($cls2->{depends_on_unmeasured_pool} == 0, 'capped LPAR does not depend on unmeasured pool');

# --- D-028: a guarantee check that fails only on the withheld pool-borrowing rule ----------
# The uplift of the tier's pool-borrowing rule is withheld where the pool was never contested; the
# check then reads N/A. A VP reduction needs the check positively proven, so without the exemption
# mark every such LPAR kept all its VPs, with no pressure evidence at all.
my %quiet = (current_vp => 7, current_entitlement => 6.00, pool_cpu => 32, is_capped => 0, peak_physc => 0.45, guarantee_demand => 0.45);
my $why = 'the pool-borrowing budget is the only condition not met, and it is not applied because no competition for the pool was observed';
my $held = UUT::_classify_vcpu_reduction_safety({ %quiet, evidence => { GuaranteeInvariantHolds => 'N/A' } });
my $free = UUT::_classify_vcpu_reduction_safety({ %quiet, evidence => { GuaranteeInvariantHolds => 'N/A', GuaranteeInvariantExempt => $why } });
ok($held->{state} eq 'CLEAR' && $held->{strategic_invariant_exempt} eq '', 'D-028: a check that did not run is not exempt');
ok($free->{state} eq 'CLEAR' && $free->{strategic_invariant_exempt} eq $why, 'D-028: the withheld pool-borrowing allowance is carried as exempt, with its reason');
my %step = (rvcpu_raw => 1, rvcpu_min => 1, rvcpu_max => 2, rvcpu_final => 1, p99w1_floor_vcpu => 1, rvcpu_tier_num => 1,
            vp_current_ent_floor => 6, current_ent_stale => 0, current_vp_numeric => 7, current_entitlement => 6.00,
            guarantee_demand => 0.45, is_capped => 0, pool_cpu => 32, dispatch_budget => 0.15, tier_label => 'G1');
my $t_held = UUT::_compute_entbounded_and_tactical({ %step, reduction_safety => $held });
my $t_free = UUT::_compute_entbounded_and_tactical({ %step, reduction_safety => $free });
ok($t_held->{tactical} == 7 && $t_held->{reduction_safety_state} eq 'BLOCK', 'D-028: a check that did not run still holds the VPs (fail-closed)');
ok($t_held->{reduction_safety_reasons} =~ /strategic guarantee invariant not positively proven \(N\/A\)/, 'D-028: and says why');
ok($t_free->{entbounded} == 6 && $t_free->{tactical} == 6 && $t_free->{reduction_safety_state} eq 'CLEAR',
   'D-028: the exempt check does not hold the VP step (7 to 6 at the tier rate)');
ok($t_free->{reduction_safety_reasons} !~ /not positively proven/, 'D-028: no guarantee reason is given as a block');
ok($t_free->{tactical_note} =~ /; guarantee check of the strategic pair exempt: \Q$why\E/, 'D-028: the reason for the exemption stays on the VP step');
ok($t_held->{tactical_note} !~ /exempt/, 'D-028: and is absent where nothing is exempt');
# The mark does not turn a failed check, or one that passed, into something else.
my $failed = UUT::_classify_vcpu_reduction_safety({ %quiet, evidence => { GuaranteeInvariantHolds => 'No', GuaranteeInvariantExempt => $why } });
ok($failed->{state} eq 'BLOCK' && $failed->{strategic_invariant_exempt} eq '', 'D-028: a check that failed outright blocks, whatever the mark');
my $t_failed = UUT::_compute_entbounded_and_tactical({ %step, reduction_safety => $failed });
ok($t_failed->{tactical} == 7, 'D-028: and the VPs stay');
# Other safety evidence is untouched by the exemption: a credible peak at the reachable capacity still blocks.
my $pressed = UUT::_classify_vcpu_reduction_safety({ %quiet, peak_physc => 6.9, evidence => { GuaranteeInvariantHolds => 'N/A', GuaranteeInvariantExempt => $why } });
my $t_pressed = UUT::_compute_entbounded_and_tactical({ %step, reduction_safety => $pressed });
ok($pressed->{state} eq 'BLOCK' && $t_pressed->{tactical} == 7, 'D-028: a peak at the reachable capacity blocks an exempt LPAR as any other');

print "\n$tests tests, $fail failures\n";
exit($fail ? 1 : 0);
