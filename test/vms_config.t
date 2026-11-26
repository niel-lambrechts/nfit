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
for my $name (qw(parse_vms_config _ha_clusters _report_vms_config _vms_config_settings _vms_config_global _vms_config_mgsys
                 _vms_config_defaults _policy_setting))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nour \$vms_config;\n$code\n1;" or die "harness eval failed: $@\n";

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
ok((grep { /^line \d+, \[mq1\]: ha_role must be one of auto, active, standby; 'primary' is not/ } @{ $c->{errors} }), 'an invalid ha_role is an error, with its line and stanza');
ok((grep { /standby_share must be a fraction above 0 and at most 1 \(for example 0.25\); '1.5' is not/ } @{ $c->{errors} }), 'an invalid share is an error');
is(scalar(@{ $c->{errors} }), 2, 'both errors are listed, not only the first');
ok((grep { /vios_peer names its own section/ } @{ $c->{warnings} }), 'a self-reference is warned');
is(scalar(grep { /unknown setting 'foo'/ } @{ $c->{warnings} }), 1, 'an unknown key is warned once');
ok((grep { /^line 19: not a 'key = value' setting/ } @{ $c->{warnings} }), 'a line that is not a setting is warned with its number');

# --- Sizing policy keys (design revision 2) ---------------------------------------
$c = cfg("[GLOBAL]\nvios_sizing = combined\nvios_takeover_margin = 0.10\nha_sizing = independent\n"
       . "[v1]\nlpar_type = vios\nvios_sizing = Match_Larger\nvios_takeover_margin = 1.5\n[n1]\nha_sizing = standby_share\nha_sizing = bogus\n");
is_deeply($c->{global}, { vios_sizing => 'combined', vios_takeover_margin => 0.10, ha_sizing => 'independent' }, '[GLOBAL]: the policy defaults');
is_deeply($c->{vms}{v1}, { lpar_type => 'vios', vios_sizing => 'match_larger' }, 'vios_sizing read; an invalid margin ignored');
ok((grep { /vios_takeover_margin must be a fraction from 0 to 1/ } @{ $c->{warnings} }), 'an invalid margin is warned');
ok((grep { /\[n1\]: ha_sizing must be one of independent, standby_share, standby_fixed; 'bogus' is not/ } @{ $c->{errors} }), 'an invalid ha_sizing is an error');
ok(!(grep { /vios_takeover_margin/ } @{ $c->{errors} }), 'an invalid setting that is not PowerHA policy stays a warning');
is($c->{vms}{n1}{ha_sizing}, 'standby_share', 'ha_sizing read');

