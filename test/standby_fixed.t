#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/standby_fixed.t
# PURPOSE  : Fixed standby entitlement (v10 B13), by sub-extraction: the policy
#            of the node resolved as standby, which nodes it acts on, the pair
#            of the current cycle (the entitlement in one step, the VPs by the
#            existing VP rules, the installed pair kept on a conflict) and the
#            planning action.
# USAGE    : perl standby_fixed.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_resolve_current_cycle_pair _ceil_ratio _current_cycle_action_text _policy_source_text
                 _ha_standby_policy _ha_sizing_plan _ha_fixed_standby_resolve _ha_fixed_standby_action _ha_fixed_not_applied_text
    _ha_role_rules _ha_sustained_workload _ha_day_text _ha_workload_text _ha_workload_veto_text))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
my ($min_ent_per_vp) = $src =~ /^my \$MIN_ENT_PER_VP\s*=\s*([0-9.]+);/m or die "MIN_ENT_PER_VP not found\n";
my ($ratio_max)      = $src =~ /^my \$VCPU_RATIO_MAX\s*=\s*([0-9.]+);/m or die "VCPU_RATIO_MAX not found\n";
eval qq{
package UUT;
use Scalar::Util qw(looks_like_number);
use List::Util qw(max);
our \$FLOAT_EPSILON = 1e-9;
our \$MIN_ENT_PER_VP = $min_ent_per_vp;
our \$VCPU_RATIO_MAX = $ratio_max;
} . $code . "\n1;\n" or die "harness eval failed: $@\n";

