# nFit Advanced Sizing Logic - A Deep Dive

> [!Note] This document provides a detailed, step-by-step explanation of the advanced sizing adjustments performed by the `nfit-profile` tool. It is intended to be a technical companion to the Rationale Log, helping capacity planners and system administrators understand the precise methodology behind each calculated CPU recommendation.

---

## Overview of the Adjustment Workflow

The `nfit-profile` tool refines initial CPU sizing values through a sequential, multi-stage process. The starting point for each profile is the **Initial Base PhysC**, a value calculated by the `nfit` engine which already incorporates any recency-weighted analysis and internal growth predictions. `nfit-profile` then subjects this value to its own adjustment logic to account for specific Run-Queue behaviours and configuration constraints.

The workflow proceeds in the following order, with the output of one stage becoming the input for the next:

1.  **Initial Input**: Starts with the `Initial Base PhysC` from `nfit`.
2.  **Stage B: CPU Downsizing (Efficiency Assessment)**: The `Initial Base PhysC` is evaluated for potential downsizing if the workload appears inefficiently sized relative to its Run-Queue. The result is the `PhysC after Downsizing`.
3.  **Stage C: CPU Upsizing (Additive CPU)**: The `PhysC after Downsizing` is evaluated for signs of CPU pressure. If pressure is detected, an additive CPU amount is calculated and applied, after being subjected to several dampening factors and caps.
4.  **Stage D: Maximum CPU Sizing Sanity Checks**: The final calculated value is capped against a dynamic ceiling based on the LPAR's maximum configured virtual processors (`MaxCPU`) to ensure the recommendation remains practical.

This entire process is documented for each VM and profile in the Rationale Log.

## Key Metrics & Concepts for Run-Queue Adjustments

The sizing logic utilises the following key inputs, derived from the `nfit` run specific to the profile being adjusted and the VM's configuration:

-   **`Initial Base PhysC`**: The starting PhysC value for the profile, as calculated by `nfit`. This metric already includes any growth adjustments applied by `nfit`'s internal prediction engine.
-   **Absolute Run-Queue (Profile Specific)**: Typically the 90th percentile (P90) of raw run-queue counts for the current profile's data view. It quantifies the depth of the CPU work queue.
-   **Normalised Run-Queue (Profile Specific)**: The Absolute Run-Queue divided by the number of *active* logical CPUs (`PhysC_interval * SMT_Used`). This indicates queue length per busy LCPU. `nfit-profile` uses several percentiles:
    -   **`NormRunQ P25, P50, P75`**: Used to calculate the IQRC for volatility assessment.
    -   **`NormRunQ P90`**: A key indicator of workload intensity; high values (e.g., > 2.0) suggest significant queuing per active LCPU.
-   **`NormRunQ IQRC (Interquartile Range Coefficient)`**: Calculated as `(NormRunQ P75 - NormRunQ P25) / NormRunQ P50`. A robust statistical measure of the Normalised Run-Queue's variability or "burstiness."
-   **SMT (Simultaneous Multi-Threading)**: The VM's SMT level.
-   **Current Entitlement & LPAR MaxCPU**: The VM's configured CPU entitlement and maximum virtual processor limit.
-   **P-99W1 Pressure Hints**: VM-level flags indicating if the mandatory "P-99W1" peak profile showed overall Run-Queue pressure. These are critical guards for the CPU Downsizing logic.

---

## Section B: CPU Downsizing (Efficiency Assessment)

This stage evaluates if the `Initial Base PhysC` for the current profile can be cautiously reduced. This is considered if the VM appears over-provisioned for the load represented by this profile, based on its low Run-Queue behaviour.

-   **Objective**: To improve resource efficiency by identifying opportunities for CPU reduction, without compromising performance during genuinely busy periods or for VMs already under stress.
-   **Key Log Terminology**: "CPU Downsizing", "Downsizing Factor", "PhysC after Downsizing".

### Governing Conditions & Safety Guards

`nfit-profile` employs several checks before attempting analytical downsizing. If any of these guards are met, downsizing is skipped, and the `DownsizingFactor` remains 1.0.

