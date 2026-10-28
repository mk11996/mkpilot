# Mazda TI startup diagnosis and corrected LDW switch

## Evidence from the deployed df926b6 source

Device startup logs read on 2026-10-28 show the following events. Times are Asia/Shanghai and depend on the device clock.

| Startup | Initial vehicle state | TI transitions |
|---|---|---|
| 2026-10-28 17:36:52 | Drive, reported filtered speed 2.3 m/s; controls inactive | `waiting -> disabled`, followed by EPS fallback; no `probing` state |
| 2026-10-28 18:36:48 | Park, 0.0 m/s | `waiting -> probing` at 18:36:52.028; `probing -> confirmed` at 18:36:52.037 |

The failed startup is in `swaglog.0000022354`; the stationary success is in `swaglog.0000022399`. Both used the expected Panda firmware signature.

For the identified failed startup, the one-second handshake timer did not expire: probing never began. The then-deployed guard disabled an unconfirmed TI when speed exceeded 0.1 m/s or controls were active, including while waiting for Panda readiness. The stationary startup confirmed feedback in about 8.4 ms. This evidence does not establish the cause of other unrecorded failures or prove torque output while engaged. The user subsequently requested removal of the movement lockout and independent OEM HUD side warnings; the final implementation below follows those updated requirements.

## Changes

- Remove TI's movement lockout: a moving vehicle may perform the one-time startup handshake while controls are inactive. Preserve the one-second deadline, active-control lockout, permanent timeout latch, EPS fallback, and the author's RUN/ramp-down permission rules.
- Log `controls_active` or `feedback_timeout` and transition diagnostics: speed, active-control state, safety readiness, actual feedback receipt, parser validity, redundant torque match, TI state/version/ramp-down/violation/error.
- Correct `EnableLdwVibration` semantics: inactive control plus switch on relays fresh stock vibration; switch off suppresses it. Active control always suppresses vibration. Camera steering torque is never copied. Update the settings description accordingly.
- Relay OEM HUD left/right warnings independently of both vibration and engagement. Replace the erroneous three-bit `LDW_WARN` with `LDW_WARN_LL` at bit 57, `LDW_WARN_RL` at bit 58 and the separate `HANDS_ON_STEER_WARN` at bit 56, matching the supplied DBC. Declare the remaining known HUD fields in the camera parser. Forward each incoming HUD frame; clear side warnings on invalid/stale camera input.
- Keep the existing hands-on warning overlay. Do not synthesize a side indication from openpilot model output, pass camera steering torque, or change Panda safety forwarding. The user reports that physical vibration works and camera faults disappear after a normal vehicle power cycle.

## Validation

The hardware-free suite includes the four vibration switch/control combinations, independent HUD sides and freshness, TI startup confirmation while moving with controls inactive, timeout/active-control lockout, and existing feedback safety checks. The deployed cereal/pycapnp readiness suite remains applicable. Native CAN tests verify raw byte positions, preservation of lane/TJA context, stale-warning clearing, and parser separation of left/right from hands-on warnings. These tests do not send CAN, enable controls, or prove physical ECU behavior.

```sh
python3 -B selfdrive/car/mazda/tests/test_startup_ldw.py -v
PYTHONPATH=/data/openpilot:/data/openpilot/pyextra python3 -B selfdrive/car/mazda/tests/test_startup_capnp.py -v
PYTHONPATH=/data/openpilot:/data/openpilot/pyextra python3 -B selfdrive/car/mazda/tests/test_hud_native.py -v
```

This change affects host Python, the Mazda DBC and settings text. A normal build must regenerate `libdbc.so` and its parser/packer links as well as the UI. Do not substitute the stale Windows CAN binaries. There are no Panda safety-source changes; if a build regenerates Panda's commit-version label, normal pandad may update the signature to that matching build.
