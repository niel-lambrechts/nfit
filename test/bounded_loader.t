#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/bounded_loader.t
# PURPOSE:   Unit tests for Phase 2 — Bounded Month Loader helpers:
#            _get_month_boundaries, _get_history_month_index,
#            _determine_required_months, _read_bounded_history_months.
#
# USAGE:     perl t/bounded_loader.t
#            prove t/bounded_loader.t
#
# NOTE:      Phase 2a covers pure-logic tests (boundaries, month selection).
#            Phase 2b will add filesystem-dependent tests for the bounded
#            reader (_read_bounded_history_months).
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 39;
use Time::Piece;
use Time::Seconds;
use File::Temp qw(tempdir);
use File::Spec;
use File::Path qw(make_path);
use JSON;

# ---------------------------------------------------------------------------
# Inline copies of Phase 2a functions for isolated testing.
# ---------------------------------------------------------------------------

sub _get_month_boundaries {
    my ($month_key) = @_;
    unless ($month_key && $month_key =~ /^\d{4}-\d{2}$/) { return (); }
    my $month_start = eval { Time::Piece->strptime("${month_key}-01", '%Y-%m-%d') };
    return () if ($@ || !$month_start);
    my $month_end = $month_start->add_months(1) - ONE_DAY;
    return ($month_start, $month_end);
}

sub _get_history_month_index {
    my ($system_cache_dir) = @_;
    my %index;
    my $partition_dir = File::Spec->catfile($system_cache_dir, '.nfit.history');
    return \%index unless (-d $partition_dir);
    opendir(my $dh, $partition_dir) or return \%index;
    while (my $entry = readdir($dh)) {
        if ($entry =~ /^nfit\.hist\.(\d{4}-\d{2})\.json$/) {
            $index{$1} = 1;
        }
    }
    closedir($dh);
    return \%index;
}

sub _determine_required_months {
    my ($end_date_obj, $lookback_days, $history_index_href) = @_;
    return [] unless ($end_date_obj && $lookback_days && $lookback_days > 0
                      && $history_index_href && ref($history_index_href) eq 'HASH'
                      && %$history_index_href);
    my $window_start = $end_date_obj - (($lookback_days - 1) * ONE_DAY);
    my $window_end   = $end_date_obj;
    my @required;
    foreach my $month_key (keys %$history_index_href) {
        my ($m_start, $m_end) = _get_month_boundaries($month_key);
        next unless ($m_start && $m_end);
        next if $m_end   < $window_start;
        next if $m_start > $window_end;
        push @required, $month_key;
    }
    return [ sort @required ];
}

# ==============================================================================
#  TESTS: _get_month_boundaries
# ==============================================================================

# Test 2.1: Standard 31-day month
{
    my ($start, $end) = _get_month_boundaries('2026-01');
    is($start->strftime('%Y-%m-%d'), '2026-01-01', '2.1: January start = 01');
    is($end->strftime('%Y-%m-%d'),   '2026-01-31', '2.1: January end = 31');
}

# Test 2.2: February non-leap year
{
    my ($start, $end) = _get_month_boundaries('2025-02');
    is($start->strftime('%Y-%m-%d'), '2025-02-01', '2.2: Feb 2025 start = 01');
    is($end->strftime('%Y-%m-%d'),   '2025-02-28', '2.2: Feb 2025 end = 28 (non-leap)');
}

# Test 2.3: February leap year
{
    my ($start, $end) = _get_month_boundaries('2024-02');
    is($start->strftime('%Y-%m-%d'), '2024-02-01', '2.3: Feb 2024 start = 01');
    is($end->strftime('%Y-%m-%d'),   '2024-02-29', '2.3: Feb 2024 end = 29 (leap)');
}

# Test 2.4: Short month (April = 30 days)
{
    my ($start, $end) = _get_month_boundaries('2026-04');
    is($start->strftime('%Y-%m-%d'), '2026-04-01', '2.4: April start = 01');
    is($end->strftime('%Y-%m-%d'),   '2026-04-30', '2.4: April end = 30');
}

# Test 2.5: December — year boundary
{
    my ($start, $end) = _get_month_boundaries('2025-12');
    is($start->strftime('%Y-%m-%d'), '2025-12-01', '2.5: December start = 01');
    is($end->strftime('%Y-%m-%d'),   '2025-12-31', '2.5: December end = 31');
}