1.  **Overall VM Health (P-99W1 Peak Profile Check)**: If the VM's mandatory "P-99W1" peak profile indicates significant Run-Queue distress (either absolute or normalised pressure), **no CPU downsizing is performed for any profile of this VM**. This is a critical VM-level safety override.
2.  **Profile Behaviour Setting**: If the current profile is configured for `"additive_only"` adjustments in `nfit.profiles.cfg`, downsizing is skipped.
3.  **Bursting Above Entitlement**: If the profile's `Initial Base PhysC` is already greater than the VM's `Current Entitlement` (and entitlement > 0), downsizing is skipped to preserve this necessary bursting capability.
4.  **High Existing Constraint**: Downsizing is skipped if the profile's `Initial Base PhysC` is already very close to the `MaxCPU_lpar` (e.g., > 90%) AND this profile's own Absolute Run-Queue also indicates high saturation against the LPAR's maximum capacity.
5.  **Excessive Workload Volatility (P90/P50 NormRunQ Ratio)**: The stability of this profile's Normalised Run-Queue is assessed using the ratio of its P90 to P50 values. If this ratio is very high (e.g., >= 2.5), the queue is considered too spiky for reliable analytical downsizing, so it is skipped.
6.  **Normalised Run-Queue P50 Level (Primary Trigger)**: For analytical downsizing to even be considered, the profile's P50 Normalised Run-Queue (median queue per *active* LCPU) must be below a low threshold (e.g., 0.5).

### Analytical Downsizing Calculation

If no guards prevent it and P50 NormRunQ is low, the following calculation occurs:

1.  A theoretical **"Efficient PhysC Target"** is calculated. This represents the CPU needed to service the profile's Absolute Run-Queue at an "optimal" Normalised Run-Queue level (a target which is SMT-dependent, e.g., 0.60 to 0.80 threads per LCPU).
2.  This theoretical target is **blended** with the observed `Initial Base PhysC`. More weight is given to the Base PhysC unless the P50 Normalised Run-Queue is exceptionally low, ensuring reductions are conservative.
3.  Any potential reduction (`Initial Base PhysC` - Blended Target) is **capped** at a maximum percentage of the Base PhysC (e.g., 15%). This cap itself is dynamically reduced if the P90/P50 Normalised Run-Queue volatility for this profile is moderately high.
4.  The final `Actual_Reduction_Cores` is used to derive the `DownsizingFactor`.

**Result**: The `PhysCAfterDownsizing` value from this stage becomes the input for the next stage.

---

## Section C: CPU Upsizing (Additive CPU)

If Run-Queue metrics indicate pressure, `nfit-profile` may add CPU.

-   **Objective**: To ensure sufficient CPU capacity for queued work, while applying advanced heuristics to avoid over-provisioning for constrained workloads.
-   **Key Log Terminology**: "CPU Upsizing", "Additive CPU", "Hot Thread Workload Dampening".

### Pressure Assessment and Additive Calculation

1.  **Per-Profile Pressure Assessment**: The tool first checks if the current profile's own Run-Queue metrics signal CPU pressure. This involves two main checks:
    -   **Overall LPAR Run-Queue Pressure**: Is the profile's Absolute Run-Queue high relative to the LPAR's total maximum logical CPU capacity (`MaxCPU_lpar * SMT`)?
    -   **Normalised Workload Intensity**: Is the profile's P90 Normalised Run-Queue high (e.g., > 2.0), AND is its Absolute Run-Queue non-trivial (e.g., at least SMT threads)?
