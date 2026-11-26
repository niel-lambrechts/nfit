#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: test/cycle_finalise.t
# PURPOSE:   The finalisation of one frame of a planning cycle (v10 C7 with
#            B11), extracted from nfit-profile: _cycle_finalise_frame,
#            _cycle_rewrite_csv, _cycle_finalise_lines.
#            - a standby is set from its primary's plan in the cycle: the floor
#              is the share of the primary's target, and may be an increase;
#            - the pool of the standby is assessed again on the final figures:
#              every row of a pool that no longer holds its entitlements says
#              so, and the standby's increase waits for the pool;
#            - without the primary's plan the standby stays as the analysis left
#              it, with the reason (frame not analysed, analysis not completed,
#              other options, primary installed differently);
#            - role evidence read on another capture than the primary's frame
#              was analysed on is an integrity error;
#            - the provisional plan is not changed, and finalising twice gives
#              the same result;
#            - a CSV row is replaced cell by cell; every other byte stays;
#            - the allocation footprint (B11): the shared capacity of a frame is
#              what its default-pool LPARs agree on; entitlements are compared
#              with it per frame, pool maxima are listed and never added up, and
#              a missing or conflicting capacity is "not assessed".
# USAGE:     perl test/cycle_finalise.t   (or through dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;

my $SRC = $ENV{NFIT_PROFILE} // '../nfit-profile';
open my $fh, '<', $SRC or die "cannot read $SRC: $!\n";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
foreach my $name (qw(_safe_dig _cycle_canonical _option_differences _ha_peer_plan_mismatch _ha_target_of_member _ha_standby_target
                     _ha_standby_action _resolve_current_cycle_pair _ceil_ratio _current_cycle_action_text _coarse_note_text
                     _coarse_review_text _coarse_review_advisories _coarse_review_action _assess_pool_entitlement_feasibility
                     _structural_shown_value _pool_units_text _pool_prerequisites _structural_csv_tags _rationale_wrap quote_csv
                     _cycle_finalise_frame _cycle_finalise_lines _cycle_rewrite_csv
                     _frame_shared_capacity _cycle_footprint_frame _cycle_footprint_lines))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name\n";
    $code .= "$1\n";
}
my %constant = map { my ($v) = $src =~ /^my \$$_\s*=\s*([0-9.e-]+);/m or die "$_ not found\n"; ($_ => $v) }
               qw(MIN_ENT_PER_VP STRUCTURAL_ENT_TOLERANCE FLOAT_EPSILON VCPU_RATIO_MAX);
my ($interval) = $src =~ /^my %EVIDENCE_INTERVAL\s*=\s*(\(.*?\));/m or die "EVIDENCE_INTERVAL not found\n";
eval "package UUT; use JSON; use POSIX qw(ceil floor); use List::Util qw(sum sum0 min max uniq); use Scalar::Util qw(looks_like_number);\n"
   . join('', map { "our \$$_ = $constant{$_};\n" } sort keys %constant) . "our %EVIDENCE_INTERVAL = $interval;\n$code 1;"
   or die "harness eval failed: $@\n";

# -- Two frames: standby sa (with x1, in pool 5 of 4.00) on FA; its primary pb on FB --
my %settings = (options => { enddate => '2026-09-21', 'guarantee-policy' => 'normal' }, filesSHA256 => { profiler => 'p1', engine => 'e1' },
                guaranteePolicy => 'tier', contestedLevel => 'off', vmFilter => undef);
