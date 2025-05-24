# Script: nfit-profile

> [!Summary]
> **nFit Profile (`nfit-profile`)** is a Perl wrapper script that automates multiple runs of the `nfit` analyser. It uses a set of predefined "profiles" (combinations of `nfit` parameters) to generate a comprehensive CSV report of various sizing metrics for all VMs, suitable for spreadsheet-based capacity planning and tier-based right-sizing.

## Overview
- **Description:** `nfit-profile` orchestrates the `nfit` script. It reads profile definitions from an external INI configuration file (`nfit.profiles.cfg`), where each profile specifies a set of `nfit` flags (e.g., for different percentiles, window sizes, time filters). It runs `nfit` for each defined profile against the NMON data. Additionally, it runs `nfit` once with the `-k` flag to determine the absolute peak CPU usage. The script can optionally merge data from a VM configuration CSV file (`config-all.csv`) to enrich the output. A key feature is the generation of a "Hint" column suggesting workload pattern, potential tiering, and CPU pressure. The final output is a single CSV table written to STDOUT, with VMs as rows and the results of each profile (plus peak and config data) as columns.
- **Language:** Perl
- **Primary Input:**
    - NMON data CSV file (passed to underlying `nfit` calls).
    - `nfit.profiles.cfg`: INI file defining the set of `nfit` runs to perform.
    - Optional: `config-all.csv` (VM configuration details).
- **Key Output:** A single CSV formatted table to STDOUT. Status messages to STDERR.

## Purpose

The main purpose of `nfit-profile` is to automate the generation of a wide range of potential sizing metrics for each VM, corresponding to different right-sizing strategies or service tiers. This avoids the manual effort of running `nfit` numerous times with different arguments.

Key functions include:
- **Profile-Based Analysis:** Executes `nfit` for each profile defined in `nfit.profiles.cfg`, allowing for tailored sets of parameters (percentile, window, time filters like `-online`, `-batch`, `-no-weekends`, and `--filter-above-perc`).
- **Absolute Peak Calculation:** Ensures the absolute peak CPU usage is always calculated and included in the report.
- **Configuration Data Merging:** Optionally incorporates static VM configuration data (e.g., serial number, system type, current entitlement, max CPU) into the report from a separate CSV file.
- **Heuristic Sizing Hints:** Generates "Hint", "Pattern", and "Pressure" columns to provide quick insights into likely workload type (Online/Batch/General), CPU usage shape (Peaky/Steady), and potential CPU resource pressure (based on P-99W1 vs maxCPU). This includes special handling for VIO servers.
- **Consolidated CSV Output:** Aggregates all calculated metrics and hints into a single CSV file, structured for easy import and use in spreadsheet-based capacity planning models where specific metrics can be selected based on VM type/tier.

## Key Output Columns (in the generated CSV)
- **VM:** The Virtual Machine name.
- **Hint:** A speculative suggestion for Pattern + Adjusted Tier (e.g., "O1", "G2/3", "P" for VIOs).
- **Type:** A blank column intended for manual user input (e.g., "O1", "B2") in their master spreadsheet, which drives the selection of the final recommended entitlement.
- **Pattern:** A descriptor of the workload shape (e.g., "Steady", "Very Peaky", "Moderately Peaky Check Duration", "VIO Server").
- **Pressure:** "True" or "False", indicating if the VM frequently operated near its `maxcpu` limit (based on P-99W1).
- **Serial, SystemType, Pool Name, Pool ID:** From the optional VM configuration CSV.
- **Peak:** The absolute highest 1-minute `PhysC` value from `nfit -k`.
- **Profile Columns (e.g., P-99W1, G1-99W5, ... B4-90W15):** Values calculated by `nfit` for each defined profile.
- **Current - ENT:** Current entitlement from the optional VM configuration CSV.
- **NFIT - ENT, NETT, NETT%:** Blank columns for user formulas in their spreadsheet.

Refer to the script's help output (`nfit-profile -h`) for detailed command-line options and configuration file locations.

## Dependencies
- Perl 5.x
- The `nfit` script (must be executable and findable by path or via `--nfit-path`).
