#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/selected_demand.t
# PURPOSE:   The reader of nfit-forecast's selected forecast in an adaptive run
#            (v10 step f), extracted from nfit-profile: _adaptive_selection_block,
#            _selection_run_verdict, _selection_pair_verdict,
#            _adaptive_selection_plan, _apply_selected_demand,
#            _demand_source_label, _adaptive_selection_report_lines.
#            A selection is used only when, as a whole, it was made for the run's
#            occurrence, quantity and definitions from data no later than the
#            run's; each condition withholds it with its own reason. A pair is
#            used only with a recognised mode and a value of zero or more; the
#            measurement profile is never substituted. The selected value is the
#            sizing input, kept apart with the source model's own demand.
# USAGE:     perl test/selected_demand.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use Scalar::Util qw(looks_like_number);

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = "package UUT; use Scalar::Util qw(looks_like_number); our \$MANDATORY_PEAK_PROFILE_FOR_HINT = 'Peak_P-99W1';\n";
foreach my $name (qw(_safe_dig _adaptive_selection_block _selection_run_verdict _selection_pair_verdict
                     _adaptive_selection_plan _apply_selected_demand _demand_source_label _adaptive_selection_report_lines))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n";
}
eval "$code 1;" or die "harness eval failed: $@\n";

my $TARGET = { occurrenceStart => '2026-10-28', occurrenceEnd => '2026-10-31', observed => 0 };
sub meta
{
    my (%over) = @_;
    return { semantics => 'selected-demand-1', quantity => 'profile-demand', event => 'ad', source_event => 'pp',
             written_to => '2026-09', candidates_stored_in => '2026-09', target_occurrence => { %$TARGET },
             data_ends => '2026-09-30', profile_definitions => 'flags-1:abc', exclusion_definitions => 'NONE',
             record_definitions => { profile_definitions => 'flags-1:abc', exclusion_definitions => 'NONE' }, %over };
}
sub pair
{
    my (%over) = @_;
    return { chosen_model => 'holt_event', selection_mode => 'tournament', forecast => 2.5, pi_lower => 2.1, pi_upper => 2.9,
             confidence_score => 61, observations => 7, quantity => 'profile-demand', candidates_stored_in => '2026-09',
             target_occurrence => { %$TARGET }, %over };
}
my %CTX = (event => 'ad', source => 'pp', source_ran => 'pp', target_start => '2026-10-28', target_end => '2026-10-31',
           data_end => '2026-09-30', profile_definitions => 'flags-1:abc', exclusion_definitions => 'NONE');

# -- where the selection is found --
my $hist = {
    '2026-08' => { SeasonalEventSnapshots => { ad => { AdaptiveForecast => { selection => { vmA => { G => pair() } }, selection_meta => meta(written_to => '2026-08') } } } },
    '2026-09' => { SeasonalEventSnapshots => { ad => { AdaptiveForecast => { selection => { vmA => { G => pair(forecast => 3.0) } }, selection_meta => meta() } } } },
    '2026-10' => { SeasonalEventSnapshots => { ad => { AdaptiveForecast => { selection => {} } } } },     # the backfill's placeholder
};
my $block = UUT::_adaptive_selection_block($hist, 'ad');
is($block->{month}, '2026-09', 'the newest month that holds a selection; an empty placeholder does not count');
is($block->{pairs}{vmA}{G}{forecast}, 3.0, 'its pairs');
like(UUT::_adaptive_selection_block({}, 'ad')->{reason}, qr/history holds no selection for this event/, 'no selection at all');
my $legacy = { '2026-09' => { SeasonalEventSnapshots => { ad => { AdaptiveForecast => { selection => { vmA => { G => pair() } } } } } } };
like(UUT::_adaptive_selection_block($legacy, 'ad')->{reason}, qr/2026-09 predates the statement of what it was made on/,
     'a selection written before selection_meta is not read');

