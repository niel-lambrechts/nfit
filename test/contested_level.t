#!/usr/bin/env perl
# ==============================================================================
# Unit tests for the contested level (research, stage 3.4 candidate): the
# percentile of PhysB + share x (PhysC - PhysB) over the contested minutes,
# rounded up to 0.01, with the provisional evidence requirement. The live
# subroutine and its two thresholds are extracted from nfit-profile.
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
$src =~ /^(sub _contested_level\s*\{.*?^\})/ms or die "could not extract _contested_level\n";
my $code = $1;
my ($min_minutes) = $src =~ /^my \$CONTESTED_LEVEL_MIN_MINUTES\s*=\s*([0-9]+);/m or die "minute requirement not found\n";
my ($min_days)    = $src =~ /^my \$CONTESTED_LEVEL_MIN_DAYS\s*=\s*([0-9]+);/m or die "day requirement not found\n";
eval "package UUT; our \$CONTESTED_LEVEL_MIN_MINUTES = $min_minutes; our \$CONTESTED_LEVEL_MIN_DAYS = $min_days; $code 1;"
    or die "harness eval failed: $@\n";

is($min_minutes, 15, 'provisional requirement: 15 contested minutes');
is($min_days, 2, 'provisional requirement: on 2 days');

my @none = UUT::_contested_level({ all => [], days => {} }, 0.65, 95);
is($none[0], undef, 'no contested minute: no level (not a measured 0)');

# 20 minutes on 2 days: PhysB 1.00 .. 2.90, PhysC one core above.
my $p = { all => [ map { [ 1 + $_ / 10, 2 + $_ / 10 ] } 0 .. 19 ], days => { 1 => 1, 2 => 1 } };
my ($level, $minutes, $days, $enough) = UUT::_contested_level($p, 0, 50);
is($level, 1.95, 'share 0, median: PhysB only');
is($minutes, 20, 'minutes counted');
is($days, 2, 'days counted');
ok($enough, '20 minutes on 2 days is sufficient');
($level) = UUT::_contested_level($p, 1, 50);
is($level, 2.95, 'share 1: PhysC');
($level) = UUT::_contested_level($p, 0.4, 100);
is($level, 3.3, 'not limited by any installed entitlement; share applied');
($level) = UUT::_contested_level({ all => [ [ 0.301, 0.301 ] ], days => { 1 => 1 } }, 0.4, 95);
is($level, 0.31, 'rounded up to 0.01');

(undef, undef, undef, $enough) = UUT::_contested_level({ all => [ @{ $p->{all} }[0 .. 13] ], days => { 1 => 1, 2 => 1 } }, 0.65, 95);
ok(!$enough, '14 minutes: below the requirement');
(undef, undef, undef, $enough) = UUT::_contested_level({ all => $p->{all}, days => { 1 => 1 } }, 0.65, 95);
ok(!$enough, 'a single day: below the requirement');

# The known sensitivity: at 15 samples a 99th percentile is close to the maximum.
my $spike = { all => [ (map { [ 0.5, 0.5 ] } 1 .. 14), [ 3.0, 3.0 ] ], days => { 1 => 1, 2 => 1 } };
($level) = UUT::_contested_level($spike, 0.85, 99);
ok($level > 2.5, 'one high reading among 15 carries a 99th percentile (documented limitation)');

# --- The provisional boundary (15 minutes on 2 days), both sides ---------------------
my $flat = sub { my ($n, @days) = @_; return { all => [ map { [ 2.0, 3.0 ] } 1 .. $n ], days => { map { $_ => 1 } @days } }; };
my @at;
@at = UUT::_contested_level($flat->(15, 1, 2), 0.4, 95);
ok($at[3], 'exactly 15 minutes on exactly 2 days: sufficient');
is($at[0], 2.4, 'and the level is the one the 14-minute evidence already showed');
@at = UUT::_contested_level($flat->(14, 1, 2), 0.4, 95);
ok(!$at[3], '14 minutes on 2 days: not sufficient');
is($at[0], 2.4, 'the level is still reported, so the withheld step is visible');
@at = UUT::_contested_level($flat->(500, 1), 0.4, 95);
ok(!$at[3], '500 minutes on one day: not sufficient (one long episode is one occasion)');
@at = UUT::_contested_level($flat->(15, 1, 2, 3, 4, 5), 0.4, 95);
ok($at[3], 'more days than needed: sufficient');
# Known limitation, recorded: the day requirement is met by a single minute on a second
# day, and one added minute moves a profile from no uplift to the whole level.
ok((UUT::_contested_level({ all => [ map { [ 2.0, 3.0 ] } 1 .. 15 ], days => { 1 => 1, 2 => 1 } }, 0.4, 95))[3],
   '14 minutes on one day and 1 on another passes (limitation: the second day may be one minute)');
# The level does not depend on which side of the boundary the evidence falls: only its use does.
is((UUT::_contested_level($flat->(14, 1, 2), 0.85, 99))[0], (UUT::_contested_level($flat->(15, 1, 2), 0.85, 99))[0],
   'the same readings give the same level on either side of the boundary');

done_testing();
