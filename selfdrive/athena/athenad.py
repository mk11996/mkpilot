#!/usr/bin/env python3
import base64
import hashlib
import io
import json
import math
import os
import queue
import random
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import namedtuple
from datetime import datetime
from functools import partial
from typing import Any, Dict

import requests
from jsonrpc import JSONRPCResponseManager, dispatcher
from websocket import (ABNF, WebSocketException, WebSocketTimeoutException,
                       create_connection)

import cereal.messaging as messaging
from cereal import log
from cereal.services import service_list
from common.api import Api
from common.api import API_HOST
from common.basedir import PERSIST
from common.file_helpers import CallbackReader
from common.params import Params
from common.realtime import sec_since_boot
from selfdrive.hardware import HARDWARE, PC, TICI
from selfdrive.loggerd.config import ROOT
from selfdrive.loggerd.xattr_cache import getxattr, setxattr
from selfdrive.statsd import STATS_DIR
from selfdrive.swaglog import SWAGLOG_DIR, cloudlog
from selfdrive.version import get_commit, get_origin, get_short_branch, get_version

_default_athena_host = API_HOST.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
ATHENA_HOST = os.getenv('ATHENA_HOST', _default_athena_host).rstrip('/')
HANDLER_THREADS = int(os.getenv('HANDLER_THREADS', "4"))
LOCAL_PORT_WHITELIST = {8022}

LOG_ATTR_NAME = 'user.upload'
LOG_ATTR_VALUE_MAX_UNIX_TIME = int.to_bytes(2147483647, 4, sys.byteorder)
RECONNECT_TIMEOUT_S = 70

RETRY_DELAY = 10  # seconds
MAX_RETRY_COUNT = 30  # Try for at most 5 minutes if upload fails immediately
MAX_AGE = 31 * 24 * 3600  # seconds
WS_FRAME_SIZE = 4096
OP_TEMP_REPORT_INTERVAL_S = 60
AMBIENT_TEMP_REPORT_INTERVAL_S = 60
VOLTAGE_REPORT_INTERVAL_S = 10
LOCATION_REPORT_MIN_INTERVAL_S = 10
ATHENA_HEARTBEAT_INTERVAL_S = 20
PANDA_DISCONNECT_GRACE_S = 5  # tolerate transient Panda status glitches before clearing state
VEHICLE_STATE_DIAGNOSTICS_INTERVAL_S = 30
LOCATION_MOVE_THRESHOLD_M = 25
LOCATION_STATIONARY_SPEED_MPS = 0.8
LOCATION_STATIONARY_SETTLE_S = 20
LOCATION_STATIONARY_REFRESH_INTERVAL_S = 60
LOCATION_STATIONARY_CORRECTION_M = 5
VEHICLE_STATE_CACHE_PARAM = "VehicleStateCache"
CAR_STATE_FRESHNESS_S = 3
HAZARD_LIGHTS_HOLD_S = 3

NetworkType = log.DeviceState.NetworkType

dispatcher["echo"] = lambda s: s
recv_queue: Any = queue.Queue()
send_queue: Any = queue.Queue()
upload_queue: Any = queue.Queue()
low_priority_send_queue: Any = queue.Queue()
log_recv_queue: Any = queue.Queue()
cancelled_uploads: Any = set()
vehicle_state_lock = threading.Lock()
# Keep these unannotated: the device runtime is Python 3.8.
latest_vehicle_state = None
latest_vehicle_state_at = None
latest_vehicle_diagnostics = {}
latest_vehicle_state_error = None
athena_connected_event = threading.Event()
panda_connected_event = threading.Event()
UploadItem = namedtuple('UploadItem', ['path', 'url', 'headers', 'created_at', 'id', 'retry_count', 'current', 'progress', 'allow_cellular'], defaults=(0, False, 0, False))

cur_upload_items: Dict[int, Any] = {}

class AbortTransferException(Exception):
  pass


class UploadQueueCache():
  params = Params()

  @staticmethod
  def initialize(upload_queue):
    try:
      upload_queue_json = UploadQueueCache.params.get("AthenadUploadQueue")
      if upload_queue_json is not None:
        for item in json.loads(upload_queue_json):
          upload_queue.put(UploadItem(**item))
    except Exception:
      cloudlog.exception("athena.UploadQueueCache.initialize.exception")

  @staticmethod
  def cache(upload_queue):
    try:
      items = [i._asdict() for i in upload_queue.queue if i.id not in cancelled_uploads]
      UploadQueueCache.params.put("AthenadUploadQueue", json.dumps(items))
    except Exception:
      cloudlog.exception("athena.UploadQueueCache.cache.exception")


def enqueue_cached_vehicle_state():
  """Send the last persisted state immediately after Athena reconnects."""
  try:
    # A cached vehicle state is only valid for upload while a real Panda/car
    # connection is currently present. Do not report stale/default values when
    # the device is powered on away from the vehicle.
    if not panda_connected_event.is_set():
      return
    cached = Params().get(VEHICLE_STATE_CACHE_PARAM, encoding='utf8')
    if not cached:
      return
    payload = json.loads(cached)
    if isinstance(payload, dict) and isinstance(payload.get("state"), dict):
      send_queue.put_nowait(json.dumps({
        "jsonrpc": "2.0",
        "method": "vehicleState",
        "params": {
          "state": payload["state"],
          "captured_at": payload.get("captured_at"),
          "cached": True,
        },
      }))
      cloudlog.info("athena.vehicle_state_cache.sent captured_at=%s", payload.get("captured_at"))
  except Exception:
    cloudlog.exception("athena.vehicle_state_cache.load.exception")


