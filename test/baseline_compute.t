#!/usr/bin/env perl
# ==============================================================================
# TEST FILE: t/baseline_compute.t
# PURPOSE:   Unit tests for Phase 4a — Baseline Computation, Coverage
#            Reporting, and Fingerprinting:
#              _compute_profile_set_hash, _compute_vm_set_hash,
#              _compute_months_content_hash, _calculate_baseline_from_stitched,
#              _compute_baseline_fingerprint, _get_baseline_from_history.
#
# USAGE:     perl t/baseline_compute.t
#            prove t/baseline_compute.t
#
# DESIGN:    nFit - History Based Baselining Implementation Plan v1.3
# ==============================================================================
use strict;
use warnings;
use Test::More tests => 51;
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
#  Inline copies of Phase 2+3 dependency functions for isolated testing.
#  These are minimal reproductions — the production code lives in
#  nfit-profile.pl.
# ==============================================================================

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
        next unless ($month_key =~ /^\d{4}-\d{2}$/);
        my $file = "nfit.hist.${month_key}.json";
        my $path = File::Spec->catfile($partition_dir, $file);
        unless (-f $path) { push @{$result{missing}}, $month_key; next; }
        unless (-s $path) { $result{errors}{$month_key} = "Empty"; next; }
        my $json_text = do {
            open my $fh, '<:encoding(utf8)', $path or do {
                $result{errors}{$month_key} = "Cannot open: $!"; next;
            };
            local $/; <$fh>;
        };
        my $chunk = eval { $json_decoder->decode($json_text) };
        if ($@ || !$chunk || ref($chunk) ne 'HASH') {
            $result{errors}{$month_key} = "Decode: " . ($@ || 'not a hash'); next;
        }
        my $payload = exists $chunk->{$month_key}
                    ? $chunk->{$month_key}
                    : $chunk->{(keys %$chunk)[0]};
        $result{months}{$month_key} = $payload;
        push @{$result{loaded}}, $month_key;
    }
    return \%result;
}

sub _expand_date_range {
    my ($start_str, $end_str) = @_;
    my @dates;
    eval {
        my $current = Time::Piece->strptime($start_str, '%Y-%m-%d');
        my $end     = Time::Piece->strptime($end_str, '%Y-%m-%d');
        if ($end < $current) { ($current, $end) = ($end, $current); }
        my $max = 366; my $n = 0;
        while ($current <= $end && $n < $max) {
            push @dates, $current->strftime('%Y-%m-%d');
            $current += ONE_DAY; $n++;
        }
    };
    return @dates;
}

