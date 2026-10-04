"""Generate a local test JWT from an openpilot device private key."""

import argparse
import time
from pathlib import Path

import jwt


parser = argparse.ArgumentParser()
parser.add_argument("device_id")
parser.add_argument("private_key", type=Path)
args = parser.parse_args()

now = int(time.time())
with args.private_key.open() as f:
  private_key = f.read()
print(jwt.encode({"identity": args.device_id, "iat": now, "nbf": now, "exp": now + 3600}, private_key, algorithm="RS256"))

