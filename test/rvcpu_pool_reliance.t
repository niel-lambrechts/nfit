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
    _classify_vcpu_reduction_safety
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

print "\n$tests tests, $fail failures\n";
exit($fail ? 1 : 0);
