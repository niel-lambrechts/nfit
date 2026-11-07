#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/stitcher_exclusions.t
# PURPOSE:   Unit tests for Phase 3 — Daily Series Stitcher and Exclusions:
#            _expand_peak_periods_to_dates, _stitch_daily_series_bounded,
#            _apply_exclusions_to_stitched_series.
#
# USAGE:     perl t/stitcher_exclusions.t
#            prove t/stitcher_exclusions.t
#
# NOTE:      Phase 3a covers _expand_peak_periods_to_dates and
#            _stitch_daily_series_bounded.
#            Phase 3b will add _apply_exclusions_to_stitched_series tests.
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 75;
use Time::Piece;
use Time::Seconds;
use File::Temp qw(tempdir);
use File::Spec;
use File::Path qw(make_path);
use JSON;

# ---------------------------------------------------------------------------
# Inline copies of Phase 2 dependencies (needed by the stitcher).
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
            $result{errors}{$month_key} = "File is blank: $file";
            next;
        }
        my $chunk = eval { $json_decoder->decode($json_text) };
        if ($@ || !$chunk || ref($chunk) ne 'HASH') {
            my $err = $@ || 'Not a hashref';
            $result{errors}{$month_key} = "JSON decode failed for $file: $err";
            next;
        }
        my $payload;
        if (exists $chunk->{$month_key}) { $payload = $chunk->{$month_key}; }
        else {
            my ($found_key) = keys %$chunk;
            if ($found_key) { $payload = $chunk->{$found_key}; }
            else { $result{errors}{$month_key} = "Empty envelope in $file"; next; }
        }
        $result{months}{$month_key} = $payload;
        push @{$result{loaded}}, $month_key;
    }
    return \%result;
}

# Inline copy of existing _expand_date_range for isolated testing
sub _expand_date_range {
    my ($start_str, $end_str) = @_;
    my @dates;
    eval {
        my $current = Time::Piece->strptime($start_str, '%Y-%m-%d');
        my $end     = Time::Piece->strptime($end_str, '%Y-%m-%d');
        if ($end < $current) { ($current, $end) = ($end, $current); }
        my $max_days = 366;
        my $day_count = 0;
        while ($current <= $end && $day_count < $max_days) {
            push @dates, $current->strftime('%Y-%m-%d');
            $current += ONE_DAY;
            $day_count++;
        }
    };
    return @dates;
}

# ---------------------------------------------------------------------------
# Inline copies of Phase 3a functions under test.
# ---------------------------------------------------------------------------

sub _expand_peak_periods_to_dates {
    my ($peak_periods_aref) = @_;
    my %date_set;
    return \%date_set unless ($peak_periods_aref && ref($peak_periods_aref) eq 'ARRAY'
                              && @$peak_periods_aref);
    foreach my $pair (@$peak_periods_aref) {
        next unless (ref($pair) eq 'ARRAY' && scalar(@$pair) >= 2);
        my ($start_obj, $end_obj) = @$pair;
        my $start_str = ref($start_obj) ? $start_obj->strftime('%Y-%m-%d') : $start_obj;
        my $end_str   = ref($end_obj)   ? $end_obj->strftime('%Y-%m-%d')   : $end_obj;
        my @expanded = _expand_date_range($start_str, $end_str);
        $date_set{$_} = 1 for @expanded;
    }
    return \%date_set;
}

