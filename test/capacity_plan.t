#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/capacity_plan.t
# PURPOSE:   The Seasonal Capacity Plan (v10 C6), extracted from nfit-profile:
#            _capacity_plan_pair, _capacity_plan_pools, _capacity_plan_event_days,
#            _capacity_plan_calendar, _capacity_plan_time_basis,
#            _capacity_plan_member_figures, _capacity_plan_step_limit,
#            _capacity_plan_build, _capacity_plan_lines. The cases follow the
#            worked example of the design note (rules P1-P10):
#              - the planned pair keeps what is installed where the event needs
#                less, and is made valid before additions and pool totals are
#                taken from it; what validity adds is kept apart from demand;
#              - a pool on the planned entitlements, its maximum in whole units;
#              - overlaps from the calendar; combined only where this run's
#                forecast took in the other event's multiplier; events on the
#                same days are alternatives;
#              - VIOS pairs on the pair figure; PowerHA stated as not assessed;
#              - the normal plan compared only; an increase never hides an
#                unresolved dependency.
# USAGE:     perl test/capacity_plan.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use File::Temp qw(tempdir);
use File::Spec;

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = "package UUT; use Scalar::Util qw(looks_like_number); use List::Util qw(sum0 max min); use JSON; use File::Spec;\n"
         . "use Time::Piece; use Time::Seconds; use File::Basename qw(basename);\n"
         . "our \$FLOAT_EPSILON = 1e-9; our \$MIN_ENT_PER_VP = 0.05; our \$STRUCTURAL_ENT_TOLERANCE = 0.005;\n";
foreach my $name (qw(_safe_dig _rationale_wrap add_months days_in_month _get_recurring_monthly_period determine_event_period
                     _detect_concurrent_events_strict _capacity_plan_pair _capacity_plan_pools _capacity_plan_event_days
                     _capacity_plan_calendar _capacity_plan_time_basis _capacity_plan_member_figures _capacity_plan_step_limit
                     _capacity_plan_build _capacity_plan_lines))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n";
}
eval "$code 1;" or die "harness eval failed: $@\n";

# -- P1-P3: the planned pair --------------------------------------------------------------
my $p = UUT::_capacity_plan_pair(2.00, 4, 2.60, 4);
is_deeply([ @{$p}{qw(Ent VP AddEnt AddVP WorkloadEnt WorkloadVP WidthVP PlatformEnt)} ], [ 2.6, 4, 0.6, 0, 0.6, 0, 0, 0 ],
          'entitlement only: +0.60, VPs kept');
$p = UUT::_capacity_plan_pair(1.60, 2, 1.50, 3);
is_deeply([ @{$p}{qw(Ent VP AddEnt AddVP WorkloadEnt WorkloadVP)} ], [ 1.6, 3, 0, 1, 0, 1 ],
          'enough entitlement, too few VPs: +1 VP, entitlement retained');
$p = UUT::_capacity_plan_pair(2.00, 4, 1.40, 3);
is_deeply([ @{$p}{qw(Ent VP AddEnt AddVP)} ], [ 2, 4, 0, 0 ], 'already large enough: installed retained, nothing cut');
is_deeply([ @{$p}{qw(EventEnt EventVP)} ], [ 1.4, 3 ], 'the lower event requirement is kept beside the retained pair');
$p = UUT::_capacity_plan_pair(1.00, 1, 1.50, 1);
is_deeply([ @{$p}{qw(Ent VP AddVP WorkloadVP WidthVP)} ], [ 1.5, 2, 1, 0, 1 ],
          'retained VPs too few for the raised entitlement: VPs rise to carry it, apart from the workload');
$p = UUT::_capacity_plan_pair(0.20, 2, 0.15, 5);
is_deeply([ @{$p}{qw(Ent VP AddEnt WorkloadEnt PlatformEnt AddVP)} ], [ 0.25, 5, 0.05, 0, 0.05, 3 ],
          'retained entitlement below 0.05 per VP: raised to the PowerVM minimum, shown apart from demand');
$p = UUT::_capacity_plan_pair(1.00, 2, 1.20, undef);
is($p->{VP}, 2, 'an event requirement without a VP figure keeps the installed VPs');
ok(!defined UUT::_capacity_plan_pair(undef, 2, 1.2, 2) && !defined UUT::_capacity_plan_pair(1, 2, undef, 2),
   'no plan without an installed pair or an event entitlement');

