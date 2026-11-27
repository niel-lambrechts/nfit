#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/pool_reduction.t
# PURPOSE  : The pool reduction candidate (v10 B6), by sub-extraction: the
#            three quantities kept apart (members' entitlements, conservative
#            takeover provision, measured use), eligibility against
#            prerequisites, member reductions needed first, unknown and known
#            takeover requirements, the evidence rules, and the wording.
# USAGE    : perl pool_reduction.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use JSON;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_pool_whole_units _pool_reduction_members _pool_reduction_candidate _pool_reduction_text _exposure_tolerance _pool_units_text))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval q{
package UUT;
use Scalar::Util qw(looks_like_number);
use List::Util qw(max min);
use JSON;
our $FLOAT_EPSILON = 1e-9;
our $STRUCTURAL_ENT_TOLERANCE = 0.005;
our %MEMBER_EXPOSURE_RULES = (localScarceAbsCores => 0.10, localScarceRel => 0.02);
our %POOL_REDUCTION_RULE = (minDays => 28, minCoverage => 0.99, minDayCoverage => 0.90);
} . $code . "\n1;\n" or die "harness eval failed: $@\n";

# A pool as the structural check gives it, from member rows [name, installed, full target, this cycle].
my $units = sub { my ($v) = @_; my $u = int($v); $u++ if ($v - $u > 1e-9); return $u; };
my $pool = sub
{
    my ($maximum, @rows) = @_;
    my ($i, $f, $c) = (0, 0, 0);
    foreach my $r (@rows) { $i += $r->[1]; $f += $r->[2]; $c += $r->[3]; }
    return { PoolId => 9, Ceiling => $maximum, InstalledSum => $i, ProposedSum => $f, CycleSum => $c, RequiredMax => $units->($f), RequiredMaxCycle => $units->($c),
             Members => [ map { { Vm => $_->[0], Current => $_->[1], Proposed => $_->[2], Cycle => $_->[3] } } @rows ] };
};
# Clean evidence: 30 days at one minute, fully covered, the pool never at its maximum.
my $evidence = sub
{
    my (%over) = @_;
    return { pool => 9, maximum => 20, cadenceMinutes => 1, days => 30, daysObserved => 30, coverage => 1, worstDayCoverage => 1, peak => 2.71,
             peakDay => '2026-09-12T00:00:00Z', secondDayPeak => 2.40, daysNearPeak => 1, routineDayPeak => 1.90,
             atMaximum => { minutes => 0, days => 0 }, changes => [], contestedMembers => [],
             frame => { assessed => JSON::true, observations => 43200, withoutSpareCores => 0 }, events => [], %over };
};
my $codes = sub { return [ map { $_->{code} } @{ $_[0] } ]; };

# --- The candidate: the largest of the parts; conditional, never an instruction ------
my $r = UUT::_pool_reduction_candidate($evidence->(), $pool->(20, [ a => 2.0, 0.6, 0.8 ], [ b => 2.0, 0.3, 0.3 ]), {});
is_deeply([ @$r{qw(verdict candidate unitsRemoved setBy)} ], ['candidate', 3, 17, 'workload'], 'measured use sets the candidate: a peak of 2.71 needs 3 whole units');
is_deeply([ $r->{structural}{full}, $r->{structural}{cycle}, $r->{installedEntitlement} ], [1, 2, 4], 'both entitlement requirements and the installed total stay visible');
ok($r->{eligible}, 'the evidence is fit to rest a candidate on');
is_deeply($codes->($r->{prerequisites}), [qw(memberReductions reserve futureEvents licensing)], 'and what remains to be done is listed apart: it is not an approved reduction');
$r = UUT::_pool_reduction_candidate($evidence->(peak => 2.95), $pool->(20, [ a => 1.0, 0.6, 0.8 ]), {});
is($r->{workload}{units}, 4, 'a peak inside the near-maximum tolerance of 3 needs 4 (tolerance 0.10; no margin beyond it)');
$r = UUT::_pool_reduction_candidate($evidence->(peak => 0.60), $pool->(8, [ a => 2.2, 2.2, 2.2 ], [ b => 2.0, 2.0, 2.0 ]), {});
is_deeply([ @$r{qw(verdict candidate setBy)} ], ['candidate', 5, 'entitlements'], 'a quiet pool: the members\' entitlements set the candidate (a maximum cannot be below their sum)');
ok(!grep({ $_ eq 'memberReductions' } @{ $codes->($r->{prerequisites}) }), 'and no member reduction is needed first');
$r = UUT::_pool_reduction_candidate($evidence->(peak => 0.60), $pool->(5, [ a => 2.2, 2.2, 2.2 ], [ b => 2.0, 2.0, 2.0 ]), {});
is_deeply([ @$r{qw(verdict candidate needed)} ], ['none', undef, 5], 'a maximum already at what is needed: no reduction');
$r = UUT::_pool_reduction_candidate($evidence->(peak => 0.60), $pool->(4, [ a => 2.0, 2.6, 2.6 ], [ b => 2.0, 2.0, 2.0 ]), {});
is($r->{verdict}, 'none', 'a pool whose members need more than its maximum is not a reduction (the shortfall is the structural check\'s to report)');