sub _stitch_daily_series_bounded {
    my ($args_href) = @_;
    my %result = (
        series => {}, months_read => [], months_missing => [],
        span_start => undef, span_end => undef, content_versions => {},
    );
    return \%result unless ($args_href && ref($args_href) eq 'HASH');
    my $system_cache_dir = $args_href->{system_cache_dir};
    my $end_date_obj     = $args_href->{end_date_obj};
    return \%result unless ($system_cache_dir && $end_date_obj);

    my $lookback_days = $args_href->{lookback_days} // 90;
    my $HARD_CAP = 120;
    if ($lookback_days > $HARD_CAP) { $lookback_days = $HARD_CAP; }
    $lookback_days = 1 if $lookback_days < 1;

    my %vm_filter;
    if ($args_href->{vms_needed} && ref($args_href->{vms_needed}) eq 'ARRAY'
        && @{$args_href->{vms_needed}}) {
        %vm_filter = map { $_ => 1 } @{$args_href->{vms_needed}};
    }
    my %profile_filter;
    if ($args_href->{profiles_needed} && ref($args_href->{profiles_needed}) eq 'ARRAY'
        && @{$args_href->{profiles_needed}}) {
        %profile_filter = map { $_ => 1 } @{$args_href->{profiles_needed}};
    }

    my $index = _get_history_month_index($system_cache_dir);
    my $required = _determine_required_months($end_date_obj, $lookback_days, $index);
    return \%result if (!@$required);

    my $load_result = _read_bounded_history_months($system_cache_dir, $required);
    $result{months_read}    = $load_result->{loaded};
    $result{months_missing} = $load_result->{missing};

    my $window_start_str = ($end_date_obj - (($lookback_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $window_end_str   = $end_date_obj->strftime('%Y-%m-%d');

    my ($global_min_date, $global_max_date);

    foreach my $month_key (@{$load_result->{loaded}}) {
        my $month_data = $load_result->{months}{$month_key};
        next unless (ref($month_data) eq 'HASH');

        $result{content_versions}{$month_key} =
            (exists $month_data->{_meta} && ref($month_data->{_meta}) eq 'HASH')
                ? ($month_data->{_meta}{content_version} // 0)
                : 0;

        my $mwa = $month_data->{MonthlyWorkloadAnalysis};
        next unless (ref($mwa) eq 'HASH');

        foreach my $vm (keys %$mwa) {
            next if (%vm_filter && !$vm_filter{$vm});
            my $dps = $mwa->{$vm}{DailyProfileSeries};
            next unless (ref($dps) eq 'HASH');

            foreach my $profile (keys %$dps) {
                next if (%profile_filter && !$profile_filter{$profile});
                my $date_values = $dps->{$profile};
                next unless (ref($date_values) eq 'HASH');

                foreach my $date (keys %$date_values) {
                    next unless ($date =~ /^\d{4}-\d{2}-\d{2}$/);
                    next if ($date lt $window_start_str);
                    next if ($date gt $window_end_str);

                    $result{series}{$vm}{$profile}{$date} = $date_values->{$date};

                    if (!defined $global_min_date || $date lt $global_min_date) {
                        $global_min_date = $date;
                    }
                    if (!defined $global_max_date || $date gt $global_max_date) {
                        $global_max_date = $date;
                    }
                }
            }
        }
    }

    $result{span_start} = $global_min_date;
    $result{span_end}   = $global_max_date;
    return \%result;
}

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

# Write a mock partition file with proper envelope and DailyProfileSeries
sub _write_mock_partition {
    my ($hist_dir, $month_key, $inner_data) = @_;
    my $path = File::Spec->catfile($hist_dir, "nfit.hist.${month_key}.json");
    my $envelope = { $month_key => $inner_data };
    open my $fh, '>:encoding(utf8)', $path or die "Cannot write $path: $!";
    print $fh JSON->new->encode($envelope);
    close $fh;
}

# Build a standard mock month payload with DailyProfileSeries
sub _build_month_payload {
    my (%args) = @_;
    # args: content_version, vms => { VM => { Profile => { date => val } } }
    my $payload = {
        MonthlyWorkloadAnalysis => {},
    };
    if (defined $args{content_version}) {
        $payload->{_meta} = { content_version => $args{content_version} };
    }
    foreach my $vm (keys %{$args{vms} || {}}) {
        $payload->{MonthlyWorkloadAnalysis}{$vm} = {
            DailyProfileSeries => $args{vms}{$vm},
        };
    }
    return $payload;
}

# Set up a temp dir with .nfit.history and write partitions
sub _setup_test_history {
    my (%months_data) = @_;
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);
    foreach my $mk (keys %months_data) {
        _write_mock_partition($hist_dir, $mk, $months_data{$mk});
    }
    return $tmpdir;
}


# ==============================================================================
#  TESTS: _expand_peak_periods_to_dates
# ==============================================================================

# Test 3.12: Multi-day expansion with Time::Piece objects
{
    my $start = Time::Piece->strptime('2026-01-28', '%Y-%m-%d');
    my $end   = Time::Piece->strptime('2026-01-31', '%Y-%m-%d');
    my $result = _expand_peak_periods_to_dates([ [$start, $end] ]);
    is_deeply([ sort keys %$result ],
        ['2026-01-28', '2026-01-29', '2026-01-30', '2026-01-31'],
        '3.12: 4-day peak period expanded correctly');
}

# Test 3.12b: Multiple non-overlapping periods
{
    my $s1 = Time::Piece->strptime('2026-01-05', '%Y-%m-%d');
    my $e1 = Time::Piece->strptime('2026-01-06', '%Y-%m-%d');
    my $s2 = Time::Piece->strptime('2026-02-10', '%Y-%m-%d');
    my $e2 = Time::Piece->strptime('2026-02-11', '%Y-%m-%d');
    my $result = _expand_peak_periods_to_dates([ [$s1, $e1], [$s2, $e2] ]);
    is(scalar keys %$result, 4, '3.12b: Two 2-day periods yield 4 dates');
    ok($result->{'2026-01-05'} && $result->{'2026-02-11'},
       '3.12b: First and last dates present');
}

# Test 3.12c: Overlapping periods — deduplicated
{
    my $s1 = Time::Piece->strptime('2026-01-10', '%Y-%m-%d');
    my $e1 = Time::Piece->strptime('2026-01-15', '%Y-%m-%d');
    my $s2 = Time::Piece->strptime('2026-01-13', '%Y-%m-%d');
    my $e2 = Time::Piece->strptime('2026-01-17', '%Y-%m-%d');
    my $result = _expand_peak_periods_to_dates([ [$s1, $e1], [$s2, $e2] ]);
    is(scalar keys %$result, 8, '3.12c: Overlapping periods produce 8 unique dates (10-17)');
}

# Test 3.12d: Empty/undef input
{
    is_deeply(_expand_peak_periods_to_dates(undef), {},
        '3.12d: undef returns empty hash');
    is_deeply(_expand_peak_periods_to_dates([]), {},
        '3.12d: empty array returns empty hash');
}

# Test 3.12e: Single-day peak period
{
    my $d = Time::Piece->strptime('2026-03-01', '%Y-%m-%d');
    my $result = _expand_peak_periods_to_dates([ [$d, $d] ]);
    is_deeply([ keys %$result ], ['2026-03-01'],
        '3.12e: Single-day period yields single date');
}

# Test 3.12f: Accepts plain string dates (defensive)
{
    my $result = _expand_peak_periods_to_dates([ ['2026-04-01', '2026-04-03'] ]);
    is(scalar keys %$result, 3, '3.12f: String date pairs also accepted');
}


# ==============================================================================
#  TESTS: _stitch_daily_series_bounded
# ==============================================================================

# Test 3.1: Basic stitching from two months
{
    my $jan = _build_month_payload(
        content_version => 1,
        vms => {
            'VM01' => {
                'G3-95W15' => {
                    '2026-01-10' => 1.5,
                    '2026-01-20' => 2.0,
                    '2026-01-31' => 2.5,
                },
            },
        },
    );
    my $feb = _build_month_payload(
        content_version => 2,
        vms => {
            'VM01' => {
                'G3-95W15' => {
                    '2026-02-05' => 3.0,
                    '2026-02-15' => 3.5,
                    '2026-02-28' => 4.0,
                },
            },
        },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan, '2026-02' => $feb);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-02-28', '%Y-%m-%d'),
        lookback_days    => 90,
    });

    is(scalar keys %{$r->{series}{VM01}{'G3-95W15'}}, 6,
        '3.1a: All 6 dates from both months stitched');
    is($r->{series}{VM01}{'G3-95W15'}{'2026-01-10'}, 1.5,
        '3.1b: January value correct');
    is($r->{series}{VM01}{'G3-95W15'}{'2026-02-28'}, 4.0,
        '3.1c: February value correct');
    is_deeply($r->{months_read}, ['2026-01', '2026-02'],
        '3.1d: Both months in months_read');
    is($r->{span_start}, '2026-01-10', '3.1e: span_start is earliest date');
    is($r->{span_end},   '2026-02-28', '3.1f: span_end is latest date');
}

# Test 3.2: Hard cap enforcement (>120 clamped)
{
    # Only need Jan and Feb — with lookback=200, it should be clamped to 120
    # 120-day window ending 2026-02-28 starts 2025-11-01
    # So only Jan + Feb (which we have) will be in window
    my $jan = _build_month_payload(
        vms => { 'VM01' => { 'P1' => { '2026-01-15' => 1.0 } } },
    );
    my $feb = _build_month_payload(
        vms => { 'VM01' => { 'P1' => { '2026-02-15' => 2.0 } } },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan, '2026-02' => $feb);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-02-28', '%Y-%m-%d'),
        lookback_days    => 200,   # Should be silently capped to 120
    });

    # It should still find dates (capped to 120, which covers Jan-Feb)
    ok(exists $r->{series}{VM01}{P1}{'2026-01-15'},
        '3.2: Dates still found despite capped lookback');
}

