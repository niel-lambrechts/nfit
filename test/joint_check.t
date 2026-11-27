#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/joint_check.t
# PURPOSE  : Checks on the resolved plan (v10 B7), by sub-extraction: the joint
#            replay of a pool's entitlement reductions (pool-minutes against
#            LPAR-minutes, shortfall, members together, episodes and gaps,
#            profile hours apart from the other hours), the saved-evidence limit
#            (never a partial replay), completeness apart from exposure, the
#            protected-level outcomes and the valid held pair.
# USAGE    : perl joint_check.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_joint_replay _joint_completeness _joint_evidence_for_plan _protected_level_outcome _protected_level_held_pair _protected_level_hold))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval q{
package UUT;
use Scalar::Util qw(looks_like_number);
our $FLOAT_EPSILON = 1e-9;
our $STRUCTURAL_ENT_TOLERANCE = 0.005;
our $MIN_ENT_PER_VP = 0.05;
our %JOINT_CHECK_RULE = (version => 'joint-check-1', maxReadings => 5000);
} . $code . "\n1;\n" or die "harness eval failed: $@\n";

my $T0 = 1_790_000_040;                       # a whole minute
my $at = sub { return $T0 + 60 * $_[0] };

# --- One member -----------------------------------------------------------------------
# Installed 2.00. Readings are min(PhysB, installed) in contested minutes: 1.50, 1.50, a gap, 1.20.
my $one = { members => ['a'], cadence => 1,
            readings => [ [ $at->(0), 0, 1.50, 1 ], [ $at->(1), 0, 1.50, 1 ], [ $at->(5), 0, 1.20, 1 ] ] };
my $r = UUT::_joint_replay($one, { a => 1.00 });
is($r->{state}, 'replayed', 'complete evidence is replayed');
my $p = $r->{scopes}{profileHours};
is_deeply([ @{$p}{qw(poolMinutes lparMinutes mostTogether longestEpisode days)} ], [ 3, 3, 1, 2, 1 ],
    'one member: 3 pool-minutes, 3 LPAR-minutes; the gap ends the episode (longest 2, not 3)');
is($p->{shortfall}, 1.2, 'shortfall: 0.50 + 0.50 + 0.20 cores over the exposed minutes');
is($r->{scopes}{otherHours}{poolMinutes}, 0, 'nothing outside the profile hours');
is_deeply($r->{scopes}{allHours}{together}, { 1 => 3 }, 'the minutes at each count of members together');
is(UUT::_joint_replay($one, { a => 1.30 })->{scopes}{allHours}{poolMinutes}, 2, 'a higher entitlement leaves the 1.20 minute unexposed');
is(UUT::_joint_replay($one, { a => 1.50 })->{scopes}{allHours}{poolMinutes}, 0, 'at the level of the highest reading: no exposure');
is(UUT::_joint_replay($one, { a => 1.4999999 })->{scopes}{allHours}{poolMinutes}, 0, 'binary noise below a reading is not exposure');
is(UUT::_joint_replay($one, {})->{scopes}{allHours}{poolMinutes}, 0, 'a member without a figure at this level is not assessed at it');

# --- Several members: aligned and not aligned ----------------------------------------
# Minute 0: a and b together. Minute 1: a alone. Minute 2: b alone, outside b's profile hours. Minute 10: c alone.
my $pool = { members => [qw(a b c)], cadence => 1, readings => [
    [ $at->(0), 0, 1.50, 1 ], [ $at->(0), 1, 0.90, 1 ],
    [ $at->(1), 0, 1.40, 1 ],
    [ $at->(2), 1, 0.80, 0 ],
    [ $at->(10), 2, 3.00, 1 ] ] };
$r = UUT::_joint_replay($pool, { a => 1.00, b => 0.50, c => 2.00 });
my $all = $r->{scopes}{allHours};
is_deeply([ @{$all}{qw(poolMinutes lparMinutes mostTogether longestEpisode)} ], [ 4, 5, 2, 3 ],
    'pool-minutes (4) are not LPAR-minutes (5): the shared minute counts once; minutes 0-2 are one episode');
is_deeply($all->{together}, { 1 => 3, 2 => 1 }, 'two members together in one minute, one member in three');
is($all->{shortfall}, 0.5 + 0.4 + 0.4 + 0.3 + 1.0, 'shortfall added over all exposed LPAR-minutes');
is_deeply([ @{ $r->{scopes}{profileHours} }{qw(poolMinutes lparMinutes longestEpisode)} ], [ 3, 4, 2 ],
    'profile hours: the minute b is exposed outside its hours is not counted here');
is_deeply([ @{ $r->{scopes}{otherHours} }{qw(poolMinutes lparMinutes)} ], [ 1, 1 ], 'and is counted with the other hours');
is_deeply($r->{scopes}{otherHours}{members}, { b => { minutes => 1, shortfall => 0.3, longest => 1 } },
    'the member exposed outside its hours is named; each member keeps its own profile-hours flag');
