# nFit Walkthrough Guide

> [!Note] This guide offers a detailed, practical interpretation of CPU right-sizing recommendations generated by `nfit-profile`, using a common execution command and set of analysis profiles. 

It aims to provide deeper insights into how configuration choices translate into actionable sizing intelligence for IBM Power systems. It assumes an analysis period of approximately 90 days of 1-minute interval NMON data.

## `nfit-profile` Execution Context at a Glance

The way you invoke `nfit-profile` sets the stage for the entire analysis. Here's a summary of a typical command and its primary effects:

| `nfit-profile` Option / Setting    | Value/Presence                        | Primary Effect & Implication for `nfit` Interaction                                                                                                                                                                                                                                                                                 |
| ---------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Data Source**                    | ~90 days, 1-min granularity           | Provides a statistically rich dataset, enabling detailed analysis of daily, weekly, and partial quarterly patterns, with high fidelity for capturing short-duration peak loads.                                                                                                                                                     |
| **`--nfit-enable-windowed-decay`** | Present                               | **Activates `nfit`'s sophisticated two-stage analysis:** Metrics are first calculated within smaller sub-windows (e.g., weekly), and these sub-window results are then aggregated with recency weighting to produce the final profile values.                                                                                       |
| `nfit`: `--process-window-unit`    | `weeks` (default from `nfit-profile`) | Instructs `nfit` (when its windowed decay is active) to process your 90-day data in **1-week chunks** (sub-windows) for its Stage 1 analysis.                                                                                                                                                                                       |
| `nfit`: `--process-window-size`    | `1` (default from `nfit-profile`)     | Works with the unit above to define the 1-week sub-window size.                                                                                                                                                                                                                                                                     |
| `nfit`: `--decay-half-life-days`   | `30` (default from `nfit-profile`)    | Controls the recency weighting for `nfit`'s Stage 2 aggregation. With a 30-day half-life over 90 days of data, the most recent month's weekly results heavily influence final figures, while earlier data provides historical context with diminishing weight.                                                                      |
| **`--match-runq-perc-to-profile`** | Present                               | For `nfit-profile`'s CPU Upsizing (Additive CPU) logic, it attempts to use an Absolute Run-Queue percentile from `nfit` that matches the profile's primary CPU percentile (e.g., `AbsRunQ_P98` for a P98 profile), making adjustments more context-specific. Falls back to `AbsRunQ_P90` if the matched percentile isn't available. |
| **`-config <configfile>`**         | Present                               | Crucial for providing VM metadata (SMT, MaxCPU, Serial, Pool, Entitlement) which `nfit-profile` uses for its global pressure assessment, detailed adjustments, and contextual logging.                                                                                                                                              |
| **`-pc physc.csv -rq rq.csv`**     | Present                               | Specifies the NMON-derived input data files for Physical CPU consumption and system Run-Queue statistics.                                                                                                                                                                                                                           |

---

## `nfit`'s Core Calculation Engine: The Two-Stage Process (Recap)

When `nfit-profile` uses `--nfit-enable-windowed-decay`, `nfit` employs this powerful two-stage analysis for each profile:

**Stage 1: Per-Sub-Window Analysis (e.g., Weekly Granularity)**
For each 1-week chunk of your 90-day data:
1.  **Rolling Averages (PhysC & Run-Queue)**: `nfit` calculates rolling averages of the 1-minute data points.
    - The profile's `-w <minutes>` flag defines the statistical window for these averages (e.g., 1-minute, 5-minute, 15-minute).
    - For EMA (`--avg-method ema` or `--runq-avg-method ema`), the profile's `--decay <level>` or `--runq-decay <level>` determines the EMA's alpha (reactivity). A "high" decay (alpha ~0.15, significant influence from ~12 prior 1-min points) is more responsive to immediate changes within that week than "medium" (alpha ~0.08, ~24 points) or "low" (alpha ~0.03, ~65 points).
