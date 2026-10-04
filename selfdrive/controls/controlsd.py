#!/usr/bin/env python3
import os
import math
from numbers import Number

from cereal import car, log
from common.numpy_fast import clip
from common.realtime import sec_since_boot, config_realtime_process, Priority, Ratekeeper, DT_CTRL
from common.profiler import Profiler
from common.params import Params, put_nonblocking
import cereal.messaging as messaging
from selfdrive.config import Conversions as CV
from selfdrive.swaglog import cloudlog
from selfdrive.boardd.boardd import can_list_to_can_capnp
from selfdrive.car.car_helpers import get_car, get_startup_event, get_one_can, get_ti
from selfdrive.controls.lib.lane_planner import CAMERA_OFFSET
from selfdrive.controls.lib.drive_helpers import update_v_cruise, initialize_v_cruise
from selfdrive.controls.lib.drive_helpers import get_lag_adjusted_curvature
from selfdrive.controls.lib.longcontrol import LongControl
from selfdrive.controls.lib.latcontrol_pid import LatControlPID
from selfdrive.controls.lib.latcontrol_indi import LatControlINDI
from selfdrive.controls.lib.latcontrol_lqr import LatControlLQR
from selfdrive.controls.lib.latcontrol_angle import LatControlAngle
from selfdrive.controls.lib.events import Events, ET, EVENT_NAME, EVENTS
from selfdrive.controls.lib.alertmanager import AlertManager, set_offroad_alert
from selfdrive.controls.lib.vehicle_model import VehicleModel
from selfdrive.locationd.calibrationd import Calibration
from selfdrive.hardware import HARDWARE, TICI, EON
from selfdrive.manager.process_config import managed_processes

SOFT_DISABLE_TIME = 3  # seconds
LDW_MIN_SPEED = 31 * CV.MPH_TO_MS
LANE_DEPARTURE_THRESHOLD = 0.1

REPLAY = "REPLAY" in os.environ
SIMULATION = "SIMULATION" in os.environ
NOSENSOR = "NOSENSOR" in os.environ
IGNORE_PROCESSES = {"rtshield", "uploader", "deleter", "loggerd", "logmessaged", "tombstoned",
                    "logcatd", "proclogd", "clocksd", "updated", "timezoned", "manage_athenad",
                    "statsd", "shutdownd", "gps_time_sync"} | \
                    {k for k, v in managed_processes.items() if not v.enabled}

ACTUATOR_FIELDS = set(car.CarControl.Actuators.schema.fields.keys())

ThermalStatus = log.DeviceState.ThermalStatus
State = log.ControlsState.OpenpilotState
PandaType = log.PandaState.PandaType
Desire = log.LateralPlan.Desire
LaneChangeState = log.LateralPlan.LaneChangeState
LaneChangeDirection = log.LateralPlan.LaneChangeDirection
EventName = car.CarEvent.EventName
ButtonEvent = car.CarState.ButtonEvent
SafetyModel = car.CarParams.SafetyModel

IGNORED_SAFETY_MODES = [SafetyModel.silent, SafetyModel.noOutput]
CSID_MAP = {"0": EventName.roadCameraError, "1": EventName.wideRoadCameraError, "2": EventName.driverCameraError}

