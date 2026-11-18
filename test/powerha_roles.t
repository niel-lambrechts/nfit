#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/powerha_roles.t
# PURPOSE:   Regression cover for the PowerHA role detector (v10 stage 3.1b
#            step 6c, detector ha-role-2): 15-minute blocks, reciprocal
#            transitions with persistence, short handovers, segment dominance
#            and the role precedence. Synthetic series only. The live
#            subroutines are extracted from nfit-profile.
# USAGE:     perl test/powerha_roles.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use Scalar::Util qw(looks_like_number);

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_ha_role_rules _ha_median_mad _ha_percentile _ha_blocks _ha_detect_pair _ha_resolve_roles
                 _ha_cluster_policy _ha_standby_target _ha_sizing_plan))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nuse Scalar::Util qw(looks_like_number);\n$code\n1;" or die "harness eval failed: $@\n";

my $T0 = 1788220800;                          # 2026-09-01 00:00 UTC
my $DAY = 86400;
our $DAYS = 6;
my %all_hours = (none => sub { 1 });

# Minute samples from a level function; a deterministic ripple of +/-2%.
sub minutes
{
    my ($level, %opt) = @_;
    my %m;
    for (my $t = $T0; $t < $T0 + $DAYS * $DAY; $t += 60)
    {
        next if ($opt{missing} && $opt{missing}->($t));
        my $c = $level->($t) * (1 + 0.02 * sin($t / 977 + ($opt{phase} // 0)));
        $m{$t} = [1, $c, ($opt{no_b} ? 0 : 1), ($opt{no_b} ? 0 : 0.9 * $c)];
    }
    return \%m;
}
sub states
{
    my ($change_at) = @_;
    return [ { start_epoch => $T0, end_epoch => $T0 + $DAYS * $DAY, metadata => { virtual_cpus => 2, entitlement => 1 } } ]
        unless defined $change_at;
    return [ { start_epoch => $T0, end_epoch => $change_at - 60, metadata => { virtual_cpus => 2, entitlement => 1 } },
             { start_epoch => $change_at, end_epoch => $T0 + $DAYS * $DAY, metadata => { virtual_cpus => 4, entitlement => 2 } } ];
}
sub detect
{
    my ($la, $lb, %opt) = @_;
    my $ba = UUT::_ha_blocks(minutes($la, %{ $opt{a} // {} }), $T0, $T0 + $DAYS * $DAY, states($opt{change_a}));
    my $bb = UUT::_ha_blocks(minutes($lb, phase => 1.7, %{ $opt{b} // {} }), $T0, $T0 + $DAYS * $DAY, states());
    return UUT::_ha_detect_pair('nodeA', 'nodeB', $ba, $bb, $T0, $opt{windows} // \%all_hours);
}
my $at = sub { my ($d, $h, $m) = @_; return $T0 + $d * $DAY + $h * 3600 + ($m // 0) * 60; };

# 1. Steady active node and a quiet peer: no variability is needed.
my $r = detect(sub { 1.00 }, sub { 0.10 });
is($r->{Metric}, 'PhysB', 'both have PhysB: compared on PhysB');
is(scalar @{ $r->{Transitions} }, 0, 'steady: no transition');
is($r->{Segments}[-1]{Outcome}, 'ACTIVE_STANDBY', 'steady active node: active-like');
is($r->{Segments}[-1]{Active}, 'nodeA', 'steady active node: the right node');

# 2. A failover on day 3 at 10:00.
my $sw = $at->(3, 10);
$r = detect(sub { $_[0] < $sw ? 1.0 : 0.1 }, sub { $_[0] < $sw ? 0.1 : 1.0 });
my @sus = grep { $_->{Sustained} } @{ $r->{Transitions} };
is(scalar @sus, 1, 'failover: one sustained transition');
ok($sus[0]{From} <= $sw && $sw < $sus[0]{To}, 'failover: the boundary interval holds the switch time');
is_deeply([ @{ $sus[0] }{qw(Rises Falls Ambiguous)} ], ['nodeB', 'nodeA', 0], 'failover: from A to B');
is(scalar @{ $r->{Segments} }, 2, 'failover: two segments');
is_deeply([ map { $_->{Active} // '' } @{ $r->{Segments} } ], ['nodeA', 'nodeB'], 'failover: A active before, B after');

# 3. Switch and switch back within one day.
my ($out, $back) = ($at->(4, 10), $at->(4, 14));
$r = detect(sub { ($_[0] >= $out && $_[0] < $back) ? 0.1 : 1.0 }, sub { ($_[0] >= $out && $_[0] < $back) ? 1.0 : 0.1 });
@sus = grep { $_->{Sustained} } @{ $r->{Transitions} };
is(scalar @sus, 2, 'switch and switch back: two sustained transitions');
is_deeply([ map { $_->{Rises} } @sus ], ['nodeB', 'nodeA'], 'switch and switch back: in order');
is($r->{Segments}[-1]{Active}, 'nodeA', 'switch and switch back: A active in the latest period');
is($r->{Segments}[1]{Outcome}, 'UNKNOWN', 'a four-hour segment is too short for a verdict');

# 4. A short handover (30 minutes) is kept, not discarded, and cuts no segment.
($out, $back) = ($at->(4, 10), $at->(4, 10, 30));
$r = detect(sub { ($_[0] >= $out && $_[0] < $back) ? 0.1 : 1.0 }, sub { ($_[0] >= $out && $_[0] < $back) ? 1.0 : 0.1 });
is(scalar @{ $r->{Transitions} }, 1, 'short handover: one candidate');
is($r->{Transitions}[0]{Sustained}, 0, 'short handover: not sustained');
is(scalar @{ $r->{Segments} }, 1, 'short handover: no segment cut');
is($r->{Segments}[0]{Active}, 'nodeA', 'short handover: A still active-like');

# 5. Missing data is never idle: a day without B's samples is no handover.
my ($gap_from, $gap_to) = ($at->(2, 0), $at->(3, 0));
$r = detect(sub { 1.0 }, sub { 0.1 }, b => { missing => sub { $_[0] >= $gap_from && $_[0] < $gap_to } });
is(scalar @{ $r->{Transitions} }, 0, 'missing blocks: no transition');
is($r->{Segments}[-1]{Active}, 'nodeA', 'missing blocks: verdict on the supported blocks');

# 6. Both quiet, and both equally busy: unresolved, never a standby.
$r = detect(sub { 0.10 }, sub { 0.10 });
is($r->{Segments}[-1]{Outcome}, 'UNKNOWN', 'both quiet: unresolved');
$r = detect(sub { 1.00 }, sub { 1.00 });
is($r->{Segments}[-1]{Outcome}, 'UNKNOWN', 'both equally busy: unresolved');

# 7. Different working hours (separate resource groups): multiple active-like.
my $hour = sub { int(($_[0] % $DAY) / 3600) };
$r = detect(sub { my $h = $hour->($_[0]); ($h >= 8 && $h < 17) ? 1.0 : 0.2 },
            sub { my $h = $hour->($_[0]); ($h >= 20 || $h < 4) ? 1.0 : 0.2 });
is(scalar(grep { $_->{Sustained} } @{ $r->{Transitions} }), 0, 'different hours: no reciprocal handover');
is($r->{Segments}[-1]{Outcome}, 'MULTIPLE_ACTIVE', 'different hours: multiple active-like');

# 8. A configuration change at the same time is flagged, not credited.
$sw = $at->(3, 10);
$r = detect(sub { $_[0] < $sw ? 1.0 : 0.1 }, sub { $_[0] < $sw ? 0.1 : 1.0 }, change_a => $sw);
@sus = grep { $_->{Sustained} } @{ $r->{Transitions} };
is($sus[0]{Ambiguous}, 1, 'coincident configuration change: ambiguous');

# 9. PhysB missing on one node: both on PhysC.
$r = detect(sub { 1.0 }, sub { 0.1 }, b => { no_b => 1 });
is($r->{Metric}, 'PhysC', 'PhysB on one node only: PhysC on both');

# 10. A quiet but separated active service is still active-like.
$r = detect(sub { 0.30 }, sub { 0.05 });
is($r->{Segments}[-1]{Active}, 'nodeA', 'quiet active service: active-like when separated');

# 11. Profile hours: windows that disagree are unresolved.
$r = detect(sub { my $h = $hour->($_[0]); ($h >= 8 && $h < 17) ? 1.0 : 0.1 },
            sub { my $h = $hour->($_[0]); ($h >= 8 && $h < 17) ? 0.1 : 1.0 },
            windows => { online => sub { my $h = $hour->($_[0]); $h >= 8 && $h < 17 },
                         batch  => sub { my $h = $hour->($_[0]); $h >= 18 || $h < 6 } });
is($r->{Segments}[-1]{Outcome}, 'UNKNOWN', 'profile windows disagree: unresolved');
is($r->{Segments}[-1]{Reason}, 'the profile windows disagree', 'profile windows disagree: the reason');

# 12. A failover in a daily-schedule workload is one transition, not one a day,
#     and a long-lived new regime gives no false switch back.
{
    local $DAYS = 12;
    my $sw2 = $at->(4, 10);
    my $online = sub { my $h = $hour->($_[0]); $h >= 8 && $h < 17 };
    $r = detect(sub { ($_[0] < $sw2 && $online->($_[0])) ? 1.0 : 0.15 }, sub { ($_[0] >= $sw2 && $online->($_[0])) ? 1.0 : 0.15 });
    my @t = grep { $_->{Sustained} } @{ $r->{Transitions} };
    is(scalar @t, 1, 'daily schedule failover: one sustained transition');
    ok($t[0]{From} <= $sw2 && $sw2 < $t[0]{To}, 'daily schedule failover: at the switch');
    is(scalar(grep { $_->{Return} } @{ $r->{Transitions} }), 0, 'long-lived regime: no false switch back');
    is($r->{Segments}[-1]{Active}, 'nodeB', 'daily schedule failover: B active-like afterwards');
}

# 13. A standby running a nightly backup is not a clean standby: unresolved.
$r = detect(sub { my $h = $hour->($_[0]); ($h >= 8 && $h < 17) ? 1.0 : 0.15 },
            sub { my $h = $hour->($_[0]); ($h >= 1 && $h < 3) ? 0.9 : 0.10 });
is($r->{Segments}[-1]{Outcome}, 'UNKNOWN', 'standby with a nightly backup: unresolved, not a confirmed standby');

# 14. Calibration round 1 (ha-role-2): a reciprocal change that does not move the
#     workload is no handover. The node that already leads rises while its peer
#     skips its usual bump: the lead does not flip.
my $bump = sub { my $h = $hour->($_[0]); $h >= 16 && $h < 18 };
my $odd_day = sub { int(($_[0] - $T0) / $DAY) == 4 };
$r = detect(sub { ($odd_day->($_[0]) && $bump->($_[0])) ? 0.60 : 0.28 },
            sub { ($bump->($_[0]) && !$odd_day->($_[0])) ? 0.50 : 0.18 });
is(scalar @{ $r->{Transitions} }, 0, 'no lead flip: no handover (the leader only grew)');

# 15. The falling node keeps most of its level: a workload change, not a handover.
$sw = $at->(3, 10);
$r = detect(sub { $_[0] < $sw ? 1.0 : 0.70 }, sub { $_[0] < $sw ? 0.1 : 1.6 });
is(scalar(grep { $_->{Sustained} } @{ $r->{Transitions} }), 0, 'falling node keeps 70% of its level: no handover');

# 16. A maintenance round trip of 45 minutes that moves the workload: a short candidate.
($out, $back) = ($at->(4, 1), $at->(4, 1, 45));
$r = detect(sub { ($_[0] >= $out && $_[0] < $back) ? 0.05 : 1.0 }, sub { ($_[0] >= $out && $_[0] < $back) ? 1.0 : 0.1 });
is(scalar @{ $r->{Transitions} }, 1, 'round trip: one candidate');
is_deeply([ @{ $r->{Transitions}[0] }{qw(Rises Sustained Blocks)} ], ['nodeB', 0, 3], 'round trip: to B, short, three blocks');

# --- Role precedence ------------------------------------------------------------------
my $latest = { Segments => [ { Outcome => 'ACTIVE_STANDBY', Active => 'nodeA', Reason => '' } ] };
my $none   = { Segments => [ { Outcome => 'UNKNOWN', Reason => 'no sustained separation between the nodes' } ] };
my %cfg;
my $settings_of = sub { $cfg{ $_[0] } // {} };
my %type;
my $type_of = sub { $type{ $_[0] } };
my $roles = UUT::_ha_resolve_roles([qw(nodeA nodeB)], $latest, '', $settings_of, $type_of);
is_deeply([ map { $roles->{$_}{Effective} } qw(nodeA nodeB) ], [qw(active standby)], 'auto: the inferred roles');
is($roles->{nodeB}{Source}, 'inferred', 'auto: labelled as inference');
%cfg = (nodeB => { ha_role => 'standby' });
$roles = UUT::_ha_resolve_roles([qw(nodeA nodeB)], { Segments => [ { Outcome => 'ACTIVE_STANDBY', Active => 'nodeB' } ] }, '', $settings_of, $type_of);
is($roles->{nodeB}{Effective}, 'standby', 'configured standby kept');
like($roles->{nodeB}{Note}, qr/points to an active node; check the configuration/, 'configured standby with contrary evidence: warned');
%cfg = ();
%type = (nodeA => 'PowerHA Primary (ACTIVE) SPLPAR', nodeB => 'PowerHA Standby (ACTIVE) SPLPAR');
$roles = UUT::_ha_resolve_roles([qw(nodeA nodeB)], $none, '', $settings_of, $type_of);
is_deeply([ map { $roles->{$_}{Effective} } qw(nodeA nodeB) ], [qw(active standby)], 'detector unresolved: the VM configuration, supplementary');
like($roles->{nodeA}{Source}, qr/not timestamped/, 'the supplementary source is labelled');
$roles = UUT::_ha_resolve_roles([qw(nodeA nodeB)], { Segments => [ { Outcome => 'ACTIVE_STANDBY', Active => 'nodeB' } ] }, '', $settings_of, $type_of);
is($roles->{nodeB}{Effective}, 'active', 'the detector wins over the VM configuration');
like($roles->{nodeB}{Note}, qr/the VM configuration says standby/, 'and the contradiction is reported');
%type = (nodeA => 'PowerHA Broken/Unknown SPLPAR');
$roles = UUT::_ha_resolve_roles([qw(nodeA nodeB)], $latest, '', $settings_of, $type_of);
is($roles->{nodeA}{Effective}, 'unknown', 'Broken/Unknown: unresolved');
%type = ();
$roles = UUT::_ha_resolve_roles([qw(n1 n2 n3)], undef, 'three or more nodes: roles are not resolved in v10', $settings_of, $type_of);
is($roles->{n2}{Effective}, 'unknown', 'three nodes: unresolved');
$roles = UUT::_ha_resolve_roles([qw(nodeA nodeB)], undef, 'no samples for nodeB in this period', $settings_of, $type_of);
is_deeply([ @{ $roles->{nodeA} }{qw(Effective Note)} ], ['unknown', 'no samples for nodeB in this period'], 'missing peer: unresolved, with the reason');

# --- Sizing (step 6d) --------------------------------------------------------------------
my %set6 = (n1 => { ha_sizing => 'standby_share' }, n2 => { standby_share => 0.30 }, n3 => { ha_sizing => 'independent' });
my $so6 = sub { $set6{ $_[0] } // {} };
my $pol = UUT::_ha_cluster_policy([qw(x y)], $so6, {});
is_deeply([ @$pol{qw(Sizing Share Conflict)} ], ['independent', 0.25, 0], 'policy: independent and 0.25 by default');
$pol = UUT::_ha_cluster_policy([qw(n1 n2)], $so6, {});
is_deeply([ @$pol{qw(Sizing Share)} ], ['standby_share', 0.30], 'policy: set on either node, for the cluster');
$pol = UUT::_ha_cluster_policy([qw(n1 n3)], $so6, {});
is_deeply([ @$pol{qw(Sizing Conflict)} ], ['standby_share', 1], 'policy: disagreement - standby_share (a floor), reported');
is(UUT::_ha_cluster_policy([qw(x y)], $so6, { ha_sizing => 'standby_share', standby_share => 0.2 })->{Share}, 0.2, 'policy: [GLOBAL]');

my $st = UUT::_ha_standby_target(0.12, 1, 2.40, 0.25);
is_deeply([ @$st{qw(Floor TargetEnt TargetVP FloorBinds)} ], [0.60, 0.60, 1, 1], 'standby: 25% of 2.40 is the floor above own 0.12');
$st = UUT::_ha_standby_target(0.90, 2, 2.40, 0.25);
is_deeply([ @$st{qw(TargetEnt TargetVP FloorBinds)} ], [0.90, 2, 0], 'standby: own workload above the share wins (never reduced by the share)');
is(UUT::_ha_standby_target(0.10, 1, 3.33, 0.25)->{Floor}, 0.84, 'standby: the floor is rounded up to 0.01 (0.8325)');
is(UUT::_ha_standby_target(0.10, 1, 5.00, 0.25)->{TargetVP}, 2, 'standby: VPs carry the floor');

my $roles_as = sub { my (%r) = @_; return { map { $_ => { Effective => $r{$_} } } keys %r }; };
my $cl = { Members => [qw(pa pb)], Roles => $roles_as->(pa => 'active', pb => 'standby'), Detection => { Transitions => [] } };
my $plan = UUT::_ha_sizing_plan($cl, { Sizing => 'standby_share' });
is_deeply($plan, { pb => { Treatment => 'standby_floor', Primary => 'pa' } }, 'plan: standby_share floors the standby');
is_deeply(UUT::_ha_sizing_plan($cl, { Sizing => 'independent' }), {}, 'plan: independent changes nothing without a handover');
$cl->{Detection}{Transitions} = [ { Sustained => 1, Rises => 'pa', From => 1000 } ];
$plan = UUT::_ha_sizing_plan($cl, { Sizing => 'independent' });
is_deeply($plan, { pa => { Treatment => 'hold_reductions', Since => 1000, Reason => 'took_over' } }, 'plan: the node that took over has its reductions held');
$cl->{Roles} = $roles_as->(pa => 'unknown', pb => 'unknown');
$plan = UUT::_ha_sizing_plan($cl, { Sizing => 'standby_share' });
is_deeply([ sort keys %$plan ], [qw(pa pb)], 'plan: a handover with unresolved roles holds both');
is($plan->{pa}{Reason}, 'unresolved', 'plan: and says why');
$cl->{Detection}{Transitions} = [];
is_deeply(UUT::_ha_sizing_plan($cl, { Sizing => 'standby_share' }), {}, 'plan: unresolved without a handover - independent, no share');
$cl->{Roles} = $roles_as->(pa => 'multiple', pb => 'multiple');
is_deeply(UUT::_ha_sizing_plan($cl, { Sizing => 'standby_share' }), {}, 'plan: multiple active - independent');
is_deeply(UUT::_ha_sizing_plan({ Members => [qw(a b c)], Roles => {} }, { Sizing => 'standby_share' }), {}, 'plan: three nodes - independent');

done_testing();