# -- the selection as a whole --
is(UUT::_selection_run_verdict(meta(), \%CTX), undef, 'made for this run: used');
my @run_cases = (
    [ undef, qr/states nothing of what it was made on/, 'no meta' ],
    [ meta(semantics => 'other-1'), qr/'other-1' record, not a selected demand/, 'semantics' ],
    [ meta(quantity => 'weekly-raw-peak'), qr/selects 'weekly-raw-peak', not the profile requirement/, 'quantity' ],
    [ meta(event => 'other'), qr/made for event 'other'/, 'event' ],
    [ meta(source_event => 'strategic'), qr/candidates are of event 'strategic', not of the source event 'pp'/, 'source event' ],
    [ meta(target_occurrence => undef), qr/states no target occurrence/, 'no target' ],
    [ meta(target_occurrence => { %$TARGET, observed => 1 }), qr/2026-10-28 to 2026-10-31\) is already observed/, 'observed target' ],
    [ meta(target_occurrence => { %$TARGET, occurrenceStart => '2026-10-29' }), qr/for the occurrence 2026-10-29 to 2026-10-31; this run sizes 2026-10-28 to 2026-10-31/, 'start differs' ],
    [ meta(target_occurrence => { %$TARGET, occurrenceEnd => '2026-11-30' }), qr/this run sizes 2026-10-28 to 2026-10-31/, 'end differs' ],
    [ meta(data_ends => '2026-10-28'), qr/candidates' data ends 2026-10-28, within the occurrence it is for/, 'data from the target occurrence' ],
    [ meta(profile_definitions => undef), qr/states no profile definitions/, 'profiles not stated' ],
    [ meta(profile_definitions => 'flags-1:other'), qr/other profile definitions than this run's/, 'profiles differ' ],
    [ meta(profile_definitions => '0123abcd'), qr/other profile definitions than this run's/, 'a names-only fingerprint never matches' ],
    [ meta(exclusion_definitions => 'deadbeef'), qr/other exclusions than this run's/, 'exclusions differ' ],
    [ meta(record_definitions => { profile_definitions => 'flags-1:old', exclusion_definitions => 'NONE' }),
      qr/source event's records in 2026-09 were made on other profile definitions/, 'the candidates were made on other definitions' ],
);
foreach my $c (@run_cases)
{
    like(UUT::_selection_run_verdict($c->[0], \%CTX) // '', $c->[1], "withheld whole: $c->[2]");
}
like(UUT::_selection_run_verdict(meta(), { %CTX, target_start => undef }), qr/this run states no target occurrence/, 'the run states no target');
like(UUT::_selection_run_verdict(meta(), { %CTX, data_end => '2026-10-28' }), qr/starts on or before the end of this run's data \(2026-10-28\)/,
     'the target occurrence has begun within the run');
like(UUT::_selection_run_verdict(meta(), { %CTX, source_ran => 'pp-fallback' }), qr/ran on event 'pp-fallback'/, 'the source model fell back');
is(UUT::_selection_run_verdict(meta(record_definitions => { profile_definitions => undef, exclusion_definitions => undef }), \%CTX), undef,
   'a candidates month without records of the source event: the run checks decide');
is(UUT::_selection_run_verdict(meta(data_ends => '2026-09-12'), \%CTX), undef, 'candidates older than the run, for the same occurrence: used');
is(UUT::_selection_run_verdict(meta(data_ends => '2026-10-20'), \%CTX), undef,
   'candidates on later data than the run window, still before the occurrence: used');

# -- one pair --
my $M = meta();
is_deeply([ UUT::_selection_pair_verdict(pair(), $M) ], [ 2.5, undef ], 'tournament: the selected value');
is_deeply([ UUT::_selection_pair_verdict(pair(selection_mode => 'unopposed'), $M) ], [ 2.5, undef ], 'unopposed is used');
is_deeply([ UUT::_selection_pair_verdict(pair(selection_mode => 'bootstrap', chosen_model => 'ensemble_median', pi_lower => undef, pi_upper => undef), $M) ],
          [ 2.5, undef ], 'bootstrap median is used, without an interval');
is_deeply([ UUT::_selection_pair_verdict(pair(forecast => 0), $M) ], [ 0, undef ], 'a selected demand of zero is a demand');
is_deeply([ UUT::_selection_pair_verdict(pair(forecast => '1.5e3'), $M) ], [ 1500, undef ], 'an exponent within range is a number');
my @pair_cases = (
    [ undef, 'no selection for the pair' ],
    [ pair(selection_mode => 'no eligible forecast', forecast => undef), 'no eligible forecast' ],
    [ pair(selection_mode => 'ensemble'), "selection mode 'ensemble' not recognised" ],
    [ pair(forecast => undef), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => -0.1), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => 'NaN'), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => 'Inf'), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => '-Infinity'), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => '1e999'), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => 9**9**9), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => 'abc'), 'the selected value is not a finite number of zero or more' ],
    [ pair(forecast => [ 1 ]), 'the selected value is not a finite number of zero or more' ],
    [ pair(quantity => 'weekly-raw-peak'), 'the pair selects another quantity' ],
    [ pair(target_occurrence => { %$TARGET, occurrenceEnd => '2026-11-30' }), 'the pair is for another occurrence' ],
);
foreach my $c (@pair_cases)
{
    is_deeply([ UUT::_selection_pair_verdict($c->[0], $M) ], [ undef, $c->[1] ], "pair falls back: $c->[1]");
}

