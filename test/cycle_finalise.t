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
#            - a CSV row is replaced cell by cell; every other byte stays.
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
                     _cycle_finalise_frame _cycle_finalise_lines _cycle_rewrite_csv))
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

done_testing();
