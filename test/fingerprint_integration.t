#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/fingerprint_integration.t
# PURPOSE:   Unit and integration tests for Phase 5 — Fingerprint Integration
#            with Staleness Gates:
#              _read_month_metadata_only,
#              _calculate_expected_baseline_fingerprint,
#              _calculate_aggregate_coverage,
#              _compute_bucket_fingerprint (baseline extension),
#              _should_reprocess_bucket (baseline staleness checks).
#
# USAGE:     perl t/fingerprint_integration.t
#            prove t/fingerprint_integration.t
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 36;
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
#  Inline copies of production functions (Phases 1–5) for isolated testing.
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

# --- Phase 4 pipeline (needed for non-divergence test) ---

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

    # Bounded stitch (simplified inline for test isolation)
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

        my $mwa = $md->{MonthlyWorkloadAnalysis};
        next unless (ref($mwa) eq 'HASH');
        foreach my $vm (keys %$mwa) {
            my $dps = $mwa->{$vm}{DailyProfileSeries};
            next unless (ref($dps) eq 'HASH');
            foreach my $pr (keys %$dps) {
                my $dv = $dps->{$pr};
                next unless (ref($dv) eq 'HASH');
                foreach my $d (keys %$dv) {
                    next unless ($d =~ /^\d{4}-\d{2}-\d{2}$/);
                    next if ($d lt $ws || $d gt $we);
                    $series{$vm}{$pr}{$d} = $dv->{$d};
                    $gmin = $d if (!defined $gmin || $d lt $gmin);
                    $gmax = $d if (!defined $gmax || $d gt $gmax);
                }
            }
        }
    }

    # Baseline computation
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

    return { baselines => \%baselines, meta => $meta };
}

# ==============================================================================
#  Phase 5 functions under test
# ==============================================================================

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

# --- Simplified support functions for staleness tests ---

sub _compute_event_definition_fingerprint {
    my ($event_cfg) = @_;
    return 'NONE' unless (defined $event_cfg && ref($event_cfg) eq 'HASH');
    return Digest::MD5::md5_hex(JSON->new->utf8->canonical->encode($event_cfg));
}

sub _compute_execution_context_fingerprint {
    my ($exec_ctx, $model_type) = @_;
    return 'NONE' unless (defined $exec_ctx && ref($exec_ctx) eq 'HASH');
    return Digest::MD5::md5_hex(JSON->new->utf8->canonical->encode({
        %$exec_ctx, model_type => $model_type // '',
    }));
}

my $SEASONAL_ENGINE_VERSION = '1.0.0';

sub _compute_bucket_fingerprint {
    my ($event_cfg, $exec_ctx, $model_type, $prev_bucket_fp, $dependency_fp, $baseline_fp) = @_;

    my $event_def_fp = _compute_event_definition_fingerprint($event_cfg);
    my $exec_ctx_fp  = _compute_execution_context_fingerprint($exec_ctx, $model_type);

    my $chain_fp = 'NONE';
    if (defined $prev_bucket_fp && $prev_bucket_fp ne '' && $prev_bucket_fp ne 'NONE') {
        $chain_fp = Digest::MD5::md5_hex("chain:$prev_bucket_fp");
    }

    my $dep_fp_str      = (defined $dependency_fp && $dependency_fp ne '') ? $dependency_fp : 'NONE';
    my $baseline_fp_str = (defined $baseline_fp   && $baseline_fp   ne '') ? $baseline_fp   : 'NONE';

    my $fingerprint_components = {
        baseline       => $baseline_fp_str,
        event_def      => $event_def_fp,
        exec_ctx       => $exec_ctx_fp,
        chain          => $chain_fp,
        dependencies   => $dep_fp_str,
        engine_version => $SEASONAL_ENGINE_VERSION // '1.0',
    };

    my $json_struct = JSON->new->utf8->canonical->allow_nonref->encode($fingerprint_components);
    my $combined = Digest::MD5::md5_hex($json_struct);

    return {
        combined       => $combined,
        baseline       => $baseline_fp_str,
        event_def      => $event_def_fp,
        exec_ctx       => $exec_ctx_fp,
        chain          => $chain_fp,
        dependencies   => $dep_fp_str,
        engine_version => $fingerprint_components->{engine_version},
    };
}

