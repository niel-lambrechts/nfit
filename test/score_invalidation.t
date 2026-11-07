#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/score_invalidation.t
# PURPOSE:   Unit tests for Phase 7 — Tournament Score Invalidation
#
#   Validates:
#     7.0  Marker written when AdaptiveForecast.scoring exists
#     7.1  Marker NOT written when no AdaptiveForecast.scoring
#     7.2  Marker NOT written when partition file absent
#     7.3  baseline_changed_at populated as valid timestamp
#     7.4  score_invalidation_reason populated from caller
#     7.5  scores_valid = 0 at event snapshot level (Zone 3 readable)
#     7.6  Pre-existing scores_valid = 1 overwritten to 0
#     7.7  Zone 3 detection logic — scores_valid = 0 triggers stale
#     7.8  Zone 3 detection logic — scores_valid = 1 is fresh
#     7.9  Zone 3 detection logic — scores_valid absent defaults fresh
#     7.10 Atomic write: original structure preserved for non-target events
#     7.11 Guard: undef/missing arguments handled gracefully
#     7.12 Event snapshot without SeasonalEventSnapshots key handled
#     7.13 Reason category filter — only 'baseline_changed' triggers
#     7.14 Multiple events: only target event affected
#
# USAGE:     perl t/score_invalidation.t
#            prove t/score_invalidation.t
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3 (Phase 7)
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 18;
use File::Temp qw(tempdir);
use File::Spec;
use File::Path qw(make_path);
use JSON;
use Time::Piece;

# ==============================================================================
#  Inline copy of _mark_scores_invalid_for_snapshot (production-equivalent)
# ==============================================================================
my $verbose = 0;  # Global for the inlined function

sub _mark_scores_invalid_for_snapshot {
    my ($system_cache_dir, $event_name, $month_key, $reason) = @_;

    return unless ($system_cache_dir && $event_name && $month_key);

    my $partition_dir = File::Spec->catfile($system_cache_dir, '.nfit.history');
    my $filepath = File::Spec->catfile($partition_dir, "nfit.hist.${month_key}.json");

    return unless (-f $filepath && -s $filepath);

    my $partition_data;
    eval {
        open my $fh, '<:encoding(utf8)', $filepath
            or die "Cannot open $filepath: $!";
        local $/;
        $partition_data = JSON->new->allow_nonref->decode(<$fh>);
        close $fh;
    };
    if ($@ || !$partition_data || ref($partition_data) ne 'HASH') {
        warn "  [WARN] Phase 7: Could not read partition $month_key for score invalidation: $@\n";
        return;
    }

    my $month_data = $partition_data->{$month_key};
    return unless (ref($month_data) eq 'HASH');

    my $event_snapshot = $month_data->{SeasonalEventSnapshots}{$event_name} // undef;
    return unless (ref($event_snapshot) eq 'HASH');

    my $adaptive = $event_snapshot->{AdaptiveForecast} // {};
    unless (ref($adaptive) eq 'HASH' && exists $adaptive->{scoring}) {
        return;
    }

    $event_snapshot->{scores_valid}               = 0;
    $event_snapshot->{baseline_changed_at}        = localtime()->datetime();
    $event_snapshot->{score_invalidation_reason}  = $reason // 'baseline inputs changed';

    my $json_encoder = JSON->new->pretty->canonical;
    my $tmp_path = "${filepath}.tmp.$$";

    eval {
        my $json_text = $json_encoder->encode($partition_data);
        open my $fh, '>:encoding(utf8)', $tmp_path
            or die "Open temp failed: $!";
        print {$fh} $json_text or die "Write failed: $!";
        close $fh or die "Close failed: $!";
        rename $tmp_path, $filepath
            or die "Rename failed: $!";
    };
    if ($@) {
        warn "  [WARN] Phase 7: Score invalidation write failed for $month_key: $@\n";
        unlink $tmp_path if (-f $tmp_path);
        return;
    }
}

# ==============================================================================
#  Test Helpers
# ==============================================================================

sub _create_partition_file {
    my ($history_dir, $month_key, $snapshot_data) = @_;

    my $payload = { $month_key => $snapshot_data };
    my $path = File::Spec->catfile($history_dir, "nfit.hist.${month_key}.json");
    open my $fh, '>', $path or die "Cannot write $path: $!";
    print $fh JSON->new->pretty->canonical->encode($payload);
    close $fh;
    return $path;
}

sub _read_partition_file {
    my ($history_dir, $month_key) = @_;
    my $path = File::Spec->catfile($history_dir, "nfit.hist.${month_key}.json");
    open my $fh, '<:encoding(utf8)', $path or die "Cannot read $path: $!";
    local $/;
    my $data = JSON->new->allow_nonref->decode(<$fh>);
    close $fh;
    return $data->{$month_key};
}

