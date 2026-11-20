#!/usr/bin/env perl
# ==============================================================================
# Unit tests for the pool-use findings (v10 stage 3.4; plan 8.2, "a pool that
# was full"): a pool repeatedly at its maximum while its LPARs had busy work
# above their entitlement gets a trial increase of one processing unit; a full
# pool without that pressure, or one seldom full, is left as it is; a pool
# whose members already require a larger maximum is not given a trial as well.
# The live subroutines are extracted from nfit-profile.
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $constant (qw($POOL_TRIAL_MIN_MINUTES $POOL_TRIAL_MIN_DAYS))
{
    $src =~ /^(my \Q$constant\E\s*=.*?;)/m or die "could not extract $constant\n";
    $code .= "$1\n";
}
for my $name (qw(_pool_use_findings _pool_use_report _pool_quantile _pool_units_text))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nuse Scalar::Util qw(looks_like_number);\nuse List::Util qw(max min);\nmy \$FLOAT_EPSILON = 1e-9;\n$code\n1;"
    or die "harness eval failed: $@\n";

# A pool accumulator as _exposure_evaluate_window keeps it.
my $pool = sub
{
    my ($size, $full, $days, $longest, @values) = @_;
    return { observed => scalar(@values), full => $full, days => { map { $_ => 1 } 1 .. $days }, longest => $longest,
             values => pack('f<*', @values), sizes => { $size => 1 }, size => $size };
};
# A tracker with busy work above entitlement while its pool was full: minutes per day.
my $tracker = sub
{
    my ($vm, $pool_id, $population, @per_day) = @_;
    my $day = 0;
    return { vm => $vm, pool => $pool_id, population => $population,
             daily => { map { (++$day => { expected => 1440, aboveLocalFull => $_ }) } @per_day } };
};
my @busy = ((2.0) x 900, (3.5) x 60, (4.0) x 40);

is($src =~ /^my \$POOL_TRIAL_MIN_MINUTES\s*=\s*15;/m ? 1 : 0, 1, 'recurrence: 15 minutes (provisional)');

# Pool 7: full on 40 minutes over 3 days; lpA above its entitlement then on 20 minutes over 2 days.
my $found = UUT::_pool_use_findings(
    { 7 => $pool->(4, 40, 3, 25, @busy), 9 => $pool->(2, 30, 3, 10, (1.0) x 500, (2.0) x 30),
      11 => $pool->(6, 5, 1, 5, (3.0) x 500), 12 => $pool->(8, 0, 0, 0, (2.0) x 500), 0 => $pool->(40, 100, 5, 50, (40) x 100) },
    [ $tracker->('lpA', 7, 'none', 12, 8, 0), $tracker->('lpA', 7, 'online', 12, 8, 0), $tracker->('lpB', 7, 'none', 14, 0, 0),
      $tracker->('lpC', 9, 'none', 3, 0, 0), $tracker->('lpD', 7, 'batch', 9, 9, 9) ]);
is($found->{7}{Verdict}, 'trial', 'recurring fullness with a member above its entitlement: trial');
is_deeply([ map { $_->{vm} } @{ $found->{7}{Pressured} } ], [qw(lpA lpD)],
    'each LPAR is counted once on its widest hours; 14 minutes on one day does not count');
is($found->{7}{Pressured}[0]{minutes}, 20, 'minutes above entitlement while the pool was full');
is($found->{7}{Peak}, 4, 'peak busy');
cmp_ok(abs($found->{7}{P50} - 2.0), '<', 1e-6, 'median busy');
is($found->{9}{Verdict}, 'fullNoPressure', 'recurring fullness without such a member: no change');
is($found->{11}{Verdict}, 'fullRarely', 'full on one day only: too seldom');
is($found->{12}{Verdict}, 'open', 'never full: open');
ok(!exists $found->{0}, 'the default pool is left to the frame check');

my $report = UUT::_pool_use_report(undef, $found, []);
is(scalar(@{ $report->{Actions} }), 1, 'one action');
like($report->{Actions}[0], qr/^Pool 7: trial increase of the pool maximum from 4 to 5 processing units, then re-measure\. The pool was at its maximum of 4 in 40 minutes on 3 days, with busy work above entitlement on 2 LPARs \(lpA, lpD\)\. A trial, not a measured requirement\. Check licensing\.$/,
    'the trial is one processing unit, labelled as a trial');
like($report->{Members}{lpA}, qr/^Pool 7 was at its maximum with this LPAR's busy work above its entitlement on 20 minutes over 2 days: a trial increase of the pool to 5 is recommended\.$/,
    'the pressured LPAR carries the sentence for its action');
ok(!exists $report->{Members}{lpB}, 'an LPAR that was not pressured carries nothing');
is(scalar(grep { /^Pool 9 was at its maximum of 2 in 30 minutes on 3 days; no LPAR's busy work was above its entitlement then often enough to act on: no change\.$/ } @{ $report->{Observed} }), 1,
    'a full pool without pressure is observed, not changed');
is(scalar(grep { /^Pool 11 .* too seldom to act on\.$/ } @{ $report->{Observed} }), 1, 'a pool seldom full is observed');
is(scalar(grep { /Pool 12/ } @{ $report->{Observed} }, @{ $report->{Actions} }), 0, 'an open pool is not mentioned');
is_deeply([ sort { $a <=> $b } keys %{ $report->{Covered} } ], [ 7, 9, 11, 12 ], 'every assessed pool is marked as covered');

# The members already require 6: the structural increase stands; no trial beside it.
$report = UUT::_pool_use_report(undef, $found, [ { PoolId => 7, RequiredMax => 6, RequiredMaxCycle => 5 } ]);
is(scalar(@{ $report->{Actions} }), 0, 'no trial where the members already require a larger maximum');
is(scalar(grep { /^Pool 7 was at its maximum.*The pool increase its members' entitlements require covers this; re-measure after it\.$/ } @{ $report->{Observed} }), 1,
    'the pressure is still stated, with the structural increase named as the answer');
ok(!%{ $report->{Members} }, 'and no LPAR carries the trial sentence');

# The section is written.
my $buffer = '';
open my $log, '>', \$buffer or die;
UUT::_pool_use_report($log, $found, []);
close $log;
like($buffer, qr/^Pool Use$/m, 'section heading');
like($buffer, qr/^\s+7\s+4\s+2\.00\s+3\.50\s+4\.00\s+4\.00\s+40 minutes on 3 days \(longest 25\)$/m, 'pool row: maximum, median, P95, P99, peak, fullness');
like($buffer, qr/^\s+12\s+8\s+2\.00\s+2\.00\s+2\.00\s+2\.00\s+never$/m, 'an open pool row');
like($buffer, qr/No pool reduction is derived here\./, 'no reduction is derived');

is_deeply(UUT::_pool_use_findings(undef, []), {}, 'no evidence: nothing');
is_deeply(UUT::_pool_use_report(undef, {}, [])->{Actions}, [], 'nothing to report');

done_testing();
