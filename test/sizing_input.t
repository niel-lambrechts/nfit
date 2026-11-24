#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/sizing_input.t
# PURPOSE:   The requirement the sizing chain starts from (_sizing_start_requirement,
#            extracted from nfit-profile): a seasonal model's demand when one is
#            given, with no further growth; otherwise the engine's pre-growth
#            base and its growth, as before.
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

done_testing();
