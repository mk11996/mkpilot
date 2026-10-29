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

## Uploaded rlog confirms the overwrite

The user subsequently provided a roughly 60.27-second rlog segment. Its initData identifies deployed commit `3644559`. File SHA-256: `e0e47fed556d9daf5e781e0b76ca4bcca3f92f9a5b5f2a258719785d97cbe82b`.

All 126 camera-bus `0x440` frames were paired with the corresponding host output. There were 119 unchanged zero-warning frames, four camera warning-group values of 3 rewritten to 2, and three warning-group values of 4 retained as 4. Panda's bus-128 TX echoes show the same output groups. For example, camera `4102000000001043` became transmitted `4102000000001042`; only bit 56 changed, about 6 ms after receipt. The actual recording contains codes 3/4, not the illustrative 4/5 pair used in the earlier offline reproduction. No physical left/right assignment is inferred without a user annotation.

Replaying the unaltered old production helper with an independent codec derived from the checked-in DBC reproduces all 126 recorded host packets. Replaying the repaired helper preserves all 126 original camera packets byte-for-byte; four output frames change, exclusively in bit 56. This is offline log replay, not new hardware execution. Native test fixtures now include the exact recorded camera frames so the previous overwrite cannot pass as expected behavior.

The log demonstrates the bit loss on this vehicle. Post-deployment physical HUD validation is still required.