# --- Member reductions required first ------------------------------------------------
$r = UUT::_pool_reduction_candidate($evidence->(peak => 0.90), $pool->(6, [ c1 => 2.2, 0.4, 1.2 ], [ c2 => 2.0, 0.3, 0.3 ]), {});
is_deeply([ @$r{qw(verdict candidate)} ], ['candidate', 2], 'the candidate is not floored at the installed entitlements: 2, though they total 4.20');
is_deeply($r->{memberReductionsFirst}, { installedMinimum => 5 }, 'the dependency is stated: until the members are reduced the maximum cannot go below 5');
my ($first) = grep { $_->{code} eq 'memberReductions' } @{ $r->{prerequisites} };
is($first->{text}, "reduce the members' entitlements first: installed they total 4.20, so the maximum cannot go below 5 until then", 'in the planner\'s words');
like(UUT::_pool_reduction_text($r, 0), qr/^Pool 9: reduction candidate, maximum 6 to 2 processing units\. For planner review, not an instruction: the maximum is not to be lowered before the points below are met\./,
     'the sentence says it is for review');
like(UUT::_pool_reduction_text($r, 0), qr/members' entitlements need 1 for the full targets and 2 this cycle \(installed they total 4\.20\); measured use needs 2 \(highest credible minute 0\.90 on 2026-09-12; no exceedance observed at the recorded resolution in 30 days\)/,
     'names each quantity, and "no exceedance observed at the recorded resolution"');
unlike(UUT::_pool_reduction_text($r, 0), qr/no clipping|will not|guarantee/i, 'no promise about the future, no "no clipping"');
like(UUT::_pool_reduction_text($r, 0), qr/Before reducing: reduce the members' entitlements first: .*; confirm the pool reserve: it is not collected and is not counted here; future event capacity has not been checked; check licensing\.$/,
     'and ends with what must be done or confirmed first; the reserve is not taken as zero');

# --- Takeover: provision where known, withheld where not -----------------------------
my @ha = ([ act => 4.0, 1.0, 1.0 ], [ sb => 3.0, 0.6, 0.6 ]);
$r = UUT::_pool_reduction_candidate($evidence->(peak => 1.20), $pool->(12, @ha), { sb => { Role => 'standby', Takeover => 2.40 } });
is_deeply([ $r->{takeover}{state}, $r->{takeover}{full}, $r->{takeover}{cycle} ], ['known', 4, 4],
          'known takeover requirement: 1.00 of the other member + 2.40 in place of the standby\'s 0.60 = 3.40, 4 units; its own 0.60 is not added again');
