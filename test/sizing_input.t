#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/sizing_input.t
# PURPOSE:   The requirement the sizing chain starts from (_sizing_start_requirement,
#            extracted from nfit-profile): a seasonal model's demand when one is
#            given, with no further growth; otherwise the engine's pre-growth
#            base and its growth, as before.
#            What the historical-evidence checks judge (_evidence_physc): the
#            engine's value of the window in a seasonal run, never the model's
#            forecast; the profile value in an ordinary run, as before.
# USAGE:     perl test/sizing_input.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use Scalar::Util qw(looks_like_number);

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
$src =~ /^(sub _sizing_start_requirement\s*\{.*?^\})/ms or die "could not extract _sizing_start_requirement\n";
eval "package UUT; use Scalar::Util qw(looks_like_number); $1 1;" or die "harness eval failed: $@\n";
$src =~ /^(sub _evidence_physc\s*\{.*?^\})/ms or die "could not extract _evidence_physc\n";
eval "package UUT; $1 1;" or die "harness eval failed: $@\n";

# A seasonal model's demand, with the engine's base and growth both present.
my ($start, $growth, $source) = UUT::_sizing_start_requirement(2.40, 2.00, 0.30);
is($start, 2.40, 'the model demand is the starting requirement, not the engine base');
is($growth, 0, 'the engine growth is not applied to it again');
is($source, 'seasonal model demand', 'the source is stated');
is($start + $growth, 2.40, 'growth is in the requirement once: 2.40, not 2.70 and not 2.30');

# The ordinary path: no model demand.
($start, $growth, $source) = UUT::_sizing_start_requirement(undef, 2.00, 0.30);
is_deeply([ $start, $growth, $source ], [ 2.00, 0.30, 'engine profile value' ], 'ordinary run: the engine base and its growth, as before');
($start, $growth) = UUT::_sizing_start_requirement(undef, 2.00, undef);
is_deeply([ $start, $growth ], [ 2.00, 0 ], 'no growth given: none applied');
($start, $growth, $source) = UUT::_sizing_start_requirement('N/A', 1.50, 0.10);
is_deeply([ $start, $growth, $source ], [ 1.50, 0.10, 'engine profile value' ], 'a non-numeric model demand is no input');
($start, $growth, $source) = UUT::_sizing_start_requirement(0, 1.50, 0.10);
is_deeply([ $start, $growth, $source ], [ 0, 0, 'seasonal model demand' ], 'a demand of zero is still the model demand');
($start, $growth, $source) = UUT::_sizing_start_requirement(undef, undef, 0.10);
ok(!defined $start && $source eq 'engine profile value', 'no base at all: undefined, as before');

# -- what the historical-evidence checks judge --
# A seasonal run: the forecast is 3.80 of a 4.00 ceiling; the window measured 2.00 with 0.10 growth.
my ($evidence, $from) = UUT::_evidence_physc(3.80, 3.80, 2.00, 0.10);
is_deeply([ $evidence, $from ], [ 2.10, 'engine value of the window' ], 'seasonal run: the measured value, not the forecast');
($evidence, $from) = UUT::_evidence_physc(1.20, 1.20, 2.00, undef);
is($evidence, 2.00, 'a forecast below the measurement does not lower the evidence either');
# An ordinary run: the profile value, exactly as before.
is_deeply([ UUT::_evidence_physc(undef, 2.30, 2.00, 0.30) ], [ 2.30, 'profile value' ], 'ordinary run: the profile value');
# A seasonal run without an engine value for the profile: nothing better is at hand, and it says so.
is_deeply([ UUT::_evidence_physc(3.80, 3.80, undef, 0.10) ], [ 3.80, 'profile value (no engine value for the window)' ], 'no engine value: stated');

done_testing();
