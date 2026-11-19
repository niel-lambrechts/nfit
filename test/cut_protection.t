#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/cut_protection.t
# PURPOSE:   Regression cover for the cut-protection helpers (v10 stage 3.3,
#            shadow): the PhysB/PhysC frontier, the exact bound for any share
#            (rounded up to 0.01, never above installed, undef without a
#            contested minute), the tier shares, the replay loss counters and
#            the whole-minute bounds shared with the candidate test. Synthetic
#            values only. The live subroutines and constants are extracted
#            from nfit-profile.
# USAGE:     perl test/cut_protection.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $constant (qw(%CUT_PROTECTION_SHARE $CUT_PROTECTION_TOP_READINGS))
{
    $src =~ /^(my \Q$constant\E\s*=.*?;)\s*$/m or die "could not extract $constant from $SRC\n";
    $code .= "$1\n";
}
for my $name (qw(_protection_add_minute _protection_bound _protection_share_of _protection_loss_minute _candidate_minute_bounds))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nuse List::Util qw(max min);\n$code\n1;" or die "harness eval failed: $@\n";

my $T0 = 1788220800;                          # 2026-09-01 00:00 UTC
my $new = sub { return { contested => 0, days => {}, frontier => [], top => [] }; };

# --- Frontier ------------------------------------------------------------------
my $p = $new->();
UUT::_protection_add_minute($p, $T0,        0.50, 0.90);
UUT::_protection_add_minute($p, $T0 + 60,   0.40, 0.80);    # dominated by the first
UUT::_protection_add_minute($p, $T0 + 120,  0.70, 0.75);    # higher busy, lower consumed: kept
UUT::_protection_add_minute($p, $T0 + 86400, 0.50, 0.95);   # dominates the first
is($p->{contested}, 4, 'frontier: every contested minute counted');
is(scalar(keys %{ $p->{days} }), 2, 'frontier: contested days');
is_deeply([ sort { $a->[0] <=> $b->[0] } map { [ @$_[0, 1] ] } @{ $p->{frontier} } ], [ [0.50, 0.95], [0.70, 0.75] ],
          'frontier: only the points no other point exceeds in both');

# --- Bound ---------------------------------------------------------------------
is(UUT::_protection_bound($p, 0, 2.0), 0.70, 'bound s=0: highest PhysB');
is(UUT::_protection_bound($p, 1, 2.0), 0.95, 'bound s=1: highest PhysC');
is(UUT::_protection_bound($p, 0.40, 2.0), 0.72, 'bound s=0.40: max(0.50+0.4x0.45, 0.70+0.4x0.05) = 0.72');
is(UUT::_protection_bound($p, 0.85, 2.0), 0.89, 'bound s=0.85: 0.50+0.85*0.45 = 0.8825, rounded up to 0.89');
is(UUT::_protection_bound($p, 1, 0.80), 0.80, 'bound: never above installed');
is(UUT::_protection_bound($p, 1, 0.777), 0.777, 'bound: an installed entitlement off the 0.01 granule is kept exactly');
is(UUT::_protection_bound($new->(), 0, 2.0), undef, 'bound: no contested minute is no bound, not 0');
my $q = $new->();
UUT::_protection_add_minute($q, $T0, 0.5, 0.5);
is(UUT::_protection_bound($q, 0, 2.0), 0.50, 'bound: exact value on the granule is not rounded up');
# Brute force over a random population: the frontier gives the same bound as all minutes.
srand(7);
my $r = $new->();
my @all;
for my $i (1 .. 400)
{
    my $c = 0.2 + rand(1.8);
    my $b = $c * (0.2 + rand(0.8));
    push @all, [ $b, $c ];
    UUT::_protection_add_minute($r, $T0 + 60 * $i, $b, $c);
}
my $all_ok = 1;
for my $share (0, 0.40, 0.65, 0.85, 0.95, 1)
{
    my $brute = List::Util::max(map { $_->[0] + $share * ($_->[1] - $_->[0]) } @all);
    my $cents = $brute * 100;
    my $up = int($cents);
    $up++ if ($cents - $up > 1e-6);
    $all_ok = 0 unless (UUT::_protection_bound($r, $share, 5.0) == $up / 100);
}
ok($all_ok, 'bound: the frontier reproduces the brute-force maximum for every share');
ok(@{ $r->{frontier} } < 40, 'frontier: small for a random population');
is(scalar(@{ $r->{top} }) <= 64, 1, 'top readings: bounded');

# --- Tier shares -----------------------------------------------------------------
is_deeply([ UUT::_protection_share_of('P-99W1') ], [0.95, 'P'], 'share: P');
is_deeply([ UUT::_protection_share_of('G1-99W5') ], [0.85, 1], 'share: tier 1');
is_deeply([ UUT::_protection_share_of('O2-98W10') ], [0.65, 2], 'share: tier 2');
is_deeply([ UUT::_protection_share_of('B3-95W15') ], [0.40, 3], 'share: tier 3');
is_deeply([ UUT::_protection_share_of('G4-90W15') ], [0.00, 4], 'share: tier 4');
is_deeply([ UUT::_protection_share_of('Peak') ], [undef, undef], 'share: not a tier profile');

# --- Loss counters ----------------------------------------------------------------
my $stats = [];
my @th = (0.50, 0.90);
UUT::_protection_loss_minute($stats, \@th, $T0,       0.80, 1.20, 1.00);   # busy 0.30 above 0.50; consumed min(1.2,1)=1.0
UUT::_protection_loss_minute($stats, \@th, $T0 + 60,  0.80, 0.95, 1.00);   # adjacent: one episode
UUT::_protection_loss_minute($stats, \@th, $T0 + 600, 0.40, 0.60, 1.00);   # busy below 0.50; consumed 0.10 above
is($stats->[0]{busy}{minutes}, 2, 'loss: busy minutes above the threshold');
is(sprintf('%.2f', $stats->[0]{busy}{coreMinutes}), '0.60', 'loss: busy core-minutes');
is($stats->[0]{busy}{episodes}, 1, 'loss: adjacent minutes are one episode');
is($stats->[0]{busy}{longest}, 2, 'loss: longest episode');
is($stats->[0]{consumed}{minutes}, 3, 'loss: consumed minutes');
is(sprintf('%.2f', $stats->[0]{consumed}{coreMinutes}), '1.05', 'loss: consumed capped at installed (0.50 + 0.45 + 0.10)');
is($stats->[0]{consumed}{episodes}, 2, 'loss: a gap starts a new episode');
is($stats->[1]{busy}, undef, 'loss: nothing above the higher threshold for busy');
is(sprintf('%.2f', $stats->[1]{consumed}{coreMinutes}), '0.15', 'loss: consumed above 0.90 (0.10 + 0.05)');

# --- Whole-minute bounds ------------------------------------------------------------
my @states = ({ start_epoch => $T0 }, { start_epoch => $T0 + 3630 });
is_deeply([ UUT::_candidate_minute_bounds(\@states, $states[0], 1, $T0, $T0 + 7200) ], [ $T0, $T0 + 3600 ],
          'bounds: the minute holding the next state start is excluded');
is_deeply([ UUT::_candidate_minute_bounds(\@states, $states[1], 2, $T0, $T0 + 7200) ], [ $T0 + 3660, $T0 + 7200 ],
          'bounds: a later state starts at its first whole minute');
is_deeply([ UUT::_candidate_minute_bounds([ { start_epoch => $T0 } ], { start_epoch => $T0 }, 1, $T0, $T0) ], [],
          'bounds: an empty interval');

done_testing();
