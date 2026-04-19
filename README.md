# Gulfstream Pool Heat Pump — Home Assistant Integration

A Home Assistant custom integration for **Gulfstream pool heat pumps** controlled through the [Compass WiFi Heat Pump Navigator](https://play.google.com/store/apps/details?id=com.icmcontrols.gulfstream) app (ICM Controls / captouchwifi.com cloud service).

![Gulfstream logo](custom_components/gulfstream/brand/logo.png)

---

## What it does

This integration connects Home Assistant to your Gulfstream pool heat pump over the cloud. It polls the ICM Controls API every 30 seconds and exposes the device as a full HA device with climate control, operating mode selection, temperature sensors, fault monitoring, flow detection, and panel lock.

Changes you make in HA — mode changes, setpoint adjustments, locking the panel — are sent to the cloud immediately and verified by reading back the device registers. While a command is in flight you see the requested value right away, flagged as pending. When the device confirms the change (or the command times out), the display snaps to the actual device state.

---

## Entities

| Platform | Entity | What it shows |
|---|---|---|
| `climate` | *(device name)* | Mode (Off/Heat/Cool/Auto) and setpoint — the primary control card |
| `select` | Mode | Full 5-mode picker including Spa |
| `sensor` | Water Temperature | Pool water temp in °F (suppressed when unreliable — see below) |
| `sensor` | Fault | Current fault status as a descriptive string |
| `binary_sensor` | Online | Whether the WiFi module is communicating with the cloud |
| `binary_sensor` | Pool Pump | Whether the circulation pump is running (flow detected) |
| `switch` | Panel Lock | Locks/unlocks the physical buttons on the heat pump |

### Climate entity

The climate card is the primary control surface. It maps the device's operating modes to HA HVAC modes:

| Device mode | HA HVAC mode |
|---|---|
| Off | `off` |
| Pool Heat | `heat` |
| Pool Cool | `cool` |
| Pool Heat/Cool | `heat_cool` |
| Spa | `heat` (display only — use the Mode select to enter Spa) |

Pool Cool and Pool Heat/Cool are only shown if the device reports them as available (DF1/DF2 registers). This is auto-detected from the device at runtime — no manual configuration needed.

When the device is in Spa mode, the climate card shows `heat`, the target temperature reflects the **spa setpoint** (RSV2 register), and adjusting the temperature writes RSV2. The pool setpoint (RSV1) is untouched. Selecting any non-off HVAC mode from the climate card exits Spa and enters the corresponding pool mode.

Setpoint limits are mode-aware:
- Pool modes: 50–100 °F
- Spa: 50–104 °F

### Mode select entity

Because HA's climate model has no "spa" concept, a companion **Mode** select entity exposes all five modes. Use it to enter Spa mode or to switch modes without going through the climate card. It shows the same pending/confirmed shadow state as the climate entity — selecting a mode shows the choice immediately while the command travels to the device.

Available options are filtered live from device registers (DF1/DF2), so Pool Cool and Pool Heat/Cool only appear if your unit supports them.

### Water Temperature sensor

Reports the pool water temperature from the RMT register. The reading is suppressed (shown as "unknown") under two conditions:

1. **Device offline** — the WiFi module hasn't checked in with the server within 60 seconds. Stale data isn't pool temperature.
2. **Pump off or just started** — the RMT sensor sits inside the heat exchanger's plumbing. When the pool circulation pump is off, the sensor reads the temperature of stagnant pipe water (roughly air temperature). Even after the pump starts, the reading doesn't reflect real pool water until fresh water has flushed through the exchanger, which takes about a minute.

The integration tracks when flow first starts (CHGF register transitions from non-zero to zero) and suppresses the temperature reading for the first 60 seconds of each pump run. When the state is "unknown", HA's recorder skips the data point entirely — history graphs won't show misleading flat lines or temperature dips when the pump cycles.

### Fault sensor

Reports the FLT register as a human-readable string:

| Code | Value | Meaning |
|---|---|---|
| 0 | `ok` | No fault |
| 1 | `no_flow` | No flow — check pool pump |
| 2 | `high_pressure` | High pressure switch |
| 3 | `low_pressure` | Low pressure switch |
| 4 | `water_sensor_fault` | Water sensor malfunction |
| 5 | `evap_sensor_fault` | Evaporator sensor malfunction |
| other | `fault` | Fault detected |

Extra attributes include the raw fault code, a longer description, and the CHGF (filter/flow) indicator value. The entity's icon changes to a filled alert circle when a non-zero fault is present.

Note: `no_flow` (FLT=1) is the normal state when the pool pump is off and water isn't flowing through the heat exchanger. It is not a hardware fault — the heat pump protects itself by stopping when there's no flow.

### Online binary sensor

True when the WiFi module has checked in with the ICM Controls server within the last 60 seconds. The module polls every 3–10 seconds normally; 60 seconds is the same threshold the bundled API library uses for `is_online`. Attributes expose `last_online` and `server_time` timestamps for automation use.

### Pool Pump binary sensor

True when the circulation pump is running, based on the CHGF register (0 = flow present, non-zero = no flow). The pool pump typically runs on a schedule for roughly half the day. When it's off the heat pump cannot condition water and goes idle — this is completely normal and should not be treated as a fault.

### Panel Lock switch

Locks or unlocks the physical control buttons on the heat pump. Uses the same shadow state pattern as the other controls: while a lock/unlock command is in flight (up to 60 s), the switch shows the requested value immediately. When the command resolves, it snaps to what the device actually reports.

---

## Installation

### HACS (recommended)

1. Open HACS → Integrations → Custom repositories.
2. Add `https://github.com/jm99999/ha-gulfstream` as an **Integration** repository.
3. Search for "Gulfstream" and install.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/gulfstream` folder into your HA `config/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

Setup is done entirely through the UI — no YAML required.

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=gulfstream)