def handle_long_poll(ws):
  end_event = threading.Event()
  athena_connected_event.set()
  enqueue_cached_vehicle_state()

  threads = [
    threading.Thread(target=ws_recv, args=(ws, end_event), name='ws_recv'),
    threading.Thread(target=ws_send, args=(ws, end_event), name='ws_send'),
    threading.Thread(target=upload_handler, args=(end_event,), name='upload_handler'),
    threading.Thread(target=log_handler, args=(end_event,), name='log_handler'),
    threading.Thread(target=stat_handler, args=(end_event,), name='stat_handler'),
  ] + [
    threading.Thread(target=jsonrpc_handler, args=(end_event,), name=f'worker_{x}')
    for x in range(HANDLER_THREADS)
  ]

  for thread in threads:
    thread.start()
  try:
    while not end_event.is_set():
      time.sleep(0.1)
  except (KeyboardInterrupt, SystemExit):
    end_event.set()
    raise
  finally:
    athena_connected_event.clear()
    for thread in threads:
      cloudlog.debug(f"athena.joining {thread.name}")
      thread.join()


def _finite_number(value):
  try:
    value = float(value)
    return value if math.isfinite(value) else None
  except (TypeError, ValueError):
    return None


def _gear_label(value):
  text = str(value).lower().split('.')[-1]
  return {
    'park': 'P', 'drive': 'D', 'neutral': 'N', 'reverse': 'R',
    'sport': 'S', 'low': 'L', 'manual': 'M', 'eco': 'E',
  }.get(text, text.upper() if text not in ('unknown', 'none', '') else '-')


def _distance_meters(first, second):
  if not first or not second:
    return None
  try:
    lat1, lon1 = math.radians(float(first['latitude'])), math.radians(float(first['longitude']))
    lat2, lon2 = math.radians(float(second['latitude'])), math.radians(float(second['longitude']))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
  except (KeyError, TypeError, ValueError):
    return None


