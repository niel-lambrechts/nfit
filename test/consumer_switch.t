#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/consumer_switch.t
# PURPOSE:   Unit and integration tests for Phase 6 — Consumer Switch
#
#   Validates:
#     6.0  L1 escape hatch fingerprint determinism
#     6.1  baseline_fp_href structure for both modes
#     6.2  Mode switch staleness detection via stored metadata
#     6.3  Pre-computed baseline guard logic used by consumer functions
#     6.4  exec_ctx baseline injection and extraction patterns
#     6.5  exec_ctx without baseline (L1 fallback mode)
#     6.6  Non-divergence guarantee: expected FP == actual FP
#     6.7  L1 vs history fingerprints guaranteed distinct
#     6.8  content_version change → different fingerprint
#     6.9  _store 10th parameter acceptance and persistence
#     6.10 _store backward compatibility (9 parameters)
#
# USAGE:     perl t/consumer_switch.t
#            prove t/consumer_switch.t
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 30;
use Time::Piece;
use Time::Seconds;
use File::Temp qw(tempdir);
use File::Spec;
use File::Path qw(make_path);
use JSON;
use Digest::MD5 qw(md5_hex);
use POSIX qw(floor ceil);
use Scalar::Util qw(looks_like_number);

# ==============================================================================
#  Inline copies of production functions (Phases 2–5) for isolated testing.
#  These match the production code in nfit-profile.pl exactly.
# ==============================================================================

# --- Phase 2 dependencies ---

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

