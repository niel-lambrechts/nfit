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
    _ha_role_rules _ha_sustained_workload _ha_workload_text _ha_workload_veto_text))
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

# --- Sustained workload: positive evidence that a node is not an idle standby ----------
# 15-minute blocks; a block counts at 80% coverage and a median of 1.00 cores or more; four in a row
# are a sustained hour; the days on which one completes are counted; two days make the verdict.
my $day = 96;
my $series = sub
{
    my ($n, %busy) = @_;          # block index => level; everything else quiet at 0.05
    return [ map { { b_med => ($busy{$_} // 0.05), b_cov => 1, c_med => ($busy{$_} // 0.05) + 0.10, c_cov => 1 } } (0 .. $n - 1) ];
};
my $hour = sub { my ($start, $lvl) = @_; return map { ($start + $_ => $lvl) } (0 .. 3); };
my $w = UUT::_ha_sustained_workload($series->(5 * $day, $hour->(10, 1.00), $hour->(2 * $day + 40, 3.2)), 0, 0, 'PhysB');
is_deeply([ @$w{qw(Basis Days Bearing)} ], ['PhysB', 2, 1], 'one busy core for a full hour on two days: sustained workload');
$w = UUT::_ha_sustained_workload($series->(5 * $day, $hour->(10, 1.4)), 0, 0, 'PhysB');
is_deeply([ @$w{qw(Days Bearing)} ], [1, 0], 'one day only: not enough');
$w = UUT::_ha_sustained_workload($series->(5 * $day, $hour->(10, 1.4), $hour->(20, 1.4), $hour->(60, 1.4)), 0, 0, 'PhysB');
is_deeply([ @$w{qw(Days Bearing)} ], [1, 0], 'several hours on the same day are one day');
$w = UUT::_ha_sustained_workload($series->(5 * $day, (map { ($_ => 1.4) } 10 .. 12), (map { ($_ => 1.4) } $day + 10 .. $day + 12)), 0, 0, 'PhysB');
is_deeply([ @$w{qw(Days Bearing)} ], [0, 0], 'three blocks are not a sustained hour');
$w = UUT::_ha_sustained_workload($series->(5 * $day, $hour->(10, 0.95), $hour->($day + 10, 0.95)), 0, 0, 'PhysB');
is($w->{Bearing}, 0, 'below one core: no verdict (use above entitlement is not the test)');
my $gappy = $series->(5 * $day, $hour->(10, 1.4), $hour->($day + 10, 1.4));
$gappy->[$day + 11]{b_cov} = 0.5;
$w = UUT::_ha_sustained_workload($gappy, 0, 0, 'PhysB');
is_deeply([ @$w{qw(Days Bearing)} ], [1, 0], 'a thinly sampled block ends a run: it is never taken as busy');
# The node's current role period only: its time as the active node does not count against it.
$w = UUT::_ha_sustained_workload($series->(5 * $day, (map { ($_ => 4.0) } 0 .. 2 * $day - 1)), 0, 2 * $day, 'PhysB');
is_deeply([ @$w{qw(Days Bearing Since)} ], [0, 0, 2 * $day * 900], 'a node that was active and has been quiet since the handover: no sustained workload');
$w = UUT::_ha_sustained_workload($series->(5 * $day, (map { ($_ => 4.0) } 0 .. 2 * $day - 1), $hour->(3 * $day, 1.2), $hour->(4 * $day, 1.2)), 0, 2 * $day, 'PhysB');
is($w->{Bearing}, 1, 'and one that kept working after it: sustained workload');
# Without PhysB the consumed cores are read, and the weaker basis is stated.
$w = UUT::_ha_sustained_workload($series->(5 * $day, $hour->(10, 0.95), $hour->($day + 10, 0.95)), 0, 0, 'PhysC');
is_deeply([ @$w{qw(Basis Bearing)} ], ['PhysC', 1], 'PhysC (0.95 busy shown as 1.05 consumed here): the verdict rests on consumed cores');
is(UUT::_ha_workload_text($w), 'at least 1.00 cores consumed for a full hour on 2 days of its current role period (PhysC: busy time is not collected for both nodes, a weaker basis)',
   'and says so');
my $none = UUT::_ha_sustained_workload([ map { { c_med => undef, c_cov => 0, b_med => undef, b_cov => 0 } } 1 .. 200 ], 0, 0, 'PhysB');
is_deeply([ @$none{qw(Days Bearing)} ], [0, 0], 'no samples: no verdict either way (never a finding of idleness)');
$w = UUT::_ha_sustained_workload($series->(5 * $day, $hour->(10, 1.00), $hour->(2 * $day + 40, 3.2)), 0, 0, 'PhysB');
is(UUT::_ha_workload_veto_text(0.10, 'mgsys', $w, 1),
   'Substantial ongoing workload conflicts with an idle-standby assumption (at least 1.00 busy cores for a full hour on 2 days of its current role period (PhysB)). '
   . 'Fixed standby allocation 0.10 (its managed system\'s default) not applied; recommendation follows this node\'s workload. '
   . 'Both nodes of this cluster carry sustained workload; which node owns the resource groups is not verified.',
   'the row of an inferred standby that carries work, both nodes working: no claim about resource-group ownership');
unlike(UUT::_ha_workload_veto_text(0.10, 'mgsys', $w, 0), qr/Both nodes/, 'one node only: no statement about the pair');

done_testing();