# Test 3.3: Profile filtering — only requested profile
{
    my $jan = _build_month_payload(
        vms => {
            'VM01' => {
                'G3-95W15' => { '2026-01-15' => 1.0 },
                'P-99W1'   => { '2026-01-15' => 9.0 },
            },
        },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        lookback_days    => 31,
        profiles_needed  => ['G3-95W15'],
    });

    ok(exists $r->{series}{VM01}{'G3-95W15'},
        '3.3a: Requested profile present');
    ok(!exists $r->{series}{VM01}{'P-99W1'},
        '3.3b: Non-requested profile excluded');
}

# Test 3.4: VM filtering — only requested VMs
{
    my $jan = _build_month_payload(
        vms => {
            'VM01' => { 'P1' => { '2026-01-15' => 1.0 } },
            'VM02' => { 'P1' => { '2026-01-15' => 2.0 } },
            'VM03' => { 'P1' => { '2026-01-15' => 3.0 } },
        },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        lookback_days    => 31,
        vms_needed       => ['VM01'],
    });

    ok(exists $r->{series}{VM01}, '3.4a: Requested VM present');
    ok(!exists $r->{series}{VM02}, '3.4b: VM02 excluded');
    ok(!exists $r->{series}{VM03}, '3.4c: VM03 excluded');
}