is_deeply([ @$r{qw(verdict candidate setBy normalOperation)} ], ['candidate', 4, 'takeover', 2], 'the provision sets the candidate; the normal-operation figure stays visible');
is_deeply([ $r->{structural}{full}, $r->{structural}{cycle} ], [2, 2], 'the ordinary structural minimum is a separate quantity');
like(UUT::_pool_reduction_text($r, 0), qr/conservative takeover provision 4 full, 4 this cycle \(sb at the takeover requirement, the other members as planned; not a verified failover scenario\)/,
     'described as conservative provision, not a verified failover');
$r = UUT::_pool_reduction_candidate($evidence->(peak => 1.20), $pool->(12, @ha), { sb => { Role => 'standby', Takeover => 0.40 } });
is_deeply([ $r->{takeover}{full}, $r->{candidate} ], [2, 2], 'a takeover requirement below the standby\'s own entitlement adds nothing');
$r = UUT::_pool_reduction_candidate($evidence->(peak => 1.20), $pool->(12, @ha), { sb => { Role => 'standby', Takeover => undef } });
is_deeply([ @$r{qw(verdict eligible)}, $r->{takeover}{state} ], ['withheld', JSON::false, 'unknown'], 'takeover requirement not known: the reduction is withheld');
is_deeply($codes->($r->{reasons}), ['takeover'], 'for that reason');
is($r->{normalOperation}, 2, 'a normal-operation figure is still shown');
like(UUT::_pool_reduction_text($r, 0), qr/^Pool 9: reduction not offered: the takeover requirement of sb is not known to nFit: the maximum the pool needs at takeover cannot be stated\. For information only, normal operation would need 2 processing units against a maximum of 12 \(.*\); that figure is not sufficient for a reduction decision\.$/,
     'labelled as not sufficient for a reduction decision');
# Which standbys have a known requirement: the same answer whatever the standby's policy.
my $profiles = { P => { currentCycle => { VPs => 2, reviewVPs => undef } } };
my $known = UUT::_pool_reduction_members({
    share_resolved => { estateRole => 'standby', planningProfile => 'P', profiles => $profiles, installedVPs => 4,
                        role => { type => 'haStandby', primaryTarget => { entitlement => 2.4, source => 'cycle' } } },
    share_pending  => { estateRole => 'standby', role => { type => 'haStandby', primaryTarget => { entitlement => 2.4, source => 'installed' } }, pendingRole => { type => 'haStandby' } },
    share_installed => { estateRole => 'standby', role => { type => 'haStandby', primaryTarget => { entitlement => 3.0, source => 'installed' }, unresolved => 'no plan' } },
    fixed          => { estateRole => 'standby', role => { type => 'haStandbyFixed', fixedEntitlement => 0.10 } },
    fixed_not_applied => { estateRole => 'standby' },
    workload_bearing => { role => {} },
    vios           => { estateRole => 'vios', role => { type => 'viosPair' } },
});
is($known->{share_resolved}{Takeover}, 2.4, 'a share standby whose primary\'s plan is resolved: the primary\'s target');
is_deeply([ map { $known->{$_}{Takeover} } qw(share_pending share_installed fixed fixed_not_applied) ], [ undef, undef, undef, undef ],
          'not known: a share standby still pending or resting on the primary\'s installed figure, and any fixed standby');
is_deeply([ map { $known->{$_}{Role} } qw(fixed fixed_not_applied share_pending workload_bearing vios) ], [qw(standby standby standby), '', 'vios'],
          'all of them are standbys to the pool; a workload-bearing node is an ordinary member; a VIOS keeps its own treatment');
foreach my $kind (qw(share_pending share_installed fixed fixed_not_applied))
{
    my $w = UUT::_pool_reduction_candidate($evidence->(peak => 1.20), $pool->(12, [ act => 4.0, 1.0, 1.0 ], [ $kind => 3.0, 0.6, 0.6 ]), $known);
    is_deeply([ $w->{verdict}, @{ $codes->($w->{reasons}) } ], ['withheld', 'takeover'], "unknown takeover need withholds the reduction alike: $kind");
}
my $v = UUT::_pool_reduction_candidate($evidence->(peak => 1.20), $pool->(12, [ vios => 2.0, 1.5, 1.5 ], [ workload_bearing => 3.0, 0.6, 0.6 ]), $known);
is_deeply([ $v->{verdict}, $v->{takeover}{state}, $v->{candidate} ], ['candidate', 'none', 3], 'a VIOS and a workload-bearing node need no takeover provision: 2.10 of entitlements, 3 units');

