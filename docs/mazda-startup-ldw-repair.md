# Mazda TI startup, I-stop timing and stock LDW relay

This repair follows the deployed-source baseline in the preceding commit. The device and the Windows source directories were not modified by this repair. No Panda was flashed.

## TI author's reference

The user supplied `D:\Openpilot\ladybug13` as the TI author's reference. Eight source-file hashes are recorded in `mazda-ti-author-reference-20261027.json` so the inspected copy is identifiable. This local copy was inspected directly; the reference label is supplied by the user.

The reference controller sends the TI signature request on bus 1 every control cycle, including zero torque while discovering the interceptor. There is no ten-frame budget or permanent shutdown after a single failed discovery. Its Panda forwards main-bus EPS `0x241` directly to the camera; the host does not reconstruct it or substitute the camera's requested torque.

The repair follows native EPS forwarding and the TI signature format, with the user's deliberate startup-only detection policy: wait for current, valid Panda reports with the configured Mazda safety model, safety parameter and unsafe flags, then probe for at most one second. Requests generated while Panda is in ELM327 mode cannot consume this window. Only a TI confirmed during that startup window receives the author's ongoing command stream.

Some reference details are deliberately not copied:

- Its `HKSCUM` transmit-field spelling conflicts with `CHKSUM` in the reference DBC. The deployed/candidate `CHKSUM` name is retained, with the same desired-torque value as the original TI protocol.
- It also generates a nonzero standard EPS torque request from openpilot's desired steering. This vehicle's existing TI strategy keeps that standard request at zero; this repair preserves that strategy. Original camera steering torque is never passed to EPS as part of LDW relay.
- Its one-time `get_ti()` replacement creates a different CarParams object and could enable TI after startup. This installation instead selects TI once during startup and retains a single shared CarParams object. The existing stock-EPS fallback can be selected only with controls inactive; late feedback cannot switch TI back on.

## Resulting behavior

### TI

The vehicle-selected `enableTorqueInterceptor` flag survives the generic constructor. A single bus-1 feedback parser is created with its required TI signals; the main-bus parser no longer incorrectly requires the same feedback on bus 0.

After Mazda safety becomes ready, a one-second, zero-torque startup probe runs at the existing control cadence. Actual feedback must have been received and its duplicated torque bytes must agree, as checked by the author's Panda detection rule. An empty parser's `can_valid` flag cannot establish TI presence. The author's bus-1 feedback parsing rate and timeout remain unchanged; no additional shorter TI feedback timeout is imposed.

Transition logs include `vehicle_moving`, `controls_active`, or `feedback_timeout` when disabled, along with speed, active-control/Panda readiness, receipt/parser/redundant-torque checks, and TI state/ramp-down/diagnostic fields. The motion lockout applies even before safety readiness and before the first probe is sent.

If feedback confirms TI during the startup window, the author's `STATE == RUN` and `not RAMP_DOWN` permission rule and ongoing TI message stream are preserved. `VIOL` and `ERROR` remain diagnostic fields. Invalid feedback inhibits torque and resets the software torque ramp to zero; normal controlsd CAN errors and the TI's own RUN/ramp-down behavior remain responsible for disengagement and permission. This does not change the interceptor firmware's internal safety logic or torque limits.

If the startup window expires, the probe enters a permanent disabled state for this controlsd run. It cannot be reopened by later feedback, a stop at a traffic light, a setting toggle or a Panda safety interruption. Vehicle motion above 0.1 m/s or control activation before confirmation also ends discovery. When controls are inactive, the existing standard-EPS fallback is selected; a mode switch is never applied while controls are active. Restarting controlsd starts a new probe, still subject to the motion/control lockout. The disabled probe state and transition log provide a future warning integration point; no new screen warning is implemented here.

### I-stop

The existing `D4 00 00 00 00 00 00 00` command and 160-frame burst remain. The burst starts only after five continuous seconds with:

- matching, fresh Panda safety reports;
- valid main-bus parsing and fresh `ENGINE_DATA.RPM` samples;
- the vehicle's existing `START_STOP == 2` awake-state indication.

TI availability is not an I-stop requirement. Loss of ECU/safety readiness rearms the delayed burst. Disabling the setting clears the burst counter. This addresses early window consumption; it is not an ECU acknowledgement, and five seconds is a conservative software delay rather than a measured guarantee for every ECU startup.

### Stock LDW vibration request

The existing `EnableLdwVibration` switch controls only the vibration relay:

