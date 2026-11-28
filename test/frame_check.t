#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/frame_check.t
# PURPOSE  : The default pool and the frame check (v10 B8), by sub-extraction:
#            shared capacity with its provenance, guarantees of the user pools
#            and the default pool counted once, the remainder and a shortfall
#            (pool 0 above the residual balance is a guarantee shortfall, never
#            a maximum of pool 0), pool maxima listed and never added, measured
#            use of the shared processors apart from the allocations (one size
#            and a changed size), and the known single-takeover entitlement
#            check with known and unknown requirements and its prerequisites.
# USAGE    : perl frame_check.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(_frame_use_summary _frame_check _frame_check_lines _frame_check_csv_cells _lpar_present _days_before_end _safe_dig _ha_target_of_member
                 _structural_shown_value _pool_units_text _rationale_wrap))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
my ($grace) = $src =~ /^my \$PRESENCE_GRACE_DAYS\s*=\s*(\d+);/m or die "PRESENCE_GRACE_DAYS not found\n";
eval qq{
package UUT;
use Scalar::Util qw(looks_like_number);
use List::Util qw(max min sum);
use JSON;
use Time::Piece;
our \$FLOAT_EPSILON = 1e-9;
our \$STRUCTURAL_ENT_TOLERANCE = 0.005;
our \$PRESENCE_GRACE_DAYS = $grace;
our %FRAME_USE_RULE = (wholeTolerance => 0.02);
} . $code . "\n1;\n" or die "harness eval failed: $@\n";