# -- P5: pools on the planned entitlements --------------------------------------------------
my $pools = UUT::_capacity_plan_pools([
    { vm => 'DB01', pool_id => 7, pool_cpu => 7, ent => 2.60 }, { vm => 'APP02', pool_id => 7, pool_cpu => 7, ent => 1.50 },
    { vm => 'OTH', pool_id => 7, pool_cpu => 7, ent => 3.30 }, { vm => 'ERP05', pool_id => 9, pool_cpu => 3, ent => 3.10 },
    { vm => 'DEF', pool_id => 0, pool_cpu => 32, ent => 9 } ]);
is(scalar(@$pools), 2, 'the default pool is not assessed here');
is_deeply([ @{ $pools->[0] }{qw(PoolId Planned Verdict RequiredMax)} ], [ 7, 7.4, 'raise', 8 ], 'pool 7: 7.40 against 7 -> at least 8');
is_deeply([ @{ $pools->[1] }{qw(PoolId Planned Verdict RequiredMax)} ], [ 9, 3.1, 'raise', 4 ], 'pool 9: 3.10 against 3 -> at least 4');
$pools = UUT::_capacity_plan_pools([ { vm => 'a', pool_id => 5, pool_cpu => 7, ent => 4.00 }, { vm => 'b', pool_id => 5, pool_cpu => 7, ent => 3.00 } ]);
is($pools->[0]{Verdict}, 'fits', 'exactly the maximum fits');
$pools = UUT::_capacity_plan_pools([ { vm => 'a', pool_id => 5, pool_cpu => 3, ent => 2.80, relied => 0.50 } ]);
is_deeply([ @{ $pools->[0] }{qw(Verdict Requirement RequiredMax)} ], [ 'borrowing', 3.3, 4 ], 'only the borrowing relied on exceeds it');
$pools = UUT::_capacity_plan_pools([ { vm => 'a', pool_id => 5, pool_cpu => 3, ent => 1 }, { vm => 'b', pool_id => 5, pool_cpu => 4, ent => 1 } ]);
is($pools->[0]{Verdict}, 'unknown', 'members disagreeing on the maximum: not assessed');
$pools = UUT::_capacity_plan_pools([ { vm => 'a', pool_id => 5, pool_cpu => 1, ent => 0.25, platform => 0.05 }, { vm => 'b', pool_id => 5, pool_cpu => 1, ent => 0.80 } ]);
is_deeply([ @{ $pools->[0] }{qw(Verdict Platform RequiredMax)} ], [ 'raise', 0.05, 2 ], 'the PowerVM minimum is carried in the pool total and named');

# -- P6/P7: the event calendar -------------------------------------------------------------
my %config = (
    Global => { min_history_days => 1 },
    'month-end-adaptive'   => { model => 'adaptive_peak', source_event => 'month-end-predictive' },
    'month-end-predictive' => { model => 'predictive_peak', period => 'monthly', day_of_period => -1, duration_days => 7 },
    'month-end-strategic'  => { model => 'multiplicative_seasonal', period => 'monthly', day_of_period => -1, duration_days => 7 },
    'black-friday'         => { model => 'multiplicative_seasonal', dates => '2025-11-21:2025-11-28, 2026-11-20:2026-11-27' },
    'payday'               => { model => 'multiplicative_seasonal', dates => '2026-11-15', duration_days => 3 },
    'quarter-end'          => { model => 'multiplicative_seasonal', period => 'monthly', day_of_period => -1, duration_days => 3 },
);
my $cal = UUT::_capacity_plan_calendar('month-end-adaptive', '2026-11-24', '2026-11-30', \%config);
is_deeply([ sort @{ $cal->{Alternatives} } ], [ 'month-end-predictive', 'month-end-strategic' ],
          'other forecasts of the same days are alternatives, not overlaps');
is_deeply([ map { [ @{$_}{qw(event start end overlapStart overlapEnd)} ] } @{ $cal->{Overlaps} } ],
          [ [ 'black-friday', '2026-11-20', '2026-11-27', '2026-11-24', '2026-11-27' ], [ 'quarter-end', '2026-11-28', '2026-11-30', '2026-11-28', '2026-11-30' ] ],
          'overlaps from the calendar with their own days and the shared days');
$cal = UUT::_capacity_plan_calendar('payday', '2026-11-16', '2026-11-16', { payday => $config{payday}, x => { dates => '2026-11-17', duration_days => 2 } });
is_deeply($cal->{Overlaps}, [], 'an event outside the occurrence is not an overlap');
my @days = UUT::_capacity_plan_event_days($config{payday}, Time::Piece->strptime('2026-11-16', '%Y-%m-%d'), Time::Piece->strptime('2026-11-16', '%Y-%m-%d'));
is_deeply(\@days, [ '2026-11-15', '2026-11-17' ], 'a single date runs for duration_days');

