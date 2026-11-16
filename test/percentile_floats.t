#!/usr/bin/env perl
# ==============================================================================
# calculate_percentile (nfit): the optional { floats, sorted } arguments must not
# change any result. The sub is extracted from ../nfit and compared, on lists of
# unpacked floats, with the plain call: random values over many magnitudes, zeros,
# negatives, values that print in exponent form (dropped by the sub's pattern),
# NaN and infinities, sorted and unsorted.
# Run from the test directory: perl percentile_floats.t
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $source = do { open my $fh, '<', '../nfit' or die "cannot open ../nfit: $!"; local $/; <$fh> };
my ($sub) = $source =~ /^(sub calculate_percentile\n\{.*?\n\})\n/ms or die "calculate_percentile not found";
eval $sub; die $@ if $@;

sub same
{
    my ($x, $y) = @_;
    return 1 if !defined $x && !defined $y;
    return 0 if !defined $x || !defined $y;
    return unpack('H*', pack('d', $x)) eq unpack('H*', pack('d', $y));
}

srand(20261116);
my $inf = 9**9**9;
my $nan = -sin($inf);
my @specials = (0, -0.0, 0.001, -0.001, 0.00099999, 1e-4, 9.9e-5, 1e-7, 1e14, 99999999999999.98, 1e15, -1e15, 5e-324);
my ($cases, $bad_floats, $bad_sorted) = (0, 0, 0);
for my $round (1 .. 400)
{
    my $n = 1 + int(rand(60));
    my @list;
    for (1 .. $n)
    {
        my $r = rand();
        my $v = $r < 0.10 ? $specials[int(rand(@specials))]
              : $r < 0.13 ? $nan
              : $r < 0.16 ? ($r < 0.145 ? $inf : -$inf)
              : (rand() * 2 - 1) * 10 ** (int(rand(24)) - 12);
        push @list, $v;
    }
    @list = unpack('f<*', pack('f<*', @list)) if $round % 2;      # as the engine holds them
    my @clean  = grep { $_ == $_ && abs($_) != $inf } @list;
    my @sorted = sort { $a <=> $b } @clean;
    for my $p (0, 0.1, 15, 25, 50, 90, 95, 99.75, 99.9, 100)
    {
        $cases++;
        $bad_floats++ unless same(calculate_percentile(\@list, $p), calculate_percentile(\@list, $p, { floats => 1 }));
        $bad_sorted++ unless same(calculate_percentile(\@sorted, $p),
                                  calculate_percentile(\@sorted, $p, { floats => 1, sorted => 1 }));
        # A list wrongly declared sorted is still handled when the filter removes a value.
    }
}
is($bad_floats, 0, "floats => 1 gives the plain result ($cases cases, unsorted lists with NaN and infinities)");
is($bad_sorted, 0, "floats => 1, sorted => 1 gives the plain result on sorted lists");

my @with_strings = ('1.5', 'N/A', '2.5', ' 3.5', '1e3', undef, '4.5');
ok(same(calculate_percentile(\@with_strings, 50), 2.5), 'the plain call still filters strings by the pattern');
is(calculate_percentile([], 50, { floats => 1 }), undef, 'an empty list gives undef');
is(calculate_percentile([ $nan, 1e-9 ], 50, { floats => 1, sorted => 1 }), undef, 'a list with nothing admitted gives undef');
done_testing();