Or manually:

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Gulfstream Pool Heat Pump**.
3. Enter your Compass WiFi / captouchwifi.com username and password.
4. If your account has multiple devices, select the one to add (see below).

There are no options to configure after setup — available operating modes (Pool Cool, Pool Heat/Cool) are detected automatically from the device.

### Multiple heat pumps on one account

If your account has more than one device, a device picker appears after you enter your credentials. Select one heat pump and finish setup. To add the second (or third) heat pump, run **Add Integration** again — the same credentials, a different device selection — and a separate HA device is created for each.

Attempting to add the same heat pump twice is safe: the integration detects the duplicate and aborts with an "already configured" message rather than creating conflicting entities.

---

## How it works

### Cloud API

The Gulfstream app communicates with **captouchwifi.com**, a white-label cloud platform from ICM Controls. The integration uses the same REST API that the app uses. The WiFi module on the heat pump polls the server every few seconds and exchanges register values. HA's commands are written to the server, and the device picks them up on its next poll (typically 5–30 seconds).

The API returns register values with names that were designed for HVAC thermostats and are often misleading on pool heaters. For example, the registers the app calls "defrost param 1" and "defrost param 2" actually control whether Pool Cool and Pool Heat/Cool are enabled — they have nothing to do with defrost. These mappings were reverse-engineered from the app APK and confirmed against live API responses.

### Polling and error resilience

The integration polls every 30 seconds. The ICM Controls API returns empty bodies and HTTP 500 errors approximately 7% of the time — this is normal behavior for this cloud service. To avoid flapping entities on every transient blip, the coordinator returns the previous state for up to 3 consecutive failures before marking entities unavailable.

### Shadow / optimistic state

Commands to the heat pump have to travel: HA → cloud server → WiFi module → heat pump → WiFi module → cloud server → HA. This round trip takes 5–30 seconds and sometimes up to 60 seconds on slow server days.

Without shadow state, the UI would snap back to the old value immediately after you change something (because the next poll shows the old device state), then jump to the new value 5–30 seconds later. This feels broken.

With shadow state, the moment you issue a command:
- The coordinator stores the requested value as a "pending" value.
- All entities that display that value immediately show the pending value, flagged with `assumed_state = True` (HA renders these with a visual pending indicator).
- The command is sent to the cloud with `verify=True`, which polls the device registers until they confirm the write, up to a 60-second timeout.
- When the command completes (confirmed, conflicted with another source, or timed out), the pending value is cleared and the entities refresh from actual device state.

If two commands overlap (rare, but possible if the user changes mode and setpoint in rapid succession), each command clears only its own pending value — it checks that the pending value still matches what it set before clearing.

