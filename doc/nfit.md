# Script: nfit

> [!Summary]
> **nFit (`nfit`)** is a command-line Perl script for AIX right-sizing. It analyses historical NMON performance data (`PhysC`) to calculate CPU entitlement recommendations based on user-defined statistical criteria, including rolling averages, percentiles, and peak values, with flexible data filtering options.

## Overview
- **Description:** `nfit` analyses NMON `PhysC` (Physical CPU consumed) data exported to CSV format for AIX LPARs. It calculates rolling average CPU utilisation over specified windows and then determines a chosen percentile of these averages. It can also identify the absolute peak CPU consumption. The script offers various filters for date ranges, time-of-day, specific VMs, and weekend exclusion, allowing for targeted analysis. Output can be rounded to specified increments.
- **Language:** Perl
- **Primary Input:** CSV file containing NMON `PhysC` data over time for multiple VMs.
- **Key Output:** Prints to STDOUT the calculated metric (Percentile of Rolling Average, and optionally Peak) for each VM processed, in the format `VMName: PXX=Value Peak=Value`. Status and error messages are printed to STDERR.

## Purpose

The `nfit` script forms the core analysis engine of the nFit suite. Its primary purpose is to take raw time-series CPU consumption data and transform it into a single, statistically relevant value that can be used to inform CPU entitlement decisions for AIX LPARs.

By allowing users to specify parameters such as:
- **Rolling Window (`-w`):** To define the period over which sustained load is measured.
- **Percentile (`-p`):** To set the statistical target for sizing (e.g., P95 means sizing to cover 95% of sustained load periods).
- **Filtering (`-s`, `-startt`, `-endt`, `-online`, `-batch`, `-no-weekends`, `--filter-above-perc`):** To focus the analysis on the most relevant data segments.
- **Peak Calculation (`-k`):** To identify absolute maximum resource needs.
- **Rounding (`-r`, `-u`):** To align outputs with hardware quantums.

`nfit` enables capacity planners to derive consistent, data-driven sizing figures that match different service tier requirements. While it can be used standalone for specific queries, it is typically invoked multiple times with varying parameters by the `nfit-profile` wrapper script to generate a comprehensive set of metrics for each VM.

## Key Functionality (Summary from Readme Block in Script)

- **Analyses historical CPU usage data** (specifically `PhysC` from NMON) for multiple AIX VMs.
- Aims to provide **statistically derived values** reflecting sustained usage and absolute peaks.
- **Calculates a 'rolling average'** of `PhysC` over a user-defined 'window' (e.g., `-w 15` for 15 minutes) to smooth brief spikes and reflect sustained load.
- **Computes a statistical percentile** (e.g., `-p 95` for 95th) from the distribution of these rolling average values.
- Optionally finds the **single highest instantaneous `PhysC` value** (`-k` flag).
- Supports extensive **data filtering:** by start date (`-s`), time-of-day (manual with `-startt`/`-endt` or shortcuts `-online`/`-batch`), weekend exclusion (`-no-weekends`), and a further statistical filter on rolling averages (`--filter-above-perc`).
- Allows **rounding** of results to specified increments (`-r` for nearest, `-u` for ceiling/up).

Refer to the script's help output (`nfit -h`) for detailed command-line options.

## Dependencies
- Perl 5.x
- Core Perl Modules: `Getopt::Long`, `File::Temp`, `List::Util`, `POSIX` (for `ceil`), `Time::Piece`.