# Test 3.5: Content versions recorded
{
    my $jan = _build_month_payload(
        content_version => 3,
        vms => { 'VM01' => { 'P1' => { '2026-01-15' => 1.0 } } },
    );
    my $feb = _build_month_payload(
        content_version => 1,
        vms => { 'VM01' => { 'P1' => { '2026-02-15' => 2.0 } } },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan, '2026-02' => $feb);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-02-28', '%Y-%m-%d'),
        lookback_days    => 90,
    });

    is($r->{content_versions}{'2026-01'}, 3, '3.5a: Jan content_version = 3');
    is($r->{content_versions}{'2026-02'}, 1, '3.5b: Feb content_version = 1');
}

# Test 3.5c: Legacy month without _meta defaults to version 0
{
    my $jan = {
        MonthlyWorkloadAnalysis => {
            'VM01' => {
                DailyProfileSeries => { 'P1' => { '2026-01-15' => 1.0 } },
            },
        },
        Metadata => { ProcessedDays => 31 },
        # NOTE: No _meta block — legacy data predating Phase 1
    };
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        lookback_days    => 31,
    });

    is($r->{content_versions}{'2026-01'}, 0,
        '3.5c: Legacy month without _meta defaults to content_version 0');
}

# Test 3.6: Window clipping — dates outside window excluded
{
    my $jan = _build_month_payload(
        vms => {
            'VM01' => {
                'P1' => {
                    '2026-01-01' => 1.0,  # Before window
                    '2026-01-10' => 2.0,  # In window
                    '2026-01-20' => 3.0,  # In window
                    '2026-01-31' => 4.0,  # In window (end date)
                },
            },
        },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    # 22-day window ending 2026-01-31 starts 2026-01-10
    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        lookback_days    => 22,
    });

    ok(!exists $r->{series}{VM01}{P1}{'2026-01-01'},
        '3.6a: Date before window excluded');
    ok(exists $r->{series}{VM01}{P1}{'2026-01-10'},
        '3.6b: Date at window start included');
    ok(exists $r->{series}{VM01}{P1}{'2026-01-31'},
        '3.6c: Date at window end included');
    is(scalar keys %{$r->{series}{VM01}{P1}}, 3,
        '3.6d: Exactly 3 dates in window');
}