2.  **Filtering (`--filter-above-perc X`)**: These *weekly rolling averages* are then filtered. Only the values at or above the Xth percentile of that week's rolling averages are kept. For example, `--filter-above-perc 30` keeps the top 70% of the calculated rolling averages for that week, focusing on busier periods.
3.  **Percentile Calculation (`-p Y`)**: The Yth percentile (e.g., P99.75) is calculated from this filtered, higher-activity set of rolling averages *for that week*. This yields a "weekly P_Y" value.
4.  Equivalent steps occur for all requested Run-Queue metrics (Absolute and Normalised).

**Stage 2: Recency-Weighted Aggregation of Weekly Results**
The weekly P_Y values (and weekly Run-Queue metrics) from Stage 1 are then combined into a single final value for the profile. This uses an exponential decay based on the `--decay-half-life-days` (e.g., 30 days) where more recent weeks contribute more significantly to the final result. This final, recency-weighted value is what `nfit` passes to `nfit-profile`.

---

## Deep Dive: Interpreting Your Specific Profile Strategy

This section provides insights into what each profile in a typical configuration practically measures and its value in right-sizing.

### `P-99W1`: Gauging Recent Absolute Peak Capacity
- **Objective**: To identify the CPU capacity required to service the most intense, very recent, short-duration peak demands.
- **Key Configuration & Combined Impact**:
    - **`-p 99.75 -w 1 --avg-method sma`**: Within each week, `nfit` looks at 1-minute Simple Moving Averages (effectively the raw 1-minute data points). After filtering to keep only the SMAs >= P30 of all SMAs that week, it finds the 99.75th percentile. This value represents the CPU level needed to cover all but the top 0.25% of these busiest 1-minute intervals in that week.
    - **`--decay high` (for PhysC EMA - though SMA is used here), `--runq-decay very-high` (for Run-Queue EMA)**: While `--decay high` doesn't affect the PhysC SMA, the `--runq-decay very-high` (alpha ~0.30) makes the *weekly Run-Queue EMA* extremely sensitive to the very latest (last ~5-6) 1-minute queue readings within that week.
    - **Overall Recency**: The final `P-99W1` value (and its Run-Queue metrics) are then heavily weighted towards the results from the most recent weeks (due to the 30-day half-life aggregation).
    - **`runq_modifier_behavior = additive_only`**: This profile's calculated CPU value is treated as a critical baseline for peak demand; `nfit-profile` will not attempt to reduce it via its CPU Downsizing logic. It can only be increased if its own Run-Queue metrics show significant pressure.
- **Practical Value & Insights**:
    - `P-99W1` is your most sensitive indicator of current peak stress. A high value here, especially if trending up across multiple `nfit-profile` reports, indicates rapidly growing peak demand or recent contention.
    - It directly drives all three components of `nfit-profile`'s **Global Sizing Hint Pressure** (MaxCPU Limit, Absolute Run-Queue Pressure, Normalised Workload Pressure).
    - A high `P-99W1` value, when contrasted with lower values from longer-window profiles like `G3-95W15`, signifies a workload with sharp, short peaks rather than sustained high load. This might lead to investigating the cause of these brief peaks.

### `G` (General Purpose) & `O` (Online) Profiles: A Tiered View of Demand