# -- the plan and its substitution --
my $sel = { vmA => { G => pair(), O => pair(selection_mode => 'bootstrap', chosen_model => 'ensemble_median', forecast => 1.1, pi_lower => undef, pi_upper => undef),
                     B => pair(selection_mode => 'no eligible forecast', forecast => undef), 'Peak_P-99W1' => pair(forecast => 9.9) },
            vmZ => { G => pair() } };     # an LPAR not in this run
my $h = { '2026-09' => { SeasonalEventSnapshots => { ad => { AdaptiveForecast => { selection => $sel, selection_meta => meta() } } } } };
my %ctx = (%CTX, vms => [ 'vmA', 'vmB' ], profiles => [ 'Peak_P-99W1', 'G', 'O', 'B' ]);
my $plan = UUT::_adaptive_selection_plan($h, \%ctx);
is($plan->{status}, 'used', 'the selection is used');
is_deeply([ sort keys %{ $plan->{used}{vmA} } ], [ 'G', 'O' ], 'two pairs of vmA take the selection');
is_deeply($plan->{modes}, { tournament => 1, bootstrap => 1 }, 'counted by mode');
is($plan->{fallback}{vmA}{B}, 'no eligible forecast', 'no eligible forecast falls back');
is($plan->{fallback}{vmB}{G}, 'no selection for the pair', 'an LPAR without a selection falls back');
ok(!exists $plan->{used}{vmA}{'Peak_P-99W1'} && !exists $plan->{fallback}{vmA}{'Peak_P-99W1'}, 'the measurement profile is not part of the plan');
ok(!exists $plan->{used}{vmZ}, 'an LPAR outside the run is ignored');

my $map = { CoreResults => { SizingDemandInput => { G => 2.0, O => 0.9, B => 1.4 },
                             ProfileValues => { 'Peak_P-99W1' => 3.3, G => 2.0, O => 0.9, B => 1.4 } } };
