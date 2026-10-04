from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import jwt
from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local.db")
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "./storage")).resolve()
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
APP_SECRET = os.environ.get("APP_SECRET", "development-secret-change-me").encode()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
DIAGNOSTICS_TOKEN = os.environ.get("DIAGNOSTICS_TOKEN", "")
ENROLL_CODE = os.environ.get("LOCAL_ENROLL_CODE", "")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
UPLOAD_TOKEN_TTL = int(os.environ.get("UPLOAD_TOKEN_TTL_SECONDS", "900"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))
APP_TOKEN_TTL = int(os.environ.get("APP_TOKEN_TTL_SECONDS", "86400"))
JWT_CLOCK_SKEW_SECONDS = int(os.environ.get("JWT_CLOCK_SKEW_SECONDS", "5"))
FFMPEG_ENABLED = os.environ.get("FFMPEG_ENABLED", "1") == "1"
VIDEO_FPS = int(os.environ.get("VIDEO_FPS", "20"))
VIDEO_STORAGE_LIMIT_BYTES = int(os.environ.get("VIDEO_STORAGE_LIMIT_BYTES", str(80 * 1024**3)))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "60"))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "3600"))
MEDIA_TICKET_TTL_SECONDS = int(os.environ.get("MEDIA_TICKET_TTL_SECONDS", "300"))
NETWORK_TEST_MAX_MIB = int(os.environ.get("NETWORK_TEST_MAX_MIB", "32"))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("openpilot-server")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
  pass


class Device(Base):
  __tablename__ = "devices"
  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  device_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
  name: Mapped[str] = mapped_column(String(200))
  public_key: Mapped[str] = mapped_column(Text)
  last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_state_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_state_device_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
  revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class StoredFile(Base):
  __tablename__ = "files"
  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  device_id: Mapped[str] = mapped_column(String(32), index=True)
  relative_path: Mapped[str] = mapped_column(String(1000), index=True)
  storage_path: Mapped[str] = mapped_column(String(1200))
  file_size: Mapped[int] = mapped_column(BigInteger)
  sha256: Mapped[str] = mapped_column(String(64))
  uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnrollmentCode(Base):
  __tablename__ = "enrollment_codes"
  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
  expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  device_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AppUser(Base):
  __tablename__ = "app_users"
  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
  password_hash: Mapped[str] = mapped_column(String(300))
  role: Mapped[str] = mapped_column(String(30), default="admin")
  disabled: Mapped[bool] = mapped_column(Boolean, default=False)


class DeviceCommand(Base):
  __tablename__ = "device_commands"
  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  device_id: Mapped[str] = mapped_column(String(32), index=True)
  username: Mapped[str] = mapped_column(String(100))
  method: Mapped[str] = mapped_column(String(100))
  params_json: Mapped[str] = mapped_column(Text, default="{}")
  status: Mapped[str] = mapped_column(String(30), default="pending")
  result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Base.metadata.create_all(engine)
app = FastAPI(title="openpilot Local Server", version="0.2.0")
STATIC_ROOT = Path(__file__).parent / "static"


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
def admin_page() -> FileResponse:
  return FileResponse(STATIC_ROOT / "admin.html", media_type="text/html")


@app.get("/replay.html", include_in_schema=False)
def replay_page() -> FileResponse:
  return FileResponse(STATIC_ROOT / "replay.html", media_type="text/html")


def now() -> datetime:
  return datetime.now(timezone.utc)


def sha256_text(value: str) -> str:
  return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str) -> str:
  salt = secrets.token_bytes(16)
  derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
  return f"scrypt${salt.hex()}${derived.hex()}"


def password_matches(password: str, encoded: str) -> bool:
  try:
    _, salt_hex, digest_hex = encoded.split("$", 2)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)
  except Exception:
    return False


def safe_relative_path(value: str) -> str:
  value = value.replace("\\", "/").lstrip("/")
  path = Path(value)
  if not value or path.is_absolute() or ".." in path.parts:
    raise HTTPException(status_code=400, detail="invalid relative path")
  return "/".join(path.parts)


def issue_app_token(user: AppUser) -> str:
  issued = int(time.time())
  payload = {"sub": str(user.id), "username": user.username, "role": user.role, "iat": issued, "exp": issued + APP_TOKEN_TTL}
  return jwt.encode(payload, APP_SECRET, algorithm="HS256")


def public_key_fingerprint(public_key: str) -> str:
  """Short non-secret identifier used to diagnose device key mismatches."""
  return hashlib.sha256(public_key.encode("utf-8")).hexdigest()[:16]


def jwt_timing_summary(raw_token: str) -> dict[str, float | None]:
  """Return token/server timestamps without logging the token itself."""
  try:
    claims = jwt.decode(raw_token, options={"verify_signature": False})
    now_ts = time.time()
    nbf = float(claims["nbf"]) if claims.get("nbf") is not None else None
    iat = float(claims["iat"]) if claims.get("iat") is not None else None
    return {"server_time": now_ts, "nbf": nbf, "iat": iat, "nbf_delta": (nbf - now_ts) if nbf is not None else None}
  except Exception:
    return {"server_time": time.time(), "nbf": None, "iat": None, "nbf_delta": None}


def get_device_from_jwt(authorization: str | None, session: Session) -> Device:
  if not authorization or not authorization.startswith("JWT "):
    raise HTTPException(status_code=401, detail="missing device JWT")
  raw = authorization[4:].strip()
  try:
    unverified = jwt.decode(raw, options={"verify_signature": False})
    device_id = unverified.get("identity")
    device = session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None or device.revoked:
      raise ValueError("unknown device")
    payload = jwt.decode(raw, device.public_key, algorithms=["RS256"], leeway=JWT_CLOCK_SKEW_SECONDS, options={"require": ["identity", "iat", "exp"]})
    if payload.get("identity") != device.device_id:
      raise ValueError("identity mismatch")
  except Exception as exc:
    device_obj = locals().get("device")
    logger.warning("device_jwt_rejected reason=%s identity=%s key_fingerprint=%s timing=%s", type(exc).__name__, locals().get("device_id"), public_key_fingerprint(device_obj.public_key) if device_obj is not None else "unknown", jwt_timing_summary(raw))
    raise HTTPException(status_code=401, detail="invalid device JWT") from exc
  device.last_seen_at = now()
  session.commit()
  return device


def get_app_user(authorization: str | None, session: Session) -> AppUser:
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="missing app token")
  try:
    payload = jwt.decode(authorization[7:].strip(), APP_SECRET, algorithms=["HS256"])
    user = session.get(AppUser, int(payload["sub"]))
    if user is None or user.disabled:
      raise ValueError("disabled user")
    return user
  except Exception as exc:
    raise HTTPException(status_code=401, detail="invalid app token") from exc