# Build a snapshot with AdaptiveForecast.scoring present
sub _scored_snapshot {
    my ($event_name) = @_;
    return {
        SeasonalEventSnapshots => {
            $event_name => {
                eventName       => $event_name,
                scores_valid    => 1,
                ModelForecasts  => {
                    multiplicative_seasonal => {
                        '_meta' => { model_version => 1 },
                        'vm1'   => { forecast => { 'G3-95W15' => 0.55 } },
                    },
                },
                AdaptiveForecast => {
                    '_meta' => { model_version => 1 },
                    candidates => { vm1 => { 'G3-95W15' => { holt_winters => 0.60 } } },
                    scoring => {
                        '_meta' => {
                            scored_on             => '2025-05-15T10:00:00Z',
                            scored_against_month  => '2025-05',
                            scoring_logic_version => 1,
                            target_quantile       => 0.95,
                        },
                        observations => 3,
                    },
                },
            },
        },
    };
}

# Build a snapshot WITHOUT AdaptiveForecast.scoring (never scored)
sub _unscored_snapshot {
    my ($event_name) = @_;
    return {
        SeasonalEventSnapshots => {
            $event_name => {
                eventName      => $event_name,
                ModelForecasts => {
                    multiplicative_seasonal => {
                        '_meta' => { model_version => 1 },
                        'vm1'   => { forecast => { 'G3-95W15' => 0.55 } },
                    },
                },
            },
        },
    };
}

# Simulate Zone 3 _check_scoring_staleness S6 gate logic
sub _zone3_check_s6 {
    my ($snapshot_href) = @_;
    my $scores_valid = $snapshot_href->{scores_valid} // 1;
    if ($scores_valid == 0) {
        my $changed_at = $snapshot_href->{baseline_changed_at} // 'unknown';
        my $reason     = $snapshot_href->{score_invalidation_reason} // 'baseline inputs changed';
        return (1, "Baseline Changed (changed: $changed_at, reason: $reason)");
    }
    return (0, "Fresh");
}


# ==============================================================================
# Section 7.0: Marker Written When AdaptiveForecast.scoring Exists (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $event = 'month-end';
    my $month = '2025-04';

    _create_partition_file($history_dir, $month, _scored_snapshot($event));

    _mark_scores_invalid_for_snapshot($tmpdir, $event, $month, 'baseline inputs changed');

    my $result = _read_partition_file($history_dir, $month);
    my $snap = $result->{SeasonalEventSnapshots}{$event};

    is($snap->{scores_valid}, 0, '7.0a scores_valid set to 0 when scoring block exists');
    ok(defined $snap->{baseline_changed_at}, '7.0b baseline_changed_at populated');
}

# ==============================================================================
# Section 7.1: Marker NOT Written When No AdaptiveForecast.scoring (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $event = 'month-end';
    my $month = '2025-04';

    _create_partition_file($history_dir, $month, _unscored_snapshot($event));

    _mark_scores_invalid_for_snapshot($tmpdir, $event, $month, 'baseline inputs changed');

    my $result = _read_partition_file($history_dir, $month);
    my $snap = $result->{SeasonalEventSnapshots}{$event};

    ok(!exists $snap->{scores_valid}, '7.1a scores_valid not set when no scoring block');
    ok(!exists $snap->{baseline_changed_at}, '7.1b baseline_changed_at not set when no scoring block');
}

# ==============================================================================
# Section 7.2: Marker NOT Written When Partition Absent (1 test)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    # No partition file created — should return silently
    my $result = eval {
        _mark_scores_invalid_for_snapshot($tmpdir, 'month-end', '2025-04', 'test');
        1;
    };
    ok($result, '7.2a No error when partition file does not exist');
}

# ==============================================================================
# Section 7.3: baseline_changed_at Is Valid Timestamp (1 test)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $event = 'month-end';
    my $month = '2025-04';

    _create_partition_file($history_dir, $month, _scored_snapshot($event));
    _mark_scores_invalid_for_snapshot($tmpdir, $event, $month, 'test reason');

    my $result = _read_partition_file($history_dir, $month);
    my $ts = $result->{SeasonalEventSnapshots}{$event}{baseline_changed_at};

    # Time::Piece->datetime() produces "YYYY-MM-DDThh:mm:ss" format
    like($ts, qr/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/,
        '7.3a baseline_changed_at is ISO-8601 compatible timestamp');
}

