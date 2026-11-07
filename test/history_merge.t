#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/history_merge.t
# PURPOSE:   Unit tests for Phase 1 — History Based Baselining date-set merge
#            primitives: _extract_date_set_from_series, _needs_history_update,
#            _merge_daily_profile_series, _update_month_metadata.
#
# USAGE:     perl t/history_merge.t
#            prove t/history_merge.t
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 35;

# ---------------------------------------------------------------------------
# Inline copies of Phase 1 functions for isolated testing.
# In production these live in nfit-profile.pl.  We replicate them here to
# allow standalone execution without loading the full 14k-line script.
# ---------------------------------------------------------------------------
use Time::Piece;

sub _extract_date_set_from_series {
    my ($daily_series_href) = @_;
    my %date_set;
    return \%date_set unless ($daily_series_href && ref($daily_series_href) eq 'HASH');
    foreach my $vm (keys %$daily_series_href) {
        unless (ref($daily_series_href->{$vm}) eq 'HASH') { next; }
        foreach my $profile (keys %{$daily_series_href->{$vm}}) {
            unless (ref($daily_series_href->{$vm}{$profile}) eq 'HASH') { next; }
            foreach my $date (keys %{$daily_series_href->{$vm}{$profile}}) {
                next unless $date =~ /^\d{4}-\d{2}-\d{2}$/;
                $date_set{$date} = 1;
            }
        }
    }
    return \%date_set;
}

sub _needs_history_update {
    my ($existing_date_set_href, $l1_date_set_href) = @_;
    unless ($l1_date_set_href && ref($l1_date_set_href) eq 'HASH' && %$l1_date_set_href) {
        return { needs_update => 0, reason => 'empty_l1', new_dates => [] };
    }
    if (!$existing_date_set_href || ref($existing_date_set_href) ne 'HASH' || !%$existing_date_set_href) {
        return { needs_update => 1, reason => 'new_month', new_dates => [ sort keys %$l1_date_set_href ] };
    }
    my @new_dates;
    foreach my $date (keys %$l1_date_set_href) {
        push @new_dates, $date unless exists $existing_date_set_href->{$date};
    }
    if (@new_dates) {
        return { needs_update => 1, reason => 'has_new_dates', new_dates => [ sort @new_dates ] };
    }
    return { needs_update => 0, reason => 'no_change', new_dates => [] };
}

sub _merge_daily_profile_series {
    my ($existing_series_href, $l1_series_href) = @_;
    my %dates_added_set;
    my %dates_skipped_set;
    my %merged;
    if ($existing_series_href && ref($existing_series_href) eq 'HASH') {
        foreach my $vm (keys %$existing_series_href) {
            next unless ref($existing_series_href->{$vm}) eq 'HASH';
            foreach my $profile (keys %{$existing_series_href->{$vm}}) {
                next unless ref($existing_series_href->{$vm}{$profile}) eq 'HASH';
                $merged{$vm}{$profile} = { %{$existing_series_href->{$vm}{$profile}} };
            }
        }
    }
    if ($l1_series_href && ref($l1_series_href) eq 'HASH') {
        foreach my $vm (keys %$l1_series_href) {
            next unless ref($l1_series_href->{$vm}) eq 'HASH';
            foreach my $profile (keys %{$l1_series_href->{$vm}}) {
                next unless ref($l1_series_href->{$vm}{$profile}) eq 'HASH';
                $merged{$vm}{$profile} //= {};
                foreach my $date (keys %{$l1_series_href->{$vm}{$profile}}) {
                    next unless $date =~ /^\d{4}-\d{2}-\d{2}$/;
                    if (exists $merged{$vm}{$profile}{$date}) {
                        $dates_skipped_set{$date} = 1;
                    } else {
                        $merged{$vm}{$profile}{$date} = $l1_series_href->{$vm}{$profile}{$date};
                        $dates_added_set{$date} = 1;
                    }
                }
            }
        }
    }
    my $merged_date_set = _extract_date_set_from_series(\%merged);
    my @all_dates = sort keys %$merged_date_set;
    return {
        merged_series => \%merged,
        dates_added   => [ sort keys %dates_added_set  ],
        dates_skipped => [ sort keys %dates_skipped_set ],
        meta => {
            first_date    => $all_dates[0]  // undef,
            last_date     => $all_dates[-1] // undef,
            ProcessedDays => scalar @all_dates,
        },
    };
}