# Test 3.7: VM in one month but not another — both included
{
    my $jan = _build_month_payload(
        vms => {
            'VM01' => { 'P1' => { '2026-01-15' => 1.0 } },
        },
    );
    my $feb = _build_month_payload(
        vms => {
            'VM01' => { 'P1' => { '2026-02-15' => 2.0 } },
            'VM02' => { 'P1' => { '2026-02-15' => 3.0 } },  # Only in Feb
        },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan, '2026-02' => $feb);

    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-02-28', '%Y-%m-%d'),
        lookback_days    => 90,
    });

    ok(exists $r->{series}{VM01} && exists $r->{series}{VM02},
        '3.7: VM01 from both months and VM02 from Feb both present');
}

# Test 3.8: No history — empty result
{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        lookback_days    => 90,
    });

    is(scalar keys %{$r->{series}}, 0, '3.8a: No history yields empty series');
    is($r->{span_start}, undef,        '3.8b: span_start is undef');
}

# Test 3.9: months_missing tracked when history is sparse
{
    # Only create January, but window covers Dec-Feb
    my $jan = _build_month_payload(
        vms => { 'VM01' => { 'P1' => { '2026-01-15' => 1.0 } } },
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    # 90-day window ending 2026-02-28 needs Dec, Jan, Feb
    # But only Jan exists — Dec and Feb should appear in months_missing
    # Actually _determine_required_months only returns months that exist in the index
    # So months_missing would come from _read_bounded_history_months if the file
    # disappeared between index scan and read. In normal operation, if the file
    # doesn't exist in the index, it simply isn't requested.
    my $r = _stitch_daily_series_bounded({
        system_cache_dir => $tmpdir,
        end_date_obj     => Time::Piece->strptime('2026-02-28', '%Y-%m-%d'),
        lookback_days    => 90,
    });

    # Only January was in the index, so only January was requested and loaded
    is_deeply($r->{months_read}, ['2026-01'],
        '3.9: Only available month loaded — no spurious missing entries');
}

# End of Phase 3a tests.  Phase 3b exclusion tests follow.

# ---------------------------------------------------------------------------
# Inline copy of Phase 3b function under test.
# ---------------------------------------------------------------------------

sub _apply_exclusions_to_stitched_series {
    my ($args_href) = @_;
    my %stats = (
        vms_included => 0, vms_excluded => 0,
        dates_excluded => { global => 0, vm_specific => 0, peak_periods => 0 },
    );
    my $series = ($args_href && ref($args_href) eq 'HASH')
               ? $args_href->{stitched_series} : undef;
    unless ($series && ref($series) eq 'HASH' && %$series) {
        return { filtered_series => ($series // {}), exclusion_stats => \%stats };
    }

    my $vm_scope  = $args_href->{vm_scope_filter};
    my $date_excl = $args_href->{date_exclusions};
    my $peak_aref = $args_href->{peak_periods};

    # STEP 1: VM Inclusions
    my $include_filter = ($vm_scope && ref($vm_scope) eq 'HASH')
                       ? $vm_scope->{include} : undef;
    if ($include_filter && ref($include_filter) eq 'HASH' && %$include_filter) {
        my @to_remove;
        foreach my $vm (keys %$series) {
            push @to_remove, $vm unless $include_filter->{$vm};
        }
        delete $series->{$_} for @to_remove;
        $stats{vms_excluded} += scalar @to_remove;
        $stats{vms_included} = scalar keys %$series;
    }

    # STEP 2: VM Exclusions
    my $exclude_filter = ($vm_scope && ref($vm_scope) eq 'HASH')
                       ? $vm_scope->{exclude} : undef;
    if ($exclude_filter && ref($exclude_filter) eq 'HASH' && %$exclude_filter) {
        my $removed = 0;
        foreach my $vm (keys %$series) {
            if ($exclude_filter->{$vm}) { delete $series->{$vm}; $removed++; }
        }
        $stats{vms_excluded} += $removed;
    }

    unless (%$series) {
        return { filtered_series => $series, exclusion_stats => \%stats };
    }

    # Build date lookup hashes
    my %global_dates;
    if ($date_excl && ref($date_excl) eq 'HASH'
        && $date_excl->{global} && ref($date_excl->{global}) eq 'ARRAY') {
        %global_dates = map { $_ => 1 } @{$date_excl->{global}};
    }
    my %vm_specific_dates;
    if ($date_excl && ref($date_excl) eq 'HASH'
        && $date_excl->{vm_specific} && ref($date_excl->{vm_specific}) eq 'HASH') {
        foreach my $vm (keys %{$date_excl->{vm_specific}}) {
            my $d = $date_excl->{vm_specific}{$vm};
            $vm_specific_dates{$vm} = { map { $_ => 1 } @$d } if ref($d) eq 'ARRAY';
        }
    }
    my $peak_dates_href = _expand_peak_periods_to_dates($peak_aref);

    unless (%global_dates || %vm_specific_dates || %$peak_dates_href) {
        return { filtered_series => $series, exclusion_stats => \%stats };
    }

    # STEPS 3-5: Date exclusions with elsif priority
    foreach my $vm (keys %$series) {
        my $vm_profiles = $series->{$vm};
        next unless (ref($vm_profiles) eq 'HASH');
        my $this_vm_excl = $vm_specific_dates{$vm};

        foreach my $profile (keys %$vm_profiles) {
            my $date_values = $vm_profiles->{$profile};
            next unless (ref($date_values) eq 'HASH');
            my @dates_to_delete;
            foreach my $date (keys %$date_values) {
                if ($global_dates{$date}) {
                    push @dates_to_delete, $date;
                    $stats{dates_excluded}{global}++;
                }
                elsif ($this_vm_excl && $this_vm_excl->{$date}) {
                    push @dates_to_delete, $date;
                    $stats{dates_excluded}{vm_specific}++;
                }
                elsif ($peak_dates_href->{$date}) {
                    push @dates_to_delete, $date;
                    $stats{dates_excluded}{peak_periods}++;
                }
            }
            delete $date_values->{$_} for @dates_to_delete;
        }
    }

    return { filtered_series => $series, exclusion_stats => \%stats };
}

# ---------------------------------------------------------------------------
# Standard test series builder for exclusion tests.
# Returns a fresh deep copy each time (so mutations don't cross tests).
# ---------------------------------------------------------------------------
sub _make_test_series {
    return {
        'VM01' => {
            'G3-95W15' => {
                '2026-01-10' => 1.0,
                '2026-01-15' => 1.5,
                '2026-01-20' => 2.0,
                '2026-01-28' => 2.5,
                '2026-01-29' => 2.8,
                '2026-01-30' => 3.0,
                '2026-01-31' => 3.2,
            },
        },
        'VM02' => {
            'G3-95W15' => {
                '2026-01-10' => 4.0,
                '2026-01-15' => 4.5,
                '2026-01-20' => 5.0,
                '2026-01-28' => 5.5,
            },
        },
        'VM03' => {
            'G3-95W15' => {
                '2026-01-10' => 6.0,
                '2026-01-15' => 6.5,
            },
        },
    };
}

# ==============================================================================
#  TESTS: _apply_exclusions_to_stitched_series (Phase 3b)
# ==============================================================================

# Test 3.20: No exclusions — series unchanged
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
    });

    is(scalar keys %{$r->{filtered_series}}, 3,
        '3.20a: No exclusions — all 3 VMs remain');
    is($r->{exclusion_stats}{vms_excluded}, 0,
        '3.20b: No VMs excluded');
    is($r->{exclusion_stats}{dates_excluded}{global}, 0,
        '3.20c: No dates excluded');
}