| openpilot control active | Switch off | Switch on |
|---|---|---|
| No | Suppress LDW | Relay stock LDW |
| Yes | Suppress LDW | Suppress LDW |

This table reflects the user's corrected requirement on 2026-10-28. The previous table allowed warnings during active control when the switch was on and ignored the off switch during inactive control. The settings help text now describes the corrected behavior.

`CarControl.active` is used, consistently with the existing Mazda controller's steering gate. A fresh `CAM_LKAS.LDW` bit is relayed into the openpilot-generated `0x243`; no guessed `CAM_LANEINFO` side enumeration, no camera `LKAS_REQUEST` substitution, and no extra 1.5-second hold are used. Missing camera frames for more than 200 ms or an invalid camera parser clears the relay. The existing checksum calculation includes the LDW bit.

No new HUD or instrument left/right indication is added. The old custom software LDW event inferred from the incorrectly decoded side enumeration is removed; stock lane-info HUD handling is otherwise unchanged.

This verifies propagation of the warning request. Source inspection and offline packing cannot prove that a specific EPS firmware will physically vibrate when standard torque is zero and TI is active.

### EPS 0x241

Panda forwards the original EPS frame from bus 0 to bus 2 without rebuilding bytes or changing cadence. Host-generated `0x241` is removed from the TX allowlist and the controller. Unused functions containing speculative checksum algorithms are also removed. The original checksum and fields absent from the DBC are preserved by forwarding the original packet.

Camera `0x243`/`0x440` forwarding remains blocked as before. This change does not pass camera steering requests to EPS.

## Validation

Hardware-free Python regression tests execute the actual startup helpers, controller, CAN message helpers and CarState startup-state method. They cover startup timeout lockout, late feedback while moving, no active-control mode switch, a safety interruption not reopening the deadline, the LDW truth table, I-stop delay and the retained author RUN/ramp-down behavior. External cereal/CAN dependencies and the unchanged torque limiter are isolated with stubs; these tests do not validate physical torque limits or ECU behavior.

```sh
python3 -B selfdrive/car/mazda/tests/test_startup_ldw.py -v
```

The host C harness compiles the actual Mazda safety header and checks original-byte `0x241` forwarding, rejection of host-injected `0x241`, continued camera-command blocking, bus-1 TI transmission, and exact I-stop TX filtering. Generic torque-limit functions are stubbed because their implementation is unchanged.

```sh
cc -std=c11 selfdrive/car/mazda/tests/test_panda_forwarding.c -o /tmp/mazda-forwarding-test
/tmp/mazda-forwarding-test
```

Additional validation checks Python syntax against the device's Python 3.8 language version and uses the currently deployed CAN packing/parsing libraries for pure-memory candidate `0x243`/`0x249` round trips. These checks do not open CAN/USB, execute controlsd or modify device files.

### Device pycapnp compatibility follow-up

During the authorized Panda update on 2026-10-27, normal startup exposed a `controlsd` exception in `panda_safety_ready`: the deployed pycapnp list reader rejects slices, even an empty trailing slice after the only Panda. The initial tests used ordinary Python lists and did not catch this. Extra Panda checks now use integer indices while retaining all safety-mode, parameter and unsafe-flag checks.

The original failure was reproduced using actual device `cereal.log.Event.pandaStates` and `CarParams.safetyConfigs` readers. Four additional tests with those real types passed for a matching Panda, ignored/non-ignored extra Pandas, missing/invalid messages, and mismatched safety fields. Run them on a matching cereal/pycapnp installation without opening CAN or USB:

```sh
PYTHONPATH=/data/openpilot:/data/openpilot/pyextra python3 -B selfdrive/car/mazda/tests/test_startup_capnp.py -v
```

This follow-up changes host Python only. It does not change Panda firmware sources; the already built and hardware-verified `DEV-96d0a9eb-DEBUG` firmware remains compatible.

## Deployment constraint

The host changes and rebuilt Panda firmware must be deployed together. The old firmware blocks native `0x241`, while the repaired host no longer synthesizes replacements. Deploying only the Python files would leave the camera without that EPS feedback.

Rebuild Panda from this source using the project's matching toolchain and deploy/verify the resulting firmware as a separate, authorized operation. Rebuild DBC libraries from source as part of a normal build; do not copy the stale Windows `libdbc.so` over the device's newer library. Neither a full target build nor physical vehicle behavior has been validated by the host-only regression tests.
