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
                 _ha_standby_policy _ha_sizing_plan _ha_fixed_standby_pair _ha_fixed_standby_action))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
my ($min_ent_per_vp) = $src =~ /^my \$MIN_ENT_PER_VP\s*=\s*([0-9.]+);/m or die "MIN_ENT_PER_VP not found\n";
my ($ratio_max)      = $src =~ /^my \$VCPU_RATIO_MAX\s*=\s*([0-9.]+);/m or die "VCPU_RATIO_MAX not found\n";
eval qq{
package UUT;
use Scalar::Util qw(looks_like_number);
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

# --- The pair: entitlement in one step; VPs by the existing VP rules ----------------
my ($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 2, current_ent => 3.00, current_vp => 4 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $why ], [0.10, 2, undef], 'installed 3.00 / 4, VP rules allow 2 this cycle: 0.10 on 2 VPs, in one step');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 3, current_ent => 3.00, current_vp => 4 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $pair->{Status} ], [3.00, 4, 'HOLD'], 'VP rules allow only 3: the installed pair is kept');
is($why, '0.10 carries at most 2 VPs (0.05 processors per VP) and the VP count of this cycle is 3, which needs at least 0.15', 'the conflict is stated with both figures and what the VPs need');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => undef, current_ent => 0.40, current_vp => 4 });
is_deeply([ $pair->{Ent}, $pair->{VP}, defined $why ? 1 : 0 ], [0.40, 4, 1], 'no VP step this cycle and 4 VPs installed: no VP reduction is invented');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 1, current_ent => 0.05, current_vp => 1 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $why ], [0.10, 1, undef], 'an override, not a ceiling: below the fixed figure the entitlement is raised to it');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 2, current_ent => 0.10, current_vp => 2 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $pair->{Status}, $why ], [0.10, 2, 'NO_CHANGE', undef], 'already there: no change, no conflict');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 2.50, step_vp => 1, current_ent => 0.50, current_vp => 1 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $why ], [2.50, 3, undef], 'a fixed figure wider than the VPs: VPs added to carry it, as for any entitlement');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 2.50, step_vp => 1, current_ent => 0.50, current_vp => 1, pool_size => 2 });
is_deeply([ $pair->{Ent}, $pair->{VP} ], [0.50, 1], 'a fixed figure above the pool\'s size: installed pair kept');
like($why, qr/^it is above the size of its pool \(2\)$/, 'and the pool named as the reason');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 1, current_ent => undef, current_vp => undef });
is($pair->{Ent}, undef, 'no installed pair: nothing is resolved');

# --- The staging guard of ordinary sizing is kept; the exception is the fixed figure only ----
# Ordinary sizing is two-stage: VPs are reduced first and the entitlement is reassessed after the
# narrower LPAR has been observed. While a VP reduction is on its way, a cut of the entitlement
# stops at what the more permissive of today's VP:ENT ratio and 2.00 carries on this cycle's VPs.
my $ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 1, step_vp => 3, current_ent => 3.00, current_vp => 4 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP}, $ordinary->{Status} ], [1.01, 3, 'STAGED'],
          'ordinary staged reduction 4 -> 3 VPs towards 1: the entitlement cut stops at 1.01 (ratio 2.00, the more permissive of 1.33 and 2.00)');
ok((grep { $_ eq 'RATIO_GUARD' } @{ $ordinary->{Binding} }), 'and the staging guard is named as what shaped it');
$ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 2, step_vp => 14, current_ent => 4.00, current_vp => 15 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP} ], [3.47, 14], 'an over-wide LPAR (15 VPs on 4.00): today\'s ratio 3.75 is kept, not forced to 2.00');
$ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 1, step_vp => 1, current_ent => 3.00, current_vp => 4 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP}, scalar(grep { $_ eq 'RATIO_GUARD' } @{ $ordinary->{Binding} }) ], [0.10, 1, 0],
          'ordinary sizing that reaches its VP destination in this cycle: no staging guard');