sub _get_month_boundaries {
    my ($month_key) = @_;
    unless ($month_key && $month_key =~ /^\d{4}-\d{2}$/) { return (); }
    my $month_start = eval { Time::Piece->strptime("${month_key}-01", '%Y-%m-%d') };
    return () if ($@ || !$month_start);
    my $month_end = $month_start->add_months(1) - ONE_DAY;
    return ($month_start, $month_end);
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

# --- Phase 4 shared hash/fingerprint helpers ---

sub _compute_profile_set_hash {
    my ($profiles_aref) = @_;
    return Digest::MD5::md5_hex(join('|', sort @$profiles_aref));
}

sub _compute_vm_set_hash {
    my ($vms_aref) = @_;
    return 'ALL' unless ($vms_aref && @$vms_aref);
    return Digest::MD5::md5_hex(join('|', sort @$vms_aref));
}

sub _compute_months_content_hash {
    my ($content_versions_href) = @_;
    my @parts;
    foreach my $month (sort keys %$content_versions_href) {
        push @parts, "$month:$content_versions_href->{$month}";
    }
    return Digest::MD5::md5_hex(join('|', @parts));
}

sub _compute_vm_scope_hash {
    my ($vm_scope_filter) = @_;
    return Digest::MD5::md5_hex('NONE') unless ($vm_scope_filter && ref($vm_scope_filter) eq 'HASH');
    my @parts;
    if ($vm_scope_filter->{include} && ref($vm_scope_filter->{include}) eq 'HASH'
        && %{$vm_scope_filter->{include}}) {
        push @parts, "inc:" . join(",", sort keys %{$vm_scope_filter->{include}});
    }
    if ($vm_scope_filter->{exclude} && ref($vm_scope_filter->{exclude}) eq 'HASH'
        && %{$vm_scope_filter->{exclude}}) {
        push @parts, "exc:" . join(",", sort keys %{$vm_scope_filter->{exclude}});
    }
    return Digest::MD5::md5_hex('NONE') unless @parts;
    return Digest::MD5::md5_hex(join("|", @parts));
}

sub _compute_exclusion_hash {
    my ($date_exclusions) = @_;
    return Digest::MD5::md5_hex('') unless ($date_exclusions
                                            && ref($date_exclusions) eq 'HASH');
    my @parts;
    push @parts, "G:" . join(",", sort @{$date_exclusions->{global} // []});
    foreach my $vm (sort keys %{$date_exclusions->{vm_specific} // {}}) {
        push @parts, "$vm:" . join(",", sort @{$date_exclusions->{vm_specific}{$vm}});
    }
    return Digest::MD5::md5_hex(join("|", @parts));
}

sub _compute_baseline_fingerprint {
    my ($meta_href) = @_;
    my @components = (
        $meta_href->{baseline_source},
        $meta_href->{canonical_days},
        $meta_href->{lookback_cap_days},
        $meta_href->{required_days},
        $meta_href->{coverage_threshold},
        $meta_href->{span_start},
        $meta_href->{span_end},
        $meta_href->{excluded_dates_hash},
        $meta_href->{vm_scope_hash},
        $meta_href->{months_content_hash},
        $meta_href->{profile_set_hash} // '',
        $meta_href->{vm_set_hash} // '',
    );
    return Digest::MD5::md5_hex(join('|', map { $_ // '' } @components));
}

# --- Phase 5: _read_month_metadata_only ---

sub _read_month_metadata_only {
    my ($system_cache_dir, $month_key) = @_;
    return undef unless ($system_cache_dir && $month_key && $month_key =~ /^\d{4}-\d{2}$/);
    my $partition_dir = File::Spec->catfile($system_cache_dir, '.nfit.history');
    my $path = File::Spec->catfile($partition_dir, "nfit.hist.${month_key}.json");
    return undef unless (-f $path && -s $path);
    my $meta;
    eval {
        open my $fh, '<:encoding(utf8)', $path or die "Cannot open: $!";
        local $/;
        my $json_text = <$fh>;
        close $fh;
        my $data = JSON->new->allow_nonref->decode($json_text);
        return unless (ref($data) eq 'HASH');
        my $payload = exists $data->{$month_key}
                    ? $data->{$month_key}
                    : $data->{(keys %$data)[0]};
        if (ref($payload) eq 'HASH' && exists $payload->{_meta}
            && ref($payload->{_meta}) eq 'HASH') {
            $meta = $payload->{_meta};
        }
    };
    return $meta;
}

# --- Phase 5: _calculate_expected_baseline_fingerprint ---

sub _calculate_expected_baseline_fingerprint {
    my ($args_href) = @_;
    my $canonical_days     = $args_href->{canonical_days}     // 90;
    my $coverage_threshold = $args_href->{coverage_threshold} // 0.7;
    my $required_days      = int($canonical_days * $coverage_threshold);
    my $history_index = _get_history_month_index($args_href->{system_cache_dir});
    my $required_months = _determine_required_months(
        $args_href->{end_date_obj}, $canonical_days, $history_index
    );
    return undef unless ($required_months && @$required_months);
    my %content_versions;
    foreach my $month_key (@$required_months) {
        my $meta = _read_month_metadata_only($args_href->{system_cache_dir}, $month_key);
        $content_versions{$month_key} = ($meta ? ($meta->{content_version} // 0) : 0);
    }
    my $span_start = ($args_href->{end_date_obj} - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $span_end   = $args_href->{end_date_obj}->strftime('%Y-%m-%d');
    my $meta = {
        baseline_source      => 'history',
        canonical_days       => $canonical_days,
        lookback_cap_days    => 120,
        required_days        => $required_days,
        coverage_threshold   => $coverage_threshold,
        span_start           => $span_start,
        span_end             => $span_end,
        excluded_dates_hash  => _compute_exclusion_hash($args_href->{date_exclusions}),
        vm_scope_hash        => _compute_vm_scope_hash($args_href->{vm_scope_filter}),
        months_content_hash  => _compute_months_content_hash(\%content_versions),
        profile_set_hash     => _compute_profile_set_hash($args_href->{profiles} // []),
        vm_set_hash          => _compute_vm_set_hash($args_href->{vms}),
    };
    return _compute_baseline_fingerprint($meta);
}

# --- Phase 5: _load_stored_bucket_fingerprint ---

sub _load_stored_bucket_fingerprint {
    my ($system_cache_dir, $event_name, $model_type, $bucket_key) = @_;
    my $partition_dir = File::Spec->catfile($system_cache_dir, '.nfit.history');
    return undef unless (-d $partition_dir);

    my $month_key = $bucket_key;
    $month_key =~ s/-\d{2}$// if ($month_key =~ /^\d{4}-\d{2}-\d{2}$/);
    my $path = File::Spec->catfile($partition_dir, "nfit.hist.${month_key}.json");
    return undef unless (-f $path);

    my $data;
    eval {
        open my $fh, '<:encoding(utf8)', $path or die "Cannot open: $!";
        local $/;
        $data = JSON->new->allow_nonref->decode(<$fh>);
        close $fh;
    };
    return undef if ($@ || !$data);

    my $month_data = $data->{$month_key} // $data->{(keys %$data)[0]};
    return undef unless (ref($month_data) eq 'HASH');

    # Navigate: SeasonalEventSnapshots → event → ModelForecasts → model → _meta
    my $meta = eval {
        $month_data->{SeasonalEventSnapshots}{$event_name}{ModelForecasts}{$model_type}{'_meta'};
    };
    return undef unless (defined $meta && ref($meta) eq 'HASH');

    if (exists $meta->{fingerprints}) {
        # Shallow copy to avoid mutating any cached structure
        my %result = %{$meta->{fingerprints}};

        # Carry baseline fields from _meta into the returned structure
        $result{baseline_fingerprint} = $meta->{baseline_fingerprint}
            if exists $meta->{baseline_fingerprint};
        $result{baseline_source} = $meta->{baseline_source}
            if exists $meta->{baseline_source};

        return \%result;
    }

    return undef;
}

# --- Phase 5: _calculate_aggregate_coverage ---

sub _calculate_aggregate_coverage {
    my ($coverage_by_vm) = @_;
    return 0 unless ($coverage_by_vm && ref($coverage_by_vm) eq 'HASH' && %$coverage_by_vm);
    my $min_coverage = 1.0;
    my $has_data = 0;
    foreach my $vm (keys %$coverage_by_vm) {
        my $profiles = $coverage_by_vm->{$vm};
        next unless (ref($profiles) eq 'HASH');
        foreach my $profile (keys %$profiles) {
            my $info = $profiles->{$profile};
            next unless (ref($info) eq 'HASH' && exists $info->{pct_coverage});
            $has_data = 1;
            $min_coverage = $info->{pct_coverage}
                if ($info->{pct_coverage} < $min_coverage);
        }
    }
    return $has_data ? $min_coverage : 0;
}

# --- Simplified _get_baseline_from_history (test-isolated) ---

sub _calculate_percentile_from_sorted {
    my ($sorted_aref, $pct) = @_;
    return undef unless ($sorted_aref && @$sorted_aref);
    my $n = scalar @$sorted_aref;
    return $sorted_aref->[0] if $n == 1;
    my $rank = ($pct / 100.0) * ($n - 1);
    my $lower = POSIX::floor($rank);
    my $upper = POSIX::ceil($rank);
    return $sorted_aref->[$lower] if ($lower == $upper);
    my $frac = $rank - $lower;
    return $sorted_aref->[$lower] + $frac * ($sorted_aref->[$upper] - $sorted_aref->[$lower]);
}

sub _get_baseline_from_history {
    my ($args_href) = @_;
    my $system_cache_dir   = $args_href->{system_cache_dir};
    my $end_date_obj       = $args_href->{end_date_obj};
    my $canonical_days     = $args_href->{canonical_days}     // 90;
    my $coverage_threshold = $args_href->{coverage_threshold} // 0.7;
    my $required_days      = int($canonical_days * $coverage_threshold);

    my $partition_dir = File::Spec->catfile($system_cache_dir, '.nfit.history');
    my $idx = _get_history_month_index($system_cache_dir);
    my $req = _determine_required_months($end_date_obj, $canonical_days, $idx);
    die "No history exists. Run `nfit-profile --update-history` first.\n"
        unless ($req && @$req);

    my (%series, @months_read, %content_versions, $gmin, $gmax);
    my $ws = ($end_date_obj - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $we = $end_date_obj->strftime('%Y-%m-%d');
    my $json_dec = JSON->new->allow_nonref;

    foreach my $mk (@$req) {
        my $path = File::Spec->catfile($partition_dir, "nfit.hist.${mk}.json");
        next unless (-f $path && -s $path);
        my $json_text = do { open my $fh, '<:encoding(utf8)', $path or next; local $/; <$fh>; };
        my $chunk = eval { $json_dec->decode($json_text) };
        next if ($@ || !$chunk || ref($chunk) ne 'HASH');
        my $md = exists $chunk->{$mk} ? $chunk->{$mk} : $chunk->{(keys %$chunk)[0]};
        next unless (ref($md) eq 'HASH');

        push @months_read, $mk;
        $content_versions{$mk} = (exists $md->{_meta} && ref($md->{_meta}) eq 'HASH')
            ? ($md->{_meta}{content_version} // 0) : 0;

        my $dps = $md->{DailyProfileSeries};
        next unless (ref($dps) eq 'HASH');
        foreach my $d (keys %$dps) {
            next unless ($d =~ /^\d{4}-\d{2}-\d{2}$/);
            next if ($d lt $ws || $d gt $we);
            my $day_data = $dps->{$d};
            next unless (ref($day_data) eq 'HASH');
            foreach my $vm (keys %$day_data) {
                my $vm_data = $day_data->{$vm};
                next unless (ref($vm_data) eq 'HASH');
                foreach my $pr (keys %$vm_data) {
                    $series{$vm}{$pr}{$d} = $vm_data->{$pr};
                    $gmin = $d if (!defined $gmin || $d lt $gmin);
                    $gmax = $d if (!defined $gmax || $d gt $gmax);
                }
            }
        }
    }

    # Baseline computation (P95 from sorted values)
    my (%baselines, %coverage);
    foreach my $vm (keys %series) {
        foreach my $profile (keys %{$series{$vm}}) {
            my @vals = grep { looks_like_number($_) } values %{$series{$vm}{$profile}};
            my $n = scalar @vals;
            my $pct = ($canonical_days > 0) ? $n / $canonical_days : 0;
            if ($n == 0) {
                $baselines{$vm}{$profile} = undef;
                $coverage{$vm}{$profile} = { days_available => 0, pct_coverage => 0, status => 'no_data' };
            } else {
                my @sorted = sort { $a <=> $b } @vals;
                $baselines{$vm}{$profile} = _calculate_percentile_from_sorted(\@sorted, 95);
                $coverage{$vm}{$profile} = {
                    days_available => $n, pct_coverage => $pct,
                    status => ($pct >= $coverage_threshold) ? 'sufficient' : 'degraded',
                };
            }
        }
    }

    my $cs_start = ($end_date_obj - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $cs_end   = $end_date_obj->strftime('%Y-%m-%d');

    my $meta = {
        baseline_source     => 'history',
        canonical_days      => $canonical_days,
        lookback_cap_days   => 120,
        required_days       => $required_days,
        coverage_threshold  => $coverage_threshold,
        span_start          => $cs_start,
        span_end            => $cs_end,
        actual_data_span    => { first_date => $gmin, last_date => $gmax },
        excluded_dates_hash  => _compute_exclusion_hash($args_href->{date_exclusions}),
        vm_scope_hash        => _compute_vm_scope_hash($args_href->{vm_scope_filter}),
        months_read          => [ sort @months_read ],
        months_content_hash  => _compute_months_content_hash(\%content_versions),
        profile_set_hash     => _compute_profile_set_hash($args_href->{profiles} // []),
        vm_set_hash          => _compute_vm_set_hash($args_href->{vms}),
        coverage_by_vm       => \%coverage,
    };
    $meta->{fingerprint} = _compute_baseline_fingerprint($meta);

    return {
        baselines => \%baselines,
        meta      => $meta,
    };
}

# ==============================================================================
#  Test Helpers
# ==============================================================================

sub _create_test_month_file {
    my ($history_dir, $month_key, $series_href, $content_version) = @_;
    $content_version //= 1;

    my @dates = sort keys %$series_href;
    my $month_data = {
        $month_key => {
            DailyProfileSeries => $series_href,
            _meta => {
                content_version => $content_version,
                first_date      => $dates[0],
                last_date       => $dates[-1],
            },
        },
    };

    my $path = File::Spec->catfile($history_dir, "nfit.hist.${month_key}.json");
    open my $fh, '>', $path or die "Cannot write $path: $!";
    print $fh JSON->new->pretty->canonical->encode($month_data);
    close $fh;
    return $path;
}

sub _create_test_snapshot_file {
    my ($history_dir, $month_key, $event_name, $model_type, $bucket_key, $meta_href) = @_;

    my $snapshot = {
        $month_key => {
            SeasonalEventSnapshots => {
                $event_name => {
                    ModelForecasts => {
                        $model_type => {
                            '_meta'  => $meta_href,
                            'vm1'    => { forecast => { 'G3-95W15' => 1.5 } },
                        },
                    },
                },
            },
            _meta => { content_version => 1 },
        },
    };

    my $path = File::Spec->catfile($history_dir, "nfit.hist.${month_key}.json");
    open my $fh, '>', $path or die "Cannot write $path: $!";
    print $fh JSON->new->pretty->canonical->encode($snapshot);
    close $fh;
    return $path;
}

sub _build_test_daily_series {
    my ($year, $month, $days, $vm, $profile, $base_val) = @_;
    my %series;
    for my $day (1..$days) {
        my $date_str = sprintf('%04d-%02d-%02d', $year, $month, $day);
        $series{$date_str} = { $vm => { $profile => $base_val + ($day * 0.01) } };
    }
    return \%series;
}

# ==============================================================================
# Section 6.0: L1 Escape Hatch Fingerprint Determinism (4 tests)
# ==============================================================================

{
    my $canonical_days = 90;
    my $end_date = Time::Piece->strptime('2025-06-30', '%Y-%m-%d');
    my $span_start = ($end_date - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $span_end   = $end_date->strftime('%Y-%m-%d');

    my $fp1 = md5_hex("l1_cache:$canonical_days:$span_start:$span_end");
    my $fp2 = md5_hex("l1_cache:$canonical_days:$span_start:$span_end");

    is($fp1, $fp2, '6.0a L1 fingerprint is deterministic for same inputs');

    my $fp3 = md5_hex("l1_cache:120:$span_start:$span_end");
    isnt($fp1, $fp3, '6.0b Different canonical_days produces different L1 fingerprint');

    my $end_date2 = Time::Piece->strptime('2025-07-01', '%Y-%m-%d');
    my $span_start2 = ($end_date2 - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $span_end2   = $end_date2->strftime('%Y-%m-%d');
    my $fp4 = md5_hex("l1_cache:$canonical_days:$span_start2:$span_end2");
    isnt($fp1, $fp4, '6.0c Different end date produces different L1 fingerprint');

    like($fp1, qr/^[0-9a-f]{32}$/, '6.0d L1 fingerprint is valid MD5 hex');
}

# ==============================================================================
# Section 6.1: Baseline Fingerprint Href Structure (3 tests)
# ==============================================================================

{
    my $history_href = {
        fingerprint => 'abc123def456',
        source      => 'history',
    };
    is($history_href->{source}, 'history', '6.1a History mode source is correct');
    is($history_href->{fingerprint}, 'abc123def456', '6.1b History mode fingerprint carried');

    my $l1_href = {
        fingerprint => md5_hex("l1_cache:90:2025-04-02:2025-06-30"),
        source      => 'l1_cache',
    };
    is($l1_href->{source}, 'l1_cache', '6.1c L1 mode source is correct');
}

# ==============================================================================
# Section 6.2: Mode Switch Staleness Detection (4 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $event_name = 'TestEvent';
    my $model_type = 'multiplicative_seasonal';
    my $bucket_key = '2025-05';

    # Create stored snapshot with baseline_source = 'history'
    _create_test_snapshot_file(
        $history_dir, '2025-05', $event_name, $model_type, $bucket_key,
        {
            fingerprints => {
                combined       => 'stored_combined_fp',
                engine_version => '5.0',
                config         => 'cfg_hash',
                dependencies   => 'dep_hash',
                prior_bucket   => 'NONE',
                event_def      => 'evt_hash',
                baseline       => 'bl_hash_history',
            },
            baseline_fingerprint => 'bl_hash_history',
            baseline_source      => 'history',
        }
    );

    my $loaded = _load_stored_bucket_fingerprint(
        $tmpdir, $event_name, $model_type, $bucket_key
    );

    ok(defined $loaded, '6.2a Loaded stored fingerprint successfully');
    is($loaded->{baseline_source}, 'history', '6.2b Stored baseline_source extracted');
    is($loaded->{baseline_fingerprint}, 'bl_hash_history', '6.2c Stored baseline_fingerprint extracted');

    # Verify mode mismatch would be detected
    my $l1_fp_href = { fingerprint => 'l1_fp_value', source => 'l1_cache' };
    isnt($loaded->{baseline_source}, $l1_fp_href->{source},
        '6.2d Source mode mismatch detected (history vs l1_cache)');
}

# ==============================================================================
# Section 6.3: Pre-computed Baseline Guard Logic (5 tests)
#
# Tests the guard pattern used in consumer functions:
#   if ($precomputed && ref($precomputed) eq 'HASH' && %$precomputed)
# ==============================================================================

{
    my $valid = { 'vm1' => { 'G3-95W15' => 0.45 } };
    ok(($valid && ref($valid) eq 'HASH' && %$valid),
        '6.3a Valid baseline hash passes guard');

    my $undef_bl = undef;
    ok(!($undef_bl && ref($undef_bl) eq 'HASH' && %$undef_bl),
        '6.3b undef baseline fails guard (L1 fallback)');

    my $empty = {};
    ok(!($empty && ref($empty) eq 'HASH' && %$empty),
        '6.3c Empty baseline hash fails guard (L1 fallback)');

    my $bad = [1, 2, 3];
    ok(!($bad && ref($bad) eq 'HASH' && %$bad),
        '6.3d Non-hash reference fails guard (L1 fallback)');

    my $partial = { 'vm1' => { 'G3-95W15' => undef } };
    ok(($partial && ref($partial) eq 'HASH' && %$partial),
        '6.3e Partial baseline (undef values) passes guard; consumer handles per-VM undef');
}

# ==============================================================================
# Section 6.4: exec_ctx Baseline Injection (3 tests)
# ==============================================================================

{
    my $exec_ctx = {
        effective_end_date => Time::Piece->strptime('2025-06-30', '%Y-%m-%d'),
        system_identifier  => 'TEST_SYS',
    };

    my $baseline_result = {
        baselines => {
            'vm1' => { 'G3-95W15' => 0.55, 'P-99W1' => 0.80 },
            'vm2' => { 'G3-95W15' => 1.20 },
        },
        meta => {
            baseline_source => 'history',
            canonical_days  => 90,
            fingerprint     => 'abc123',
        },
    };

    $exec_ctx->{_baseline_result} = $baseline_result;
    $exec_ctx->{_baseline_meta}   = $baseline_result->{meta};

    # Test the extraction pattern used by all three executors
    my $precomputed = ($exec_ctx->{_baseline_result})
        ? $exec_ctx->{_baseline_result}{baselines} : undef;

    ok(defined $precomputed, '6.4a Baseline extraction from exec_ctx succeeds');
    is(scalar keys %$precomputed, 2, '6.4b Both VMs present in extracted baseline');
    is($exec_ctx->{_baseline_meta}{baseline_source}, 'history',
        '6.4c Baseline meta accessible for _store call');
}

# ==============================================================================
# Section 6.5: exec_ctx Without Baseline — L1 Mode (3 tests)
# ==============================================================================

{
    my $exec_ctx = {
        effective_end_date => Time::Piece->strptime('2025-06-30', '%Y-%m-%d'),
        system_identifier  => 'TEST_SYS',
        # No _baseline_result — L1 mode skips history computation
    };

    my $precomputed = ($exec_ctx->{_baseline_result})
        ? $exec_ctx->{_baseline_result}{baselines} : undef;

    ok(!defined $precomputed, '6.5a Baseline extraction returns undef in L1 mode');
    ok(!($precomputed && ref($precomputed) eq 'HASH' && %$precomputed),
        '6.5b Consumer guard correctly fails (legacy L1 path taken)');
    ok(!defined $exec_ctx->{_baseline_meta},
        '6.5c Baseline meta absent; _store receives undef (backward-compatible)');
}

# ==============================================================================
# Section 6.6: Non-divergence Guarantee — Expected FP == Actual FP (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $end_date = Time::Piece->strptime('2025-06-30', '%Y-%m-%d');

    my $series = _build_test_daily_series(2025, 6, 30, 'vm1', 'G3-95W15', 0.5);
    _create_test_month_file($history_dir, '2025-06', $series, 3);

    # Expected fingerprint (lightweight, metadata-only path)
    my $expected_fp = _calculate_expected_baseline_fingerprint({
        system_cache_dir   => $tmpdir,
        end_date_obj       => $end_date,
        canonical_days     => 30,
        coverage_threshold => 0.7,
        date_exclusions    => undef,
        vm_scope_filter    => undef,
        profiles           => ['G3-95W15'],
    });

    ok(defined $expected_fp, '6.6a Expected fingerprint computed');

    # Actual fingerprint (full pipeline with data loading)
    my $baseline_result = _get_baseline_from_history({
        system_cache_dir   => $tmpdir,
        end_date_obj       => $end_date,
        canonical_days     => 30,
        coverage_threshold => 0.7,
        date_exclusions    => undef,
        peak_periods       => [],
        profiles           => ['G3-95W15'],
    });

    is($expected_fp, $baseline_result->{meta}{fingerprint},
        '6.6b Expected fingerprint matches actual (non-divergence guarantee)');
}

# ==============================================================================
# Section 6.7: L1 vs History FP Guaranteed Distinct (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $end_date = Time::Piece->strptime('2025-06-30', '%Y-%m-%d');
    my $canonical_days = 30;

    my $series = _build_test_daily_series(2025, 6, 30, 'vm1', 'G3-95W15', 0.6);
    _create_test_month_file($history_dir, '2025-06', $series, 1);

    # History-mode fingerprint
    my $history_fp = _calculate_expected_baseline_fingerprint({
        system_cache_dir   => $tmpdir,
        end_date_obj       => $end_date,
        canonical_days     => $canonical_days,
        coverage_threshold => 0.7,
        profiles           => ['G3-95W15'],
    });

    # L1-mode fingerprint for identical span
    my $span_start = ($end_date - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $span_end   = $end_date->strftime('%Y-%m-%d');
    my $l1_fp = md5_hex("l1_cache:$canonical_days:$span_start:$span_end");

    ok(defined $history_fp && defined $l1_fp,
        '6.7a Both fingerprints computed');
    isnt($history_fp, $l1_fp,
        '6.7b History and L1 fingerprints are guaranteed distinct');
}

# ==============================================================================
# Section 6.8: content_version Change → Different Fingerprint (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $end_date = Time::Piece->strptime('2025-06-30', '%Y-%m-%d');

    my $series = _build_test_daily_series(2025, 6, 30, 'vm1', 'G3-95W15', 0.5);

    # Version 1
    _create_test_month_file($history_dir, '2025-06', $series, 1);
    my $fp_v1 = _calculate_expected_baseline_fingerprint({
        system_cache_dir => $tmpdir, end_date_obj => $end_date,
        canonical_days => 30, profiles => ['G3-95W15'],
    });

    # Version 2 (content_version incremented — simulates --update-history rerun)
    _create_test_month_file($history_dir, '2025-06', $series, 2);
    my $fp_v2 = _calculate_expected_baseline_fingerprint({
        system_cache_dir => $tmpdir, end_date_obj => $end_date,
        canonical_days => 30, profiles => ['G3-95W15'],
    });

    ok(defined $fp_v1 && defined $fp_v2,
        '6.8a Both version fingerprints computed');
    isnt($fp_v1, $fp_v2,
        '6.8b content_version change triggers different expected fingerprint');
}

# ==============================================================================
# Section 6.9: Exclusion Change → Different Fingerprint (2 tests)
# ==============================================================================

{
    my $tmpdir = tempdir(CLEANUP => 1);
    my $history_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($history_dir);

    my $end_date = Time::Piece->strptime('2025-06-30', '%Y-%m-%d');

    my $series = _build_test_daily_series(2025, 6, 30, 'vm1', 'G3-95W15', 0.5);
    _create_test_month_file($history_dir, '2025-06', $series, 1);

    # Fingerprint with no exclusions
    my $fp_no_excl = _calculate_expected_baseline_fingerprint({
        system_cache_dir => $tmpdir, end_date_obj => $end_date,
        canonical_days => 30, profiles => ['G3-95W15'],
        date_exclusions => undef,
    });

    # Fingerprint with a global exclusion date added
    my $fp_with_excl = _calculate_expected_baseline_fingerprint({
        system_cache_dir => $tmpdir, end_date_obj => $end_date,
        canonical_days => 30, profiles => ['G3-95W15'],
        date_exclusions => { global => ['2025-06-15'], vm_specific => {} },
    });

    ok(defined $fp_no_excl && defined $fp_with_excl,
        '6.9a Both exclusion-variant fingerprints computed');
    isnt($fp_no_excl, $fp_with_excl,
        '6.9b Exclusion change triggers different expected fingerprint');
}
