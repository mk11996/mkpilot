#!/usr/bin/env python3
import json
import os
import random
import requests
import threading
import time
import traceback
from pathlib import Path

from cereal import log
import cereal.messaging as messaging
from common.api import Api
from common.params import Params
from selfdrive.hardware import TICI
from selfdrive.loggerd.xattr_cache import getxattr, setxattr
from selfdrive.loggerd.config import ROOT
from selfdrive.swaglog import cloudlog

NetworkType = log.DeviceState.NetworkType
# Use a server-specific marker. The upstream marker may already be set on
# files uploaded to comma's servers; reusing it would make a migration to the
# self-hosted server silently skip all existing route files.
UPLOAD_ATTR_NAME = 'user.openpilot_local_upload'
UPLOAD_ATTR_VALUE = b'1'

def env_bool(name, default=False):
  value = os.getenv(name)
  if value is None:
    return default
  return value.strip().lower() not in ("0", "false", "no", "off", "")


allow_sleep = env_bool("UPLOADER_SLEEP", True)
force_wifi = os.getenv("FORCEWIFI") is not None
fake_upload = os.getenv("FAKEUPLOAD") is not None


def get_directory_sort(d):
  return list(map(lambda s: s.rjust(10, '0'), d.rsplit('--', 1)))

def listdir_by_creation(d):
  try:
    paths = os.listdir(d)
    paths = sorted(paths, key=get_directory_sort)
    return paths
  except OSError:
    cloudlog.exception("listdir_by_creation failed")
    return list()

def clear_locks(root):
  for logname in os.listdir(root):
    path = os.path.join(root, logname)
    try:
      for fname in os.listdir(path):
        if fname.endswith(".lock"):
          os.unlink(os.path.join(path, fname))
    except OSError:
      cloudlog.exception("clear_locks failed")


