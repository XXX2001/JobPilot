# Design spec — DX SZVAV and MZVAV chapters (and SZVAV cleanup)

**Date:** 2026-04-28
**Project:** Schneider Electric ATH600 — *Application Note for Air Handling Units* (`BM_ATH600_AN_AHU_DD01314252.xml`)
**Author of edits:** Claude (W-3 mode — Claude edits DITA directly; user manually relabels schema images)
**Status:** awaiting user sign-off before any DITA file is touched

---

## 1. Scope

This spec captures every decision agreed during brainstorming for completing the **MZVAV** and **DX SZVAV** chapters of the Application Note, plus the **typo / consistency corrections** to the existing **SZVAV** chapter and supporting topics.

Once approved:

- **Claude edits the DITA XML files directly.**
- **The user manually relabels the K2 schema images** (EPS / drawio — outside Claude's editing reach) per §8.

Both new chapters end up at structural parity with SZVAV: each parent chapter has a setup page plus three nested sub-topics — *Fan Control*, *Supply air temperature control*, *Safety and HVAC features*.

## 2. Cross-cutting decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| 1 | **One drive per architecture** — the **supply-fan drive**. Return fans (where present in the larger architecture diagrams) are out of scope for these chapters. | Matches existing SZVAV precedent; the K2 schemas show one drive symbol per architecture. |
| 2 | **Three genuinely different configurations** (option B from brainstorm). Each chapter has its own wiring table, its own fan-PID strategy, its own sensor set. | The architectures are physically different (hydronic vs. DX vs. multi-zone) — copy-paste hides the real differences from the customer. |
| 3 | **Canonical chapter names: SZVAV / MZVAV / DX SZVAV** (not "DXVAV"). | "DXVAV" misnames the architecture: DX describes the cooling type; the system is still single-zone VAV. The schema filename and the *Classification of AHU* topic both already use "DX SZVAV". |
| 4 | **Sub-topic names harmonized** — *Fan Control*, *Supply air temperature control*, *Safety and HVAC features* — same three labels under all three parent chapters. The architecture context is carried by the parent chapter title. | Removes the SZ-specific *"SZVAV safety and HVAC features"* so MZ and DX inherit the same shape cleanly. |
| 5 | **Topic-title pattern:** *"Configuration and setup for {ARCH} Air Handling Unit"* — singular, "for", not "of/AHUs". | Aligns the topic `<title>` with the bookmap `navtitle` (which already says "for"/singular). Existing SZVAV title is updated to match. |
| 6 | **Bookmap restructure:** each parent chapter gets nested children mirroring SZVAV's structure. | Required by decision #4; current bookmap has SZVAV with children but MZ/DX are flat. |

## 3. SZVAV corrections (apply BEFORE the parallel chapters)

These are existing issues in the SZVAV-side topics. Fix them first so MZ / DX are not grafted onto bad text.

### 3.1 Typos
| Where | Find | Replace |
|---|---|---|
| `…Supply_air_temperature_control…xml` (×2) | `imrpove` | `improve` |
| `…Fan_Control…xml` | `tempearature` | `temperature` |
| `…About_the_application…xml` | `controlling g the humidifier` | `controlling the humidifier` |
| `…Fan_Control…xml` | `submenu. of` | `submenu of` |
| `…Fan_Control…xml` step list | `1, 2, 3, 4, 5, 7` | renumber to `1, 2, 3, 4, 5, 6` |

### 3.2 Broken bullet list — `Setup_Configuration_SZVAV` Wiring section

The two bullets currently read:

```
- The temperature measurement parameters shown in the specifications may be adjusted to match specific
- HVAC application requirements based on installation environment.
```

Merge into one bullet:

```
- The temperature measurement parameters shown in the specifications may be adjusted to match specific HVAC application requirements based on installation environment.
```

### 3.3 DI / AI cross-document inconsistencies

Two clashes between the SZVAV setup wiring table and the SZVAV `safety_and_HVAC_features` prose. The **wiring table is authoritative** in both cases.

- **Damper feedback DI.** Setup table: `DI6 (DaF)`. Safety doc Step 3 of the Damper Control table currently says *"In our implementation `[DI5] LI5`"*. Change → *"In our implementation `[DI6] LI6`"*.
- **Run-permissive interlock 2.** Safety doc currently says interlocks are "freeze stat + duct smoke detector" but Step 3 assigns `RPI2 = [DI4] LI4` (which the wiring table labels Dirty Filter Switch). Resolution: **rewrite the prose** to say *"freeze stat + dirty filter as interlocks"*, and replace `RPT2 = [Interlock Opened] INLKOPEN` with a tag that matches the dirty-filter semantic (best Dicolabel match — flagged §7).

### 3.4 Schema label fixes (manual — see §8 asset checklist)

K2 SZVAV image only:

- Right-side `T_OA` → `T_ia` (Indoor air temperature). The label currently says outdoor air on the *supply* side, which is wrong.
- "Air Quality Sensor" → "Duct Smoke Detector" (matches the wiring table's DI5 description; the K2 currently labels a physically different device).
- `P_S` and `T_MA` are visible on the K2 but never appear in the wiring table — **annotate them on the schema as "→ PLC"** (PLC-side sensors), so a reader doesn't expect to find them in the AI table.

### 3.5 Title / ID consistency

- `Setup_Configuration_SZVAV` topic title: *"Configuration and setup of SZVAV AHUs"* → *"Configuration and setup for SZVAV Air Handling Unit"*.
- `SZVAV_safety_and_HVAC_features` topic title: *"SZVAV safety and HVAC features"* → *"Safety and HVAC features"* (per cross-cutting decision #4).
- **DITA concept ID collision** — `TPC_ATH600_AN_AHU_DXVAV_DD01314168.xml` and `TPC_ATH600_AN_AHU_MZVAV_DD01314169.xml` both currently use `id="ConfigurationAndSetupForMZVAVAirHan-F03F0096"`. Each must be unique. Resolved when both files are rewritten in §4 / §5 with new IDs.
- The shared `id="Sensorintegrationdetailstemper-DD945522"` and `id="WiringDiagrams-A7E07540"` (`<section>` IDs reused across SZ, MZ, DX setup files) — change to unique-per-architecture IDs (e.g., `…-MZ`, `…-DXSZ` suffixes) when the MZ / DX files are rewritten.

### 3.6 Empty / placeholder topics

- `Overview` (`…_DD01314172.xml`), `Application Description` (`…_DD01308080.XML.xml`), `Configuration of the drive` (`…_DD01308079.XML.xml`) — all have empty `<conbody>`. **Recommendation: leave as-is** (navigation-only `part`-level containers are valid DITA practice). No edit.
- `Prerequisites for the Configuration` (`…_DD01308086.XML.xml`) — has 4 sections all marked *"To Be Filled"*: Factory Settings, Expert Level Access, System Units Customization, Communication protocols. **Out of scope for this spec** — flagged as a follow-up work item.

### 3.7 Run-permissive table — Step 1 cut off

`SZVAV_safety_and_HVAC_features` Run-permissive table, Step 1 currently reads:

```
Go to [Complete settings] CST menu, [HVAC functions] HVAC submenu, then [Run
```

Sentence is truncated mid-line. Complete it with the actual Run-Permissive submenu name from the Dicolabel database (best candidate: *"[Run Permissive] RPM"*, but verify Dicolabel — flagged §7).

## 4. MZVAV chapter

### 4.1 Setup Configuration topic

- **File:** `TPC_ATH600_AN_AHU_MZVAV_DD01314169.xml` (currently a copy of SZVAV — full rewrite).
- **New concept id:** suggest `ConfigurationAndSetupForMZVAVAirHandlingUnit-MZ001` (must differ from DX SZVAV).
- **New topic title:** *Configuration and setup for MZVAV Air Handling Unit*.
- **K2 image reference:** `K2 MZVAV AHU_DD01456504.eps`.

**Sensor integration intro (rewrite):** keep the SZVAV framing (FAN macro, external PIDs for water valves, Run Permissive, optional Fire Mode) **and add** a paragraph stating that per-zone temperature, per-zone humidity, and per-zone damper modulation are out of scope — handled by the PLC or BMS. The drive controls the **supply fan only** and closes its fan PID on duct static pressure.

**Wiring table:**

| Components | Terminals | Characteristics |
|---|---|---|
| Supply Air temperature + | `AI2 (T_sa)` | Temperature scaled, 4-20 mA, (-20…60 °C) (-4…140 °F) |
| Supply Air temperature − | COM | Analog input circuit common |
| **Duct static pressure +** | **`AI3 (P_S)`** | **Pressure scaled, 4-20 mA, 0…1000 Pa** (default — integrator scales `CRL3 / CRH3` to actual sensor range) |
| **Duct static pressure −** | COM | Analog input circuit common |
| Damper PLC Signal | R2C | PLC modulating signal of outside-air damper |
| Damper Input | R2A | Input signal to the outside-air damper actuator |
| Cold water valve control | `AQ2 (CV)` | External PID 2 out: 2-10 V, 0-100% |
| Hot water valve control | `AQ1 (HV)` | External PID 1 out: 2-10 V, 0-100% |
| Damper Feedback | `DI6 (DaF)` | Closed/open logic feedback for damper control |
| Air Quality / Duct smoke | `DI5 (AQS)` | Duct smoke detector |
| Dirty Filter Switch | `DI4 (DFS)` | Sensor to detect excess dirt on the air filter |
| Fire Mode Switch | `DI3 (FMS)` | External signal for Fire/Force Mode activation |
| Freeze Stat | `DI2 (FS)` | Detect low temperatures and stop frosting |
| Run Order | `DI1 (S1)` | Button to give run order to the drive |

**Deltas vs. SZVAV wiring table:**
- AI1 (Zone humidity) — **dropped** (no single-zone humidity in MZ).
- AI3 — changed from `T_zone` to `P_S` (duct static pressure).

Wiring image (`wiring_DD01331751.eps`) is reused.

### 4.2 Fan Control sub-topic — *NEW FILE for MZ*

- **Filename:** `TPC_ATH600_AN_AHU_MZVAV_Fan_Control_DD01314171.xml`.
- **Topic title:** *Fan Control*.
- **Concept id (suggested):** `FanControlMZVAV-MZ002`.

Mirrors the existing SZVAV `Fan_Control` topic structure: two `<section>` blocks — *Overview* + *Step-by-step configuration*.

**Section: Overview** *(`id="Overview-MZ002A"`)*

> *"The supply fan is regulated by the drive's internal PID (FAN macro), which closes on duct static pressure (`P_S` on AI3) rather than zone temperature. The setpoint is a fixed static-pressure target; the drive ramps the supply fan up as zones close (their VAV-box dampers throttle, raising trunk pressure — the drive responds to maintain the target). Per-zone temperature regulation is handled by the PLC or BMS via dedicated zone equipment and is not an input to this drive."*

**Section: Step-by-step configuration** *(`id="StepByStepMZVAV-MZ002B"`)*

Derived from the SZVAV `Fan_Control` table with these adapted steps:

- **Step 3** — `PIF` feedback selection → `[AI3] AI3` (terminal unchanged; the upstream sensor changed from temperature to pressure).
- **Step 4** — feedback signal scaling. Adapt all four parameters to the static-pressure sensor's range (default 0…1000 Pa over an internal scale of 0…10000 → 100 units / Pa):
  - `AI3T` → `[0A] 0A` (4-20 mA).
  - `CRL3` = 4 mA, `CRH3` = 20 mA.
  - `AI3L` = `[0-100%]` (or pressure-equivalent label).
  - `PIF1` = 0 (= 0 Pa). `PIF2` = 10000 (= 1000 Pa).
  - `PAH` (high warning) ≈ 9000 (= 900 Pa). `PAL` (low warning) ≈ 1000 (= 100 Pa). *Indicative — integrator tunes.*
- **Step 5** — PID reference. `RPI` to the fixed static-pressure setpoint expressed in scaled units (e.g., 5000 = 500 Pa). Auto/Manual reference reference unchanged from SZ.
- **Step 6** — *(reuse step 7 from corrected SZ — PID controller setup, ST_ submenu)*.

**Trailing note:** zone temperature, zone humidity, and per-zone damper modulation are not inputs to this drive — they are handled by the PLC or BMS via separate equipment.

### 4.3 Supply air temperature control sub-topic — *REUSE existing SZVAV file*

Reference the existing `TPC_ATH600_AN_AHU_Supply_air_temperature_control_DD01306733.XML.xml` from the MZVAV chapter via a `<topicref>` in the bookmap. Content is identical: HV + CV PIDs both close on `T_sa` via AI2, AQ1 / AQ2 outputs unchanged.

### 4.4 Safety and HVAC features sub-topic — *NEW FILE for MZ*

- **Filename:** `TPC_ATH600_AN_AHU_MZVAV_Safety_and_HVAC_features_DD01314173.xml`.
- **Topic title:** *Safety and HVAC features*.
- **Concept id (suggested):** `SafetyHVACMZVAV-MZ004`.

Mirrors the SZVAV `safety_and_HVAC_features` topic structure: leading *Overview* section followed by *Damper control* / *Run permissive and interlocks* / *Warnings and threshold detection (replacement)* / *Fire Mode*.

**Section: Overview** *(verbatim from SZ — same wording about monitoring devices, damper-status verification before fan activation, run permissive validating external device states. Same `Overview-9F682617`-style id naming convention; suggest `Overview-MZ004A`.)*

**Sections:**

- **Damper control** — verbatim from SZ.
- **Run permissive and interlocks** — verbatim from SZ (with §3.3 fix applied: dirty filter as interlock 2 + matching tag).
- **Warnings and threshold detection** — **dropped entirely**. Replace with one paragraph:

  > *"Zone humidity and per-zone supply-air temperature regulation are handled by the PLC or BMS via per-zone dampers and dedicated zone equipment, and are not inputs to this drive. The fan PID closes on duct static pressure (see Fan Control); the supply-air-temperature loops on the central AHU coils close on `T_sa` (see Supply air temperature control)."*

- **Fire Mode** — verbatim from SZ.

## 5. DX SZVAV chapter

### 5.1 Setup Configuration topic

- **File:** `TPC_ATH600_AN_AHU_DXVAV_DD01314168.xml`. *Recommended rename* to `TPC_ATH600_AN_AHU_DX_SZVAV_DD01314168.xml` — but rename is optional (filename can stay; bookmap navtitle and topic title change are sufficient). User decides whether the filename rename is worth the cross-reference churn.
- **New concept id:** suggest `ConfigurationAndSetupForDXSZVAVAirHandlingUnit-DX001`.
- **New topic title:** *Configuration and setup for DX SZVAV Air Handling Unit*.
- **K2 image reference:** `K2 SZVAV AHU DX_DD01456503.eps`.

**Sensor integration intro (rewrite):** keep the SZVAV framing, **and add** two paragraphs explaining the DX-specific actuators:

> *"The cooling coil is direct-expansion (DX). The drive's analog output `AQ2` sends a 0-100% cooling-capacity demand signal (2-10 V) to a packaged Compressor / Condenser / EEV unit. The packaged unit's onboard controller modulates the EEV opening, compressor speed, and condenser fan internally to satisfy the demanded capacity — the drive does not close the refrigerant superheat loop."*
>
> *"The reheat coil is electric resistance, switched on/off by a relay output (R3 or expansion-module relay) driven by AI threshold detection on `T_sa`. Heating is therefore staged on/off, not modulated; PID1 / `AQ1` are unused in this configuration."*

**Wiring table:**

| Components | Terminals | Characteristics |
|---|---|---|
| Zone Air Relative Humidity + | `AI1 (RH%)` | Relative Humidity scaled, 4-20 mA, 0-100% |
| Zone Air Relative Humidity − | COM | Analog input circuit common |
| Supply Air temperature + | `AI2 (T_sa)` | Temperature scaled, 4-20 mA, (-20…60 °C) (-4…140 °F) |
| Supply Air temperature − | COM | Analog input circuit common |
| **Indoor Air temperature +** | **`AI3 (T_ia)`** | Temperature scaled, 4-20 mA, (0…50 °C) (32…122 °F) |
| **Indoor Air temperature −** | COM | Analog input circuit common |
| Damper PLC Signal | R2C | PLC modulating signal of outside-air damper |
| Damper Input | R2A | Input signal to the outside-air damper actuator |
| **Cooling-demand to DX unit** | **`AQ2 (DX-D)`** | External PID 2 out: 2-10 V, 0-100% (cooling-capacity demand) |
| *(reserved — unused in DX SZVAV)* | `AQ1` | — |
| **Electric reheat — Stage 1** | **R3 *(or expansion)*** | Relay output, on/off, driven by AI threshold detection on `T_sa` |
| Damper Feedback | `DI6 (DaF)` | Closed/open logic feedback for damper control |
| Air Quality / Duct smoke | `DI5 (AQS)` | Duct smoke detector |
| Dirty Filter Switch | `DI4 (DFS)` | Sensor to detect excess dirt on the air filter |
| Fire Mode Switch | `DI3 (FMS)` | External signal for Fire/Force Mode activation |
| Freeze Stat | `DI2 (FS)` | Detect low temperatures and stop frosting |
| Run Order | `DI1 (S1)` | Button to give run order to the drive |

**Deltas vs. SZVAV wiring table:**
- AI3 — **renamed** `T_zone` → `T_ia` (also a schema-label fix, §8).
- AQ1 — **vacated** (was Hot water valve; PID1 / CPID1 unused in DX).
- AQ2 — **relabeled** `CV (Cold water valve)` → `DX-D (Cooling-demand to DX unit)`. PID2 / CPID2 plumbing identical (still 2-10 V, still `AO2T = [10U] U10`, still `AO2 = [CPO2] CPO2`).
- R3 — **new row**: Electric reheat stage 1.

### 5.2 Fan Control sub-topic — *REUSE existing SZVAV file*

Reference the existing `TPC_ATH600_AN_AHU_Fan_Control_DD01303476.xml` from the DX SZVAV chapter via `<topicref>`. Content is identical: zone-temperature PID on AI3 via `T_ia` (was `T_zone` — renamed in §3 and §5.1).

### 5.3 Supply air temperature control sub-topic — *NEW FILE for DX*

- **Filename:** `TPC_ATH600_AN_AHU_DX_SZVAV_Supply_air_temperature_control_DD01314174.xml`.
- **Topic title:** *Supply air temperature control*.
- **Concept id (suggested):** `SupplyAirTempControlDXSZVAV-DX003`.

Mirrors the existing SZVAV `Supply_air_temperature_control` topic structure: leading *Overview* section followed by **two** Step-by-step sections (the SZ doc has *Hot water Valve PID* + *Cooled water Valve PID* — DX has *Cooling-demand PID* + *Electric-reheat threshold detection*).

**Section: Overview** *(`id="Overview-DX003A"`)*

> *"A temperature sensor positioned within the supply duct (`T_sa` on AI2) provides feedback for two complementary control loops. The cooling loop uses an external PID (CPID2) to compute a cooling-capacity demand that is sent as a 2-10 V signal on AQ2 to the packaged Compressor / Condenser / EEV unit; the packaged unit's onboard controller modulates the EEV opening, compressor speed, and condenser fan internally to deliver the demanded capacity. The heating loop uses AI threshold detection on `T_sa` to switch a relay output (R3 or expansion-module relay) that energizes the electric-resistance reheat coil — heating is staged on/off, not modulated, and PID1 / AQ1 are unused in this configuration."*

**Section A — *Step-by-step configuration of the cooling-demand PID (DX unit)*** *(`id="StepByStepDXCooling-DX003B"`)*

Adapted from the existing SZVAV *"Step-by-step configuration of the Cooled water Valve PID"* section. All steps carry over with **relabelling only** — the drive parameters are identical:

- All references to *"chilled water valve"* / *"cold water valve"* → *"cooling-demand input of the packaged DX unit"*.
- All references to `CV` (label, not parameter) → `DX-D`.
- AQ2 / AO2 / `AO2T = [10U] U10` / `AO2 = [CPO2] CPO2` — unchanged.
- PID parameters (`CFF2`, `CF12`, `CF22`, `CPI2`, `FH2`, `FL2`, `PIC2`, `RPG2-RIG2-RDG2-PRP2`) — unchanged.
- Setpoint and warning thresholds (`CPI2` = 7000, `FH2` = 8000, `FL2` = 5000 from the SZ doc) — same Tsa physics, same defaults.

**Section B — *Step-by-step configuration of the electric-reheat threshold detection*** *(`id="StepByStepDXReheat-DX003C"`)*

Configures AI threshold detection on `AI2 (T_sa)` so the drive activates the reheat relay when supply air drops below a low-temperature threshold and deactivates it after a hysteresis on the way up. Format mirrors the existing SZ `Warnings and threshold detection` AITD table — 6-row Step / Action.

| Step | Action |
|---|---|
| 1 | Go to `[Complete settings] CST` menu, `[HVAC functions] HVAC` submenu, then `[AI threshold detection] AITD`. |
| 2 | Go to `[AI Threshold Assign] TDA` and select `[AI2] AI2` to assign the function to the supply-air-temperature sensor. |
| 3 | Go to `[AI Threshold Mode] TDMO` and select the relay-driving mode (verify available modes in Dicolabel — see §7). |
| 4 | Go to `[AI Threshold Value] TDT` and set the low-temperature trigger value in scaled units. Indicative default: `5400` (≈17 °C in a 0…10000 scale that maps -50…50 °C — adapt scale to your installation). |
| 5 | Go to `[AI Threshold Hyst] TDH` and set the hysteresis in scaled units. Indicative default: `100` (= 1 °C hysteresis). |
| 6 | Assign the threshold-trigger output to the reheat relay (`R3` or expansion-module relay). Refer to the programming manual for the exact relay-assignment parameter. |

The SZVAV *"Step-by-step configuration of the Hot water Valve PID"* section is **NOT carried over** — DX has no hot-water heating coil.

### 5.4 Safety and HVAC features sub-topic — *NEW FILE for DX*

- **Filename:** `TPC_ATH600_AN_AHU_DX_SZVAV_Safety_and_HVAC_features_DD01314175.xml`.
- **Topic title:** *Safety and HVAC features*.
- **Concept id (suggested):** `SafetyHVACDXSZVAV-DX005`.

Mirrors the SZVAV `safety_and_HVAC_features` topic structure: leading *Overview* section followed by *Damper control* / *Run permissive and interlocks* / *Warnings and threshold detection* / *Fire Mode*.

**Section: Overview** *(verbatim from SZ — same wording about monitoring devices, damper-status verification before fan activation, run permissive validating external device states. Suggest `id="Overview-DX005A"`.)*

**Sections:**

- **Damper control** — verbatim from SZ.
- **Run permissive and interlocks** — verbatim from SZ (with §3.3 fix applied).
- **Warnings and threshold detection** — **extended**:
  - Keep the SZ humidity-driven fan-speed override (AI1 → fan PID switches to fixed speed).
  - Add a 1-line cross-reference: *"For the supply-air-temperature electric-reheat trigger, see Supply air temperature control → Section B."* (Avoids duplicating the configuration table; the threshold-detection table lives in 5.3 Section B.)
- **Fire Mode** — verbatim from SZ.

## 6. Bookmap (`BM_ATH600_AN_AHU_DD01314252.xml`) updates

Restructure the *Configuration of the drive* `<part>` so each chapter has the same three children:

```
<part Configuration of the drive>
  <chapter Prerequisites for the Configuration:>                   (existing — unchanged)
  <chapter Configuration and setup for SZVAV Air Handling Unit>    (rename topic title; structure unchanged)
    <topicref Fan Control>                                          (existing file)
    <topicref Supply air temperature control>                       (existing file)
    <topicref Safety and HVAC features>                             (existing file — title renamed §3.5)
  <chapter Configuration and setup for MZVAV Air Handling Unit>    (rewrite — §4.1)
    <topicref Fan Control>                                          (NEW MZ file — §4.2)
    <topicref Supply air temperature control>                       (REUSE — same file as SZ, §4.3)
    <topicref Safety and HVAC features>                             (NEW MZ file — §4.4)
  <chapter Configuration and setup for DX SZVAV Air Handling Unit> (rewrite, navtitle DXVAV→DX SZVAV — §5.1)
    <topicref Fan Control>                                          (REUSE — same file as SZ, §5.2)
    <topicref Supply air temperature control>                       (NEW DX file — §5.3)
    <topicref Safety and HVAC features>                             (NEW DX file — §5.4)
```

Other bookmap edits:
- All `navtitle` and href references containing `"DXVAV"` → `"DX SZVAV"` (text only; href filename rename is optional per §5.1).
- Topic-title `navtitle` text re-synced with the updated `<title>` in each topic file (per cross-cutting decision #5).

## 7. Open items to verify — non-blocking, confirmed during DITA editing

1. **Two simultaneous AI Threshold Detection (AITD) instances on ATH600.** SZ uses one for the humidity-driven fan-speed override; DX needs a second for the Tsa-driven electric reheat. If the drive supports only one AITD instance, the heater trigger falls back to a relay-assignment-by-AI-comparison alternative (or moves the humidity override to a different mechanism). **Verify in ATH600 programming manual.**
2. **R3 availability on the reference ATH600.** R1 + R2 are standard; R3 typically requires an I/O expansion module. Document the expansion-module need if confirmed.
3. **Static pressure sensor range.** Default 0…1000 Pa baked into §4.2 — confirm with customer / integrator and adjust scaling if different.
4. **Dicolabel database entries** for new DX-specific parameter/label refs (cooling-demand, electric-reheat trigger, "Run Permissive" submenu, "dirty filter" interlock tag for §3.3). Some entries may need to be added.
5. **Filename rename of `TPC_ATH600_AN_AHU_DXVAV_DD01314168.xml`** to `…_DX_SZVAV_…` — optional. If kept, the file's *content* still gets the DX-SZVAV title and ID changes; only the on-disk filename retains the legacy `DXVAV` token.
6. **`P_S` / `T_MA` on the SZVAV K2 schema** — final call between "annotate → PLC" (recommended) and "remove from K2".

## 8. Asset checklist (manual edits — for the user)

These are EPS / drawio source files outside Claude's editing reach. The DITA topics reference them by `href`; the relabels happen in the source images.

- [ ] **K2 SZVAV** image (`K2 SZVAV AHU_DD01456505.eps`):
  - Right-side `T_OA` → `T_ia`.
  - "Air Quality Sensor" → "Duct Smoke Detector".
  - `P_S` and `T_MA` → annotate "→ PLC" (or remove — see §7 item 6).
- [ ] **K2 MZVAV** image (`K2 MZVAV AHU_DD01456504.eps`):
  - "Air Quality Sensor" → "Duct Smoke Detector".
  - Verify "Pressure Setpoint Control" dotted line is clearly originating from the drive (already present in current K2; no edit needed unless it reads ambiguously).
- [ ] **K2 DX SZVAV** image (`K2 SZVAV AHU DX_DD01456503.eps`):
  - Right-side `T_OA` → `T_ia`.
  - "Air Quality Sensor" → "Duct Smoke Detector".
  - **Add a wire / signal line** from a drive relay output (R3 or expansion) to the `HC` (electric reheat) coil. The current K2 shows `HC` in the airflow but does not show how the drive triggers it.
  - Add `HC` heater stage relay to the legend (e.g., *"R3 → HC: Electric reheat coil stage 1"*).

---

## End notes

The shape of this spec is intentionally compact — when Claude edits the DITA, the content lives in those XML files, not duplicated here. This document captures **decisions** and **open items** so the customer (and your future self) can audit *why* the DITA looks the way it does without re-reading every brainstorm message.
