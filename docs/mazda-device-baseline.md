# Mazda device baseline — 2026-10-27

This commit captures the deployed /data/openpilot source files before the TI, I-stop and LDW repair. Existing repository files absent from the device are removed; additional vendored SMB/SPNEGO source and CAN reference files are included. File contents, executable bits and symlink targets follow the device.

The device reports branch ladybug13 and commit a573cddab67cbdd31c14ba9cde7dd2fa20f6b45e, but its Git index is corrupt and its deployed files differ extensively from that historical tree. The manifest records direct file hashes rather than treating the branch label as evidence.

Generated libraries and firmware, caches, runtime logs and credentials are excluded from source control. Their relevant build hashes are recorded in mazda-device-baseline-20261027.json. In particular, the deployed libdbc.so already supports LDW_WARN; the older Windows build is not the deployed library. The disk Panda firmware hash does not establish what is currently flashed on the MCU.

This baseline intentionally preserves existing bugs. The next commit contains the repairs and their validation. No files or parameters were changed on the device.
