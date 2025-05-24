# nFit: AIX and Linux on Power VM Right-Sizing Toolkit

## Overview

nFit is designed to assist with AIX and Linux on Power LPAR (Logical Partition) CPU right-sizing on IBM Power systems. It leverages historical performance data from NMON (`PhysC` - Physical CPU consumed) to provide data-driven, statistically sound entitlement recommendations. The suite aims to move beyond subjective "eye-balling" of performance graphs, offering a consistent, automatable, and configurable approach to capacity planning.

This suite helps in optimising resource utilisation, ensuring critical workloads have the guaranteed CPU they need, while identifying potential savings by accurately sizing less critical workloads or standby systems.

## Components

The nFit Suite currently consists of three main components:

1.  **`nfit` (Perl):**
    * The core analysis engine.
    * Parses NMON `PhysC` data (exported to CSV).
    * Calculates rolling average CPU utilisation and specified percentiles of these averages.
    * Can identify absolute peak CPU consumption.
    * Offers extensive filtering options: by date range, time-of-day (including "online" and "batch" presets), specific VMs, and exclusion of weekends.
    * Includes an advanced filter (`--filter-above-perc`) to focus percentile calculations on periods of higher sustained load.
    * Provides options for rounding results to align with hardware entitlement increments.
    * Outputs results per VM to STDOUT, status/errors to STDERR.

2.  **`nfit-profile` (Perl):**
    * A wrapper script that automates multiple runs of `nfit`.
    * Reads a set of predefined "profiles" from an INI configuration file (`nfit.profiles.cfg`). Each profile defines a specific combination of `nfit` flags (percentile, window, time filters, etc.).
    * Runs `nfit` for each defined profile.
    * Always runs `nfit` with the `-k` flag to capture absolute peak usage.
    * Optionally merges static VM configuration data (e.g., serial number, system type, current entitlement, max CPU) from another CSV file (`config-all.csv` by default).
    * Generates heuristic "Hint", "Pattern", and "Pressure" columns based on the collected metrics and VM configuration to provide quick insights.
    * Aggregates all results into a single, comprehensive CSV output to STDOUT, suitable for import into spreadsheets for further analysis and decision-making.

3.  **`nfit-plot` (Python):**
    * A visualisation tool to generate capacity charts.
    * Reads frame infrastructure details from `nfit.mgsys.cfg` (number of frames, cores/VIOs per frame, DC name).
    * Reads LPAR entitlement scenarios (per-frame LPAR entitlements for different what-if scenarios) from `nfit.scenarios.cfg`.
    * For each defined scenario, it generates:
        * A DC-Wide Summary Chart: Visualising total capacity, allocations (VIO, LPARs), available headroom, and comparison against the Frame Evacuation Target for the entire Data Centre.
        * Per-Frame Detail Charts: Visualising capacity, allocation, headroom for each individual frame, and its contribution target for frame evacuation.
    * Outputs charts as PNG files to `/tmp/`.

## Core Methodology

The underlying sizing methodology (primarily implemented in `nfit` and orchestrated by `nfit-profile`) is based on:

- **Data Source:** 90 days of 1-minute interval AIX `PhysC` data from NMON.
- **Primary Metrics:**
    - **Absolute Peak:** For highest-tier systems.
    - **Percentiles of Rolling Averages:** For various tiers of sustained load, using different window sizes (W) and percentile targets (P). Common profiles include P99W5, P98W10, P95W15, P90W15.
- **Filtering:** Includes date, time-of-day (with `-online`/`-batch` presets and `-no-weekends` option), and a statistical filter (`--filter-above-perc`) to focus on relevant busy periods.
- **Tiering Concept:** The generated profiles are designed to map to different service tiers, allowing for differentiated sizing based on business criticality.
- **Heuristics (in `nfit-profile`):** Suggests workload pattern (Online/Batch/General), CPU usage shape (Peaky/Steady), and potential CPU pressure (based on P-99W1 vs. maxCPU) to aid planners.

*(For a more detailed explanation of the methodology, see `doc/nfit_methodology.md`)*

## Prerequisites

- **For `nfit` and `nfit-profile` (Perl):**
    - Perl 5.x
    - Core Perl Modules: `Getopt::Long`, `File::Temp`, `List::Util`, `POSIX` (for `ceil`), `Time::Piece`. These are generally standard.
- **For `nfit-plot` (Python):**
    - Python 3.x
    - `matplotlib` library (`pip install matplotlib`)
    - `numpy` library (`pip install numpy`)
    (These can be installed from the `requirements.txt` file: `pip install -r requirements.txt`)

## Setup & Configuration

1.  **Clone the Repository:**
    `git clone <repository_url> nmon-fit`
    `cd nmon-fit`

2.  **Makes Executable:**
    `chmod +x nfit nfit-profile nfit-plot`