# --- Evidence rules -------------------------------------------------------------------
my $plain = $pool->(20, [ a => 2.0, 0.6, 0.8 ]);
my $why = sub { return [ UUT::_pool_reduction_candidate($evidence->(@_), $plain, {})->{verdict}, @{ $codes->(UUT::_pool_reduction_candidate($evidence->(@_), $plain, {})->{reasons}) } ]; };
is_deeply($why->(cadenceMinutes => 15), ['notAssessed', 'resolution'], '15-minute data: not assessed');
is(UUT::_pool_reduction_text(UUT::_pool_reduction_candidate($evidence->(cadenceMinutes => 15), $plain, {}), 0),
   'Pool 9: reduction not assessed: 15-minute data can hide short peaks. Collect one-minute data.', 'with the settled sentence');
is_deeply($why->(days => 27), ['withheld', 'days'], '27 days: withheld (28 needed)');
is_deeply($why->(coverage => 0.985), ['withheld', 'coverage'], '98.5% of the minutes: withheld (99% needed)');
is_deeply($why->(worstDayCoverage => 0.5), ['withheld', 'dayCoverage'], 'one day at 50%: withheld (90% on every day)');
is_deeply($why->(atMaximum => { minutes => 40, days => 3 }), ['withheld', 'atMaximum'], 'the pool reached its maximum: its measured use is a lower bound');
is_deeply($why->(changes => ['2026-09-14T00:00:00Z']), ['withheld', 'changed'], 'a member or the pool changed in the period (entitlement, VPs, capped mode, roster): re-measure');
is_deeply($why->(maximumChanged => [6, 8]), ['withheld', 'changed'], 'so does a change of the maximum itself');
is(UUT::_pool_reduction_candidate($evidence->(changes => ['2026-09-14T00:00:00Z', '2026-09-20T00:00:00Z'], changeKinds => [ ['VPs', 'entitlement'], ['members joined or left'] ]), $plain, {})->{reasons}[0]{text},
   'the pool or its members changed in the period (2026-09-14: VPs, entitlement; 2026-09-20: members joined or left): re-measure after the change', 'what changed is named with its date');
# The frame anywhere in the period, not only at the pool's highest minutes.
is_deeply($why->(frame => { assessed => JSON::true, observations => 43200, withoutSpareCores => 12 }), ['withheld', 'frame'],
          'the frame was without spare cores in 12 minutes of the period, whenever they fell: withheld');
$r = UUT::_pool_reduction_candidate($evidence->(frame => { assessed => JSON::false, observations => 0, withoutSpareCores => 0 }), $plain, {});
is_deeply([ $r->{verdict}, scalar(grep { $_ eq 'frameNotAssessed' } @{ $codes->($r->{prerequisites}) }) ], ['candidate', 1],
          'no frame observations: the check is reported as not assessed, among the prerequisites');
# Planned member changes that can release demand the measurements did not see.
my $busy = $pool->(20, [ a => 2.0, 0.6, 0.8 ], [ b => 1.0, 1.6, 1.6 ], [ c => 1.0, 1.4, 1.4 ]);
$r = UUT::_pool_reduction_candidate($evidence->(contestedMembers => ['b']), $busy, { a => { InstalledVP => 2, CycleVP => 3 }, c => { InstalledVP => 2, CycleVP => 2, ReviewVP => 4 } });
is_deeply($codes->($r->{reasons}), ['memberChange'], 'planned member changes that can release held-back demand withhold the reduction');
like($r->{reasons}[0]{text}, qr/^planned changes can release demand that was held back: a \(more VPs\); b \(a higher entitlement where it competed for the pool\); c \(a VP increase under review\)\. Re-measure after them$/,
     'more VPs, a VP increase under review, and an entitlement increase where the member competed for the pool');
