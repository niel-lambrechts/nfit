#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/width_evidence.t
# PURPOSE  : VP width evidence (v10 B10), by sub-extraction: the histogram of
#            consumed cores, the minutes near a width and their reference, the
#            support levels and labels, the capped limits, what makes the
#            evidence not assessed, and the sentence on a review of an increase.
#            Diagnostic only: nothing here returns an entitlement or a VP count.
# USAGE    : perl width_evidence.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_width_add_minute _width_figures _width_compare _width_row _width_assessable _width_evidence _width_label
                 _width_review_phrase _width_review_verdict _width_lines _exposure_totals _exposure_population_label _rationale_wrap))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
my ($rule) = $src =~ /^our %WIDTH_EVIDENCE_RULE\s*=\s*(\(.*?\));/ms or die "WIDTH_EVIDENCE_RULE not found\n";
eval qq{
package UUT;
use Scalar::Util qw(looks_like_number);
use List::Util qw(max min);
use JSON;
our \%WIDTH_EVIDENCE_RULE = $rule;
our \@MEMBER_EXPOSURE_COLUMNS = (1 .. 7);
} . $code . "\n1;\n" or die "harness eval failed: $@\n";

# A tracker as the evidence pass leaves it: 30 days, every profile minute paired.
my $tracker = sub
{
    my (%over) = @_;
    return { vm => 'a', population => 'none', vp => 4, entitlement => 1.5, capped => 0, from => 0, to => 30 * 86400, thresholds => [],
             daily => { 1 => { expected => 43200, paired => 43200 } }, width => { bins => {}, dayMax => {} }, %over };
};
my $fill = sub
{
    my ($t, $minutes, $days, $c, $b, $queue, $state) = @_;
    foreach my $day (@$days)
    {
        UUT::_width_add_minute($t->{width}, $day, $c, $b, $queue, ($state // 2), ($t->{capped} ? $t->{entitlement} : undef)) for (1 .. $minutes);
    }
};

# --- The histogram: one update a minute; the edges of a whole VP width are bin edges ---
my $t = $tracker->();
$fill->($t, 40, [ 5, 12, 19 ], 3.80, 3.42, 3.5);            # near 4 VPs (3.60 or more)
$fill->($t, 5, [ 5 ], 3.59, 1.00, 0.5);                      # just under 90% of 4: not near
$fill->($t, 5, [ 5 ], 3.60, 3.00, 3.5);                      # exactly 90% of 4: near
$fill->($t, 5, [ 6 ], 4.00, 3.90, 3.5, 3);                   # at the width, the pool full with others busy
$fill->($t, 600, [ 1 .. 30 ], 1.00, 0.35, 0.5);              # the ordinary minutes: the reference
$fill->($t, 50, [ 2 ], 0.20, 0.05, 0.1);                     # below 0.25 cores: neither
$fill->($t, 50, [ 2 ], 2.80, 1.00, 0.5);                     # exactly 70% of 4: not reference
is(scalar(keys %{ $t->{width}{bins} }), 7, 'one bin for each distinct 0.05 step of consumed cores, however many minutes');
my $row = UUT::_width_row($t->{width}, 4, 'installed');
is_deeply([ @{ $row->{near} }{qw(minutes days atOrAbove)}, $row->{reference}{minutes} ], [ 130, 4, 5, 18000 ],
          'near 4 VPs: 90% of it or more, 3.59 excluded and 3.60 included; the reference is 0.25 cores to under 70% of it');
is_deeply($row->{near}{pool}, { room => 125, others => 5, alone => 0, unobserved => 0 }, 'the pool state of the near minutes is kept');
is_deeply([ $row->{support}, $row->{comparison}, $row->{near}{highestBusy} ], [ 'sufficient', 'rose', 3.9 ], 'sufficient, and busy share and queue rose');
is(UUT::_width_label($row), 'busy share and queue rose near width', 'the label says what rose; it does not say the cores were busy throughout');
# A proposed width reads the same histogram; its reference lies below 70% of that width, so no minute is on both sides.
my $two = UUT::_width_row($t->{width}, 2, 'target');
is_deeply([ $two->{near}{minutes}, $two->{near}{atOrAbove}, $two->{reference}{minutes} ], [ 185, 185, 18000 ],
          'near 2 VPs: every minute at 1.80 or more, all of them at or above 2; reference 0.25 to under 1.40');
my $one = UUT::_width_row($t->{width}, 1, 'target');
is_deeply([ $one->{near}{minutes}, $one->{reference}{minutes}, $one->{support}, $one->{comparison} ], [ 18185, 0, 'sufficient', 'noReference' ],
          'near 1 VP the ordinary minutes are themselves near: no reference is left, and that is said');
is(UUT::_width_label($one), 'sufficient minutes near width, no reference minutes to compare with', 'the limitation in words');

# --- Support levels: none, limited, sufficient ---------------------------------------
my $quiet = $tracker->();
$fill->($quiet, 600, [ 1 .. 30 ], 1.00, 0.35, 0.5);
is(UUT::_width_label(UUT::_width_row($quiet->{width}, 4, 'installed')), 'no observed minutes near width', 'no minute near the width');
my $brief = $tracker->();
$fill->($brief, 600, [ 1 .. 30 ], 1.00, 0.35, 0.5);
$fill->($brief, 29, [ 5, 6 ], 3.80, 3.42, 3.5);
is(UUT::_width_row($brief->{width}, 4, 'installed')->{support}, 'sufficient', '58 minutes on 2 days: sufficient');
my $one_day = $tracker->();
$fill->($one_day, 600, [ 1 .. 30 ], 1.00, 0.35, 0.5);
$fill->($one_day, 200, [ 5 ], 3.80, 3.42, 3.5);
my $limited = UUT::_width_row($one_day->{width}, 4, 'installed');
is_deeply([ $limited->{support}, exists $limited->{comparison} ? 1 : 0 ], [ 'limited', 0 ], '200 minutes on one day: limited, and no comparison is drawn');
is(UUT::_width_label($limited), 'limited evidence near width', 'limited evidence is not "width not approached"');
my $few = $tracker->();
$fill->($few, 14, [ 5, 6 ], 3.80, 3.42, 3.5);
is(UUT::_width_row($few->{width}, 4, 'installed')->{support}, 'limited', '28 minutes on 2 days: limited');

# --- The comparison: rose, did not rise, disagree, no queue ---------------------------
my $compare = sub
{
    my ($busy, $queue, $ref_queue) = @_;
    my $x = $tracker->();
    $fill->($x, 600, [ 1 .. 30 ], 1.00, 0.35, $ref_queue);
    $fill->($x, 40, [ 5, 12 ], 3.80, $busy, $queue);
    return UUT::_width_row($x->{width}, 4, 'installed');
};
is($compare->(1.71, 3.5, 0.5)->{comparison}, 'rose', 'busy share 0.45 against 0.35 with a higher queue: rose (0.10 or more)');
is($compare->(1.67, 3.5, 0.5)->{comparison}, 'mixed', 'busy share 0.44: under 0.10 above the reference; the queue rose: the two disagree');
is($compare->(1.33, 0.5, 0.5)->{comparison}, 'notRisen', 'neither rose');
is($compare->(3.42, 0.5, 0.5)->{comparison}, 'mixed', 'busy share rose, the queue did not: the two disagree');
is($compare->(3.42, undef, undef)->{comparison}, 'noQueue', 'no run-queue readings: said, not guessed');
is(UUT::_width_label($compare->(1.33, 0.5, 0.5)), 'busy share and queue did not rise near width', 'no rise is reported as no rise');

# --- Not assessed: an honest limitation, no figures ----------------------------------
is_deeply([ (UUT::_width_assessable(undef))[0, 1] ], [ 0, 'no evidence was gathered for this LPAR (not in a shared pool, or no current configuration state in the period)' ], 'no tracker');
is((UUT::_width_assessable($tracker->(width => { bins => {}, dayMax => {}, coarse => 15 })))[1], '15-minute data: one-minute samples are needed', 'coarse data');
is((UUT::_width_assessable($tracker->(to => 20 * 86400)))[1], '20 days in the current configuration, 28 needed', 'a recent configuration change');
is((UUT::_width_assessable($tracker->(daily => { 1 => { expected => 43200, paired => 30000 } })))[1],
   'PhysC and PhysB are both present in 69% of the profile minutes, 90% needed', 'PhysB missing in too many minutes');
is((UUT::_width_assessable($tracker->(daily => { 1 => { expected => 43200, paired => 0 } })))[1], 'no minute with both PhysC and PhysB in the profile hours', 'no PhysB at all');
my $short = UUT::_width_evidence($tracker->(to => 20 * 86400), {});
is_deeply([ $short->{status}, exists $short->{widths} ? 1 : 0 ], [ 'notAssessed', 0 ], 'not assessed: a reason, and no figures');

# --- The record: installed width always; proposed widths where they differ -----------
my $record = UUT::_width_evidence($t, { cycle => 4, target => 2 });
is_deeply([ map { "$_->{basis}:$_->{width}" } @{ $record->{widths} } ], [ 'installed:4', 'target:2' ], 'a cycle width equal to the installed one is not repeated');
is_deeply([ map { "$_->{basis}:$_->{width}" } @{ UUT::_width_evidence($t, { cycle => 3, target => 2 })->{widths} } ], [ 'installed:4', 'cycle:3', 'target:2' ], 'both proposed widths');
my @lines = UUT::_width_lines('a', $record, ': fewer VPs proposed (4 this cycle, 2 the full target)');
my $text = join(' ', map { s/^\s+//r } @lines);
like($text, qr/^a \(4 VPs, all hours, 30 days\): fewer VPs proposed \(4 this cycle, 2 the full target\) At the installed 4 VPs: busy share and queue rose near width\. 130 minutes on 4 days at 90% of it or more \(5 at or above it\): busy share 90%/,
     'the lines: what was observed near the installed width');
like($text, qr/Pool in those minutes: room 125, full with others busy 5\./, 'only the pool states that occurred');
like($text, qr/At 2 VPs \(the full target\): .* Observed on 4 VPs: this says which minutes of the period lay near 2, not how the LPAR would run on 2\.$/,
     'a proposed width is described as observed at the installed VP count, not as a prediction');
unlike($text, qr/after (?:the|a) (?:change|reduction)|would be|will be|unnecessary|spare usable/i, 'no prediction and no verdict on removable width');

# --- Capped: the cap and the VP count are two limits ---------------------------------
my $cap_low = $tracker->(capped => 1, entitlement => 2.0);
$fill->($cap_low, 600, [ 1 .. 30 ], 0.80, 0.30, 0.25);
$fill->($cap_low, 40, [ 5, 12, 19 ], 1.96, 1.70, 1.5);
my $low = UUT::_width_evidence($cap_low, {});
is_deeply([ $low->{cap}{relation}, $low->{cap}{support}, $low->{cap}{near}{minutes}, $low->{cap}{near}{days}, $low->{widths}[0]{support} ],
          [ 'belowWidth', 'sufficient', 120, 3, 'none' ], 'cap 2.00 on 4 VPs: near the cap, never near the VP count');
ok(!exists $low->{widths}[0]{comparison}, 'no comparison is drawn for a capped LPAR');
like(join(' ', map { s/^\s+//r } UUT::_width_lines('c', $low, '')),
     qr/At the entitlement cap of 2\.00: sufficient minutes near the cap\. 120 minutes on 3 days at 90% of the cap or more: busy share 87%.* The cap lies below the VP count \(2\.00 on 4 VPs\): consumption cannot approach the VP count, so unused width is not evidence that the workload is unconstrained\. The findings for uncapped LPARs are not applied to a capped one; its figures are unchanged\.$/,
     'cap below the width: unused width is not evidence of an unconstrained workload');
my $cap_same = $tracker->(capped => 1, entitlement => 2.0, vp => 2);
$fill->($cap_same, 600, [ 1 .. 30 ], 0.80, 0.30, 0.5);
$fill->($cap_same, 40, [ 5, 12, 19 ], 1.96, 1.70, 3.0);
my $same = UUT::_width_evidence($cap_same, {});
is_deeply([ $same->{cap}{relation}, $same->{widths}[0]{support}, exists $same->{widths}[0]{comparison} ? 1 : 0 ], [ 'coincides', 'sufficient', 0 ],
          'cap 2.00 on 2 VPs: the two limits coincide; figures as observed, no comparison');
like(join(' ', map { s/^\s+//r } UUT::_width_lines('c', $same, '')), qr/The cap and the VP count coincide \(2\.00 on 2 VPs\): the observations do not separate the two limits\./,
     'coinciding limits are said not to be separated');
is(UUT::_width_label($same->{widths}[0]), 'sufficient minutes near width', 'and the label claims no rise');

# --- The review of an increase: corroborates, does not corroborate, insufficient ----
is(UUT::_width_review_phrase(undef), undef, 'no evidence tracker: no sentence is added to the review');
is(UUT::_width_review_phrase($t), 'The width evidence corroborates the review.', 'busy share and queue rose near the installed width');
my $flat = $tracker->();
$fill->($flat, 600, [ 1 .. 30 ], 1.00, 0.35, 0.5);
$fill->($flat, 40, [ 5, 12 ], 3.80, 1.33, 0.5);
is(UUT::_width_review_phrase($flat), 'The width evidence does not corroborate the review.', 'neither rose: it does not corroborate; it does not say an increase is unnecessary');
is(UUT::_width_review_phrase($quiet), 'The width evidence is insufficient to assess the review.', 'no minutes near the installed width');
is(UUT::_width_review_phrase($one_day), 'The width evidence is insufficient to assess the review.', 'limited evidence');
is(UUT::_width_review_phrase($compare->(3.42, 0.5, 0.5) && $tracker->(to => 20 * 86400)), 'The width evidence is insufficient to assess the review.', 'not assessed');
is(UUT::_width_review_phrase($cap_same), 'The width evidence is insufficient to assess the review.', 'a capped LPAR: the uncapped comparison is not applied');

done_testing();