3.  **Configuration Files:**
    The scripts look for configuration files by default in an `etc/` subdirectory relative to their location, and then in their own directory as a fallback. You can override these paths using command-line options.
    It is recommended to copy the relevant `.example` files from the `examples/` directory to `etc/`, rename them (remove `.theme.example` or `.default`), and then customise them.

    * **`etc/nfit.profiles.cfg` (for `nfit-profile`):** Defines the set of nFit runs.
        * Format: INI style. Section name is the profile name/CSV column header. Key `nfit_flags` contains the flags for `nfit`.
        * See `examples/nfit.profiles.cfg.*.example` for different templates (mission-critical, general, cost-sensitive).
        * Copy an example: `cp examples/nfit.profiles.cfg.general_enterprise.example etc/nfit.profiles.cfg`
    * **`etc/config-all.csv` (optional, for `nfit-profile`):** Your VM configuration inventory.
        * Format: CSV with headers. Expected columns include `hostname`, `serial`, `systemtype`, `procpool_name`, `procpool_id`, `entitledcpu`, `maxcpu`.
        * See `examples/config-all.csv.example`.
        * Used via the `-config` option in `nfit-profile`.
    * **`etc/nfit.mgsys.cfg` (for `nfit-plot`):** Defines frame infrastructure and DC name.
        * Format: INI style. `[GLOBAL]` section for `dc_name`. Each frame is a section (e.g., `[Frame 1]`) with `name`, `total_cores`, `vio_allocation`.
        * See `examples/nfit.mgsys.cfg.*.example`.
        * Copy an example: `cp examples/nfit.mgsys.cfg.general_enterprise.example etc/nfit.mgsys.cfg`
    * **`etc/nfit.scenarios.cfg` (for `nfit-plot`):** Defines LPAR entitlement scenarios.
        * Format: INI style. Each scenario is a section with `name`, `lpar_entitlements_per_frame` (comma-separated, must match frame count), `filename_suffix`.
        * See `examples/nfit.scenarios.cfg.*.example`.
        * Copy an example: `cp examples/nfit.scenarios.cfg.general_enterprise.example etc/nfit.scenarios.cfg`

4.  **Input NMON Data:**
    * `nfit` (and by extension `nfit-profile`) expects a CSV file containing NMON `PhysC` data. The first column should be a timestamp (e.g., "YYYY-MM-DD HH:MM:SS"), and subsequent columns should be `"VMName PhysC"`.
    * An example snippet is in `examples/nmon_data.csv.example`.

## Basic Usage

**(Ensure scripts are executable and configuration files are in place or paths specified)**

* **`nfit` (Core Analyser):**
    ```bash
    ./nfit -f <nmon_data.csv> -p 95 -w 15 -online -no-weekends
    ./nfit -f <nmon_data.csv> -k -vm myvm01 -s 2025-04-01
    ./nfit -h # For all options
    ```

* **`nfit-profile` (Profile Runner & CSV Generator):**
    ```bash
    # Using default config file locations (./etc/ or ./):
    ./nfit-profile -f <nmon_data.csv> > full_report.csv
    ./nfit-profile -f <nmon_data.csv> -config my_vm_inventory.csv -u > rounded_report.csv
    ./nfit-profile -h # For all options
    ```

* **`nfit-plot` (Visualisation Tool):**
    ```bash
    # Using default config file locations (./etc/ or ./):
    ./nfit-plot
    ./nfit-plot --mgsys-config custom_frames.cfg --scenarios-config custom_scenarios.cfg
    ./nfit-plot -h # For all options
    ```
    *(Charts are saved to ./output/)*

## Output

* **`nfit`:** Prints results to STDOUT in the format `VMName: PXX=Value Peak=Value`. Status messages to STDERR.
* **`nfit-profile`:** Prints a comprehensive CSV table to STDOUT. Status messages to STDERR. Key columns include VM, Hint, Type (user-defined), Pattern, Pressure, config data, Peak, all profile results, Current Entitlement, and blank columns for spreadsheet formulas.
* **`nfit-plot`:** Saves PNG chart images to `./output/`, showing DC-wide and per-frame capacity scenarios. Prints status messages to STDOUT.

## Contributing

Contributions are welcome and encouraged! Whether it’s reporting a bug, suggesting an enhancement, improving documentation, or submitting code — your help makes the project better.

Ways to contribute:
	•	💡 Open a Discussion to ask questions or propose ideas.
	•	🐞 File an Issue for bugs, feature requests, or enhancements.
	•	🔧 Fork the repository and submit a Pull Request (PR) for improvements.
	•	📚 Help improve documentation or usage examples.

Guidelines:
	•	Please ensure your changes are clear, concise, and tested where applicable.
	•	Follow the existing code style and structure.
	•	For significant changes, open a discussion first to avoid duplicated work.

Optional:

If your contribution relates to commercial or enterprise use cases, please feel free to start a Discussion labelled enquiry.

## 👤 Author

Developed and maintained by Niël Lambrechts.

## Support

nFit is offered as a free and open-source tool, licensed under the GNU AGPL v3.0. If you find it useful and would like to support its ongoing development, maintenance, and the addition of new features, please consider making a small donation.

Your support helps to dedicate more time to improving nFit and is greatly appreciated!

* **PayPal:** `https://paypal.me/NielLambrechts`
* **Bitcoin (BTC):** `3MuQ5SRyVG8UjQzeuSjpqhNGPm92dcU26t`

Thank you for your support!

## ⚙️  Value-Added Commercial Services

In some cases, it may be possible to arrange access to enhanced features such as:
- Automated generation of managed system and virtual machine (LPAR) configuration data
- Extensive system health check scripts tailored to AIX and Linux on Power environments

Provision of such services is subject to commercial terms and may involve prior consultation with existing service providers or partners. These options are considered on a case-by-case basis and may not be immediately available.

For commercial or collaboration enquiries, please open a [GitHub Discussion](https://github.com/niel-lambrechts/nfit/discussions) with the `enquiry` label.

## License

This project is licensed under the [GNU AFFERO GENERAL PUBLIC LICENSE (AGPL-3.0)](LICENSE) - see the LICENSE file for details.

## 🔖 Keywords
AIX · NMON · Capacity Planning · Right-Sizing · IBM Power · Linux on Power · Performance Tuning · Shell Scripting