is($all->{members}{a}{longest}, 2, "a member's own longest run");
is($all->{members}{b}{longest}, 1, 'b at minutes 0 and 2: not consecutive for b, though the pool episode runs through');
# The same evidence at two levels: this cycle's entitlements and the full targets are assessed apart.
is(UUT::_joint_replay($pool, { a => 1.45, b => 0.90, c => 3.00 })->{scopes}{allHours}{lparMinutes}, 1, 'at higher (cycle) figures: one LPAR-minute');
is(UUT::_joint_replay($pool, { a => 1.00 })->{scopes}{allHours}{poolMinutes}, 2, "an unchanged member's readings stay out only when it has no figure");

# --- A coarse interval: consecutive means the next observation -----------------------
my $coarse = { members => ['a'], cadence => 15, readings => [ [ $at->(0), 0, 1.5, 1 ], [ $at->(15), 0, 1.5, 1 ], [ $at->(45), 0, 1.5, 1 ] ] };
$r = UUT::_joint_replay($coarse, { a => 1.0 });
is_deeply([ $r->{cadence}, @{ $r->{scopes}{allHours} }{qw(poolMinutes longestEpisode)} ], [ 15, 3, 2 ],
    '15-minute observations: 3 observations, the longest run 2 (a missing observation ends it)');

# --- The saved-evidence limit ----------------------------------------------------------
my $kept = UUT::_joint_evidence_for_plan($pool, 5000);
is_deeply([ $kept->{truncated}, $kept->{readingCount}, scalar @{ $kept->{readings} } ], [ 0, 5, 5 ], 'within the limit: every reading is kept');
is_deeply(UUT::_joint_replay($kept, { a => 1.00, b => 0.50, c => 2.00 })->{scopes}{allHours}{together}, { 1 => 3, 2 => 1 },
    'and the saved evidence replays to the same result');
my $cut = UUT::_joint_evidence_for_plan($pool, 4);
is_deeply([ $cut->{truncated}, $cut->{readingCount}, scalar @{ $cut->{readings} } ], [ 1, 5, 0 ],
    'beyond the limit: no reading is kept (a part would replay as if it were the whole); the count stays');
is_deeply(UUT::_joint_replay($cut, { a => 1.00, b => 0.50 }), { state => 'notReassessed', reason => 'saved evidence limit reached' },
    'truncated evidence is never replayed: not reassessed, with the reason');
is(scalar @{ $pool->{readings} }, 5, 'the evidence of the analysis itself is left whole');
is(UUT::_joint_replay(undef, {})->{state}, 'notAssessed', 'no evidence: not assessed, never zero');

# --- Completeness, apart from exposure ----------------------------------------------
my $gappy = { %$pool, notAssessed => { c => 'no PhysB measurement in the period' } };
my $c = UUT::_joint_completeness($gappy, [qw(a b c d)]);
is_deeply([ @{$c}{qw(state members assessed)} ], [ 'incomplete', 4, 2 ], 'two of four members assessed');
is_deeply($c->{notAssessed}, { c => 'no PhysB measurement in the period', d => 'no evidence was gathered for it' }, 'each missing member with its reason');
ok(UUT::_joint_replay($gappy, { a => 1.00, b => 0.50 })->{scopes}{allHours}{poolMinutes} > 0, 'the same pool shows exposure and incomplete evidence together');
is(UUT::_joint_completeness($pool, [qw(a b c)])->{state}, 'complete', 'all members assessed');
is(UUT::_joint_completeness($cut, [qw(a b c)])->{state}, 'limitReached', 'the limit is its own state');

# --- Protected level ----------------------------------------------------------------
my $o = sub { return UUT::_protected_level_outcome({ installed => 2.00, @_ }) };
is($o->(entitlement => 2.00, protected => 1.50)->{outcome}, 'notReduced', 'no reduction: nothing to verify');
is($o->(entitlement => 2.40, protected => 1.50)->{outcome}, 'notReduced', 'nor on an increase');
is($o->(entitlement => 1.50, protected => 1.50)->{outcome}, 'met', 'at the protected level: met');
is($o->(entitlement => 1.4999999, protected => 1.50)->{outcome}, 'met', 'binary noise makes no violation');
is($o->(entitlement => 1.80)->{outcome}, 'notKnown', 'no protected level recorded: not known, not met');
is_deeply($o->(entitlement => 1.49, protected => 1.50), { outcome => 'heldUnexplained', required => 1.5, shortBy => 0.01 },
    'one hundredth below, with no policy and no prerequisite: held (no materiality threshold)');
is_deeply($o->(entitlement => 0.10, protected => 1.50, policy => 'fixed standby entitlement'),
    { outcome => 'belowByPolicy', required => 1.5, shortBy => 1.4, policy => 'fixed standby entitlement' }, 'a stated policy is an exception, reported');