# -- P4: the time basis ---------------------------------------------------------------------
my $dir = tempdir(CLEANUP => 1);
my $write = sub { open my $m, '>', File::Spec->catfile($dir, '.nfit.cache.manifest') or die; print {$m} @_; close $m };
$write->("cache_build_status: success\nstaged_by: nfit-stage-influxdb.py v1.9\n");
is_deeply(UUT::_capacity_plan_time_basis($dir), { Basis => 'UTC', Verified => 1 }, 'InfluxDB-staged data: UTC');
$write->("cache_build_status: success\nbuild_timestamp: 2026-11-01T10:00:00+0200\n");
is(UUT::_capacity_plan_time_basis($dir)->{Verified}, 0, 'NMON-staged data: not verified');
is(UUT::_capacity_plan_time_basis(File::Spec->catdir($dir, 'none'))->{Basis}, 'not verified', 'no manifest: not verified');

# -- the worked example ---------------------------------------------------------------------
sub member
{
    my ($ient, $ivp, $profile, $ent, $vp, $cent, $cvp, %extra) = @_;
    return { installedEntitlement => $ient, installedVPs => $ivp, planningProfile => $profile,
             profiles => { $profile => { entitlement => $ent, recommendedVPs => $vp,
                                         currentCycle => { entitlement => $cent, VPs => $cvp, binding => $extra{binding} // [] } } },
             (defined $extra{role} ? (role => $extra{role}) : ()) };
}
my %members = (
    DB01  => member(2.00, 4, 'G1', 2.60, 4, 2.60, 4),
    APP02 => member(1.50, 3, 'G3', 1.30, 3, 1.30, 3),
    BAT04 => member(1.60, 2, 'B3', 1.50, 3, 1.60, 2),
    ERP05 => member(2.40, 4, 'G2', 3.10, 4, 3.00, 4, binding => [ 'POOL_SIZE' ]),
    WEB03 => member(2.00, 4, 'G3', 1.40, 3, 1.40, 3),
    OTH   => member(3.30, 4, 'G3', 3.30, 4, 3.30, 4),
    vios1 => member(1.00, 2, 'P', 1.20, 2, 1.20, 2, role => { type => 'viosPair', profile => 'P', partners => [ 'vios2' ],
                                                               targetEntitlement => 1.50, targetVPs => 2, entitlement => 1.50, VPs => 2 }),
    vios2 => member(1.00, 2, 'P', 1.10, 2, 1.10, 2, role => { type => 'viosPair', profile => 'P', partners => [ 'vios1' ],
                                                               targetEntitlement => 1.50, targetVPs => 2, entitlement => 1.50, VPs => 2 }),
    DBHA  => member(0.50, 2, 'G3', 0.30, 1, 0.30, 1),
);
my %configuration = (DB01 => { pool_id => 7, pool_cpu => 7 }, APP02 => { pool_id => 7, pool_cpu => 7 }, OTH => { pool_id => 7, pool_cpu => 7 },
                     ERP05 => { pool_id => 9, pool_cpu => 3 }, BAT04 => { pool_id => 0, pool_cpu => 32 }, WEB03 => { pool_id => 0, pool_cpu => 32 },
                     vios1 => { pool_id => 0, pool_cpu => 32 }, vios2 => { pool_id => 0, pool_cpu => 32 }, DBHA => { pool_id => 0, pool_cpu => 32 });
my $normal = { Status => 'matching', Snapshot => 'n.json', Record => { members => {
    APP02 => member(1.50, 3, 'G3', 1.20, 3, 1.20, 3), DB01 => member(2.00, 4, 'G1', 2.70, 4, 2.70, 4) } } };
my $bf = { Status => 'matching', Snapshot => 'b.json', Record => { members => {
    DB01 => { %{ member(2.00, 4, 'G1', 2.40, 4, 2.40, 4) }, capacityPlan => { combinedEvents => [] } } } } };
my %ctx = (event => 'month-end-adaptive', start => '2026-11-24', end => '2026-11-30', timeBasis => { Basis => 'UTC', Verified => 1 },
           vmOrder => [ qw(DB01 APP02 BAT04 ERP05 WEB03 OTH vios1 vios2 DBHA) ], configuration => \%configuration,
           calendar => { Overlaps => [ { event => 'black-friday', start => '2026-11-20', end => '2026-11-27', overlapStart => '2026-11-24', overlapEnd => '2026-11-27' } ],
                         Alternatives => [ 'month-end-predictive' ] },
           references => { 'black-friday' => $bf }, normal => $normal, normalModel => 'hybrid-state-decay',
           clusters => [ [ 'DB01', 'DBHA' ] ], combined => {}, windowPlan => { Members => {} }, roleBinding => {});
my $plan = UUT::_capacity_plan_build(\%members, \%ctx);
my %row = map { ($_->{vm} => $_) } @{ $plan->{Rows} };

is_deeply([ $row{DB01}{action}, $row{DB01}{pair}{Ent}, $row{DB01}{pair}{AddEnt} ], [ 'increase', 2.6, 0.6 ], 'DB01: +0.60 ENT');
ok((grep { $_ eq 'overlap' } @{ $row{DB01}{unresolved} }) && (grep { $_ eq 'PowerHA role' } @{ $row{DB01}{unresolved} }),
   'an increase does not hide the overlap or the role dependency');
is_deeply([ $row{DB01}{overlaps}[0]{reference}{entitlement}, !!$row{DB01}{overlaps}[0]{combined} ], [ 2.4, '' ],
          'the overlapping event\'s stored requirement is a reference; the combined requirement is not assessed');
is_deeply([ $row{APP02}{action}, $row{APP02}{pair}{Ent}, $row{APP02}{normalConflict}{entitlement} ], [ 'retain', 1.5, 1.2 ],
          'APP02: installed retained; the normal plan\'s 1.20 below the event requirement 1.30 is reported');
ok(!$row{DB01}{normalConflict}, 'a normal plan at or above the event requirement is no conflict');
is_deeply([ @{ $row{BAT04}{pair} }{qw(Ent VP AddVP)}, $row{BAT04}{step}{VP} ], [ 1.6, 3, 1, 2 ],
          'BAT04: +1 VP; this cycle\'s VP step is shown beside it');
like($row{BAT04}{step}{Limit}, qr/this cycle's VP step/, 'the VP step named as the limit');
is_deeply([ @{ $row{ERP05}{pair} }{qw(Ent VP)}, @{ $row{ERP05}{step} }{qw(Ent VP)} ], [ 3.1, 4, 3, 4 ],
          'ERP05: the full requirement 3.10 is planned, the step 3.00 shown beside it, never in its place');
is($row{ERP05}{step}{Limit}, 'the size of pool 9 (3)', 'the step\'s limit is the pool\'s size');
is_deeply([ $row{WEB03}{action}, @{ $row{WEB03}{pair} }{qw(Ent VP)} ], [ 'retain', 2, 4 ], 'WEB03: already large enough, nothing cut');
is_deeply([ map { [ $row{$_}{pair}{Ent}, $row{$_}{pair}{AddEnt}, $row{$_}{role} ] } qw(vios1 vios2) ],
          [ [ 1.5, 0.5, 'VIOS pair with vios2' ], [ 1.5, 0.5, 'VIOS pair with vios1' ] ], 'the VIOS pair is planned together on its pair figure');
like($row{DBHA}{roleLimitation}, qr/^PowerHA role not assessed for the event: confirm takeover capacity for the event allocation of DB01 2\.60 \/ 4$/,
     'DBHA: PowerHA role not assessed, the peer\'s event allocation named');
is_deeply([ @{ $row{DBHA}{pair} }{qw(Ent VP)} ], [ 0.5, 2 ], 'nothing of a PowerHA node is lowered');
my %pool = map { ($_->{PoolId} => $_) } @{ $plan->{Pools} };
is_deeply([ @{ $pool{7} }{qw(Planned RequiredMax)}, @{ $pool{9} }{qw(Planned RequiredMax)} ], [ 7.4, 8, 3.1, 4 ],
          'pools on the retained and increased allocations: 7.40 -> 8, 3.10 -> 4');
like($row{APP02}{poolPrerequisite}, qr/^pool 7 maximum to at least 8 first/, 'a retained member carries its pool\'s prerequisite');
is_deeply([ @{ $plan->{Totals} }{qw(Increases AddEnt AddVP Retained)} ], [ 5, 2.3, 1, 4 ], 'increases: 5 LPARs, +2.30, +1 VP');

my $text = join("\n", UUT::_capacity_plan_lines($plan, \%ctx));
(my $flat = $text) =~ s/\s+/ /g;
like($text, qr/Capacity available before the event starts: 24 Nov 2026 00:00 \(UTC, the data's time basis\)\./, 'required by the event start, UTC');
like($text, qr/Pool 7: calculated requirement 7\.40 processing units; set the maximum to at least 8 \(now 7\)\./, 'pool 7 prerequisite');
like($text, qr/Pool 9: calculated requirement 3\.10 processing units; set the maximum to at least 4 \(now 3\)\./, 'pool 9 prerequisite');
like($flat, qr/black-friday \(20 Nov to 27 Nov 2026\) overlaps this occurrence on 24 Nov to 27 Nov 2026; combined requirement not assessed for all 9 LPARs of this run\./,
     'the overlap is unresolved');
like($flat, qr/Separate requirements, month-end-adaptive and black-friday \(stored plan, same data\): DB01 2\.60 and 2\.40\./, 'separate requirements as references');
like($flat, qr/APP02: the normal plan's 1\.20 \/ 3 is below the event requirement 1\.30 \/ 3 for 24 Nov to 30 Nov 2026\./, 'the conflict, reported only');
like($text, qr/\n    ERP05  2\.40 \/ 4  ->  3\.10 \/ 4   \(\+0\.70 ENT;/, 'ERP05 line, columns kept');
like($flat, qr/ERP05 2\.40 \/ 4 -> 3\.10 \/ 4 \(\+0\.70 ENT; this cycle's pair stops at 3\.00 \/ 4: the size of pool 9 \(3\)\)/, 'ERP05: the step beside the full requirement');
like($flat, qr/BAT04 1\.60 \/ 2 -> 1\.60 \/ 3 \(\+1 VP; entitlement sufficient;/, 'BAT04 line');
like($text, qr/Same occurrence, other forecasts \(alternatives, not overlaps\): month-end-predictive\./, 'alternatives listed');
like($flat, qr/Already sufficient: 4 LPARs \(APP02, WEB03, OTH, DBHA\)/, 'already sufficient, summarised');
like($text, qr/Post-event allocation is determined by the normal capacity review\.\n*\z/, 'one footer, last');
unlike($text, qr/partition profile|return to|rerun|re-run/i, 'no profile-maximum warning, no return schedule, no rerun date');

# Unverified time basis; a missing normal plan; a combined forecast; borrowing relied on.
$ctx{timeBasis} = { Basis => 'not verified', Verified => 0 };
$ctx{normal} = { Status => 'absent' };
$ctx{combined} = { DB01 => [ 'black-friday' ] };
$ctx{windowPlan} = { Members => { ERP05 => { allowanceWithheld => 1, entitlement => 3.10, borrowingReliedOn => 1.20 } } };
$configuration{ERP05}{pool_cpu} = 4;
$plan = UUT::_capacity_plan_build(\%members, \%ctx);
%row = map { ($_->{vm} => $_) } @{ $plan->{Rows} };
ok($row{DB01}{overlaps}[0]{combined} && !grep({ $_ eq 'overlap' } @{ $row{DB01}{unresolved} }),
   'combined only where this run\'s forecast took in the other event');
%pool = map { ($_->{PoolId} => $_) } @{ $plan->{Pools} };
is_deeply([ @{ $pool{9} }{qw(Verdict Planned Relied Requirement RequiredMax)} ], [ 'borrowing', 3.1, 1.2, 4.3, 5 ],
          'only the borrowing relied on exceeds pool 9');
$text = join("\n", UUT::_capacity_plan_lines($plan, \%ctx));
($flat = $text) =~ s/\s+/ /g;
like($text, qr/24 Nov 2026 00:00 on the data's time basis, which is not\n\s+verified/, 'an unverified time basis is said so');
like($flat, qr/Normal plan \(hybrid-state-decay\): no stored plan for this system; not compared\./, 'a missing normal plan is stated');
like($text, qr/Overlaps combined in this forecast:\n\s+black-friday .*\n?.*combined in this forecast for 1 LPAR \(DB01\)\./, 'the combined overlap');
like($flat, qr/combined requirement not assessed for 8 LPARs \(APP02, BAT04, ERP05, WEB03, OTH, vios1, vios2, DBHA\)\./, 'the others stay unresolved');
like($flat, qr/Pool 9: the planned entitlements \(3\.10\) fit the maximum of 4, but with the borrowing relied on \(1\.20\) the requirement is 4\.30: raise the maximum to at least 5, or accept the reliance\./,
     'borrowing-only wording') or diag($text);

# A stored plan of the overlapping event that combined this one is flagged as such.
$bf->{Record}{members}{DB01}{capacityPlan}{combinedEvents} = [ 'month-end-adaptive' ];
$ctx{combined} = {};
$plan = UUT::_capacity_plan_build(\%members, \%ctx);
%row = map { ($_->{vm} => $_) } @{ $plan->{Rows} };
ok($row{DB01}{overlaps}[0]{reference}{combinedThisEvent}, 'the reference states that its plan combined this event');
is(UUT::_capacity_plan_member_figures($normal->{Record}, 'APP02')->{CycleEnt}, 1.2, 'a stored plan\'s current-cycle entitlement');

done_testing();
