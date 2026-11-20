#!/usr/bin/env perl
# ==============================================================================
# Unit tests for the no-contention exemption (decision F21): the decision
# _peak_uplift_gate_of takes from the contested minutes and each day's
# (expected, classified) minutes. The live subroutine and its two thresholds
# are extracted from nfit-profile.
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
$src =~ /^(sub _peak_uplift_gate_of\s*\{.*?^\})/ms or die "could not extract _peak_uplift_gate_of\n";
my $code = $1;
my ($min_all) = $src =~ /^my \$PEAK_UPLIFT_GATE_MIN_CLASSIFIED\s*=\s*([0-9.]+);/m or die "window threshold not found\n";
my ($min_day) = $src =~ /^my \$PEAK_UPLIFT_GATE_MIN_DAY_CLASSIFIED\s*=\s*([0-9.]+);/m or die "day threshold not found\n";
eval "package UUT; our \$PEAK_UPLIFT_GATE_MIN_CLASSIFIED = $min_all; our \$PEAK_UPLIFT_GATE_MIN_DAY_CLASSIFIED = $min_day; $code 1;"
    or die "harness eval failed: $@\n";

is($min_all, 0.99, 'window threshold is the conservative 99%');
my @full = map { [ 1440, 1440 ] } 1 .. 30;

my ($gate, $contested, $share, $worst) = UUT::_peak_uplift_gate_of(0, \@full);
is($gate, 'withheld (no contested minute)', 'full classification, no contested minute: the rule is withheld');
is($share, 1, 'classified share reported');

($gate) = UUT::_peak_uplift_gate_of(1, \@full);
is($gate, 'applied (contested)', 'one contested minute applies the rule (the known step)');

# 98.5% classified overall, spread evenly: below the window threshold.
($gate) = UUT::_peak_uplift_gate_of(0, [ map { [ 1440, 1418 ] } 1 .. 30 ]);
is($gate, 'applied (legacy: pool evidence insufficient)', 'below 99% classified: legacy');

# 99.6% overall, but one day (a close, say) only 88% classified: not read as open capacity.
($gate, undef, $share, $worst) = UUT::_peak_uplift_gate_of(0, [ @full[0 .. 28], [ 1440, 1267 ] ]);
ok($share > 0.99, 'the window as a whole passes');
is($gate, 'applied (legacy: pool evidence insufficient)', 'a poorly observed day keeps the legacy rule');
ok(abs($worst - 1267 / 1440) < 1e-9, 'lowest day reported');

# A contested minute wins over thin coverage: the rule applies either way.
($gate) = UUT::_peak_uplift_gate_of(3, [ [ 1440, 100 ] ]);
is($gate, 'applied (contested)', 'contested with thin coverage: applied');

($gate) = UUT::_peak_uplift_gate_of(0, []);
is($gate, 'applied (legacy: pool evidence insufficient)', 'no day at all: legacy');

done_testing();
