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
for my $name (qw(_resolve_current_cycle_pair _ceil_ratio))
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

# The same rise in a 3-core user pool: the pair cannot hold more than the pool.
$r = pair(target_ent => 3.53, target_vp => 4, step_vp => 3, current_ent => 1.50, current_vp => 3, pool_size => 3);
check('rise beyond pool', $r, Ent => 3.00, VP => 4, Status => 'BLOCKED', Binding => [qw(VP_WIDTH POOL_SIZE)]);
valid_pair('rise beyond pool', $r);

# A verified VP limit (HMC profile maximum) stops the VP rise.
$r = pair(target_ent => 3.53, target_vp => 4, step_vp => 3, current_ent => 1.50, current_vp => 3, vp_limit => 3);
check('verified VP limit', $r, Ent => 3.00, VP => 3, Status => 'BLOCKED', Binding => [qw(VP_WIDTH VP_LIMIT)]);
valid_pair('verified VP limit', $r);

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

done_testing();
