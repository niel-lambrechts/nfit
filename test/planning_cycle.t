#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/planning_cycle.t
# PURPOSE:   The comparisons a planning cycle rests on (v10 C7 with B11),
#            extracted from nfit-profile: _resolved_options, _option_differences,
#            _cycle_context_differences, _capture_identity.
#            - options are compared as resolved: option and value together, an
#              option given twice by the value in effect, frame options left out;
#            - a context differs by its analysis end, an executable or a
#              configuration file, an optional file appearing or disappearing
#              included;
#            - a capture is identified by its manifest revisions together with
#              the size and time of the files: a file replaced under an
#              unchanged manifest is a change; without revisions the identity
#              is labelled as the weaker check.
# USAGE:     perl test/planning_cycle.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use File::Temp qw(tempdir);
use File::Spec;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = "package UUT; use JSON; use File::Spec; our \$POOL_OBSERVATIONS_FILE = '.nfit.cache.pool.observations.csv.gz';\n";
foreach my $name (qw(_resolved_options _option_differences _cycle_context_differences _capture_identity _cycle_canonical))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n";
}
eval "$code 1;" or die "harness eval failed: $@\n";

# -- options as resolved --
my ($dir, $policy, $decay, $end, $verbose, $unset) = ('/x/stage/A', 'normal', 1, '2026-09-21', 2, undef);
my @spec = ('nmondir=s' => \$dir, 'guarantee-policy=s' => \$policy, 'decay-over-states' => \$decay, 'enddate|e=s' => \$end,
            'v|verbose+' => \$verbose, 'startdate|s=s' => \$unset, 'cut-protection!' => \$unset);
my $options = UUT::_resolved_options(\@spec, { nmondir => 1, v => 1 });
is_deeply($options, { 'guarantee-policy' => 'normal', 'decay-over-states' => '1', enddate => '2026-09-21' },
          'the options in effect, by name; the frame and verbosity left out; an option not given is absent');
is_deeply([ UUT::_option_differences($options, { enddate => '2026-09-21', 'decay-over-states' => '1', 'guarantee-policy' => 'normal' }) ], [],
          'the same options in another order are the same');
is_deeply([ UUT::_option_differences($options, { %$options, 'guarantee-policy' => 'strict' }) ],
          [ "--guarantee-policy ('normal' here, 'strict' there)" ], 'another value is named with its option');
is_deeply([ UUT::_option_differences($options, { %$options, startdate => '2026-08-01' }) ],
          [ "--startdate (not set here, '2026-08-01' there)" ], 'an option one side did not give');

# -- the shared context --
my $context = sub
{
    my (%over) = @_;
    return { analysisEnd => '2026-10-31', executables => { 'nfit-profile' => { version => '10.0', sha256 => 'p1' }, nfit => { version => '10.0', sha256 => 'e1' } },
             configuration => { profiles => 'f1', tiers => 't1', vmConfig => 'absent', seasonality => 's1' }, %over };
};
is_deeply([ UUT::_cycle_context_differences($context->(), $context->()) ], [], 'the same context');
is_deeply([ UUT::_cycle_context_differences($context->(analysisEnd => '2026-09-30'), $context->()) ],
          [ 'the analysis end date (2026-09-30 here, 2026-10-31 in the cycle)' ], 'another analysis end');
like(join('|', UUT::_cycle_context_differences($context->(executables => { 'nfit-profile' => { version => '10.0', sha256 => 'p2' }, nfit => { version => '10.0', sha256 => 'e1' } }), $context->())),
     qr/^nfit-profile \(version 10\.0 here, 10\.0 in the cycle; the program file differs\)$/, 'a changed executable of the same version');
is_deeply([ UUT::_cycle_context_differences($context->(configuration => { profiles => 'f2', tiers => 't1', vmConfig => 'c1', seasonality => 'absent' }), $context->()) ],
          [ 'the profile file (changed)', 'the seasonality file (not there now)', 'the VM configuration file (--config) (not there when the cycle began)' ],
          'a changed file, one that went and one that appeared');

# -- capture identity --
my $cache = tempdir(CLEANUP => 1);
my $put = sub { my ($name, $text) = @_; open my $o, '>', File::Spec->catfile($cache, $name) or die; print {$o} $text; close $o; };
$put->('.nfit.cache.data', "a\n");
$put->('.nfit.cache.states', "{}\n");
$put->('.nfit.cache.manifest', "cache_build_status: success\n");
my $plain = UUT::_capture_identity($cache);
is($plain->{kind}, 'size and time (weaker)', 'no revisions in the manifest: size and time, labelled weaker');
is($plain->{pool}, 'absent', 'no pool observations: recorded as absent');
$put->('.nfit.cache.manifest', "data_revision: sha256:aa\nstates_revision: sha256:bb\n");
my $hashed = UUT::_capture_identity($cache);
is_deeply([ $hashed->{kind}, $hashed->{data}{revision}, $hashed->{data}{size} ], [ 'content hash', 'sha256:aa', 2 ], 'manifest revisions with the file sizes');
is(UUT::_cycle_canonical(UUT::_capture_identity($cache)), UUT::_cycle_canonical($hashed), 'an unchanged capture has the same identity');
$put->('.nfit.cache.data', "a\nb\n");
isnt(UUT::_cycle_canonical(UUT::_capture_identity($cache)), UUT::_cycle_canonical($hashed), 'a file replaced under an unchanged manifest is a change');
$put->('.nfit.cache.data', "a\n");
utime(1, 1, File::Spec->catfile($cache, '.nfit.cache.data'));
isnt(UUT::_cycle_canonical(UUT::_capture_identity($cache)), UUT::_cycle_canonical($hashed), 'the same size at another time is a change');
$put->('.nfit.cache.pool.observations.csv.gz', "p");
is(UUT::_capture_identity($cache)->{kind}, 'size and time (weaker)', 'pool observations without a revision: the weaker check');
$put->('.nfit.cache.manifest', "data_revision: sha256:aa\nstates_revision: sha256:bb\npool_revision: sha256:cc\n");
is_deeply([ UUT::_capture_identity($cache)->{kind}, UUT::_capture_identity($cache)->{pool}{revision} ], [ 'content hash', 'sha256:cc' ],
          'the pool observations are part of the identity');

done_testing();