# Test 3.21: VM include filter — keep only VM01
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        vm_scope_filter => { include => { 'VM01' => 1 } },
    });

    ok(exists $r->{filtered_series}{'VM01'},
        '3.21a: VM01 remains');
    ok(!exists $r->{filtered_series}{'VM02'},
        '3.21b: VM02 removed by include filter');
    ok(!exists $r->{filtered_series}{'VM03'},
        '3.21c: VM03 removed by include filter');
    is($r->{exclusion_stats}{vms_excluded}, 2,
        '3.21d: 2 VMs excluded');
    is($r->{exclusion_stats}{vms_included}, 1,
        '3.21e: 1 VM included');
}

# Test 3.22: VM exclude filter — remove VM03 only
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        vm_scope_filter => { exclude => { 'VM03' => 1 } },
    });

    ok(exists $r->{filtered_series}{'VM01'} && exists $r->{filtered_series}{'VM02'},
        '3.22a: VM01 and VM02 remain');
    ok(!exists $r->{filtered_series}{'VM03'},
        '3.22b: VM03 removed');
    is($r->{exclusion_stats}{vms_excluded}, 1,
        '3.22c: 1 VM excluded');
}

# Test 3.23: Processing order — include then exclude
# include = { VM01, VM02 }, exclude = { VM02 }
# After include: VM01, VM02.  After exclude: VM01 only.
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        vm_scope_filter => {
            include => { 'VM01' => 1, 'VM02' => 1 },
            exclude => { 'VM02' => 1 },
        },
    });

    my @remaining = sort keys %{$r->{filtered_series}};
    is_deeply(\@remaining, ['VM01'],
        '3.23a: Only VM01 remains (include then exclude)');
    # vms_excluded: 1 from include (VM03) + 1 from exclude (VM02) = 2
    is($r->{exclusion_stats}{vms_excluded}, 2,
        '3.23b: 2 total VMs excluded across both stages');
}

