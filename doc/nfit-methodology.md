# nFit Suite: Core Methodology and Concepts

This document outlines the core methodology, statistical approaches, and key considerations behind the nFit suite for AIX LPAR right-sizing. It is intended for capacity planners and system administrators who wish to understand the principles applied by the `nfit` and `nfit-profile` tools.

## Core Principles of Right-Sizing with nFit

The nFit suite is built upon the following core principles to ensure robust and reliable entitlement recommendations:

1.  **Tiering is Key:** The fundamental idea of classifying VMs into Tiers (e.g., Tier 1 for highest criticality, Tier 2 for high availability, Tier 3 for general use, Tier 4 for less critical workloads) based on business importance and performance requirements is central. Each tier can then be sized using different statistical aggressiveness.
2.  **Data-Driven Decisions:** All sizing recommendations are derived from historical performance data, specifically the `PhysC` (Physical CPU consumed) metric captured by NMON over a significant period (typically 90 days at 1-minute intervals).
3.  **Statistical Rigour:** The methodology aims to replace subjective "eye-balling" of graphs with reproducible statistical methods (rolling averages, percentiles, peak analysis) that reflect each Tier's objectives.
4.  **Addressing Data Skew:** Raw performance data is often heavily skewed by periods of low or zero activity. The nFit approach accounts for this, particularly by using rolling averages and the `--filter-above-perc` option, to prevent underestimation of resource needs.
5.  **Peak Awareness and Sustained Load:** The methods are sensitive to relevant peak usage patterns (for Tier 1) and sustained load periods (for Tiers 2-4), ensuring that the LPARs can handle demanding periods.
6.  **Automation & Scalability:** The suite is designed for automation (`nfit-profile` wrapper) to handle large numbers of VMs efficiently. Configuration files are used to manage parameters and profiles.

## Key Statistical Concepts Applied

### 1. `PhysC` - The Sizing Metric
-   **Definition:** Physical CPU cores consumed by the LPAR during a measurement interval. This is the actual demand placed on the physical hardware.
-   **Relevance:** CPU entitlement directly guarantees a minimum level of `PhysC`. Therefore, `PhysC` is the correct and most direct metric for entitlement sizing.

### 2. Rolling Averages (`-w` option in `nfit`)
-   **Concept:** Instead of using instantaneous `PhysC` values which can be very spiky, `nfit` calculates a rolling average. For example, a 15-minute window means that at any point, `nfit` considers the average `PhysC` over the preceding 15 minutes.
-   **Purpose:** This smooths out very short-lived spikes (noise) and focuses on the *sustained* CPU load, providing a more stable view of workload intensity. Different window sizes can be chosen to reflect how quickly a system needs to respond or how long its typical busy periods last.

### 3. Percentiles (`-p` option in `nfit`)
-   **Concept:** After calculating rolling averages for the analysis period, `nfit` computes a statistical percentile of these rolling average values.
-   **Meaning (e.g., 95th Percentile - P95):** This identifies the CPU load level (of the rolling averages) that the VM was at or below for 95% of the analysed time intervals. Conversely, the sustained load only exceeded this level during the busiest 5% of the time.
-   **Purpose:** This selects a value representing a high, but usually typical, level of sustained load. It deliberately ignores the most extreme sustained load periods (e.g., top 1-10%, depending on the percentile chosen), making the recommendation robust against rare or anomalous events while still covering the vast majority of demanding periods. Different percentiles (P99, P98, P95, P90) are used to reflect different Tier requirements.

### 4. Filtering Above Percentile (`--filter-above-perc` option in `nfit`)
-   **Concept:** This option refines the percentile calculation. Before the final percentile (e.g., P95) is calculated, `nfit` first determines an intermediate percentile (e.g., P20) of *all* rolling averages. It then discards all rolling average values below this P20 threshold. The final P95 is then calculated *only* from the remaining, higher rolling average values.
-   **Purpose:** This gives more weight to the busier periods of sustained load by removing the "pull-down" effect of lower-activity periods *within* the already filtered (e.g., time-of-day, weekend) dataset. It helps focus the sizing on when the system is genuinely working hard.

### 5. Absolute Peak (`-k` option in `nfit`)
-   **Concept:** Identifies the single highest instantaneous `PhysC` value recorded for the VM during the analysed timeframe (after all date/time/weekend filters are applied).
-   **Purpose:** Essential for sizing Tier 1 systems or any workload that cannot tolerate *any* CPU starvation, even for a single minute.

## Workload Pattern and Time Filtering

-   **Online ("O" Profiles):** Recognising that some systems are primarily active during business hours, these profiles restrict `nfit`'s analysis to these periods (e.g., 08:00-17:00) and also exclude weekend data (`-no-weekends` flag) to avoid dilution from off-peak inactivity.
-   **Batch ("B" Profiles):** Similarly, for systems with primarily overnight workloads, these profiles focus `nfit`'s analysis on those windows (e.g., 18:00-06:00).
-   **General ("G" Profiles):** These profiles consider the full 24-hour cycle for systems with less predictable patterns or continuous activity.

## Hint Generation in `nfit-profile`

The "Hint", "Pattern", and "Pressure" columns provide heuristic suggestions:

1.  **VIO Server Override:** If `systemtype` indicates a "VIO Server", the Hint is set to "P", Pattern to "VIO Server", and Pressure to "False", as VIOs have unique, critical sizing needs.
2.  **Workload Pattern (O/B/G):** Suggested by comparing the `O3-95W15` and `B3-95W15` profile results. A significant dominance of one over the other (using a ratio threshold) suggests "O" or "B"; otherwise, it defaults to "G".
3.  **Shape Descriptor ("Pattern" Column):** A "Peakiness Ratio" (`P-99W1 / G3-95W15`) categorises the load shape as "Steady", "Moderately Peaky", or "Very Peaky". For "B" patterns, " Check Duration" is appended.
4.  **Pressure ("Pressure" Column):** "True" if `P-99W1 >= (maxcpu * 0.98)`, indicating frequent operation near the VM's maximum configured CPU. This uses `P-99W1` for resilience against single outliers.
5.  **Tier Suggestion ("Hint" Column):** An initial Tier range (1/2, 2/3, 3/4) is suggested based on the Shape Descriptor. If Pressure is "True", this range is adjusted upwards by one level (e.g., "2/3" becomes "2"; capped at "1"). This is combined with the Pattern (e.g., "O2", "G3/4").

**Disclaimer:** The hints are data-driven suggestions and should always be validated against business requirements and application knowledge.

## PowerHA Standby Sizing

The `nfit-profile` tool generates metrics for primary nodes. The methodology for standby nodes involves applying a reduction factor (e.g., 50% or 25%) to the primary node's `nFit` recommendation. This acknowledges their typically idle state while ensuring they have a baseline for failover. The risk associated with this reduction is managed by ensuring sufficient overall Data Centre capacity, often accounted for in Frame Evacuation reserve planning, which typically exceeds the PowerHA failover requirements for any single cluster.
