#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/current_cycle_pair.t
# PURPOSE:   Regression cover for _resolve_current_cycle_pair (v10 stage 3.1):
#            the entitlement and VP pair to configure in the current cycle.
#            The live subroutines are extracted from nfit-profile and evaluated
#            in an isolated package, so the assertions run against production
#            source, not a copy.
# USAGE:     perl test/current_cycle_pair.t   (or through dev/run-tests)
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
for my $name (qw(_resolve_current_cycle_pair _ceil_ratio _current_cycle_action_text))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
my ($min_ent_per_vp) = $src =~ /^my \$MIN_ENT_PER_VP\s*=\s*([0-9.]+);/m or die "MIN_ENT_PER_VP not found\n";
my ($ratio_max)      = $src =~ /^my \$VCPU_RATIO_MAX\s*=\s*([0-9.]+);/m or die "VCPU_RATIO_MAX not found\n";

my $harness = qq{
package UUT;
use Scalar::Util qw(looks_like_number);
our \$FLOAT_EPSILON = 1e-9;
our \$MIN_ENT_PER_VP = $min_ent_per_vp;
our \$VCPU_RATIO_MAX = $ratio_max;
} . $code . "\n1;\n";
eval $harness or die "harness eval failed: $@\n";

sub pair
{
    my (%terms) = @_;
    return UUT::_resolve_current_cycle_pair(\%terms);
}