### Water temperature validity

The RMT temperature sensor is located in the plumbing, inside or immediately adjacent to the heat exchanger. When the circulation pump is off:
- No water is moving through the exchanger.
- The sensor reads the temperature of stagnant pipe water, which equilibrates to roughly air temperature.
- This number is meaningless as "pool temperature."

When the pump first starts:
- The pipe initially contains the same stagnant water.
- It takes approximately 60 seconds for fresh pool water to flush the exchanger and reach the sensor.
- Until then, the reading is still closer to air temperature than pool temperature.

The coordinator tracks the moment CHGF transitions from non-zero (no flow) to zero (flow present) and records it as `_flow_started_at`. The `water_temp_valid` property returns False until 60 seconds have elapsed. Both the Water Temperature sensor and the climate entity's `current_temperature` check `water_temp_valid` before reporting a value, returning `None` (shown as "unknown" in HA) otherwise.

### Command verification and conflict detection

All writes use `verify=True` with a 60-second timeout. The API library polls the device registers after writing and returns a `CommandResult` indicating:

- **Verified** — device register matches what was written.
- **Conflict** — device register changed, but to a different value (e.g. another user changed the setting via the app simultaneously). The integration logs a warning and does not retry.
- **Failed/timeout** — the register didn't update within 60 seconds. The integration logs an error. The entity refreshes and shows whatever the device actually has.

---

## What is not included

The device supports several features that are not exposed in this integration:

| Feature | Status | Notes |
|---|---|---|
| Defrost mode (DFL) | Not exposed | Read-only attribute only; the setting is rarely changed |
| Defrost end temperature (AXD) | Not exposed | Hardware configuration |
| Spa timer (DF3 / STOF) | Not exposed | Timer hours/minutes for automatic spa mode timeout |
| Schedule mode (SCH) | Not exposed | On / Off / Vacation scheduling |
| Water sensor calibration (DB) | Not exposed | Raw value available via attributes |
| Evap sensor calibration (HTS) | Not exposed | Raw value available via attributes |
| Anti-short-cycle delay (CAL) | Not exposed | Hardware configuration |
| Pool Heat/Cool deadband (DFU) | Not exposed | Hardware configuration |
| Coil temperature (GEN15) | Not exposed | Raw register; internal diagnostic |
| Suction line temperature (LCS) | Not exposed | Raw register; internal diagnostic |
| Remote thermostat mode (DFG/VH) | Not exposed | Rarely used; complex interaction with setpoints |
| OptionsFlow (reconfigure) | Not implemented | Re-add the integration to change credentials; remove the entry first |
| Multiple devices per entry | Not supported | One config entry per heat pump; run setup again to add a second unit |

---

## Troubleshooting

**Entities show "unavailable"**
The coordinator has failed to reach the API 3 times in a row. Check your internet connection, your captouchwifi.com credentials, and whether the Compass WiFi app can connect. The `Online` binary sensor will show False if the WiFi module itself is offline.

**Water temperature shows "unknown"**
Either the pool pump is off, or it has been on for less than 60 seconds. This is by design — see the [Water Temperature sensor](#water-temperature-sensor) section above.

**Mode change seems to work but the display flickers back**
This can happen if another source (the app, a schedule) changes the mode simultaneously. Check the `Fault` sensor's `filter_indicator` attribute and look for conflict warnings in the HA log.

**Pool Cool or Pool Heat/Cool modes not available**
These modes are only shown when the device reports DF1 (Pool Cool) or DF2 (Pool Heat/Cool) as enabled. If your unit doesn't support these modes, the device registers will be 0 and the options won't appear. Confirm in the Compass WiFi app whether these modes are available for your model.

---

## Technical background

The register names in the API do not match their actual purpose. The ICM Controls cloud platform is designed for HVAC thermostats; Gulfstream (and other ICM customers) repurpose the registers. The mappings used in this integration were reverse-engineered from the APK `com.icmcontrols.gulfstream v1.0.12` and confirmed against a live device. The bundled `api/` library documents the empirical mapping in `discovery/confirmed-register-mapping.md`.

The cloud service URL is `https://www.captouchwifi.com`. This integration does not communicate with any Anthropic or third-party service beyond captouchwifi.com and Home Assistant's own infrastructure.
