#!/usr/bin/env perl
# ==============================================================================
# NAME     : test/stage_nmon_static_states.t
# PURPOSE  : The static configuration states nfit-stage-nmon writes for the CSV
#            source (--physc-data), by sub-extraction (D-025).
#            - a VM's state ends at its own last sample, not at the end of the
#              files: a VM whose column stops has left;
#            - a VM sampled to the end is as before;
#            - a VM named in a header and never sampled keeps the capture end;
#            - the merged rows are as before.
# USAGE    : perl stage_nmon_static_states.t      (from test/; or via dev/run-tests)
# ==============================================================================
use strict;
use warnings;
use Test::More;
use File::Temp qw(tempdir);
use JSON;

my $SRC = $ENV{NFIT_STAGE_NMON} // '../nfit-stage-nmon';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;
my $code = '';
for my $name (qw(merge_csv_to_cache create_static_state_cache))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
eval "package UUT;\nuse Time::Piece;\nuse JSON;\nuse Scalar::Util qw(looks_like_number);\nour \$DEFAULT_SMT = 8;\n$code\n1;" or die "harness eval failed: $@\n";

my $dir = tempdir(CLEANUP => 1);
my $write = sub
{
    my ($name, $text) = @_;
    open my $out, '>', "$dir/$name" or die;
    print {$out} $text;
    close $out;
    return "$dir/$name";
};
# vmGone has values for the first two samples only; vmNever is named and never sampled.
my $physc = $write->('physc.csv', join("\n", 'Time,vmStay,vmGone,vmNever',
    '2026-09-01 00:00:00,1.0,0.5,', '2026-09-01 00:01:00,1.1,0.6,', '2026-09-01 00:02:00,1.2,,', '2026-09-01 00:03:00,1.3,,') . "\n");
my $runq = $write->('runq.csv', join("\n", 'Time,vmStay,vmGone,vmNever',
    '2026-09-01 00:00:00,2,1,', '2026-09-01 00:01:00,2,1,', '2026-09-01 00:02:00,2,,', '2026-09-01 00:03:00,2,,') . "\n");

my ($vms, $min, $max, $last) = UUT::merge_csv_to_cache($physc, $runq, "$dir/data");
is_deeply([ sort keys %$vms ], [qw(vmGone vmNever vmStay)], 'every VM named in a header is found, as before');
is($max - $min, 180, 'the capture spans the four samples');
is_deeply($last, { vmStay => $max, vmGone => $min + 60 }, 'the last sample of each VM; none for a VM never sampled');
open my $in, '<', "$dir/data" or die;
my @rows = <$in>;
close $in;
is(scalar(@rows), 1 + 4 + 2, 'the merged rows are as before: a header, four of vmStay and two of vmGone');

UUT::create_static_state_cache($vms, undef, $min, $max, "$dir/states", $last);
open $in, '<', "$dir/states" or die;
my $states = decode_json(do { local $/; <$in> });
close $in;
is_deeply([ @{ $states->{vmStay}[0] }{qw(start_epoch end_epoch)} ], [ $min, $max ], 'a VM sampled to the end: its state runs to the end, as before');
is_deeply([ @{ $states->{vmGone}[0] }{qw(start_epoch end_epoch)} ], [ $min, $min + 60 ], 'a VM that left: its state ends at its last sample');
is_deeply([ @{ $states->{vmNever}[0] }{qw(start_epoch end_epoch)} ], [ $min, $max ], 'a VM never sampled keeps the capture end: nothing is known about it');
is($states->{vmGone}[0]{metadata}{smt}, 8, 'the metadata is as before');

UUT::create_static_state_cache($vms, undef, $min, $max, "$dir/states2");
open $in, '<', "$dir/states2" or die;
$states = decode_json(do { local $/; <$in> });
close $in;
is($states->{vmGone}[0]{end_epoch}, $max, 'without the last samples the states are as they were');

done_testing();