These profile families provide a spectrum of demand views, from very high percentiles (x1) to more common high loads (x4). The `O` profiles focus this analysis on business hours (typically Mon-Fri, excluding weekends, based on `nfit`'s `-online` and `-no-weekends` interpretation).

#### `G1-99W5` & `O1-99W5`: High Percentile, Peak-Focused
- **Objective**: Capture near-peak demand sustained over slightly longer intervals (5 minutes) than `P-99W1`, with a strong emphasis on recent data.
- **Key Configuration & Combined Impact**:
    - **`-p 99 -w 5 --filter-above-perc 30`**: Within each week, this calculates the P99 of 5-minute rolling averages, after keeping only those averages >= P30 of all 5-minute averages that week.
    - **Decay Levels**: High decay settings ensure that within each weekly analysis, the rolling EMAs (if used for PhysC, and definitely for Run-Queue) are highly reactive to recent changes.
    - **`additive_only`**: These profiles are also treated as critical demand levels, protected from CPU Downsizing.
- **Practical Value & Insights**:
    - Provides a robust metric for critical systems that must handle high loads sustained for several minutes.
    - Comparing `G1` (24x7) with `O1` (online hours) helps quantify how much of this near-peak demand is concentrated within business hours.

#### `G2-98W10` & `O2-98W10`: Very High Sustained Load
- **Objective**: To size for consistently high operational loads that persist for longer periods (10 minutes).
- **Key Configuration & Combined Impact**:
    - **`-p 98 -w 10 --filter-above-perc 25`**: Weekly: P98 of 10-minute rolling averages (after keeping those >= P25).
    - **Eligibility for Downsizing**: Unlike the tier 1 profiles, these **can be downsized** by `nfit-profile`'s logic if the `P-99W1` profile shows no global Run-Queue distress AND their own recency-weighted P50 Normalised Run-Queue is very low.
- **Practical Value & Insights**:
    - These are often good sizing targets for important production systems.
    - If `O2` is significantly downsized, it means that even during its busy 10-minute online periods, the median queue per active CPU was still very low (as per the final recency-weighted P50 NormRunQ), indicating potential over-provisioning for typical "very busy" online states.

#### `G3-95W15` & `O3-95W15`: Busy Baseline Load
- **Objective**: To establish a reliable CPU level for typical busy operational periods, smoothing out shorter spikes.
- **Key Configuration & Combined Impact**:
    - **`-p 95 -w 15 --filter-above-perc 20`**: Weekly: P95 of 15-minute rolling averages (from the top 80% of those averages).
    - **`--decay medium --runq-decay medium`**: Moderate reactivity for EMAs within weekly calculations.
- **Practical Value & Insights**:
    - These are often primary candidates for baseline sizing for many production VMs. The 15-minute statistical window focuses on more sustained demand.
    - Their eligibility for full downsizing and upsizing adjustments makes them highly responsive to `nfit-profile`'s right-sizing intelligence.

#### `G4-90W15` & `O4-90W15`: Standard High Load
- **Objective**: Represents a common, sustained high-load scenario rather than extreme peaks.
- **Key Configuration & Combined Impact**:
    - **`-p 90 -w 15 --filter-above-perc 15`**: Weekly: P90 of 15-minute rolling averages (from the top 85%).
    - **`G4` settings**: Often use EMA for PhysC and a `low` decay for Run-Queue, making its Run-Queue analysis very smooth and less reactive to transient spikes within the week.
- **Practical Value & Insights**:
    - Excellent for understanding the CPU needed for the bulk of high-demand periods. `G4`'s use of EMA for PhysC and low decay for Run-Queue makes it a particularly stable, smoothed baseline.

### `B` (Batch) Profiles: Safeguarding Off-Peak Processing
- **Objective**: To ensure sufficient capacity for critical off-peak batch workloads.
- **Key Configuration & Combined Impact**:
    - **`runq_modifier_behavior = additive_only`**: This is their defining characteristic. `nfit-profile` will **never** reduce their calculated CPU values based on the downsizing logic. This protects the batch window sizing from being wrongly reduced based on low daytime activity.
    - **Filters**: They use `-batch` to instruct `nfit` to focus its analysis only on the overnight batch window.

---

> [!Tip] Holistic Review
> Always use these nFit-Profile recommendations as a strong, data-driven starting point. Combine them with your knowledge of the application, business cycles, upcoming projects, and existing service level objectives. The Rationale Log and this guide are designed to give you transparency into how the numbers were derived, empowering your final decision.
