# nFit - Release History & Changelog

This document details the major milestones and feature introductions throughout the evolution of the nFit suite.

---

### **Version 6 - Single Pass Engine and Business Cycle Awareness (Seasonality Engine)**

nFit v6 represents the culmination of four months of intensive development focused on performance, forecasting sophistication, and production-readiness. Building upon the enterprise integration capabilities introduced in v5, this release addresses two critical challenges in large-scale capacity planning:

1. **Performance at Scale:** The new Single Pass Engine reduces analysis time by up to 16× for typical configurations, transforming multi-hour history priming operations into sub-hour workflows.
2. **Business Cycle Awareness:** The Seasonality Engine enables capacity planners to model and forecast the impact of known business events (month-end processing, product launches, seasonal peaks) with statistical rigour, moving beyond simple linear growth projections.

This release also includes major improvements to RunQ-based sizing recommendations, introducing tier-aware upsizing logic that appropriately balances business criticality against operational constraints, and clipping detection that identifies workloads constrained by available processor capacity.

#### The Performance Problem

Prior to v6, generating historical records for a system with 16 workload profiles across 3 months required **48 separate nfit invocations** (16 profiles × 3 months). Each invocation scanned the entire L1 data cache from the beginning, resulting in massive I/O redundancy:

```plaintext
Month 1 (April):  16 nfit runs × full cache scan = 16× I/O
Month 2 (May):    16 nfit runs × full cache scan = 16× I/O
Month 3 (June):   16 nfit runs × full cache scan = 16× I/O
Total: 48 nfit invocations, 48× full cache scans
```

For seasonal events (which analyse baseline, peak, and residual periods), this multiplied to **144 invocations** for the same 3-month period.

#### The Single Pass Solution

The Single Pass Engine uses **manifest-driven unified analysis**, executing nfit once with a manifest describing all profiles simultaneously:
```
Month 1 (April):  1 nfit run (all 16 profiles) = 1× I/O
Month 2 (May):    1 nfit run (all 16 profiles) = 1× I/O
Month 3 (June):   1 nfit run (all 16 profiles) = 1× I/O
Total: 3 nfit invocations, 3× full cache scans
```

**Result:** 16× I/O reduction per analysis period.

#### Seasonality and Business Cycle Forecasting Engine

nFit v6 introduces a sophisticated **Seasonality and Business Cycle Forecasting Engine** designed to model and predict the impact of known business events on CPU demand. This addresses a fundamental limitation of traditional capacity planning: linear growth models cannot account for massive, cyclical spikes like month-end processing, Black Friday, or quarterly financial close.


### **Version 5 - Abstracted Cache Connector Architecture**

nFit v5 represents a major leap forward in enterprise data integration, building upon the high-performance caching engine introduced in v4. The cornerstone of this release is a new **Abstracted Cache Connector Architecture**, which decouples data ingestion from analysis and introduces native support for **InfluxDB**, allowing capacity planners to connect nFit directly to their strategic monitoring platforms.

This release refactors `nfit-stage-nmon` and introduces `nfit-stage-influxdb` as dedicated, high-performance tools that create a standardised, persistent cache. This enhancement, combined with architectural improvements to support hierarchical caches, allows nFit to analyse entire estates composed of multiple managed systems, making it a true enterprise-grade capacity planning platform.


### **Version 4.x - The Performance Release**

Version 4 represents a landmark architectural overhaul focused on delivering game-changing performance and efficiency, especially for large-scale enterprise environments.

* **Added: Multi-Level Caching Engine.** The cornerstone of v4 is a persistent caching system that dramatically reduces analysis time.
    * **Level 1: Data & State Cache:** On the first run, `nfit` now processes all source NMON files once to create a highly optimised data cache (`.nfit.cache.data`) and a pre-compiled configuration state cache (`.nfit.cache.states`).
    * **Level 2: Results Cache:** Subsequent `nfit` runs with identical parameters retrieve results directly from a results cache, often returning in sub-second time without any recalculation.
* **Added: `nfit-stage` Companion Tool.** A new utility designed to create a "staged" directory (a lightweight view using symbolic links) of the most recent NMON files from a massive, multi-terabyte archive. This minimises filesystem overhead before `nfit` even begins its work.
* **Changed: Unified Analysis Engine.** The data processing pipelines for both direct NMON files and NIMON CSV exports were re-architected to use the new caching engine, bringing massive performance gains to all supported data sources.

---

### **Version 3.x - The Accuracy & Context Release**

Version 3 focused on adding deep contextual awareness to the analysis, ensuring that recommendations are not just statistically sound, but also relevant to the VM's entire lifecycle and future trends.

* **Added: Configuration State Windowing.** This was a revolutionary feature for nFit. The engine now automatically detects historical changes to a VM's core configuration (CPU Entitlement, SMT, vCPUs) and analyses each period independently. This prevents data from an old configuration from polluting the recommendation for the current one.
* **Added: Recency-Weighted Analysis.** When windowed decay is enabled, the model gives more statistical weight to the most recent performance data, ensuring that the final recommendation is more reflective of current behaviour.
* **Added: Growth Prediction.** The toolkit can now perform a linear regression on historical data to predict future CPU needs, allowing for proactive, forward-looking capacity planning.
* **Added: Native NMON File Support.** `nfit` gained the ability to process raw `.nmon` files (including compressed ones) directly, making it far easier to get started with ad-hoc analysis without needing a time-series database.

---

### **Version 2.x - The Workload Intelligence Release**

Version 2 extended the suite's accuracy by looking beyond raw CPU consumption and into how the workload was actually behaving on the system.

* **Added: Run-Queue (RunQ) Analysis.** `nfit` began analysing RunQ metrics to understand true CPU pressure and queuing.
* **Added: Intelligent Additive Logic.** Based on RunQ data, the `nfit-profile` script could now intelligently recommend not only *down-sizing* but also *up-sizing* entitlement for constrained workloads.
* **Added: Hot Thread Workload (HTW) Detection.** Advanced heuristics were built in to identify workloads that appear constrained (high RunQ) but are not actually using their full CPU entitlement, typical of single-threaded applications. This prevents erroneously recommending more CPU for workloads that cannot use it.

---

### **Version 1.x - The Foundation**

Version 1 established the core philosophy of the nFit suite.

* **Added: Configurable Sizing Profiles.** The foundational innovation of nFit was the `nfit.profiles.cfg` file, allowing users to define multiple, reusable sizing strategies. This provided a powerful and flexible alternative to one-size-fits-all analysis, forming the basis for all future development.
* [cite_start]**Added: Core Analysis Engine.** The initial `nfit` script was created to parse NMON data, calculate rolling averages and percentiles, and apply basic filtering. 

---