sub _expand_peak_periods_to_dates {
    my ($peak_periods_aref) = @_;
    my %date_set;
    return \%date_set unless ($peak_periods_aref && ref($peak_periods_aref) eq 'ARRAY'
                              && @$peak_periods_aref);
    foreach my $pair (@$peak_periods_aref) {
        next unless (ref($pair) eq 'ARRAY' && scalar(@$pair) >= 2);
        my ($s, $e) = @$pair;
        my $ss = ref($s) ? $s->strftime('%Y-%m-%d') : $s;
        my $es = ref($e) ? $e->strftime('%Y-%m-%d') : $e;
        $date_set{$_} = 1 for _expand_date_range($ss, $es);
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
    my $scd = $args_href->{system_cache_dir};
    my $edo = $args_href->{end_date_obj};
    return \%result unless ($scd && $edo);

    my $lb = $args_href->{lookback_days} // 90;
    $lb = 120 if $lb > 120; $lb = 1 if $lb < 1;

    my %vmf; my %pf;
    if ($args_href->{vms_needed} && ref($args_href->{vms_needed}) eq 'ARRAY'
        && @{$args_href->{vms_needed}}) {
        %vmf = map { $_ => 1 } @{$args_href->{vms_needed}};
    }
    if ($args_href->{profiles_needed} && ref($args_href->{profiles_needed}) eq 'ARRAY'
        && @{$args_href->{profiles_needed}}) {
        %pf = map { $_ => 1 } @{$args_href->{profiles_needed}};
    }

    my $idx = _get_history_month_index($scd);
    my $req = _determine_required_months($edo, $lb, $idx);
    return \%result if (!@$req);

    my $lr = _read_bounded_history_months($scd, $req);
    $result{months_read}    = $lr->{loaded};
    $result{months_missing} = $lr->{missing};

    my $ws = ($edo - (($lb - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $we = $edo->strftime('%Y-%m-%d');
    my ($gmin, $gmax);

    foreach my $mk (@{$lr->{loaded}}) {
        my $md = $lr->{months}{$mk};
        next unless (ref($md) eq 'HASH');
        $result{content_versions}{$mk} =
            (exists $md->{_meta} && ref($md->{_meta}) eq 'HASH')
                ? ($md->{_meta}{content_version} // 0) : 0;
        my $mwa = $md->{MonthlyWorkloadAnalysis};
        next unless (ref($mwa) eq 'HASH');
        foreach my $vm (keys %$mwa) {
            next if (%vmf && !$vmf{$vm});
            my $dps = $mwa->{$vm}{DailyProfileSeries};
            next unless (ref($dps) eq 'HASH');
            foreach my $pr (keys %$dps) {
                next if (%pf && !$pf{$pr});
                my $dv = $dps->{$pr};
                next unless (ref($dv) eq 'HASH');
                foreach my $d (keys %$dv) {
                    next unless ($d =~ /^\d{4}-\d{2}-\d{2}$/);
                    next if ($d lt $ws || $d gt $we);
                    $result{series}{$vm}{$pr}{$d} = $dv->{$d};
                    $gmin = $d if (!defined $gmin || $d lt $gmin);
                    $gmax = $d if (!defined $gmax || $d gt $gmax);
                }
            }
        }
    }
    $result{span_start} = $gmin;
    $result{span_end}   = $gmax;
    return \%result;
}

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
    my $vsc  = $args_href->{vm_scope_filter};
    my $dex  = $args_href->{date_exclusions};
    my $pa   = $args_href->{peak_periods};

    # Stage 1: VM inclusions
    my $inc = ($vsc && ref($vsc) eq 'HASH') ? $vsc->{include} : undef;
    if ($inc && ref($inc) eq 'HASH' && %$inc) {
        my @rm; foreach my $vm (keys %$series) { push @rm, $vm unless $inc->{$vm}; }
        delete $series->{$_} for @rm;
        $stats{vms_excluded} += scalar @rm;
        $stats{vms_included} = scalar keys %$series;
    }
    # Stage 2: VM exclusions
    my $exc = ($vsc && ref($vsc) eq 'HASH') ? $vsc->{exclude} : undef;
    if ($exc && ref($exc) eq 'HASH' && %$exc) {
        my $r = 0;
        foreach my $vm (keys %$series) { if ($exc->{$vm}) { delete $series->{$vm}; $r++; } }
        $stats{vms_excluded} += $r;
    }
    unless (%$series) {
        return { filtered_series => $series, exclusion_stats => \%stats };
    }

    # Build lookup hashes for date exclusions
    my %gd;
    if ($dex && ref($dex) eq 'HASH' && $dex->{global} && ref($dex->{global}) eq 'ARRAY') {
        %gd = map { $_ => 1 } @{$dex->{global}};
    }
    my %vsd;
    if ($dex && ref($dex) eq 'HASH' && $dex->{vm_specific} && ref($dex->{vm_specific}) eq 'HASH') {
        foreach my $vm (keys %{$dex->{vm_specific}}) {
            my $d = $dex->{vm_specific}{$vm};
            $vsd{$vm} = { map { $_ => 1 } @$d } if ref($d) eq 'ARRAY';
        }
    }
    my $pd = _expand_peak_periods_to_dates($pa);
    unless (%gd || %vsd || %$pd) {
        return { filtered_series => $series, exclusion_stats => \%stats };
    }
    # Stages 3-5: Date exclusions with elsif priority chain
    foreach my $vm (keys %$series) {
        my $vp = $series->{$vm}; next unless (ref($vp) eq 'HASH');
        my $tve = $vsd{$vm};
        foreach my $pr (keys %$vp) {
            my $dv = $vp->{$pr}; next unless (ref($dv) eq 'HASH');
            my @del;
            foreach my $d (keys %$dv) {
                if    ($gd{$d})          { push @del, $d; $stats{dates_excluded}{global}++; }
                elsif ($tve && $tve->{$d}) { push @del, $d; $stats{dates_excluded}{vm_specific}++; }
                elsif ($pd->{$d})        { push @del, $d; $stats{dates_excluded}{peak_periods}++; }
            }
            delete $dv->{$_} for @del;
        }
    }
    return { filtered_series => $series, exclusion_stats => \%stats };
}


# ==============================================================================
#  Inline copies of Phase 4a functions under test.
#  These match the spec contracts from the Implementation Plan v1.3.
# ==============================================================================

# --- Hash helpers (spec-defined, verbatim from implementation plan) ---

sub _compute_profile_set_hash {
    my ($profiles_aref) = @_;
    return md5_hex(join('|', sort @$profiles_aref));
}

sub _compute_vm_set_hash {
    my ($vms_aref) = @_;
    return 'ALL' unless ($vms_aref && @$vms_aref);
    return md5_hex(join('|', sort @$vms_aref));
}

sub _compute_months_content_hash {
    my ($content_versions_href) = @_;
    my @parts;
    foreach my $month (sort keys %$content_versions_href) {
        push @parts, "$month:$content_versions_href->{$month}";
    }
    return md5_hex(join('|', @parts));
}

# --- Percentile helper (standard linear interpolation, NIST method) ---

sub _percentile {
    my ($values_aref, $pct) = @_;
    return undef unless ($values_aref && @$values_aref);
    my @sorted = sort { $a <=> $b } @$values_aref;
    my $n = scalar @sorted;
    return $sorted[0] if $n == 1;
    my $rank = ($pct / 100.0) * ($n - 1);
    my $lower = floor($rank);
    my $upper = ceil($rank);
    return $sorted[$lower] if ($lower == $upper);
    my $frac = $rank - $lower;
    return $sorted[$lower] + $frac * ($sorted[$upper] - $sorted[$lower]);
}

# --- Function 4.1: _calculate_baseline_from_stitched ---

sub _calculate_baseline_from_stitched {
    my ($args_href) = @_;

    my %baselines;
    my %coverage;

    my $filtered = ($args_href && ref($args_href) eq 'HASH')
                 ? $args_href->{filtered_series} : {};
    my $canonical_days     = $args_href->{canonical_days}     // 90;
    my $coverage_threshold = $args_href->{coverage_threshold} // 0.7;

    foreach my $vm (keys %$filtered) {
        my $profiles = $filtered->{$vm};
        next unless (ref($profiles) eq 'HASH');

        foreach my $profile (keys %$profiles) {
            my $date_vals = $profiles->{$profile};
            my @values;
            if (ref($date_vals) eq 'HASH') {
                @values = grep { looks_like_number($_) }
                          values %$date_vals;
            }

            my $days_available = scalar @values;
            my $pct_coverage   = ($canonical_days > 0)
                               ? $days_available / $canonical_days : 0;

            if ($days_available == 0) {
                $baselines{$vm}{$profile} = undef;
                $coverage{$vm}{$profile}  = {
                    days_available => 0,
                    pct_coverage   => 0,
                    status         => 'no_data',
                };
            } else {
                my $p95 = _percentile(\@values, 95);
                $baselines{$vm}{$profile} = $p95;

                my $status = ($pct_coverage >= $coverage_threshold)
                           ? 'sufficient' : 'degraded';
                $coverage{$vm}{$profile} = {
                    days_available => $days_available,
                    pct_coverage   => $pct_coverage,
                    status         => $status,
                };
            }
        }
    }

    return { baselines => \%baselines, coverage => \%coverage };
}

# --- Function 4.3: _compute_baseline_fingerprint ---

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
    return md5_hex(join('|', map { $_ // '' } @components));
}

# --- Exclusion and VM scope hash helpers (from existing codebase patterns) ---

sub _compute_exclusion_hash {
    my ($date_exclusions) = @_;
    return md5_hex('') unless ($date_exclusions && ref($date_exclusions) eq 'HASH');
    my @parts;
    push @parts, "G:" . join(",", sort @{$date_exclusions->{global} // []});
    foreach my $vm (sort keys %{$date_exclusions->{vm_specific} // {}}) {
        push @parts, "$vm:" . join(",", sort @{$date_exclusions->{vm_specific}{$vm}});
    }
    return md5_hex(join("|", @parts));
}

sub _compute_vm_scope_hash {
    my ($vm_scope_filter) = @_;
    return md5_hex('NONE') unless ($vm_scope_filter && ref($vm_scope_filter) eq 'HASH');
    my @parts;
    if ($vm_scope_filter->{include} && ref($vm_scope_filter->{include}) eq 'HASH'
        && %{$vm_scope_filter->{include}}) {
        push @parts, "inc:" . join(",", sort keys %{$vm_scope_filter->{include}});
    }
    if ($vm_scope_filter->{exclude} && ref($vm_scope_filter->{exclude}) eq 'HASH'
        && %{$vm_scope_filter->{exclude}}) {
        push @parts, "exc:" . join(",", sort keys %{$vm_scope_filter->{exclude}});
    }
    return md5_hex('NONE') unless @parts;
    return md5_hex(join("|", @parts));
}

# --- Function 4.2: _get_baseline_from_history ---

sub _get_baseline_from_history {
    my ($args_href) = @_;

    my $system_cache_dir   = $args_href->{system_cache_dir};
    my $end_date_obj       = $args_href->{end_date_obj};
    my $canonical_days     = $args_href->{canonical_days}     // 90;
    my $coverage_threshold = $args_href->{coverage_threshold} // 0.7;
    my $required_days      = int($canonical_days * $coverage_threshold);

    # Step 1: Stitch bounded history
    my $stitch_result = _stitch_daily_series_bounded({
        system_cache_dir => $system_cache_dir,
        end_date_obj     => $end_date_obj,
        lookback_days    => $canonical_days,
        profiles_needed  => $args_href->{profiles},
        vms_needed       => $args_href->{vms},
    });

    # Step 2: No history check
    if (!$stitch_result->{months_read} || !@{$stitch_result->{months_read}}) {
        die "No history exists. Run `nfit-profile --update-history` first.\n";
    }

    # Step 3: Apply exclusions
    my $excl_result = _apply_exclusions_to_stitched_series({
        stitched_series => $stitch_result->{series},
        vm_scope_filter => $args_href->{vm_scope_filter},
        date_exclusions => $args_href->{date_exclusions},
        peak_periods    => $args_href->{peak_periods},
    });

    # Step 4: Compute baselines
    my $baseline_result = _calculate_baseline_from_stitched({
        filtered_series    => $excl_result->{filtered_series},
        canonical_days     => $canonical_days,
        coverage_threshold => $coverage_threshold,
    });

    # Step 5: Canonical span (deterministic from inputs, NOT actual data range)
    my $canonical_span_start = ($end_date_obj - (($canonical_days - 1) * ONE_DAY))->strftime('%Y-%m-%d');
    my $canonical_span_end   = $end_date_obj->strftime('%Y-%m-%d');

    # Step 6: Assemble meta with fingerprint components
    my $meta = {
        baseline_source     => 'history',
        canonical_days      => $canonical_days,
        lookback_cap_days   => 120,
        required_days       => $required_days,
        coverage_threshold  => $coverage_threshold,
        span_start          => $canonical_span_start,
        span_end            => $canonical_span_end,
        actual_data_span    => {
            first_date => $stitch_result->{span_start},
            last_date  => $stitch_result->{span_end},
        },
        excluded_dates_hash  => _compute_exclusion_hash($args_href->{date_exclusions}),
        vm_scope_hash        => _compute_vm_scope_hash($args_href->{vm_scope_filter}),
        months_read          => $stitch_result->{months_read},
        months_content_hash  => _compute_months_content_hash($stitch_result->{content_versions}),
        profile_set_hash     => _compute_profile_set_hash($args_href->{profiles} // []),
        vm_set_hash          => _compute_vm_set_hash($args_href->{vms}),
        coverage_by_vm       => $baseline_result->{coverage},
        exclusion_stats      => $excl_result->{exclusion_stats},
    };

    # Step 7: Compute combined fingerprint
    $meta->{fingerprint} = _compute_baseline_fingerprint($meta);

    return {
        baselines => $baseline_result->{baselines},
        meta      => $meta,
    };
}


# ==============================================================================
#  Test helpers
# ==============================================================================

sub _write_mock_partition {
    my ($hist_dir, $month_key, $inner_data) = @_;
    my $path = File::Spec->catfile($hist_dir, "nfit.hist.${month_key}.json");
    open my $fh, '>:encoding(utf8)', $path or die "Cannot write $path: $!";
    print $fh JSON->new->encode({ $month_key => $inner_data });
    close $fh;
}

# Build a history month payload with the standard envelope structure.
sub _build_month_payload {
    my (%args) = @_;
    my $payload = { MonthlyWorkloadAnalysis => {} };
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

# Set up a tmpdir with .nfit.history containing the given month payloads.
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


# ##############################################################################
#
#  TESTS: _compute_profile_set_hash (4.H1–4.H4)
#
# ##############################################################################

# Test 4.H1: Deterministic — same input yields same hash
{
    my $h1 = _compute_profile_set_hash(['G3-95W15', 'P-99W1']);
    my $h2 = _compute_profile_set_hash(['G3-95W15', 'P-99W1']);
    is($h1, $h2, '4.H1: profile_set_hash is deterministic');
}

# Test 4.H2: Order-independent (sorted internally)
{
    my $h1 = _compute_profile_set_hash(['P-99W1', 'G3-95W15']);
    my $h2 = _compute_profile_set_hash(['G3-95W15', 'P-99W1']);
    is($h1, $h2, '4.H2: profile_set_hash is order-independent');
}

# Test 4.H3: Different profiles yield different hash
{
    my $h1 = _compute_profile_set_hash(['G3-95W15']);
    my $h2 = _compute_profile_set_hash(['P-99W1']);
    isnt($h1, $h2, '4.H3: Different profiles yield different hash');
}

# Test 4.H4: Hash is a 32-char hex string (MD5)
{
    my $h = _compute_profile_set_hash(['G3-95W15']);
    like($h, qr/^[0-9a-f]{32}$/, '4.H4: profile_set_hash is 32-char hex');
}


# ##############################################################################
#
#  TESTS: _compute_vm_set_hash (4.H5–4.H7)
#
# ##############################################################################

# Test 4.H5: undef/empty input returns 'ALL'
{
    is(_compute_vm_set_hash(undef), 'ALL', '4.H5a: undef VMs returns ALL');
    is(_compute_vm_set_hash([]),    'ALL', '4.H5b: empty array returns ALL');
}

# Test 4.H6: Specific VMs yield MD5 hash
{
    my $h = _compute_vm_set_hash(['VM01', 'VM02']);
    like($h, qr/^[0-9a-f]{32}$/, '4.H6: Specific VMs yield MD5 hash');
}

# Test 4.H7: Order-independent
{
    my $h1 = _compute_vm_set_hash(['VM02', 'VM01']);
    my $h2 = _compute_vm_set_hash(['VM01', 'VM02']);
    is($h1, $h2, '4.H7: vm_set_hash is order-independent');
}


# ##############################################################################
#
#  TESTS: _compute_months_content_hash (4.H8–4.H10)
#
# ##############################################################################

# Test 4.H8: Deterministic
{
    my $h1 = _compute_months_content_hash({ '2026-01' => 1, '2026-02' => 2 });
    my $h2 = _compute_months_content_hash({ '2026-01' => 1, '2026-02' => 2 });
    is($h1, $h2, '4.H8: months_content_hash is deterministic');
}

# Test 4.H9: Changes when content_version changes
{
    my $h1 = _compute_months_content_hash({ '2026-01' => 1, '2026-02' => 1 });
    my $h2 = _compute_months_content_hash({ '2026-01' => 2, '2026-02' => 1 });
    isnt($h1, $h2, '4.H9: Different content_version yields different hash');
}

# Test 4.H10: Sorted by month key (insertion order irrelevant)
{
    my $h1 = _compute_months_content_hash({ '2026-02' => 1, '2026-01' => 2 });
    my $h2 = _compute_months_content_hash({ '2026-01' => 2, '2026-02' => 1 });
    is($h1, $h2, '4.H10: months_content_hash is key-order independent');
}


# ##############################################################################
#
#  TESTS: _calculate_baseline_from_stitched (4.1–4.4)
#
# ##############################################################################

# Test 4.1: Sufficient coverage — 90 days of data, canonical = 90
{
    my %series;
    my $current = Time::Piece->strptime('2026-01-01', '%Y-%m-%d');
    for my $i (0 .. 89) {
        my $d = $current->strftime('%Y-%m-%d');
        $series{VM01}{'G3-95W15'}{$d} = 1.0 + ($i * 0.01);  # 1.00 .. 1.89
        $current += ONE_DAY;
    }

    my $r = _calculate_baseline_from_stitched({
        filtered_series    => \%series,
        canonical_days     => 90,
        coverage_threshold => 0.7,
    });

    ok(defined $r->{baselines}{VM01}{'G3-95W15'},
        '4.1a: P95 baseline defined for sufficient coverage');
    # P95 of 1.00..1.89 (90 values): rank = 0.95 * 89 = 84.55
    # sorted[84] = 1.84, sorted[85] = 1.85 → 1.84 + 0.55*0.01 = 1.8455
    my $p95 = $r->{baselines}{VM01}{'G3-95W15'};
    ok($p95 > 1.8 && $p95 < 1.9,
        '4.1b: P95 value in expected range (1.8–1.9)');
    is($r->{coverage}{VM01}{'G3-95W15'}{status}, 'sufficient',
        '4.1c: Coverage status is sufficient');
    is($r->{coverage}{VM01}{'G3-95W15'}{days_available}, 90,
        '4.1d: days_available = 90');
    cmp_ok($r->{coverage}{VM01}{'G3-95W15'}{pct_coverage}, '==', 1.0,
        '4.1e: pct_coverage = 1.0');
}

# Test 4.2: Degraded coverage — 45 days of data, canonical = 90
{
    my %series;
    my $current = Time::Piece->strptime('2026-01-01', '%Y-%m-%d');
    for my $i (0 .. 44) {
        my $d = $current->strftime('%Y-%m-%d');
        $series{VM01}{'G3-95W15'}{$d} = 2.0 + ($i * 0.02);
        $current += ONE_DAY;
    }

    my $r = _calculate_baseline_from_stitched({
        filtered_series    => \%series,
        canonical_days     => 90,
        coverage_threshold => 0.7,
    });

    ok(defined $r->{baselines}{VM01}{'G3-95W15'},
        '4.2a: P95 baseline still computed for degraded coverage');
    is($r->{coverage}{VM01}{'G3-95W15'}{status}, 'degraded',
        '4.2b: Coverage status is degraded');
    is($r->{coverage}{VM01}{'G3-95W15'}{days_available}, 45,
        '4.2c: days_available = 45');
    cmp_ok($r->{coverage}{VM01}{'G3-95W15'}{pct_coverage}, '==', 0.5,
        '4.2d: pct_coverage = 0.5');
}

# Test 4.3: No data — 0 days for VM/profile
{
    my %series = ( 'VM01' => { 'G3-95W15' => {} } );

    my $r = _calculate_baseline_from_stitched({
        filtered_series    => \%series,
        canonical_days     => 90,
        coverage_threshold => 0.7,
    });

    ok(!defined $r->{baselines}{VM01}{'G3-95W15'},
        '4.3a: Baseline is undef for no data');
    is($r->{coverage}{VM01}{'G3-95W15'}{status}, 'no_data',
        '4.3b: Coverage status is no_data');
    is($r->{coverage}{VM01}{'G3-95W15'}{days_available}, 0,
        '4.3c: days_available = 0');
}

# Test 4.4: Per-VM coverage varies — VM01 has 90 days, VM02 has 30 days
{
    my %series;
    my $current = Time::Piece->strptime('2026-01-01', '%Y-%m-%d');
    for my $i (0 .. 89) {
        my $d = $current->strftime('%Y-%m-%d');
        $series{VM01}{'G3-95W15'}{$d} = 1.0;
        $series{VM02}{'G3-95W15'}{$d} = 2.0 if $i < 30;
        $current += ONE_DAY;
    }

    my $r = _calculate_baseline_from_stitched({
        filtered_series    => \%series,
        canonical_days     => 90,
        coverage_threshold => 0.7,
    });

    is($r->{coverage}{VM01}{'G3-95W15'}{status}, 'sufficient',
        '4.4a: VM01 with 90 days is sufficient');
    is($r->{coverage}{VM02}{'G3-95W15'}{status}, 'degraded',
        '4.4b: VM02 with 30 days is degraded');
    is($r->{coverage}{VM02}{'G3-95W15'}{days_available}, 30,
        '4.4c: VM02 days_available = 30');
}


# ##############################################################################
#
#  TESTS: _compute_baseline_fingerprint (4.7–4.9e)
#
# ##############################################################################

# Standard meta block for fingerprint tests
my %base_meta = (
    baseline_source     => 'history',
    canonical_days      => 90,
    lookback_cap_days   => 120,
    required_days       => 63,
    coverage_threshold  => 0.7,
    span_start          => '2025-11-03',
    span_end            => '2026-01-31',
    excluded_dates_hash => md5_hex('G:'),
    vm_scope_hash       => md5_hex('NONE'),
    months_content_hash => md5_hex('2026-01:1'),
    profile_set_hash    => md5_hex('G3-95W15'),
    vm_set_hash         => 'ALL',
);

# Test 4.7: Deterministic — same meta twice yields identical fingerprint
{
    my $fp1 = _compute_baseline_fingerprint(\%base_meta);
    my $fp2 = _compute_baseline_fingerprint(\%base_meta);
    is($fp1, $fp2, '4.7a: Fingerprint is deterministic');
    like($fp1, qr/^[0-9a-f]{32}$/, '4.7b: Fingerprint is 32-char MD5 hex');
}

# Test 4.8: Changes when months_content_hash changes (content_version update)
{
    my %modified = %base_meta;
    $modified{months_content_hash} = md5_hex('2026-01:2');  # Version bumped
    my $fp_orig = _compute_baseline_fingerprint(\%base_meta);
    my $fp_mod  = _compute_baseline_fingerprint(\%modified);
    isnt($fp_orig, $fp_mod,
        '4.8: Fingerprint changes when content_version changes');
}

# Test 4.9: Changes when exclusions change
{
    my %modified = %base_meta;
    $modified{excluded_dates_hash} = md5_hex('G:2026-01-15');
    my $fp_orig = _compute_baseline_fingerprint(\%base_meta);
    my $fp_mod  = _compute_baseline_fingerprint(\%modified);
    isnt($fp_orig, $fp_mod,
        '4.9a: Fingerprint changes when exclusions change');
}

# Test 4.9b: Changes when vm_scope_hash changes
{
    my %modified = %base_meta;
    $modified{vm_scope_hash} = md5_hex('inc:VM01');
    my $fp_orig = _compute_baseline_fingerprint(\%base_meta);
    my $fp_mod  = _compute_baseline_fingerprint(\%modified);
    isnt($fp_orig, $fp_mod,
        '4.9b: Fingerprint changes when vm_scope changes');
}

# Test 4.9c: Changes when profile_set_hash changes
{
    my %modified = %base_meta;
    $modified{profile_set_hash} = md5_hex('P-99W1');
    my $fp_orig = _compute_baseline_fingerprint(\%base_meta);
    my $fp_mod  = _compute_baseline_fingerprint(\%modified);
    isnt($fp_orig, $fp_mod,
        '4.9c: Fingerprint changes when profile set changes');
}

# Test 4.9d: Changes when vm_set_hash changes
{
    my %modified = %base_meta;
    $modified{vm_set_hash} = md5_hex('VM01|VM02');
    my $fp_orig = _compute_baseline_fingerprint(\%base_meta);
    my $fp_mod  = _compute_baseline_fingerprint(\%modified);
    isnt($fp_orig, $fp_mod,
        '4.9d: Fingerprint changes when vm_set changes');
}

# Test 4.9e: Changes when canonical span changes
{
    my %modified = %base_meta;
    $modified{span_start} = '2025-11-04';  # One day later
    my $fp_orig = _compute_baseline_fingerprint(\%base_meta);
    my $fp_mod  = _compute_baseline_fingerprint(\%modified);
    isnt($fp_orig, $fp_mod,
        '4.9e: Fingerprint changes when canonical span changes');
}


# ##############################################################################
#
#  TESTS: _get_baseline_from_history — full pipeline (4.5, 4.6, 4.10)
#
# ##############################################################################

# Test 4.5: Full pipeline — mock history with known values
{
    # Build 31-day January with known linear series: 1.0, 1.1, ... 4.0
    my %jan_vms;
    my $current = Time::Piece->strptime('2026-01-01', '%Y-%m-%d');
    for my $i (0 .. 30) {
        my $d = $current->strftime('%Y-%m-%d');
        $jan_vms{'VM01'}{'G3-95W15'}{$d} = 1.0 + ($i * 0.1);
        $current += ONE_DAY;
    }

    my $jan = _build_month_payload(
        content_version => 1,
        vms => \%jan_vms,
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    my $r = _get_baseline_from_history({
        system_cache_dir   => $tmpdir,
        end_date_obj       => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        canonical_days     => 31,
        coverage_threshold => 0.7,
        profiles           => ['G3-95W15'],
    });

    # Baselines
    ok(defined $r->{baselines}{VM01}{'G3-95W15'},
        '4.5a: Baseline computed');
    my $b = $r->{baselines}{VM01}{'G3-95W15'};
    # P95 of 1.0,1.1,...,4.0 (31 values): rank = 0.95*30 = 28.5
    # sorted[28]=3.8, sorted[29]=3.9 → 3.8 + 0.5*0.1 = 3.85
    ok($b > 3.8 && $b < 3.9, '4.5b: P95 value in expected range (3.8–3.9)');

    # Meta structure
    is($r->{meta}{baseline_source}, 'history',
        '4.5c: baseline_source = history');
    is($r->{meta}{canonical_days}, 31,
        '4.5d: canonical_days preserved');
    is($r->{meta}{required_days}, int(31 * 0.7),
        '4.5e: required_days derived correctly (int(31*0.7) = 21)');

    # Fingerprint
    ok(defined $r->{meta}{fingerprint},
        '4.5f: Fingerprint present');
    like($r->{meta}{fingerprint}, qr/^[0-9a-f]{32}$/,
        '4.5g: Fingerprint is valid MD5 hex');

    # Months metadata
    is_deeply($r->{meta}{months_read}, ['2026-01'],
        '4.5h: months_read correct');

    # Span — canonical, not actual
    is($r->{meta}{span_end}, '2026-01-31',
        '4.5i: span_end is canonical window end');

    # Hash components present
    like($r->{meta}{profile_set_hash}, qr/^[0-9a-f]{32}$/,
        '4.5j: profile_set_hash computed');
    is($r->{meta}{vm_set_hash}, 'ALL',
        '4.5k: vm_set_hash is ALL when no VM filter');
}

# Test 4.6: No history exists — dies with proper message
{
    my $tmpdir = tempdir(CLEANUP => 1);
    # No .nfit.history directory at all

    eval {
        _get_baseline_from_history({
            system_cache_dir   => $tmpdir,
            end_date_obj       => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
            canonical_days     => 90,
            profiles           => ['G3-95W15'],
        });
    };
    like($@, qr/No history exists/,
        '4.6: Dies with "No history exists" when no history files');
}

# Test 4.10: Fingerprint uses canonical span, not actual data span
{
    # History has only Jan 15–31 (17 days), but canonical = 90 ending Jan 31
    my %jan_vms;
    my $current = Time::Piece->strptime('2026-01-15', '%Y-%m-%d');
    for my $i (0 .. 16) {
        my $d = $current->strftime('%Y-%m-%d');
        $jan_vms{'VM01'}{'G3-95W15'}{$d} = 5.0;
        $current += ONE_DAY;
    }

    my $jan = _build_month_payload(
        content_version => 1,
        vms => \%jan_vms,
    );
    my $tmpdir = _setup_test_history('2026-01' => $jan);

    my $r = _get_baseline_from_history({
        system_cache_dir   => $tmpdir,
        end_date_obj       => Time::Piece->strptime('2026-01-31', '%Y-%m-%d'),
        canonical_days     => 90,
        coverage_threshold => 0.7,   # 17/90 = 0.189 < 0.7 → degraded
        profiles           => ['G3-95W15'],
    });

    # Canonical span: 90 days ending Jan 31 → start = Nov 03 (Jan 31 - 89 days)
    is($r->{meta}{span_start}, '2025-11-03',
        '4.10a: span_start is canonical window start (Nov 03), not actual data');
    is($r->{meta}{span_end}, '2026-01-31',
        '4.10b: span_end is canonical Jan 31');

    # Actual data span is different — captured for audit
    is($r->{meta}{actual_data_span}{first_date}, '2026-01-15',
        '4.10c: actual first_date reflects real data (Jan 15)');
    is($r->{meta}{actual_data_span}{last_date}, '2026-01-31',
        '4.10d: actual last_date is Jan 31');

    # Coverage should be degraded (17/90 ≈ 0.189)
    is($r->{meta}{coverage_by_vm}{VM01}{'G3-95W15'}{status}, 'degraded',
        '4.10e: Coverage status is degraded with sparse data');
}
