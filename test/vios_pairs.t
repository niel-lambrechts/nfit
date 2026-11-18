#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/vios_pairs.t
# PURPOSE:   Regression cover for VIOS sizing (v10 stage 3.1b): a VIOS without
#            a configured TIER is peak protected (P), VIOS groups are formed
#            from vios_peer or, without it, from all VIOS of a system, and both
#            VIOS of a pair are sized to the larger. The live subroutines are
#            extracted from nfit-profile and evaluated in an isolated package.
# USAGE:     perl test/vios_pairs.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use Scalar::Util qw(looks_like_number);

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_resolve_vm_tier_pattern_policy _vios_groups _align_vios_group _vios_pair_sentence
                 _resolve_current_cycle_pair _ceil_ratio))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
my ($min_ent_per_vp) = $src =~ /^my \$MIN_ENT_PER_VP\s*=\s*([0-9.]+);/m or die "MIN_ENT_PER_VP not found\n";
my ($ratio_max)      = $src =~ /^my \$VCPU_RATIO_MAX\s*=\s*([0-9.]+);/m or die "VCPU_RATIO_MAX not found\n";
my $harness = qq{
package UUT;
use Scalar::Util qw(looks_like_number);
our \$FLOAT_EPSILON = 1e-9;
our \$MIN_ENT_PER_VP = $min_ent_per_vp;
our \$VCPU_RATIO_MAX = $ratio_max;
our \$MISSION_CRITICAL_PROFILE_NAME = 'P-99W1';
} . $code . "\n1;\n";
eval $harness or die "harness eval failed: $@\n";

# --- Tier: a VIOS without a configured TIER is P ------------------------------------
my @profiles = map { { name => $_, csv_output => 1 } } qw(P-99W1 G1-99W5 G2-98W10 G3-95W15 O3-95W15);
my $r = UUT::_resolve_vm_tier_pattern_policy('', 'P', \@profiles, 'vios');
is_deeply([ @$r{qw(ResolvedTierLabel ResolvedPlanningProfile TierPolicySource)} ], ['P', 'P-99W1', 'VIOS_DEFAULT_P'],
    'VIOS without a TIER: P, peak protected');
$r = UUT::_resolve_vm_tier_pattern_policy('G2', 'P', \@profiles, 'vios');
is_deeply([ @$r{qw(ResolvedTierLabel TierPolicySource)} ], ['G2', 'CONFIGURED_TIER'], 'VIOS with a TIER: the configured TIER');
$r = UUT::_resolve_vm_tier_pattern_policy('', 'P', \@profiles, 'lpar');
is_deeply([ @$r{qw(ResolvedTierLabel TierPolicySource)} ], ['G3', 'DEFAULT_TIER_3'], 'any other LPAR with a P hint: tier 3');
$r = UUT::_resolve_vm_tier_pattern_policy('', 'O2', \@profiles);
is($r->{ResolvedTierLabel}, 'O3', 'no LPAR type given: as before');

# --- Groups ----------------------------------------------------------------------------
my %type = map { $_ => 'vios' } qw(va vb vc vd ve);
my %peers = (va => ['vb'], vc => ['vd'], vd => ['vc']);
my $type_of  = sub { $type{ $_[0] } // 'lpar' };
my $peers_of = sub { $peers{ $_[0] } // [] };
my $g = UUT::_vios_groups([qw(va vb vc vd ve app1)], $type_of, $peers_of);
is_deeply($g->{vb}, { Partners => ['va'], Source => 'vios_peer' }, 'vios_peer set on one VIOS is enough');
is_deeply($g->{vc}, { Partners => ['vd'], Source => 'vios_peer' }, 'a second named pair');
ok(!exists $g->{ve}, 'a single unnamed VIOS has no group');
ok(!exists $g->{app1}, 'an LPAR is never in a VIOS group');
$g = UUT::_vios_groups([qw(va vb ve)], $type_of, sub { [] });
is_deeply($g->{ve}, { Partners => [qw(va vb)], Source => 'system' }, 'without vios_peer: all VIOS of the system form one group');
$g = UUT::_vios_groups([qw(va app1)], $type_of, sub { ['vb'] });
is_deeply($g, {}, 'a vios_peer outside this system forms no group');

# --- Alignment -------------------------------------------------------------------------
my @members = (
    { vm => 'va', planning => 'P-99W1', current_ent => 1.00, current_vp => 2,
      values => { 'P-99W1' => { target_ent => 0.47, target_vp => 1, step_vp => 1 } } },
    { vm => 'vb', planning => 'P-99W1', current_ent => 1.00, current_vp => 2,
      values => { 'P-99W1' => { target_ent => 1.37, target_vp => 2, step_vp => 2 } } },
);
my $al = UUT::_align_vios_group(\@members);
is($al->{va}{TargetEnt}, 1.37, 'the smaller VIOS takes the larger target entitlement');
is($al->{va}{TargetVP}, 2, 'and the larger VP count');
is($al->{va}{Raised}, 1, 'the smaller is raised');
is($al->{vb}{Raised}, 0, 'the larger is not');
is_deeply([ @{ $al->{va}{Pair} }{qw(Ent VP)} ], [1.37, 2], 'the VP reduction step is not taken below the pair\'s VP count');
is_deeply([ @{ $al->{vb}{Pair} }{qw(Ent VP)} ], [1.37, 2], 'the larger keeps its own pair');
is($al->{va}{OwnTargetEnt}, 0.47, 'the own target is kept for the rationale');

# Different planning profiles: each member is aligned on its own profile.
@members = (
    { vm => 'va', planning => 'G2-98W10', current_ent => 2.0, current_vp => 4,
      values => { 'G2-98W10' => { target_ent => 1.10, target_vp => 2, step_vp => 3 } } },
    { vm => 'vb', planning => 'P-99W1', current_ent => 2.0, current_vp => 4,
      values => { 'P-99W1' => { target_ent => 1.20, target_vp => 2, step_vp => 3 },
                  'G2-98W10' => { target_ent => 0.90, target_vp => 2, step_vp => 3 } } },
);
$members[0]{values}{'P-99W1'} = { target_ent => 2.50, target_vp => 3, step_vp => 4 };
$al = UUT::_align_vios_group(\@members);
is($al->{va}{TargetEnt}, 1.10, 'on G2 the first VIOS is already the larger');
is($al->{vb}{TargetEnt}, 2.50, 'on P the second takes the first one\'s P target');
is(UUT::_align_vios_group([ $members[0] ])->{va}, undef, 'a group of one is not aligned');

# A member without a result takes its partner's target.
@members = (
    { vm => 'va', planning => 'P-99W1', current_ent => 0.50, current_vp => 1, values => { 'P-99W1' => {} } },
    { vm => 'vb', planning => 'P-99W1', current_ent => 0.50, current_vp => 1,
      values => { 'P-99W1' => { target_ent => 0.80, target_vp => 1, step_vp => 1 } } },
);
$al = UUT::_align_vios_group(\@members);
is_deeply([ $al->{va}{TargetEnt}, $al->{va}{Raised}, @{ $al->{va}{Pair} }{qw(Ent VP)} ], [0.80, 1, 0.80, 1],
    'a VIOS without a result is sized from its partner');

like(UUT::_vios_pair_sentence(['vb'], 1), qr/^Sized to match vb, its VIOS partner, for SEA and storage-path failover\.$/, 'raised: names the partner');
like(UUT::_vios_pair_sentence([qw(vb vc)], 0), qr/^Sized with vb, vc, its VIOS partners: this VIOS is the larger\.$/, 'larger: names the partners');

done_testing();
