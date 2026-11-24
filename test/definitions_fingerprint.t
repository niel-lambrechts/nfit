#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/definitions_fingerprint.t
# PURPOSE:   The fingerprints a seasonal record states of the definitions it was
#            made on (_profile_definitions_fingerprint,
#            _exclusion_definitions_fingerprint, extracted from nfit-profile).
#            The profile fingerprint covers each profile's engine flags: a change
#            of percentile, window or hours changes it; the order of the
#            profiles and spacing in the flags do not. It names its scheme, so a
#            value written before the scheme (names only) is never taken for one.
#            The injected mandatory peak profile carries its flags under the key
#            every reader uses.
# USAGE:     perl test/definitions_fingerprint.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} or die "NFIT_PROFILE not set\n";
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
foreach my $name (qw(_profile_definitions_fingerprint _exclusion_definitions_fingerprint))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    eval "package UUT; $1 1;" or die "harness eval failed: $@\n";
}

my @base = (
    { name => 'Peak_P-99W1', flags => '-p 99.75 -w 1 --filter-above-perc 30' },
    { name => 'G3-95W15',    flags => '-p 95 -w 15' },
    { name => 'O3-95W15',    flags => '-p 95 -w 15 --online' },
);
my $clone = sub
{
    my ($name, $flags) = @_;
    return [ map { +{ %$_, ($_->{name} eq $name ? (flags => $flags) : ()) } } @base ];
};
my $fp = UUT::_profile_definitions_fingerprint(\@base);

like($fp, qr/^flags-1:[0-9a-f]{32}$/, 'the fingerprint names its scheme');
isnt(UUT::_profile_definitions_fingerprint($clone->('G3-95W15', '-p 98 -w 15')), $fp, 'a different percentile changes it');
isnt(UUT::_profile_definitions_fingerprint($clone->('G3-95W15', '-p 95 -w 5')), $fp, 'a different window changes it');
isnt(UUT::_profile_definitions_fingerprint($clone->('O3-95W15', '-p 95 -w 15 --batch')), $fp, 'different hours change it');
isnt(UUT::_profile_definitions_fingerprint($clone->('Peak_P-99W1', '-p 99 -w 1 --filter-above-perc 30')), $fp,
     'the measurement profile is covered too');
isnt(UUT::_profile_definitions_fingerprint([ @base[0, 1] ]), $fp, 'a profile removed changes it');
is(UUT::_profile_definitions_fingerprint([ reverse @base ]), $fp, 'the order of the profiles does not');
is(UUT::_profile_definitions_fingerprint($clone->('G3-95W15', "  -p 95\t -w  15 ")), $fp, 'spacing in the flags does not');

# The value records held before the scheme: a hash of the names alone, no prefix. It is not the
# fingerprint of the same definitions, so a reader can never certify one against the other.
require Digest::MD5;
my $names_only = Digest::MD5::md5_hex(join('|', map { "$_->{name}=" } sort { $a->{name} cmp $b->{name} } @base));
isnt($fp, $names_only, 'a names-only value never equals the fingerprint of the same definitions');
unlike($names_only, qr/^flags-1:/, 'and it carries no scheme');

# Exclusions: unchanged in value.
is(UUT::_exclusion_definitions_fingerprint({}), 'NONE', 'no exclusions');
is(UUT::_exclusion_definitions_fingerprint({ exclude_dates => '2026-01-02, 2026-01-01' }),
   Digest::MD5::md5_hex('2026-01-01,2026-01-02'), 'excluded dates in order, spacing ignored');
isnt(UUT::_exclusion_definitions_fingerprint({ exclude_dates => '2026-01-03' }),
     UUT::_exclusion_definitions_fingerprint({ exclude_dates => '2026-01-02' }), 'other dates, another fingerprint');

# The injected mandatory peak profile: its flags are under 'flags', where the manifest, the run-queue
# key and the fingerprint read them (not under the configuration file's key name).
$src =~ /if \(!\$mandatory_peak_present\) \{\s*push \@profiles, \{(.*?)\};/s or die "could not find the injected profile\n";
my $injected = $1;
like($injected, qr/^\s*flags\s*=>\s*\$DEFAULT_MANDATORY_PEAK_PROFILE_FLAGS,/m, 'the injected profile carries its flags under flags');
unlike($injected, qr/nfit_flags/, 'and not under nfit_flags');
unlike($src, qr/->\{nfit_flags\}|\{nfit_flags\}/, 'no profile reader looks for nfit_flags');

done_testing();
