# Preserve the original Mazda HUD warning group

The user reports that both left and right lane departures display the left warning on the OEM HUD with deployed commit `3644559`.

## Confirmed software defect

The helper copies camera bits 57 and 58 but overwrites adjacent bit 56 (`HANDS_ON_STEER_WARN` in the supplied DBC) with `steer_required`. This makes the complete three-bit stock warning group non-preserving. Executing the actual old production helper shows:

| Stock three-bit value | Old output, host hands request off | Old output, host hands request on |
|---|---|---|
| 4 (`100`) | 4 (`100`) | 5 (`101`) |
| 5 (`101`) | 4 (`100`) | 5 (`101`) |

Thus two distinct stock codes are merged regardless of the camera's original bit 56. Earlier native tests incorrectly required the overwrite to follow the DBC field name, so those tests did not establish actual left/right HUD behavior.

This is a demonstrated software defect that can explain the symptom if this vehicle distinguishes directions using that bit. At the time of diagnosis, the device SSH connection was unavailable and the raw left/right driving records had not been read. Do not claim that physical left/right values are proven to be 4/5 merely from this reproduction.

## Repair

Preserve camera bit 56 along with bits 57/58 whenever the camera message is fresh. Host hands requests, engagement and the vibration setting cannot alter this stock warning group. Clear all three bits when the existing camera freshness gate expires. Keep other overlays, original message fields and all steering/TI/I-stop rules unchanged.

There is no DBC or Panda safety-source change. The repair is host Python; a matching existing native CAN library can exercise the added tests. If a normal build regenerates the firmware commit label, pandad retains its existing signature-check/update process.

## Validation

The ordinary regression suite covers all eight possible input warning groups with both host hands-request states, and stale-message clearing. Native packer tests check preservation of the actual low three bits of byte 7, explicitly keep 4 and 5 distinct, and retain unrelated lane/TJA fields. These tests do not open USB or CAN sockets.

```sh
python3 -B selfdrive/car/mazda/tests/test_startup_ldw.py -v
PYTHONPATH=/data/openpilot:/data/openpilot/pyextra python3 -B selfdrive/car/mazda/tests/test_hud_native.py -v
```

Actual incoming camera `0x440` and outgoing host `0x440` should be compared for the user's left/right test before claiming a vehicle-level resolution.
