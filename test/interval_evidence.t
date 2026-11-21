#!/usr/bin/env perl
# ==============================================================================
# Unit tests for interval-aware evidence (v10 stage 3.4) in the evidence pass
# (_exposure_process_day): observations are counted as recorded; coverage is
# measured against the cadence; a gap is missing observations; LPAR and pool
# are matched by interval at a coarse cadence and only when their cadences are
# compatible; a sample belongs to the profile window that holds its timestamp;
# one contested observation is kept; one-minute data behaves as before.
# The live subroutines and rules are extracted from nfit-profile.
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $block ('my %MEMBER_EXPOSURE_RULES', 'my @MEMBER_EXPOSURE_COLUMNS', 'my %EVIDENCE_INTERVAL', 'my %BUSY_SHARE_RULES')
{
    $src =~ /^(\Q$block\E\s*=.*?\);)/ms or die "could not extract $block\n";
    $code .= "$1\n";
}
for my $name (qw(_exposure_process_day _exposure_population_admits _exposure_tolerance _evidence_cadence _evidence_nearest
                 _evidence_cadence_of _evidence_count_text _protection_add_minute _protection_loss_minute _protection_borrow_minute
                 _busy_share_add_minute _pressure_add_minute _sampling_interval_survey _mixed_interval_text))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nuse List::Util qw(max min);\nuse Scalar::Util qw(looks_like_number);\n"
   . "my \$CUT_PROTECTION_TOP_READINGS = 32; my \$research_contested_level; my \%PRESSURE_KIND_CODE;\n$code\n1;" or die "harness eval failed: $@\n";

my $DAY = int(1788220800 / 86400);                # 2026-09-01, a Tuesday
my $T0 = $DAY * 86400;

