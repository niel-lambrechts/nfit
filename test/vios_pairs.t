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
                 _vios_group_policy _vios_group_profile _combine_vios_group _vios_takeover_review
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

is(UUT::_vios_pair_sentence(['vb'], { Mode => 'match_larger', Raised => 1 }),
   'Aligned with vb, its VIOS partner (match_larger; takeover capacity not verified).', 'match_larger raised: names the partner');
is(UUT::_vios_pair_sentence([qw(vb vc)], { Mode => 'match_larger', Raised => 0 }),
   'Aligned with vb, vc, its VIOS partners: this VIOS is the larger (match_larger; takeover capacity not verified).',
   'match_larger larger: names the partners');

# --- Group policy (design revision 2) ---------------------------------------------------
my %set = (va => { vios_sizing => 'match_larger' }, vb => {}, vc => { vios_takeover_margin => 0.10 });
my $settings_of = sub { $set{ $_[0] } // {} };
my $pol = UUT::_vios_group_policy([qw(vb vd)], $settings_of, {}, 'vios_peer');
is_deeply([ @$pol{qw(Mode Margin Conflict)} ], ['combined', 0, 0], 'policy: combined, no margin by default');
$pol = UUT::_vios_group_policy([qw(va vd)], $settings_of, {}, 'vios_peer');
is_deeply([ @$pol{qw(Mode Conflict)} ], ['combined', 1], 'policy: members disagree - the more protective (combined), warned');
$pol = UUT::_vios_group_policy([qw(va vd)], sub { { vios_sizing => 'match_larger' } }, {}, 'vios_peer');
is($pol->{Mode}, 'match_larger', 'policy: match_larger when every member asks for it');
$pol = UUT::_vios_group_policy([qw(vb vc)], $settings_of, { vios_takeover_margin => 0.05 }, 'vios_peer');
is_deeply([ @$pol{qw(Margin Conflict)} ], [0.10, 1], 'policy: margins differ - the larger');
$pol = UUT::_vios_group_policy([qw(vb vd)], $settings_of, { vios_sizing => 'match_larger', vios_takeover_margin => 0.10 }, 'vios_peer');
is_deeply([ @$pol{qw(Mode Margin)} ], ['match_larger', 0.10], 'policy: [GLOBAL] defaults');
$pol = UUT::_vios_group_policy([qw(vb vd ve)], $settings_of, {}, 'system');
is($pol->{Mode}, 'independent', 'policy: three unnamed VIOS are not one takeover group');
like($pol->{Note}, qr/name the pairs with vios_peer/, 'policy: and the planner is told why');
is(UUT::_vios_group_policy([qw(vb vd)], $settings_of, {}, 'system')->{Mode}, 'combined', 'policy: the two VIOS of a frame are combined');

# --- Group profile ------------------------------------------------------------------------
is(UUT::_vios_group_profile([ { planning => 'P-99W1', label => 'P' }, { planning => 'P-99W1', label => 'P' } ]), 'P-99W1', 'profile: P without TIERs');
is(UUT::_vios_group_profile([ { planning => 'G2-98W10', label => 'G2' }, { planning => 'P-99W1', label => 'P' } ]), 'P-99W1', 'profile: P is the most protective');
is(UUT::_vios_group_profile([ { planning => 'O1-99W5', label => 'O1' }, { planning => 'G1-99W5', label => 'G1' } ]), 'G1-99W5', 'profile: all-hours before online at the same tier');
is(UUT::_vios_group_profile([ { planning => 'G3-95W15', label => 'G3' }, { planning => 'B2-98W10', label => 'B2' } ]), 'B2-98W10', 'profile: the lower tier number first');

# --- Combined sizing ----------------------------------------------------------------------
@members = (
    { vm => 'va', planning => 'P-99W1', current_ent => 1.00, current_vp => 2,
      values => { 'P-99W1' => { target_ent => 0.47, target_vp => 1, step_vp => 1 } } },
    { vm => 'vb', planning => 'P-99W1', current_ent => 1.00, current_vp => 2,
      values => { 'P-99W1' => { target_ent => 1.37, target_vp => 2, step_vp => 2 } } },
);
my $cb = UUT::_combine_vios_group(\@members, 'P-99W1', 0);
is($cb->{va}{TargetEnt}, 1.84, 'combined: the sum of the own targets');
is($cb->{vb}{TargetEnt}, 1.84, 'combined: the same target for both');
is($cb->{va}{TargetVP}, 3, 'combined: the sum of the own VP targets');
is_deeply([ @{ $cb->{va}{Pair} }{qw(Ent VP)} ], [1.84, 2], 'combined: the current-cycle pair from the installed VPs');
is($cb->{va}{Pair}{ReviewVP}, 3, 'combined: the extra VP is a review');
is($cb->{va}{Pair}{ReviewWhy}, 'combined VIOS width', 'combined: with its reason');
is_deeply($cb->{vb}{OwnTargets}, [0.47, 1.37], 'combined: own targets kept for the rationale');
$cb = UUT::_combine_vios_group(\@members, 'P-99W1', 0.10);
is($cb->{va}{TargetEnt}, 2.03, 'combined: margin 10% rounded up to 0.01 (1.84 x 1.10 = 2.024)');
is($cb->{va}{TargetVP}, 3, 'combined: VPs at least the whole cores of the target');
is(UUT::_combine_vios_group([ @members[0, 1], { vm => 'vc', planning => 'P-99W1', values => { 'P-99W1' => {} } } ], 'P-99W1', 0)->{va},
   undef, 'combined: a member without an own target - not combined');
like(UUT::_vios_pair_sentence(['vb'], $cb->{va}),
     qr/^Combined VIOS sizing with vb \(planning scenario\): own targets 0\.47 \+ 1\.37, margin 10% = 2\.03 entitlement on 3 VPs\.$/,
     'combined: the action states the sum, the margin and the target');
my $big = UUT::_combine_vios_group([ map { { %$_, current_ent => 0.50, current_vp => 1 } } @members ], 'P-99W1', 0);
ok($big->{va}{Pair}{Ent} <= $big->{va}{Pair}{VP} && $big->{va}{Pair}{Ent} >= 0.05 * $big->{va}{Pair}{VP}, 'combined: a valid pair from a small installed VIOS');

# --- Independent VIOS sizing (v10 B17) -----------------------------------------------------
my $every = sub { my ($mode) = @_; return sub { { vios_sizing => $mode } }; };
$pol = UUT::_vios_group_policy([qw(va vd)], $every->('independent'), {}, 'vios_peer');
is_deeply([ @$pol{qw(Mode ByPolicy Conflict)} ], [ 'independent', 1, 0 ], 'independent when both VIOS resolve to it');
is(UUT::_vios_group_policy([qw(vb vd)], sub { {} }, { vios_sizing => 'independent' }, 'system')->{Mode}, 'independent',
   'a [GLOBAL] or managed-system default gives it to a pair with no stanza of its own');
is(UUT::_vios_group_policy([qw(vb vd)], sub { {} }, {}, 'system')->{Mode}, 'combined', 'the built-in default is still combined');
my %mixed = (va => { vios_sizing => 'independent' }, vd => { vios_sizing => 'combined' });
$pol = UUT::_vios_group_policy([qw(va vd)], sub { $mixed{ $_[0] } }, {}, 'vios_peer');
is_deeply([ @$pol{qw(Mode ByPolicy Conflict)} ], [ 'combined', 0, 1 ], 'independent and combined: combined applies');
is($pol->{ConflictText}, 'vios_sizing differs within the pair: va independent (its own stanza); vd combined (its own stanza). combined applies (the more protective setting); '
   . 'independent sizing needs both to resolve to independent', 'the conflict names both settings, their sources and what applies');
%mixed = (va => { vios_sizing => 'independent' }, vd => {});
$pol = UUT::_vios_group_policy([qw(va vd)], sub { $mixed{ $_[0] } }, { vios_sizing => 'match_larger' }, 'vios_peer',
                               sub { return $_[0] eq 'va' ? 'lpar' : 'mgsys' });
is($pol->{Mode}, 'match_larger', 'independent and match_larger: match_larger applies');
like($pol->{ConflictText}, qr/va independent \(its own stanza\); vd match_larger \(its managed system's stanza\)\. match_larger applies/, 'with the source of an inherited setting');
is(UUT::_vios_group_policy([qw(va vd)], $every->('independent'), {}, 'vios_peer')->{ConflictText}, undef, 'no conflict message when they agree');
$pol = UUT::_vios_group_policy([qw(vb vd ve)], sub { {} }, {}, 'system');
is_deeply([ @$pol{qw(Mode ByPolicy)} ], [ 'independent', 0 ], 'three unnamed VIOS: still the ungrouped fallback, not the policy');

# The takeover review: unequal own targets; an estimate, not a measurement; capped apart from uncapped.
my $rv = UUT::_vios_takeover_review([ { vm => 'va', ownEnt => 6.77, ownVP => 7, capped => 0 }, { vm => 'vb', ownEnt => 2.10, ownVP => 3, capped => 1 } ], 0.10);
is($rv->{estimate}, 8.87, 'the combined estimate is the sum of the two own targets');
is_deeply($rv->{illustrative}, { combined => 19.51, match_larger => 13.54, independent => 8.87 }, 'the pair under each policy, for comparison (combined with its margin)');
is_deeply([ @{ $rv->{members}{va} }{qw(partner ownEntitlement beyond borrowing capped vpsShort)} ], [ 'vb', 6.77, 2.1, 1, 0, 9 ], 'the uncapped VIOS: borrowing 2.10, and 9 VPs needed against 7');
like($rv->{members}{va}{text}, qr/^carrying the work of vb as well is estimated at 8\.87 \(the sum of the two own targets; not a measured peak\), 2\.10 above its own 6\.77\. Uncapped, it would have to borrow that, which needs capacity free in its pool and on the frame at the time; neither its weight nor its VPs assures that\. Its 7 VPs cannot reach the estimate: at least 9 would be needed\.$/,
     'uncapped: borrowing needs pool and frame capacity; weight and VPs assure nothing');
is_deeply([ @{ $rv->{members}{vb} }{qw(borrowing capped)} ], [ 0, 1 ], 'the capped VIOS cannot borrow');
like($rv->{members}{vb}{text}, qr/It is capped: it cannot borrow, so it cannot exceed its entitlement in a takeover\.$/, 'capped: said so, with no borrowing wording');
like($rv->{members}{vb}{action}, qr/^It is capped and cannot borrow: a takeover of va \(combined estimate 8\.87\) is not covered\.$/, 'and on its row');
like($rv->{members}{va}{action}, qr/^A takeover of vb would rely on borrowing 2\.10 above its entitlement \(combined estimate 8\.87, not measured\); not verified\. Its VPs cannot reach that estimate \(9 needed\)\.$/, 'the row of the uncapped VIOS');
unlike(join(' ', map { $_->{text}, $_->{action} } values %{ $rv->{members} }), qr/guarantee|verified takeover|sufficient/i, 'no wording of a guaranteed or verified takeover');
is(UUT::_vios_takeover_review([ { vm => 'va', ownEnt => 6.77 }, { vm => 'vb' } ], 0), undef, 'without two own targets there is no review');

done_testing();