class Uploader():
  def __init__(self, dongle_id, root):
    self.dongle_id = dongle_id
    self.api = Api(dongle_id)
    self.root = root
    # The custom vehicle logger writes permanent trajectory CSV files outside
    # the native route tree. They use a virtual "trajectory/" prefix on the
    # server and are uploaded only after they have been stable for a short
    # period, so an actively growing CSV is not finalized prematurely.
    configured_trajectory_root = os.getenv("VEHICLE_LOG_DIR", "/data/logs")
    self.trajectory_roots = []
    for candidate in (configured_trajectory_root, "/data/logs", "/data/media/0/logs"):
      if candidate not in self.trajectory_roots:
        self.trajectory_roots.append(candidate)

    self.upload_thread = None

    self.last_resp = None
    self.last_exc = None

    self.immediate_size = 0
    self.immediate_count = 0

    # stats for last successfully uploaded file
    self.last_time = 0
    self.last_speed = 0
    self.last_filename = ""

    self.immediate_folders = ["crash/", "boot/"]
    self.immediate_priority = {"qlog.bz2": 0, "qcamera.ts": 1}

  def get_upload_sort(self, name):
    if name in self.immediate_priority:
      return self.immediate_priority[name]
    return 1000

  def list_upload_files(self):
    if not os.path.isdir(self.root):
      return

    self.immediate_size = 0
    self.immediate_count = 0

    for logname in listdir_by_creation(self.root):
      path = os.path.join(self.root, logname)
      try:
        names = os.listdir(path)
      except OSError:
        continue

      if any(name.endswith(".lock") for name in names):
        continue

      for name in sorted(names, key=self.get_upload_sort):
        key = os.path.join(logname, name)
        fn = os.path.join(path, name)
        # skip files already uploaded
        try:
          is_uploaded = getxattr(fn, UPLOAD_ATTR_NAME)
        except OSError:
          cloudlog.event("uploader_getxattr_failed", exc=self.last_exc, key=key, fn=fn)
          is_uploaded = True  # deleter could have deleted
        if is_uploaded:
          continue

        try:
          if name in self.immediate_priority:
            self.immediate_count += 1
            self.immediate_size += os.path.getsize(fn)
        except OSError:
          pass

        yield (name, key, fn)

    for trajectory_root in self.trajectory_roots:
      if not os.path.isdir(trajectory_root):
        continue
      for dirpath, _, filenames in os.walk(trajectory_root):
        for name in sorted(filenames):
          if not name.lower().endswith(".csv") or name.endswith(".part"):
            continue
          fn = os.path.join(dirpath, name)
          try:
            stat = os.stat(fn)
            # Avoid uploading the CSV while vehicle_data_logger is still
            # writing the current drive/session file.
            if time.time() - stat.st_mtime < 30:
              continue
            relative = os.path.relpath(fn, trajectory_root).replace(os.sep, "/")
            key = "trajectory/" + relative
            expected_marker = f"{stat.st_size}:{stat.st_mtime_ns}".encode()
            try:
              is_uploaded = getxattr(fn, UPLOAD_ATTR_NAME)
            except OSError:
              # Some Android data mounts do not support user xattrs. Keep a
              # sidecar marker so CSV files are not silently skipped.
              is_uploaded = None
            sidecar = Path(fn + ".uploaded")
            sidecar_marker = sidecar.read_bytes() if sidecar.is_file() else None
            if is_uploaded == expected_marker or sidecar_marker == expected_marker:
              continue
          except OSError:
            continue
          yield (name, key, fn)

  def next_file_to_upload(self):
    upload_files = list(self.list_upload_files())

    for name, key, fn in upload_files:
      if any(f in fn for f in self.immediate_folders):
        return (key, fn)

    for name, key, fn in upload_files:
      if name in self.immediate_priority:
        return (key, fn)

    # CSV trajectories must not wait behind an ever-growing backlog of route
    # files; they are small and are the user's permanent driving data.
    for _, key, fn in upload_files:
      if key.startswith("trajectory/"):
        return (key, fn)

    # Do not leave non-qcamera streams or other route files stranded. The
    # previous implementation returned None here, so fcamera/dcamera/ecamera
    # and ordinary log files were never uploaded unless they were in boot/.
    if upload_files:
      _, key, fn = upload_files[0]
      return (key, fn)

    return None

  def do_upload(self, key, fn):
    try:
      source_mtime_ms = int(os.stat(fn).st_mtime * 1000)
      url_resp = self.api.get("v1.4/" + self.dongle_id + "/upload_url/", timeout=10, path=key,
                              source_mtime_ms=source_mtime_ms, access_token=self.api.get_token())
      if url_resp.status_code == 412:
        self.last_resp = url_resp
        return
      if url_resp.status_code != 200:
        cloudlog.error("upload_url_failed status=%s body=%s key=%s", url_resp.status_code, url_resp.text[:500], key)
        self.last_resp = url_resp
        return

      url_resp_json = json.loads(url_resp.text)
      url = url_resp_json['url']
      headers = url_resp_json['headers']
      cloudlog.debug("upload_url v1.4 %s %s", url, str(headers))

      if fake_upload:
        cloudlog.debug(f"*** WARNING, THIS IS A FAKE UPLOAD TO {url} ***")

        class FakeResponse():
          def __init__(self):
            self.status_code = 200

        self.last_resp = FakeResponse()
      else:
        with open(fn, "rb") as f:
          self.last_resp = requests.put(url, data=f, headers=headers, timeout=10)
    except Exception as e:
      self.last_exc = (e, traceback.format_exc())
      raise

  def normal_upload(self, key, fn):
    self.last_resp = None
    self.last_exc = None

    try:
      self.do_upload(key, fn)
    except Exception:
      pass

    return self.last_resp

  def upload(self, key, fn, network_type):
    try:
      sz = os.path.getsize(fn)
    except OSError:
      cloudlog.exception("upload: getsize failed")
      return False

    cloudlog.event("upload_start", key=key, fn=fn, sz=sz, network_type=network_type)
    is_trajectory = key.startswith("trajectory/")
    try:
      trajectory_stat = os.stat(fn) if is_trajectory else None
      upload_marker = (f"{trajectory_stat.st_size}:{trajectory_stat.st_mtime_ns}".encode()
                       if trajectory_stat is not None else UPLOAD_ATTR_VALUE)
    except OSError:
      return False

    if sz == 0:
      try:
        # tag files of 0 size as uploaded
        setxattr(fn, UPLOAD_ATTR_NAME, upload_marker)
      except OSError:
        cloudlog.event("uploader_setxattr_failed", exc=self.last_exc, key=key, fn=fn, sz=sz)
      success = True
    else:
      start_time = time.monotonic()
      stat = self.normal_upload(key, fn)
      if stat is not None and stat.status_code in (200, 201, 403, 412):
        try:
          # tag file as uploaded
          setxattr(fn, UPLOAD_ATTR_NAME, upload_marker)
        except OSError:
          cloudlog.event("uploader_setxattr_failed", exc=self.last_exc, key=key, fn=fn, sz=sz)
        if is_trajectory:
          try:
            Path(fn + ".uploaded").write_bytes(upload_marker)
          except OSError:
            cloudlog.event("uploader_trajectory_marker_failed", key=key, fn=fn, sz=sz)

        self.last_filename = fn
        self.last_time = time.monotonic() - start_time
        self.last_speed = (sz / 1e6) / self.last_time
        success = True
        cloudlog.event("upload_success" if stat.status_code != 412 else "upload_ignored", key=key, fn=fn, sz=sz, network_type=network_type)
      else:
        success = False
        cloudlog.event("upload_failed", stat=stat, exc=self.last_exc, key=key, fn=fn, sz=sz, network_type=network_type)

    return success

  def get_msg(self):
    msg = messaging.new_message("uploaderState")
    us = msg.uploaderState
    us.immediateQueueSize = int(self.immediate_size / 1e6)
    us.immediateQueueCount = self.immediate_count
    us.lastTime = self.last_time
    us.lastSpeed = self.last_speed
    us.lastFilename = self.last_filename
    return msg