sub flat { my $t = join(' ', @_); $t =~ s/\s+/ /g; return $t; }
my $END = '2026-09-30';
my $lpar = sub
{
    my (%o) = @_;
    return { lastSampled => ($o{last} // $END), installedEntitlement => $o{ent}, planningProfile => 'G3', profiles => { G3 => { entitlement => ($o{full} // $o{ent}), recommendedVPs => ($o{vps} // 2) } },
             poolMember => { pool_id => $o{pool}, pool_cpu => $o{max}, current_ent => $o{ent}, proposed_ent => ($o{full} // $o{ent}), cycle_ent => ($o{cycle} // $o{ent}) },
             ($o{role} ? (role => $o{role}) : ()), ($o{pending} ? (pendingRole => { primary => 'x' }) : ()),
             published => { entitlement => sprintf('%.2f', $o{cycle} // $o{ent}), VPs => ($o{vps} // 2) } };
};
my %members = (
    u1 => $lpar->(pool => 5, max => 4, ent => 2.0, full => 1.5, cycle => 1.8),
    u2 => $lpar->(pool => 5, max => 4, ent => 1.0),
    d1 => $lpar->(pool => 0, ent => 10.0, full => 9.0, cycle => 9.5),
    d2 => $lpar->(pool => 0, ent => 8.0),
    gone => $lpar->(pool => 0, ent => 5.0, last => '2026-07-01'),            # left the frame: not counted
    nopool => { lastSampled => $END, installedEntitlement => 1.0 },
);
my $agreed = { status => 'agreed', processors => 20, reporters => 2, values => { 20 => 2 }, source => 'phys_cpus_pool' };
my $c = UUT::_frame_check(\%members, $agreed, undef, $END);
is_deeply([ map { $c->{guarantees}{userPools}{$_} } qw(Count Installed Full CurrentCycle) ], [ 2, 3, 2.5, 2.8 ], 'user-pool guarantees, each LPAR once');
is_deeply([ map { $c->{guarantees}{defaultPool}{$_} } qw(Count Installed Full CurrentCycle) ], [ 2, 18, 17, 17.5 ], 'default-pool guarantees; the LPAR that left is not counted');
is_deeply($c->{remaining}, { Installed => -1, Full => 0.5, CurrentCycle => -0.3 }, 'the remainder after all analysed guarantees; negative balances kept');
is($c->{notCounted}{noPool}, 1, 'an LPAR with no pool is named, not counted');
is_deeply($c->{poolMaxima}, [ { pool => 5, maximum => 4, guarantees => { Installed => 3, Full => 2.5, CurrentCycle => 2.8 } } ], 'the user-pool maxima listed with their guarantees');
my $text = flat(UUT::_frame_check_lines($c, 'analysis'));
like($text, qr/Shared capacity: 20 processing units, the shared processor capacity the frame reports \(phys_cpus_pool\), agreed by 2 default-pool LPARs\. It is not the frame's installed or activated processors\./,
     'the shared capacity with its provenance, and what it is not');
like($text, qr/Shortfall: the guarantees exceed the shared capacity as installed by 1\.00; this cycle by 0\.30\./, 'pool 0 above the residual balance: a guarantee shortfall');
unlike($text, qr/pool 0[^\n]*(?:maximum|exceed)/i, 'and never a maximum of pool 0 exceeded');
like($text, qr/The default pool has no maximum of its own: its LPARs borrow up to the shared capacity\./, 'the default pool is said to borrow');
like($text, qr/Borrowing between\s+pools is dynamic, and a positive remainder is not a certificate that the capacity is available\./, 'a positive balance is no certificate');
like($text, qr/not added up: pool 5 4\./, 'maxima listed, not added');
like($text, qr/Not assessed here: seasonal runs \(seasonal parity, C9\)/, 'seasonal runs are said not to be assessed here');
like($text, qr/Measured use of the shared processors: not assessed: not gathered\./, 'measured use: not assessed when not gathered');

# Capacity in conflict or absent: no remainder, said so.
my $conflict = UUT::_frame_check(\%members, { status => 'conflict', values => { 20 => 1, 24 => 1 } }, undef, $END);
is($conflict->{remaining}, undef, 'conflicting capacity: no remainder');
like(flat(UUT::_frame_check_lines($conflict, 'analysis')), qr/not assessed: the default-pool LPARs report different values \(20 by 1, 24 by 1\)/, 'and the disagreement is stated');
like(flat(UUT::_frame_check_lines(UUT::_frame_check(\%members, undef, undef, $END), 'analysis')), qr/Shared capacity: not assessed: no LPAR of the default pool reports it\./, 'absent capacity');

# --- Measured use ---------------------------------------------------------------------
my $raw = { sizes => { 64 => [ pack('f<*', 1 .. 100), 3 ] }, windowMinutes => 200 };
my $m = UUT::_frame_use_summary($raw);
is_deeply([ @{$m}{qw(status highest p99 withoutSpare observedMinutes coverage)} ], [ 'observed', 100, 99, 3, 100, 0.5 ], 'one total: highest, P99, minutes without spare capacity, coverage');
my $two = UUT::_frame_use_summary({ sizes => { 64 => [ pack('f<*', 1 .. 10), 0 ], 72 => [ pack('f<*', 50 .. 59), 1 ] }, windowMinutes => 20 });
ok($two->{sizeChanged} && !defined $two->{highest}, 'a changed shared total: no combined highest');
is_deeply([ map { [ $_->{size}, $_->{highest} ] } @{ $two->{sizes} } ], [ [ 64, 10 ], [ 72, 59 ] ], 'the minutes given for each total');
like(flat(UUT::_frame_check_lines(UUT::_frame_check({}, $agreed, $two, $END), 'analysis')), qr/the shared total changed in the period, so the\s+minutes are given for each total and not combined/,
     'and said so');
like(flat(UUT::_frame_check_lines(UUT::_frame_check({}, $agreed, $m, $END), 'analysis')),
     qr/highest credible minute 100\.00, P99 99\.00, of a shared total of 64; 50\.0% of the minutes of the period observed; 3 minutes without spare capacity\./, 'the measured-use line');
is(UUT::_frame_use_summary(undef)->{status}, 'notObserved', 'no frame-level observation: not observed');
# The readings of the shared total vary in the third decimal: one total of 120, not several.
my $jitter = UUT::_frame_use_summary({ sizes => { '119.995' => [ pack('f<*', 10, 20), 1 ], '120.004' => [ pack('f<*', 30), 0 ], '119.9965' => [ pack('f<*', 40), 0 ] }, windowMinutes => 4 });
is_deeply([ $jitter->{sizeChanged} ? 1 : 0, scalar @{ $jitter->{sizes} }, $jitter->{sizes}[0]{size}, @{$jitter}{qw(highest withoutSpare observedMinutes coverage)} ], [ 0, 1, 120, 40, 1, 4, 1 ],
    'interval variation of the shared total is one total of 120');
# A reading far from a whole number is not rounded: its minutes are counted apart. A real change of the total still separates.
my $odd = UUT::_frame_use_summary({ sizes => { '119.995' => [ pack('f<*', 10, 20), 0 ], '119.5' => [ pack('f<*', 99, 99, 99), 0 ], '128.001' => [ pack('f<*', 70), 0 ] }, windowMinutes => 6 });
is_deeply([ $odd->{inconsistentMinutes}, $odd->{sizeChanged} ? 1 : 0, [ map { [ $_->{size}, $_->{minutes}, $_->{highest} ] } @{ $odd->{sizes} } ] ], [ 3, 1, [ [ 120, 2, 20 ], [ 128, 1, 70 ] ] ],
    'a reading of 119.5 is flagged, not rounded; a change from 120 to 128 is still a change');
is(UUT::_frame_use_summary({ sizes => { '119.5' => [ pack('f<*', 1), 0 ] }, windowMinutes => 1 })->{status}, 'notObserved', 'only inconsistent readings: not observed, with the reason');
like(flat(UUT::_frame_check_lines(UUT::_frame_check({}, $agreed, UUT::_frame_use_summary({ sizes => { '120' => [ pack('f<*', 5), 0 ], '119.5' => [ pack('f<*', 9), 0 ] }, windowMinutes => 2 }), $END), 'analysis')),
     qr/1 minute left out: the shared total read was not near a whole number of processors\./, 'and the rationale says how many minutes were left out');

# --- Known single-takeover entitlement check ---------------------------------------------
my %ha = (
    %members,
    # Resolved: the primary's target is known (5.00); planned 1.00 this cycle, 0.80 full; 2 VPs.
    s1 => $lpar->(pool => 0, ent => 1.0, full => 0.8, cycle => 1.0, vps => 2,
                  role => { type => 'haStandby', primary => 'p1', VPs => 2, targetVPs => 2, primaryTarget => { entitlement => 5.0, source => 'cycle' } }),
    # Only the primary's installed figure: unknown.
    s2 => $lpar->(pool => 0, ent => 1.0, role => { type => 'haStandby', primary => 'p2', primaryTarget => { entitlement => 4.0, source => 'installed' } }),
    # Pending: unknown.
    s3 => $lpar->(pool => 0, ent => 1.0, pending => 1, role => { type => 'haStandby', primary => 'p3', unresolved => 'pending' }),
    # Fixed standby, its primary analysed here: known from the primary's own target. In pool 5, whose maximum it would exceed.
    s4 => $lpar->(pool => 5, max => 4, ent => 0.1, vps => 3, role => { type => 'haStandbyFixed', primary => 'p4', VPs => 3, targetVPs => 3 }),
    p4 => $lpar->(pool => 0, ent => 3.0),
    # Fixed standby whose primary is elsewhere: unknown.
    s5 => $lpar->(pool => 0, ent => 0.1, role => { type => 'haStandbyFixed', primary => 'elsewhere' }),
    # Planned above the requirement: increment 0.
    s6 => $lpar->(pool => 0, ent => 3.0, role => { type => 'haStandby', primary => 'p6', primaryTarget => { entitlement => 2.0, source => 'run' } }),
    # A held active node and an LPAR with no role: not eligible; nothing is invented for them.
    h1 => $lpar->(pool => 0, ent => 1.0, role => { type => 'haHold' }),
);
my $big = { %$agreed, processors => 40 };
my $hc = UUT::_frame_check(\%ha, $big, undef, $END);
my $t = $hc->{takeover};
is_deeply([ @{$t}{qw(eligible known)} ], [ 6, 3 ], 'six standbys sized as one, three with a known requirement');
like((grep { $_->{standby} eq 's5' } @{ $t->{unknown} })[0]{reason}, qr/the finalisation of a planning cycle looks for its plan/, 'a fixed standby with its primary elsewhere: unknown in the analysis, with where it becomes known');
# The cycle's lookup (a reporting dependency): a compatible plan of the primary gives the requirement; an incompatible one the reason.
my $with = UUT::_frame_check(\%ha, $big, undef, $END, { s5 => { requirement => 2.5, source => 'the target of elsewhere in its plan of this planning cycle (frame FB)' } });
my ($s5) = grep { $_->{standby} eq 's5' } @{ $with->{takeover}{cases} };
is_deeply([ $with->{takeover}{known}, $s5->{requirement}, $s5->{increment}{Full}, $s5->{planned}{Full} ], [ 4, 2.5, 2.4, 0.1 ], 'known through the cycle: the increment above the fixed 0.10, which is itself unchanged');
my $without = UUT::_frame_check(\%ha, $big, undef, $END, { s5 => { reason => 'the plan of elsewhere on frame FB is not used: its saved plan was made with other options' } });
like((grep { $_->{standby} eq 's5' } @{ $without->{takeover}{unknown} })[0]{reason}, qr/made with other options/, 'an incompatible plan: unknown, with the reason');
# An already-negative balance: no favourable reading of the takeover.
my $tight = UUT::_frame_check(\%ha, { %$agreed, processors => 20 }, undef, $END);
ok($tight->{remaining}{Full} < 0, 'a frame whose guarantees already exceed its shared capacity');
my $tight_text = flat(UUT::_frame_check_lines($tight, 'cycle'));
like($tight_text, qr/the guarantees already exceed the shared capacity by \d+\.\d\d, and the increment adds to that shortfall: \d+\.\d\d/, 'the increment is said to add to the shortfall');
like($tight_text, qr/As planned, these allocations cannot all be configured on the shared capacity observed: the shortfall has to be resolved first .* No figure has been reduced to fit\./, 'the plan is said not to be implementable as it stands; nothing is capped');
unlike($tight_text, qr/remaining of the shared capacity after it/, 'and no remainder is offered after the takeover');
like(flat(UUT::_frame_check_lines($hc, 'cycle')), qr/This is an entitlement requirement against allocations\. It does not verify that a takeover would perform/, 'a known requirement is not a verified takeover');
is_deeply([ sort map { $_->{standby} } @{ $t->{unknown} } ], [qw(s2 s3 s5)], 'the unknown ones named');
my %case = map { ($_->{standby} => $_) } @{ $t->{cases} };
is_deeply($case{s1}{increment}, { CurrentCycle => 4, Full => 4.2 }, 'the increment above the planned entitlement, by basis');
is_deeply($case{s6}{increment}, { CurrentCycle => 0, Full => 0 }, 'never negative');
is($case{s4}{requirement}, 3, "a fixed standby's requirement from its primary's own target on this frame");
is_deeply($t->{largest}, { CurrentCycle => 's1', Full => 's1' }, 'the largest known increment, this cycle and full');
is_deeply($t->{after}, { CurrentCycle => $hc->{remaining}{CurrentCycle} - 4, Full => $hc->{remaining}{Full} - 4.2 }, 'the remainder after it, the other allocations kept');
like(join(' ', @{ $case{s1}{prerequisites}{CurrentCycle} }), qr/^VPs: 2 planned, at least 5 needed to carry 5\.00$/, 'a VP prerequisite beside it');
like(join(' ', @{ $case{s4}{prerequisites}{Full} }), qr/pool 5: maximum 4, guarantees 2\.60; the increment of 2\.90 needs a maximum of at least 5\.50/, 'a pool prerequisite');
my $ht = flat(UUT::_frame_check_lines($hc, 'cycle'));
like($ht, qr/Known single-takeover entitlement check \(one takeover at a time; the other allocations stay as they are; nFit does not assume\s+ROHA or capacity on demand\): 6 standbys, requirement known for 3; coverage is incomplete\./,
     'the check is named as a known single-takeover entitlement check, with its coverage');
like($ht, qr/This cycle: the largest known increment is s1 taking over from p1: requirement 5\.00 \(the primary's target\), planned 1\.00, increment 4\.00; remaining of the\s+shared capacity after it/, 'the largest case');
like($ht, qr/Requirement not known: s2 \(only the primary's installed entitlement is known, not its requirement\); s3 \(its primary's plan is not resolved\); s5 \(its primary is not analysed on this frame/,
     'the unknown cases with their reasons');
like($ht, qr/Made on the finalised plan of the planning cycle\./, 'at finalisation, said so');
unlike($ht, qr/failover capacity|can absorb|fits/i, 'no unqualified failover-capacity verdict');
is(scalar(my @cells = UUT::_frame_check_csv_cells($hc)), 12, 'twelve CSV cells');
is_deeply([ @cells[2 .. 4] ], [ 6, 3, '4.00' ], 'standbys, known, largest increment this cycle');
is(UUT::_frame_check({}, $agreed, undef, $END)->{takeover}{eligible}, 0, 'no standby: nothing to check');

done_testing();