def vehicle_state_reporter(end_event):
  """Continuously collect and persist state; send changes when Athena is available."""
  global latest_vehicle_state, latest_vehicle_state_at, latest_vehicle_diagnostics, latest_vehicle_state_error
  sm = messaging.SubMaster(['carState', 'deviceState', 'pandaStates', 'peripheralState', 'liveLocationKalman'])
  last_state = None
  last_reported_cpu_temperature = None
  last_cpu_temperature_report_at = 0.0
  last_reported_ambient_temperature = None
  last_ambient_temperature_report_at = 0.0
  last_reported_voltage = None
  last_reported_voltage_raw_mv = None
  last_reported_voltage_source = None
  last_voltage_report_at = 0.0
  last_reported_location = None
  last_location_report_at = 0.0
  last_location_captured_at = None
  stationary_since = None
  last_stationary_location_refresh_at = 0.0
  last_car_state_update_at = 0.0
  last_fresh_engine_rpm = None
  last_fresh_engine_rpm_at = 0.0
  lights_snapshot_received = False
  last_hazard_lights_seen_at = 0.0
  hazard_lights_active = False
  last_athena_heartbeat_at = 0.0
  last_vehicle_state_diagnostics_at = 0.0
  last_athena_connection = False
  panda_disconnected_since = None
  stage = "initializing"
  try:
    cached = Params().get(VEHICLE_STATE_CACHE_PARAM, encoding='utf8')
    payload = json.loads(cached) if cached else {}
    if isinstance(payload, dict) and isinstance(payload.get("state"), dict):
      last_state = payload["state"]
      last_reported_location = last_state.get("location")
      last_location_captured_at = last_state.get("location_captured_at")
      last_reported_cpu_temperature = last_state.get("cpu_temperature_c")
      with vehicle_state_lock:
        latest_vehicle_state = dict(last_state)
        latest_vehicle_state_at = payload.get("captured_at")
  except Exception:
    cloudlog.exception("athena.vehicle_state_cache.initialize.exception")
  while not end_event.is_set():
    try:
      stage = "waiting for messages"
      sm.update(1000)
      diagnostics = {}
      for name in ('carState', 'deviceState', 'pandaStates', 'peripheralState', 'liveLocationKalman'):
        diagnostics[name] = {
          "updated": bool(sm.updated.get(name, False)),
          "valid": bool(sm.valid.get(name, False)),
          "rcv_frame": int(sm.rcv_frame.get(name, -1)),
        }
      with vehicle_state_lock:
        latest_vehicle_diagnostics = diagnostics
        latest_vehicle_state_error = None
      # Connection presence is independent of Panda/car validity. Without this
      # heartbeat an offroad device has a live WebSocket but is incorrectly
      # marked offline by the server after its presence timeout.
      monotonic_now = time.monotonic()
      if sm.updated.get('carState', False):
        last_car_state_update_at = monotonic_now
      athena_now_connected = athena_connected_event.is_set()
      # Force one complete report after Athena reconnects. A cached state may be
      # unchanged, but it still must be sent to the server after reconnection.
      if athena_now_connected and not last_athena_connection:
        last_state = None
        cloudlog.info("athena.vehicle_state_reporter.connection_restored")
      last_athena_connection = athena_now_connected
      if (athena_now_connected and
          monotonic_now - last_athena_heartbeat_at >= ATHENA_HEARTBEAT_INTERVAL_S):
        send_queue.put_nowait(json.dumps({"jsonrpc": "2.0", "method": "vehicleStateHeartbeat", "params": {}}))
        last_athena_heartbeat_at = monotonic_now
      panda_states = sm['pandaStates']
      panda_details = []
      panda_connected = False
      if sm.valid.get('pandaStates', False) and len(panda_states):
        for panda in panda_states:
          panda_type = str(getattr(panda, "pandaType", "unknown")).lower()
          harness_status = str(getattr(panda, "harnessStatus", "notConnected")).lower()
          accepted = panda_type != "unknown" and harness_status not in ("notconnected", "not_connected")
          panda_details.append({"panda_type": panda_type, "harness_status": harness_status, "accepted": accepted})
          if accepted:
            panda_connected = True

      car_for_diagnostics = sm['carState']
      diagnostics['carState']['can_valid'] = (bool(getattr(car_for_diagnostics, 'canValid', False))
                                               if sm.rcv_frame.get('carState', -1) >= 0 else None)
      if panda_connected:
        panda_disconnected_since = None
        diagnostics['panda_connection'] = {"accepted": True, "reason": None, "states": panda_details}
      else:
        if panda_disconnected_since is None:
          panda_disconnected_since = monotonic_now
        disconnected_for = round(monotonic_now - panda_disconnected_since, 1)
        if not sm.valid.get('pandaStates', False):
          reason = 'pandaStates topic invalid'
        elif not panda_details:
          reason = 'no Panda state received'
        else:
          reason = 'no Panda with connected harness'
        diagnostics['panda_connection'] = {
          "accepted": False, "reason": reason, "disconnected_for_s": disconnected_for, "states": panda_details,
        }
      with vehicle_state_lock:
        latest_vehicle_diagnostics = diagnostics
      # Send a small periodic diagnostic even when state collection is blocked.
      # This exposes Panda/CAN gating failures on the local server.
      if (athena_now_connected and
          monotonic_now - last_vehicle_state_diagnostics_at >= VEHICLE_STATE_DIAGNOSTICS_INTERVAL_S):
        send_queue.put_nowait(json.dumps({
          "jsonrpc": "2.0", "method": "vehicleStateDiagnostics",
          "params": {"diagnostics": diagnostics},
        }))
        last_vehicle_state_diagnostics_at = monotonic_now


      if not panda_connected:
        # One missed Panda update must not erase a useful last state. After the
        # grace period, clear it so a detached device cannot report stale data.
        if panda_disconnected_since is not None and monotonic_now - panda_disconnected_since < PANDA_DISCONNECT_GRACE_S:
          with vehicle_state_lock:
            latest_vehicle_state_error = 'Panda connection temporarily unavailable: ' + diagnostics['panda_connection']['reason']
          continue
        panda_connected_event.clear()
        last_state = None
        with vehicle_state_lock:
          latest_vehicle_state = None
          latest_vehicle_state_at = None
          latest_vehicle_state_error = 'Panda connection not confirmed: ' + diagnostics['panda_connection']['reason']
        continue
      if not panda_connected_event.is_set():
        # Force one fresh report after the Panda/car connection is restored.
        last_state = None
      panda_connected_event.set()
      # A carState message can be marked invalid while the engine is off
      # because one powertrain signal stops. Its body CAN fields may still be
      # valid, so carState.valid must not block body-state reporting.
      # Only require that at least one carState message has been received.
      if sm.rcv_frame.get('carState', -1) < 0:
        continue
      car_valid = bool(sm.valid.get('carState', False))
      stage = "reading vehicle messages"
      car = sm['carState']
      device = sm['deviceState']
      cpu_temps = list(getattr(device, "cpuTempC", []))
      fuel_value = (_finite_number(getattr(car, "fuelPercent", None))
                    if bool(getattr(car, "fuelValid", False)) else None)
      stage = "building vehicle state"
      current_cpu_temperature = round(max(cpu_temps), 1) if cpu_temps else None
      if (last_reported_cpu_temperature is None or
          monotonic_now - last_cpu_temperature_report_at >= OP_TEMP_REPORT_INTERVAL_S):
        last_reported_cpu_temperature = current_cpu_temperature
        last_cpu_temperature_report_at = monotonic_now
      current_ambient_temperature = _finite_number(getattr(device, "ambientTempC", None))
      if (last_reported_ambient_temperature is None or
          monotonic_now - last_ambient_temperature_report_at >= AMBIENT_TEMP_REPORT_INTERVAL_S):
        last_reported_ambient_temperature = current_ambient_temperature
        last_ambient_temperature_report_at = monotonic_now
      reported_engine_rpm = (_finite_number(getattr(car, "engineRpm", None))
                             if bool(getattr(car, "engineRpmValid", False)) else None)
      if reported_engine_rpm is not None:
        last_fresh_engine_rpm = reported_engine_rpm
        last_fresh_engine_rpm_at = monotonic_now
      # ENGINE_DATA is a 100 Hz Mazda message. Keep its last sample only for
      # a short grace interval to avoid a one-frame drop flickering the UI;
      # after that it is stale and must never keep the engine marked running.
      engine_rpm_fresh = monotonic_now - last_fresh_engine_rpm_at <= 1.5
      engine_rpm = last_fresh_engine_rpm if engine_rpm_fresh else None
      gear = _gear_label(car.gearShifter)
      car_state_fresh = monotonic_now - last_car_state_update_at <= CAR_STATE_FRESHNESS_S
      panda_ignition_can = any(bool(getattr(panda, "ignitionCan", False)) for panda in panda_states)
      panda_ignition_line = any(bool(getattr(panda, "ignitionLine", False)) for panda in panda_states)
      if car_state_fresh and engine_rpm is not None:
        # RPM is the only signal that distinguishes a running engine from an
        # ACC-on vehicle. Never infer engine-running from ignition alone.
        engine_running = engine_rpm >= 300.0
        engine_state_source = "engine_rpm"
      elif car_state_fresh:
        # This Mazda publishes ENGINE_DATA continuously while the engine is
        # running. A fresh carState without a recent RPM frame therefore
        # means ACC/accessory power or an engine that has been shut down.
        engine_running = False
        engine_state_source = "engine_rpm_not_fresh"
      elif not panda_ignition_can and not panda_ignition_line:
        engine_running = False
        engine_state_source = "panda_ignition_off"
      else:
        # Keep the value honest if the vehicle is still electrically awake but
        # a fresh RPM message is unavailable.
        engine_running = None
        engine_state_source = "car_state_stale"
      drive_active = gear in ("D", "R") and engine_running is True
      left_blinker = bool(car.leftBlinker)
      right_blinker = bool(car.rightBlinker)
      # A cold-started reporter can restore an old persisted state before its
      # first live body-CAN snapshot. Force that first current snapshot to
      # replace the cache and be sent after connection is available.
      if car_state_fresh and not lights_snapshot_received:
        lights_snapshot_received = True
        last_state = None
        cloudlog.info("athena.vehicle_state_reporter.lights_snapshot_received")
      if left_blinker and right_blinker:
        hazard_lights_active = True
        last_hazard_lights_seen_at = monotonic_now
      elif hazard_lights_active and monotonic_now - last_hazard_lights_seen_at >= HAZARD_LIGHTS_HOLD_S:
        # Both lamps are periodically off between flashes. Hold the detected
        # state briefly so the report stays stable and does not upload on each
        # flash cycle.
        hazard_lights_active = False
      state = {
        "gear": gear,
        "door_open": bool(car.doorOpen),
        "seatbelt_unlatched": bool(car.seatbeltUnlatched),
        "hazard_lights": hazard_lights_active,
        "headlights_on": bool(getattr(car, "headlightsOn", False)),
        "low_beams_on": bool(getattr(car, "lowBeamsOn", False)),
        "high_beams_on": bool(getattr(car, "highBeamsOn", False)),
        "brake_pressed": bool(car.brakePressed),
        "gas_pressed": bool(car.gasPressed),
        "engine_running": engine_running,
        "engine_state_source": engine_state_source,
        "car_state_fresh": car_state_fresh,
        "engine_rpm": round(engine_rpm, 0) if engine_rpm is not None else None,
        "drive_active": drive_active,
        "fuel_percent": fuel_value,
        "vehicle_voltage": None,
        "ambient_temperature_c": last_reported_ambient_temperature,
        "cpu_temperature_c": last_reported_cpu_temperature,
        "location": last_reported_location,
        "location_captured_at": last_location_captured_at,
      }
      if bool(getattr(car, "tirePressureValid", False)):
        state["tire_pressure_bar"] = {
          "front_left": _finite_number(getattr(car, "tirePressureFlBar", None)),
          "front_right": _finite_number(getattr(car, "tirePressureFrBar", None)),
          "rear_left": _finite_number(getattr(car, "tirePressureRlBar", None)),
          "rear_right": _finite_number(getattr(car, "tirePressureRrBar", None)),
        }
      else:
        state["tire_pressure_bar"] = None
      if bool(getattr(car, "tireTemperatureValid", False)):
        state["tire_temperature_c"] = {
          "front_left": _finite_number(getattr(car, "tireTemperatureFlC", None)),
          "front_right": _finite_number(getattr(car, "tireTemperatureFrC", None)),
          "rear_left": _finite_number(getattr(car, "tireTemperatureRlC", None)),
          "rear_right": _finite_number(getattr(car, "tireTemperatureRrC", None)),
        }
      else:
        state["tire_temperature_c"] = None
      stage = "reading panda state"
      peripheral = sm['peripheralState']
      voltage = None
      voltage_source = None
      if sm.valid.get('pandaStates', False) and len(panda_states):
        panda = panda_states[0]
        # Engine state is based on Mazda engine RPM, not device.started or
        # Panda ignitionLine. Those only describe software/electrical state.
        # The value above already applies the RPM-first / ignition-off
        # fallback. Do not overwrite it with a potentially stale RPM here.
        # Prefer the Panda vehicle-side measurement; use PeripheralState only
        # as a compatibility fallback for older runtime variants.
        voltage = _finite_number(getattr(panda, "voltageDEPRECATED", 0))
        if voltage is not None and voltage > 0:
          voltage_source = "pandaStates.voltageDEPRECATED"
      if (voltage is None or voltage <= 0) and sm.valid.get('peripheralState', False):
        voltage = _finite_number(getattr(peripheral, "voltage", 0))
        if voltage is not None and voltage > 0:
          voltage_source = "peripheralState.voltage"
      if monotonic_now - last_voltage_report_at >= VOLTAGE_REPORT_INTERVAL_S or last_reported_voltage is None:
        if voltage is not None and voltage > 0:
          last_reported_voltage = round(voltage / 1000.0, 3)
          last_reported_voltage_raw_mv = round(voltage, 1)
          last_reported_voltage_source = voltage_source
        else:
          last_reported_voltage = None
          last_reported_voltage_raw_mv = None
          last_reported_voltage_source = None
        last_voltage_report_at = monotonic_now
      state["vehicle_voltage"] = last_reported_voltage
      if last_reported_voltage_raw_mv is not None:
        state["vehicle_voltage_raw_mv"] = last_reported_voltage_raw_mv
        state["vehicle_voltage_source"] = last_reported_voltage_source
      stage = "reading location"
      location = sm['liveLocationKalman'].positionGeodetic if sm.valid.get('liveLocationKalman', False) else None
      vehicle_speed = _finite_number(getattr(car, "vEgo", None))
      vehicle_is_stationary = (car_state_fresh and vehicle_speed is not None and
                               vehicle_speed <= LOCATION_STATIONARY_SPEED_MPS)
      if vehicle_is_stationary:
        if stationary_since is None:
          stationary_since = monotonic_now
      else:
        stationary_since = None
      if location is not None and location.valid and len(location.value) >= 2:
        lat, lon = _finite_number(location.value[0]), _finite_number(location.value[1])
        if lat is not None and lon is not None:
          candidate_location = {"latitude": round(lat, 7), "longitude": round(lon, 7)}
          distance = _distance_meters(last_reported_location, candidate_location)
          moved_enough = (distance is not None and distance >= LOCATION_MOVE_THRESHOLD_M and
                           monotonic_now - last_location_report_at >= LOCATION_REPORT_MIN_INTERVAL_S)
          # When the car has stopped, the last pre-parking point can be 10-20 m
          # away from the final GPS fix. Permit a single small correction only
          # after the car has stayed still long enough, then rate-limit it so
          # normal GPS drift cannot repeatedly trigger state uploads.
          settled_correction = (
            stationary_since is not None and
            monotonic_now - stationary_since >= LOCATION_STATIONARY_SETTLE_S and
            distance is not None and distance >= LOCATION_STATIONARY_CORRECTION_M and
            monotonic_now - last_stationary_location_refresh_at >= LOCATION_STATIONARY_REFRESH_INTERVAL_S
          )
          if last_reported_location is None or moved_enough or settled_correction:
            last_reported_location = candidate_location
            last_location_report_at = monotonic_now
            last_location_captured_at = int(time.time())
            if settled_correction:
              last_stationary_location_refresh_at = monotonic_now
          state["location"] = last_reported_location
          state["location_captured_at"] = last_location_captured_at
      stage = "sending vehicle state"
      if state != last_state:
        stage = "caching vehicle state"
        captured_at = int(time.time())
        with vehicle_state_lock:
          latest_vehicle_state = dict(state)
          latest_vehicle_state_at = captured_at
        Params().put(VEHICLE_STATE_CACHE_PARAM, json.dumps({
          "state": state,
          "captured_at": captured_at,
        }, ensure_ascii=False, separators=(",", ":")))
        if athena_connected_event.is_set():
          send_queue.put_nowait(json.dumps({"jsonrpc": "2.0", "method": "vehicleState", "params": {"state": state, "captured_at": captured_at}}))
        if last_state is None:
          cloudlog.info("athena.vehicle_state_reporter.first_report valid=%s", {name: sm.valid.get(name, False) for name in ('carState', 'deviceState', 'pandaStates', 'peripheralState', 'liveLocationKalman')})
        last_state = state
    except Exception:
      with vehicle_state_lock:
        latest_vehicle_state_error = "%s: %s: %s" % (stage, type(sys.exc_info()[1]).__name__, str(sys.exc_info()[1]))
      cloudlog.exception("athena.vehicle_state_reporter.exception")
      time.sleep(1)