# Test 2.6: Malformed month_key returns empty
{
    my @result = _get_month_boundaries('bad');
    is(scalar @result, 0, '2.6a: Malformed key returns empty list');
    my @result2 = _get_month_boundaries(undef);
    is(scalar @result2, 0, '2.6b: undef key returns empty list');
}

# ==============================================================================
#  TESTS: _get_history_month_index
# ==============================================================================

# Test 2.7: Reads filenames from .nfit.history directory
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);

    # Create mock history files
    for my $mk ('2025-10', '2025-11', '2025-12', '2026-01') {
        open my $fh, '>', File::Spec->catfile($hist_dir, "nfit.hist.${mk}.json") or die $!;
        print $fh '{}';
        close $fh;
    }
    # Create a non-matching file that should be ignored
    open my $fh, '>', File::Spec->catfile($hist_dir, 'readme.txt') or die $!;
    close $fh;

    my $index = _get_history_month_index($tmpdir);
    is_deeply([ sort keys %$index ], ['2025-10', '2025-11', '2025-12', '2026-01'],
        '2.7: Index contains exactly the valid month files');
}

# Test 2.8: Non-existent directory returns empty
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $index = _get_history_month_index($tmpdir);
    is_deeply($index, {}, '2.8: Missing .nfit.history returns empty index');
}

# ==============================================================================
#  TESTS: _determine_required_months
# ==============================================================================

# Test 2.9: 90-day window ending mid-March
# Window: 2025-12-16 to 2026-03-15 (90 days inclusive)
# Should include: 2025-12, 2026-01, 2026-02, 2026-03
{
    my $end = Time::Piece->strptime('2026-03-15', '%Y-%m-%d');
    my $index = { '2025-11' => 1, '2025-12' => 1, '2026-01' => 1,
                  '2026-02' => 1, '2026-03' => 1, '2026-04' => 1 };
    my $result = _determine_required_months($end, 90, $index);
    is_deeply($result, ['2025-12', '2026-01', '2026-02', '2026-03'],
        '2.9: 90-day window selects correct 4 months');
}

# Test 2.10: Sparse history — only available months returned
{
    my $end = Time::Piece->strptime('2026-03-15', '%Y-%m-%d');
    my $index = { '2026-01' => 1, '2026-03' => 1 };  # Missing 2025-12, 2026-02
    my $result = _determine_required_months($end, 90, $index);
    is_deeply($result, ['2026-01', '2026-03'],
        '2.10: Sparse history — only available months returned');
}

# Test 2.11: Short window within a single month
{
    my $end = Time::Piece->strptime('2026-01-20', '%Y-%m-%d');
    my $index = { '2025-12' => 1, '2026-01' => 1, '2026-02' => 1 };
    my $result = _determine_required_months($end, 15, $index);
    is_deeply($result, ['2026-01'],
        '2.11: 15-day window within January selects only January');
}

# Test 2.12: Window spanning year boundary
# 30-day window ending 2026-01-15 → starts 2025-12-17
{
    my $end = Time::Piece->strptime('2026-01-15', '%Y-%m-%d');
    my $index = { '2025-11' => 1, '2025-12' => 1, '2026-01' => 1 };
    my $result = _determine_required_months($end, 30, $index);
    is_deeply($result, ['2025-12', '2026-01'],
        '2.12: 30-day window spans year boundary correctly');
}

# Test 2.13: No history available — empty result
{
    my $end = Time::Piece->strptime('2026-03-15', '%Y-%m-%d');
    my $result = _determine_required_months($end, 90, {});
    is_deeply($result, [],
        '2.13: Empty history index returns empty list');
}

# Test 2.14: Window exactly aligns with month boundaries
# 31-day window ending 2026-01-31 → starts 2026-01-01
{
    my $end = Time::Piece->strptime('2026-01-31', '%Y-%m-%d');
    my $index = { '2025-12' => 1, '2026-01' => 1 };
    my $result = _determine_required_months($end, 31, $index);
    is_deeply($result, ['2026-01'],
        '2.14: 31-day window exactly fits January');
}