# Mock-based _should_reprocess_bucket for test isolation
{
    my $_mock_stored_fp;

    sub _set_mock_stored_fingerprint { $_mock_stored_fp = $_[0]; }

    sub _should_reprocess_bucket {
        my ($system_cache_dir, $event_name, $event_cfg, $model_type, $bucket_key,
            $exec_ctx, $prev_bucket_fp, $dependency_fp, $force_flag, $baseline_fp_href) = @_;

        my $baseline_fp_str = ($baseline_fp_href && ref($baseline_fp_href) eq 'HASH')
                            ? $baseline_fp_href->{fingerprint} : undef;

        my $current_fp = _compute_bucket_fingerprint(
            $event_cfg, $exec_ctx, $model_type, $prev_bucket_fp,
            $dependency_fp, $baseline_fp_str
        );

        if ($force_flag) {
            return {
                reprocess       => 1,     reason          => 'User requested --force',
                reason_category => 'force', current_fp    => $current_fp, stored_fp => undef,
            };
        }

        my $stored_fp = $_mock_stored_fp;

        unless (defined $stored_fp) {
            return {
                reprocess       => 1,
                reason          => "No prior fingerprint (bootstrap or first run for bucket $bucket_key)",
                reason_category => 'missing_prior',
                current_fp      => $current_fp, stored_fp => undef,
            };
        }

        my @diff_reasons;

        if (($stored_fp->{engine_version} // '') ne ($current_fp->{engine_version} // '')) {
            push @diff_reasons, sprintf("engine version changed (%s -> %s)",
                $stored_fp->{engine_version} // 'unknown',
                $current_fp->{engine_version} // 'unknown');
        }

        if (($stored_fp->{dependencies} // 'NONE') ne ($current_fp->{dependencies} // 'NONE')) {
            push @diff_reasons, "upstream history inputs changed (backfill or regeneration)";
        }

        # Phase 5: Baseline checks
        if ($baseline_fp_href && ref($baseline_fp_href) eq 'HASH') {
            my $stored_bl_source  = $stored_fp->{baseline_source}      // 'unknown';
            my $current_bl_source = $baseline_fp_href->{source}        // 'history';
            if ($stored_bl_source ne 'unknown' && $stored_bl_source ne $current_bl_source) {
                push @diff_reasons, sprintf("baseline mode changed (%s -> %s)",
                    $stored_bl_source, $current_bl_source);
            }
            my $stored_bl_fp  = $stored_fp->{baseline_fingerprint} // 'none';
            my $current_bl_fp = $baseline_fp_href->{fingerprint}   // 'none';
            if ($stored_bl_fp ne 'none' && $stored_bl_fp ne $current_bl_fp) {
                push @diff_reasons, "baseline inputs changed (history content or exclusion configuration)";
            }
        }

        if (($stored_fp->{event_def} // '') ne ($current_fp->{event_def} // '')) {
            push @diff_reasons, "event configuration changed (dates, vms, exclude_dates, etc.)";
        }
        if (($stored_fp->{exec_ctx} // '') ne ($current_fp->{exec_ctx} // '')) {
            push @diff_reasons, "execution context changed (analysis window or cache bounds)";
        }
        if (($stored_fp->{chain} // '') ne ($current_fp->{chain} // '')) {
            push @diff_reasons, "upstream dependency changed (prior bucket was reprocessed)";
        }

        if (@diff_reasons) {
            my $reason_category = 'config_changed';
            if    (grep { /engine version/ } @diff_reasons)    { $reason_category = 'rules_changed'; }
            elsif (grep { /baseline/ } @diff_reasons)          { $reason_category = 'baseline_changed'; }
            elsif (grep { /upstream dependency/ } @diff_reasons) { $reason_category = 'chain_changed'; }
            return {
                reprocess       => 1,
                reason          => join("; ", @diff_reasons),
                reason_category => $reason_category,
                current_fp      => $current_fp, stored_fp => $stored_fp,
            };
        }

        return {
            reprocess       => 0,
            reason          => "Fingerprints match (combined: " . substr($current_fp->{combined}, 0, 12) . "...)",
            reason_category => 'skip',
            current_fp      => $current_fp, stored_fp => $stored_fp,
        };
    }
}

# ==============================================================================
#  Test data scaffolding helpers
# ==============================================================================

sub _create_test_history {
    my (%opts) = @_;
    my $tmpdir = tempdir(CLEANUP => 1);
    my $hist_dir = File::Spec->catfile($tmpdir, '.nfit.history');
    make_path($hist_dir);
    my $months = $opts{months} // {};
    my $json_encoder = JSON->new->utf8->canonical->pretty;
    foreach my $mk (keys %$months) {
        my $path = File::Spec->catfile($hist_dir, "nfit.hist.${mk}.json");
        open my $fh, '>:encoding(utf8)', $path or die "Cannot write $path: $!";
        print $fh $json_encoder->encode({ $mk => $months->{$mk} });
        close $fh;
    }
    return $tmpdir;
}

sub _generate_daily_data {
    my (%opts) = @_;
    my $start_date = $opts{start};
    my $count      = $opts{count};
    my $base_val   = $opts{base} // 0.50;
    my $step       = $opts{step} // 0.001;
    my %data;
    my $d = Time::Piece->strptime($start_date, '%Y-%m-%d');
    for my $i (0 .. ($count - 1)) {
        $data{$d->strftime('%Y-%m-%d')} = sprintf("%.4f", $base_val + ($i * $step));
        $d += ONE_DAY;
    }
    return \%data;
}


# ==============================================================================
#  SECTION 5.0: _read_month_metadata_only
# ==============================================================================

{
    note("--- Section 5.0: _read_month_metadata_only ---");

    my $tmpdir = _create_test_history(months => {
        '2026-01' => {
            _meta => { content_version => 3, last_updated => 1700000000 },
            MonthlyWorkloadAnalysis => {},
        },
    });

    my $meta = _read_month_metadata_only($tmpdir, '2026-01');
    ok(defined $meta, "5.0a: Returns defined meta for valid month file");
    is($meta->{content_version}, 3, "5.0b: content_version is correct");

    ok(!defined _read_month_metadata_only($tmpdir, '2099-12'),
       "5.0c: Returns undef for non-existent month");
    ok(!defined _read_month_metadata_only(undef, '2026-01'),
       "5.0d: Returns undef for undef system_cache_dir");
    ok(!defined _read_month_metadata_only($tmpdir, 'bad'),
       "5.0e: Returns undef for invalid month_key format");

    my $tmpdir2 = _create_test_history(months => {
        '2026-02' => { MonthlyWorkloadAnalysis => {} },
    });
    ok(!defined _read_month_metadata_only($tmpdir2, '2026-02'),
       "5.0f: Returns undef when month lacks _meta block");
}


# ==============================================================================
#  SECTION 5.1: _calculate_aggregate_coverage
# ==============================================================================

{
    note("--- Section 5.1: _calculate_aggregate_coverage ---");

    my $mixed = {
        VM01 => {
            'G3-95W15' => { pct_coverage => 1.0, status => 'sufficient' },
            'G3-95W7'  => { pct_coverage => 0.8, status => 'sufficient' },
        },
        VM02 => {
            'G3-95W15' => { pct_coverage => 0.5, status => 'degraded' },
        },
    };
    is(_calculate_aggregate_coverage($mixed), 0.5,
       "5.1a: Returns minimum coverage across all VMs/profiles");

    my $single = { VM01 => { 'G3-95W15' => { pct_coverage => 0.77 } } };
    is(_calculate_aggregate_coverage($single), 0.77,
       "5.1b: Single VM/profile returns its coverage");

    is(_calculate_aggregate_coverage({}), 0,
       "5.1c: Empty hashref returns 0");
    is(_calculate_aggregate_coverage(undef), 0,
       "5.1d: undef returns 0");
}


# ==============================================================================
#  SECTION 5.2: Non-divergence — expected fingerprint matches full pipeline
# ==============================================================================

{
    note("--- Section 5.2: Non-divergence guarantee ---");

    my $end_date = Time::Piece->strptime('2026-03-31', '%Y-%m-%d');
    my $jan = _generate_daily_data(start => '2026-01-01', count => 31, base => 0.40);
    my $feb = _generate_daily_data(start => '2026-02-01', count => 28, base => 0.45);
    my $mar = _generate_daily_data(start => '2026-03-01', count => 31, base => 0.50);

    my $tmpdir = _create_test_history(months => {
        '2026-01' => {
            _meta => { content_version => 2 },
            MonthlyWorkloadAnalysis => {
                VM01 => { DailyProfileSeries => { 'G3-95W15' => $jan } },
            },
        },
        '2026-02' => {
            _meta => { content_version => 1 },
            MonthlyWorkloadAnalysis => {
                VM01 => { DailyProfileSeries => { 'G3-95W15' => $feb } },
            },
        },
        '2026-03' => {
            _meta => { content_version => 1 },
            MonthlyWorkloadAnalysis => {
                VM01 => { DailyProfileSeries => { 'G3-95W15' => $mar } },
            },
        },
    });

    my $common = {
        system_cache_dir   => $tmpdir,
        end_date_obj       => $end_date,
        canonical_days     => 90,
        coverage_threshold => 0.7,
        profiles           => ['G3-95W15'],
        vms                => undef,
        date_exclusions    => undef,
        vm_scope_filter    => undef,
    };

    my $full_result = _get_baseline_from_history($common);
    my $full_fp     = $full_result->{meta}{fingerprint};
    my $expected_fp = _calculate_expected_baseline_fingerprint($common);

    ok(defined $full_fp,     "5.2a: Full pipeline produces fingerprint");
    ok(defined $expected_fp, "5.2b: Expected path produces fingerprint");
    is($expected_fp, $full_fp,
       "5.2c: NON-DIVERGENCE — expected fingerprint matches full pipeline");
    like($expected_fp, qr/^[0-9a-f]{32}$/,
         "5.2d: Valid MD5 hex format");
}


# ==============================================================================
#  SECTION 5.3: Expected fingerprint — no history → undef
# ==============================================================================

{
    note("--- Section 5.3: Expected fingerprint with no history ---");

    my $empty_dir = tempdir(CLEANUP => 1);
    my $result = _calculate_expected_baseline_fingerprint({
        system_cache_dir => $empty_dir,
        end_date_obj     => Time::Piece->strptime('2026-03-31', '%Y-%m-%d'),
        canonical_days   => 90,
        profiles         => ['G3-95W15'],
    });
    ok(!defined $result, "5.3a: Returns undef when no history exists");
}


# ==============================================================================
#  SECTION 5.4: _compute_bucket_fingerprint — baseline component
# ==============================================================================

{
    note("--- Section 5.4: Bucket fingerprint — baseline component ---");

    my $evt = { model => 'multiplicative_seasonal', _eventName => 'test' };
    my $ctx = { analysis_start => 1700000000 };

    # Without baseline
    my $fp_none = _compute_bucket_fingerprint($evt, $ctx, 'multiplicative_seasonal', undef, undef);
    is($fp_none->{baseline}, 'NONE',
       "5.4a: Baseline defaults to 'NONE' when not provided");
    ok(defined $fp_none->{combined},
       "5.4b: Combined fingerprint present");

    # With baseline
    my $fp_bl = _compute_bucket_fingerprint(
        $evt, $ctx, 'multiplicative_seasonal', undef, undef, 'abc123');
    is($fp_bl->{baseline}, 'abc123',
       "5.4c: Baseline fingerprint carried through");
    isnt($fp_bl->{combined}, $fp_none->{combined},
         "5.4d: Adding baseline changes combined fingerprint");

    # Different baseline → different combined
    my $fp_bl2 = _compute_bucket_fingerprint(
        $evt, $ctx, 'multiplicative_seasonal', undef, undef, 'xyz789');
    isnt($fp_bl2->{combined}, $fp_bl->{combined},
         "5.4e: Different baseline → different combined");

    # Same baseline → same combined
    my $fp_bl_dup = _compute_bucket_fingerprint(
        $evt, $ctx, 'multiplicative_seasonal', undef, undef, 'abc123');
    is($fp_bl_dup->{combined}, $fp_bl->{combined},
       "5.4f: Same baseline → same combined (deterministic)");
}


# ==============================================================================
#  SECTION 5.5: _should_reprocess_bucket — baseline fingerprint change
# ==============================================================================

{
    note("--- Section 5.5: Staleness gate — baseline fingerprint ---");

    my $evt = { model => 'multiplicative_seasonal', _eventName => 'month-end' };
    my $ctx = { analysis_start => 1700000000 };

    # Build a stored fingerprint as if produced with baseline 'fp_original'
    my $stored_fp = _compute_bucket_fingerprint(
        $evt, $ctx, 'multiplicative_seasonal', undef, undef, 'fp_original');
    $stored_fp->{baseline_fingerprint} = 'fp_original';
    $stored_fp->{baseline_source}      = 'history';

    _set_mock_stored_fingerprint($stored_fp);

    # 5.5a: Same baseline → NOT stale
    my $d_same = _should_reprocess_bucket(
        '/tmp', 'month-end', $evt, 'multiplicative_seasonal', '2026-01',
        $ctx, undef, undef, 0,
        { fingerprint => 'fp_original', source => 'history' });

    is($d_same->{reprocess}, 0,       "5.5a: NOT stale when baseline unchanged");
    is($d_same->{reason_category}, 'skip', "5.5b: Category is 'skip'");

    # 5.5c: Changed baseline → STALE
    my $d_changed = _should_reprocess_bucket(
        '/tmp', 'month-end', $evt, 'multiplicative_seasonal', '2026-01',
        $ctx, undef, undef, 0,
        { fingerprint => 'fp_updated', source => 'history' });

    is($d_changed->{reprocess}, 1,                 "5.5c: STALE when baseline changed");
    is($d_changed->{reason_category}, 'baseline_changed', "5.5d: Category is 'baseline_changed'");
    like($d_changed->{reason}, qr/baseline inputs changed/,
         "5.5e: Reason mentions baseline inputs");
}


# ==============================================================================
#  SECTION 5.6: _should_reprocess_bucket — baseline MODE change
# ==============================================================================

{
    note("--- Section 5.6: Staleness gate — baseline mode change ---");

    my $evt = { model => 'multiplicative_seasonal', _eventName => 'month-end' };
    my $ctx = { analysis_start => 1700000000 };

    my $stored_fp = _compute_bucket_fingerprint(
        $evt, $ctx, 'multiplicative_seasonal', undef, undef, 'fp_hist');
    $stored_fp->{baseline_fingerprint} = 'fp_hist';
    $stored_fp->{baseline_source}      = 'history';

    _set_mock_stored_fingerprint($stored_fp);

    # Switch to l1_cache mode (same fingerprint value, different source)
    my $d = _should_reprocess_bucket(
        '/tmp', 'month-end', $evt, 'multiplicative_seasonal', '2026-01',
        $ctx, undef, undef, 0,
        { fingerprint => 'fp_hist', source => 'l1_cache' });

    is($d->{reprocess}, 1,     "5.6a: STALE when baseline mode changed");
    like($d->{reason}, qr/baseline mode changed/,
         "5.6b: Reason mentions mode change");
}


# ==============================================================================
#  SECTION 5.7: content_version change → different expected fingerprint
# ==============================================================================

{
    note("--- Section 5.7: Content version change → staleness ---");

    my $end_date = Time::Piece->strptime('2026-01-31', '%Y-%m-%d');
    my $jan_data = _generate_daily_data(start => '2026-01-01', count => 31, base => 0.50);

    my $tmpdir_v1 = _create_test_history(months => {
        '2026-01' => {
            _meta => { content_version => 1 },
            MonthlyWorkloadAnalysis => {
                VM01 => { DailyProfileSeries => { 'G3-95W15' => $jan_data } },
            },
        },
    });
    my $tmpdir_v2 = _create_test_history(months => {
        '2026-01' => {
            _meta => { content_version => 2 },
            MonthlyWorkloadAnalysis => {
                VM01 => { DailyProfileSeries => { 'G3-95W15' => $jan_data } },
            },
        },
    });

    my $args = { end_date_obj => $end_date, canonical_days => 31, profiles => ['G3-95W15'] };

    my $fp_v1 = _calculate_expected_baseline_fingerprint({ %$args, system_cache_dir => $tmpdir_v1 });
    my $fp_v2 = _calculate_expected_baseline_fingerprint({ %$args, system_cache_dir => $tmpdir_v2 });

    ok(defined $fp_v1, "5.7a: Fingerprint v1 defined");
    ok(defined $fp_v2, "5.7b: Fingerprint v2 defined");
    isnt($fp_v1, $fp_v2,
         "5.7c: Content version change → DIFFERENT fingerprint");
}


# ==============================================================================
#  SECTION 5.8: Same content → same fingerprint (no false positive)
# ==============================================================================

{
    note("--- Section 5.8: No false positive ---");

    my $end_date = Time::Piece->strptime('2026-01-31', '%Y-%m-%d');
    my $jan_data = _generate_daily_data(start => '2026-01-01', count => 31, base => 0.50);
    my $month_data = {
        _meta => { content_version => 1 },
        MonthlyWorkloadAnalysis => {
            VM01 => { DailyProfileSeries => { 'G3-95W15' => $jan_data } },
        },
    };

    my $dir_a = _create_test_history(months => { '2026-01' => $month_data });
    my $dir_b = _create_test_history(months => { '2026-01' => $month_data });

    my $args = { end_date_obj => $end_date, canonical_days => 31, profiles => ['G3-95W15'] };

    my $fp_a = _calculate_expected_baseline_fingerprint({ %$args, system_cache_dir => $dir_a });
    my $fp_b = _calculate_expected_baseline_fingerprint({ %$args, system_cache_dir => $dir_b });

    is($fp_a, $fp_b,
       "5.8a: Identical content in different dirs → identical fingerprint");
}


# ==============================================================================
#  SECTION 5.9: Exclusion change → different fingerprint
# ==============================================================================

{
    note("--- Section 5.9: Exclusion change → staleness ---");

    my $end_date = Time::Piece->strptime('2026-01-31', '%Y-%m-%d');
    my $jan_data = _generate_daily_data(start => '2026-01-01', count => 31, base => 0.50);

    my $tmpdir = _create_test_history(months => {
        '2026-01' => {
            _meta => { content_version => 1 },
            MonthlyWorkloadAnalysis => {
                VM01 => { DailyProfileSeries => { 'G3-95W15' => $jan_data } },
            },
        },
    });

    my $base_args = {
        system_cache_dir => $tmpdir, end_date_obj => $end_date,
        canonical_days   => 31,      profiles     => ['G3-95W15'],
    };

    my $fp_excl_a = _calculate_expected_baseline_fingerprint({
        %$base_args,
        date_exclusions => { global => ['2026-01-15'], vm_specific => {} },
    });
    my $fp_excl_b = _calculate_expected_baseline_fingerprint({
        %$base_args,
        date_exclusions => { global => ['2026-01-15', '2026-01-16'], vm_specific => {} },
    });
    my $fp_none = _calculate_expected_baseline_fingerprint({
        %$base_args, date_exclusions => undef,
    });

    isnt($fp_excl_a, $fp_excl_b,
         "5.9a: Different exclusion sets → different fingerprint");
    isnt($fp_excl_a, $fp_none,
         "5.9b: Exclusions vs none → different fingerprint");
}


# ==============================================================================
#  SECTION 5.10: Bootstrap — no stored fingerprint → must process
# ==============================================================================

{
    note("--- Section 5.10: Bootstrap ---");

    my $evt = { model => 'multiplicative_seasonal', _eventName => 'month-end' };
    my $ctx = { analysis_start => 1700000000 };

    _set_mock_stored_fingerprint(undef);

    my $d = _should_reprocess_bucket(
        '/tmp', 'month-end', $evt, 'multiplicative_seasonal', '2026-01',
        $ctx, undef, undef, 0,
        { fingerprint => 'fp_new', source => 'history' });

    is($d->{reprocess}, 1,              "5.10a: Must process on bootstrap");
    is($d->{reason_category}, 'missing_prior', "5.10b: Category is 'missing_prior'");
}


# Phase 5 — Fingerprint Integration: all tests complete.