@dispatcher.add_method
def getVehicleState():
  """Return the latest locally collected state for an on-demand refresh."""
  with vehicle_state_lock:
    if latest_vehicle_state is None:
      return {"available": False, "state": None, "captured_at": None, "diagnostics": dict(latest_vehicle_diagnostics), "error": latest_vehicle_state_error}
    return {"available": True, "state": dict(latest_vehicle_state), "captured_at": latest_vehicle_state_at, "diagnostics": dict(latest_vehicle_diagnostics), "error": latest_vehicle_state_error}


def jsonrpc_handler(end_event):
  dispatcher["startLocalProxy"] = partial(startLocalProxy, end_event)
  while not end_event.is_set():
    try:
      data = recv_queue.get(timeout=1)
      if "method" in data:
        cloudlog.debug(f"athena.jsonrpc_handler.call_method {data}")
        response = JSONRPCResponseManager.handle(data, dispatcher)
        send_queue.put_nowait(response.json)
      elif "id" in data and ("result" in data or "error" in data):
        log_recv_queue.put_nowait(data)
      else:
        raise Exception("not a valid request or response")
    except queue.Empty:
      pass
    except Exception as e:
      cloudlog.exception("athena jsonrpc handler failed")
      send_queue.put_nowait(json.dumps({"error": str(e)}))