2.  **Initial Additive CPU**: If pressure is detected, "Excess Threads" are calculated (Profile's AbsRunQ vs. a "Tolerated Run-Queue" level). This translates to a "Raw Additive CPU," which is then capped by a sliding scale based on `Current_Entitlement`.

### Hot Thread Workload (HTW) Dampening

This is a critical heuristic to avoid oversizing for workloads that cannot benefit from more cores.

-   **Detection**: It looks for a combination of (typically 4 out of 5) signals for the current profile:
    1.  High Normalised Workload Intensity (high P90 NormRunQ).
    2.  Underutilisation of Configured Capacity (low Base PhysC relative to Entitlement or `MaxCPU_lpar`).
    3.  Sustained High Normalised Queue (high P50 NormRunQ).
    4.  No Overall LPAR Run-Queue Saturation (from the P-99W1 assessment).
    5.  High Normalised Run-Queue Variability (high **NormRunQ IQRC**).
-   **Dampening Action**: If HTW is detected, a **dynamic dampening factor** significantly reduces the additive CPU. The dampening is more aggressive if CPU utilisation is low or IQRC is high. The Rationale Log details this check and its outcome.

### Final Additive Adjustments and Safety Caps

The potentially dampened additive CPU is then adjusted by:
-   **Volatility Confidence Factor**: Based on this profile's P90/P50 NormRunQ ratio.
-   **Pool Confidence Factor**: A small reduction if the VM is in a non-default shared pool.
-   **Enhanced Safety Cap**: A final hard cap is applied. The additive CPU cannot exceed the **minimum** of:
    1.  A small absolute value (e.g., **0.5 cores**).
    2.  A multiple of the profile's original Base PhysC (e.g., **200% of Base PhysC**).

**Result**: The `Final_Additive_CPU`, which is added to the `PhysCAfterDownsizing` value.

---

## Section D: Maximum CPU Sizing Sanity Checks

This final stage ensures the recommendation is practical against the LPAR's configured hardware limits.

-   **Objective**: To cap the total recommended CPU at a scaled limit derived from `MaxCPU_lpar`.
-   **Method**: An `Effective_MaxCPU_Sanity_Limit` is calculated as `MaxCPU_lpar * Forecast_Multiplier`. The `Forecast_Multiplier` is dynamic, larger for VMs with smaller entitlements (e.g., 2.5x for entitlements < 0.5 cores) and decreasing for larger entitlements (e.g., to 1.25x).
-   **Outcome**: If the recommendation exceeds this limit, it's capped. This is the final unrounded value.

---

## Key Configuration Parameters and Tuning "Knobs"

The `nfit-profile` script includes internal constants that act as thresholds and factors ("knobs") to fine-tune its sizing logic. These are defined at the top of the `nfit-profile` script. Understanding these allows for tailoring the analysis to specific environmental needs.

### 1. CPU Downsizing (Efficiency Assessment) Parameters:
-   `NORM_P50_THRESHOLD_FOR_EFFICIENCY_CONSIDERATION` (e.g., `0.5`): A profile's P50 Normalised Run-Queue must be *below* this to trigger analytical downsizing.
-   `VOLATILITY_CAUTION_THRESHOLD` (e.g., `2.5`): If a profile's NormRunQ P90/P50 ratio is at or *above* this, downsizing is skipped.
-   `MAX_EFFICIENCY_REDUCTION_PERCENTAGE` (e.g., `0.15` for 15%): The maximum percentage a profile's CPU can be cut by this logic.

### 2. CPU Upsizing (Additive CPU) Parameters:
-   `WORKLOAD_PRESSURE_NORM_P90_TRIGGER_THRESHOLD` (e.g., `2.0`): A profile's P90 Normalised Run-Queue must exceed this to contribute to "Workload Pressure".
-   `RUNQ_PRESSURE_P90_SATURATION_THRESHOLD` (e.g., `1.8`): A profile's Absolute Run-Queue relative to `MaxCPU_lpar` capacity must exceed this to indicate "RunQ Pressure".
-   `RUNQ_ADDITIVE_TOLERANCE_FACTOR` (e.g., `1.8`): The Run-Queue level (as a multiple of current LCPUs) tolerated before "Excess Threads" are calculated. A higher value makes upsizing less aggressive.
-   `MAX_ADD...` constants: Define the sliding scale for capping raw additive CPU based on entitlement.

### 3. Hot Thread Workload (HTW) Dampening Parameters:
-   `HOT_THREAD_WL_..._FACTOR`, `..._THRESHOLD`, `..._MIN_CONDITIONS_MET`: A set of constants controlling the detection of and reaction to constrained workloads. The most significant are the `HOT_THREAD_WL_HIGH_NORM_P50_THRESHOLD` and `HOT_THREAD_WL_IQRC_THRESHOLD` which define "high" P50 and "high" variability for this heuristic.
-   `HOT_THREAD_WL_BASE_DAMPENING_FACTOR`: The starting point for the dynamic dampening factor calculation.

### 4. Additive CPU Safety Cap Parameters:
-   `ADDITIVE_CPU_SAFETY_CAP_FACTOR_OF_BASE` (e.g., `2.0`): Limits final additive CPU to 200% of the profile's `Initial Base PhysC`.
-   `ADDITIVE_CPU_SAFETY_CAP_ABSOLUTE` (e.g., `0.5` cores): An absolute hard limit on the final additive CPU. The effective cap is the *minimum* of the calculation from these two parameters.

Modifying these parameters requires care, and their impact should be observed in the Rationale Log before being applied broadly.
