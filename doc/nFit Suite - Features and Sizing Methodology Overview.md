# nFit Suite - Features and Sizing Methodology Overview

> [!Summary]
> The nFit Suite, comprising `nfit` (the core analysis engine) and `nfit-profile` (the orchestration and adjustment layer), provides powerful, data-driven insights for optimising your IBM Power Systems virtual machine (VM) and LPAR sizing. 

By meticulously analysing NMON performance data for Physical CPU (PhysC) consumption and Run-Queue (RunQ) behaviour, the nFit Suite delivers nuanced recommendations to help you right-size your environment, ensuring robust performance while identifying key efficiencies. 

This document highlights the advanced capabilities designed to empower your capacity planning process.

## Key Capabilities of the nFit Suite

### **Advanced Time-Weighted Analysis: Windowed Recency Decay**
  - Understanding that recent performance is often the most indicative predictor of future needs, the nFit Suite employs a sophisticated **Windowed Recency Decay** methodology when `nfit-profile` activates this feature in `nfit` (via its `--nfit-enable-windowed-decay` option).
  - **How it practically works (The Two-Stage Process in `nfit`):**
    1.  **Stage 1: Per-Processing-Sub-Window Analysis:** `nfit` first segments the total analysis period (e.g., 90 days of NMON data, after any global date filters from `nfit-profile`) into smaller, sequential "processing sub-windows." `nfit-profile` typically instructs `nfit` to use **1-week sub-windows** by default (via `nfit` parameters like `--process-window-unit weeks` and `--process-window-size 1`). Within each of these sub-windows, `nfit` calculates all profile-specific metrics, including rolling averages, filtered percentiles for PhysC, and Run-Queue statistics (as detailed further below).
    2.  **Stage 2: Recency-Weighted Aggregation of Sub-Window Results:** Once metrics are derived for each sub-window, `nfit` aggregates these individual sub-window results (e.g., the P99 PhysC value from week 1, week 2, etc.) into a single, final value for that profile metric. This aggregation is intelligently weighted to give significantly more importance to data from more recent sub-windows. This weighting is governed by `nfit`'s `--decay-half-life-days <N>` parameter (e.g., a default of 30 days passed from `nfit-profile`).
        - _Benefit for Planners:_ This two-stage approach ensures that final recommendations are highly responsive to the latest operational trends (due to the half-life weighting of recent sub-window results) while still being stabilised by considering a broader historical context. For example, with a 30-day half-life, a weekly result from 30 days prior to the analysis reference date will contribute approximately half as much to the final aggregated metric as a result from the most current week.