def retry_upload(tid: int, end_event: threading.Event, increase_count: bool = True) -> None:
  if cur_upload_items[tid].retry_count < MAX_RETRY_COUNT:
    item = cur_upload_items[tid]
    new_retry_count = item.retry_count + 1 if increase_count else item.retry_count

    item = item._replace(
      retry_count=new_retry_count,
      progress=0,
      current=False
    )
    upload_queue.put_nowait(item)
    UploadQueueCache.cache(upload_queue)

    cur_upload_items[tid] = None

    for _ in range(RETRY_DELAY):
      time.sleep(1)
      if end_event.is_set():
        break


def upload_handler(end_event: threading.Event) -> None:
  sm = messaging.SubMaster(['deviceState'])
  tid = threading.get_ident()

  while not end_event.is_set():
    cur_upload_items[tid] = None

    try:
      cur_upload_items[tid] = upload_queue.get(timeout=1)._replace(current=True)

      if cur_upload_items[tid].id in cancelled_uploads:
        cancelled_uploads.remove(cur_upload_items[tid].id)
        continue

      # Remove item if too old
      age = datetime.now() - datetime.fromtimestamp(cur_upload_items[tid].created_at / 1000)
      if age.total_seconds() > MAX_AGE:
        cloudlog.event("athena.upload_handler.expired", item=cur_upload_items[tid], error=True)
        continue

      # Check if uploading over cell is allowed
      sm.update(0)
      cell = sm['deviceState'].networkType not in [NetworkType.wifi, NetworkType.ethernet]
      if cell and (not cur_upload_items[tid].allow_cellular):
        retry_upload(tid, end_event, False)
        continue

      try:
        def cb(sz, cur):
          # Abort transfer if connection changed to cell after starting upload
          sm.update(0)
          cell = sm['deviceState'].networkType not in [NetworkType.wifi, NetworkType.ethernet]
          if cell and (not cur_upload_items[tid].allow_cellular):
            raise AbortTransferException

          cur_upload_items[tid] = cur_upload_items[tid]._replace(progress=cur / sz if sz else 1)


        network_type = sm['deviceState'].networkType.raw
        fn = cur_upload_items[tid].path
        try:
          sz = os.path.getsize(fn)
        except OSError:
          sz = -1

        cloudlog.event("athena.upload_handler.upload_start", fn=fn, sz=sz, network_type=network_type)
        response = _do_upload(cur_upload_items[tid], cb)

        if response.status_code not in (200, 201, 403, 412):
          cloudlog.event("athena.upload_handler.retry", status_code=response.status_code, fn=fn, sz=sz, network_type=network_type)
          retry_upload(tid, end_event)
        else:
          cloudlog.event("athena.upload_handler.success", fn=fn, sz=sz, network_type=network_type)

        UploadQueueCache.cache(upload_queue)
      except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.SSLError):
        cloudlog.event("athena.upload_handler.timeout", fn=fn, sz=sz, network_type=network_type)
        retry_upload(tid, end_event)
      except AbortTransferException:
        cloudlog.event("athena.upload_handler.abort", fn=fn, sz=sz, network_type=network_type)
        retry_upload(tid, end_event, False)

    except queue.Empty:
      pass
    except Exception:
      cloudlog.exception("athena.upload_handler.exception")