sub tracker
{
    my ($population) = @_;
    return { vm => 'lp1', pool => 7, population => $population // 'none', from => $T0, to => $T0 + 86400,
             thresholds => [ { value => 2.0 } ], entitlement => 2.0, vp => 4, capped => 0, ceiling => 4,
             protection => { contested => 0, days => {}, frontier => [], top => [] }, daily => {} };
}
# LPAR samples every $step minutes: [samples, sum PhysC, pairs, sum PhysB]; pool records at an offset.
sub member { my ($step, %skip) = @_; return { lp1 => { map { ($T0 + 60 * $_ => [ 1, 1.0, 1, 0.5 ]) } grep { $_ % $step == 0 && !$skip{$_} } 0 .. 1439 } }; }
sub pool   { my ($step, $offset, $busy) = @_; return { 7 => { map { ($T0 + 60 * ($_ + ($offset // 0)) => { value => ($busy // 2.0), size => 4 }) }
                                                             grep { $_ % $step == 0 } 0 .. 1439 } }; }
# The frame's shared pool has spare cores wherever the pool was observed.
sub evaluate { my ($t, $cpu, $pool) = @_;
                my %frame = map { ($_ => { idle => 20, size => 64 }) } keys %{ $pool->{7} };
                UUT::_exposure_process_day([$t], $DAY, $cpu, $pool, \%frame); return $t->{daily}{$DAY}; }

# --- 15-minute cadence -------------------------------------------------------------
my $t = tracker();
my $acc = evaluate($t, member(15), pool(15));
is($acc->{paired}, 96, 'one observation per sample: 96 in a day at 15 minutes, nothing repeated into minutes');
is($acc->{expected}, 1440, 'the window is still 1440 minutes');
cmp_ok(abs($acc->{expectedObs} - 96), '<', 1e-6, 'and 96 observations were to be had');
is($acc->{classified}, 96, 'each is classified against the pool reading of its interval');
is_deeply($t->{observations}, { 15 => 96 }, 'the cadence is recorded with the observations');
is(UUT::_evidence_cadence_of($t), 15, 'and reported for the tracker');
is(UUT::_evidence_count_text(6, 15), '6 of the 15-minute intervals', 'counts read as intervals');
is(UUT::_evidence_count_text(1, 1), '1 minute', 'and as minutes at one-minute cadence');

# A two-hour gap is missing observations, not a shorter day.
$t = tracker();
$acc = evaluate($t, member(15, map { ($_ => 1) } grep { $_ % 15 == 0 } 600 .. 719), pool(15));
is($acc->{paired}, 88, 'eight samples missing');
cmp_ok(abs($acc->{expectedObs} - 96), '<', 1e-6, 'the eight still count as expected');

# The pool reading is the nearest within half an interval.
$t = tracker();
$acc = evaluate($t, member(15), pool(15, 3));
is($acc->{classified}, 96, 'pool observations three minutes after the LPAR samples are matched');
ok(!$acc->{misaligned}, 'and are not misaligned');

# Different cadences are not matched: the evidence is unclassified.
$t = tracker();
$acc = evaluate($t, member(15), pool(5));
is($acc->{classified}, 0, 'LPAR every 15 minutes, pool every 5: nothing is classified');
is($acc->{misaligned}, 96, 'every observation is counted as misaligned');
is($t->{protection}{contested}, 0, 'and nothing is contested');

# A sample belongs to the profile window that holds its timestamp.
$t = tracker('online+no-weekends');
$acc = evaluate($t, member(15), pool(15));
is($acc->{paired}, 36, 'online hours 08:00 to 17:00: the samples from 08:00 to 16:45');
cmp_ok(abs($acc->{expectedObs} - 36), '<', 1e-6, 'expected likewise; the sample at 17:00 belongs to the next window');

# One contested observation is kept: the pool at its maximum with others busy at one sample.
$t = tracker();
my $p = pool(15);
$p->{7}{ $T0 + 600 * 60 } = { value => 4.0, size => 4 };
my $m = member(15);
$m->{lp1}{ $T0 + 600 * 60 } = [ 1, 2.5, 1, 2.2 ];                # busy above the entitlement of 2.0
$acc = evaluate($t, $m, $p);
is($t->{protection}{contested}, 1, 'one contested observation');
is($acc->{aboveLocalFull}, 1, 'with busy work above the entitlement while the pool was at its maximum');
is($acc->{scarce}, 1, 'in one interval');

# --- One-minute cadence is as before -----------------------------------------------
$t = tracker();
$acc = evaluate($t, member(1), pool(1));
is($acc->{paired}, 1440, 'one-minute data: 1440 observations');
cmp_ok(abs($acc->{expectedObs} - 1440), '<', 1e-6, 'expected observations equal the minutes');
is($acc->{classified}, 1440, 'all classified');
$t = tracker();
$p = pool(1);
delete $p->{7}{ $T0 + 600 * 60 };                                 # one pool minute missing
$acc = evaluate($t, member(1), $p);
is($acc->{classified}, 1439, 'a missing pool minute is not filled from its neighbour');
is(UUT::_evidence_cadence_of($t), 1, 'cadence 1');

# A day without samples keeps the last cadence, so that its observations count as missing at that rate.
$t = tracker();
evaluate($t, member(15), pool(15));
$t->{from} = $T0 + 86400; $t->{to} = $T0 + 2 * 86400;
UUT::_exposure_process_day([$t], $DAY + 1, {}, {}, undef);
cmp_ok(abs($t->{daily}{ $DAY + 1 }{expectedObs} - 96), '<', 1e-6, 'an empty day expects 96 observations, not 1440');
is($t->{daily}{ $DAY + 1 }{paired}, 0, 'and has none');

is(UUT::_evidence_cadence({ 0 => 1 }), undef, 'one sample gives no cadence');
is(UUT::_evidence_cadence({ map { (900 * $_ => 1) } 0 .. 9 }), 15, 'median spacing in minutes');

# --- More than one sampling interval in a CPU cache ---
{
    require File::Temp;
    require POSIX;
    my $write = sub
    {
        my (@blocks) = @_;                       # [first epoch, step seconds, samples, LPARs]
        my ($fh, $path) = File::Temp::tempfile(UNLINK => 1);
        print {$fh} "Timestamp,VMName,PhysC,RunQ,PhysB\n";
        foreach my $b (@blocks)
        {
            my ($from, $step, $n, @vms) = @$b;
            foreach my $i (0 .. $n - 1)
            {
                print {$fh} POSIX::strftime('%Y-%m-%d %H:%M:%S', gmtime($from + $i * $step)), ",$_,1.0000,1.0000,0.5000\n" for @vms;
            }
        }
        close $fh;
        return $path;
    };
    my $one = UUT::_sampling_interval_survey($write->([ $T0, 60, 600, 'a', 'b' ]));
    is_deeply($one->{intervals}, [60], 'survey: one-minute data throughout');
    is($one->{mixed}, 0, '  not mixed');
    is(UUT::_mixed_interval_text($one, 60), undef, '  nothing to say');
    my $coarse = UUT::_sampling_interval_survey($write->([ $T0, 900, 600, 'a', 'b' ]));
    is_deeply($coarse->{intervals}, [900], 'survey: 15-minute data throughout is one interval');
    # The collection interval was changed part-way: the first samples say one minute, the later ones fifteen.
    my $changed = UUT::_sampling_interval_survey($write->([ $T0, 60, 600, 'a', 'b' ], [ $T0 + 600 * 60, 900, 600, 'a', 'b' ]));
    is_deeply($changed->{intervals}, [60, 900], 'survey: a change of interval part-way is found');
    is($changed->{mixed}, 1, '  and reported as mixed');
    like(UUT::_mixed_interval_text($changed, 60), qr/^The data holds more than one sampling interval \(1, 15 minutes\)\. Smoothing and the pool evidence rules use 1 minute throughout/,
        '  the planner is told which interval was used');
    # Two LPARs collected at different intervals.
    my $two = UUT::_sampling_interval_survey($write->([ $T0, 60, 600, 'a' ], [ $T0, 300, 600, 'b' ]));
    is_deeply($two->{intervals}, [60, 300], 'survey: LPARs sampled at different intervals are found');
    # Jitter and a gap do not make an interval.
    my $jitter = UUT::_sampling_interval_survey($write->([ $T0, 61, 300, 'a' ], [ $T0 + 86400, 59, 300, 'a' ]));
    is_deeply($jitter->{intervals}, [60], 'survey: a second of jitter and a day\'s gap are still one minute');
    is(UUT::_sampling_interval_survey('/nonexistent'), undef, 'survey: no file, no finding');
}

done_testing();