$ordinary = UUT::_resolve_current_cycle_pair({ target_ent => 0.10, target_vp => 1, step_vp => 1, current_ent => 0.50, current_vp => 1 });
is_deeply([ $ordinary->{Ent}, $ordinary->{VP} ], [0.10, 1], 'a one-VP LPAR has no narrower step: a small entitlement is not raised for a ratio');
# The fixed standby figure on the same inputs: never raised by the guard; the VP count is still the permitted step.
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 1, current_ent => 0.50, current_vp => 1 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $why ], [0.10, 1, undef], 'fixed standby 0.10 on 1 VP: valid, not inflated');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.20, step_vp => 3, current_ent => 3.00, current_vp => 4 });
is_deeply([ $pair->{Ent}, $pair->{VP}, $why ], [0.20, 3, undef],
          'fixed standby 0.20 during a staged VP reduction 4 -> 3: the figure stands (ordinary sizing would hold 1.01), VPs are the permitted step');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.20, step_vp => 4, current_ent => 3.00, current_vp => 4 });
is_deeply([ $pair->{Ent}, $pair->{VP} ], [0.20, 4], 'the VP rules hold the VPs (no step): the fixed figure removes no VP of its own');

# --- The planning action ------------------------------------------------------------
($pair) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 2, current_ent => 3.00, current_vp => 4 });
my $text = join(' ', grep { $_ ne '' } UUT::_ha_fixed_standby_action({ primary => 'dbnode1', roleSource => 'inferred', fixed => 0.10, fixedSource => 'mgsys',
    ownEnt => 0.30, pair => $pair, currentEnt => 3.00, currentVP => 4, poolId => 0, primaryEnt => 12 }));
like($text, qr/^PowerHA standby for dbnode1 \(inferred\): fixed standby entitlement 0\.10 \(its managed system's default\)\. Its own workload needs 0\.30, above the standby allocation\./,
     'the row names the policy, its source, and the own-workload requirement above it');
like($text, qr/entitlement 3\.00 to 0\.10/, 'the entitlement change');
like($text, qr/VPs 4 to 2/, 'the VP step of this cycle');
like($text, qr/At failover it needs up to 12\.00 entitlement; capacity at takeover is arranged outside nFit and is not verified\.$/, 'the failover figure is unchanged');
unlike($text, qr/can carry|sufficient for takeover/i, 'nothing says the standby can take the load at the fixed figure');
$text = join(' ', grep { $_ ne '' } UUT::_ha_fixed_standby_action({ primary => 'dbnode1', roleSource => 'configured', fixed => 0.10, fixedSource => 'lpar',
    ownEnt => 0.05, pair => { Ent => 0.10, VP => 1, Status => 'NO_CHANGE' }, currentEnt => 0.10, currentVP => 1, poolId => 0 }));
like($text, qr/\(its own setting in nfit\.vms\.cfg\)\. Its own workload needs 0\.05\. /, 'own workload below the fixed figure: stated without "above"');
like($text, qr/Capacity at takeover is arranged outside nFit and is not verified\.$/, 'without the primary\'s target in this run, the standing sentence only');
($pair, $why) = UUT::_ha_fixed_standby_pair({ fixed => 0.10, step_vp => 3, current_ent => 3.00, current_vp => 4 });
$text = join(' ', grep { $_ ne '' } UUT::_ha_fixed_standby_action({ primary => 'dbnode1', roleSource => 'inferred', fixed => 0.10, fixedSource => 'global',
    ownEnt => 0.30, pair => $pair, conflict => $why, currentEnt => 3.00, currentVP => 4, poolId => 0 }));
like($text, qr/fixed standby entitlement 0\.10 \(the \[GLOBAL\] default\) cannot be set this cycle: 0\.10 carries at most 2 VPs \(0\.05 processors per VP\) and the VP count of this cycle is 3, which needs at least 0\.15\. The installed 3\.00 on 4 VPs is kept; no change\./,
     'a conflict: both figures, the installed pair kept');

done_testing();