def issue_media_ticket(user: AppUser, file_id: int) -> str:
  expires_at = int(time.time()) + MEDIA_TICKET_TTL_SECONDS
  payload = f"{user.id}:{file_id}:{expires_at}"
  signature = hmac.new(APP_SECRET, payload.encode(), hashlib.sha256).hexdigest()
  return f"{payload}:{signature}"


def get_media_ticket_user(ticket: str, file_id: int, session: Session) -> AppUser:
  try:
    user_id, ticket_file_id, expires_at, signature = ticket.rsplit(":", 3)
    payload = f"{user_id}:{ticket_file_id}:{expires_at}"
    expected = hmac.new(APP_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if (int(ticket_file_id) != file_id or int(expires_at) < int(time.time()) or
        not hmac.compare_digest(signature, expected)):
      raise ValueError("invalid media ticket")
    user = session.get(AppUser, int(user_id))
    if user is None or user.disabled:
      raise ValueError("disabled user")
    return user
  except Exception as exc:
    raise HTTPException(status_code=401, detail="invalid or expired media ticket") from exc


@app.middleware("http")
async def request_logging(request: Request, call_next):
  request_id = request.headers.get("X-Request-ID", secrets.token_hex(8))
  started = time.perf_counter()
  try:
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%.1f", request_id, request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
    return response
  except Exception:
    logger.exception("request_id=%s method=%s path=%s unhandled_exception", request_id, request.method, request.url.path)
    raise


def seed_defaults() -> None:
  with SessionLocal() as session:
    if ENROLL_CODE and session.scalar(select(EnrollmentCode).where(EnrollmentCode.code_hash == sha256_text(ENROLL_CODE))) is None:
      session.add(EnrollmentCode(code_hash=sha256_text(ENROLL_CODE), expires_at=None))
    if ADMIN_PASSWORD and session.scalar(select(AppUser).where(AppUser.username == ADMIN_USERNAME)) is None:
      session.add(AppUser(username=ADMIN_USERNAME, password_hash=password_hash(ADMIN_PASSWORD), role="admin"))
    session.commit()


seed_defaults()


class EnrollRequest(BaseModel):
  enroll_code: str = Field(min_length=8, max_length=200)
  public_key: str = Field(min_length=32, max_length=10000)
  device_name: str = Field(default="openpilot device", max_length=200)


class AppLoginRequest(BaseModel):
  username: str
  password: str


class EnrollResponse(BaseModel):
  device_id: str
  device_name: str
  server_time: int


@app.get("/health")
def health() -> dict[str, str]:
  try:
    with engine.connect() as connection:
      connection.execute(text("SELECT 1"))
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "database": "ok", "storage": "ok"}
  except Exception as exc:
    logger.exception("health_check_failed")
    raise HTTPException(status_code=503, detail="dependency unavailable") from exc


@app.get("/ready")
def ready() -> dict[str, str]:
  result = health()
  result["status"] = "ready"
  return result