### **Sophisticated Data Smoothing & Peak Characterisation within Sub-Windows**
  - Raw performance data, especially at high granularity like 1-minute intervals, can be highly variable. `nfit` offers robust data smoothing options (Simple Moving Average - SMA, or Exponential Moving Average - EMA) for PhysC and Run-Queue data. This smoothing is applied *within each processing sub-window* (e.g., weekly) before percentile calculations, helping to reveal underlying trends for a more stable analysis.

  - **Defining Meaningful Peaks: The "Smoothed Value Percentile" Approach**
    - To accurately capture significant high-demand periods while avoiding overreaction to fleeting, instantaneous noise, `nfit` calculates percentiles from a series of *rolling averages* (either SMA or EMA, as specified by the profile).
        1.  **Intelligent Smoothing for Clarity:** Within each sub-window, 1-minute performance data (PhysC or Run-Queue) is first processed into a series of rolling averages. The profile's `-w <minutes>` flag dictates the window for SMA or the conceptual priming period for EMA.
        2.  **Focusing on Significant Highs (Filtering):** The profile's `--filter-above-perc X` flag then refines this series of rolling averages. `nfit` calculates the Xth percentile *value* from all rolling averages in the sub-window and keeps only those rolling average data points that are **at or above** this value. This focuses the analysis on the busier periods identified by the rolling averages within that sub-window.
        3.  **Percentile of Filtered Averages:** Finally, `nfit` calculates the target high percentile (e.g., P98, P99.75 specified by `-p`) from this *filtered set of higher-activity rolling averages*.
    - **Benefit for Planners:** This "rolling average -> filter -> percentile" method, applied within each sub-window, ensures that recommendations are based on figures that reflect genuinely demanding operational states that have some persistence (as captured by the rolling average window) and significance (by surviving the filter), rather than being skewed by isolated raw data spikes or overly influenced by periods of low activity.

  - **Tunable EMA Responsiveness (for Rolling Averages within Sub-Windows):**
    - When EMA is selected for PhysC (via a profile's `--avg-method ema --decay <level>`) or Run-Queue (via `--runq-avg-method ema --runq-decay <level>`), the `<level>` flag (`low`, `medium`, `high`, `very-high`, `extreme`) precisely controls the EMA's alpha (smoothing factor). This determines how quickly the rolling EMA reacts to new 1-minute data points *within the current sub-window's analysis*.
        - A `low` decay (alpha 0.03) results in a very smooth EMA, with its value being substantially influenced by roughly the last **65-66 minutes** of 1-minute interval activity.
        - A `medium` decay (alpha 0.08) is shaped by approximately the last **24 minutes**.
        - A `high` decay (alpha 0.15) by the last **12-13 minutes**.
        - A `very-high` decay (alpha 0.30) by the last **5-6 minutes**.
        - An `extreme` decay (alpha 0.40) by only the last **~4 minutes**.
    - _Benefit for Planners:_ This allows precise tuning of how "nervous" or "stable" the view of performance is within each weekly chunk, before these weekly views are themselves aggregated with longer-term recency weighting.

### **Contextual Run-Queue Analysis: Per-Profile Metrics**
  - `nfit-profile` orchestrates `nfit` to perform multiple analysis runs, each defined by a distinct "profile" in `nfit.profiles.cfg` (e.g., Peak Demand, Online Business Hours, Overnight Batch). Each profile applies its own specific data selection filters (like time-of-day via `-online` or `-batch`) and the detailed analysis parameters described above (averaging windows, decay levels, filters, percentiles).
  - **Tailored Run-Queue Metrics:** As a result, key Run-Queue metrics are calculated by `nfit` reflecting the precise data view and smoothing settings of each individual profile. These metrics are first derived per sub-window and then aggregated with recency weighting by `nfit` before being passed to `nfit-profile`. They include:
      - **P90 Absolute Run-Queue**: The 90th percentile of total threads running or ready to run.
      - **P50 & P90 Normalised Run-Queue**: The median and 90th percentile of queue length relative to the number of *active* logical CPUs.
      - **P25 & P75 Normalised Run-Queue**: Used by `nfit-profile` to calculate the IQRC.
      - **NormRunQ IQRC (Interquartile Range Coefficient)**: A key new metric calculated by `nfit-profile` as `(P75 - P25) / P50`. It is a robust statistical measure of the Normalised Run-Queue's volatility or "burstiness" that is less sensitive to extreme outliers than standard deviation. It is a critical input to the Hot Thread Workload heuristic.
  - **Benefit:** This ensures that CPU adjustments and pressure assessments performed by `nfit-profile` for a specific profile (e.g., a batch profile) are driven by Run-Queue behaviour observed *only* during batch hours and with batch-appropriate smoothing and recency considerations, leading to highly relevant and accurate recommendations.

### **Intelligent VM Sizing Recommendations by `nfit-profile`**
  - `nfit-profile` synthesises the recency-weighted metrics from these multiple profile runs (as calculated by `nfit`) to provide clear, actionable insights for each VM.

  - **Comprehensive Global Pressure Detection & Hints:**
    - `nfit-profile` automatically identifies if a VM is under overall CPU pressure by primarily examining the metrics from the mandatory **`P-99W1` (Peak) profile**.
    - **Detailed Pressure Insights (`PressureDetail`):** The nature of this global hint pressure is specified:
        - **MaxCPU Pressure:** Detects if the `P-99W1` profile's final (recency-weighted) PhysC value is at or very near (e.g., >= 98% of) the LPAR's configured MaxCPU limit.
        - **Absolute Run-Queue Pressure (P-99W1 based):** Assesses if `P-99W1`'s final (recency-weighted) P90 Absolute Run-Queue is high relative to the LPAR's total logical CPU capacity (MaxCPU * SMT), using a configurable saturation threshold (typically 1.8).
        - **Normalised Workload Pressure (P-99W1 based):** Evaluates if `P-99W1`'s final (recency-weighted) P90 Normalised Run-Queue exceeds a set threshold (typically 2.0), but *only if* `P-99W1`'s P90 Absolute Run-Queue (also recency-weighted) is substantial enough (e.g., at least SMT-level).
        - **Pool Indication:** Notes if a non-default processor pool is used if any other pressure types are detected, as a contextual factor.
  - **Workload Pattern Recognition & Sizing Tier (`Hint` Column):** Based on comparisons between different profile families (Online, Batch, General) and the overall pressure assessment, a primary workload pattern and a consolidated sizing tier (e.g., G3, O1) are suggested in the CSV output.

### **Refined RunQ-Driven CPU Adjustments by `nfit-profile`**
  - `nfit-profile` intelligently adjusts the initial (recency-weighted) CPU recommendations from `nfit` for each profile based on Run-Queue behaviour specific to *that profile's own final recency-weighted Run-Queue metrics*:

  - **CPU Downsizing (Efficiency Adjustment):**
    - This logic may reduce a profile's CPU recommendation if its final (recency-weighted) typical (median) Normalised CPU queue (`NormRunQ P50`) is very low, indicating potential over-provisioning for that profile's specific load characteristic. This is applied cautiously and is disabled by several safety guards, such as evidence of any Run-Queue distress on the VM's main `P-99W1` peak profile.

  - **Contextual CPU Upsizing (Additive Logic):**
    - If significant Run-Queue Saturation or Normalised Workload Intensity is detected for a profile (using *that profile's own final recency-weighted* Run-Queue metrics), `nfit-profile` calculates an "Additive CPU" amount. This considers "Excess Threads" above a tolerated Run-Queue level and applies caps based on VM entitlement and other confidence factors.

  - **Specialised Heuristic: Hot Thread Workload (HTW) Dampening:**
    - **Purpose**: This advanced heuristic prevents excessive CPU upsizing for workloads that appear to be constrained by factors other than raw core count (e.g., single-threaded applications). Such workloads may show a high Run-Queue on the few CPUs they use, but cannot benefit from more cores.
    - **Detection**: It identifies these workloads by looking for a combination of signals for the profile being analysed, including: high P90 *and* P50 Normalised Run-Queue, high Run-Queue variability (a high **IQRC**), low overall CPU usage relative to configured capacity (Entitlement or MaxCPU), and a lack of saturation at the full LPAR level.
    - **Action**: If a Hot Thread Workload is detected, a **dynamic dampening factor** is applied to significantly reduce the calculated additive CPU. The dampening is more aggressive if CPU utilisation is very low or if the IQRC indicates highly erratic behaviour.

  - **Final Safeguard: Enhanced Safety Caps on Additive CPU:**
    - **Purpose**: Provides an ultimate hard limit on the amount of CPU that can be added by the upsizing logic, preventing any single calculation from producing an extreme result.
    - **The Rule**: The final additive CPU amount for any profile is capped at the *minimum* of two values:
        1. A small, fixed absolute value (e.g., **0.5 cores**).
        2. A multiple of the profile's initial Base PhysC (e.g., **200% of the Base PhysC**).
    - This provides a sensible boundary for both very small and larger VMs.

  - **LPAR MaxCPU Capping:**
    - The final recommended CPU for any profile, after all adjustments, is capped by a forecast based on the LPAR's configured MaxCPU and its current entitlement, using a dynamic multiplier that allows more relative headroom for smaller LPARs.

### **Powerful Core Analysis Engine (`nfit` features leveraged by `nfit-profile`)**
  - The underlying `nfit` script provides robust data processing capabilities that `nfit-profile` configures for each profile run:
  - **Flexible Data Selection:** Profiles can specify time-of-day filters (e.g., `nfit`'s `-online`, `-batch`) and weekend exclusion (`-no-weekends`).
  - **`--filter-above-perc X` (Focus on Busy Periods within Sub-Windows):** This `nfit` option ensures that percentile calculations *within each sub-window* are performed on a dataset of rolling CPU averages that represent more significant activity.
  - **Granular Averaging & Decay Control for Sub-Window Smoothing:** Profile flags give `nfit` fine-grained control over SMA or EMA methods and independent, tunable EMA decay levels for PhysC and Run-Queue.
  - **Customisable Run-Queue Percentiles:** Profiles instruct `nfit` to calculate specific sets of absolute and normalised Run-Queue percentiles, providing rich data for `nfit-profile`'s adjustment logic.

### **Transparent Decision Making: The Rationale Log**
  - With each `nfit-profile` run, a comprehensive log file (default: `/tmp/nfit-profile.log`) is generated.
  - **Practical Insight:** This log meticulously details the "story" behind each recommendation with a new, planner-friendly structure including top and bottom summaries. It includes:
      - Global settings and a "Global Sizing Hint Pressure Assessment" section for each VM.
      - For every VM and *each of its sizing profiles*, a step-by-step breakdown of how the final CPU value was derived. This covers all inputs, intermediate calculations, and the specific reasons for decisions made by `nfit-profile`'s CPU Downsizing, CPU Upsizing (including HTW and Safety Cap application), and final sanity checks.
  - **Benefit:** Offers complete transparency, enabling planners to understand, validate, trust, and if necessary, query the basis for the sizing recommendations.

## Conclusion

> [!Summary] The nFit Suite offers a sophisticated, highly configurable, and transparent framework for VM and LPAR capacity planning on IBM Power Systems. 
> By leveraging detailed NMON data and applying advanced analytical techniques—including multi-stage windowed processing with dual-level recency weighting, nuanced data smoothing, and context-aware Run-Queue adjustments with specialised heuristics like Hot Thread Workload Dampening—nFit empowers capacity planners to make more informed decisions, optimise resource utilisation, and confidently ensure application performance.
