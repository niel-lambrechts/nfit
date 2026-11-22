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

# --- The floor on the planning target (--contested-level): every tier and percentile -------------
{
    my $code2 = '';
    for my $name (qw(_contested_level _contested_floor_for _contested_floor_text _protection_share_of _evidence_cadence_of
                     _evidence_count_text _pool_iso))
    {
        $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
        $code2 .= "$1\n\n";
    }
    my ($shares) = $src =~ /^(my %CUT_PROTECTION_SHARE\s*=.*?\);)/ms or die "shares not found\n";
    my ($rule) = $src =~ /^my \$CONTESTED_LEVEL_RULE\s*=\s*'([^']+)';/m or die "rule version not found\n";
    eval "package UUT2; use Time::Piece; my \$CONTESTED_LEVEL_MIN_MINUTES = $min_minutes; my \$CONTESTED_LEVEL_MIN_DAYS = $min_days;\n"
       . "my \$DEFAULT_PERCENTILE = 99.75; my \$CONTESTED_LEVEL_RULE = '$rule';\n$shares\n$code2\n1;" or die "harness eval failed: $@\n";

    # 100 observations on 5 days: busy work 1.00 .. 1.99, consumption one core above.
    my $tracker = { pool => 7, stateStart => 1788220800, protection => { all => [ map { [ 1 + $_ / 100, 2 + $_ / 100 ] } 0 .. 99 ],
                                                                         days => { map { $_ => 1 } 1 .. 5 } } };
    # share by tier (P 0.95, 1 0.85, 2 0.65, 3 0.40, 4 0) and the profile's own percentile
    my %want = ('P-99W1' => [ 99, 0.95, 2.94 ], 'G1-99W5' => [ 99, 0.85, 2.84 ], 'O2-95W15' => [ 95, 0.65, 2.60 ],
                'G3-95W15' => [ 95, 0.40, 2.35 ], 'B3-95W15' => [ 95, 0.40, 2.35 ], 'G4-90W15' => [ 90, 0, 1.90 ]);
    foreach my $profile (sort keys %want)
    {
        my ($percentile, $share, $level) = @{ $want{$profile} };
        my $floor = UUT2::_contested_floor_for($tracker, $profile, "-p $percentile -w 15");
        ok($floor && abs($floor->{level} - $level) < 0.0051, "$profile: floor $level (share $share, P$percentile)") or diag($floor ? $floor->{level} : 'none');
        is($floor->{share}, $share, "  $profile: the tier's share");
    }
    my $floor = UUT2::_contested_floor_for($tracker, 'G3-95W15', '-p 95 -w 15');
    is_deeply([ @{$floor}{qw(rule observations days cadenceMinutes pool percentile minObservations minDays)} ], [ $rule, 100, 5, 1, 7, 95, 15, 2 ],
        'the floor carries its support, cadence, pool and rule version');
    like($floor->{stateStartUTC}, qr/^2026-09-01T00:00:00Z$/, '  and the configuration state it was measured in');
    is(UUT2::_contested_floor_for({ protection => { all => [ @{ $tracker->{protection}{all} }[0 .. 13] ], days => { 1 => 1, 2 => 1 } } }, 'G3-95W15', '-p 95'),
       undef, 'fewer than 15 observations: no floor');
    is(UUT2::_contested_floor_for({ protection => { all => $tracker->{protection}{all}, days => { 1 => 1 } } }, 'G3-95W15', '-p 95'), undef, 'one day only: no floor');
    is(UUT2::_contested_floor_for($tracker, 'Peak', '-p 99'), undef, 'a profile without a tier: no floor');
    # The observations must be able to resolve the percentile: one of them, at least, lies above it.
    my $thin = { pool => 7, protection => { all => [ @{ $tracker->{protection}{all} }[0 .. 31] ], days => { 1 => 1, 2 => 1, 3 => 1 } } };
    is(UUT2::_contested_floor_for($thin, 'P-99W1', '-p 99'), undef, '32 observations cannot resolve a 99th percentile: no floor');
    is(UUT2::_contested_floor_for($thin, 'G2-98W10', '-p 98'), undef, '  nor a 98th (50 needed)');
    ok(UUT2::_contested_floor_for($thin, 'G3-95W15', '-p 95'), '  a 95th is resolved by 20');
    my $nineteen = { pool => 7, protection => { all => [ @{ $tracker->{protection}{all} }[0 .. 18] ], days => { 1 => 1, 2 => 1 } } };
    is(UUT2::_contested_floor_for($nineteen, 'G3-95W15', '-p 95'), undef, '19 observations do not resolve a 95th percentile');
    ok(UUT2::_contested_floor_for($nineteen, 'G4-90W15', '-p 90'), '  they resolve a 90th');

    # The planner sentence states a percentile's support, not that each observation reached the level.
    is(UUT2::_contested_floor_text({ %$floor, applied => 1 }, 2.35),
       'Target raised to 2.35: profile-level protection of observed work during pool competition; 100 observations over 5 days.', 'action sentence at one minute');
    is(UUT2::_contested_floor_text({ %$floor, applied => 0 }, 2.35), '', 'a floor that did not raise the target says nothing');
    is(UUT2::_contested_floor_text({ %$floor, applied => 1, cadenceMinutes => 15, observations => 20 }, 2.35),
       'Target raised to 2.35: profile-level protection of observed work during pool competition; 20 of the 15-minute intervals over 5 days '
       . '(15-minute data: the rule is validated on one-minute data only).', 'coarse data: counted in intervals, and not presented as equally validated');
}

done_testing();