my %capture_b = (kind => 'size and time (weaker)', data => { revision => undef, size => 10, modified => 5 }, states => 'absent', pool => 'absent');
my $evidence = sub
{
    my ($role) = @_;
    return { cluster => [qw(sa pb)], role => { effective => $role }, sizing => { policy => 'standby_share', share => 0.5 },
             caches => { FA => undef, FB => undef }, configuration => { sa => { entitlement => 2, VPs => 4 }, pb => { entitlement => 4, VPs => 8 } } };
};
my $standby_doc = sub
{
    return { window => { end => '2026-09-21' }, settings => { %settings }, cycle => { id => 'C1', state => 'provisional' },
        members => {
            sa => { planningProfile => 'P-95', installedEntitlement => 2, installedVPs => 4, poolId => 5, poolSize => 4,
                    profiles => { 'P-95' => { entitlement => 0.8, recommendedVPs => 1, tacticalVPs => 3 } },
                    role => { type => 'haStandby', profile => 'P-95', targetEntitlement => 2, targetVPs => 2, entitlement => 2, VPs => 4,
                              primary => 'pb', unresolved => 'its floor is set when the planning cycle is finalised' },
                    pendingRole => { type => 'haStandby', primary => 'pb', share => 0.5, cluster => [qw(sa pb)], primaryFrames => ['FB'],
                                     roleSource => 'configured', primaryRole => 'active' },
                    roleEvidence => $evidence->('standby'),
                    poolMember => { pool_id => 5, pool_cpu => 4, current_ent => 2, proposed_ent => 2, cycle_ent => 2, lowest_ent => 0.8,
                                    planning_profile => 'P-95', tier_source => 'configured' },
                    published => { entitlement => '2.00', VPs => 4, action => 'Provisional: floor pending. No change.',
                                   ownAction => 'Provisional: floor pending. No change.', comments => 'note', ownComments => 'note', alerts => [] } },
            x1 => { planningProfile => 'P-95', installedEntitlement => 1.5, installedVPs => 2, poolId => 5, poolSize => 4,
                    profiles => { 'P-95' => { entitlement => 1.5, recommendedVPs => 2, tacticalVPs => 2 } },
                    poolMember => { pool_id => 5, pool_cpu => 4, current_ent => 1.5, proposed_ent => 1.5, cycle_ent => 1.5, lowest_ent => 1.5,
                                    planning_profile => 'P-95', tier_source => 'configured' },
                    published => { entitlement => '1.50', VPs => 2, action => 'No change.', ownAction => 'No change.',
                                   comments => '[VP_MISMATCH]; kept', ownComments => 'kept', alerts => ['[VP_MISMATCH]'] } } } };
};
my $primary_doc = sub
{
    my (%over) = @_;
    return { window => { end => '2026-09-21' }, settings => { %settings, %{ $over{settings} // {} } }, cycle => { id => 'C1', state => 'provisional' },
        members => { pb => { planningProfile => 'P-99', installedEntitlement => 4, installedVPs => 8,
                             profiles => { 'P-99' => { entitlement => 6, recommendedVPs => 7 } }, roleEvidence => $evidence->('active'),
                             %{ $over{member} // {} } } } };
};
my %plan = (planA => $standby_doc->(), planB => $primary_doc->());
my $plan_of = sub { return $plan{ $_[0] }; };
my $reg_a = { system => 'FA', model => 'state-based', plan => 'planA', peers => { FB => { %capture_b } },
              pending => [ { lpar => 'sa', primary => 'pb' } ] };
my $reg_b = { system => 'FB', model => 'state-based', plan => 'planB', capture => { %capture_b }, pending => [] };
my $before = UUT::_cycle_canonical($plan{planA});

my $r = UUT::_cycle_finalise_frame($plan{planA}, $reg_a, { FA => $reg_a, FB => $reg_b }, {}, $plan_of, 'C1');
is($r->{integrity}, undef, 'no integrity error');
is(UUT::_cycle_canonical($plan{planA}), $before, 'the provisional plan is not changed');
my $sa = $r->{doc}{members}{sa};
is_deeply([ $r->{standbys}[0]{state}, $sa->{role}{floor}, $sa->{role}{targetEntitlement}, $sa->{role}{primaryTarget}{source} ],
          [ 'resolved', 3, 3, 'cycle' ], 'the floor is the share of the primary\'s target in the cycle, here an increase');
ok(!exists $sa->{pendingRole} && !exists $sa->{role}{unresolved}, 'nothing is left pending');
is_deeply($r->{doc}{cycle}, { id => 'C1', state => 'final', provisionalPlan => 'planA' }, 'the final plan names the cycle and its provisional plan');
like($r->{rows}{sa}{action}, qr/PowerHA standby for pb \(configured\): 50% of its 6\.00 target \(its plan in this planning cycle\) = 3\.00 entitlement; own workload needs 0\.80\./,
     'the action states the floor and its source');
is($sa->{finalisation}{provisional}{entitlement}, '2.00', 'the provisional figure is kept beside the final one');
# The pool on the final figures: 3.00 + 1.50 against a maximum of 4.
is_deeply([ map { ($_->{PoolId}, $_->{Status}) } @{ $r->{pools} } ], [ 5, 'SHORTFALL' ], 'the standby\'s pool is assessed again and no longer holds the targets');
is($r->{rows}{sa}{comments}, '[POOL_SHORTFALL]; note', 'the standby\'s row carries the pool alert before its own comment');
is($r->{rows}{x1}{comments}, '[POOL_SHORTFALL]; [VP_MISMATCH]; kept', 'so does the other LPAR of the pool, with its own alert and comment kept');
like($r->{rows}{sa}{action}, qr/pool 5 raised to at least 5 processing units|raise the maximum of pool 5 from .* to at least 5 processing units/,
     'the increase waits for the pool maximum');
is_deeply([ @{ $r->{rows}{x1} }{qw(entitlement VPs action)} ], [ '1.50', 2, 'No change.' ], 'an LPAR that does not rise keeps its figures and action');
ok(abs($r->{delta}{Target} - 1) < 1e-9, 'the frame totals move by the standby\'s change');
my $again = UUT::_cycle_finalise_frame($plan{planA}, $reg_a, { FA => $reg_a, FB => $reg_b }, {}, $plan_of, 'C1');
is(UUT::_cycle_canonical($again->{doc}), UUT::_cycle_canonical($r->{doc}), 'finalising again gives the same plan: the floor does not compound');

# A primary whose reductions were held after it took over: its held pair is the reference.
$plan{planB} = $primary_doc->(member => { profiles => { 'P-99' => { entitlement => 1, recommendedVPs => 2 } },
                                          role => { type => 'haHold', entitlement => 4, VPs => 8 } });
$r = UUT::_cycle_finalise_frame($plan{planA}, $reg_a, { FA => $reg_a, FB => $reg_b }, {}, $plan_of, 'C1');
is_deeply([ $r->{standbys}[0]{state}, $r->{doc}{members}{sa}{role}{floor} ], [ 'resolved', 2 ], 'a held primary counts at its held pair');

# -- Unresolved: the standby stays as analysed, with the reason --
my $unresolved = sub
{
    my ($label, $frames, $incomplete, $pattern) = @_;
    my $u = UUT::_cycle_finalise_frame($plan{planA}, $reg_a, $frames, $incomplete, $plan_of, 'C1');
    my $role = $u->{doc}{members}{sa}{role};
    like($u->{standbys}[0]{reason} // '', $pattern, "$label: the reason");
    is_deeply([ $u->{standbys}[0]{state}, $role->{primaryTarget}{source}, $role->{floor}, $u->{rows}{sa}{entitlement} ],
              [ 'unresolved', 'installed', 2, '2.00' ], "$label: the installed primary limits a cut and adds no increase");
    like($u->{rows}{sa}{action}, qr/^PowerHA standby for pb \(configured\): unresolved, /, "$label: the row says unresolved");
    return;
};
$plan{planB} = $primary_doc->();
$unresolved->('primary frame not in the cycle', { FA => $reg_a }, {}, qr/^pb's frame is not analysed in this cycle$/);
$unresolved->('primary frame begun and not completed', { FA => $reg_a }, { FB => 1 }, qr/^the analysis of pb's frame was not completed in this cycle$/);
$plan{planB} = $primary_doc->(settings => { options => { enddate => '2026-09-21', 'guarantee-policy' => 'strict' } });
$unresolved->('primary analysed with other options', { FA => $reg_a, FB => $reg_b }, {}, qr/other options/);
$plan{planB} = $primary_doc->(member => { installedEntitlement => 5 });
$unresolved->('primary installed differently', { FA => $reg_a, FB => $reg_b }, {}, qr/installed configuration differs/);
$plan{planB} = $primary_doc->();
$plan{planB}{cycle}{id} = 'C0';
$unresolved->('a provisional plan of another cycle', { FA => $reg_a, FB => $reg_b }, {}, qr/provisional/);
$plan{planB} = $primary_doc->();

# -- The role evidence read another capture than the one analysed --
$r = UUT::_cycle_finalise_frame($plan{planA}, { %$reg_a, peers => { FB => { %capture_b, data => { revision => undef, size => 11, modified => 5 } } } },
                                { FA => $reg_a, FB => $reg_b }, {}, $plan_of, 'C1');
like($r->{integrity} // '', qr/^the staged data of frame FB read for the PowerHA roles is not the data that frame was analysed on$/, 'another capture of the peer is an integrity error');

# -- The rationale block --
$r = UUT::_cycle_finalise_frame($plan{planA}, $reg_a, { FA => $reg_a, FB => $reg_b }, {}, $plan_of, 'C1');
my @block = UUT::_cycle_finalise_lines('C1', $r, { All => { Installed => 3.5, CurrentCycle => 4.5, Target => 4.5 } });
is_deeply([ @block[0 .. 3], $block[-2] ], [ '', '=' x 70, 'Cycle Finalisation (planning cycle C1)', '=' x 70, '[End of Cycle Finalisation]' ], 'the block is delimited');
ok((grep { /^    sa: 2\.00 \/ 4 provisional, now / } @block), 'the standby with its provisional and final pair');
like(join(' ', @block), qr/Pool 5: entitlements 4\.50 this cycle, 4\.50 at the targets; maximum 4: raise it to at least 5\s+processing units/, 'the pool on the final figures');
ok(!(grep { /\d\d:\d\d/ } @block), 'no time of day: a repeat writes the same block');

# -- CSV rows --
my $csv = join("\n", '"VM","Current - ENT","NFIT - ENT","NFIT - VCPU","NFIT - Action","VM_Comments"',
               '"sa","2.00","2.00","4","Provisional: ""floor"" pending, later","note"', '"x1","1.50","1.50","2","No change.","[VP_MISMATCH]; kept"', '');
my $out = UUT::_cycle_rewrite_csv($csv, { sa => { entitlement => '3.00', VPs => 4, action => 'Raise, then "check"', comments => '[POOL_SHORTFALL]; note' },
                                          x1 => { entitlement => '1.50', VPs => 2, action => 'No change.', comments => '[VP_MISMATCH]; kept' } });
my @in = split /\n/, $csv, -1;
my @got = split /\n/, $out, -1;
is($got[1], '"sa","2.00","3.00","4","Raise, then ""check""","[POOL_SHORTFALL]; note"', 'the row is replaced cell by cell, quotes kept');
is_deeply([ @got[0, 2, 3] ], [ @in[0, 2, 3] ], 'the header, an unchanged row and the end of the file are byte for byte as they were');
is(UUT::_cycle_rewrite_csv($out, { sa => { entitlement => '3.00', VPs => 4, action => 'Raise, then "check"', comments => '[POOL_SHORTFALL]; note' } }), $out,
   'rewriting with the same cells changes nothing');
is(UUT::_cycle_rewrite_csv($csv, { zz => { entitlement => '1.00' } }), undef, 'a row that is not there: the file is left alone');
is(UUT::_cycle_rewrite_csv("no header\n", { sa => { entitlement => '1.00' } }), undef, 'not a CSV of the analysis: left alone');

# -- The allocation footprint --
my $map = { a => { Configuration => { pool_id => 0, pool_cpu => 40 } }, b => { Configuration => { pool_id => 0, pool_cpu => 40 } },
            c => { Configuration => { pool_id => 5, pool_cpu => 4 } }, d => { Configuration => { pool_id => 0, pool_cpu => 0 } } };
my $capacity = UUT::_frame_shared_capacity($map, [qw(a b c d)]);
is_deeply([ @{$capacity}{qw(status processors reporters)} ], [ 'agreed', 40, 2 ], 'shared capacity: what the default-pool LPARs agree on; a user pool and a zero are no reporters');
$map->{b}{Configuration}{pool_cpu} = 38;
$capacity = UUT::_frame_shared_capacity($map, [qw(a b c d)]);
is_deeply([ $capacity->{status}, $capacity->{processors}, $capacity->{values} ], [ 'conflict', undef, { 38 => 1, 40 => 1 } ], 'shared capacity: reporters that disagree are a conflict, with no figure');
is(UUT::_frame_shared_capacity($map, [qw(c)])->{status}, 'absent', 'shared capacity: no default-pool reporter');

my $frame = sub
{
    my ($capacity_block, %ent) = @_;
    return { frame => ($capacity_block ? { sharedProcessors => $capacity_block } : undef), members => {
        v1 => { installedVPs => 2, planningProfile => 'P', profiles => { P => { recommendedVPs => 3 } }, role => { type => 'viosPair', targetVPs => 4 },
                published => { VPs => 3 }, poolMember => { pool_id => 0, pool_cpu => 8, current_ent => 1, proposed_ent => ($ent{v1} // 2), cycle_ent => 1.5 } },
        s1 => { installedVPs => 4, planningProfile => 'P', profiles => { P => { recommendedVPs => 1 } }, published => { VPs => '' },
                poolMember => { pool_id => 5, pool_cpu => 4, current_ent => 2, proposed_ent => ($ent{s1} // 3), cycle_ent => 2.5 } },
        x1 => { installedVPs => 2, planningProfile => 'P', profiles => { P => { recommendedVPs => 2 } }, published => { VPs => 2 },
                poolMember => { pool_id => 5, pool_cpu => 4, current_ent => 1.5, proposed_ent => undef, cycle_ent => undef } },
        n1 => { installedVPs => 1, planningProfile => 'P', profiles => { P => {} }, poolMember => { pool_id => undef, current_ent => 0.5 } } } };
};
my $fp = UUT::_cycle_footprint_frame($frame->({ status => 'agreed', processors => 8, reporters => 1, values => { 8 => 1 } }));
is_deeply([ $fp->{lpars}, $fp->{noPool}, $fp->{vps} ], [ 4, 1, { Installed => 9, Full => 8, CurrentCycle => 10 } ],
          'VPs: installed, the full requirement (a role figure before the profile\'s) and this cycle (installed where the row states none)');
is_deeply($fp->{shared}, { Count => 3, Installed => 4.5, Full => 6.5, CurrentCycle => 5.5 }, 'entitlements of the LPARs with a pool, each once; an LPAR without a result keeps what it has');
is_deeply($fp->{uncommitted}, { Installed => 3.5, Full => 1.5, CurrentCycle => 2.5 }, 'against the shared capacity: what is not committed as entitlement');
is($fp->{assessment}, 'the entitlements of the LPARs analysed are within the shared capacity reported', 'within: stated of the LPARs analysed, no frame verdict');
is_deeply([ map { [ @{$_}{qw(Pool Default Maximum Installed Full CurrentCycle MinFull MinCurrentCycle Status)} ] } @{ $fp->{pools} } ],
          [ [ 0, 1, 8, 1, 2, 1.5, undef, undef, 'the default pool: its limit is the shared capacity of the frame' ],
            [ 5, 0, 4, 3.5, 4.5, 4, 5, 4, 'below the full requirement' ] ],
          'pools: the default pool against the shared capacity; a user pool with its maximum and the maximum each basis needs');
$fp = UUT::_cycle_footprint_frame($frame->({ status => 'agreed', processors => 6, reporters => 1, values => { 6 => 1 } }, v1 => 3));
is($fp->{assessment}, 'the full requirement exceeds it by 1.50', 'an excess is named with its size, per basis');
$fp = UUT::_cycle_footprint_frame($frame->({ status => 'conflict', reporters => 2, values => { 6 => 1, 8 => 1 } }));
is_deeply([ $fp->{assessment}, $fp->{uncommitted} ], [ 'not assessed: the default-pool LPARs report different shared capacities (6 by 1, 8 by 1)', undef ], 'a conflict is not assessed');
$fp = UUT::_cycle_footprint_frame($frame->(undef));
like($fp->{assessment}, qr/^not assessed: no LPAR of the default pool reports the shared capacity of the frame$/, 'no capacity in the saved plan: not assessed');

my $good = UUT::_cycle_footprint_frame($frame->({ status => 'agreed', processors => 8, reporters => 1, values => { 8 => 1 } }));
my %groups = (All => { Count => 4, Installed => 5, CurrentCycle => 6, Target => 7 }, Vios => { Count => 1, Installed => 1, CurrentCycle => 1.5, Target => 2 },
              Standby => { Count => 0, Installed => 0, CurrentCycle => 0, Target => 0 });
my ($frames, $pools, $text) = UUT::_cycle_footprint_lines('C1', [
    { model => 'state-based', frame => 'FA', state => 'final', lpars => 4, totals => \%groups, resolved => 0, unresolved => 0, footprint => $good },
    { model => 'state-based', frame => 'FB', state => 'standbys unresolved', lpars => 4, totals => \%groups, resolved => 1, unresolved => 2, footprint => $good },
    { model => 'state-based', frame => 'FC', state => 'not analysed' } ], [ 'Planning cycle C1: INCOMPLETE' ]);
my $all = join("\n", @$text);
is($text->[0], 'Planning cycle C1: INCOMPLETE', 'the footprint opens with the completeness of the cycle');
like($all, qr/Model: hybrid-state-decay \(primary view\)\n-+\n  Not analysed in this cycle\.\n.*Model: state-based\n/s, 'hybrid-state-decay comes first, and says so when it was not analysed');
like($all, qr/^  FA\s+4\s+5\.00\s+7\.00\s+6\.00\s+9\/8\/10\s+final$/m, 'a frame: installed, full and cycle apart');
like($all, qr/of which VIOS\s+1\s+1\.00\s+2\.00\s+1\.50/, 'VIOS shown as part of the frame, not added to it');
like($all, qr/Standbys across frames: 1 set, 2 UNRESOLVED/, 'unresolved standbys are stated on their frame');
like($all, qr/^  FC\s+not analysed$/m, 'an expected frame that was not analysed is listed without figures');
like($all, qr/Frames analysed\s+10\.00\s+14\.00\s+12\.00\s+\(2 frames analysed; a sum of entitlements, not a capacity statement\)/, 'the sum over frames is labelled as no capacity statement');
unlike($all, qr/\bfits\b|procure|consolidat|spare/i, 'no fit, procurement, consolidation or spare-capacity wording');
is(scalar(@$frames), 4, 'frame file: a header and one row per frame of the model');
is(scalar(@$pools), 5, 'pool file: each pool of each analysed frame on its own row');
like($pools->[2], qr/^"C1","state-based","FA","5","2","4","3\.50","4\.50","4\.00","5","4","below the full requirement"$/, 'a pool row: maximum, the three entitlement sums and the maximum each needs');

done_testing();