# --- Policy: that of the standby; only this mode's parameter is read ----------------
my %lpar  = (sb => {}, own => { standby_entitlement => 0.20 }, act => { ha_sizing => 'standby_fixed', standby_entitlement => 0.50 });
my %mgsys = (FRAME_B => { ha_sizing => 'standby_fixed', standby_entitlement => 0.10 }, FRAME_C => { ha_sizing => 'standby_fixed' });
my %global = (ha_sizing => 'standby_share', standby_share => 0.25);
my $of = sub
{
    my ($vm, $frame, $key) = @_;
    return ($lpar{$vm}{$key}, 'lpar') if (defined(($lpar{$vm} // {})->{$key}));
    return ($mgsys{$frame}{$key}, 'mgsys') if (defined $frame && defined(($mgsys{$frame} // {})->{$key}));
    return ($global{$key}, 'global') if (defined $global{$key});
    return (undef, 'built-in');
};
my $pair_of = sub
{
    my ($roles, $frames) = @_;
    return { Members => [ sort keys %$roles ], FrameOf => $frames // {}, Roles => { map { $_ => { Effective => $roles->{$_} } } keys %$roles } };
};
my $pol = UUT::_ha_standby_policy($pair_of->({ act => 'active', sb => 'standby' }, { sb => 'FRAME_B', act => 'FRAME_A' }), $of);
is_deeply([ @$pol{qw(Sizing Fixed Node Frame)} ], ['standby_fixed', 0.10, 'sb', 'FRAME_B'], 'the standby inherits its managed system\'s fixed policy');
is($pol->{Source}{standby_entitlement}, 'mgsys', 'and the source of the entitlement is recorded');
is($pol->{Error}, undef, 'no error');
$pol = UUT::_ha_standby_policy($pair_of->({ act => 'active', own => 'standby' }, { own => 'FRAME_B' }), $of);
is_deeply([ $pol->{Fixed}, $pol->{Source}{standby_entitlement}, $pol->{Source}{ha_sizing} ], [0.20, 'lpar', 'mgsys'],
          'an LPAR\'s own entitlement under the inherited mode');
$pol = UUT::_ha_standby_policy($pair_of->({ act => 'active', sb => 'standby' }, { sb => 'FRAME_A', act => 'FRAME_B' }), $of);
is_deeply([ $pol->{Sizing}, $pol->{Fixed} ], ['standby_share', undef], 'the active node\'s fixed policy (its own and its frame\'s) does not reach the standby');
$pol = UUT::_ha_standby_policy($pair_of->({ sb => 'active', act => 'standby' }, { act => 'FRAME_A' }), $of);
is_deeply([ $pol->{Sizing}, $pol->{Fixed}, $pol->{Node} ], ['standby_fixed', 0.50, 'act'], 'roles reversed: the other node\'s policy applies');
$pol = UUT::_ha_standby_policy($pair_of->({ act => 'active', sb => 'standby' }, { sb => 'FRAME_C' }), $of);
like($pol->{Error}, qr/^ha_sizing = standby_fixed applies to the standby sb on managed system FRAME_C and no standby_entitlement is set for it/,
     'a fixed policy without an entitlement for this node is an error, not a default');
is($pol->{Fixed}, undef, 'and no figure is invented');
$pol = UUT::_ha_standby_policy($pair_of->({ act => 'active', sb => 'standby' }, { sb => 'FRAME_B' }), $of);
is(exists $pol->{Share} ? 1 : 0, 1, 'the share is still resolved for the record; it has no effect under standby_fixed');

# --- Which node it acts on: the resolved standby only; role safeguards as without it ----
my $fixed_policy = { Sizing => 'standby_fixed', Fixed => 0.10 };
my $cluster = sub
{
    my ($roles, $transitions) = @_;
    return { Members => [ sort keys %$roles ], Roles => { map { $_ => { Effective => $roles->{$_} } } keys %$roles },
             Detection => { Transitions => $transitions // [] } };
};
my $plan = UUT::_ha_sizing_plan($cluster->({ a => 'active', b => 'standby' }), $fixed_policy);
is_deeply($plan, { b => { Treatment => 'standby_fixed', Primary => 'a' } }, 'one active, one standby: the standby takes the fixed entitlement');
$plan = UUT::_ha_sizing_plan($cluster->({ a => 'active', b => 'standby' }, [ { Sustained => 1, From => 1000, Rises => 'a', Falls => 'b' } ]), $fixed_policy);
is_deeply([ $plan->{a}{Treatment}, $plan->{a}{Reason}, $plan->{b}{Treatment} ], ['hold_reductions', 'took_over', 'standby_fixed'],
          'after a handover the new active node\'s reductions are still held');
is_deeply(UUT::_ha_sizing_plan($cluster->({ a => 'active', b => 'active' }), $fixed_policy), {}, 'two active nodes: not applied');
is_deeply(UUT::_ha_sizing_plan($cluster->({ a => 'unknown', b => 'unknown' }), $fixed_policy), {}, 'unresolved roles: not applied');
$plan = UUT::_ha_sizing_plan($cluster->({ a => 'unknown', b => 'unknown' }, [ { Sustained => 1, From => 1000, Rises => 'a', Falls => 'b' } ]), $fixed_policy);
is_deeply([ map { $plan->{$_}{Treatment} } qw(a b) ], ['hold_reductions', 'hold_reductions'], 'unresolved after a handover: both held, as without the setting');
is_deeply(UUT::_ha_sizing_plan($cluster->({ a => 'active', b => 'standby', c => 'standby' }), $fixed_policy), {}, 'three nodes: not applied');

# --- Entitlement and VPs resolved together ------------------------------------------
# The VP count is the width the node's own workload needs (its strategic VP target). The floor that
# preserves the installed entitlement and the per-cycle step cap are not needs for VPs.
my $fx = sub { return UUT::_ha_fixed_standby_resolve({ fixed => 0.10, safety_state => 'CLEAR', @_ }); };
my $r = $fx->(own_vp => 1, step_vp => 6, current_ent => 6.00, current_vp => 7);
is_deeply([ $r->{Applied}, $r->{Pair}{Ent}, $r->{Pair}{VP}, $r->{Transition} ], [1, 0.10, 1, 1],
          'high installed entitlement (6.00 on 7 VPs), quiet standby, workload width 1 VP, safety CLEAR: 0.10 on 1 VP at once');
$r = $fx->(own_vp => 1, step_vp => 3, current_ent => 1.50, current_vp => 4);
is_deeply([ $r->{Applied}, $r->{Pair}{Ent}, $r->{Pair}{VP}, $r->{Transition} ], [1, 0.10, 1, 1], 'a pure tier-step limit (4 to 3) does not hold the pair: 0.10 on 1 VP');
$r = $fx->(own_vp => 2, step_vp => 2, current_ent => 3.00, current_vp => 4);
is_deeply([ $r->{Applied}, $r->{Pair}{VP}, $r->{Transition} ], [1, 2, 0], 'where the step already reaches the workload\'s width there is no transition to explain');
# A genuine safety concern is not bypassed: the VPs take only the step the VP rules give.
$r = $fx->(own_vp => 1, step_vp => 7, current_ent => 6.00, current_vp => 7, safety_state => 'BLOCK', safety_reasons => 'material RunQ/workload pressure is present');
is_deeply([ $r->{Applied}, $r->{Kind}, $r->{CycleVP} ], [0, 'safety', 7], 'a pressure-based block: the VPs stay, 0.10 cannot be carried on 7, the fixed figure is not applied');
like($r->{Cause}, qr/^the VP reduction is limited this cycle \(BLOCK: material RunQ\/workload pressure is present\), which leaves 7 VPs, and 0\.10 carries at most 2$/,
     'and the cause names the verdict and its evidence, not a step limit');
$r = $fx->(own_vp => 1, step_vp => 2, current_ent => 1.50, current_vp => 3, safety_state => 'CAUTION', safety_reasons => 'positive growth adjustment 0.20 cores');
is_deeply([ $r->{Applied}, $r->{Pair}{Ent}, $r->{Pair}{VP}, $r->{Limited} ], [1, 0.10, 2, 'CAUTION: positive growth adjustment 0.20 cores'],
          'a caution: the VPs take the permitted step; the fixed figure is set where that step can carry it');
$r = $fx->(own_vp => 1, step_vp => 3, current_ent => 1.50, current_vp => 4, safety_state => undef);
is_deeply([ $r->{Applied}, $r->{Kind} ], [0, 'safety'], 'no safety verdict is not a clear one');
# The workload's own width is never undercut, and the fixed figure is never raised.
$r = $fx->(own_vp => 3, step_vp => 5, current_ent => 3.00, current_vp => 6);
is_deeply([ $r->{Applied}, $r->{Kind}, $r->{Width} ], [0, 'width', 3], 'credible peaks or run queue needing 3 VPs: 0.10 carries 2, so the fixed figure is not applied');
is($r->{Cause}, 'its own workload needs 3 VPs and 0.10 carries at most 2 (0.05 processors per VP)', 'the cause is the measured width');
ok(!defined $r->{Pair}, 'no pair is made up: the caller keeps the node\'s ordinary pair');
$r = $fx->(own_vp => 4, step_vp => 4, current_ent => 2.00, current_vp => 4);
is_deeply([ $r->{Applied}, $r->{Kind} ], [0, 'width'], 'a node whose workload needs 4 VPs: not applied (its ordinary pair may be an increase)');
$r = UUT::_ha_fixed_standby_resolve({ fixed => 0.15, safety_state => 'CLEAR', own_vp => 3, step_vp => 3, current_ent => 1.50, current_vp => 4 });
is_deeply([ $r->{Applied}, $r->{Pair}{Ent}, $r->{Pair}{VP} ], [1, 0.15, 3], 'a fixed figure that carries the workload\'s width is set on it');
$r = $fx->(own_vp => 1, step_vp => 1, current_ent => 0.05, current_vp => 1);
is_deeply([ $r->{Applied}, $r->{Pair}{Ent}, $r->{Pair}{VP} ], [1, 0.10, 1], 'an override, not a ceiling: below the fixed figure the entitlement is raised to it');
$r = $fx->(own_vp => 2, step_vp => 1, current_ent => 0.50, current_vp => 1);
is_deeply([ $r->{Applied}, $r->{Pair}{VP}, $r->{Pair}{ReviewVP} ], [1, 1, 2], 'more VPs than installed on evidence alone stay a review, as for any LPAR');
$r = UUT::_ha_fixed_standby_resolve({ fixed => 2.50, safety_state => 'CLEAR', own_vp => 1, step_vp => 1, current_ent => 0.50, current_vp => 1 });
is_deeply([ $r->{Applied}, $r->{Pair}{Ent}, $r->{Pair}{VP} ], [1, 2.50, 3], 'a fixed figure wider than the VPs: VPs added to carry it');
$r = UUT::_ha_fixed_standby_resolve({ fixed => 2.50, safety_state => 'CLEAR', own_vp => 1, step_vp => 1, current_ent => 0.50, current_vp => 1, pool_size => 2 });
is_deeply([ $r->{Applied}, $r->{Kind} ], [0, 'pool'], 'a fixed figure above the pool\'s size is not applied');
$r = $fx->(own_vp => undef, step_vp => 3, current_ent => 1.50, current_vp => 4);
is_deeply([ $r->{Applied}, $r->{Kind} ], [0, 'evidence'], 'no VP requirement from the workload: insufficient evidence, not applied');
$r = $fx->(own_vp => 1, step_vp => 1, current_ent => undef, current_vp => undef);
is_deeply([ $r->{Applied}, $r->{Kind} ], [0, 'evidence'], 'no installed pair: not applied');

# --- The staging guard of ordinary sizing is kept; the exception is the fixed figure only ----
# Ordinary sizing is coordinated: entitlement and VPs may both come down in one cycle. The staging
# guard is one of its safeguards and is not entitlement preservation. (The coordinated change itself
# is traced end to end in stage1_reduction_test.py.)
my $ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 1, step_vp => 3, current_ent => 3.00, current_vp => 4 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP}, $ordinary->{Status} ], [1.01, 3, 'STAGED'],
          'ordinary staged reduction 4 -> 3 VPs towards 1: unchanged (the entitlement cut stops at 1.01)');
ok((grep { $_ eq 'RATIO_GUARD' } @{ $ordinary->{Binding} }), 'and the staging guard is named as what shaped it');
$ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 2, step_vp => 14, current_ent => 4.00, current_vp => 15 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP} ], [3.47, 14], 'an over-wide ordinary LPAR (15 VPs on 4.00): today\'s ratio 3.75 is kept, as before');
$ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 1, step_vp => 1, current_ent => 0.50, current_vp => 1 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP} ], [0.10, 1], 'a one-VP LPAR has no narrower step: a small entitlement is not raised for a ratio');