sub _update_month_metadata {
    my ($existing_meta_href, $merge_result_href) = @_;
    my $content_version;
    my $dates_added = $merge_result_href->{dates_added} // [];
    if (!$existing_meta_href || ref($existing_meta_href) ne 'HASH') {
        $content_version = 1;
    } elsif (scalar @$dates_added > 0) {
        $content_version = ($existing_meta_href->{content_version} // 0) + 1;
    } else {
        $content_version = $existing_meta_href->{content_version} // 1;
    }
    my $merge_meta = $merge_result_href->{meta} // {};
    return {
        ProcessedDays   => $merge_meta->{ProcessedDays} // 0,
        first_date      => $merge_meta->{first_date},
        last_date       => $merge_meta->{last_date},
        content_version => $content_version,
        last_modified   => gmtime()->strftime('%Y-%m-%dT%H:%M:%SZ'),
    };
}

# ==============================================================================
#  TESTS
# ==============================================================================

# --- _extract_date_set_from_series -------------------------------------------

# Test 1.0: Single VM, single profile
{
    my $input = { VM1 => { Prof1 => { '2026-01-05' => 1.0, '2026-01-10' => 2.0 } } };
    my $result = _extract_date_set_from_series($input);
    is_deeply($result, { '2026-01-05' => 1, '2026-01-10' => 1 },
        '1.0: Extracts all dates from single VM/profile');
}

# Test 1.0a: Multiple VMs and profiles — union of all dates
{
    my $input = {
        VM1 => { P1 => { '2026-01-05' => 1 }, P2 => { '2026-01-07' => 3 } },
        VM2 => { P1 => { '2026-01-06' => 2 } },
    };
    my $result = _extract_date_set_from_series($input);
    is_deeply($result, { '2026-01-05' => 1, '2026-01-06' => 1, '2026-01-07' => 1 },
        '1.0a: Union of dates across multiple VMs/profiles');
}

# Test 1.0b: Empty input
{
    is_deeply(_extract_date_set_from_series({}),    {}, '1.0b: Empty hashref returns empty');
    is_deeply(_extract_date_set_from_series(undef), {}, '1.0b: undef returns empty');
}

# Test 1.0c: Malformed date strings are skipped
{
    my $input = { VM1 => { P1 => { '2026-01-05' => 1, 'bad_date' => 2, '2026-1-5' => 3 } } };
    my $result = _extract_date_set_from_series($input);
    is_deeply($result, { '2026-01-05' => 1 },
        '1.0c: Malformed dates silently skipped');
}

# --- _needs_history_update ---------------------------------------------------

# Test 1.1: New month (no existing history)
{
    my $l1 = { '2026-01-15' => 1, '2026-01-16' => 1 };
    my $r = _needs_history_update(undef, $l1);
    is($r->{needs_update}, 1,            '1.1: New month — needs_update = 1');
    is($r->{reason},       'new_month',  '1.1: New month — reason correct');
    is(scalar @{$r->{new_dates}}, 2,     '1.1: New month — all L1 dates are new');
}

# Test 1.2: Forward extension
{
    my $existing = { map { "2026-01-" . sprintf('%02d', $_) => 1 } 1..25 };
    my $l1       = { '2026-01-20' => 1, '2026-01-26' => 1, '2026-01-27' => 1 };
    my $r = _needs_history_update($existing, $l1);
    is($r->{needs_update}, 1,               '1.2: Forward extension detected');
    is($r->{reason},       'has_new_dates', '1.2: Reason = has_new_dates');
    is_deeply($r->{new_dates}, ['2026-01-26', '2026-01-27'],
        '1.2: Only genuinely new dates reported');
}

# Test 1.3: Backward extension
{
    my $existing = { map { "2026-01-" . sprintf('%02d', $_) => 1 } 10..25 };
    my $l1       = { '2026-01-05' => 1, '2026-01-15' => 1 };
    my $r = _needs_history_update($existing, $l1);
    is($r->{needs_update}, 1, '1.3: Backward extension detected');
    is_deeply($r->{new_dates}, ['2026-01-05'], '1.3: Only pre-range date is new');
}

# Test 1.4: Internal gap fill
{
    my %existing = map { "2026-01-" . sprintf('%02d', $_) => 1 } (1..10, 15..31);
    my $l1 = { '2026-01-10' => 1, '2026-01-11' => 1, '2026-01-12' => 1 };
    my $r = _needs_history_update(\%existing, $l1);
    is($r->{needs_update}, 1, '1.4: Gap fill detected');
    is_deeply($r->{new_dates}, ['2026-01-11', '2026-01-12'],
        '1.4: Only gap dates are new (01-10 already exists)');
}

# Test 1.5: No change — L1 is a subset of existing
{
    my $existing = { map { "2026-01-" . sprintf('%02d', $_) => 1 } 1..31 };
    my $l1       = { '2026-01-15' => 1, '2026-01-16' => 1 };
    my $r = _needs_history_update($existing, $l1);
    is($r->{needs_update}, 0,           '1.5: No update when L1 is a subset');
    is($r->{reason},       'no_change', '1.5: Reason = no_change');
}

# --- _merge_daily_profile_series ---------------------------------------------

# Test 1.6: First-write-wins — existing value preserved
{
    my $existing = { VM1 => { Prof1 => { '2026-01-15' => 1.5 } } };
    my $l1       = { VM1 => { Prof1 => { '2026-01-15' => 2.0, '2026-01-16' => 2.5 } } };
    my $r = _merge_daily_profile_series($existing, $l1);

    is($r->{merged_series}{VM1}{Prof1}{'2026-01-15'}, 1.5,
        '1.6: First-write-wins — existing value 1.5 preserved');
    is($r->{merged_series}{VM1}{Prof1}{'2026-01-16'}, 2.5,
        '1.6: New date 01-16 added from L1');
    is_deeply($r->{dates_added},   ['2026-01-16'], '1.6: dates_added tracks new dates');
    is_deeply($r->{dates_skipped}, ['2026-01-15'], '1.6: dates_skipped tracks preserved');
}

# Test 1.7: New VM added from L1
{
    my $existing = { VM1 => { Prof1 => { '2026-01-15' => 1.5 } } };
    my $l1       = { VM2 => { Prof1 => { '2026-01-20' => 3.0 } } };
    my $r = _merge_daily_profile_series($existing, $l1);

    ok(exists $r->{merged_series}{VM1}, '1.7: Existing VM1 preserved');
    ok(exists $r->{merged_series}{VM2}, '1.7: New VM2 added from L1');
    is($r->{merged_series}{VM2}{Prof1}{'2026-01-20'}, 3.0, '1.7: VM2 data correct');
    is($r->{meta}{ProcessedDays}, 2, '1.7: ProcessedDays = 2 (unique dates across VMs)');
    is($r->{meta}{first_date}, '2026-01-15', '1.7: first_date correct');
    is($r->{meta}{last_date},  '2026-01-20', '1.7: last_date correct');
}

# --- _update_month_metadata --------------------------------------------------

# Test 1.8: New month — content_version = 1
{
    my $merge_result = {
        dates_added => ['2026-01-01', '2026-01-02'],
        meta => { first_date => '2026-01-01', last_date => '2026-01-02', ProcessedDays => 2 },
    };
    my $meta = _update_month_metadata(undef, $merge_result);
    is($meta->{content_version}, 1,             '1.8: New month gets content_version = 1');
    is($meta->{ProcessedDays},   2,             '1.8: ProcessedDays from merge');
    is($meta->{first_date},      '2026-01-01',  '1.8: first_date from merge');
    like($meta->{last_modified},  qr/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/,
        '1.8: last_modified is ISO 8601 UTC');
}

# Test 1.9: Existing month with new dates — version increments
{
    my $existing_meta = { content_version => 3, ProcessedDays => 25 };
    my $merge_result = {
        dates_added => ['2026-01-26'],
        meta => { first_date => '2026-01-01', last_date => '2026-01-26', ProcessedDays => 26 },
    };
    my $meta = _update_month_metadata($existing_meta, $merge_result);
    is($meta->{content_version}, 4, '1.9: content_version incremented on new dates');
    is($meta->{ProcessedDays},  26, '1.9: ProcessedDays updated');
}

# Test 1.10: Existing month, no new dates — version unchanged
{
    my $existing_meta = { content_version => 5, ProcessedDays => 31 };
    my $merge_result = {
        dates_added => [],
        meta => { first_date => '2026-01-01', last_date => '2026-01-31', ProcessedDays => 31 },
    };
    my $meta = _update_month_metadata($existing_meta, $merge_result);
    is($meta->{content_version}, 5,  '1.10: content_version unchanged when no new dates');
    is($meta->{ProcessedDays},   31, '1.10: ProcessedDays unchanged');
}

done_testing();