is($o->(entitlement => 1.00, protected => 1.50, prerequisite => 'Pool 7 must allow 1.50 first')->{outcome}, 'heldPrerequisite', 'a prerequisite holds the reduction');
is($o->(entitlement => 0.10, protected => 1.50, policy => 'fixed standby entitlement', prerequisite => 'x')->{outcome}, 'belowByPolicy', 'the policy is never reversed by a hold');
is($o->(entitlement => 1.90, protected => 2.60)->{required}, 2, 'the protected level is never above the installed entitlement');

# --- The held pair is a valid pair ----------------------------------------------------
is_deeply(UUT::_protected_level_held_pair(6.00, 7), { Ent => 6, VP => 7 }, 'held: the installed entitlement on the installed VPs (6.00 is never put beside one VP)');
like(UUT::_protected_level_held_pair(6.00, 1)->{unresolved}, qr/6\.00 entitlement on 1 VP\) is not one PowerVM accepts/, 'an installed pair PowerVM would not accept: unresolved, no pair claimed');
like(UUT::_protected_level_held_pair(0.10, 4)->{unresolved}, qr/not one PowerVM accepts/, 'below 0.05 a VP likewise');
like(UUT::_protected_level_held_pair(undef, 2)->{unresolved}, qr/not known/, 'unknown installed figures: unresolved');

# --- A hold on the resolved pair ---------------------------------------------------------
# 6.00 on 7 VPs, resolved to 1.00 on 1 VP; the protected level is 3.00. The reduced VPs cannot carry the
# installed entitlement, so the held pair is the installed pair, and the target stays visible.
my $pair = { Ent => 1.00, VP => 1, Status => 'APPLY', Order => 'EITHER', Binding => [], TargetEnt => 1.00, TargetVP => 1, ReviewVP => undef, Reason => '' };
my $held = UUT::_protected_level_hold($pair, 3.00, 6.00, 7, 12);
is_deeply([ @{$pair}{qw(Ent VP Status Order TargetEnt TargetVP)} ], [ 6, 7, 'HOLD', 'NONE', 1, 1 ],
    'held at the installed pair (6.00 on 7 VPs, never 6.00 on 1 VP); the full target is kept');
is_deeply([ @{$held}{qw(outcome required shortBy)}, $held->{was}{entitlement}, $held->{was}{VPs} ], [ 'heldUnexplained', 3, 2, 1, 1 ], 'the outcome records the level and the pair it replaced');
like($pair->{Reason}, qr/^a reduction to 1\.00 would leave the entitlement below its protected level of 3\.00 \(busy work observed while its pool was contested\); held until the cause is found$/, 'the reason in planner words');
is(ref($pair->{ProtectedLevelHold}), 'HASH', 'and the pair carries the hold for the tag and the plan');
# A pool maximum that prevents the protected level: a prerequisite.
$pair = { Ent => 2.00, VP => 2, Status => 'BLOCKED', Order => 'EITHER', Binding => ['POOL_SIZE'], TargetEnt => 2.5, TargetVP => 3, Reason => '' };
UUT::_protected_level_hold($pair, 3.00, 4.00, 4, 12);
like($pair->{Reason}, qr/; pool 12 must allow 3\.00 for this LPAR first$/, 'a pool maximum below the protected level is named as the prerequisite');
is_deeply([ @{$pair}{qw(Ent VP Status)} ], [ 4, 4, 'HOLD' ], 'held at the installed pair meanwhile');
# Met, not known, not a reduction, already held: the pair is left as it is.
foreach my $case ([ 'met', 3.00, 2.00 ], [ 'not known', 1.00, undef ], [ 'an increase', 7.00, 3.00 ])
{
    my $p2 = { Ent => $case->[1], VP => 7, Status => 'APPLY', Order => 'EITHER', Binding => [] };
    is(UUT::_protected_level_hold($p2, $case->[2], 6.00, 7, 0), undef, "$case->[0]: no hold");
    is_deeply([ @{$p2}{qw(Ent VP Status)} ], [ $case->[1], 7, 'APPLY' ], "$case->[0]: pair unchanged");
}
my $kept_hold = { Ent => 6.00, VP => 7, Status => 'HOLD', Reason => 'excluded role' };
is(UUT::_protected_level_hold($kept_hold, 7.00, 6.00, 7, 0), undef, 'a pair already held for another reason keeps its reason');
# An installed pair PowerVM would not accept: unresolved, no pair stated.
$pair = { Ent => 1.00, VP => 1, Status => 'APPLY', Order => 'EITHER', Binding => [] };
UUT::_protected_level_hold($pair, 3.00, 6.00, 1, 0);
is_deeply([ $pair->{Ent}, $pair->{VP}, $pair->{Status} ], [ undef, undef, 'HOLD' ], 'no pair is stated when the recorded installed pair is not valid');
like($pair->{Reason}, qr/is not one PowerVM accepts: unresolved$/, 'and the row says unresolved');

done_testing();
