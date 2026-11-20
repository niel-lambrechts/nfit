#!/usr/bin/env perl
# ==============================================================================
# Unit tests for the busy-share comparison (research, stage 3.4 design note
# 3.1): minutes under pressure against relaxed minutes of the same consumption
# band, below, near and above the entitlement, and the run queue carried into
# the minute reader. The live subroutines and rules are extracted from
# nfit-profile.
# ==============================================================================
use strict;
use warnings;
use Test::More;
use File::Temp qw(tempfile);

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
$src =~ /^(my %BUSY_SHARE_RULES\s*=.*?;)\s*$/m or die "rules not found\n";
$code .= "$1\n";
for my $name (qw(_busy_share_add_minute _busy_share_record _exposure_read_cpu_day _pool_number))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nuse Scalar::Util qw(looks_like_number);\nuse JSON;\nmy \$research_busy_share = 1;\n"
   . "my %MEMBER_EXPOSURE_RULES = (pairToleranceCores => 0.01);\n$code\n1;" or die "harness eval failed: $@\n";

my $T0 = 1788220800;                          # 2026-09-01 00:00 UTC
my $minute = 0;
my $add = sub
{
    my ($bs, $n, $busy, $consumed, $ent, $rq, $condition, $day) = @_;
    UUT::_busy_share_add_minute($bs, $T0 + ($day // 0) * 86400 + 60 * $minute++, $busy, $consumed, $ent, $rq, $condition) for 1 .. $n;
};
my $row = sub { my ($rows, $c, $r) = @_; return (grep { $_->{condition} eq $c && $_->{regime} eq $r } @$rows)[0]; };

# Entitlement 1.0. Above it (PhysC 1.5): contested minutes carry busy share 0.60 and run
# queue 2.0 per VP; relaxed minutes of the same consumption 0.40 and 1.0.
my $bs = {};
$add->($bs, 20, 0.90, 1.50, 1.0, 2.0, 'contested', 0);
$add->($bs, 30, 0.60, 1.50, 1.0, 1.0, 'relaxed', 1);
# Near it (PhysC 1.0): 16 contested against 16 relaxed, no change in busy share.
$add->($bs, 16, 0.50, 1.00, 1.0, undef, 'contested', 0);
$add->($bs, 16, 0.50, 1.00, 1.0, undef, 'relaxed', 1);
# Below it (PhysC 0.5): contested minutes with no relaxed minute of that consumption.
$add->($bs, 25, 0.30, 0.50, 1.0, 1.5, 'contested', 0);
$add->($bs, 40, 0.10, 0.20, 1.0, 0.5, 'relaxed', 1);
# Very low consumption is left out.
$add->($bs, 50, 0.01, 0.05, 1.0, 0.1, 'contested', 0);

my $rows = UUT::_busy_share_record($bs);
my $above = $row->($rows, 'contested', 'above');
ok($above && $above->{sufficient}, 'above the entitlement: compared');
is($above->{busySharePressured}, 0.6, 'busy share under pressure');
is($above->{busyShareRelaxed}, 0.4, 'busy share relaxed, same consumption');
is($above->{runqPerVpPressured}, 2, 'run queue per VP under pressure');
is($above->{runqPerVpRelaxed}, 1, 'run queue per VP relaxed');
is($above->{pressuredMinutes}, 20, 'pressured minutes');
is($above->{relaxedMinutes}, 30, 'relaxed minutes');
is($above->{pressuredDays}, 1, 'days reported');
my $near = $row->($rows, 'contested', 'near');
ok($near->{sufficient}, 'near the entitlement: compared');
is($near->{busySharePressured}, $near->{busyShareRelaxed}, 'no change near the entitlement');
ok(!exists $near->{runqPerVpPressured}, 'no run-queue figure without run-queue minutes');
my $below = $row->($rows, 'contested', 'below');
ok(!$below->{sufficient}, 'no relaxed minute of the same consumption: not compared');
is($below->{pressuredMinutesAll}, 25, 'the unmatched minutes are still counted (the very idle ones are not)');
ok(!exists $below->{busySharePressured}, 'and no figure is given');

# Bands are weighted by the pressured side: both sides are read at its consumption.
$bs = {};
$add->($bs, 30, 1.20, 2.00, 1.0, undef, 'solitary', 0);      # band 2.0: 0.60
$add->($bs, 10, 0.90, 3.00, 1.0, undef, 'solitary', 0);      # band 3.0: 0.30
$add->($bs, 10, 1.00, 2.00, 1.0, undef, 'relaxed', 1);       # band 2.0: 0.50
$add->($bs, 90, 0.30, 3.00, 1.0, undef, 'relaxed', 1);       # band 3.0: 0.10
my $solo = $row->(UUT::_busy_share_record($bs), 'solitary', 'above');
is($solo->{busySharePressured}, 0.525, 'pressured: 0.75 x 0.60 + 0.25 x 0.30');
is($solo->{busyShareRelaxed}, 0.4, 'relaxed at the same weights: 0.75 x 0.50 + 0.25 x 0.10 (not its own 0.14)');
is($solo->{bands}, 2, 'two bands');

# Too few minutes on one side: no figure.
$bs = {};
$add->($bs, 14, 0.9, 1.5, 1.0, undef, 'vpCeiling', 0);
$add->($bs, 60, 0.6, 1.5, 1.0, undef, 'relaxed', 1);
ok(!$row->(UUT::_busy_share_record($bs), 'vpCeiling', 'above')->{sufficient}, '14 pressured minutes: below the requirement');
is_deeply(UUT::_busy_share_record({ contested => {} }), [], 'no relaxed minute at all: nothing to compare');

# The minute reader carries the run queue (fifth field) under the switch.
my ($out, $path) = tempfile(UNLINK => 1);
print {$out} join("\t", $T0, 'lp1', '1.0', '0.5', '4'), "\n", join("\t", $T0 + 30, 'lp1', '1.0', '0.7', '6'), "\n",
             join("\t", $T0 + 60, 'lp1', '2.0', '1.0'), "\n";
close $out;
my $minutes = UUT::_exposure_read_cpu_day($path);
is_deeply($minutes->{lp1}{$T0}, [ 2, 2, 2, 1.2, 2, 10 ], 'two samples: PhysC, PhysB and RunQ summed');
is_deeply($minutes->{lp1}{ $T0 + 60 }, [ 1, 2, 1, 1, 0, 0 ], 'a sample without a run queue still pairs');

done_testing();