$r = UUT::_pool_reduction_candidate($evidence->(), $busy, { a => { InstalledVP => 4, CycleVP => 3 } });
is($r->{verdict}, 'candidate', 'a VP reduction, and an entitlement increase without competition, do not');
# Events: missing and incompletely observed occurrences are prerequisites, not silence.
$r = UUT::_pool_reduction_candidate($evidence->(events => [ { event => 'month_end', state => 'observed' }, { event => 'year_end', state => 'notInPeriod' },
    { event => 'quarter_close', state => 'incomplete' } ]), $plain, {});
is_deeply($codes->($r->{prerequisites}), [qw(reserve eventsMissing eventsIncomplete futureEvents licensing)], 'configured events against the period');
is_deeply([ map { $_->{text} } grep { $_->{code} =~ /^events/ } @{ $r->{prerequisites} } ],
          ['the period does not contain year_end', 'incompletely observed in the period: quarter_close'], 'named');
# A compatible saved seasonal plan: no candidate below what it needs; other events stay unchecked.
$r = UUT::_pool_reduction_candidate($evidence->(events => [ { event => 'month_end', state => 'observed' }, { event => 'year_end', state => 'notInPeriod' } ],
    seasonalPlans => [ { event => 'year_end', requirement => 5.4, units => 6 } ]), $plain, {});
is_deeply([ @$r{qw(verdict candidate setBy)}, $r->{seasonalPlan}{units} ], ['candidate', 6, 'seasonalPlan', 6], 'a compatible saved seasonal plan needing 6: the candidate is not below it');
my ($future) = grep { $_->{code} eq 'futureEvents' } @{ $r->{prerequisites} };
is($future->{text}, 'future event capacity has been checked against the saved seasonal plan of year_end only; not checked for month_end', 'and the events without a plan are named as unchecked');
like(UUT::_pool_reduction_text($r, 0), qr/; the saved seasonal plan needs 6 \(year_end\)\. Before reducing:/, 'the plan is named among the quantities');
$r = UUT::_pool_reduction_candidate($evidence->(events => [ { event => 'year_end', state => 'observed' } ], seasonalPlans => [ { event => 'year_end', requirement => 1.2, units => 2 } ]), $plain, {});
is_deeply([ $r->{candidate}, scalar(grep { $_->{code} eq 'futureEvents' } @{ $r->{prerequisites} }) ], [3, 0], 'a plan for every configured event, below the candidate: nothing left unchecked');
is(UUT::_pool_reduction_candidate($evidence->(seasonalPlans => [ { event => 'year_end', requirement => 19.5, units => 20 } ]), $plain, {})->{verdict}, 'none',
   'a seasonal plan that needs the whole maximum: no reduction');
# No evidence, or no consistent maximum: not assessed.
is(UUT::_pool_reduction_candidate(undef, $plain, {})->{verdict}, 'notAssessed', 'no pool observations: not assessed');
is(UUT::_pool_reduction_candidate($evidence->(), { %$plain, Ceiling => undef }, {})->{verdict}, 'notAssessed', 'no consistent maximum: not assessed');

# --- The short form for a footprint line ----------------------------------------------
$r = UUT::_pool_reduction_candidate($evidence->(), $pool->(20, [ a => 2.0, 0.6, 0.8 ], [ b => 2.0, 0.3, 0.3 ]), {});
is(UUT::_pool_reduction_text($r, 1), 'reduction candidate 20 to 3 (conditional: memberReductions, reserve, futureEvents, licensing)', 'short form: candidate with its conditions');
is(UUT::_pool_reduction_text(UUT::_pool_reduction_candidate($evidence->(days => 20), $plain, {}), 1), 'reduction not offered (days)', 'short form: not offered');

done_testing();
