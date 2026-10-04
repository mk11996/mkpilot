import os
import time
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["STORAGE_ROOT"] = "./test-storage"
os.environ["LOCAL_ENROLL_CODE"] = "local-test-code"
os.environ["APP_SECRET"] = "test-secret"

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from app.main import app
from app import main as server


def make_identity():
  private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  private_pem = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
  public_pem = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
  return private_pem, public_pem


def test_health():
  response = TestClient(app).get("/health")
  assert response.status_code == 200
  assert response.json() == {"status": "ok"}


def test_invalid_enrollment_code():
  response = TestClient(app).post("/api/v1/enroll", json={"enroll_code": "wrong-code", "public_key": "x" * 40})
  assert response.status_code == 403


def test_native_style_raw_upload():
  private_pem, public_pem = make_identity()
  client = TestClient(app)
  enrolled = client.post("/api/v1/enroll", json={
    "enroll_code": "local-test-code",
    "public_key": public_pem.decode(),
    "device_name": f"test-{uuid4()}",
  })
  assert enrolled.status_code == 200
  device_id = enrolled.json()["device_id"]
  now = int(time.time())
  device_token = jwt.encode({"identity": device_id, "iat": now, "nbf": now, "exp": now + 3600}, private_pem, algorithm="RS256")
  headers = {"Authorization": f"JWT {device_token}"}

  url_response = client.get(f"/v1.4/{device_id}/upload_url/", params={"path": "2026-08-04/vehicle_data.csv"}, headers=headers)
  assert url_response.status_code == 200
  upload_url = url_response.json()["url"].replace("http://127.0.0.1:8080", "")
  uploaded = client.put(upload_url, content=b"timestamp,speed\n1,20\n", headers={"Content-Type": "application/octet-stream"})
  assert uploaded.status_code == 200
  assert uploaded.json()["size_bytes"] > 0

  files = client.get(f"/api/v1/devices/{device_id}/files", headers=headers)
  assert files.status_code == 200
  assert len(files.json()) == 1


def test_storage_retention_keeps_csv_and_evicts_old_video_and_logs():
  device_id = f"retention-{uuid4().hex[:8]}"
  root = server.STORAGE_ROOT / device_id
  root.mkdir(parents=True, exist_ok=True)
  video_old = root / "2026-01-01" / "qcamera.ts"
  video_new = root / "2026-01-02" / "qcamera.ts"
  trajectory = root / "2025-01-01" / "vehicle_data.csv"
  old_log = root / "2025-01-01" / "qlog.bz2"
  for path, data in ((video_old, b"1" * 8), (video_new, b"2" * 8), (trajectory, b"timestamp,speed\n1,20\n"), (old_log, b"log")):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
  from datetime import timedelta
  with server.SessionLocal() as session:
    from app.main import StoredFile, now
    created = now()
    session.add_all([
      StoredFile(device_id=device_id, relative_path="2026-01-01/qcamera.ts", storage_path=str(video_old), file_size=8, sha256="1" * 64, uploaded_at=created - timedelta(days=2)),
      StoredFile(device_id=device_id, relative_path="2026-01-02/qcamera.ts", storage_path=str(video_new), file_size=8, sha256="2" * 64, uploaded_at=created),
      StoredFile(device_id=device_id, relative_path="2025-01-01/vehicle_data.csv", storage_path=str(trajectory), file_size=trajectory.stat().st_size, sha256="3" * 64, uploaded_at=created - timedelta(days=1000)),
      StoredFile(device_id=device_id, relative_path="2025-01-01/qlog.bz2", storage_path=str(old_log), file_size=3, sha256="4" * 64, uploaded_at=created - timedelta(days=61)),
    ])
    session.commit()
  old_limit = server.VIDEO_STORAGE_LIMIT_BYTES
  try:
    server.VIDEO_STORAGE_LIMIT_BYTES = 10
    result = server.cleanup_storage()
    assert result["deleted_videos"] == 1
    assert result["deleted_logs"] == 1
    assert video_old.exists() is False
    assert video_new.exists() is True
    assert trajectory.exists() is True
    assert old_log.exists() is False
  finally:
    server.VIDEO_STORAGE_LIMIT_BYTES = old_limit
    with server.SessionLocal() as session:
      from app.main import StoredFile
      for row in session.query(StoredFile).filter(StoredFile.device_id == device_id).all():
        session.delete(row)
      session.commit()
    import shutil
    shutil.rmtree(root, ignore_errors=True)