# --- The planning action ------------------------------------------------------------
$r = $fx->(own_vp => 1, step_vp => 6, current_ent => 6.00, current_vp => 7);
my $text = join(' ', grep { $_ ne '' } UUT::_ha_fixed_standby_action({ primary => 'dbnode1', roleSource => 'inferred', fixed => 0.10, fixedSource => 'mgsys',
    ownEnt => 0.30, resolved => $r, currentEnt => 6.00, currentVP => 7, poolId => 0, primaryEnt => 12 }));
like($text, qr/^PowerHA standby for dbnode1 \(inferred\): fixed standby entitlement 0\.10 \(its managed system's default\)\. Its own workload needs 0\.30, above the standby allocation\./,
     'the row names the policy, its source, and the own-workload requirement above it');
like($text, qr/The workload supports fewer VPs; the previous limit came from preserving installed entitlement\. Entitlement and VPs are assessed together\./,
     'a pure transition constraint is explained as such');
like($text, qr/entitlement 6\.00 to 0\.10/, 'the entitlement change');
like($text, qr/VPs 7 to 1/, 'the VP change');
like($text, qr/At failover it needs up to 12\.00 entitlement; capacity at takeover is arranged outside nFit and is not verified\.$/, 'the failover figure is unchanged');
unlike($text, qr/can carry|sufficient for takeover/i, 'nothing says the standby can take the load at the fixed figure');
$r = $fx->(own_vp => 1, step_vp => 1, current_ent => 0.10, current_vp => 1);
$text = join(' ', grep { $_ ne '' } UUT::_ha_fixed_standby_action({ primary => 'dbnode1', roleSource => 'configured', fixed => 0.10, fixedSource => 'lpar',
    ownEnt => 0.05, resolved => $r, currentEnt => 0.10, currentVP => 1, poolId => 0 }));
like($text, qr/\(its own setting in nfit\.vms\.cfg\)\. Its own workload needs 0\.05\. /, 'own workload below the fixed figure: stated without "above"');
unlike($text, qr/previous limit/, 'no transition sentence where nothing was held back');
like($text, qr/Capacity at takeover is arranged outside nFit and is not verified\.$/, 'without the primary\'s target in this run, the standing sentence only');
$r = $fx->(own_vp => 1, step_vp => 2, current_ent => 1.50, current_vp => 3, safety_state => 'CAUTION', safety_reasons => 'positive growth adjustment 0.20 cores');
$text = join(' ', grep { $_ ne '' } UUT::_ha_fixed_standby_action({ primary => 'dbnode1', roleSource => 'inferred', fixed => 0.10, fixedSource => 'global',
    ownEnt => 0.30, resolved => $r, currentEnt => 1.50, currentVP => 3, poolId => 0 }));
like($text, qr/The VP reduction is limited this cycle \(CAUTION: positive growth adjustment 0\.20 cores\)\./, 'a limited VP reduction names the verdict and its evidence');
is(UUT::_ha_fixed_not_applied_text(0.10, 'mgsys', 'its own workload needs 3 VPs and 0.10 carries at most 2 (0.05 processors per VP)'),
   'Fixed standby entitlement 0.10 (its managed system\'s default) not applied: its own workload needs 3 VPs and 0.10 carries at most 2 (0.05 processors per VP). '
   . 'The recommendation follows this node\'s workload.', 'not applied: said on the node\'s ordinary row, with the actual cause');

# --- Sustained workload observed: each node on its own ---------------------------------
# PhysC, 15-minute blocks (median of the minute values, 80% coverage), in the node's profile hours and
# its current role period: 0.20 cores or more for two hours without a break, on two distinct days.
my $day = 96;                                   # blocks in a day
my $series = sub
{
    my ($n, $base, %busy) = @_;                 # block index => PhysC level; everything else at the base level
    return [ map { my $c = $busy{$_} // $base; { c_med => $c, c_cov => 1, b_med => $c * 0.5, b_cov => 1 } } (0 .. $n - 1) ];
};
my $run = sub { my ($start, $blocks, $lvl) = @_; return map { ($start + $_ => $lvl) } (0 .. $blocks - 1); };
my $w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(40, 8, 0.20), $run->(2 * $day + 40, 8, 0.45)), 0, 0, undef, undef);
is_deeply([ @$w{qw(Metric Cores Seconds DaysNeeded Days Assessed Observed)} ], ['PhysC', 0.20, 7200, 2, 2, 1, 1],
          '0.20 cores for two hours on two days: sustained workload observed');
is($w->{LongestSeconds}, 7200, 'the duration is elapsed time: eight 15-minute blocks are two hours');
# Each node on its own: a small node beside a large one is judged by what it does, not by its share.
my $small = UUT::_ha_sustained_workload($series->(5 * $day, 0.30), 0, 0, undef, undef);
my $large = UUT::_ha_sustained_workload($series->(5 * $day, 3.00), 0, 0, undef, undef);
is_deeply([ $small->{Observed}, $large->{Observed}, $small->{Days} ], [1, 1, 5], 'unequal nodes that both work: observed on each (0.30 beside 3.00)');
# A quiet standby: housekeeping at 0.05, a monitoring spike every hour, and a daily job of an hour and three quarters.
my %spikes = map { ($_ * 4 => 0.30) } (0 .. 5 * 24 - 1);
my %jobs = map { $run->($_ * $day + 20, 7, 0.40) } (0 .. 4);
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, %spikes, %jobs), 0, 0, undef, undef);
is_deeply([ @$w{qw(Days Observed Assessed)} ], [0, 0, 1], 'a quiet standby with hourly monitoring spikes and a daily job under two hours: not observed');
is($w->{LongestSeconds}, 6300, 'its longest run is reported (an hour and three quarters)');
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(40, 8, 0.25)), 0, 0, undef, undef);
is_deeply([ @$w{qw(Days Observed)} ], [1, 0], 'one day only: not observed');
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(10, 8, 0.25), $run->(30, 8, 0.25), $run->(60, 8, 0.25)), 0, 0, undef, undef);
is_deeply([ @$w{qw(Days Observed)} ], [1, 0], 'several runs on the same day are one day');
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(40, 8, 0.19), $run->($day + 40, 8, 0.19)), 0, 0, undef, undef);
is($w->{Observed}, 0, 'just below the level: not observed');
# Nothing is bridged: a thinly sampled block ends the run.
my $gappy = $series->(5 * $day, 0.05, $run->(40, 8, 0.30), $run->($day + 40, 8, 0.30));
$gappy->[$day + 43]{c_cov} = 0.5;
$w = UUT::_ha_sustained_workload($gappy, 0, 0, undef, undef);
is_deeply([ @$w{qw(Days Observed)} ], [1, 0], 'a block with too few samples ends a run: missing data is not made into activity');
# Profile hours. The same two hours count in general (all hours) and in online hours (08:00 to 17:00);
# hours outside the profile are not read, and a run is not carried across them.
my $online = sub { my ($t) = @_; my $m = int(($t % 86400) / 60); return ($m >= 480 && $m < 1020) ? 1 : 0; };
my $midday = $series->(5 * $day, 0.05, $run->(40, 8, 0.30), $run->($day + 40, 8, 0.30));            # 10:00 to 12:00
is_deeply([ UUT::_ha_sustained_workload($midday, 0, 0, undef, undef)->{Observed}, UUT::_ha_sustained_workload($midday, 0, 0, $online, undef)->{Observed} ],
          [1, 1], 'two hours at midday count under general and under online hours alike');
