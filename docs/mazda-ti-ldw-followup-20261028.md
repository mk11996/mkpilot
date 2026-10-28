# Mazda TI startup diagnosis and corrected LDW switch

## Evidence from the deployed df926b6 source

Device startup logs read on 2026-10-28 show the following events. Times are Asia/Shanghai and depend on the device clock.

| Startup | Initial vehicle state | TI transitions |
|---|---|---|
| 2026-10-28 17:36:52 | Drive, reported filtered speed 2.3 m/s; controls inactive | `waiting -> disabled`, followed by EPS fallback; no `probing` state |
| 2026-10-28 18:36:48 | Park, 0.0 m/s | `waiting -> probing` at 18:36:52.028; `probing -> confirmed` at 18:36:52.037 |

The failed startup is in `swaglog.0000022354`; the stationary success is in `swaglog.0000022399`. Both used the expected Panda firmware signature.

For the identified failed startup, the one-second handshake timer did not expire: probing never began. The existing guard disables an unconfirmed TI when speed exceeds 0.1 m/s or controls are active, including while waiting for Panda readiness. This is the user's requested prevention of TI activation after driving has started. The stationary startup confirmed feedback in about 8.4 ms. This evidence does not establish the cause of other unrecorded failures or prove torque output while engaged.

## Changes

- Preserve startup-only TI selection, the one-second deadline, motion/control lockout, EPS fallback, and the author's RUN/ramp-down permission rules.
- Add explicit disable reasons (`vehicle_moving`, `controls_active`, `feedback_timeout`) and transition diagnostics: speed, active-control state, safety readiness, actual feedback receipt, parser validity, redundant torque match, TI state/version/ramp-down/violation/error.
- Correct `EnableLdwVibration` semantics: inactive control plus switch on relays fresh stock vibration; switch off suppresses it. Active control always suppresses vibration. Camera steering torque is never copied. Update the settings description accordingly.
- Do not add HUD or left/right warning functionality. The user reports that the physical LDW vibration works and that camera faults disappear after the vehicle's normal power cycle.

## Validation

The hardware-free suite includes the four LDW switch/control combinations, immediate fresh-warning handling, existing TI startup/feedback safety checks, and movement lockout before Panda readiness. The deployed cereal/pycapnp readiness suite remains applicable. These tests do not send CAN, enable controls, or prove physical ECU behavior.

```sh
python3 -B selfdrive/car/mazda/tests/test_startup_ldw.py -v
PYTHONPATH=/data/openpilot:/data/openpilot/pyextra python3 -B selfdrive/car/mazda/tests/test_startup_capnp.py -v
```

This change affects host Python and settings text. There are no Panda safety-source or DBC changes. A normal build is required for the changed settings text; if a build regenerates Panda's commit-version label, normal pandad may update the signature to that matching build.
