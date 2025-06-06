# nfit

> [!Summary]
> **nFit (`nfit`)** is a command-line Perl script that serves as the core analysis engine for AIX and Linux on Power right-sizing. It analyses historical NMON performance data (`PhysC` and `RunQ`) to calculate CPU entitlement recommendations based on user-defined statistical criteria. Its advanced capabilities include multiple data smoothing methods (SMA and EMA), a two-stage windowed recency decay analysis, and predictive growth modelling.

## Overview
- **Description:** `nfit` analyses NMON `PhysC` (Physical CPU consumed) and `RunQ` (Run-Queue) data exported to CSV format. It can operate in two primary modes: a standard analysis over an entire dataset, or a more sophisticated **windowed recency decay** mode. In its analysis, it smooths out volatile data using rolling averages (SMA or EMA) and then calculates specified percentiles of these smoothed values. It can also identify absolute peak consumption and includes extensive data filtering options.
- **Language:** Perl
- **Primary Input:** CSV file(s) containing NMON `PhysC` data and, optionally, `RunQ` data over time for multiple VMs.
- **Key Output:** Prints to STDOUT the calculated metrics for each VM processed. The output is a single line per VM in a key-value format, for example: `VMName: P95=Value Peak=Value NormRunQ_P50=Value AbsRunQ_P90=Value GrowthAdj=Value GrowthAdjAbs=Value`. Status and error messages are printed to STDERR.

## Purpose

The `nfit` script is the foundational analysis engine of the nFit suite. Its primary purpose is to transform raw, high-granularity time-series performance data into single, statistically relevant metrics that can inform CPU entitlement decisions. While it can be used standalone for specific queries, it is typically invoked multiple times with varying parameters by the `nfit-profile` wrapper script to generate a comprehensive set of metrics for each VM.

By allowing users to specify parameters such as:
- **Analysis Mode:** Standard analysis or advanced `--enable-windowed-decay` and `--enable-growth-prediction` modes.
- **Smoothing (`--avg-method`, `-w`, `--decay`):** To define how sustained load is measured and how responsive the analysis is to recent changes.
- **Percentile (`-p`):** To set the statistical target for sizing (e.g., P95 reflects sizing for 95% of sustained load periods).
- **Filtering (`-s`, `-startt`, `-online`, `--filter-above-perc`):** To focus the analysis on the most relevant data segments.
- **Peak Calculation (`-k`):** To identify absolute maximum resource needs.
- **Rounding (`-r`, `-u`):** To align outputs with hardware quantums.

`nfit` enables capacity planners to derive consistent, data-driven sizing figures that match different service tier requirements and workload characteristics.

## Key Functionality

### Data Sources
- **`--physc-data`**: The primary input file containing `PhysC` (Physical CPU consumed) data.
- **`--runq-data`**: An optional input file containing `RunQ` (Run-Queue) data.

### Data Smoothing Methods
`nfit` first smooths raw data to mitigate noise. This is applied before percentile calculations.
- **Simple Moving Average (SMA)** (`--avg-method sma`): Calculates a simple arithmetic average over a fixed window (`-w <minutes>`). It gives equal weight to all points in the window and provides stable but less responsive smoothing.
- **Exponential Moving Average (EMA)** (`--avg-method ema`): Calculates a weighted average that gives exponentially more importance to recent data points. It is more responsive to new trends. Its reactivity is controlled by the `--decay <level>` parameter (e.g., `low`, `medium`, `high`), which sets the smoothing constant (alpha). The `-w` parameter serves as an initial "priming" period for the EMA calculation.
- **Independent Run-Queue Smoothing**: The Run-Queue data can be smoothed independently using `--runq-avg-method` and `--runq-decay` if desired.

### Core Calculation Modes

1.  **Standard Mode (Default)**
    - In this mode, `nfit` applies the smoothing and filtering over the *entire* specified date/time range as a single continuous block of data. It produces one final set of metrics representing that whole period.

2.  **Windowed Recency Decay Mode (`--enable-windowed-decay`)**
    - This advanced mode employs a powerful two-stage process for a more time-sensitive analysis:
        - **Stage 1: Per-Sub-Window Analysis**: `nfit` first divides the total analysis period (e.g., 90 days) into smaller, sequential "sub-windows" (typically configured as 1-week chunks via `--process-window-unit` and `--process-window-size`). All calculations (smoothing, filtering, percentiles) are performed *independently within each sub-window*. This results in a set of metrics for each week (e.g., Week 1 P99, Week 2 P99, etc.).
        - **Stage 2: Recency-Weighted Aggregation**: `nfit` then aggregates these individual sub-window results into a single final value. This aggregation is intelligently weighted using a decay half-life (`--decay-half-life-days`). Results from more recent weeks contribute significantly more to the final metric than results from older weeks, making the output highly reflective of the most current performance trends.

### Predictive Growth Modelling (`--enable-growth-prediction`)

- **Concept:** Building upon the Windowed Recency Decay mode, this feature analyses the trend of the per-sub-window metrics (e.g., the P99 values from each week).
- **Calculation:** It uses linear regression to find the growth slope (e.g., cores per week) and projects this trend into the future over a specified period (`--growth-projection-days`).
- **Inflation & Capping:** The resulting `Projected Increase` is added to the recency-weighted baseline. This inflation is capped by a percentage (`--max-growth-inflation-percent`) to prevent excessive recommendations from overly aggressive trends.
- **Safeguards:** Growth prediction is automatically skipped if the historical data is too short, too volatile (based on Coefficient of Variation), or if the growth trend is flat or negative.

### Filtering and Data Selection
`nfit` supports extensive data filtering to focus the analysis:
-   **Date/Time:** by start date (`-s`), time-of-day (manual with `-startt`/`-endt` or shortcuts `-online`/`-batch`), and weekend exclusion (`-no-weekends`).
-   **Statistical Filtering (`--filter-above-perc`)**: This crucial filter is applied *after* data smoothing. It removes the lower portion of the smoothed data (e.g., the bottom 20%) before the final percentile is calculated. This focuses the sizing calculation on periods of more significant activity, preventing idle or low-activity periods from skewing the result downwards.

### Output Metrics
The script can produce a rich set of metrics for each VM on a single line:
-   **`PXX=Value`**: The primary CPU percentile metric (e.g., P95, P99.75) calculated from the (smoothed and filtered) data. If growth prediction was active, this value is the growth-inflated result.
-   **`Peak=Value`**: The single highest instantaneous `PhysC` value (`-k` flag).
-   **`NormRunQ_PXX=Value`**: Calculated percentiles of Normalised Run-Queue.
-   **`AbsRunQ_PXX=Value`**: Calculated percentiles of Absolute Run-Queue.
-   **`GrowthAdj=Value`**: The final, capped amount of CPU that was added by the growth prediction logic.
-   **`GrowthAdjAbs=Value`**: The initial, *uncapped* amount of CPU predicted by the growth trend before capping was applied.

Refer to the script's help output (`nfit -h`) for a complete list of command-line options.

## Dependencies
- Perl 5.x
- Core Perl Modules: `Getopt::Long`, `File::Temp`, `List::Util`, `POSIX`, `Time::Piece`, `version`.