# Test 3.24: Global date exclusion — removes dates from ALL VMs/profiles
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        date_exclusions => {
            global      => ['2026-01-15'],
            vm_specific => {},
        },
    });

    # 2026-01-15 should be gone from VM01, VM02, VM03 (all have G3-95W15 with that date)
    ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-15'},
        '3.24a: Global date removed from VM01');
    ok(!exists $r->{filtered_series}{'VM02'}{'G3-95W15'}{'2026-01-15'},
        '3.24b: Global date removed from VM02');
    ok(!exists $r->{filtered_series}{'VM03'}{'G3-95W15'}{'2026-01-15'},
        '3.24c: Global date removed from VM03');
    # Other dates in VM01 still present
    ok(exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-10'},
        '3.24d: Non-excluded date unaffected');
    # Stats: 3 date-points removed (one per VM)
    is($r->{exclusion_stats}{dates_excluded}{global}, 3,
        '3.24e: 3 global date-point removals counted');
}

# Test 3.25: VM-specific date exclusion — removes from that VM only
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        date_exclusions => {
            global      => [],
            vm_specific => { 'VM01' => ['2026-01-20'] },
        },
    });

    ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-20'},
        '3.25a: VM-specific date removed from VM01');
    ok(exists $r->{filtered_series}{'VM02'}{'G3-95W15'}{'2026-01-20'},
        '3.25b: Same date NOT removed from VM02');
    is($r->{exclusion_stats}{dates_excluded}{vm_specific}, 1,
        '3.25c: 1 VM-specific date-point removal');
}

# Test 3.26: Peak period exclusion — removes dates within peak range
{
    my $series = _make_test_series();
    my $s = Time::Piece->strptime('2026-01-28', '%Y-%m-%d');
    my $e = Time::Piece->strptime('2026-01-31', '%Y-%m-%d');

    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        peak_periods    => [ [$s, $e] ],
    });

    # VM01 had dates 28,29,30,31 — all should be gone
    for my $d ('2026-01-28', '2026-01-29', '2026-01-30', '2026-01-31') {
        ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{$d},
            "3.26: Peak date $d removed from VM01");
    }
    # VM02 had only 2026-01-28 in peak range
    ok(!exists $r->{filtered_series}{'VM02'}{'G3-95W15'}{'2026-01-28'},
        '3.26: Peak date 2026-01-28 removed from VM02');
    # Non-peak dates preserved
    ok(exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-10'},
        '3.26: Non-peak date preserved');
    # Stats: VM01 had 4 peak dates, VM02 had 1 = 5 total
    is($r->{exclusion_stats}{dates_excluded}{peak_periods}, 5,
        '3.26: 5 peak period date-point removals');
}