class Controls:
  def __init__(self, sm=None, pm=None, can_sock=None):
    cloudlog.info("=" * 80)
    cloudlog.info("[INIT][controlsd] *** Controls.__init__() started ***")
    cloudlog.info("=" * 80)
    config_realtime_process(4 if TICI else 3, Priority.CTRL_HIGH)
    cloudlog.info("[INIT][controlsd] Realtime process configured")

    # Setup sockets
    self.pm = pm
    if self.pm is None:
      self.pm = messaging.PubMaster(['sendcan', 'controlsState', 'carState',
                                     'carControl', 'carEvents', 'carParams'])
      cloudlog.info("[INIT][controlsd] PubMaster created")

    self.camera_packets = ["roadCameraState", "driverCameraState"]
    if TICI:
      self.camera_packets.append("wideRoadCameraState")

    self.params = Params()
    self.joystick_mode = self.params.get_bool("JoystickDebugMode")
    joystick_packet = ['testJoystick'] if self.joystick_mode else []

    # 保存驾驶员监控状态，支持运行时动态切换
    self.driver_monitoring_enabled = self.params.get_bool("DriverMonitoringEnabled")
    cloudlog.info(f"[DM][controlsd][INIT] Driver monitoring enabled: {self.driver_monitoring_enabled}")

    self.sm = sm
    if self.sm is None:
      ignore = ['driverCameraState', 'managerState'] if SIMULATION else []
      # 如果驾驶员监控被禁用，忽略 driverMonitoringState 消息的 alive 和 valid 检查
      if not self.driver_monitoring_enabled:
        ignore.append('driverMonitoringState')
        cloudlog.info("[DM][controlsd][INIT] Adding driverMonitoringState to ignore_alive list")
      cloudlog.info(f"[DM][controlsd][INIT] SubMaster ignore_alive list: {ignore}")
      self.sm = messaging.SubMaster(['deviceState', 'pandaStates', 'peripheralState', 'modelV2', 'liveCalibration',
                                     'driverMonitoringState', 'longitudinalPlan', 'lateralPlan', 'liveLocationKalman',
                                     'managerState', 'liveParameters', 'radarState'] + self.camera_packets + joystick_packet,
                                     ignore_alive=ignore, ignore_avg_freq=['radarState', 'longitudinalPlan', 'lateralPlan'])
      cloudlog.info("[DM][controlsd][INIT] SubMaster created successfully")
      cloudlog.info("[DM][controlsd][INIT] ignore_avg_freq list: ['radarState', 'longitudinalPlan', 'lateralPlan']")

    self.can_sock = can_sock
    if can_sock is None:
      can_timeout = None if os.environ.get('NO_CAN_TIMEOUT', False) else 100
      self.can_sock = messaging.sub_sock('can', timeout=can_timeout)
      cloudlog.info(f"[INIT][controlsd] CAN socket created with timeout: {can_timeout}ms")

    if TICI:
      self.log_sock = messaging.sub_sock('androidLog')
      cloudlog.info("[INIT][controlsd] Android log socket created")

    # wait for one pandaState and one CAN packet
    cloudlog.info("[INIT][controlsd] Waiting for CAN messages...")
    print("Waiting for CAN messages...")
    get_one_can(self.can_sock)
    cloudlog.info("[INIT][controlsd] CAN messages received successfully")

    self.ti_ready = False

    try:
      cloudlog.info("[Mode][controlsd][INIT] Starting car fingerprinting...")
      self.CI, self.CP = get_car(self.can_sock, self.pm.sock['sendcan'])
      cloudlog.info(f"[Mode][controlsd][INIT] Car fingerprinted successfully: {self.CP.carName}, fingerprint={self.CP.carFingerprint}")
    except Exception as e:
      cloudlog.exception(f"[Mode][controlsd][INIT][ERROR] Failed to fingerprint car: {e}")
      raise

    # read params
    cloudlog.info("[Mode][controlsd][INIT] Reading parameters...")
    self.is_metric = self.params.get_bool("IsMetric")
    self.is_ldw_enabled = self.params.get_bool("IsLdwEnabled")
    openpilot_enabled_toggle = self.params.get_bool("OpenpilotEnabledToggle")
    passive = self.params.get_bool("Passive") or not openpilot_enabled_toggle

    # 方案3改进版：读取UI按钮设置的激活状态
    # 注意：不在这里清除参数，由 interface.py 统一管理
    self.lateral_only_active = False

    # 日志节流：记录上次输出processNotRunning警告的时间
    self.last_process_warning_time = 0


    # detect sound card presence and ensure successful init
    sounds_available = HARDWARE.get_sound_card_online()

    car_recognized = self.CP.carName != 'mock'

    controller_available = self.CI.CC is not None and not passive and not self.CP.dashcamOnly
    self.read_only = not car_recognized or not controller_available or self.CP.dashcamOnly
    if self.read_only:
      safety_config = car.CarParams.SafetyConfig.new_message()
      safety_config.safetyModel = car.CarParams.SafetyModel.noOutput
      self.CP.safetyConfigs = [safety_config]

    # Write CarParams for radard
    cloudlog.info("[Mode][controlsd][INIT] Writing CarParams...")
    cp_bytes = self.CP.to_bytes()
    self.params.put("CarParams", cp_bytes)
    put_nonblocking("CarParamsCache", cp_bytes)

    cloudlog.info("[Mode][controlsd][INIT] Initializing controllers...")
    self.CC = car.CarControl.new_message()
    self.AM = AlertManager()
    self.events = Events()

    try:
      cloudlog.info("[Mode][controlsd][INIT] Creating LongControl...")
      self.LoC = LongControl(self.CP)
      cloudlog.info("[Mode][controlsd][INIT] Creating VehicleModel...")
      self.VM = VehicleModel(self.CP)
      cloudlog.info("[Mode][controlsd][INIT] Creating LatControl...")
    except Exception as e:
      cloudlog.exception(f"[Mode][controlsd][INIT][ERROR] Failed to initialize controllers: {e}")
      raise

    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      self.LaC = LatControlAngle(self.CP, self.CI)
      cloudlog.info("[Mode][controlsd][INIT] LatControl type: angle")
    elif self.CP.lateralTuning.which() == 'pid':
      self.LaC = LatControlPID(self.CP, self.CI)
      cloudlog.info("[Mode][controlsd][INIT] LatControl type: PID")
    elif self.CP.lateralTuning.which() == 'indi':
      self.LaC = LatControlINDI(self.CP, self.CI)
      cloudlog.info("[Mode][controlsd][INIT] LatControl type: INDI")
    elif self.CP.lateralTuning.which() == 'lqr':
      self.LaC = LatControlLQR(self.CP, self.CI)
      cloudlog.info("[Mode][controlsd][INIT] LatControl type: LQR")

    cloudlog.info("[Mode][controlsd][INIT] Controllers initialized successfully")

    self.initialized = False
    self.state = State.disabled
    self.enabled = False
    self.active = False
    self.can_rcv_error = False
    self.soft_disable_timer = 0
    self.v_cruise_kph = 255
    self.v_cruise_kph_last = 0
    self.mismatch_counter = 0
    self.cruise_mismatch_counter = 0
    self.can_rcv_error_counter = 0
    self.last_blinker_frame = 0
    self.distance_traveled = 0
    self.last_functional_fan_frame = 0
    self.events_prev = []
    self.current_alert_types = [ET.PERMANENT]
    self.logged_comm_issue = False
    self.logged_comm_check_mode = False  # 用于避免重复记录通信检查模式
    self.button_timers = {ButtonEvent.Type.decelCruise: 0, ButtonEvent.Type.accelCruise: 0}
    self.last_actuators = car.CarControl.Actuators.new_message()

    # 横向控制模式参数读取计数器
    self.lateral_only_param_read_counter = 0

    # TODO: no longer necessary, aside from process replay
    self.sm['liveParameters'].valid = True

    self.startup_event = get_startup_event(car_recognized, controller_available, len(self.CP.carFw) > 0)
    cloudlog.info(f"[Mode][controlsd][INIT] Startup event: {self.startup_event}, car_recognized={car_recognized}, controller_available={controller_available}")

    if not sounds_available:
      self.events.add(EventName.soundsUnavailable, static=True)
    if not car_recognized:
      self.events.add(EventName.carUnrecognized, static=True)
      if len(self.CP.carFw) > 0:
        set_offroad_alert("Offroad_CarUnrecognized", True)
      else:
        set_offroad_alert("Offroad_NoFirmware", True)
    elif self.read_only:
      self.events.add(EventName.dashcamMode, static=True)
    elif self.joystick_mode:
      self.events.add(EventName.joystickDebug, static=True)
      self.startup_event = None

    # controlsd is driven by can recv, expected at 100Hz
    self.rk = Ratekeeper(100, print_delay_threshold=None)
    self.prof = Profiler(False)  # off by default

    cloudlog.info("=" * 80)
    cloudlog.info("[INIT][controlsd] *** Initialization complete, entering main loop ***")
    cloudlog.info("[INIT][controlsd] ControlsReady will be set after services initialization in data_sample()")
    cloudlog.info("=" * 80)

  def update_events(self, CS):
    """Compute carEvents from carState"""

    self.events.clear()

    # 定期读取横向控制模式参数（每 30 帧约 0.3 秒读一次）
    # 这样可以及时响应用户在 UI 上的点击操作
    self.lateral_only_param_read_counter += 1
    if self.lateral_only_param_read_counter >= 30:
      lateral_only_prev = self.lateral_only_active
      self.lateral_only_active = self.params.get_bool("LateralOnlyActive")
      if self.lateral_only_active != lateral_only_prev:
        cloudlog.info(f"[Mode][controlsd] Lateral-only mode changed: {lateral_only_prev} -> {self.lateral_only_active}")
      self.lateral_only_param_read_counter = 0

    # Add startup event
    if self.startup_event is not None:
      self.events.add(self.startup_event)
      self.startup_event = None

    # Don't add any more events if not initialized
    if not self.initialized:
      self.events.add(EventName.controlsInitializing)
      return

    self.events.add_from_msg(CS.events)
    # 只有在驾驶员监控启用时才添加相关事件
    if self.sm.valid['driverMonitoringState']:
      self.events.add_from_msg(self.sm['driverMonitoringState'].events)

    # Create events for battery, temperature, disk space, and memory
    if EON and (self.sm['peripheralState'].pandaType != PandaType.uno) and \
       self.sm['deviceState'].batteryPercent < 1 and self.sm['deviceState'].chargingError:
      # at zero percent battery, while discharging, OP should not allowed
      self.events.add(EventName.lowBattery)
    if self.sm['deviceState'].thermalStatus >= ThermalStatus.red:
      self.events.add(EventName.overheat)
    if self.sm['deviceState'].freeSpacePercent < 7 and not SIMULATION:
      # under 7% of space free no enable allowed
      self.events.add(EventName.outOfSpace)
    # TODO: make tici threshold the same
    if self.sm['deviceState'].memoryUsagePercent > (90 if TICI else 65) and not SIMULATION:
      self.events.add(EventName.lowMemory)

    # TODO: enable this once loggerd CPU usage is more reasonable
    #cpus = list(self.sm['deviceState'].cpuUsagePercent)[:(-1 if EON else None)]
    #if max(cpus, default=0) > 95 and not SIMULATION:
    #  self.events.add(EventName.highCpuUsage)

    # Alert if fan isn't spinning for 5 seconds
    if self.sm['peripheralState'].pandaType in (PandaType.uno, PandaType.dos):
      if self.sm['peripheralState'].fanSpeedRpm == 0 and self.sm['deviceState'].fanSpeedPercentDesired > 50:
        if (self.sm.frame - self.last_functional_fan_frame) * DT_CTRL > 5.0:
          self.events.add(EventName.fanMalfunction)
      else:
        self.last_functional_fan_frame = self.sm.frame

    # Handle calibration status
    cal_status = self.sm['liveCalibration'].calStatus
    if cal_status != Calibration.CALIBRATED:
      if cal_status == Calibration.UNCALIBRATED:
        self.events.add(EventName.calibrationIncomplete)
      else:
        self.events.add(EventName.calibrationInvalid)

    

    # Handle lane change
    if self.sm['lateralPlan'].laneChangeState == LaneChangeState.preLaneChange:
      direction = self.sm['lateralPlan'].laneChangeDirection
      if (CS.leftBlindspot and direction == LaneChangeDirection.left) or \
         (CS.rightBlindspot and direction == LaneChangeDirection.right):
        self.events.add(EventName.laneChangeBlocked)
      else:
        if direction == LaneChangeDirection.left:
          self.events.add(EventName.preLaneChangeLeft)
        else:
          self.events.add(EventName.preLaneChangeRight)
    elif self.sm['lateralPlan'].laneChangeState in (LaneChangeState.laneChangeStarting,
                                                    LaneChangeState.laneChangeFinishing):
      self.events.add(EventName.laneChange)

    if not CS.canValid:
      self.events.add(EventName.canError)

    for i, pandaState in enumerate(self.sm['pandaStates']):
      # All pandas must match the list of safetyConfigs, and if outside this list, must be silent or noOutput
      if i < len(self.CP.safetyConfigs):
        safety_mismatch = pandaState.safetyModel != self.CP.safetyConfigs[i].safetyModel or \
                          pandaState.safetyParam != self.CP.safetyConfigs[i].safetyParam or \
                          pandaState.unsafeMode != self.CP.unsafeMode
      else:
        safety_mismatch = pandaState.safetyModel not in IGNORED_SAFETY_MODES

      if safety_mismatch or self.mismatch_counter >= 200:
        self.events.add(EventName.controlsMismatch)

      if log.PandaState.FaultType.relayMalfunction in pandaState.faults:
        self.events.add(EventName.relayMalfunction)

      if pandaState.torqueInterceptorDetected and not self.ti_ready:
        self.ti_ready = True
        self.CP.enableTorqueInterceptor = True
        #Update CP based on torque_interceptor_ready
        self.CP = get_ti()

    # Check for HW or system issues

    if len(self.sm['radarState'].radarErrors):
      self.events.add(EventName.radarFault)
    elif not self.sm.valid["pandaStates"]:
      self.events.add(EventName.usbError)
    else:
      # 动态检查通信状态，支持运行时切换驾驶员监控
      comm_issue = False
      driver_monitoring_enabled = self.params.get_bool("DriverMonitoringEnabled")

      # 检测驾驶员监控状态是否改变
      if driver_monitoring_enabled != self.driver_monitoring_enabled:
        cloudlog.info(f"[DM][controlsd] Driver monitoring state changed: {self.driver_monitoring_enabled} -> {driver_monitoring_enabled}")
        self.driver_monitoring_enabled = driver_monitoring_enabled
        self.logged_comm_check_mode = False  # 状态改变时重置，以便记录新模式

      # 始终使用手动检查以支持运行时动态切换
      # 因为 SubMaster 的 ignore_alive 列表在创建时就固定了，无法运行时修改
      if not self.logged_comm_check_mode:
        if driver_monitoring_enabled:
          cloudlog.info("[DM][controlsd] Communication check mode: MANUAL with driver monitoring (checking all services)")
        else:
          cloudlog.info("[DM][controlsd] Communication check mode: MANUAL without driver monitoring (skipping driverMonitoringState)")
        self.logged_comm_check_mode = True

      # 定期记录关键服务状态（每30秒记录一次）
      if self.sm.frame % int(30.0 / DT_CTRL) == 0:
        key_services = ['lateralPlan', 'modelV2', 'carState', 'controlsState']
        status_info = []
        for svc in key_services:
          if svc in self.sm.data:
            alive = self.sm.alive.get(svc, False)
            valid = self.sm.valid.get(svc, False)
            in_ignore_freq = svc in self.sm.ignore_average_freq
            status_info.append(f"{svc}(alive={alive}, valid={valid}, ignore_freq={in_ignore_freq})")
        cloudlog.info(f"[DM][controlsd] Key services status: {', '.join(status_info)}")

      for key in self.sm.data.keys():
        # 如果驾驶员监控禁用，跳过 driverMonitoringState
        if key == 'driverMonitoringState' and not driver_monitoring_enabled:
          continue
        # 检查存活状态（如果不在 ignore_alive 列表中）
        if key not in self.sm.ignore_alive:
          if not self.sm.alive.get(key, False):
            comm_issue = True
            if not self.logged_comm_issue:
              cloudlog.warning(f"[DM][controlsd] Service not alive: {key}")
            break
        # 检查有效性状态（跳过 ignore_average_freq 列表中的服务）
        # ignore_average_freq 中的服务（如 radarState, longitudinalPlan, lateralPlan）允许可变频率和有效性
        if key not in self.sm.ignore_average_freq:
          if not self.sm.valid.get(key, False):
            comm_issue = True
            if not self.logged_comm_issue:
              cloudlog.warning(f"[DM][controlsd] Service not valid: {key}")
            break

      if comm_issue or self.can_rcv_error:
        self.events.add(EventName.commIssue)
        if not self.logged_comm_issue:
          invalid = [s for s, valid in self.sm.valid.items() if not valid]
          not_alive = [s for s, alive in self.sm.alive.items() if not alive]
          cloudlog.event("commIssue", invalid=invalid, not_alive=not_alive, can_error=self.can_rcv_error, error=True)
          self.logged_comm_issue = True
      else:
        self.logged_comm_issue = False

    # 检查关键状态并记录（每5秒记录一次）
    if self.sm.frame % int(5.0 / DT_CTRL) == 0:
      cloudlog.info(f"[ENGAGE][controlsd] liveParameters.valid={self.sm['liveParameters'].valid}, "
                    f"lateralPlan.mpcSolutionValid={self.sm['lateralPlan'].mpcSolutionValid}, "
                    f"liveLocationKalman.sensorsOK={self.sm['liveLocationKalman'].sensorsOK}, "
                    f"liveLocationKalman.posenetOK={self.sm['liveLocationKalman'].posenetOK}, "
                    f"liveLocationKalman.deviceStable={self.sm['liveLocationKalman'].deviceStable}")

    if not self.sm['liveParameters'].valid:
      self.events.add(EventName.vehicleModelInvalid)
      if self.sm.frame % int(5.0 / DT_CTRL) == 0:
        cloudlog.warning("[ENGAGE][controlsd] Adding vehicleModelInvalid event (liveParameters.valid=False)")
    if not self.sm['lateralPlan'].mpcSolutionValid:
      self.events.add(EventName.plannerError)
      if self.sm.frame % int(5.0 / DT_CTRL) == 0:
        cloudlog.warning("[ENGAGE][controlsd] Adding plannerError event (lateralPlan.mpcSolutionValid=False)")
    if not self.sm['liveLocationKalman'].sensorsOK and not NOSENSOR:
      if self.sm.frame > 5 / DT_CTRL:  # Give locationd some time to receive all the inputs
        self.events.add(EventName.sensorDataInvalid)
        if self.sm.frame % int(5.0 / DT_CTRL) == 0:
          cloudlog.warning("[ENGAGE][controlsd] Adding sensorDataInvalid event (liveLocationKalman.sensorsOK=False)")
    if not self.sm['liveLocationKalman'].posenetOK:
      self.events.add(EventName.posenetInvalid)
      if self.sm.frame % int(5.0 / DT_CTRL) == 0:
        cloudlog.warning("[ENGAGE][controlsd] Adding posenetInvalid event (liveLocationKalman.posenetOK=False)")
    if not self.sm['liveLocationKalman'].deviceStable:
      self.events.add(EventName.deviceFalling)
      if self.sm.frame % int(5.0 / DT_CTRL) == 0:
        cloudlog.warning("[ENGAGE][controlsd] Adding deviceFalling event (liveLocationKalman.deviceStable=False)")

    if not REPLAY:
      # Check for mismatch between openpilot and car's PCM
      cruise_mismatch = CS.cruiseState.enabled and (not self.enabled or not self.CP.pcmCruise)
      self.cruise_mismatch_counter = self.cruise_mismatch_counter + 1 if cruise_mismatch else 0
      if self.cruise_mismatch_counter > int(3. / DT_CTRL):
        self.events.add(EventName.cruiseMismatch)

    # Check for FCW
    stock_long_is_braking = self.enabled and not self.CP.openpilotLongitudinalControl and CS.aEgo < -1.5
    model_fcw = self.sm['modelV2'].meta.hardBrakePredicted and not CS.brakePressed and not stock_long_is_braking
    planner_fcw = self.sm['longitudinalPlan'].fcw and self.enabled
    if planner_fcw or model_fcw:
      self.events.add(EventName.fcw)

    if TICI:
      for m in messaging.drain_sock(self.log_sock, wait_for_one=False):
        try:
          msg = m.androidLog.message
          if any(err in msg for err in ("ERROR_CRC", "ERROR_ECC", "ERROR_STREAM_UNDERFLOW", "APPLY FAILED")):
            csid = msg.split("CSID:")[-1].split(" ")[0]
            evt = CSID_MAP.get(csid, None)
            if evt is not None:
              self.events.add(evt)
        except UnicodeDecodeError:
          pass

    # TODO: fix simulator
    if not SIMULATION:
      if not NOSENSOR:
        if not self.sm['liveLocationKalman'].gpsOK and (self.distance_traveled > 1000):
          # Not show in first 1 km to allow for driving out of garage. This event shows after 5 minutes
          self.events.add(EventName.noGps)
      if not self.sm.all_alive(self.camera_packets):
        self.events.add(EventName.cameraMalfunction)
      if self.sm['modelV2'].frameDropPerc > 20:
        self.events.add(EventName.modeldLagging)
      if self.sm['liveLocationKalman'].excessiveResets:
        self.events.add(EventName.localizerMalfunction)

      # Check if all manager processes are running
      not_running = {p.name for p in self.sm['managerState'].processes if not p.running}
      # 动态读取驾驶员监控状态，支持运行时切换
      driver_monitoring_enabled = self.params.get_bool("DriverMonitoringEnabled")
      if not driver_monitoring_enabled:
        not_running = not_running - {"dmonitoringd", "dmonitoringmodeld"}
      if self.sm.rcv_frame['managerState'] and (not_running - IGNORE_PROCESSES):
        missing_processes = not_running - IGNORE_PROCESSES
        # 日志节流：只在每20秒输出一次警告，避免影响系统性能
        current_time = sec_since_boot()
        if current_time - self.last_process_warning_time >= 20.0:
          cloudlog.warning(f"processNotRunning event added! Missing processes: {missing_processes}, IGNORE_PROCESSES: {IGNORE_PROCESSES}")
          self.last_process_warning_time = current_time
        self.events.add(EventName.processNotRunning)

    # Only allow engagement with brake pressed when stopped behind another stopped car
    speeds = self.sm['longitudinalPlan'].speeds
    if len(speeds) > 1:
      v_future = speeds[-1]
    else:
      v_future = 100.0
    if CS.brakePressed and v_future >= self.CP.vEgoStarting \
      and self.CP.openpilotLongitudinalControl and CS.vEgo < 0.3:
      self.events.add(EventName.noTarget)

  def data_sample(self):
    """Receive data from sockets and update carState"""

    # Update carState from CAN
    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)
    CS = self.CI.update(self.CC, can_strs)

    self.sm.update(0)

    if not self.initialized:
      all_valid = CS.canValid and self.sm.all_alive_and_valid()
      # 添加详细的诊断日志
      if not all_valid and self.sm.frame % 50 == 0:  # 每0.5秒记录一次
        invalid = [s for s, valid in self.sm.valid.items() if not valid]
        not_alive = [s for s, alive in self.sm.alive.items() if not alive]
        cloudlog.info(f"[Mode][controlsd][INIT] Waiting for initialization: CS.canValid={CS.canValid}, frame={self.sm.frame}, invalid={invalid}, not_alive={not_alive}")
      if all_valid or self.sm.frame * DT_CTRL > 3.5 or SIMULATION:
        cloudlog.info(f"[Mode][controlsd][INIT] Starting CI.init(), all_valid={all_valid}, frame={self.sm.frame}, read_only={self.read_only}")
        if not self.read_only:
          try:
            self.CI.init(self.CP, self.can_sock, self.pm.sock['sendcan'])
            cloudlog.info("[Mode][controlsd][INIT] CI.init() completed successfully")
          except Exception as e:
            cloudlog.exception(f"[Mode][controlsd][INIT][ERROR] CI.init() failed: {e}")
            raise
        self.initialized = True
        cloudlog.info("=" * 80)
        cloudlog.info("[Mode][controlsd][INIT] System initialized, controls ready")
        cloudlog.info("[Mode][controlsd][INIT] Setting ControlsReady=True")
        cloudlog.info("=" * 80)

        if REPLAY and self.sm['pandaStates'][0].controlsAllowed:
          self.state = State.enabled

        Params().put_bool("ControlsReady", True)

    # Check for CAN timeout
    if not can_strs:
      self.can_rcv_error_counter += 1
      self.can_rcv_error = True
    else:
      self.can_rcv_error = False

    # When the panda and controlsd do not agree on controls_allowed
    # we want to disengage openpilot. However the status from the panda goes through
    # another socket other than the CAN messages and one can arrive earlier than the other.
    # Therefore we allow a mismatch for two samples, then we trigger the disengagement.
    if not self.enabled:
      self.mismatch_counter = 0

    # All pandas not in silent mode must have controlsAllowed when openpilot is enabled
    if self.enabled and any(not ps.controlsAllowed for ps in self.sm['pandaStates']
           if ps.safetyModel not in IGNORED_SAFETY_MODES):
      self.mismatch_counter += 1

    self.distance_traveled += CS.vEgo * DT_CTRL

    return CS

  def state_transition(self, CS):
    """Compute conditional state transitions and execute actions on state transitions"""

    self.v_cruise_kph_last = self.v_cruise_kph

    # if stock cruise is completely disabled, then we can use our own set speed logic
    if not self.CP.pcmCruise:
      self.v_cruise_kph = update_v_cruise(self.v_cruise_kph, CS.buttonEvents, self.button_timers, self.enabled, self.is_metric)
    elif CS.cruiseState.enabled:
      prev_v_cruise = self.v_cruise_kph
      self.v_cruise_kph = CS.cruiseState.speed * CV.MS_TO_KPH
      # 记录ACC速度变化（降低频率，只在变化超过1 km/h时记录）
      if abs(self.v_cruise_kph - prev_v_cruise) > 1.0:
        cloudlog.debug(f"[Mode][controlsd] ACC cruise speed updated: {prev_v_cruise:.1f} -> {self.v_cruise_kph:.1f} km/h")
    elif self.lateral_only_active:
      # 仅横向模式：ACC未启动，使用当前车速作为巡航速度
      # 这样可以避免纵向规划器的MPC误判为碰撞（v_cruise=255但实际车速很低）
      prev_v_cruise = self.v_cruise_kph
      self.v_cruise_kph = CS.vEgo * CV.MS_TO_KPH
      # 记录仅横向模式的速度设置（降低频率，只在变化超过5 km/h时记录）
      if abs(self.v_cruise_kph - prev_v_cruise) > 5.0:
        cloudlog.debug(f"[Mode][controlsd] Lateral-only v_cruise updated: {prev_v_cruise:.1f} -> {self.v_cruise_kph:.1f} km/h (using vEgo)")
    else:
      # ACC待命或关闭状态，且未激活仅横向模式：使用当前车速避免v_cruise保持在255导致FCW误报
      # 这是关键修复：确保v_cruise_kph始终有合理值，即使ACC处于待命状态
      prev_v_cruise = self.v_cruise_kph
      self.v_cruise_kph = max(CS.vEgo * CV.MS_TO_KPH, 30.0)  # 最小30km/h，避免过低
      # 只在首次设置或变化较大时记录（避免日志刷屏）
      if prev_v_cruise > 200.0 or abs(self.v_cruise_kph - prev_v_cruise) > 10.0:
        cloudlog.info(f"[Mode][controlsd] ACC standby/off: v_cruise set to vEgo: {prev_v_cruise:.1f} -> {self.v_cruise_kph:.1f} km/h")

    # decrement the soft disable timer at every step, as it's reset on
    # entrance in SOFT_DISABLING state
    self.soft_disable_timer = max(0, self.soft_disable_timer - 1)

    self.current_alert_types = [ET.PERMANENT]

    # ENABLED, PRE ENABLING, SOFT DISABLING
    if self.state != State.disabled:
      # user and immediate disable always have priority in a non-disabled state
      if self.events.any(ET.USER_DISABLE):
        self.state = State.disabled
        self.current_alert_types.append(ET.USER_DISABLE)

      elif self.events.any(ET.IMMEDIATE_DISABLE):
        self.state = State.disabled
        self.current_alert_types.append(ET.IMMEDIATE_DISABLE)

      else:
        # ENABLED
        if self.state == State.enabled:
          if self.events.any(ET.SOFT_DISABLE):
            self.state = State.softDisabling
            self.soft_disable_timer = int(SOFT_DISABLE_TIME / DT_CTRL)
            self.current_alert_types.append(ET.SOFT_DISABLE)

        # SOFT DISABLING
        elif self.state == State.softDisabling:
          if not self.events.any(ET.SOFT_DISABLE):
            # no more soft disabling condition, so go back to ENABLED
            self.state = State.enabled

          elif self.soft_disable_timer > 0:
            self.current_alert_types.append(ET.SOFT_DISABLE)

          elif self.soft_disable_timer <= 0:
            self.state = State.disabled

        # PRE ENABLING
        elif self.state == State.preEnabled:
          if not self.events.any(ET.PRE_ENABLE):
            self.state = State.enabled
          else:
            self.current_alert_types.append(ET.PRE_ENABLE)

    # DISABLED
    elif self.state == State.disabled:
      if self.events.any(ET.ENABLE):
        if self.events.any(ET.NO_ENTRY):
          self.current_alert_types.append(ET.NO_ENTRY)

        else:
          if self.events.any(ET.PRE_ENABLE):
            self.state = State.preEnabled
          else:
            self.state = State.enabled
          self.current_alert_types.append(ET.ENABLE)
          self.v_cruise_kph = initialize_v_cruise(CS.vEgo, CS.buttonEvents, self.v_cruise_kph_last)

    # Check if actuators are enabled
    self.active = self.state == State.enabled or self.state == State.softDisabling
    if self.active:
      self.current_alert_types.append(ET.WARNING)

    # Check if openpilot is engaged
    self.enabled = self.active or self.state == State.preEnabled

  def state_control(self, CS):
    """Given the state, this function returns an actuators packet"""

    # Update VehicleModel
    params = self.sm['liveParameters']
    x = max(params.stiffnessFactor, 0.1)
    sr = max(params.steerRatio, 0.1)
    self.VM.update_params(x, sr)

    lat_plan = self.sm['lateralPlan']
    long_plan = self.sm['longitudinalPlan']

    actuators = car.CarControl.Actuators.new_message()
    actuators.longControlState = self.LoC.long_control_state

    if CS.leftBlinker or CS.rightBlinker:
      self.last_blinker_frame = self.sm.frame

    # State specific actions

    if not self.active:
      self.LaC.reset()
      self.LoC.reset(v_pid=CS.vEgo)

    # 横向控制模式状态在 update_events 中已读取，这里直接使用
    # 额外的实时冲突检测：如果ACC已启动，立即退出仅横向模式
    # 这提供了额外的安全层，避免参数读取延迟导致的模式冲突
    if self.lateral_only_active and CS.cruiseState.enabled:
      self.lateral_only_active = False
      cloudlog.warning("[Mode][controlsd] Lateral-only mode disabled: ACC is active (conflict detected)")

    if not self.joystick_mode:
      # accel PID loop - 仅在非横向控制模式下才启用纵向控制
      if not self.lateral_only_active:
        pid_accel_limits = self.CI.get_pid_accel_limits(self.CP, CS.vEgo, self.v_cruise_kph * CV.KPH_TO_MS)
        actuators.accel = self.LoC.update(self.active, CS, self.CP, long_plan, pid_accel_limits)
      else:
        # 横向控制模式：禁用纵向控制
        actuators.accel = 0.0
        self.LoC.reset(v_pid=CS.vEgo)
        # 定期记录仅横向模式状态（每5秒一次）
        if not hasattr(self, 'lateral_only_log_counter'):
          self.lateral_only_log_counter = 0
        self.lateral_only_log_counter += 1
        if self.lateral_only_log_counter >= 500:  # 100Hz * 5s = 500
          cloudlog.debug(f"[Mode][controlsd] Lateral-only mode active: long_control=disabled, v_cruise={self.v_cruise_kph:.1f}km/h, vEgo={CS.vEgo*CV.MS_TO_KPH:.1f}km/h")
          self.lateral_only_log_counter = 0

      # Steering PID loop and lateral MPC
      # 仅横向模式：移除车速限制，只要在D档就可以激活
      # 同时忽略5秒脱手警告，因为用户正在手动控制油门/刹车，说明驾驶员是清醒的
      # 正常模式：需要满足最小转向速度
      if self.lateral_only_active:
        # 横向模式：忽略steerWarning（5秒脱手检测），因为驾驶员在控制油门/刹车
        lat_active = self.active and not CS.steerError
        # 记录横向控制激活状态变化
        if not hasattr(self, 'lat_active_last'):
          self.lat_active_last = False
        if lat_active != self.lat_active_last:
          cloudlog.info(f"[Mode][controlsd] Lateral control active changed: {self.lat_active_last} -> {lat_active} (lateral-only mode)")
          self.lat_active_last = lat_active
      else:
        lat_active = self.active and not CS.steerWarning and not CS.steerError and CS.vEgo > self.CP.minSteerSpeed
        # 记录横向控制激活状态变化（完整控制模式）
        if not hasattr(self, 'lat_active_last'):
          self.lat_active_last = False
        if lat_active != self.lat_active_last:
          cloudlog.info(f"[Mode][controlsd] Lateral control active changed: {self.lat_active_last} -> {lat_active} (full control mode, vEgo={CS.vEgo:.1f}m/s, minSteerSpeed={self.CP.minSteerSpeed:.1f}m/s)")
          self.lat_active_last = lat_active

      desired_curvature, desired_curvature_rate = get_lag_adjusted_curvature(self.CP, CS.vEgo,
                                                                             lat_plan.psis,
                                                                             lat_plan.curvatures,
                                                                             lat_plan.curvatureRates)
      actuators.steer, actuators.steeringAngleDeg, lac_log = self.LaC.update(lat_active, CS, self.CP, self.VM, params, self.last_actuators,
                                                                             desired_curvature, desired_curvature_rate)
    else:
      lac_log = log.ControlsState.LateralDebugState.new_message()
      if self.sm.rcv_frame['testJoystick'] > 0 and self.active:
        actuators.accel = 4.0*clip(self.sm['testJoystick'].axes[0], -1, 1)

        steer = clip(self.sm['testJoystick'].axes[1], -1, 1)
        # max angle is 45 for angle-based cars
        actuators.steer, actuators.steeringAngleDeg = steer, steer * 45.

        lac_log.active = True
        lac_log.steeringAngleDeg = CS.steeringAngleDeg
        lac_log.output = steer
        lac_log.saturated = abs(steer) >= 0.9

    # Send a "steering required alert" if saturation count has reached the limit
    if lac_log.active and lac_log.saturated and not CS.steeringPressed:
      dpath_points = lat_plan.dPathPoints
      if len(dpath_points):
        # Check if we deviated from the path
        # TODO use desired vs actual curvature
        left_deviation = actuators.steer > 0 and dpath_points[0] < -0.20
        right_deviation = actuators.steer < 0 and dpath_points[0] > 0.20

        if left_deviation or right_deviation:
          self.events.add(EventName.steerSaturated)

    # Ensure no NaNs/Infs
    for p in ACTUATOR_FIELDS:
      attr = getattr(actuators, p)
      if not isinstance(attr, Number):
        continue

      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{p} not finite {actuators.to_dict()}")
        setattr(actuators, p, 0.0)

    return actuators, lac_log

  def update_button_timers(self, buttonEvents):
    # increment timer for buttons still pressed
    for k in self.button_timers:
      if self.button_timers[k] > 0:
        self.button_timers[k] += 1

    for b in buttonEvents:
      if b.type.raw in self.button_timers:
        self.button_timers[b.type.raw] = 1 if b.pressed else 0

  def publish_logs(self, CS, start_time, actuators, lac_log):
    """Send actuators and hud commands to the car, send controlsstate and MPC logging"""

    CC = car.CarControl.new_message()
    CC.enabled = self.enabled
    CC.active = self.active
    CC.actuators = actuators

    orientation_value = self.sm['liveLocationKalman'].orientationNED.value
    if len(orientation_value) > 2:
      CC.roll = orientation_value[0]
      CC.pitch = orientation_value[1]

    CC.cruiseControl.cancel = CS.cruiseState.enabled and (not self.enabled or not self.CP.pcmCruise)
    if self.joystick_mode and self.sm.rcv_frame['testJoystick'] > 0 and self.sm['testJoystick'].buttons[0]:
      CC.cruiseControl.cancel = True

    hudControl = CC.hudControl
    hudControl.setSpeed = float(self.v_cruise_kph * CV.KPH_TO_MS)
    hudControl.speedVisible = self.enabled
    hudControl.lanesVisible = self.enabled
    hudControl.leadVisible = self.sm['longitudinalPlan'].hasLead

    hudControl.rightLaneVisible = True
    hudControl.leftLaneVisible = True

    recent_blinker = (self.sm.frame - self.last_blinker_frame) * DT_CTRL < 5.0  # 5s blinker cooldown
    ldw_allowed = self.is_ldw_enabled and CS.vEgo > LDW_MIN_SPEED and not recent_blinker \
                    and not self.active and self.sm['liveCalibration'].calStatus == Calibration.CALIBRATED

    model_v2 = self.sm['modelV2']
    desire_prediction = model_v2.meta.desirePrediction
    if len(desire_prediction) and ldw_allowed:
      right_lane_visible = self.sm['lateralPlan'].rProb > 0.5
      left_lane_visible = self.sm['lateralPlan'].lProb > 0.5
      l_lane_change_prob = desire_prediction[Desire.laneChangeLeft - 1]
      r_lane_change_prob = desire_prediction[Desire.laneChangeRight - 1]

      lane_lines = model_v2.laneLines
      l_lane_close = left_lane_visible and (lane_lines[1].y[0] > -(1.08 + CAMERA_OFFSET))
      r_lane_close = right_lane_visible and (lane_lines[2].y[0] < (1.08 - CAMERA_OFFSET))

      hudControl.leftLaneDepart = bool(l_lane_change_prob > LANE_DEPARTURE_THRESHOLD and l_lane_close)
      hudControl.rightLaneDepart = bool(r_lane_change_prob > LANE_DEPARTURE_THRESHOLD and r_lane_close)

    if hudControl.rightLaneDepart or hudControl.leftLaneDepart:
      self.events.add(EventName.ldw)

    clear_event_types = set()
    if ET.WARNING not in self.current_alert_types:
      clear_event_types.add(ET.WARNING)
    if self.enabled:
      clear_event_types.add(ET.NO_ENTRY)

    alerts = self.events.create_alerts(self.current_alert_types, [self.CP, self.sm, self.is_metric, self.soft_disable_timer])
    self.AM.add_many(self.sm.frame, alerts)
    current_alert = self.AM.process_alerts(self.sm.frame, clear_event_types)
    if current_alert:
      hudControl.visualAlert = current_alert.visual_alert

    if not self.read_only and self.initialized:
      # send car controls over can
      self.last_actuators, can_sends = self.CI.apply(CC)
      self.pm.send('sendcan', can_list_to_can_capnp(can_sends, msgtype='sendcan', valid=CS.canValid))
      CC.actuatorsOutput = self.last_actuators

    # 只有在驾驶员监控启用时才检查注意力状态
    # 如果驾驶员监控关闭，awarenessStatus默认为1.0（正常状态）
    if self.sm.valid['driverMonitoringState']:
      force_decel = (self.sm['driverMonitoringState'].awarenessStatus < 0.) or \
                    (self.state == State.softDisabling)
    else:
      force_decel = (self.state == State.softDisabling)

    # Curvature & Steering angle
    params = self.sm['liveParameters']

    steer_angle_without_offset = math.radians(CS.steeringAngleDeg - params.angleOffsetDeg)
    curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, params.roll)

    # controlsState
    dat = messaging.new_message('controlsState')
    dat.valid = CS.canValid
    controlsState = dat.controlsState
    if current_alert:
      controlsState.alertText1 = current_alert.alert_text_1
      controlsState.alertText2 = current_alert.alert_text_2
      controlsState.alertSize = current_alert.alert_size
      controlsState.alertStatus = current_alert.alert_status
      controlsState.alertBlinkingRate = current_alert.alert_rate
      controlsState.alertType = current_alert.alert_type
      controlsState.alertSound = current_alert.audible_alert

    controlsState.canMonoTimes = list(CS.canMonoTimes)
    controlsState.longitudinalPlanMonoTime = self.sm.logMonoTime['longitudinalPlan']
    controlsState.lateralPlanMonoTime = self.sm.logMonoTime['lateralPlan']
    controlsState.enabled = self.enabled
    controlsState.active = self.active
    controlsState.curvature = curvature
    controlsState.state = self.state
    controlsState.engageable = not self.events.any(ET.NO_ENTRY)

    # 记录 engageable 状态和 NO_ENTRY 事件（每5秒记录一次）
    if self.sm.frame % int(5.0 / DT_CTRL) == 0:
      no_entry_events = [EVENT_NAME[e] for e in self.events.names if e in EVENTS and ET.NO_ENTRY in EVENTS[e]]
      cloudlog.info(f"[ENGAGE][controlsd] engageable={controlsState.engageable}, "
                    f"enabled={self.enabled}, state={self.state}, "
                    f"NO_ENTRY_events={no_entry_events if no_entry_events else 'None'}")

    controlsState.longControlState = self.LoC.long_control_state
    controlsState.vPid = float(self.LoC.v_pid)
    controlsState.vCruise = float(self.v_cruise_kph)
    controlsState.upAccelCmd = float(self.LoC.pid.p)
    controlsState.uiAccelCmd = float(self.LoC.pid.i)
    controlsState.ufAccelCmd = float(self.LoC.pid.f)
    controlsState.cumLagMs = -self.rk.remaining * 1000.
    controlsState.startMonoTime = int(start_time * 1e9)
    controlsState.forceDecel = bool(force_decel)
    controlsState.canErrorCounter = self.can_rcv_error_counter

    lat_tuning = self.CP.lateralTuning.which()
    if self.joystick_mode:
      controlsState.lateralControlState.debugState = lac_log
    elif self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      controlsState.lateralControlState.angleState = lac_log
    elif lat_tuning == 'pid':
      controlsState.lateralControlState.pidState = lac_log
    elif lat_tuning == 'lqr':
      controlsState.lateralControlState.lqrState = lac_log
    elif lat_tuning == 'indi':
      controlsState.lateralControlState.indiState = lac_log

    self.pm.send('controlsState', dat)

    # carState
    car_events = self.events.to_msg()
    cs_send = messaging.new_message('carState')
    cs_send.valid = CS.canValid
    cs_send.carState = CS
    cs_send.carState.events = car_events
    self.pm.send('carState', cs_send)

    # carEvents - logged every second or on change
    if (self.sm.frame % int(1. / DT_CTRL) == 0) or (self.events.names != self.events_prev):
      ce_send = messaging.new_message('carEvents', len(self.events))
      ce_send.carEvents = car_events
      self.pm.send('carEvents', ce_send)
    self.events_prev = self.events.names.copy()

    # carParams - logged every 50 seconds (> 1 per segment)
    if (self.sm.frame % int(50. / DT_CTRL) == 0):
      cp_send = messaging.new_message('carParams')
      cp_send.carParams = self.CP
      self.pm.send('carParams', cp_send)

    # carControl
    cc_send = messaging.new_message('carControl')
    cc_send.valid = CS.canValid
    cc_send.carControl = CC
    self.pm.send('carControl', cc_send)

    # copy CarControl to pass to CarInterface on the next iteration
    self.CC = CC

  def step(self):
    start_time = sec_since_boot()
    self.prof.checkpoint("Ratekeeper", ignore=True)

    # Sample data from sockets and get a carState
    CS = self.data_sample()
    self.prof.checkpoint("Sample")

    self.update_events(CS)

    if not self.read_only and self.initialized:
      # Update control state
      self.state_transition(CS)
      self.prof.checkpoint("State transition")

    # Compute actuators (runs PID loops and lateral MPC)
    actuators, lac_log = self.state_control(CS)

    self.prof.checkpoint("State Control")

    # Publish data
    self.publish_logs(CS, start_time, actuators, lac_log)
    self.prof.checkpoint("Sent")

    self.update_button_timers(CS.buttonEvents)

  def controlsd_thread(self):
    cloudlog.info("[MAIN][controlsd] *** Main control loop started ***")
    loop_count = 0
    while True:
      self.step()
      self.rk.monitor_time()
      self.prof.display()

      # Log first few iterations to confirm loop is running
      if loop_count < 5:
        cloudlog.info(f"[MAIN][controlsd] Loop iteration {loop_count + 1} completed")
        loop_count += 1

def main(sm=None, pm=None, logcan=None):
  cloudlog.info("[MAIN][controlsd] *** main() function called ***")
  try:
    controls = Controls(sm, pm, logcan)
    cloudlog.info("[MAIN][controlsd] Controls object created successfully, starting thread")
    controls.controlsd_thread()
  except Exception as e:
    cloudlog.exception(f"[MAIN][controlsd][ERROR] Fatal error in main(): {e}")
    raise


if __name__ == "__main__":
  main()