UUT::_apply_selected_demand('vmA', $map, $plan, [ map { +{ name => $_ } } @{ $ctx{profiles} } ]);
my $core = $map->{CoreResults};
is_deeply([ @{ $core->{SizingDemandInput} }{qw(G O B)} ], [ 2.5, 1.1, 1.4 ], 'the selected value is the sizing input; the fallback keeps the model');
is_deeply([ @{ $core->{ProfileValues} }{qw(G O B)} ], [ 2.5, 1.1, 1.4 ], 'and the profile value the chain plans for');
is($core->{ProfileValues}{'Peak_P-99W1'}, 3.3, 'the measured peak is untouched');
ok(!exists $core->{SizingDemandInput}{'Peak_P-99W1'}, 'and is no sizing input');
is_deeply([ @{ $core->{DemandSource}{G} }{qw(source demand sourceModelDemand model mode piLower piUpper candidatesStoredIn selectionStoredIn)} ],
          [ 'selection', 2.5, 2.0, 'holt_event', 'tournament', 2.1, 2.9, '2026-09', '2026-09' ], 'provenance kept apart, with the source model demand');
is_deeply($core->{DemandSource}{G}{target}, { start => '2026-10-28', end => '2026-10-31' }, 'and the occurrence');
is_deeply([ @{ $core->{DemandSource}{O} }{qw(mode piLower piUpper)} ], [ 'bootstrap', undef, undef ], 'bootstrap carries no interval');
is_deeply([ @{ $core->{DemandSource}{B} }{qw(source model demand reason)} ], [ 'source model', 'predictive_peak', 1.4, 'no eligible forecast' ],
          'the fallback states its model and reason');
my $nomodel = { CoreResults => { ProfileValues => { G => 1.0 } } };
UUT::_apply_selected_demand('vmB', $nomodel, $plan, [ { name => 'G' } ]);
is($nomodel->{CoreResults}{DemandSource}{G}{model}, 'engine profile value', 'no source forecast and no selection: the engine value, said so');
ok(!exists $nomodel->{CoreResults}{SizingDemandInput}{G}, 'no demand input is invented');

# -- a withheld selection: every pair falls back with the same reason --
my $stale = { '2026-09' => { SeasonalEventSnapshots => { ad => { AdaptiveForecast => { selection => $sel,
              selection_meta => meta(target_occurrence => { %$TARGET, observed => 1 }) } } } } };
my $withheld = UUT::_adaptive_selection_plan($stale, \%ctx);
is($withheld->{status}, 'withheld', 'a stale selection is withheld whole');
is_deeply($withheld->{used}, {}, 'no pair takes it');
is(scalar(keys %{ $withheld->{reasons} }), 1, 'one reason');
is((values %{ $withheld->{reasons} })[0], 6, 'for all six pairs of the run');

# -- labels and the rationale --
is(UUT::_demand_source_label($core->{DemandSource}{G}), 'Selected: holt_event (tournament)', 'label: tournament');
is(UUT::_demand_source_label($core->{DemandSource}{O}), 'Selected: bootstrap median', 'label: bootstrap');
is(UUT::_demand_source_label($core->{DemandSource}{B}), 'Source model: predictive peak', 'label: fallback');
my $text = join("\n", UUT::_adaptive_selection_report_lines($plan));
like($text, qr/Selection: stored in 2026-09, candidates of 2026-09, for the occurrence 2026-10-28 to 2026-10-31\./, 'the section states the selection');
like($text, qr/The candidates' data ends 2026-09-30; this run's window ends 2026-09-30\./, 'and both data ends');
like($text, qr/Sized from the selection: 2 pairs \(1 bootstrap median, 1 tournament\)\./, 'counts by mode');
like($text, qr/Sized from the source model: 4 pairs\./, 'counts of the fallback');
like($text, qr/^\s+3  no selection for the pair$/m, 'reasons counted once each');
like($text, qr/Not assessed in this run: PowerHA role sizing \(standard runs only\) and frame capacity\. Neither has\n\s+been checked/,
     'the checks not made are stated');
unlike($text, qr/vm[ABZ]/, 'no LPAR is named');
like(join("\n", UUT::_adaptive_selection_report_lines($withheld)), qr/Selection not used: its target occurrence .* is already observed in history\./,
     'a withheld selection says why');

done_testing();