def uploader_fn(exit_event):
  params = Params()
  dongle_id = params.get("DongleId", encoding='utf8')

  if dongle_id is None:
    cloudlog.info("uploader missing dongle_id")
    raise Exception("uploader can't start without dongle id")

  if TICI and not Path("/data/media").is_mount():
    cloudlog.warning("NVME not mounted")

  cloudlog.info("uploader_started dongle_id=%s root=%s", dongle_id, ROOT)

  sm = messaging.SubMaster(['deviceState'])
  pm = messaging.PubMaster(['uploaderState'])
  uploader = Uploader(dongle_id, ROOT)

  backoff = 0.1
  while not exit_event.is_set():
    sm.update(0)
    offroad = params.get_bool("IsOffroad")
    network_type = sm['deviceState'].networkType if not force_wifi else NetworkType.wifi
    if network_type == NetworkType.none:
      if allow_sleep:
        cloudlog.warning("uploader_network_none offroad=%s", offroad)
        time.sleep(60 if offroad else 5)
      continue

    d = uploader.next_file_to_upload()
    if d is None:  # Nothing to upload
      if allow_sleep:
        cloudlog.debug("uploader_queue_empty root=%s", ROOT)
        time.sleep(60 if offroad else 5)
      continue

    key, fn = d

    success = uploader.upload(key, fn, sm['deviceState'].networkType.raw)
    if success:
      backoff = 0.1
    elif allow_sleep:
      cloudlog.info("upload backoff %r", backoff)
      time.sleep(backoff + random.uniform(0, backoff))
      backoff = min(backoff*2, 120)

    pm.send("uploaderState", uploader.get_msg())

def main():
  uploader_fn(threading.Event())


if __name__ == "__main__":
  main()
