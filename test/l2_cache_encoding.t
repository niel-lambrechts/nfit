#!/usr/bin/perl
# Regression cover for UTF-8 handling between the nfit engine, its L2 results
# cache and nfit-profile. Extracts the live lookup_cached_result from nfit, so
# the assertions run against production source, not a copy.
use strict;
use warnings;
use JSON;
use File::Temp qw(tempdir);

my $SRC = $ENV{NFIT_ENGINE} // '../nfit';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;

my ($tests, $fails) = (0, 0);
sub ok
{
    my ($cond, $name) = @_;
    $tests++;
    if ($cond) { print "ok $tests - $name\n"; }
    else       { $fails++; print "not ok $tests - $name\n"; }
}

# 1. Static guard: raw non-ASCII may appear in nfit only in comments or in
#    STDERR progress output. Anything else can reach JSON and be encoded twice.
my @offending;
my $ln = 0;
for my $line (split /\n/, $src)
{
    $ln++;
    next unless $line =~ /[^\x00-\x7F]/;
    next if $line =~ /^\s*#/;
    (my $code = $line) =~ s/\s+#.*$//;
    next unless $code =~ /[^\x00-\x7F]/;
    next if $code =~ /STDERR/;
    push @offending, $ln;
}
ok(!@offending, 'no raw non-ASCII literals outside comments and STDERR in nfit'
    . (@offending ? " (lines @offending)" : ''));

# 2. Round trip through the L2 cache preserves characters exactly.
$src =~ /^(sub lookup_cached_result\s*\{.*?^\})/ms
    or die "could not extract lookup_cached_result from $SRC\n";
my $sub_code = $1;
eval "package UUT; use JSON; $sub_code; 1" or die "eval of lookup_cached_result failed: $@";

my $note   = "Strong trend detected (|\x{03C4}|=0.5055 \x{2265} 0.4)";
my $result = [ { vmName => 'TEST_VM', note => $note } ];
my $key    = 'test-key';
my $dir    = tempdir(CLEANUP => 1);
my $cache  = "$dir/.nfit.cache.results";

# Written exactly as save_result_to_cache writes: character-mode encoder,
# UTF-8 output layer.
open my $w, '>:encoding(utf8)', $cache or die "cannot write $cache: $!";
print $w JSON->new->pretty->canonical->encode({ $key => $result });
close $w;

my $back = UUT::lookup_cached_result($key, $cache);
ok(ref($back) eq 'ARRAY' && defined $back->[0]{note} && $back->[0]{note} eq $note,
   'cached text round-trips as characters');
ok(ref($back) eq 'ARRAY'
   && JSON->new->utf8->canonical->encode($back->[0]) eq JSON->new->utf8->canonical->encode($result->[0]),
   'cached-path JSON bytes equal fresh-path JSON bytes');
ok(-f $cache, 'a cache file holding non-ASCII text is not reset as corrupt');

print "\n$tests tests, $fails failures\n";
exit($fails ? 1 : 0);