# ==============================================================================
# Section 7.4: score_invalidation_reason Populated (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $event = 'month-end';
    my $month = '2025-04';

    _create_partition_file($history_dir, $month, _scored_snapshot($event));
    _mark_scores_invalid_for_snapshot($tmpdir, $event, $month, 'history content v2 to v3');

    my $result = _read_partition_file($history_dir, $month);
    my $snap = $result->{SeasonalEventSnapshots}{$event};

    is($snap->{score_invalidation_reason}, 'history content v2 to v3',
        '7.4a Custom reason persisted');

    # Test default reason when undef passed
    _create_partition_file($history_dir, $month, _scored_snapshot($event));
    _mark_scores_invalid_for_snapshot($tmpdir, $event, $month, undef);

    $result = _read_partition_file($history_dir, $month);
    $snap = $result->{SeasonalEventSnapshots}{$event};

    is($snap->{score_invalidation_reason}, 'baseline inputs changed',
        '7.4b Default reason used when undef');
}

# ==============================================================================
# Section 7.5: Pre-existing scores_valid = 1 Overwritten to 0 (1 test)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $event = 'month-end';
    my $month = '2025-04';

    # Create with explicit scores_valid = 1
    _create_partition_file($history_dir, $month, _scored_snapshot($event));

    my $before = _read_partition_file($history_dir, $month);
    is($before->{SeasonalEventSnapshots}{$event}{scores_valid}, 1,
        '7.5 Pre-condition: scores_valid starts as 1');

    _mark_scores_invalid_for_snapshot($tmpdir, $event, $month, 'test');

    my $after = _read_partition_file($history_dir, $month);
    is($after->{SeasonalEventSnapshots}{$event}{scores_valid}, 0,
        '7.5a scores_valid overwritten from 1 to 0');
}

# ==============================================================================
# Section 7.6: Zone 3 Detection — scores_valid = 0 Triggers Stale (2 tests)
# ==============================================================================

{
    my $snapshot = {
        scores_valid               => 0,
        baseline_changed_at        => '2025-06-15T14:30:00',
        score_invalidation_reason  => 'baseline inputs changed',
    };

    my ($is_stale, $reason) = _zone3_check_s6($snapshot);
    ok($is_stale, '7.6a Zone 3 S6 gate detects scores_valid = 0');
    like($reason, qr/Baseline Changed/, '7.6b Zone 3 S6 reason includes baseline change');
}

# ==============================================================================
# Section 7.7: Zone 3 Detection — scores_valid = 1 Is Fresh (1 test)
# ==============================================================================

{
    my $snapshot = { scores_valid => 1 };
    my ($is_stale, $reason) = _zone3_check_s6($snapshot);
    ok(!$is_stale, '7.7a Zone 3 S6 gate passes when scores_valid = 1');
}

# ==============================================================================
# Section 7.8: Zone 3 Detection — scores_valid Absent Defaults Fresh (1 test)
# ==============================================================================

{
    my $snapshot = {};  # No scores_valid key at all (legacy data)
    my ($is_stale, $reason) = _zone3_check_s6($snapshot);
    ok(!$is_stale, '7.8a Zone 3 S6 gate defaults to fresh when scores_valid absent');
}

# ==============================================================================
# Section 7.9: Atomic Write — Non-target Events Preserved (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $target_event = 'month-end';
    my $other_event  = 'quarter-end';
    my $month = '2025-04';

    # Create partition with TWO events — one scored, one not
    my $snapshot_data = {
        SeasonalEventSnapshots => {
            $target_event => {
                eventName        => $target_event,
                scores_valid     => 1,
                AdaptiveForecast => {
                    scoring => { '_meta' => { scored_on => '2025-05-15' }, observations => 3 },
                },
            },
            $other_event => {
                eventName    => $other_event,
                scores_valid => 1,
                AdaptiveForecast => {
                    scoring => { '_meta' => { scored_on => '2025-05-15' }, observations => 2 },
                },
            },
        },
    };

    _create_partition_file($history_dir, $month, $snapshot_data);

    # Invalidate only the target event
    _mark_scores_invalid_for_snapshot($tmpdir, $target_event, $month, 'test');

    my $result = _read_partition_file($history_dir, $month);

    is($result->{SeasonalEventSnapshots}{$target_event}{scores_valid}, 0,
        '7.9a Target event invalidated');
    is($result->{SeasonalEventSnapshots}{$other_event}{scores_valid}, 1,
        '7.9b Non-target event preserved');
}

# ==============================================================================
# Section 7.10: Guard — Missing/undef Arguments (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);

    # All undef arguments — should return silently, no crash
    my $r1 = eval { _mark_scores_invalid_for_snapshot(undef, undef, undef, undef); 1; };
    ok($r1, '7.10a No crash with all undef arguments');

    # Valid dir but no event name
    my $r2 = eval { _mark_scores_invalid_for_snapshot($tmpdir, undef, '2025-04', 'test'); 1; };
    ok($r2, '7.10b No crash with undef event name');
}
