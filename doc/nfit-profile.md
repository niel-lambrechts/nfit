# Script: nfit-profile

> [!Summary]
> **nFit Profile (`nfit-profile`)** is a high-level orchestration script that automates multiple runs of the `nfit` analyser and then applies its own sophisticated, Run-Queue (RunQ) driven sizing logic to refine the results. It uses predefined "profiles" to generate a comprehensive CSV report, provides heuristic hints about workload patterns and CPU pressure, and produces a detailed Rationale Log explaining every step of its calculations.

## Overview
- **Description:** `nfit-profile` orchestrates `nfit`, running it for each profile defined in an external configuration file (`nfit.profiles.cfg`). After collecting the initial CPU and Run-Queue metrics from these `nfit` runs, `nfit-profile` applies its own multi-stage adjustment logic. This logic includes "CPU Downsizing" for efficiency, "CPU Upsizing" to handle pressure (featuring "Hot Thread Workload Dampening" and safety caps), and final "Maximum CPU Sizing Sanity Checks". The result is a single, unified CSV report containing the final, adjusted CPU recommendations for each profile, enriched with VM configuration data and sizing hints.
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

Refer to the program's help output (`nfit-profile -h`) for detailed command-line options and configuration file locations.

## Dependencies
- Perl 5.x
- Core Perl Modules: `Getopt::Long`, `Cwd`, `File::Basename`, `Time::Piece`, `List::Util`, `IO::File`, `version`.
- The `nfit` program (must be executable and findable by path or via `--nfit-path`) at the corresponding version than `nfit-profile`.

### Tactical proposal store

Each standard report publishes one private JSON snapshot under the system cache in
`.nfit.store/proposals/v1/<model>/<sha256>.json`. The model directory matches the
report type (`state-based`, `hybrid-state-decay` or `windowed-decay`). The file
contains the LPARs evaluated together. Each LPAR has its installed entitlement,
installed VPs, pool ID, resolved planning profile and all actionable CSV profiles.
Each profile records the displayed entitlement, recommended VPs, entitlement-bounded
VPs and tactical VPs as numbers. An unavailable numeric cell is JSON null.

Use the snapshot's system, input revisions, effective dates, model and settings
to select a run. Check the VM filter and actual membership before assessing a
whole pool. File modification time does not identify a compatible run. The
settings include the invocation and fingerprints of the programs and configuration
files. Legacy data and state files use size and modification time for their revisions,
as in the engine results cache.

The filename hashes the UTF-8 JSON bytes. Identical retries reuse one file, while
different scopes and results coexist. New snapshots are published by atomic rename.
These files contain local VM names and command arguments. They are derived tactical
results and can be regenerated from the matching inputs. Monthly history has a
separate lifecycle.

### Shadow entitlement candidate test

Add `--candidate-test` to a tactical run to add the candidate table to its
rationale. Supply the VM configuration CSV for role information. The rows
replay measured busy CPU at the installed VP width. Each actionable profile
uses its own eligible hours. Cuts and holds are evaluated at the installed
entitlement, the feasible proposal, their midpoint, and one 10% lower trial
where feasible. Thresholds are rounded to 0.01 core, with a minimum of 0.05
per installed VP. A proposal below that minimum is labelled `widthFloor`.
Multiple labels on one row mean they resolve to the same threshold.

For a later-period test, set the tactical `--enddate` to the training cutoff
and give `--candidate-window YYYY-MM-DD/YYYY-MM-DD` (inclusive UTC dates).
Alternatively, `--candidate-proposals FILE` with `--nmondir` replays a saved
2.1 snapshot and prints the table to standard output. It checks the file
fingerprint, system, data and state revisions, and profile definitions.
Use the original VM configuration for the role check. The saved file supplies
the model, sizing dates and VM scope. The optional later window must lie in
the same capture. For example, a snapshot fitted through 2026-10-02 can use
`--candidate-window 2026-10-03/2026-11-05`.

The comparison uses only the configuration state present at the proposal date.
A later change ends that comparison. Capped LPARs, recognised VIOS, standby
or failover reserves, and unknown roles are excluded. A separate VP change
would require fresh observations. A run breaks at a missing or ineligible
minute; consecutive eligible minutes across UTC midnight stay in one run.

