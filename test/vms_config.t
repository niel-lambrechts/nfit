#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/vms_config.t
# PURPOSE:   Regression cover for the etc/nfit.vms.cfg reader (v10 stage 3.1b):
#            TIERs read as before, the reserved [GLOBAL] section, the PowerHA
#            and VIOS keys with their warnings, and the PowerHA clusters formed
#            from ha_peer set on either node. The live subroutines are
#            extracted from nfit-profile and evaluated in an isolated package.
# USAGE:     perl test/vms_config.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use File::Temp qw(tempfile);

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(parse_vms_config _ha_clusters _report_vms_config))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval "package UUT;\n$code\n1;" or die "harness eval failed: $@\n";

sub cfg
{
    my ($text) = @_;
    my ($out, $path) = tempfile(SUFFIX => '.cfg', UNLINK => 1);
    binmode($out, ':encoding(utf8)');
    print {$out} $text;
    close $out;
    return UUT::parse_vms_config($path);
}

# --- TIER: as the reader before 3.1b --------------------------------------------
my $c = cfg("# comment\n[vmA]\ntier = g2   # trailing\n[ vm B ]\n; note\nTIER=O 3\n[vmC]\nother = 1\n"
          . "[vmD]\ntier = P\ntier = B1\n[vmE]\n\ntier= b4-custom\n");
is_deeply($c->{tiers}, { vmA => 'G2', 'vm B' => 'O3', vmD => 'P', vmE => 'B4-CUSTOM' },
    'TIER: upper case, spaces removed, the first in a section applies');
is(scalar(grep { /unknown setting 'other'/ } @{ $c->{warnings} }), 1, 'an unknown key is warned');
is(UUT::parse_vms_config('/nonexistent/nfit.vms.cfg')->{exists}, 0, 'a missing file: nothing read');

# --- [GLOBAL], PowerHA and VIOS keys --------------------------------------------
$c = cfg(<<'CFG');
[GLOBAL]
standby_share = 0.25
tier = G1
[db1a]
tier = G1
ha_peer = db1b
ha_role = Auto
[db1b]
standby_share = 0.3
ha_peer = db1a, db1a
[mq1]
ha_role = primary
standby_share = 1.5
lpar_type = VIOS
vios_peer = mq1
[x1]
foo = 1
foo = 2
bar
CFG
is_deeply($c->{global}, { standby_share => 0.25 }, '[GLOBAL]: the default share');
ok(!exists $c->{tiers}{GLOBAL}, '[GLOBAL]: an LPAR setting there is not applied');
ok((grep { /'tier' is an LPAR setting, not valid in \[GLOBAL\]/ } @{ $c->{warnings} }), '[GLOBAL]: the LPAR setting is warned');
is_deeply($c->{vms}{db1a}, { tier => 'G1', ha_peer => ['db1b'], ha_role => 'auto' }, 'PowerHA keys read; ha_role in lower case');
is_deeply($c->{vms}{db1b}, { standby_share => 0.3, ha_peer => ['db1a'] }, 'a peer named twice is kept once');
is_deeply($c->{vms}{mq1}, { lpar_type => 'vios' }, 'invalid values ignored; lpar_type read');
ok((grep { /ha_role must be one of auto, active, standby; 'primary' ignored/ } @{ $c->{warnings} }), 'an invalid ha_role is warned');
ok((grep { /standby_share must be a fraction above 0 and at most 1/ } @{ $c->{warnings} }), 'an invalid share is warned');
ok((grep { /vios_peer names its own section/ } @{ $c->{warnings} }), 'a self-reference is warned');
is(scalar(grep { /unknown setting 'foo'/ } @{ $c->{warnings} }), 1, 'an unknown key is warned once');
ok((grep { /^line 19: not a 'key = value' setting/ } @{ $c->{warnings} }), 'a line that is not a setting is warned with its number');

# --- Clusters from ha_peer set on either node ------------------------------------
$c = cfg("[a1]\nha_peer = a2\n[b2]\ntier = G2\n[b1]\nha_peer = b2\n[c1]\nha_peer = c2, c3\n[d2]\nha_peer = d3\n[d1]\nha_peer = d2\n"
       . "[e1]\nha_peer = E2\n[e2]\ntier = G3\n[solo]\ntier = G1\n");
is_deeply($c->{clusters}, [ [qw(a1 a2)], [qw(b1 b2)], [qw(c1 c2 c3)], [qw(d1 d2 d3)], [qw(e1 e2)] ],
    'clusters: one ha_peer entry is enough; peers without a section join; chains and lists form one cluster');
ok((grep { /\[e1\] ha_peer 'E2' matches \[e2\] only by letter case; \[e2\] is used/ } @{ $c->{warnings} }),
    'a peer matching a section only by letter case is read as it, with a warning');
is_deeply(UUT::_ha_clusters({ x => { ha_peer => ['y'] }, y => { ha_peer => ['x'] } }), [ [qw(x y)] ], 'both directions: one cluster');
is_deeply(UUT::_ha_clusters({}), [], 'no ha_peer: no clusters');

# --- The tracked sample is valid ---------------------------------------------------
(my $sample = $SRC) =~ s{nfit-profile$}{etc/nfit.vms.cfg.sample};
$c = UUT::parse_vms_config($sample);
ok($c->{exists}, 'the sample exists');
is_deeply($c->{warnings}, [], 'the sample reads without warnings');
is_deeply([ keys %{ $c->{vms} } ], [], 'as shipped, the sample configures no LPAR');
# The examples, enabled (leading ';' removed), are valid settings.
open my $in, '<', $sample or die;
my $enabled = join('', map { s/^;(?=\[|[A-Za-z_]+\s*=)//r } <$in>);
close $in;
$c = cfg($enabled);
is_deeply($c->{warnings}, [], 'the enabled examples read without warnings');
is_deeply($c->{clusters}, [ [qw(db01a db01b)], [qw(mq01a mq01b)] ], 'the example clusters');
is_deeply($c->{vms}{vios01a}, { lpar_type => 'vios', vios_peer => ['vios01b'] }, 'the example VIOS pair');

done_testing();