sub check
{
    my ($label, $got, %want) = @_;
    for my $key (sort keys %want)
    {
        my $value = $got->{$key};
        if (ref $want{$key} eq 'ARRAY')
        {
            is_deeply([ sort @{ $value // [] } ], [ sort @{ $want{$key} } ], "$label: $key");
        }
        elsif (defined $want{$key} && looks_like_number($want{$key}) && defined $value && looks_like_number($value))
        {
            ok(abs($value - $want{$key}) < 1e-9, "$label: $key = $want{$key}") or diag("got $value");
        }
        else
        {
            is($value, $want{$key}, "$label: $key");
        }
    }
}

# Every non-held result must be a valid PowerVM pair.
sub valid_pair
{
    my ($label, $got) = @_;
    ok($got->{Ent} <= $got->{VP} + 1e-9 && $got->{Ent} >= $min_ent_per_vp * $got->{VP} - 1e-9,
       "$label: 0.05 x VPs <= ENT <= VPs") or diag("ENT $got->{Ent}, VPs $got->{VP}");
}

my $r;

# Over-wide LPAR, deep cut: the VP step is rate-limited (15 -> 14), and the
# entitlement follows only as far as today's VP:ENT ratio (3.75) allows.
$r = pair(target_ent => 0.10, target_vp => 2, step_vp => 14, current_ent => 4.00, current_vp => 15);
check('deep cut', $r, Ent => 3.47, VP => 14, Status => 'STAGED', Order => 'EITHER',
      Binding => [qw(PLATFORM_MINIMUM RATIO_GUARD)], ReviewVP => undef);
valid_pair('deep cut', $r);

# Entitlement rising beyond the VPs: VPs rise to carry it, VPs first.
$r = pair(target_ent => 3.53, target_vp => 4, step_vp => 3, current_ent => 1.50, current_vp => 3);
check('rise beyond VPs', $r, Ent => 3.53, VP => 4, Status => 'APPLY', Order => 'VP_FIRST',
      Binding => ['VP_WIDTH'], NeedVP => 4);
valid_pair('rise beyond VPs', $r);

# The same rise in a 3-core user pool: the pair cannot hold more than the pool,
# and a fourth VP would add no reach while the pool binds.
$r = pair(target_ent => 3.53, target_vp => 4, step_vp => 3, current_ent => 1.50, current_vp => 3, pool_size => 3);
check('rise beyond pool', $r, Ent => 3.00, VP => 3, Status => 'BLOCKED', Binding => [qw(POOL_SIZE)], NeedVP => 3,
      ReviewVP => undef, TargetEnt => 3.53);
valid_pair('rise beyond pool', $r);

# Reproduction (peer review, 18 November): the minimum for the VPs must not be
# lost when the pool clips the entitlement. Formerly 1.00 on 30 VPs (invalid).
$r = pair(target_ent => 30, target_vp => 30, step_vp => 10, current_ent => 0.50, current_vp => 10, pool_size => 1);
check('pool clips a large rise', $r, Ent => 1.00, VP => 10, Status => 'BLOCKED', Binding => [qw(POOL_SIZE)],
      ReviewVP => undef, TargetEnt => 30, TargetVP => 30);
valid_pair('pool clips a large rise', $r);

# More VPs than the pool maximum can carry at 0.05 each: no valid pair, so the
# installed pair is kept, blocked, with the target visible.
$r = pair(target_ent => 2.00, target_vp => 30, step_vp => 30, current_ent => 1.00, current_vp => 30, pool_size => 1);
check('no valid pair in the pool', $r, Ent => 1.00, VP => 30, Status => 'BLOCKED', Order => 'NONE',
      Reason => 'no valid pair within the pool maximum', TargetEnt => 2.00);
is(UUT::_current_cycle_action_text($r, 1.00, 30, 7),
   'No change: no valid pair within the pool maximum. Pool 7 must allow at least 2 cores to reach the 2.00 target.',
   'no valid pair: the action states why and the pool prerequisite');

# A verified VP limit (HMC profile maximum) stops the VP rise.
$r = pair(target_ent => 3.53, target_vp => 4, step_vp => 3, current_ent => 1.50, current_vp => 3, vp_limit => 3);
check('verified VP limit', $r, Ent => 3.00, VP => 3, Status => 'BLOCKED', Binding => [qw(VP_WIDTH VP_LIMIT)]);
valid_pair('verified VP limit', $r);

# The current-cycle pair meets the tier floor at its own VPs (v10 step 3b
# correction): a cut whose target relies on a second VP that is only a review
# item stops where one VP needs it.
my $floor = sub { my ($v) = @_; return ($v >= 2) ? 0.10 : 0.30; };
$r = pair(target_ent => 0.23, target_vp => 2, step_vp => 1, current_ent => 0.50, current_vp => 1, tier_floor => $floor);
check('cut stops at the floor of its own VPs', $r, Ent => 0.30, VP => 1, Status => 'STAGED', Binding => [qw(TIER_FLOOR)],
      ReviewVP => 2, TargetEnt => 0.23);
valid_pair('cut stops at the floor of its own VPs', $r);
like(UUT::_current_cycle_action_text($r, 0.50, 1, 0), qr/^Reduce entitlement 0\.50 to 0\.30\. Cut limited to 0\.30 by the tier's pool-borrowing limit on 1 VP\./,
     'the action states where the cut stops and why');
$r = pair(target_ent => 0.23, target_vp => 2, step_vp => 1, current_ent => 0.25, current_vp => 1, tier_floor => sub { 1.00 });
check('a floor above installed holds the cut', $r, Ent => 0.25, VP => 1, Binding => [qw(TIER_FLOOR)]);
$r = pair(target_ent => 0.60, target_vp => 1, step_vp => 1, current_ent => 0.80, current_vp => 1, tier_floor => sub { 0.30 });
check('a floor below the target changes nothing', $r, Ent => 0.60, Status => 'APPLY', Binding => []);
$r = pair(target_ent => 1.20, target_vp => 2, step_vp => 2, current_ent => 0.80, current_vp => 2, tier_floor => sub { 2.00 });
check('a raise is not touched by the floor', $r, Ent => 1.20, Binding => []);

# A staged target below the platform minimum of its VPs is stated as the pair
# PowerVM accepts, with the workload figure beside it.
$r = pair(target_ent => 0.01, target_vp => 2, step_vp => 1, current_ent => 0.50, current_vp => 1);
like(UUT::_current_cycle_action_text($r, 0.50, 1, 0),
     qr/Target 0\.10 entitlement on 2 VPs over later cycles \(the platform minimum for 2 VPs; the workload figure is 0\.01\)\./,
     'staged target stated as a configurable pair');
unlike(UUT::_current_cycle_action_text($r, 0.50, 1, 0), qr/Target 0\.01 entitlement on/, 'no invalid pair in the action');

# A simple cut that reaches its target in one cycle.
$r = pair(target_ent => 1.20, target_vp => 3, step_vp => 3, current_ent => 2.00, current_vp => 4);
check('simple cut', $r, Ent => 1.20, VP => 3, Status => 'APPLY', Order => 'EITHER', Binding => []);

# The same cut while VPs are still being staged: the 2:1 guard allows 1.01 or
# more on 3 VPs, so the target 1.20 stands.
$r = pair(target_ent => 1.20, target_vp => 2, step_vp => 3, current_ent => 2.00, current_vp => 4);
check('cut while staging', $r, Ent => 1.20, VP => 3, Status => 'STAGED', Binding => []);

# Micro-partition: the PowerVM minimum binds before the (looser) ratio guard.
$r = pair(target_ent => 0.10, target_vp => 2, step_vp => 4, current_ent => 0.25, current_vp => 5);
check('platform minimum', $r, Ent => 0.20, VP => 4, Status => 'STAGED', Binding => ['PLATFORM_MINIMUM']);
valid_pair('platform minimum', $r);

# Evidence asks for more VPs than the entitlement needs: a review, not a change.
$r = pair(target_ent => 1.00, target_vp => 4, step_vp => 2, current_ent => 2.00, current_vp => 2);
check('evidence-only VP increase', $r, Ent => 1.00, VP => 2, ReviewVP => 4, Binding => []);
valid_pair('evidence-only VP increase', $r);

# Target equal to the installed pair.
$r = pair(target_ent => 2.00, target_vp => 4, step_vp => 4, current_ent => 2.00, current_vp => 4);
check('no change', $r, Status => 'NO_CHANGE', Order => 'NONE', Ent => 2.00, VP => 4);

# Entitlements are compared at the CSV's two decimals.
$r = pair(target_ent => 1.204, target_vp => 2, step_vp => 2, current_ent => 1.2, current_vp => 2);
check('two decimals', $r, Status => 'NO_CHANGE', Ent => 1.20);

# Neither single-step order is valid: one combined DLPAR operation.
$r = pair(target_ent => 0.06, target_vp => 2, step_vp => 2, current_ent => 3.00, current_vp => 40);
check('combined order', $r, Order => 'COMBINED', VP => 2, Ent => 0.10);
valid_pair('combined order', $r);

# Held and unknown cases keep the installed pair.
$r = pair(target_ent => 0.50, target_vp => 1, step_vp => 1, current_ent => 1.00, current_vp => 2,
          hold_reason => 'excluded from automatic change: VIOS');
check('held role', $r, Status => 'HOLD', Ent => 1.00, VP => 2, Reason => 'excluded from automatic change: VIOS');
$r = pair(target_ent => 0.50, target_vp => 1, step_vp => 1, current_vp => 2);
check('unknown installed entitlement', $r, Status => 'HOLD', Reason => 'installed entitlement or VP count unknown',
      Ent => undef, VP => undef);
$r = pair(target_ent => undef, step_vp => 1, current_ent => 1.00, current_vp => 2);
check('no target', $r, Status => 'HOLD', Reason => 'no result for this profile', Ent => undef, VP => undef);

# The guard never raises the entitlement above today's while VPs fall, and the
# result is always a valid pair: a sweep over small configurations.
my ($sweeps, $bad) = (0, 0);
for my $cv (1 .. 16)
{
    for my $ce (map { $_ / 20 } 1 .. 20 * $cv)
    {
        next if ($ce < $min_ent_per_vp * $cv);
        for my $step (1 .. $cv)
        {
            for my $te (0.05, 0.3, 0.75, 1.5, 3.2, 7.9)
            {
                my $g = pair(target_ent => $te, target_vp => 1, step_vp => $step, current_ent => $ce, current_vp => $cv);
                $sweeps++;
                my $ok = $g->{Ent} <= $g->{VP} + 1e-9 && $g->{Ent} >= $min_ent_per_vp * $g->{VP} - 1e-9;
                $ok &&= ($g->{Ent} <= $ce + 1e-9) if ($te <= $ce && $g->{VP} <= $cv);
                $bad++ unless $ok;
            }
        }
    }
}
is($bad, 0, "sweep: $sweeps pairs valid, no entitlement raised during a reduction");

# With every limit and their combinations: the returned pair is always valid
# where it changes, never above a user pool's size, and VPs never rise past
# what the entitlement within the pool needs (peer review, 18 November).
my ($lsweeps, $lbad, @lfirst) = (0, 0);
for my $cv (1 .. 12)
{
    for my $ce (map { $_ / 4 } 1 .. 4 * $cv)
    {
        for my $step (grep { $_ <= $cv } (1, 2, 3, 5, 8, 12))
        {
            for my $te (0.05, 0.3, 1.5, 3.2, 7.9, 30)
            {
                for my $pool (undef, 1, 2, 4)
                {
                    next if (defined $pool && $ce > $pool);     # installed already within the pool
                    for my $limit (undef, $cv + 1)
                    {
                        for my $tvp (1, 30)
                        {
                            my $g = pair(target_ent => $te, target_vp => $tvp, step_vp => $step, current_ent => $ce,
                                         current_vp => $cv, pool_size => $pool, vp_limit => $limit);
                            $lsweeps++;
                            my $changed = (abs($g->{Ent} - $ce) >= 0.005 || $g->{VP} != $cv);
                            my $ok = !$changed || ($g->{Ent} <= $g->{VP} + 1e-9 && $g->{Ent} >= $min_ent_per_vp * $g->{VP} - 1e-9);
                            $ok &&= ($g->{Ent} <= $pool + 1e-9 || !$changed) if (defined $pool);
                            if ($g->{VP} > $cv)
                            {
                                my $reach = (defined $pool && $te > $pool) ? $pool : $te;
                                my $need = int($reach); $need++ if ($reach - $need > 1e-9);
                                $ok &&= ($g->{VP} <= $need);
                                $ok &&= ($g->{VP} <= $limit) if (defined $limit);
                            }
                            if (!$ok) { $lbad++; push @lfirst, "$te/$tvp/$step/$ce/$cv/" . ($pool // '-') . '/' . ($limit // '-')
                                . " -> $g->{Ent}/$g->{VP}" if (@lfirst < 5); }
                        }
                    }
                }
            }
        }
    }
}
is($lbad, 0, "limit sweep: $lsweeps pairs valid within every limit") or diag(join("\n", @lfirst));

# --- Coarse pool evidence: the entitlement cut is held, the target is kept (v10 stage 3.4) ---
{
    my $held = UUT::_resolve_current_cycle_pair({ target_ent => 0.12, target_vp => 1, step_vp => 1, current_ent => 0.50, current_vp => 1,
                                                  pool_size => 4, hold_cut => 15 });
    is($held->{Ent}, 0.50, 'held: the installed entitlement stays this cycle');
    is($held->{TargetEnt}, 0.12, '  and the target is as calculated');
    ok((grep { $_ eq 'COARSE_EVIDENCE' } @{ $held->{Binding} }), '  bound by the coarse evidence');
    is(UUT::_current_cycle_action_text($held, 0.50, 1, 7),
       'No change. Hold the cut to 0.12: 15-minute data cannot assess brief pool competition. Obtain finer evidence or authorise a monitored trial.',
       '  the action names the target and what would release the cut');

    # With a VP reduction staged: the VPs follow the entitlement that is kept.
    $held = UUT::_resolve_current_cycle_pair({ target_ent => 0.60, target_vp => 1, step_vp => 3, current_ent => 2.00, current_vp => 4, hold_cut => 15 });
    is_deeply([ $held->{Ent}, $held->{VP}, $held->{Status} ], [ 2.00, 3, 'STAGED' ], 'held with a VP step: 2.00 on 3 VPs');
    like(UUT::_current_cycle_action_text($held, 2.00, 4, 0),
         qr/^Reduce VPs 4 to 3\. Hold the cut: 15-minute data cannot assess brief pool competition\. Obtain finer evidence or authorise a monitored trial\. Target 0\.60 entitlement on 1 VP over later cycles\./,
         '  the staged target follows the hold');
    $held = UUT::_resolve_current_cycle_pair({ target_ent => 0.60, target_vp => 1, step_vp => 1, current_ent => 2.00, current_vp => 4, hold_cut => 15 });
    is($held->{VP}, 2, 'the VPs are not cut below what the kept entitlement needs');

    # An increase is not held, and without the term a cut goes ahead as before.
    my $up = UUT::_resolve_current_cycle_pair({ target_ent => 1.00, target_vp => 2, step_vp => 2, current_ent => 0.50, current_vp => 2, hold_cut => 15 });
    is($up->{Ent}, 1.00, 'an increase goes ahead');
    ok(!(grep { $_ eq 'COARSE_EVIDENCE' } @{ $up->{Binding} }), '  and is not marked');
    my $cut = UUT::_resolve_current_cycle_pair({ target_ent => 0.12, target_vp => 1, step_vp => 1, current_ent => 0.50, current_vp => 1 });
    is($cut->{Ent}, 0.12, 'without coarse evidence the cut is applied');
}

done_testing();