# --- [DEFAULT] is not [GLOBAL] ----------------------------------------------------
$c = cfg("[DEFAULT]\nstandby_share = 0.30\n");
is_deeply($c->{global}, {}, '[DEFAULT]: not read as the defaults');
ok((grep { /^line 1: \[DEFAULT\] is read as the section of an LPAR of that name; defaults belong in \[GLOBAL\]/ } @{ $c->{warnings} }),
   '[DEFAULT]: warned');

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
is_deeply($c->{errors}, [], 'the sample reads without errors');
is_deeply([ keys %{ $c->{vms} } ], [], 'as shipped, the sample configures no LPAR');
# The examples, enabled (leading ';' removed), are valid settings.
open my $in, '<', $sample or die;
my $enabled = join('', map { s/^;(?=\[|[A-Za-z_]+\s*=)//r } <$in>);
close $in;
$c = cfg($enabled);
is_deeply($c->{warnings}, [], 'the enabled examples read without warnings');
is_deeply($c->{clusters}, [ [qw(db01a db01b)], [qw(mq01a mq01b)] ], 'the example clusters');
is_deeply($c->{vms}{vios01a}, { lpar_type => 'vios', vios_peer => ['vios01b'] }, 'the example VIOS pair');

# --- [MGSYS:<name>]: defaults for one managed system ------------------------------
$c = cfg("[GLOBAL]\nha_sizing = standby_share\nstandby_share = 0.25\n"
       . "[MGSYS:FRAME_B]\nha_sizing = independent\nvios_takeover_margin = 0.10\n"
       . "[ mgsys : Frame_C ]\nstandby_share = 0.5\nvios_sizing = match_larger\n[n1]\nha_peer = n2\n");
is_deeply($c->{errors}, [], 'managed-system stanzas read without errors');
is_deeply($c->{warnings}, [], 'and without warnings');
is_deeply($c->{mgsys}, { FRAME_B => { ha_sizing => 'independent', vios_takeover_margin => 0.10 },
                         Frame_C => { standby_share => 0.5, vios_sizing => 'match_larger' } },
          'the defaults of each managed system; MGSYS in any letter case, the name as written');
is_deeply($c->{global}, { ha_sizing => 'standby_share', standby_share => 0.25 }, '[GLOBAL] is not changed by them');
ok(!exists $c->{vms}{'MGSYS:FRAME_B'}, 'a managed-system stanza is not an LPAR');
is_deeply($c->{clusters}, [ [qw(n1 n2)] ], 'clusters as before');

$c = cfg("[MGSYS:FRAME_B]\nha_peer = n2\ntier = P\nha_role = standby\nha_sizing = sometimes\n[MGSYS:]\nha_sizing = independent\n"
       . "[MGSYS:FRAME_B]\nstandby_share = 0.9\n[MGSYS:FRAME_D]\nstandby_share =\nfoo = 1\n");
ok((grep { /^line 2, \[MGSYS:FRAME_B\]: 'ha_peer' is an LPAR setting; a managed-system stanza takes defaults only/ } @{ $c->{errors} }),
   'an LPAR setting in a managed-system stanza is an error');
is(scalar(grep { /is an LPAR setting/ } @{ $c->{errors} }), 3, 'each of them');
ok((grep { /\[MGSYS:FRAME_B\]: ha_sizing must be one of/ } @{ $c->{errors} }), 'an invalid value there is an error');
ok((grep { /^line 6, \[MGSYS:\]: a managed-system stanza needs the managed system's name/ } @{ $c->{errors} }), 'a stanza without a name is an error');
ok((grep { /^line 8, \[MGSYS:FRAME_B\]: this managed system has a stanza already/ } @{ $c->{errors} }), 'a second stanza for a managed system is an error');
ok((grep { /\[MGSYS:FRAME_D\]: 'standby_share' has no value/ } @{ $c->{errors} }), 'a policy setting without a value is an error');
is_deeply($c->{mgsys}, { FRAME_B => {}, FRAME_D => {} }, 'nothing is taken from a refused stanza or setting');
is(scalar(grep { /unknown setting 'foo'/ } @{ $c->{warnings} }), 1, 'an unknown setting stays a warning');
is(scalar(@{ $c->{errors} }), 7, 'every error is listed');

# --- Fixed standby entitlement (B13) -----------------------------------------------
$c = cfg("[GLOBAL]\nha_sizing = standby_share\n[MGSYS:FRAME_B]\nha_sizing = standby_fixed\nstandby_entitlement = 0.10\n"
       . "[n1]\nstandby_entitlement = .2\n[n2]\nha_sizing = Standby_Fixed\n[n3]\nha_sizing = standby_fixed\nstandby_entitlement = 1\n");
is_deeply($c->{errors}, [], 'standby_fixed with its entitlement reads without errors');
is_deeply($c->{mgsys}{FRAME_B}, { ha_sizing => 'standby_fixed', standby_entitlement => 0.10 }, 'a managed system\'s fixed policy');
is_deeply([ $c->{vms}{n1}{standby_entitlement}, $c->{vms}{n2}{ha_sizing}, $c->{vms}{n3}{standby_entitlement} ], [ 0.2, 'standby_fixed', 1 ],
          'LPAR values; an LPAR may take the entitlement of a managed system');
$c = cfg("[n1]\nstandby_entitlement = 0.04\n[n2]\nstandby_entitlement = 0.125\n[n3]\nstandby_entitlement = lots\n[n4]\nstandby_entitlement = 0.05\n"
       . "[MGSYS:FRAME_B]\nstandby_entitlement = -1\n");
is(scalar(grep { /standby_entitlement must be a number of processors of at least 0\.05, with two decimals at most/ } @{ $c->{errors} }), 4,
   'below 0.05, three decimals, not a number, negative: each an error');
is($c->{vms}{n4}{standby_entitlement}, 0.05, 'the platform minimum of one VP is accepted');
ok(!exists $c->{vms}{n1}{standby_entitlement}, 'a refused value is not taken');
$c = cfg("[GLOBAL]\nha_sizing = standby_fixed\n");
ok((grep { /^line 2, \[GLOBAL\]: ha_sizing = standby_fixed needs standby_entitlement, in this stanza or a more general one/ } @{ $c->{errors} }),
   '[GLOBAL] standby_fixed without an entitlement is an error');
$c = cfg("[MGSYS:FRAME_A]\nstandby_entitlement = 0.2\n[MGSYS:FRAME_B]\nha_sizing = standby_fixed\n[n1]\nha_sizing = standby_fixed\n");
is_deeply($c->{errors}, [ 'line 4, [MGSYS:FRAME_B]: ha_sizing = standby_fixed needs standby_entitlement, in this stanza or a more general one' ],
          'another managed system\'s entitlement does not serve a managed system; for an LPAR it may (its frame is not known from the file)');
$c = cfg("[GLOBAL]\nstandby_entitlement = 0.15\n[MGSYS:FRAME_B]\nha_sizing = standby_fixed\n[n1]\nha_sizing = standby_fixed\nha_sizing = independent\n");
is_deeply($c->{errors}, [], '[GLOBAL] supplies the entitlement; a stanza whose last ha_sizing is not standby_fixed needs none');

# --- One resolver: LPAR, managed system, [GLOBAL]; each setting on its own ---------
$UUT::vms_config = cfg("[GLOBAL]\nha_sizing = standby_share\nstandby_share = 0.25\nvios_sizing = combined\n"
                     . "[MGSYS:FRAME_B]\nha_sizing = independent\nvios_takeover_margin = 0.10\n[n1]\nstandby_share = 0.6\n");
is_deeply([ UUT::_policy_setting('n1', 'FRAME_B', 'standby_share') ], [0.6, 'lpar'], 'resolver: the LPAR\'s own setting');
is_deeply([ UUT::_policy_setting('n1', 'FRAME_B', 'ha_sizing') ], ['independent', 'mgsys'], 'resolver: its other settings still inherit');
is_deeply([ UUT::_policy_setting('n1', 'FRAME_A', 'ha_sizing') ], ['standby_share', 'global'], 'resolver: [GLOBAL] where the managed system is silent');
is_deeply([ UUT::_policy_setting('n1', undef, 'ha_sizing') ], ['standby_share', 'global'], 'resolver: managed system not established - its stanza is not consulted');
is_deeply([ UUT::_policy_setting('n9', 'FRAME_B', 'vios_sizing') ], ['combined', 'global'], 'resolver: a setting the managed system does not name');
is_deeply([ UUT::_policy_setting('n9', 'FRAME_B', 'ha_role') ], [undef, 'built-in'], 'resolver: nothing set - the built-in default is the caller\'s');
is_deeply(UUT::_vms_config_defaults('FRAME_B'), { ha_sizing => 'independent', standby_share => 0.25, vios_sizing => 'combined', vios_takeover_margin => 0.10 },
          'the defaults on a managed system: [GLOBAL] with its own in their place');
is_deeply(UUT::_vms_config_defaults('FRAME_A'), $UUT::vms_config->{global}, 'and [GLOBAL] alone elsewhere');
is_deeply(UUT::_vms_config_defaults(undef), $UUT::vms_config->{global}, 'or where the managed system is not known');

done_testing();