# Test 2.15: Window ending on first of month
# 90-day window ending 2026-03-01 → starts 2025-12-02
# 2025-12 month_end = Dec 31 ≥ Dec 02 (start), and month_start = Dec 01 ≤ Mar 01 (end) → included
{
    my $end = Time::Piece->strptime('2026-03-01', '%Y-%m-%d');
    my $index = { '2025-11' => 1, '2025-12' => 1, '2026-01' => 1,
                  '2026-02' => 1, '2026-03' => 1 };
    my $result = _determine_required_months($end, 90, $index);
    is_deeply($result, ['2025-12', '2026-01', '2026-02', '2026-03'],
        '2.15: Window ending on 1st of month includes partial first month');
}

# Test 2.16: Month wholly outside window excluded
# 60-day window ending 2026-03-15 → starts 2026-01-15
# 2025-11 ends Nov 30, before Jan 15 → excluded
{
    my $end = Time::Piece->strptime('2026-03-15', '%Y-%m-%d');
    my $index = { '2025-11' => 1, '2026-01' => 1, '2026-02' => 1, '2026-03' => 1 };
    my $result = _determine_required_months($end, 60, $index);
    is_deeply($result, ['2026-01', '2026-02', '2026-03'],
        '2.16: Month wholly before window correctly excluded');
}

# Test 2.17: Lookback = 1 day — only the anchor month
{
    my $end = Time::Piece->strptime('2026-02-15', '%Y-%m-%d');
    my $index = { '2026-01' => 1, '2026-02' => 1, '2026-03' => 1 };
    my $result = _determine_required_months($end, 1, $index);
    is_deeply($result, ['2026-02'],
        '2.17: 1-day lookback selects only anchor month');
}

# End of Phase 2a tests — plan count is authoritative.

# ---------------------------------------------------------------------------
# Inline copy of Phase 2b function for isolated testing.
# ---------------------------------------------------------------------------

sub _read_bounded_history_months {
    my ($system_cache_dir, $month_keys_aref) = @_;
    my %result = ( months => {}, loaded => [], missing => [], errors => {} );
    return \%result unless ($system_cache_dir && $month_keys_aref
                            && ref($month_keys_aref) eq 'ARRAY'
                            && @$month_keys_aref);
    my $partition_dir = File::Spec->catfile($system_cache_dir, '.nfit.history');
    unless (-d $partition_dir) {
        $result{missing} = [ sort @$month_keys_aref ];
        return \%result;
    }
    my $json_decoder = JSON->new->allow_nonref;
    foreach my $month_key (sort @$month_keys_aref) {
        unless ($month_key =~ /^\d{4}-\d{2}$/) {
            $result{errors}{$month_key} = "Invalid month key format: '$month_key'";
            next;
        }
        my $file = "nfit.hist.${month_key}.json";
        my $path = File::Spec->catfile($partition_dir, $file);
        unless (-f $path) { push @{$result{missing}}, $month_key; next; }
        unless (-s $path) {
            $result{errors}{$month_key} = "File exists but is empty: $file";
            next;
        }
        my $json_text = do {
            open my $fh, '<:encoding(utf8)', $path or do {
                $result{errors}{$month_key} = "Cannot open $file: $!";
                next;
            };
            local $/; <$fh>;
        };
        unless ($json_text && $json_text =~ /\S/) {
            $result{errors}{$month_key} = "File is blank or whitespace-only: $file";
            next;
        }
        my $chunk = eval { $json_decoder->decode($json_text) };
        if ($@ || !$chunk || ref($chunk) ne 'HASH') {
            my $err = $@ || 'Decoded value is not a hashref';
            $result{errors}{$month_key} = "JSON decode failed for $file: $err";
            next;
        }
        my $payload;
        if (exists $chunk->{$month_key}) {
            $payload = $chunk->{$month_key};
        } else {
            my ($found_key) = keys %$chunk;
            if ($found_key) { $payload = $chunk->{$found_key}; }
            else { $result{errors}{$month_key} = "Envelope is empty hash in $file"; next; }
        }
        $result{months}{$month_key} = $payload;
        push @{$result{loaded}}, $month_key;
    }
    return \%result;
}