@app.get("/internal/diagnostics")
def diagnostics(x_diagnostics_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
  if not DIAGNOSTICS_TOKEN or not hmac.compare_digest(x_diagnostics_token or "", DIAGNOSTICS_TOKEN):
    raise HTTPException(status_code=404, detail="not found")
  try:
    with engine.connect() as connection:
      connection.execute(text("SELECT 1"))
    db_status = "ok"
  except Exception:
    db_status = "error"
  usage = shutil.disk_usage(STORAGE_ROOT)
  return {"version": app.version, "database": db_status, "storage_root": str(STORAGE_ROOT), "storage_free_bytes": usage.free, "video_limit_bytes": VIDEO_STORAGE_LIMIT_BYTES, "log_retention_days": LOG_RETENTION_DAYS, "cleanup_interval_seconds": CLEANUP_INTERVAL_SECONDS, "active_athena_connections": len(athena_connections), "log_level": os.environ.get("LOG_LEVEL", "INFO")}


@app.post("/api/v1/app/login")
def app_login(payload: AppLoginRequest):
  with SessionLocal() as session:
    user = session.scalar(select(AppUser).where(AppUser.username == payload.username))
    if user is None or user.disabled or not password_matches(payload.password, user.password_hash):
      logger.warning("app_login_failed username=%s", payload.username)
      raise HTTPException(status_code=401, detail="invalid credentials")
    logger.info("app_login_success username=%s", user.username)
    return {"access_token": issue_app_token(user), "token_type": "bearer", "expires_in": APP_TOKEN_TTL}


@app.post("/api/v1/enroll", response_model=EnrollResponse)
def enroll(payload: EnrollRequest) -> EnrollResponse:
  with SessionLocal() as session:
    code = session.scalar(select(EnrollmentCode).where(EnrollmentCode.code_hash == sha256_text(payload.enroll_code), EnrollmentCode.used_at.is_(None)))
    if code is None or (code.expires_at is not None and code.expires_at < now()):
      logger.warning("enrollment_rejected reason=invalid_or_expired_code")
      raise HTTPException(status_code=403, detail="invalid or expired enrollment code")
    device_id = secrets.token_hex(8)
    session.add(Device(device_id=device_id, name=payload.device_name, public_key=payload.public_key))
    code.used_at = now()
    code.device_id = device_id
    session.commit()
  logger.info("device_enrolled device_id=%s name=%s", device_id, payload.device_name)
  return EnrollResponse(device_id=device_id, device_name=payload.device_name, server_time=int(time.time()))


@app.post("/v2/pilotauth/")
def native_pilotauth(
  imei: Annotated[str | None, Query()] = None,
  imei2: Annotated[str | None, Query()] = None,
  serial: Annotated[str | None, Query()] = None,
  public_key: Annotated[str, Query(min_length=32, max_length=10000)] = "",
  register_token: Annotated[str, Query(min_length=20, max_length=20000)] = "",
):
  """Compatibility endpoint used by openpilot's stock registration.py.

  Registration is authenticated by proof of possession of the private key:
  the device signs a short-lived token with ``register=True``. No hardware
  identifier is trusted as a secret, and an already-known public key receives
  its existing device id instead of creating duplicates.
  """
  try:
    payload = jwt.decode(register_token, public_key, algorithms=["RS256"], options={"require": ["register", "exp"]})
    if payload.get("register") is not True:
      raise ValueError("invalid registration claim")
  except Exception as exc:
    logger.warning("native_registration_rejected reason=%s key_fingerprint=%s", type(exc).__name__, public_key_fingerprint(public_key) if public_key else "missing")
    raise HTTPException(status_code=403, detail="invalid registration proof") from exc
  with SessionLocal() as session:
    device = session.scalar(select(Device).where(Device.public_key == public_key))
    if device is None:
      device = Device(device_id=secrets.token_hex(8), name=serial or imei or "openpilot device", public_key=public_key)
      session.add(device)
    elif serial:
      device.name = serial
    session.commit()
    device_id = device.device_id
  logger.info("native_device_registered device_id=%s serial=%s imei=%s", device_id, serial, imei)
  return {"dongle_id": device_id}


@app.get("/v1/me")
def native_me(authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    device = get_device_from_jwt(authorization, session)
    return {"dongle_id": device.device_id, "device_id": device.device_id}


@app.get("/v1.4/{device_id}/upload_url/")
def upload_url(device_id: str, path: Annotated[str, Query(min_length=1)], request: Request, authorization: Annotated[str | None, Header()] = None, source_mtime_ms: int | None = Query(default=None)):
  relative_path = safe_relative_path(path)
  recorded_at = None
  if source_mtime_ms is not None:
    try:
      timestamp = float(source_mtime_ms) / 1000.0
      if 946684800 <= timestamp <= time.time() + 300:
        recorded_at = datetime.fromtimestamp(timestamp, timezone.utc)
      else:
        logger.warning("upload_url_invalid_source_mtime path=%s value=%r", relative_path, source_mtime_ms)
    except (TypeError, ValueError, OverflowError, OSError):
      logger.warning("upload_url_invalid_source_mtime path=%s value=%r", relative_path, source_mtime_ms)
  with SessionLocal() as session:
    device = get_device_from_jwt(authorization, session)
    if device.device_id != device_id:
      raise HTTPException(status_code=403, detail="device mismatch")
    raw_token = secrets.token_urlsafe(36)
    token = UploadToken(device_id=device_id, token_hash=sha256_text(raw_token), relative_path=relative_path, expires_at=now() + timedelta(seconds=UPLOAD_TOKEN_TTL), recorded_at=recorded_at)
    session.add(token)
    session.commit()
  logger.info("upload_url_issued device_id=%s path=%s expires=%s", device_id, relative_path, token.expires_at)
  base_url = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
  return {"url": f"{base_url}/storage/put/{raw_token}", "headers": {"Content-Type": "application/octet-stream"}}


class UploadToken(Base):
  __tablename__ = "upload_tokens"
  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  device_id: Mapped[str] = mapped_column(String(32), index=True)
  token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
  relative_path: Mapped[str] = mapped_column(String(1000))
  expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# UploadToken is declared after the initial model block so the API remains readable.
Base.metadata.create_all(engine)


def migrate_schema() -> None:
  # StoredFile.file_size was originally an INTEGER. Upgrade existing PostgreSQL
  # installations so files near the 2 GiB upload limit cannot overflow.
  if engine.dialect.name == "postgresql":
    with engine.begin() as connection:
      connection.execute(text("ALTER TABLE files ALTER COLUMN file_size TYPE BIGINT"))
      connection.execute(text("ALTER TABLE upload_tokens ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ"))
      connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_state_at TIMESTAMPTZ"))
      connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_state_device_at TIMESTAMPTZ"))
      connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS state_json TEXT"))
      connection.execute(text("ALTER TABLE files ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ"))
      connection.execute(text("ALTER TABLE upload_tokens ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ"))


migrate_schema()


VIDEO_SUFFIXES = {".ts", ".hevc", ".h265", ".mp4", ".mkv", ".webm"}


def file_class(relative_path: str) -> str:
  """Return the retention class for an uploaded file.

  CSV is deliberately treated as permanent because it contains the user's
  driving trajectory data. All video formats count toward the 80 GiB video
  quota. The remaining files are operational/driving logs and follow the
  retention period.
  """
  suffix = Path(relative_path).suffix.lower()
  if suffix == ".csv":
    return "trajectory"
  if suffix in VIDEO_SUFFIXES:
    return "video"
  return "log"


def storage_category(relative_path: str) -> str:
  """Return the physical storage folder without changing the public path."""
  normalized = relative_path.replace("\\", "/").lstrip("/").lower()
  if normalized.startswith("errors/"):
    return "system-errors"
  return {"video": "videos", "trajectory": "trajectory-csv", "log": "driving-logs"}[file_class(relative_path)]


def storage_path_for(device_id: str, relative_path: str) -> Path:
  return STORAGE_ROOT / device_id / storage_category(relative_path) / relative_path


def video_source(relative_path: str) -> str | None:
  name = Path(relative_path).name.lower()
  return {
    "qcamera.ts": "道路主摄 qcamera",
    "fcamera.hevc": "前视摄像头 fcamera",
    "dcamera.hevc": "驾驶员摄像头 dcamera",
    "ecamera.hevc": "扩展摄像头 ecamera",
  }.get(name) if file_class(relative_path) == "video" else None


def video_source(relative_path: str) -> str | None:
  """Identify all native camera stream names, including .ts uploads."""
  if file_class(relative_path) != "video":
    return None
  name = Path(relative_path).name.lower()
  return {
    "qcamera.ts": "主摄像头 qcamera",
    "fcamera.ts": "前视摄像头 fcamera",
    "dcamera.ts": "驾驶员摄像头 dcamera",
    "ecamera.ts": "扩展摄像头 ecamera",
    "fcamera.hevc": "前视摄像头 fcamera",
    "dcamera.hevc": "驾驶员摄像头 dcamera",
    "ecamera.hevc": "扩展摄像头 ecamera",
  }.get(name)


def recorded_time_metadata(relative_path: str) -> dict[str, str | None]:
  """Infer device-side recording time from standard openpilot filenames."""
  path = relative_path.replace("\\", "/")
  match = re.search(r"(20\d{2}-\d{2}-\d{2})--(\d{2})-(\d{2})-(\d{2})", path)
  if match:
    stamp = datetime.strptime("%s %s:%s:%s" % match.groups(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return {"recorded_at": stamp.isoformat(), "recorded_at_precision": "second", "recorded_at_source": "filename_utc"}
  match = re.search(r"(?:^|/)trajectory/(20\d{2}-\d{2}-\d{2})(?:/|$)", path, flags=re.IGNORECASE)
  if match:
    stamp = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {"recorded_at": stamp.isoformat(), "recorded_at_precision": "date", "recorded_at_source": "path_date"}
  return {"recorded_at": None, "recorded_at_precision": None, "recorded_at_source": None}


def file_metadata(relative_path: str) -> dict[str, str | None]:
  kind = file_class(relative_path)
  return {"kind": kind, "kind_name": {"video": "视频", "log": "日志", "trajectory": "驾驶轨迹 CSV"}[kind], "camera": video_source(relative_path), "storage_category": storage_category(relative_path), **recorded_time_metadata(relative_path)}

def remove_stored_file(session: Session, row: StoredFile) -> int:
  path = Path(row.storage_path)
  size = row.file_size or 0
  session.delete(row)
  path.unlink(missing_ok=True)
  # A generated preview is derived data and must not survive its source.
  preview = STORAGE_ROOT / ".media-cache" / f"{row.sha256}.mp4"
  preview.unlink(missing_ok=True)
  return size


def cleanup_storage() -> dict[str, int]:
  """Apply retention rules and return a small operational summary.

  This is intentionally database-driven: deleting a file also removes its
  metadata, while missing files are cleaned from the database on the next
  pass. CSV files are never selected by either automatic policy.
  """
  deleted_videos = 0
  deleted_logs = 0
  deleted_video_bytes = 0
  cutoff = now() - timedelta(days=LOG_RETENTION_DAYS)
  with SessionLocal() as session:
    all_rows = session.scalars(select(StoredFile).order_by(StoredFile.uploaded_at.asc(), StoredFile.id.asc())).all()
    for row in all_rows:
      if not Path(row.storage_path).is_file():
        session.delete(row)
    session.flush()
    all_rows = [row for row in all_rows if Path(row.storage_path).is_file()]
    video_rows = [row for row in all_rows if file_class(row.relative_path) == "video"]
    video_total = sum(max(row.file_size or 0, 0) for row in video_rows if Path(row.storage_path).is_file())
    for row in video_rows:
      if video_total <= VIDEO_STORAGE_LIMIT_BYTES:
        break
      video_total -= remove_stored_file(session, row)
      deleted_videos += 1
      deleted_video_bytes += row.file_size or 0

    log_rows = [row for row in all_rows if row.uploaded_at < cutoff and file_class(row.relative_path) == "log"]
    for row in log_rows:
      remove_stored_file(session, row)
      deleted_logs += 1

    # Expired upload tokens and stale claims are safe to discard/reset. A
    # stale claim can happen if the API process dies during a large upload.
    stale_claim_cutoff = now() - timedelta(seconds=max(UPLOAD_TOKEN_TTL, 1800))
    for token in session.scalars(select(UploadToken)).all():
      if token.expires_at < now():
        session.delete(token)
      elif token.started_at is not None and token.started_at < stale_claim_cutoff:
        token.started_at = None
    session.commit()

  logger.info("storage_cleanup deleted_videos=%s deleted_video_bytes=%s deleted_logs=%s video_limit_bytes=%s", deleted_videos, deleted_video_bytes, deleted_logs, VIDEO_STORAGE_LIMIT_BYTES)
  return {"deleted_videos": deleted_videos, "deleted_video_bytes": deleted_video_bytes, "deleted_logs": deleted_logs}


cleanup_task: asyncio.Task | None = None
presence_task: asyncio.Task | None = None


async def cleanup_loop() -> None:
  while True:
    try:
      await asyncio.to_thread(cleanup_storage)
    except asyncio.CancelledError:
      raise
    except Exception:
      logger.exception("storage_cleanup_failed")
    await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
async def start_cleanup_task() -> None:
  global cleanup_task, presence_task
  # Run once immediately so an already-full NAS is corrected after restart.
  await asyncio.to_thread(cleanup_storage)
  cleanup_task = asyncio.create_task(cleanup_loop())
  presence_task = asyncio.create_task(presence_loop())


@app.on_event("shutdown")
async def stop_cleanup_task() -> None:
  global cleanup_task, presence_task
  if cleanup_task is not None:
    cleanup_task.cancel()
    try:
      await cleanup_task
    except asyncio.CancelledError:
      pass
    cleanup_task = None
  if presence_task is not None:
    presence_task.cancel()
    try:
      await presence_task
    except asyncio.CancelledError:
      pass
    presence_task = None


@app.post("/api/v1/admin/storage/cleanup")
def run_storage_cleanup(authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    user = get_app_user(authorization, session)
    if user.role != "admin":
      raise HTTPException(status_code=403, detail="admin role required")
  return cleanup_storage()


@app.get("/api/v1/admin/storage")
def storage_summary(authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    user = get_app_user(authorization, session)
    if user.role != "admin":
      raise HTTPException(status_code=403, detail="admin role required")
    rows = session.scalars(select(StoredFile)).all()
  totals = {"video": {"count": 0, "bytes": 0}, "log": {"count": 0, "bytes": 0}, "trajectory": {"count": 0, "bytes": 0}}
  for row in rows:
    kind = file_class(row.relative_path)
    totals[kind]["count"] += 1
    totals[kind]["bytes"] += row.file_size or 0
  usage = shutil.disk_usage(STORAGE_ROOT)
  return {"totals": totals, "video_limit_bytes": VIDEO_STORAGE_LIMIT_BYTES, "log_retention_days": LOG_RETENTION_DAYS, "storage_free_bytes": usage.free}


@app.put("/storage/put/{raw_token}")
async def put_file(raw_token: str, request: Request):
  with SessionLocal() as session:
    token = session.scalar(select(UploadToken).where(UploadToken.token_hash == sha256_text(raw_token)).with_for_update())
    if token is None or token.used_at is not None or token.expires_at < now():
      raise HTTPException(status_code=401, detail="invalid or expired upload token")
    if token.started_at is not None:
      raise HTTPException(status_code=409, detail="upload token is already being used")
    token.started_at = now()
    session.commit()
    device_id, relative_path = token.device_id, token.relative_path
  target = storage_path_for(device_id, relative_path)
  transfer_started = time.perf_counter()
  target.parent.mkdir(parents=True, exist_ok=True)
  temp_target = target.with_name(target.name + ".part")
  total = 0
  digest = hashlib.sha256()
  try:
    with temp_target.open("wb") as output:
      async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
          raise HTTPException(status_code=413, detail="file too large")
        digest.update(chunk)
        output.write(chunk)
    temp_target.replace(target)
  except Exception:
    temp_target.unlink(missing_ok=True)
    with SessionLocal() as session:
      token = session.scalar(select(UploadToken).where(UploadToken.token_hash == sha256_text(raw_token)))
      if token is not None and token.used_at is None:
        token.started_at = None
        session.commit()
    logger.exception("upload_failed device_id=%s path=%s", device_id, relative_path)
    raise
  with SessionLocal() as session:
    token = session.scalar(select(UploadToken).where(UploadToken.token_hash == sha256_text(raw_token)).with_for_update())
    if token is None or token.used_at is not None:
      raise HTTPException(status_code=409, detail="upload token already used")
    row = session.scalar(select(StoredFile).where(StoredFile.device_id == device_id, StoredFile.relative_path == relative_path))
    if row is None:
      row = StoredFile(device_id=device_id, relative_path=relative_path, storage_path=str(target), file_size=total, sha256=digest.hexdigest(), uploaded_at=now(), recorded_at=token.recorded_at)
      session.add(row)
    else:
      row.storage_path, row.file_size, row.sha256, row.uploaded_at = str(target), total, digest.hexdigest(), now()
      if token.recorded_at is not None:
        row.recorded_at = token.recorded_at
    token.used_at = now()
    session.commit()
    file_id = row.id
  transfer_seconds = max(time.perf_counter() - transfer_started, 0.001)
  transfer_bps = total / transfer_seconds
  with upload_metrics_lock:
    upload_metrics[device_id] = {
      "path": relative_path, "bytes": total, "duration_seconds": round(transfer_seconds, 3),
      "bytes_per_second": round(transfer_bps, 1), "recorded_at": now().isoformat(),
    }
  logger.info("upload_completed device_id=%s path=%s bytes=%s duration_s=%.3f rate_mbps=%.3f sha256=%s", device_id, relative_path, total, transfer_seconds, transfer_bps * 8 / 1000 / 1000, digest.hexdigest())
  await broadcast_admin({"type": "file", "action": "uploaded", "device_id": device_id, "file_id": file_id, "path": relative_path, "size_bytes": total})
  return {"file_id": file_id, "path": relative_path, "size_bytes": total, "sha256": digest.hexdigest()}


@app.get("/v1.1/devices/{device_id}/")
def native_device_status(device_id: str, authorization: Annotated[str | None, Header()] = None):
  """Minimal comma API compatibility for the on-device setup screen."""
  with SessionLocal() as session:
    device = get_device_from_jwt(authorization, session)
    if device.device_id != device_id:
      raise HTTPException(status_code=403, detail="device mismatch")
    return {"dongle_id": device.device_id, "is_paired": True, "prime_type": 0}


@app.get("/v1/devices/{device_id}/owner")
def native_device_owner(device_id: str, authorization: Annotated[str | None, Header()] = None):
  """Local-only Prime points response; no cloud account data is exposed."""
  with SessionLocal() as session:
    device = get_device_from_jwt(authorization, session)
    if device.device_id != device_id:
      raise HTTPException(status_code=403, detail="device mismatch")
    return {"points": 0}

def app_or_device(authorization: str | None, session: Session) -> tuple[AppUser | None, Device | None]:
  if authorization and authorization.startswith("Bearer "):
    return get_app_user(authorization, session), None
  return None, get_device_from_jwt(authorization, session)


def parse_device_timestamp(value: Any) -> datetime | None:
  try:
    timestamp = float(value)
    if timestamp < 946684800 or timestamp > time.time() + 300:
      return None
    return datetime.fromtimestamp(timestamp, timezone.utc)
  except (TypeError, ValueError, OverflowError, OSError):
    return None


def persist_vehicle_state(device_id: str, state: dict[str, Any], captured_at: Any = None) -> datetime:
  received_at = now()
  with SessionLocal() as session:
    device = session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None:
      raise HTTPException(status_code=404, detail="device not found")
    device.state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    device.last_state_at = received_at
    device.last_state_device_at = parse_device_timestamp(captured_at)
    device.last_seen_at = received_at
    session.commit()
  return received_at


@app.get("/api/v1/devices")
def list_devices(authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
    rows = session.scalars(select(Device).order_by(Device.name)).all()
    cutoff = now() - timedelta(seconds=45)
    return [{"device_id": d.device_id, "name": d.name, "last_seen_at": d.last_seen_at, "last_state_at": d.last_state_at, "last_state_device_at": d.last_state_device_at, "state": json.loads(d.state_json) if d.state_json else None, "revoked": d.revoked, "online": d.device_id in athena_connections and (d.last_seen_at is None or d.last_seen_at >= cutoff)} for d in rows]


@app.get("/api/v1/devices/{device_id}")
def device_status(device_id: str, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
    device = session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None:
      raise HTTPException(status_code=404, detail="device not found")
    cutoff = now() - timedelta(seconds=45)
    return {"device_id": device.device_id, "name": device.name, "last_seen_at": device.last_seen_at, "last_state_at": device.last_state_at, "last_state_device_at": device.last_state_device_at, "state": json.loads(device.state_json) if device.state_json else None, "revoked": device.revoked, "online": device.device_id in athena_connections and (device.last_seen_at is None or device.last_seen_at >= cutoff)}


@app.get("/api/v1/devices/{device_id}/files")
def list_files(device_id: str, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    user, device = app_or_device(authorization, session)
    if device is not None and device.device_id != device_id:
      raise HTTPException(status_code=403, detail="device mismatch")
    rows = session.scalars(select(StoredFile).where(StoredFile.device_id == device_id).order_by(StoredFile.uploaded_at.desc())).all()
    result = []
    for row in rows:
      metadata = file_metadata(row.relative_path)
      if row.recorded_at is not None:
        metadata.update({"recorded_at": row.recorded_at, "recorded_at_precision": "second", "recorded_at_source": "device_file_mtime"})
      result.append({"id": row.id, "path": row.relative_path, "size_bytes": row.file_size, "sha256": row.sha256, "uploaded_at": row.uploaded_at, **metadata})
    return result


@app.get("/api/v1/devices/{device_id}/state")
def device_state(device_id: str, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
    device = session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None:
      raise HTTPException(status_code=404, detail="device not found")
    cutoff = now() - timedelta(seconds=45)
    return {"device_id": device.device_id, "online": device.device_id in athena_connections and (device.last_seen_at is None or device.last_seen_at >= cutoff), "last_state_at": device.last_state_at, "last_state_device_at": device.last_state_device_at, "state": json.loads(device.state_json) if device.state_json else None}


@app.get("/api/v1/devices/{device_id}/routes")
def list_routes(device_id: str, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
    rows = session.scalars(select(StoredFile).where(StoredFile.device_id == device_id)).all()
    routes = sorted({row.relative_path.split("/", 1)[0] for row in rows if "/" in row.relative_path}, reverse=True)
    return [{"route_name": route, "file_count": sum(row.relative_path.startswith(route + "/") for row in rows)} for route in routes]


@app.get("/api/v1/files/{file_id}/download")
def download_file(file_id: int, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    user, device = app_or_device(authorization, session)
    row = session.get(StoredFile, file_id)
    if row is None or (device is not None and row.device_id != device.device_id) or not Path(row.storage_path).is_file():
      raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(row.storage_path, filename=Path(row.relative_path).name)


@app.get("/api/v1/files/{file_id}/raw/{filename}")
def raw_file(file_id: int, filename: str, authorization: Annotated[str | None, Header()] = None):
  """Serve a raw file while keeping its original filename in the URL.

  openpilot's built-in replay parser identifies qlog/qcamera/rlog files from
  the URL filename, so a generic ``/download`` URL is not sufficient here.
  """
  with SessionLocal() as session:
    user, device = app_or_device(authorization, session)
    row = session.get(StoredFile, file_id)
    if row is None or not Path(row.storage_path).is_file() or Path(row.relative_path).name != filename:
      raise HTTPException(status_code=404, detail="file not found")
    if device is not None and row.device_id != device.device_id:
      raise HTTPException(status_code=403, detail="device mismatch")
    return FileResponse(row.storage_path, filename=filename, media_type=media_type_for(row.relative_path))


@app.get("/v1/route/{route_name}/files")
def native_route_files(route_name: str, authorization: Annotated[str | None, Header()] = None):
  """Compatibility index for openpilot's built-in route replay."""
  if "|" not in route_name:
    raise HTTPException(status_code=400, detail="invalid route name")
  device_id, timestamp = route_name.split("|", 1)
  with SessionLocal() as session:
    device = get_device_from_jwt(authorization, session)
    if device.device_id != device_id:
      raise HTTPException(status_code=403, detail="device mismatch")
    rows = session.scalars(select(StoredFile).where(StoredFile.device_id == device_id)).all()
  grouped: dict[str, list[str]] = {"qlog": [], "rlog": [], "qcamera": [], "camera": [], "dcamera": [], "ecamera": []}
  for row in rows:
    parts = row.relative_path.replace("\\", "/").split("/")
    if len(parts) < 2 or not parts[0].startswith(timestamp + "--"):
      continue
    filename = parts[-1]
    key = {"qlog.bz2": "qlog", "rlog.bz2": "rlog", "qcamera.ts": "qcamera", "fcamera.hevc": "camera", "dcamera.hevc": "dcamera", "ecamera.hevc": "ecamera"}.get(filename)
    if key is not None:
      grouped[key].append(f"{PUBLIC_BASE_URL}/api/v1/files/{row.id}/raw/{quote(filename)}")
  return grouped


def media_type_for(path: str) -> str:
  suffix = Path(path).suffix.lower()
  return {".ts": "video/mp2t", ".mp4": "video/mp4", ".m3u8": "application/vnd.apple.mpegurl", ".hevc": "video/h265", ".h265": "video/h265"}.get(suffix, "application/octet-stream")


def preview_path(row: StoredFile) -> Path:
  cache_root = STORAGE_ROOT / ".media-cache"
  cache_root.mkdir(parents=True, exist_ok=True)
  return cache_root / f"{row.sha256}.mp4"


def make_preview(row: StoredFile) -> Path:
  cached = preview_path(row)
  if cached.is_file() and cached.stat().st_size > 0:
    return cached
  source = Path(row.storage_path)
  # Keep an .mp4 suffix so FFmpeg can select the output container. A plain
  # ".part" filename makes FFmpeg fail with "Unable to find a suitable
  # output format" even when the input video is valid.
  temporary = cached.with_name(cached.name + ".part.mp4")
  suffix = source.suffix.lower()
  if suffix in {".hevc", ".h265"}:
    input_args = ["-f", "hevc", "-r", str(VIDEO_FPS), "-i", str(source)]
  else:
    input_args = ["-i", str(source)]
  # Only HEVC needs the hvc1 sample-entry tag.  Applying it to an arbitrary
  # container (for example a non-HEVC .ts upload) can make the remux invalid.
  tag_args = ["-tag:v", "hvc1"] if suffix in {".hevc", ".h265"} else []
  remux = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *input_args, "-an", "-c:v", "copy", *tag_args, "-movflags", "+faststart", "-f", "mp4", str(temporary)]
  try:
    subprocess.run(remux, check=True, timeout=300)
  except Exception:
    temporary.unlink(missing_ok=True)
    # Fallback: H.264 is much more widely supported by browsers.
    transcode = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *input_args, "-an", "-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart", "-f", "mp4", str(temporary)]
    subprocess.run(transcode, check=True, timeout=1800)
  temporary.replace(cached)
  return cached


@app.get("/api/v1/files/{file_id}/stream")
def stream_file(file_id: int, ticket: str | None = Query(default=None), authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    if ticket:
      get_media_ticket_user(ticket, file_id, session)
    else:
      get_app_user(authorization, session)
    row = session.get(StoredFile, file_id)
    if row is None or not Path(row.storage_path).is_file():
      raise HTTPException(status_code=404, detail="file not found")
    filename = Path(row.relative_path).name.replace('"', "_")
    if FFMPEG_ENABLED and Path(row.relative_path).suffix.lower() in {".hevc", ".h265", ".ts"}:
      try:
        preview = make_preview(row)
        return FileResponse(preview, media_type="video/mp4", headers={"Content-Disposition": f'inline; filename="{Path(filename).stem}.mp4"'})
      except Exception as exc:
        logger.exception("video_preview_failed file_id=%s path=%s", file_id, row.relative_path)
        raise HTTPException(status_code=415, detail="video preview could not be generated") from exc
    return FileResponse(row.storage_path, media_type=media_type_for(row.relative_path), headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.get("/api/v1/files/{file_id}/stream-url")
def stream_url(file_id: int, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    user = get_app_user(authorization, session)
    row = session.get(StoredFile, file_id)
    if row is None or not Path(row.storage_path).is_file():
      raise HTTPException(status_code=404, detail="file not found")
    return {"url": f"/api/v1/files/{file_id}/stream?ticket={quote(issue_media_ticket(user, file_id), safe='')}", "expires_in_seconds": MEDIA_TICKET_TTL_SECONDS}


@app.get("/api/v1/files/{file_id}/text")
def view_text_file(file_id: int, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
    row = session.get(StoredFile, file_id)
    if row is None or not Path(row.storage_path).is_file() or file_class(row.relative_path) != "log":
      raise HTTPException(status_code=404, detail="text log not found")
    if not row.relative_path.startswith("errors/"):
      raise HTTPException(status_code=400, detail="only error logs can be viewed as text")
  content = Path(row.storage_path).read_bytes()[:10 * 1024 * 1024].decode("utf-8", errors="replace")
  return PlainTextResponse(content)


@app.delete("/api/v1/files/{file_id}")
async def delete_file(file_id: int, authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    user = get_app_user(authorization, session)
    row = session.get(StoredFile, file_id)
    if row is None:
      raise HTTPException(status_code=404, detail="file not found")
    path = Path(row.storage_path)
    session.delete(row)
    session.commit()
  path.unlink(missing_ok=True)
  logger.warning("file_deleted file_id=%s device_id=%s username=%s", file_id, row.device_id, user.username)
  await broadcast_admin({"type": "file", "action": "deleted", "device_id": row.device_id, "file_id": file_id})
  return {"deleted": True, "file_id": file_id}


@dataclass
class AthenaConnection:
  websocket: WebSocket
  pending: dict[int, asyncio.Future] = field(default_factory=dict)
  next_id: int = 1


athena_connections: dict[str, AthenaConnection] = {}
upload_metrics: dict[str, dict[str, Any]] = {}
upload_metrics_lock = Lock()


@app.get("/api/v1/admin/network")
def network_summary(authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
  with upload_metrics_lock:
    recent_uploads = dict(upload_metrics)
  return {"recent_device_uploads": recent_uploads, "browser_test_mib": min(16, NETWORK_TEST_MAX_MIB)}


@app.get("/api/v1/admin/network-test")
def network_test(size_mib: int = Query(default=16, ge=1, le=32), authorization: Annotated[str | None, Header()] = None):
  with SessionLocal() as session:
    get_app_user(authorization, session)
  safe_size_mib = min(size_mib, NETWORK_TEST_MAX_MIB)
  body = os.urandom(safe_size_mib * 1024 * 1024)
  return Response(content=body, media_type="application/octet-stream", headers={"Cache-Control": "no-store, no-transform", "Content-Length": str(len(body))})


@app.post("/api/v1/admin/network-test/upload")
async def network_test_upload(request: Request, authorization: Annotated[str | None, Header()] = None):
  """Receive a disposable browser speed-test payload without writing it to disk."""
  with SessionLocal() as session:
    get_app_user(authorization, session)
  max_bytes = NETWORK_TEST_MAX_MIB * 1024 * 1024
  total = 0
  started = time.perf_counter()
  async for chunk in request.stream():
    total += len(chunk)
    if total > max_bytes:
      raise HTTPException(status_code=413, detail="network test payload too large")
  seconds = max(time.perf_counter() - started, 0.001)
  return {"bytes": total, "duration_seconds": round(seconds, 3), "bytes_per_second": round(total / seconds, 1)}
admin_connections: set[WebSocket] = set()


def json_time(value: datetime | None) -> str | None:
  return value.isoformat() if value is not None else None


async def broadcast_admin(event: dict[str, Any]) -> None:
  stale: list[WebSocket] = []
  for websocket in tuple(admin_connections):
    try:
      await websocket.send_json(event)
    except Exception as exc:
      logger.info("admin_ws_send_failed reason=%s", type(exc).__name__)
      stale.append(websocket)
  for websocket in stale:
    admin_connections.discard(websocket)


async def presence_loop() -> None:
  """Push presence changes/heartbeats so the console never relies on polling."""
  while True:
    await asyncio.sleep(5)
    cutoff = now() - timedelta(seconds=45)
    with SessionLocal() as session:
      rows = session.scalars(select(Device).order_by(Device.id)).all()
      events = [
        {
          "type": "device",
          "action": "presence",
          "device_id": row.device_id,
          "online": row.device_id in athena_connections and (row.last_seen_at is None or row.last_seen_at >= cutoff),
          "last_seen_at": json_time(row.last_seen_at),
        }
        for row in rows
      ]
    for event in events:
      await broadcast_admin(event)


@app.websocket("/api/v1/admin/ws")
async def admin_live(websocket: WebSocket):
  await websocket.accept()
  try:
    hello = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    token = hello.get("token") if isinstance(hello, dict) else None
    with SessionLocal() as session:
      get_app_user(f"Bearer {token}", session)
  except Exception as exc:
    logger.warning("admin_ws_rejected reason=%s", type(exc).__name__)
    await websocket.close(code=1008, reason="invalid admin token")
    return
  admin_connections.add(websocket)
  await websocket.send_json({"type": "connected"})
  try:
    while True:
      await websocket.receive_text()
  except WebSocketDisconnect:
    logger.info("admin_ws_disconnected")
  except Exception as exc:
    logger.info("admin_ws_closed reason=%s", type(exc).__name__)
  finally:
    admin_connections.discard(websocket)


async def send_athena_command(device_id: str, method: str, params: dict[str, Any] | None = None, timeout: float = 15) -> Any:
  connection = athena_connections.get(device_id)
  if connection is None:
    raise HTTPException(status_code=409, detail="device is offline")
  request_id = connection.next_id
  connection.next_id += 1
  future = asyncio.get_running_loop().create_future()
  connection.pending[request_id] = future
  await connection.websocket.send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
  try:
    return await asyncio.wait_for(future, timeout=timeout)
  finally:
    connection.pending.pop(request_id, None)


SAFE_COMMANDS = {"getVersion", "getNetworkType", "getVehicleState", "listUploadQueue", "listDataDirectory", "takeSnapshot", "reboot", "uploadFileToUrl", "uploadFilesToUrls", "cancelUpload", "setBandwithLimit"}


@app.post("/api/v1/devices/{device_id}/commands/{method}")
async def device_command(device_id: str, method: str, authorization: Annotated[str | None, Header()] = None, params: dict[str, Any] = Body(default_factory=dict)):
  with SessionLocal() as session:
    user = get_app_user(authorization, session)
    if method not in SAFE_COMMANDS:
      raise HTTPException(status_code=403, detail="command is not allowed")
    device = session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None or device.revoked:
      raise HTTPException(status_code=404, detail="device not found")
    command = DeviceCommand(device_id=device_id, username=user.username, method=method, params_json=json.dumps(params), created_at=now())
    session.add(command)
    session.commit()
    command_id = command.id
  try:
    state_received_at = None
    result = await send_athena_command(device_id, method, params)
    if method == "getVehicleState" and isinstance(result, dict):
      returned_state = result.get("state")
      has_state = isinstance(returned_state, dict)
      if has_state:
        state_received_at = persist_vehicle_state(device_id, returned_state, result.get("captured_at"))
        await broadcast_admin({"type": "state", "device_id": device_id, "state": returned_state, "last_state_at": json_time(state_received_at)})
      logger.info("vehicle_state_refresh device_id=%s command_id=%s available=%s has_state=%s error=%s diagnostics=%s", device_id, command_id, result.get("available"), has_state, result.get("error"), result.get("diagnostics", {}))
    status = "success"
    error = None
  except Exception as exc:
    result = None
    status = "failed"
    error = str(exc)
  with SessionLocal() as session:
    command = session.get(DeviceCommand, command_id)
    command.status = status
    command.result_json = json.dumps(result if error is None else {"error": error})
    command.completed_at = now()
    session.commit()
  if error is not None:
    logger.warning("device_command_failed device_id=%s method=%s command_id=%s error=%s", device_id, method, command_id, error)
    raise HTTPException(status_code=502, detail=error)
  logger.info("device_command_success device_id=%s method=%s command_id=%s", device_id, method, command_id)
  return {"command_id": command_id, "status": status, "result": result, "state_received_at": json_time(state_received_at)}


@app.websocket("/ws/v2/{device_id}")
async def athena(device_id: str, websocket: WebSocket):
  token = websocket.cookies.get("jwt")
  if not token:
    logger.warning("athena_rejected device_id=%s reason=missing_jwt", device_id)
    await websocket.close(code=1008, reason="missing jwt")
    return
  with SessionLocal() as session:
    device = session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None or device.revoked:
      await websocket.close(code=1008, reason="unknown device")
      return
    try:
      payload = jwt.decode(token, device.public_key, algorithms=["RS256"], leeway=JWT_CLOCK_SKEW_SECONDS, options={"require": ["identity", "iat", "exp"]})
      if payload.get("identity") != device_id:
        raise ValueError("identity mismatch")
    except Exception as exc:
      logger.warning("athena_rejected device_id=%s reason=%s key_fingerprint=%s timing=%s", device_id, type(exc).__name__, public_key_fingerprint(device.public_key), jwt_timing_summary(token))
      await websocket.close(code=1008, reason="invalid jwt")
      return
    device.last_seen_at = now()
    session.commit()
  await websocket.accept()
  connection = AthenaConnection(websocket)
  old = athena_connections.get(device_id)
  if old is not None:
    await old.websocket.close(code=1012, reason="replaced by newer connection")
  athena_connections[device_id] = connection
  logger.info("athena_connected device_id=%s", device_id)
  await broadcast_admin({"type": "device", "action": "online", "device_id": device_id, "online": True, "last_seen_at": json_time(device.last_seen_at)})
  try:
    while True:
      message = json.loads(await websocket.receive_text())
      seen_at = now()
      with SessionLocal() as session:
        device_row = session.scalar(select(Device).where(Device.device_id == device_id))
        if device_row is not None:
          device_row.last_seen_at = seen_at
          session.commit()
      await broadcast_admin({"type": "device", "action": "presence", "device_id": device_id, "online": True, "last_seen_at": json_time(seen_at)})
      if "id" in message and ("result" in message or "error" in message):
        future = connection.pending.get(int(message["id"]))
        if future is not None and not future.done():
          future.set_result(message.get("result") if "result" in message else {"error": message["error"]})
      elif message.get("method") == "vehicleState":
        state = message.get("params", {}).get("state")
        if isinstance(state, dict):
          state_time = persist_vehicle_state(device_id, state, message.get("params", {}).get("captured_at"))
          await broadcast_admin({"type": "state", "device_id": device_id, "state": state, "last_state_at": json_time(state_time)})
          logger.info("athena_vehicle_state device_id=%s fields=%s", device_id, sorted(state.keys()))
      elif message.get("method") == "vehicleStateHeartbeat":
        with SessionLocal() as session:
          device_row = session.scalar(select(Device).where(Device.device_id == device_id))
          if device_row is not None:
            device_row.last_seen_at = now()
            session.commit()
      elif message.get("method") == "vehicleStateDiagnostics":
        diagnostics = message.get("params", {}).get("diagnostics", {})
        logger.info("athena_vehicle_state_diagnostics device_id=%s diagnostics=%s", device_id, diagnostics)
      elif message.get("method") == "forwardLogs":
        params = message.get("params", {})
        logs = params.get("logs", "")
        recorded_at = None
        source_mtime_ms = params.get("source_mtime_ms")
        if isinstance(source_mtime_ms, (int, float)):
          try:
            recorded_at = datetime.fromtimestamp(float(source_mtime_ms) / 1000, tz=timezone.utc)
          except (OverflowError, OSError, ValueError):
            logger.warning("athena_log_invalid_mtime device_id=%s value=%r", device_id, source_mtime_ms)
        if isinstance(logs, str) and logs:
          content = logs.encode("utf-8", errors="replace")[:10 * 1024 * 1024]
          relative_path = f"errors/athena-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.log"
          target = storage_path_for(device_id, relative_path)
          target.parent.mkdir(parents=True, exist_ok=True)
          target.write_bytes(content)
          with SessionLocal() as session:
            row = StoredFile(device_id=device_id, relative_path=relative_path, storage_path=str(target), file_size=len(content), sha256=hashlib.sha256(content).hexdigest(), uploaded_at=now(), recorded_at=recorded_at)
            session.add(row)
            session.commit()
            log_file_id = row.id
          await broadcast_admin({"type": "file", "action": "uploaded", "device_id": device_id, "file_id": log_file_id, "path": relative_path, "size_bytes": len(content)})
          logger.info("athena_log_stored device_id=%s path=%s bytes=%s", device_id, relative_path, len(content))
        if "id" in message:
          await websocket.send_json({"jsonrpc": "2.0", "id": message["id"], "result": {"success": 1}})
      elif message.get("method") == "storeStats":
        logger.info("athena_notification device_id=%s method=storeStats", device_id)
        if "id" in message:
          await websocket.send_json({"jsonrpc": "2.0", "id": message["id"], "result": {"success": 1}})
      elif "id" in message:
        await websocket.send_json({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "method not found"}})
  except WebSocketDisconnect:
    logger.info("athena_disconnected device_id=%s", device_id)
  except (json.JSONDecodeError, ValueError):
    logger.warning("athena_disconnected device_id=%s reason=invalid_json_rpc", device_id)
  finally:
    if athena_connections.get(device_id) is connection:
      athena_connections.pop(device_id, None)
      await broadcast_admin({"type": "device", "action": "offline", "device_id": device_id, "online": False, "last_seen_at": json_time(now())})
    for future in connection.pending.values():
      if not future.done():
        future.set_exception(ConnectionError("Athena connection closed"))