def _do_upload(upload_item, callback=None):
  with open(upload_item.path, "rb") as f:
    size = os.fstat(f.fileno()).st_size

    if callback:
      f = CallbackReader(f, callback, size)

    return requests.put(upload_item.url,
                        data=f,
                        headers={**upload_item.headers, 'Content-Length': str(size)},
                        timeout=30)


# security: user should be able to request any message from their car
@dispatcher.add_method
def getMessage(service=None, timeout=1000):
  if service is None or service not in service_list:
    raise Exception("invalid service")

  socket = messaging.sub_sock(service, timeout=timeout)
  ret = messaging.recv_one(socket)

  if ret is None:
    raise TimeoutError

  return ret.to_dict()


@dispatcher.add_method
def getVersion():
  return {
    "version": get_version(),
    "remote": get_origin(),
    "branch": get_short_branch(),
    "commit": get_commit(),
  }


@dispatcher.add_method
def setNavDestination(latitude=0, longitude=0, place_name=None, place_details=None):
  destination = {
    "latitude": latitude,
    "longitude": longitude,
    "place_name": place_name,
    "place_details": place_details,
  }
  Params().put("NavDestination", json.dumps(destination))

  return {"success": 1}


def scan_dir(path, prefix):
  files = list()
  # only walk directories that match the prefix
  # (glob and friends traverse entire dir tree)
  with os.scandir(path) as i:
    for e in i:
      rel_path = os.path.relpath(e.path, ROOT)
      if e.is_dir(follow_symlinks=False):
        # add trailing slash
        rel_path = os.path.join(rel_path, '')
        # if prefix is a partial dir name, current dir will start with prefix
        # if prefix is a partial file name, prefix with start with dir name
        if rel_path.startswith(prefix) or prefix.startswith(rel_path):
          files.extend(scan_dir(e.path, prefix))
      else:
        if rel_path.startswith(prefix):
          files.append(rel_path)
  return files

@dispatcher.add_method
def listDataDirectory(prefix=''):
  return scan_dir(ROOT, prefix)


@dispatcher.add_method
def reboot():
  sock = messaging.sub_sock("deviceState", timeout=1000)
  ret = messaging.recv_one(sock)
  if ret is None or ret.deviceState.started:
    raise Exception("Reboot unavailable")

  def do_reboot():
    time.sleep(2)
    HARDWARE.reboot()

  threading.Thread(target=do_reboot).start()

  return {"success": 1}


@dispatcher.add_method
def uploadFileToUrl(fn, url, headers):
  return uploadFilesToUrls([{
    "fn": fn,
    "url": url,
    "headers": headers,
  }])


@dispatcher.add_method
def uploadFilesToUrls(files_data):
  items = []
  failed = []
  for file in files_data:
    fn = file.get('fn', '')
    if len(fn) == 0 or fn[0] == '/' or '..' in fn or 'url' not in file:
      failed.append(fn)
      continue
    path = os.path.join(ROOT, fn)
    if not os.path.exists(path):
      failed.append(fn)
      continue

    item = UploadItem(
      path=path,
      url=file['url'],
      headers=file.get('headers', {}),
      created_at=int(time.time() * 1000),
      id=None,
      allow_cellular=file.get('allow_cellular', False),
    )
    upload_id = hashlib.sha1(str(item).encode()).hexdigest()
    item = item._replace(id=upload_id)
    upload_queue.put_nowait(item)
    items.append(item._asdict())

  UploadQueueCache.cache(upload_queue)

  resp = {"enqueued": len(items), "items": items}
  if failed:
    resp["failed"] = failed

  return resp


@dispatcher.add_method
def listUploadQueue():
  items = list(upload_queue.queue) + list(cur_upload_items.values())
  return [i._asdict() for i in items if (i is not None) and (i.id not in cancelled_uploads)]


@dispatcher.add_method
def cancelUpload(upload_id):
  if not isinstance(upload_id, list):
    upload_id = [upload_id]

  uploading_ids = {item.id for item in list(upload_queue.queue)}
  cancelled_ids = uploading_ids.intersection(upload_id)
  if len(cancelled_ids) == 0:
    return 404

  cancelled_uploads.update(cancelled_ids)
  return {"success": 1}


@dispatcher.add_method
def primeActivated(activated):
  return {"success": 1}