**Above** measures minutes, excess core-minutes, longest run and distinct days.
**Full** gives the same measures during observed pool scarcity. The table also
counts episodes and newly contested minutes (busy covered by the installed
guarantee with competing use). A/B/C are illustrative budgets, ordered by
full-pool minutes, longest consecutive run and excess core-minutes. The limits
are printed with the results. A partial-coverage result applies only to the
classified minutes. These figures describe borrowing exposure and do not
predict lost throughput. Candidate testing is opt-in and writes no sizing
change to the CSV or monthly history.

### Shadow pool reconciliation

With `--candidate-test` (or `--candidate-proposals`), the rationale also gains a
**Pool reconciliation** section. The planning-profile cuts of the LPARs in one
pool are replayed together at the same minutes, at all hours: a configured
entitlement applies in every minute, whatever the planning profile's own hours.
The eligibility rules and candidate entitlements are those of the candidate test.

Only full-pool minutes in which every cut member has a paired sample are counted.
A member is **exposed** in such a minute when its busy cores exceed the candidate
while other LPARs are busy; the guarantee lost is the busy work between the
candidate and the installed entitlement. At the proposals the section gives the
minutes in which all, some or none of the members were exposed together, the
longest run with all exposed, and the per-LPAR counts. Joint counts cannot be
derived from the per-LPAR ones. The candidate test's "new contested" counts,
which also require busy within the installed entitlement, are printed beside
them for comparison; that rule alone would treat a cut on an LPAR already above
its guarantee as free.

For each illustrative budget (minutes any member is exposed / longest run /
excess core-minutes), the section names the combination of candidate
entitlements that releases the most guarantee within it. Up to six cuts in one
pool are searched exhaustively. The section writes nothing to the CSV or to
monthly history.

### Shadow pressure and remedy

The same runs add a **Pressure and remedy** section. For every eligible LPAR,
each paired minute in which its busy cores were above its installed entitlement
(work that depended on borrowing) is classified by what limited it: its VP width
(PhysC at 95% of its VPs, pool open), the pool full with other LPARs busy
(contested), the pool full with no other LPAR busy (alone), both, open (no limit
observed) or unknown (pool state not observed).

The remedy is decided where the sizing is: on the minutes at or above the
LPAR's **planning level**, its busy cores at the planning profile's percentile
over the period (for G3-95W15, P95 of its minute busy). An LPAR is listed when
that level is above its installed entitlement and at least 15 of those minutes
were limited. Borrowing that met a limit only above the planning level is
counted in one closing line, because the guarantee does not size for it.

- **review the VP width**: mostly at its VPs while the pool had room;
- **review the guarantee**: mostly contested, and the pool's installed
  guarantees leave room for what its contested LPARs need together; each needs
  the larger of its proposal and its planning level, less its installed
  entitlement;
- **review the pool**: competing guarantees or the pool maximum, when that room
  is missing, or the LPAR alone filled the pool;
- **review the VP width and the pool together**: both limits, or each at least a
  quarter of the limited minutes at the planning level.

The pool's guarantees are its members' installed entitlements at the end of the
period; reserved capacity is not collected. A full pool is not proof of lost
work, and the size of a change that would clear the pressure is not measured.
The section writes nothing to the CSV or to monthly history.

### Shadow decision table and estate summary

With `--candidate-test`, a run also writes
`output/nfit-profile.<system>.<model>.decisions.<date>.csv` beside its CSV
report; `--candidate-proposals` writes `...decisions.replay-<from>_<to>.csv`.
There is one row per LPAR: the installed and proposed entitlement for its
planning profile, the candidate test at that proposal, the pool
reconciliation's entitlement within each budget, and the pressure remedy with
its need and evidence. An LPAR the shadow checks could not assess carries the
reason in `status`. Evidence is `weak` for a VP-width remedy when busy work at
the planning level is under half the VPs: PhysC reached the VPs, the busy work
did not, and a sustained run queue would be needed to confirm the limit.

`dev/estate-impact` combines the newest table per system and model in `output/`
(`--replay` for replay tables, `--expect SYS1,SYS2` to name missing systems,
`--out FILE` for the combined table) and prints a summary: LPARs assessed and
not, guarantee released at the proposals and within each budget, increases
proposed, and remedies with their evidence. The tables and the summary carry
system and LPAR names; pipe the summary through `dev/nfit-redact` before
sharing it. Neither changes a recommendation.