# Test 3.27: Priority — global wins over vm_specific and peak
# Date 2026-01-15 is in both global AND vm_specific for VM01
# It should be counted as global, not vm_specific
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        date_exclusions => {
            global      => ['2026-01-15'],
            vm_specific => { 'VM01' => ['2026-01-15'] },
        },
    });

    # Date removed from VM01 — counted as global (since global has priority)
    ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-15'},
        '3.27a: Overlapping date removed from VM01');
    # All 3 VMs had that date, all counted as global
    is($r->{exclusion_stats}{dates_excluded}{global}, 3,
        '3.27b: All 3 removals attributed to global (priority)');
    is($r->{exclusion_stats}{dates_excluded}{vm_specific}, 0,
        '3.27c: VM-specific count is 0 (global took priority)');
}

# Test 3.28: Combined — all exclusion types active simultaneously
{
    my $series = _make_test_series();
    my $s = Time::Piece->strptime('2026-01-28', '%Y-%m-%d');
    my $e = Time::Piece->strptime('2026-01-29', '%Y-%m-%d');

    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        vm_scope_filter => {
            include => { 'VM01' => 1, 'VM02' => 1 },  # VM03 excluded
        },
        date_exclusions => {
            global      => ['2026-01-10'],              # Removed from all
            vm_specific => { 'VM01' => ['2026-01-20'] },
        },
        peak_periods    => [ [$s, $e] ],                # 28-29 removed
    });

    # VM03 gone (include filter)
    ok(!exists $r->{filtered_series}{'VM03'},
        '3.28a: VM03 excluded by include filter');
    # Global date gone from both remaining VMs
    ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-10'},
        '3.28b: Global date removed from VM01');
    ok(!exists $r->{filtered_series}{'VM02'}{'G3-95W15'}{'2026-01-10'},
        '3.28c: Global date removed from VM02');
    # VM-specific date
    ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-20'},
        '3.28d: VM-specific date removed from VM01');
    ok(exists $r->{filtered_series}{'VM02'}{'G3-95W15'}{'2026-01-20'},
        '3.28e: VM-specific date NOT removed from VM02');
    # Peak dates
    ok(!exists $r->{filtered_series}{'VM01'}{'G3-95W15'}{'2026-01-28'},
        '3.28f: Peak date removed from VM01');

    # Stats check: global=2 (VM01+VM02 × Jan-10), vm_specific=1 (VM01 × Jan-20),
    # peak: VM01 had 28,29 = 2, VM02 had 28 = 1 → 3 peak removals
    is($r->{exclusion_stats}{dates_excluded}{global}, 2,
        '3.28g: 2 global removals');
    is($r->{exclusion_stats}{dates_excluded}{vm_specific}, 1,
        '3.28h: 1 VM-specific removal');
    is($r->{exclusion_stats}{dates_excluded}{peak_periods}, 3,
        '3.28i: 3 peak removals');
}

# Test 3.29: All VMs filtered out — returns empty series, no crash
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        vm_scope_filter => { include => { 'NONEXISTENT' => 1 } },
    });

    is(scalar keys %{$r->{filtered_series}}, 0,
        '3.29a: All VMs removed — empty series');
    is($r->{exclusion_stats}{vms_excluded}, 3,
        '3.29b: 3 VMs excluded');
    # No date exclusions attempted (no VMs to process)
    is($r->{exclusion_stats}{dates_excluded}{global}, 0,
        '3.29c: No date exclusions on empty VM set');
}

# Test 3.30: undef/empty exclusion inputs — series passes through unchanged
{
    my $series = _make_test_series();
    my $r = _apply_exclusions_to_stitched_series({
        stitched_series => $series,
        vm_scope_filter => undef,
        date_exclusions => undef,
        peak_periods    => undef,
    });

    is(scalar keys %{$r->{filtered_series}}, 3,
        '3.30: All undef exclusions — series unchanged');
}