@dispatcher.add_method
def setBandwithLimit(upload_speed_kbps, download_speed_kbps):
  if not TICI:
    return {"success": 0, "error": "only supported on comma three"}

  try:
    HARDWARE.set_bandwidth_limit(upload_speed_kbps, download_speed_kbps)
    return {"success": 1}
  except subprocess.CalledProcessError as e:
    return {"success": 0, "error": "failed to set limit", "stdout": e.stdout, "stderr": e.stderr}


def startLocalProxy(global_end_event, remote_ws_uri, local_port):
  try:
    if local_port not in LOCAL_PORT_WHITELIST:
      raise Exception("Requested local port not whitelisted")

    cloudlog.debug("athena.startLocalProxy.starting")

    dongle_id = Params().get("DongleId").decode('utf8')
    identity_token = Api(dongle_id).get_token()
    ws = create_connection(remote_ws_uri,
                           cookie="jwt=" + identity_token,
                           enable_multithread=True)

    ssock, csock = socket.socketpair()
    local_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    local_sock.connect(('127.0.0.1', local_port))
    local_sock.setblocking(0)

    proxy_end_event = threading.Event()
    threads = [
      threading.Thread(target=ws_proxy_recv, args=(ws, local_sock, ssock, proxy_end_event, global_end_event)),
      threading.Thread(target=ws_proxy_send, args=(ws, local_sock, csock, proxy_end_event))
    ]
    for thread in threads:
      thread.start()

    cloudlog.debug("athena.startLocalProxy.started")
    return {"success": 1}
  except Exception as e:
    cloudlog.exception("athenad.startLocalProxy.exception")
    raise e


@dispatcher.add_method
def getPublicKey():
  if not os.path.isfile(PERSIST + '/comma/id_rsa.pub'):
    return None

  with open(PERSIST + '/comma/id_rsa.pub') as f:
    return f.read()


@dispatcher.add_method
def getSshAuthorizedKeys():
  return Params().get("GithubSshKeys", encoding='utf8') or ''


@dispatcher.add_method
def getSimInfo():
  return HARDWARE.get_sim_info()


@dispatcher.add_method
def getNetworkType():
  return HARDWARE.get_network_type()


@dispatcher.add_method
def getNetworks():
  return HARDWARE.get_networks()


@dispatcher.add_method
def takeSnapshot():
  from selfdrive.camerad.snapshot.snapshot import jpeg_write, snapshot
  ret = snapshot()
  if ret is not None:
    def b64jpeg(x):
      if x is not None:
        f = io.BytesIO()
        jpeg_write(f, x)
        return base64.b64encode(f.getvalue()).decode("utf-8")
      else:
        return None
    return {'jpegBack': b64jpeg(ret[0]),
            'jpegFront': b64jpeg(ret[1])}
  else:
    raise Exception("not available while camerad is started")


def get_logs_to_send_sorted():
  # TODO: scan once then use inotify to detect file creation/deletion
  curr_time = int(time.time())
  logs = []
  for log_entry in os.listdir(SWAGLOG_DIR):
    log_path = os.path.join(SWAGLOG_DIR, log_entry)
    try:
      time_sent = int.from_bytes(getxattr(log_path, LOG_ATTR_NAME), sys.byteorder)
    except (ValueError, TypeError):
      time_sent = 0
    # assume send failed and we lost the response if sent more than one hour ago
    if not time_sent or curr_time - time_sent > 3600:
      logs.append(log_entry)
  # excluding most recent (active) log file
  return sorted(logs)[:-1]


def log_handler(end_event):
  if PC:
    return

  log_files = []
  last_scan = 0
  while not end_event.is_set():
    try:
      curr_scan = sec_since_boot()
      if curr_scan - last_scan > 10:
        log_files = get_logs_to_send_sorted()
        last_scan = curr_scan

      # send one log
      curr_log = None
      if len(log_files) > 0:
        log_entry = log_files.pop() # newest log file
        cloudlog.debug(f"athena.log_handler.forward_request {log_entry}")
        try:
          curr_time = int(time.time())
          log_path = os.path.join(SWAGLOG_DIR, log_entry)
          setxattr(log_path, LOG_ATTR_NAME, int.to_bytes(curr_time, 4, sys.byteorder))
          with open(log_path) as f:
            jsonrpc = {
              "method": "forwardLogs",
              "params": {
                "logs": f.read(),
                # Archive names are generated by the server; retain the device
                # file modification time so the UI can show its original time.
                "source_mtime_ms": int(os.path.getmtime(log_path) * 1000),
              },
              "jsonrpc": "2.0",
              "id": log_entry
            }
            low_priority_send_queue.put_nowait(json.dumps(jsonrpc))
            curr_log = log_entry
        except OSError:
          pass  # file could be deleted by log rotation

      # wait for response up to ~100 seconds
      # always read queue at least once to process any old responses that arrive
      for _ in range(100):
        if end_event.is_set():
          break
        try:
          log_resp = json.loads(log_recv_queue.get(timeout=1))
          log_entry = log_resp.get("id")
          log_success = "result" in log_resp and log_resp["result"].get("success")
          cloudlog.debug(f"athena.log_handler.forward_response {log_entry} {log_success}")
          if log_entry and log_success:
            log_path = os.path.join(SWAGLOG_DIR, log_entry)
            try:
              setxattr(log_path, LOG_ATTR_NAME, LOG_ATTR_VALUE_MAX_UNIX_TIME)
            except OSError:
              pass  # file could be deleted by log rotation
          if curr_log == log_entry:
            break
        except queue.Empty:
          if curr_log is None:
            break

    except Exception:
      cloudlog.exception("athena.log_handler.exception")


