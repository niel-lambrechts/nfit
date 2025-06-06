# Script: nfit-profile

> [!Summary]
> **nFit Profile (`nfit-profile`)** is a high-level orchestration script that automates multiple runs of the `nfit` analyser and then applies its own sophisticated, Run-Queue (RunQ) driven sizing logic to refine the results. It uses predefined "profiles" to generate a comprehensive CSV report, provides heuristic hints about workload patterns and CPU pressure, and produces a detailed Rationale Log explaining every step of its calculations.

## Overview
- **Description:** `nfit-profile` orchestrates the `nfit` script, running it for each profile defined in an external configuration file (`nfit.profiles.cfg`). After collecting the initial CPU and Run-Queue metrics from these `nfit` runs, `nfit-profile` applies its own multi-stage adjustment logic. This logic includes "CPU Downsizing" for efficiency, "CPU Upsizing" to handle pressure (featuring "Hot Thread Workload Dampening" and safety caps), and final "Maximum CPU Sizing Sanity Checks". The result is a single, unified CSV report containing the final, adjusted CPU recommendations for each profile, enriched with VM configuration data and sizing hints.
- **Language:** Perl
- **Primary Input:**
    - NMON data CSV files for PhysC and RunQ (passed to underlying `nfit` calls).
    - `nfit.profiles.cfg`: INI file defining the analysis profiles and their `nfit` parameters.
    - Optional: `config-all.csv` (VM configuration details like SMT, Entitlement, MaxCPU).
- **Key Output:**
    - A single CSV formatted table to STDOUT, with VMs as rows and the final adjusted recommendation for each profile as columns.
    - A comprehensive, human-readable **Rationale Log** (`/tmp/nfit-profile.log`) detailing the step-by-step calculations for every VM and profile.
    - Status messages to STDERR.

## Purpose

The main purpose of `nfit-profile` is to automate and elevate the sizing process beyond simple percentile calculations. It transforms the initial metrics from `nfit` into context-aware, highly refined CPU recommendations suitable for production capacity planning.

Key functions include:
- **Profile-Based Analysis:** Executes `nfit` for each defined profile, allowing for tailored analysis of different workload characteristics (e.g., Online, Batch, Peak).
- **Advanced RunQ-Driven Adjustments:** This is the core intelligence of `nfit-profile`. It refines each profile's initial CPU value by:
    - **Applying CPU Downsizing (Efficiency Assessment):** Cautiously reduces CPU for profiles that exhibit signs of being over-provisioned relative to their low Run-Queue behaviour.
    - **Applying CPU Upsizing (Additive Logic):** Intelligently adds CPU for profiles that show evidence of CPU saturation or high workload intensity, using advanced heuristics to handle different types of pressure.
    - **Dampening for Constrained Workloads:** Employs "Hot Thread Workload (HTW) Dampening" using Run-Queue volatility (IQRC) and other signals to prevent excessive upsizing for workloads that may not benefit from more cores (e.g., single-threaded applications).
    - **Applying Safety Caps:** Enforces hard limits on CPU additions to ensure stability and prevent extreme recommendations.
    - **Enforcing Sanity Checks:** Ensures final recommendations are plausible relative to the LPAR's configured maximum CPU capacity.
- **Configuration Data Merging:** Enriches the analysis and output with static VM configuration data (e.g., serial number, system type, current entitlement, MaxCPU).
- **Heuristic Sizing Hints:** Generates "Hint", "Pattern", and "Pressure" columns to provide quick insights into workload type, peakiness, and potential CPU resource pressure.
- **Transparent and Auditable Logging:** Produces a detailed Rationale Log that explains exactly how a final recommendation was derived, from initial inputs to every intermediate adjustment, cap, and dampening factor applied.
- **Consolidated CSV Output:** Aggregates all final metrics and hints into a single CSV file, structured for easy use in spreadsheet-based capacity planning models.

## Key Output Columns (in the generated CSV)
- **VM:** The Virtual Machine name.
- **TIER:** A blank column intended for manual user input (e.g., "O1", "B2") in their master spreadsheet, which can drive the selection of the final recommended entitlement from the profile columns.
- **Hint:** A heuristic suggestion for a sizing tier (e.g., "O1", "G2/3") based on workload pattern and pressure.
- **Pattern:** A descriptor of the workload shape (e.g., "Steady", "Very Peaky", "VIO Server").
- **Pressure:** "True" or "False", indicating if the VM showed signs of pressure during its peak ("P-99W1") operational context.
- **PressureDetail:** A summary of the specific reasons a VM was flagged for pressure (e.g., "MaxCPU, RunQNorm_P-99W1(P90=2.5)").
- **SMT, Serial, SystemType, Pool Name, Pool ID:** From the optional VM configuration CSV.
- **Peak:** The absolute highest `PhysC` value from `nfit`.
- **Profile Columns (e.g., P-99W1, G1-99W5, ...):** The **final, fully adjusted** CPU recommendation values from `nfit-profile` for each defined profile.
- **Current - ENT:** Current entitlement from the optional VM configuration CSV.
- **NFIT - ENT, NETT, NETT%:** Columns containing dynamic Excel formulas for use in a planning spreadsheet, facilitating comparison between current and recommended entitlements.

Refer to the script's help output (`nfit-profile -h`) for detailed command-line options and configuration file locations.

## Dependencies
- Perl 5.x
- Core Perl Modules: `Getopt::Long`, `Cwd`, `File::Basename`, `Time::Piece`, `List::Util`, `IO::File`, `version`.
- The `nfit` script (must be executable and findable by path or via `--nfit-path`). A recent version of `nfit` (e.g., v2.29+) is recommended to support all advanced features like windowed decay and growth prediction.