my $night = $series->(5 * $day, 0.05, $run->(8, 8, 0.30), $run->($day + 8, 8, 0.30));                # 02:00 to 04:00
is_deeply([ UUT::_ha_sustained_workload($night, 0, 0, undef, undef)->{Observed}, UUT::_ha_sustained_workload($night, 0, 0, $online, undef)->{Observed} ],
          [1, 0], 'two hours at night count in general hours and are outside online hours');
my $evening = $series->(5 * $day, 0.05, $run->(64, 8, 0.30), $run->($day + 64, 8, 0.30));            # 16:00 to 18:00
$w = UUT::_ha_sustained_workload($evening, 0, 0, $online, undef);
is_deeply([ $w->{Observed}, $w->{LongestSeconds} ], [0, 3600], 'a run that leaves the online hours counts only as far as they go (one hour of two)');
my $quiet_g = UUT::_ha_sustained_workload($midday, 0, 0, undef, undef);
my $quiet_o = UUT::_ha_sustained_workload($midday, 0, 0, $online, undef);
cmp_ok($quiet_g->{Blocks}, '>', $quiet_o->{Blocks}, 'general hours read more blocks than online hours');
is($quiet_g->{Days}, $quiet_o->{Days}, 'and the extra quiet hours do not dilute the finding: the same two days');
# The current role period only: a node that was active and has been quiet since the handover.
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(0, 2 * $day, 4.0)), 0, 2 * $day, undef, undef);
is_deeply([ @$w{qw(Days Observed Since)} ], [0, 0, 2 * $day * 900], 'a failover followed by a quiet standby: its time as the active node does not count');
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(0, 2 * $day, 4.0), $run->(3 * $day, 8, 0.6), $run->(4 * $day, 8, 0.6)), 0, 2 * $day, undef, undef);
is($w->{Observed}, 1, 'and one that kept working after the handover: observed');
# Missing or coarse evidence: not assessed, which is not a finding either way.
my $coarse = [ map { { c_med => 2.0, c_cov => 0.2, b_med => 1.0, b_cov => 0.2 } } 1 .. 3 * $day ];    # a sample every five minutes
$w = UUT::_ha_sustained_workload($coarse, 0, 0, undef, undef);
is_deeply([ @$w{qw(Assessed Observed Blocks)} ], [0, 0, 0], 'coarse sampling: no block reaches the coverage, the node is not assessed');
like(UUT::_ha_workload_text({ %$w, Hours => 'none' }), qr/^not assessed: no 15-minute block of all hours since 01 Jan 1970 holds enough samples/, 'and the text says so');
$w = UUT::_ha_sustained_workload([], 0, 0, undef, undef);
is_deeply([ @$w{qw(Assessed Observed)} ], [0, 0], 'no samples: not assessed');
# PhysB is put through the same test and reported beside it; PhysC decides.
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(40, 8, 0.30), $run->($day + 40, 8, 0.30)), 0, 0, undef, undef);
is_deeply([ $w->{Observed}, $w->{PhysB}{Days}, $w->{PhysB}{Observed} ], [1, 0, 0], 'PhysC 0.30 with PhysB 0.15: observed on PhysC; the PhysB result is shown apart');
my $no_b = [ map { { c_med => 0.30, c_cov => 1, b_med => undef, b_cov => 0 } } 1 .. 3 * $day ];
$w = UUT::_ha_sustained_workload($no_b, 0, 0, undef, undef);
is_deeply([ $w->{Observed}, $w->{PhysB} ], [1, undef], 'without PhysB the finding stands on PhysC, and PhysB is stated as not collected');
# The settings are policy values and can be set.
$w = UUT::_ha_sustained_workload($series->(6 * $day, 0.05, map { $run->($_ * $day + 40, 4, 0.60) } (0 .. 3)), 0, 0, undef, { Cores => 0.50, Seconds => 3600, Days => 4 });
is_deeply([ @$w{qw(Cores Seconds DaysNeeded Days Observed)} ], [0.50, 3600, 4, 4, 1], 'other settings: 0.50 cores for one hour on four days');
$w = UUT::_ha_sustained_workload($series->(6 * $day, 0.05, map { $run->($_ * $day + 40, 4, 0.60) } (0 .. 2)), 0, 0, undef, { Cores => 0.50, Seconds => 3600, Days => 4 });
is($w->{Observed}, 0, 'and three days are then not enough');
# Wording.
$w = UUT::_ha_sustained_workload($series->(5 * $day, 0.05, $run->(40, 8, 0.20), $run->(2 * $day + 40, 12, 0.45)), 0, 0, undef, undef);
@$w{qw(Hours Period)} = ('online', 'role');
my $said = UUT::_ha_workload_text($w);
is($said, 'PhysC at 0.20 cores or more (15-minute medians) for 2 hours or longer without a break on 2 days; 2 needed; longest run 3 hours; '
   . 'its profile hours (online), its current role period from 01 Jan 1970; PhysB by the same test: 1 day', 'the evidence names metric, level, duration, days, hours and period');
is(UUT::_ha_workload_veto_text(0.10, 'mgsys', $w, 1),
   'Sustained workload observed: idle-standby allocation not applied. Recommendation follows this node\'s workload. '
   . 'Not applied: fixed standby entitlement 0.10 (its managed system\'s default). Evidence: ' . $said . '. '
   . 'Sustained workload is observed on both nodes of this cluster in this period: neither is sized as an idle standby. '
   . 'Which node owns the resource groups is not verified.',
   'the row of an inferred standby with sustained workload: sizing eligibility, no claim about resource-group ownership');
unlike(UUT::_ha_workload_veto_text(0.10, 'mgsys', $w, 0), qr/both nodes/, 'one node only: no statement about the pair');

done_testing();