# ---------------------------------------------------------------------------
# Helper: write a mock partition file with proper envelope structure
# ---------------------------------------------------------------------------
sub _write_mock_partition {
    my ($hist_dir, $month_key, $inner_data) = @_;
    my $path = File::Spec->catfile($hist_dir, "nfit.hist.${month_key}.json");
    my $envelope = { $month_key => $inner_data };
    open my $fh, '>:encoding(utf8)', $path or die "Cannot write $path: $!";
    print $fh JSON->new->encode($envelope);
    close $fh;
    return $path;
}

# ==============================================================================
#  TESTS: _read_bounded_history_months (Phase 2b)
# ==============================================================================

# Test 2.20: Successful load of two valid months
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);

    my $jan_data = {
        '_meta' => { ProcessedDays => 31, content_version => 1 },
        'MonthlyWorkloadAnalysis' => { 'VM01' => { 'cpu' => 0.5 } },
    };
    my $feb_data = {
        '_meta' => { ProcessedDays => 28, content_version => 2 },
        'MonthlyWorkloadAnalysis' => { 'VM01' => { 'cpu' => 0.6 } },
    };
    _write_mock_partition($hist_dir, '2026-01', $jan_data);
    _write_mock_partition($hist_dir, '2026-02', $feb_data);

    my $r = _read_bounded_history_months($tmpdir, ['2026-01', '2026-02']);

    is_deeply($r->{loaded}, ['2026-01', '2026-02'],
        '2.20a: Both months in loaded list');
    is_deeply($r->{missing}, [],
        '2.20b: Nothing missing');
    is(scalar keys %{$r->{errors}}, 0,
        '2.20c: No errors');
    is($r->{months}{'2026-01'}{_meta}{ProcessedDays}, 31,
        '2.20d: January payload correctly unwrapped');
    is($r->{months}{'2026-02'}{_meta}{content_version}, 2,
        '2.20e: February payload correctly unwrapped');
}

# Test 2.21: Partial load — one exists, one missing
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);

    _write_mock_partition($hist_dir, '2026-01', { '_meta' => { ProcessedDays => 31 } });

    my $r = _read_bounded_history_months($tmpdir, ['2026-01', '2026-02']);

    is_deeply($r->{loaded},  ['2026-01'],   '2.21a: Only existing month in loaded');
    is_deeply($r->{missing}, ['2026-02'],   '2.21b: Missing month correctly tracked');
}

# Test 2.22: Corrupt JSON file
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);

    my $path = File::Spec->catfile($hist_dir, 'nfit.hist.2026-01.json');
    open my $fh, '>', $path or die $!;
    print $fh '{ this is not valid JSON }}}';
    close $fh;

    my $r = _read_bounded_history_months($tmpdir, ['2026-01']);

    is_deeply($r->{loaded}, [],             '2.22a: Corrupt file not in loaded');
    ok(exists $r->{errors}{'2026-01'},      '2.22b: Corrupt file recorded in errors');
    like($r->{errors}{'2026-01'}, qr/JSON decode failed/,
        '2.22c: Error message mentions JSON decode');
}

# Test 2.23: Empty file
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);

    my $path = File::Spec->catfile($hist_dir, 'nfit.hist.2026-03.json');
    open my $fh, '>', $path or die $!;
    close $fh;  # 0 bytes

    my $r = _read_bounded_history_months($tmpdir, ['2026-03']);

    is_deeply($r->{loaded}, [],             '2.23a: Empty file not in loaded');
    ok(exists $r->{errors}{'2026-03'},      '2.23b: Empty file recorded in errors');
}

# Test 2.24: Empty month_keys — returns empty result
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $r = _read_bounded_history_months($tmpdir, []);

    is_deeply($r->{months},  {},  '2.24a: Empty keys → empty months');
    is_deeply($r->{loaded},  [],  '2.24b: Empty keys → empty loaded');
}

# Test 2.25: No .nfit.history directory — all months flagged missing
{
    my $tmpdir = tempdir(CLEANUP => 1);
    # Deliberately do NOT create .nfit.history subdir

    my $r = _read_bounded_history_months($tmpdir, ['2026-01', '2026-02']);

    is_deeply($r->{loaded},  [],                     '2.25a: Nothing loaded');
    is_deeply($r->{missing}, ['2026-01', '2026-02'], '2.25b: All months in missing');
}