def stat_handler(end_event):
  while not end_event.is_set():
    last_scan = 0
    curr_scan = sec_since_boot()
    try:
      if curr_scan - last_scan > 10:
        stat_filenames = list(filter(lambda name: not name.startswith(tempfile.gettempprefix()), os.listdir(STATS_DIR)))
        if len(stat_filenames) > 0:
          stat_path = os.path.join(STATS_DIR, stat_filenames[0])
          with open(stat_path) as f:
            jsonrpc = {
              "method": "storeStats",
              "params": {
                "stats": f.read()
              },
              "jsonrpc": "2.0",
              "id": stat_filenames[0]
            }
            low_priority_send_queue.put_nowait(json.dumps(jsonrpc))
          os.remove(stat_path)
        last_scan = curr_scan
    except Exception:
      cloudlog.exception("athena.stat_handler.exception")
    time.sleep(0.1)


def ws_proxy_recv(ws, local_sock, ssock, end_event, global_end_event):
  while not (end_event.is_set() or global_end_event.is_set()):
    try:
      data = ws.recv()
      local_sock.sendall(data)
    except WebSocketTimeoutException:
      pass
    except Exception:
      cloudlog.exception("athenad.ws_proxy_recv.exception")
      break

  cloudlog.debug("athena.ws_proxy_recv closing sockets")
  ssock.close()
  local_sock.close()
  cloudlog.debug("athena.ws_proxy_recv done closing sockets")

  end_event.set()


def ws_proxy_send(ws, local_sock, signal_sock, end_event):
  while not end_event.is_set():
    try:
      r, _, _ = select.select((local_sock, signal_sock), (), ())
      if r:
        if r[0].fileno() == signal_sock.fileno():
          # got end signal from ws_proxy_recv
          end_event.set()
          break
        data = local_sock.recv(4096)
        if not data:
          # local_sock is dead
          end_event.set()
          break

        ws.send(data, ABNF.OPCODE_BINARY)
    except Exception:
      cloudlog.exception("athenad.ws_proxy_send.exception")
      end_event.set()

  cloudlog.debug("athena.ws_proxy_send closing sockets")
  signal_sock.close()
  cloudlog.debug("athena.ws_proxy_send done closing sockets")


def ws_recv(ws, end_event):
  last_ping = int(sec_since_boot() * 1e9)
  while not end_event.is_set():
    try:
      opcode, data = ws.recv_data(control_frame=True)
      if opcode in (ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY):
        if opcode == ABNF.OPCODE_TEXT:
          data = data.decode("utf-8")
        recv_queue.put_nowait(data)
      elif opcode == ABNF.OPCODE_PING:
        last_ping = int(sec_since_boot() * 1e9)
        Params().put("LastAthenaPingTime", str(last_ping))
    except WebSocketTimeoutException:
      ns_since_last_ping = int(sec_since_boot() * 1e9) - last_ping
      if ns_since_last_ping > RECONNECT_TIMEOUT_S * 1e9:
        cloudlog.exception("athenad.ws_recv.timeout")
        end_event.set()
    except Exception:
      cloudlog.exception("athenad.ws_recv.exception")
      end_event.set()


def ws_send(ws, end_event):
  while not end_event.is_set():
    try:
      try:
        data = send_queue.get_nowait()
      except queue.Empty:
        data = low_priority_send_queue.get(timeout=1)
      for i in range(0, len(data), WS_FRAME_SIZE):
        frame = data[i:i+WS_FRAME_SIZE]
        last = i + WS_FRAME_SIZE >= len(data)
        opcode = ABNF.OPCODE_TEXT if i == 0 else ABNF.OPCODE_CONT
        ws.send_frame(ABNF.create_frame(frame, opcode, last))
    except queue.Empty:
      pass
    except Exception:
      cloudlog.exception("athenad.ws_send.exception")
      end_event.set()


def backoff(retries):
  return random.randrange(0, min(128, int(2 ** retries)))


def main():
  params = Params()
  dongle_id = params.get("DongleId", encoding='utf-8')
  UploadQueueCache.initialize(upload_queue)
  state_end_event = threading.Event()
  state_thread = threading.Thread(target=vehicle_state_reporter, args=(state_end_event,), name='vehicle_state_reporter', daemon=True)
  state_thread.start()

  ws_uri = ATHENA_HOST + "/ws/v2/" + dongle_id
  api = Api(dongle_id)

  conn_retries = 0
  while 1:
    try:
      cloudlog.event("athenad.main.connecting_ws", ws_uri=ws_uri)
      ws = create_connection(ws_uri,
                             cookie="jwt=" + api.get_token(),
                             enable_multithread=True,
                             timeout=30.0)
      cloudlog.event("athenad.main.connected_ws", ws_uri=ws_uri)
      params.delete("PrimeRedirected")

      conn_retries = 0
      cur_upload_items.clear()

      handle_long_poll(ws)
    except (KeyboardInterrupt, SystemExit):
      break
    except (ConnectionError, TimeoutError, WebSocketException):
      conn_retries += 1
      params.delete("PrimeRedirected")
      params.delete("LastAthenaPingTime")
    except socket.timeout:
      try:
        r = requests.get(API_HOST + "/v1/me", allow_redirects=False,
                         headers={"User-Agent": f"openpilot-{get_version()}"}, timeout=15.0)
        if r.status_code == 302 and r.headers['Location'].startswith("http://u.web2go.com"):
          params.put_bool("PrimeRedirected", True)
      except Exception:
        cloudlog.exception("athenad.socket_timeout.exception")
      params.delete("LastAthenaPingTime")
    except Exception:
      cloudlog.exception("athenad.main.exception")

      conn_retries += 1
      params.delete("PrimeRedirected")
      params.delete("LastAthenaPingTime")

    time.sleep(backoff(conn_retries))


if __name__ == "__main__":
  main()



