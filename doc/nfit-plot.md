# Script: nfit-plot

> [!Summary]
> **nFit Plot (`nfit-plot`)** is a Python script that generates stacked bar charts. It visualises Data Centre and per-frame CPU capacity, allocations, and headroom against frame evacuation reserve targets for multiple user-defined scenarios, with configurations loaded from external INI files.

## Overview
- **Description:** This Python script creates a series of clear, visual representations of CPU capacity utilisation and headroom within an IBM Power environment, typically composed of multiple frames (managed systems). It takes configuration data for the physical frames (from `nfit.mgsys.cfg`) and various LPAR entitlement scenarios (from `nfit.scenarios.cfg`) as input. For each scenario, it generates a DC-Wide Summary Chart and Per-Frame Detail Charts.
- **Language:** Python 3
- **Primary Input:**
    - `nfit.mgsys.cfg`: INI file defining the frame infrastructure (number of frames, cores/VIOs per frame) and global `dc_name`.
    - `nfit.scenarios.cfg`: INI file defining various LPAR entitlement scenarios, with per-frame LPAR entitlement values for each scenario.
- **Key Output:** A set of PNG image files (one DC summary chart and multiple per-frame charts for *each* scenario) saved to the `/tmp/` directory. Status messages are printed to the console.

## Purpose

The `nfit-plot` script aims to provide management-friendly visuals that clearly communicate the impact of different right-sizing or HA standby adjustment scenarios on overall capacity and the ability to meet critical reserve requirements, such as frame evacuation.

Key functions include:
- **External Configuration:** Frame infrastructure and scenario data (LPAR entitlements per frame) are loaded from easy-to-edit INI configuration files (`nfit.mgsys.cfg`, `nfit.scenarios.cfg`).
- **DC-Wide Summary Visualisation:** For each scenario, it generates a chart showing:
    - Total DC capacity.
    - Total VIO server allocation.
    - Total LPAR entitlements for the current scenario.
    - Resulting available DC headroom.
    - This headroom is compared against a "Frame Evacuation Target" (dynamically calculated as the LPAR CPU load of the single busiest frame in that scenario) to indicate overall DC readiness.
- **Per-Frame Detail Visualisation:** For each scenario, and for each frame, it generates a chart showing:
    - Individual frame capacity, VIO allocation, and LPAR entitlement.
    - Available headroom on that frame.
    - This frame-specific headroom is compared against its "Evacuation Contribution Target" (its proportional share of capacity needed to absorb the busiest frame's LPARs) to indicate if it's contributing sufficiently to DC resilience.
- **Clear Surplus/Deficit Indication:** Each chart explicitly states if there is a SURPLUS or DEFICIT against the relevant evacuation reserve target.

This tool helps in presenting complex capacity scenarios in an accessible visual format, supporting strategic discussions about resource allocation and risk management.

## Configuration Files

1.  **`nfit.mgsys.cfg`:**
    * Defines the physical frame infrastructure and global Data Centre name.
    * Expected in `./etc/` or script directory (overridable by `--mgsys-config`).
    * Format:
      ```ini
      [GLOBAL]
      dc_name = My Production Data Centre

      [Frame 1]
      name = P10_CEC1
      total_cores = 120
      vio_allocation = 16.0
      ; ... more frames ...
      ```

2.  **`nfit.scenarios.cfg`:**
    * Defines various LPAR entitlement scenarios.
    * Expected in `./etc/` or script directory (overridable by `--scenarios-config`).
    * Format:
      ```ini
      [Scenario 1 - As Is]
      name = 1. As-Is Entitlement
      lpar_entitlements_per_frame = 80.0, 85.5, 90.0, 88.25, 87.0
      filename_suffix = as_is
      ; ... more scenarios ...
      ```
    * The number of values in `lpar_entitlements_per_frame` must match the number of frames defined in `nfit.mgsys.cfg`.

Refer to the script's help output (`nfit-plot --help`) for detailed command-line options.

## Dependencies
- Python 3
- `matplotlib` library
- `numpy` library
  (Install via: `pip install matplotlib numpy`)
