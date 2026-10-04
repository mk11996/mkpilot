from cereal import car
from selfdrive.config import Conversions as CV
from opendbc.can.can_define import CANDefine
from opendbc.can.parser import CANParser
from selfdrive.car.interfaces import CarStateBase
from selfdrive.car.mazda.values import DBC, LKAS_LIMITS, GEN1, TI_STATE, CAR
from selfdrive.swaglog import cloudlog

ButtonType = car.CarState.ButtonEvent.Type

# 速度校正 - 根据观察到的误差进行调整
# 如果显示速度比实际速度快x%，则 SPEED_SENSOR_OVERREAD_PERCENT = x
# 例如：显示77km/h而实际是70km/h，则 (77-70)/70 ≈ 0.10 = 10% 高估，设置 SPEED_SENSOR_OVERREAD_PERCENT = 10
SPEED_SENSOR_OVERREAD_PERCENT = 9  # 速度传感器高估百分比，根据需要调整

# 计算校正因子（将测量速度降低以匹配实际速度）
SPEED_CORRECTION_FACTOR = (100.0 - SPEED_SENSOR_OVERREAD_PERCENT) / 100.0  # 例如：10%高估则校正因子为0.90

class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)

    can_define = CANDefine(DBC[CP.carFingerprint]["pt"])
    self.shifter_values = can_define.dv["GEAR"]["GEAR"]

    self.crz_btns_counter = 0
    self.acc_active_last = False
    self.lkas_allowed_speed = False
    self.lkas_disabled = False

    # 按键状态变量（用于消抖）
    self.button_cancel_prev = False
    self.button_resume_prev = False
    self.button_set_plus_prev = False
    self.button_set_minus_prev = False

    # RES按键长按检测（用于激活仅横向模式）
    self.button_resume_press_start_time = 0  # 按下开始时间（帧计数）
    self.button_resume_long_press_triggered = False  # 是否已触发长按
    self.button_resume_release_frames = 0  # 释放帧计数（用于防抖）
    self.LONG_PRESS_FRAMES = 150  # 1.5秒 = 150帧（100Hz更新频率）
    self.DEBOUNCE_FRAMES = 5  # 防抖阈值：允许5帧（50ms）的信号中断

    # 刹车压力循环计数器处理
    self.brake_counter_last = None
    self.brake_accumulated = 0.0

    self.ti_ramp_down = False
    self.ti_version = 1
    self.ti_state = TI_STATE.RUN
    self.ti_violation = 0
    self.ti_error = 0
    self.ti_lkas_allowed = False

    # 用于记录状态变化的变量
    self.gear_last = None
    self.brake_pressed_last = False
    self.gas_pressed_last = False

    # TPMS message 0x728 (DBC id 1832). The message is multiplexed by the
    # type bytes: 05-08 are pressure and 0A-0D are temperature.
    self.tpms_pressure_bar = [None, None, None, None]  # FL, FR, RL, RR
    self.tpms_temperature_c = [None, None, None, None]  # FL, FR, RL, RR

  def update(self, cp, cp_cam, cp_body):
    ret = car.CarState.new_message()

    # Decode the raw Mazda TPMS frame. Do not require this frame in the CAN
    # checks because some vehicles only transmit it after wake-up or when a
    # sensor report changes.
    tpms = cp.vl.get("MAZDA_TPMS", {})
    for type_key, value_key in (("TPMS_Type_L", "TPMS_Value_L"),
                                ("TPMS_Type_R", "TPMS_Value_R")):
      try:
        signal_type = int(tpms.get(type_key, 0))
        raw_value = int(tpms.get(value_key, 0))
      except (TypeError, ValueError):
        continue
      if 5 <= signal_type <= 8:
        pressure_bar = (raw_value - 55) * 0.02
        # 0x00/-1.10 bar is the Mazda TPMS no-sensor/invalid value.
        # Require a physically plausible value before exposing it.
        self.tpms_pressure_bar[signal_type - 5] = round(pressure_bar, 2) if 0.3 <= pressure_bar <= 4.0 else None
      elif 10 <= signal_type <= 13:
        temperature_c = raw_value - 50
        # -50 C (raw 0) is used when the wheel sensor has no valid report.
        self.tpms_temperature_c[signal_type - 10] = temperature_c if -40 <= temperature_c <= 120 else None

    # TPMS reports arrive in pairs and different wheels may announce at
    # different times. Publish any valid wheel immediately; NaN is converted
    # to an unavailable value by the state reporter, so it never appears as 0.
    ret.tirePressureValid = any(value is not None for value in self.tpms_pressure_bar)
    if ret.tirePressureValid:
      values = [value if value is not None else float('nan') for value in self.tpms_pressure_bar]
      ret.tirePressureFlBar, ret.tirePressureFrBar, ret.tirePressureRlBar, ret.tirePressureRrBar = values
    ret.tireTemperatureValid = any(value is not None for value in self.tpms_temperature_c)
    if ret.tireTemperatureValid:
      values = [value if value is not None else float('nan') for value in self.tpms_temperature_c]
      ret.tireTemperatureFlC, ret.tireTemperatureFrC, ret.tireTemperatureRlC, ret.tireTemperatureRrC = values

    ret.wheelSpeeds = self.get_wheel_speeds(
      cp.vl["WHEEL_SPEEDS"]["FL"],
      cp.vl["WHEEL_SPEEDS"]["FR"],
      cp.vl["WHEEL_SPEEDS"]["RL"],
      cp.vl["WHEEL_SPEEDS"]["RR"],
    )
    ret.vEgoRaw = (ret.wheelSpeeds.fl + ret.wheelSpeeds.fr + ret.wheelSpeeds.rl + ret.wheelSpeeds.rr) / 4.
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    
    # 应用速度校正因子
    ret.vEgo *= SPEED_CORRECTION_FACTOR
    ret.vEgoRaw *= SPEED_CORRECTION_FACTOR

    # 匹配panda速度读数
    speed_kph = cp.vl["ENGINE_DATA"]["SPEED"]
    ret.standstill = speed_kph < .1

    # Engine speed is the reliable runtime signal available on the Mazda
    # powertrain CAN bus. Keep it separate from canValid and ignition state.
    engine_rpm = cp.vl["ENGINE_DATA"]["RPM"]
    ret.engineRpm = max(0.0, float(engine_rpm))
    # CANParser retains the last decoded value when a message stops arriving.
    # Do not let a pre-shutdown RPM keep the engine marked as running after
    # ignition-off: valid means ENGINE_DATA was actually received this cycle.
    ret.engineRpmValid = bool(cp.vl_all.get("ENGINE_DATA", {}))

    can_gear = int(cp.vl["GEAR"]["GEAR"])
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(can_gear, None))
    ret.driveActive = ret.gearShifter in (car.CarState.GearShifter.drive,
                                           car.CarState.GearShifter.reverse) and engine_rpm >= 300.0

    # 记录档位变化
    if ret.gearShifter != self.gear_last:
      cloudlog.info(f"[Mode][carstate] Gear changed: {str(self.gear_last)} -> {str(ret.gearShifter)}")
      self.gear_last = ret.gearShifter

    # Mazda BLINK_INFO contains the actual exterior-light state.  Keep the
    # legacy genericToggle value for compatibility, while publishing explicit
    # fields for the local vehicle-state reporter.
    low_beams = cp.vl["BLINK_INFO"]["LOW_BEAMS"] != 0
    high_beams = cp.vl["BLINK_INFO"]["HIGH_BEAMS"] != 0
    ret.genericToggle = high_beams
    ret.lowBeamsOn = low_beams
    ret.highBeamsOn = high_beams
    ret.headlightsOn = low_beams or high_beams

    # 使用新的BSM消息（CAN ID 0x47b = 1147）
    # BSM_LEFT/BSM_RIGHT: 盲区有车的基本信号
    # BSM_LEFT_Light/BSM_RIGHT_Light: 盲区有车且打了转向灯时的报警信号
    # 工作逻辑：正常检测到有车时BSM_LEFT/RIGHT=1，打转向灯后信号切换为BSM_LEFT/RIGHT_Light=1

    # 读取原始BSM信号
    bsm_msg = cp.vl.get("NEW_BSM_47B", {})
    bsm_left = bsm_msg.get("BSM_LEFT", 0) == 1
    bsm_left_light = bsm_msg.get("BSM_LEFT_Light", 0) == 1
    bsm_right = bsm_msg.get("BSM_RIGHT", 0) == 1
    bsm_right_light = bsm_msg.get("BSM_RIGHT_Light", 0) == 1

    # 初始化状态变量（用于检测信号变化）
    if not hasattr(self, 'bsm_left_prev'):
      self.bsm_left_prev = False
      self.bsm_left_light_prev = False
      self.bsm_right_prev = False
      self.bsm_right_light_prev = False

    # 调试日志：记录BSM信号变化（使用WARNING级别确保被记录）
    if bsm_left != self.bsm_left_prev or bsm_left_light != self.bsm_left_light_prev:
      cloudlog.warning(f"[BSM] Left: BSM_LEFT={bsm_left} (was {self.bsm_left_prev}), BSM_LEFT_Light={bsm_left_light} (was {self.bsm_left_light_prev})")
    if bsm_right != self.bsm_right_prev or bsm_right_light != self.bsm_right_light_prev:
      cloudlog.warning(f"[BSM] Right: BSM_RIGHT={bsm_right} (was {self.bsm_right_prev}), BSM_RIGHT_Light={bsm_right_light} (was {self.bsm_right_light_prev})")

    # 保存当前状态供下次比较
    self.bsm_left_prev = bsm_left
    self.bsm_left_light_prev = bsm_left_light
    self.bsm_right_prev = bsm_right
    self.bsm_right_light_prev = bsm_right_light

    # 合并信号：只要任一信号为true，就判断盲点有车
    ret.leftBlindspot = bsm_left or bsm_left_light
    ret.rightBlindspot = bsm_right or bsm_right_light
    # 直接使用原始CAN信号，不经过延长处理，以便UI层能检测到每次闪烁
    ret.leftBlinker = cp.vl["BLINK_INFO"]["LEFT_BLINK"] == 1
    ret.rightBlinker = cp.vl["BLINK_INFO"]["RIGHT_BLINK"] == 1

    # 处理巡航控制按钮事件
    # 读取按钮状态
    button_events = []  # 使用临时Python列表收集按钮事件

    try:
      button_cancel = cp.vl["CRZ_BTNS"]["CAN_OFF"] == 1
      button_resume = cp.vl["CRZ_BTNS"]["RES"] == 1
      button_set_plus = cp.vl["CRZ_BTNS"]["SET_P"] == 1
      button_set_minus = cp.vl["CRZ_BTNS"]["SET_M"] == 1

      # 输出按键状态变化（用于调试）
      if button_resume != self.button_resume_prev:
        cloudlog.info(f"[Mode][carstate] RES button state changed: {self.button_resume_prev} -> {button_resume}")

      # CANCEL按键边沿检测
      if button_cancel and not self.button_cancel_prev:
        cloudlog.info(f"[Mode][carstate] Button pressed: CANCEL")
        button_events.append(car.CarState.ButtonEvent.new_message(type=ButtonType.cancel, pressed=True))

      # RES按键长按检测逻辑（用于激活仅横向模式）
      if button_resume:
        # 按键按下状态
        self.button_resume_release_frames = 0  # 清除释放计数器

        if not self.button_resume_prev:
          # 按键刚按下（可能是首次按下，也可能是防抖后恢复）
          if self.button_resume_press_start_time == 0:
            # 首次按下，开始计时
            self.button_resume_press_start_time = 1
            self.button_resume_long_press_triggered = False
            cloudlog.info("[Mode][carstate] RES button pressed, starting long-press timer")
          else:
            # 防抖期间恢复按下，继续计时
            cloudlog.info(f"[Mode][carstate] RES button resumed after brief release (debounce), continuing timer at {self.button_resume_press_start_time} frames")
        else:
          # 按键持续按下，递增计时
          self.button_resume_press_start_time += 1

          # 每100帧（约1秒）输出一次进度
          if self.button_resume_press_start_time % 100 == 0:
            cloudlog.info(f"[Mode][carstate] RES button held: {self.button_resume_press_start_time} frames ({self.button_resume_press_start_time/100:.1f}s)")

          # 检查是否达到长按阈值（1.5秒）
          if (self.button_resume_press_start_time >= self.LONG_PRESS_FRAMES and
              not self.button_resume_long_press_triggered):
            # 长按1.5秒触发
            self.button_resume_long_press_triggered = True

            # 检查前置条件
            acc_enabled = ret.cruiseState.enabled
            in_park = (ret.gearShifter == car.CarState.GearShifter.park)

            cloudlog.info(f"[Mode][carstate] RES long press threshold reached! acc_enabled={acc_enabled}, in_park={in_park}")

            if not acc_enabled and not in_park:
              # 激活仅横向模式
              from common.params import Params
              Params().put_bool("LateralOnlyActive", True)
              cloudlog.info("[Mode][carstate] RES long press (1.5s): Lateral-only mode ACTIVATED")
            else:
              reason = "ACC already enabled" if acc_enabled else "In Park gear"
              cloudlog.warning(f"[Mode][carstate] RES long press: Cannot activate lateral-only mode ({reason})")
      else:
        # 按键释放状态
        if self.button_resume_press_start_time > 0:
          # 正在计时中，开始防抖计数
          self.button_resume_release_frames += 1

          if self.button_resume_release_frames == 1:
            cloudlog.debug(f"[Mode][carstate] RES button released, starting debounce (timer at {self.button_resume_press_start_time} frames)")

          # 检查是否超过防抖阈值
          if self.button_resume_release_frames >= self.DEBOUNCE_FRAMES:
            # 确认真正释放（连续5帧都是False）
            if (self.button_resume_press_start_time < self.LONG_PRESS_FRAMES and
                not self.button_resume_long_press_triggered):
              # 短按：发送原车RES事件
              cloudlog.info(f"[Mode][carstate] Button pressed: RESUME (short press, {self.button_resume_press_start_time} frames)")
              button_events.append(car.CarState.ButtonEvent.new_message(type=ButtonType.accelCruise, pressed=True))
            elif self.button_resume_long_press_triggered:
              cloudlog.info(f"[Mode][carstate] RES released after long press")

            # 重置所有状态
            self.button_resume_press_start_time = 0
            self.button_resume_long_press_triggered = False
            self.button_resume_release_frames = 0

      # SET+按键边沿检测
      if button_set_plus and not self.button_set_plus_prev:
        cloudlog.info(f"[Mode][carstate] Button pressed: SET_PLUS")
        button_events.append(car.CarState.ButtonEvent.new_message(type=ButtonType.accelCruise, pressed=True))

      # SET-按键边沿检测
      if button_set_minus and not self.button_set_minus_prev:
        cloudlog.info(f"[Mode][carstate] Button pressed: SET_MINUS")
        button_events.append(car.CarState.ButtonEvent.new_message(type=ButtonType.decelCruise, pressed=True))

      # 保存当前状态供下次比较
      self.button_cancel_prev = button_cancel
      self.button_resume_prev = button_resume
      self.button_set_plus_prev = button_set_plus
      self.button_set_minus_prev = button_set_minus
    except Exception as e:
      # 如果读取按键失败，记录错误
      cloudlog.warning(f"[Mode][carstate][ERROR] Button processing failed: {e}")
      # 确保状态变量被更新，避免下次误判
      self.button_cancel_prev = False
      self.button_resume_prev = False
      self.button_set_plus_prev = False
      self.button_set_minus_prev = False
      button_events = []

    # 一次性赋值给Cap'n Proto对象，避免对列表构建器调用append
    ret.buttonEvents = button_events

    if self.CP.enableTorqueInterceptor:
      ret.steeringTorque = cp_body.vl["TI_FEEDBACK"]["TI_TORQUE_SENSOR"]

      self.ti_version = cp_body.vl["TI_FEEDBACK"]["VERSION_NUMBER"]
      self.ti_state = cp_body.vl["TI_FEEDBACK"]["STATE"] # 发现 = 0, 关闭 = 1, 驾驶员接管 = 2, 运行=3
      self.ti_violation = cp_body.vl["TI_FEEDBACK"]["VIOL"] # 0 = 无违规
      self.ti_error = cp_body.vl["TI_FEEDBACK"]["ERROR"] # 0 = 无错误
      if self.ti_version > 1:
        self.ti_ramp_down = (cp_body.vl["TI_FEEDBACK"]["RAMP_DOWN"] == 1)

      ret.steeringPressed = abs(ret.steeringTorque) > LKAS_LIMITS.TI_STEER_THRESHOLD
      self.ti_lkas_allowed = not self.ti_ramp_down and self.ti_state == TI_STATE.RUN
    else:
      ret.steeringTorque = cp.vl["STEER_TORQUE"]["STEER_TORQUE_SENSOR"]
      ret.steeringPressed = abs(ret.steeringTorque) > LKAS_LIMITS.STEER_THRESHOLD

    ret.steeringAngleDeg = cp.vl["STEER"]["STEER_ANGLE"]      

    ret.steeringTorqueEps = cp.vl["STEER_TORQUE"]["STEER_TORQUE_MOTOR"]
    ret.steeringRateDeg = cp.vl["STEER_RATE"]["STEER_ANGLE_RATE"]

    # TODO: 这应该是从 0 - 1.
    ret.brakePressed = cp.vl["PEDALS"]["BRAKE_ON"] == 1

    # 处理循环刹车压力计数器（0-255回绕）
    raw_brake_counter = cp.vl["BRAKE"]["BRAKE_PRESSURE"]

    if self.brake_counter_last is None:
      # 首次读取，初始化
      self.brake_counter_last = raw_brake_counter
      self.brake_accumulated = 0.0
    else:
      # 计算增量，处理255->0的回绕
      delta = raw_brake_counter - self.brake_counter_last
      if delta < -200:  # 检测到正向回绕（255 -> 0）
        delta += 256
      elif delta > 200:  # 检测到反向回绕（0 -> 255，理论上不应该发生）
        delta -= 256

      # 累积增量
      if ret.brakePressed:
        # 刹车按下时累积
        self.brake_accumulated += delta
        # 限制累积值在合理范围内（防止无限增长）
        # 最大值：152→253(101) + 0→203(203) = 304
        self.brake_accumulated = max(0.0, min(self.brake_accumulated, 304.0))
      else:
        # 刹车释放时逐渐衰减到0
        self.brake_accumulated = max(0.0, self.brake_accumulated - 5.0)

      self.brake_counter_last = raw_brake_counter

    # 归一化到0-1范围（实测最大累积值：152→253(101) + 0→203(203) = 304）
    ret.brake = max(0.0, min(1.0, self.brake_accumulated / 304.0))

    # 记录刹车状态变化（只记录按下和释放的转换）
    if ret.brakePressed != self.brake_pressed_last:
      cloudlog.debug(f"[Mode][carstate] Brake pressed changed: {self.brake_pressed_last} -> {ret.brakePressed}, "
                     f"pressure={ret.brake:.2f}, counter={raw_brake_counter}, accumulated={self.brake_accumulated:.1f}")
      self.brake_pressed_last = ret.brakePressed

    # 调试信息已注释，避免日志过多
    # 当归一化值异常低时，记录原始值（用于调试）
    # if ret.brake < 0.05 and raw_brake_pressure > 0:
    #   cloudlog.warning(f"[Mode][carstate][BRAKE_DEBUG] Low normalized brake: raw={raw_brake_pressure}, normalized={ret.brake:.3f}")
    # 当归一化值较高时，也记录原始值（用于确认正常工作）
    # elif ret.brake > 0.8:
    #   cloudlog.info(f"[Mode][carstate][BRAKE_DEBUG] High brake pressure: raw={raw_brake_pressure}, normalized={ret.brake:.3f}")


    # ABS/刹车系统警告
    ret.brakeWarning = cp.vl["TRACTION"]["BRAKE_WARNING"] == 1

    ret.seatbeltUnlatched = cp.vl["SEATBELT"]["DRIVER_SEATBELT"] == 0
    ret.doorOpen = any([cp.vl["DOORS"]["FL"], cp.vl["DOORS"]["FR"],
                        cp.vl["DOORS"]["BL"], cp.vl["DOORS"]["BR"]])

    # 油门踏板归一化到0-1范围（实测最大值为4000）
    raw_gas = cp.vl["ENGINE_DATA"]["PEDAL_GAS"]
    ret.gas = max(0.0, min(1.0, raw_gas / 4000.0))
    ret.gasPressed = ret.gas > 0.05  # 使用小阈值避免噪声

    # 记录油门状态变化（只记录按下和释放的转换）
    if ret.gasPressed != self.gas_pressed_last:
      cloudlog.debug(f"[Mode][carstate] Gas pressed changed: {self.gas_pressed_last} -> {ret.gasPressed}, gas={ret.gas:.2f}")
      self.gas_pressed_last = ret.gasPressed

    # 由于低速或手离开方向盘
    lkas_blocked = cp.vl["STEER_RATE"]["LKAS_BLOCK"] == 1

    if self.CP.minSteerSpeed > 0:
      # LKAS 在52km/h时启动，在45km/h时关闭
      # 当加速时等待LKAS_BLOCK信号清除，因为它有时滞后于速度
      if speed_kph > LKAS_LIMITS.ENABLE_SPEED and not lkas_blocked:
        self.lkas_allowed_speed = True
      elif speed_kph < LKAS_LIMITS.DISABLE_SPEED:
        self.lkas_allowed_speed = False

    # TODO: 可用信号似乎自适应巡航信号，而不是主开关
    # 应该用于 carState.cruiseState.nonAdaptive
    ret.cruiseState.available = cp.vl["CRZ_CTRL"]["CRZ_AVAILABLE"] == 1
    ret.cruiseState.enabled = cp.vl["CRZ_CTRL"]["CRZ_ACTIVE"] == 1
    ret.cruiseState.speed = cp.vl["CRZ_EVENTS"]["CRZ_SPEED"] * CV.KPH_TO_MS

    # 记录ACC状态变化
    if ret.cruiseState.enabled != self.acc_active_last:
      cloudlog.info(f"[Mode][carstate] ACC enabled changed: {self.acc_active_last} -> {ret.cruiseState.enabled}, speed={ret.cruiseState.speed*CV.MS_TO_KPH:.1f}km/h")
      self.acc_active_last = ret.cruiseState.enabled

    # 记录ACC available状态变化
    if not hasattr(self, 'acc_available_last'):
      self.acc_available_last = False
    if ret.cruiseState.available != self.acc_available_last:
      cloudlog.info(f"[Mode][carstate] ACC available changed: {self.acc_available_last} -> {ret.cruiseState.available}")
      self.acc_available_last = ret.cruiseState.available

    # 如果过去5秒内没有驾驶员扭矩
    if self.CP.carFingerprint not in (CAR.CX5_2022, CAR.CX9_2021): 
      ret.steerWarning = cp.vl["STEER_RATE"]["HANDS_OFF_5_SECONDS"] == 1
    else:
      ret.steerWarning = False

    self.acc_active_last = ret.cruiseState.enabled

    # 读取CRZ_BTNS计数器（用于发送按键命令）
    # 使用try-except保护，避免消息不可用时崩溃
    try:
      self.crz_btns_counter = cp.vl["CRZ_BTNS"]["CTR"]
    except Exception as e:
      # 如果读取失败（例如ACC关闭时消息不可用），记录错误并保持上一次的计数器值
      cloudlog.warning(f"[Mode][carstate][ERROR] CTR read failed: {e}")
      pass

    # 摄像头信号
    lane_lines = cp_cam.vl["CAM_LANEINFO"]["LANE_LINES"]
    err_bit_1 = cp_cam.vl["CAM_LKAS"]["ERR_BIT_1"]

    # 记录原车摄像头状态变化（只在状态变化时记录一次）
    if lane_lines == 0 and not hasattr(self, 'lane_lines_zero_logged'):
      self.lane_lines_zero_logged = True
      cloudlog.warning(f"[LKAS][Camera] Original camera lost lane lines: LANE_LINES=0")
    elif lane_lines != 0 and hasattr(self, 'lane_lines_zero_logged'):
      delattr(self, 'lane_lines_zero_logged')
      cloudlog.info(f"[LKAS][Camera] Original camera recovered lane lines: LANE_LINES={lane_lines}")

    if err_bit_1 == 1 and not hasattr(self, 'err_bit_logged'):
      self.err_bit_logged = True
      cloudlog.error(f"[LKAS][Camera] Original camera error detected: ERR_BIT_1=1, LANE_LINES={lane_lines}")
    elif err_bit_1 == 0 and hasattr(self, 'err_bit_logged'):
      delattr(self, 'err_bit_logged')
      cloudlog.info(f"[LKAS][Camera] Original camera error cleared: ERR_BIT_1=0")

    self.lkas_disabled = lane_lines == 0

    self.cam_lkas = cp_cam.vl["CAM_LKAS"]
    self.cam_laneinfo = cp_cam.vl["CAM_LANEINFO"]
    self.steer_rate = cp.vl["STEER_RATE"]

    # 保存原车摄像头的LDW信号（供interface.py触发openpilot警告使用）
    # LDW_WARN: 0=无警告, 4=左侧偏离, 5=右侧偏离
    ldw_warn = self.cam_laneinfo.get("LDW_WARN", 0)
    self.cam_ldw_left = (ldw_warn == 4)
    self.cam_ldw_right = (ldw_warn == 5)

    # 保留 ERR_BIT_1 检查，因为 openpilot 会复制这个错误位到发送的 CAN 消息中
    # 如果原车摄像头硬件故障，车辆 ECU 可能会拒绝带有错误位的转向命令
    ret.steerError = err_bit_1 == 1

    # 当LKAS状态或转向错误状态变化时，输出详细状态（只在变化时记录）
    current_lkas_state = (self.lkas_disabled, ret.steerError)
    if not hasattr(self, 'last_lkas_state'):
      self.last_lkas_state = (False, False)

    if current_lkas_state != self.last_lkas_state and (self.lkas_disabled or ret.steerError):
      cloudlog.warning(f"[LKAS][Status] lkas_disabled={self.lkas_disabled}, steerError={ret.steerError}, "
                      f"LANE_LINES={lane_lines}, ERR_BIT_1={err_bit_1}, "
                      f"vEgo={ret.vEgo:.1f}m/s, gear={ret.gearShifter}")
      self.last_lkas_state = current_lkas_state

    return ret

  @staticmethod
  def get_can_parser(CP):
    signals = [
      # 信号名, 信号地址
      ("LEFT_BLINK", "BLINK_INFO"),
      ("RIGHT_BLINK", "BLINK_INFO"),
      ("LOW_BEAMS", "BLINK_INFO"),
      ("HIGH_BEAMS", "BLINK_INFO"),
      ("STEER_ANGLE", "STEER"),
      ("STEER_ANGLE_RATE", "STEER_RATE"),
      ("STEER_TORQUE_SENSOR", "STEER_TORQUE"),
      ("STEER_TORQUE_MOTOR", "STEER_TORQUE"),
      ("FL", "WHEEL_SPEEDS"),
      ("FR", "WHEEL_SPEEDS"),
      ("RL", "WHEEL_SPEEDS"),
      ("RR", "WHEEL_SPEEDS"),
      ("TPMS_Type_L", "MAZDA_TPMS"),
      ("TPMS_Value_L", "MAZDA_TPMS"),
      ("TPMS_Type_R", "MAZDA_TPMS"),
      ("TPMS_Value_R", "MAZDA_TPMS"),
    ]
    checks = [
      # 信号地址, 频率
      ("BLINK_INFO", 10),
      ("STEER", 67),
      ("STEER_RATE", 83),
      ("STEER_TORQUE", 83),
      ("WHEEL_SPEEDS", 100),
      # TPMS is intentionally optional and may only be transmitted after a
      # wake-up, sensor change, or periodic sensor announcement.
      ("MAZDA_TPMS", 0),
    ]
    if CP.carFingerprint in GEN1:
      signals += [
        ("LKAS_BLOCK", "STEER_RATE"),
        ("LKAS_TRACK_STATE", "STEER_RATE"),
        ("HANDS_OFF_5_SECONDS", "STEER_RATE"),
        ("CTR", "STEER_RATE"),
        ("CHKSUM", "STEER_RATE"),
        ("LKAS_REQUEST", "STEER_RATE"),
        ("LKAS_EFFECTIVE", "STEER_RATE"),
        ("CRZ_ACTIVE", "CRZ_CTRL"),
        ("CRZ_AVAILABLE", "CRZ_CTRL"),
        ("CRZ_SPEED", "CRZ_EVENTS"),
        ("STANDSTILL", "PEDALS"),
        ("BRAKE_ON", "PEDALS"),
        ("BRAKE_PRESSURE", "BRAKE"),
        ("GEAR", "GEAR"),
        ("DRIVER_SEATBELT", "SEATBELT"),
        ("FL", "DOORS"),
        ("FR", "DOORS"),
        ("BL", "DOORS"),
        ("BR", "DOORS"),
        ("PEDAL_GAS", "ENGINE_DATA"),
        ("SPEED", "ENGINE_DATA"),
        ("RPM", "ENGINE_DATA"),
        ("START_STOP", "MSG_04"),
        # CRZ_BTNS信号：在signals列表中声明以便解析器解析此消息
        # 但不在checks列表中声明，避免强制频率检查导致ACC关闭时触发安全模式
        # 解析器会在消息可用时解析，不可用时返回默认值（不抛出异常）
        ("CTR", "CRZ_BTNS"),
        ("CAN_OFF", "CRZ_BTNS"),
        ("RES", "CRZ_BTNS"),
        ("SET_P", "CRZ_BTNS"),
        ("SET_M", "CRZ_BTNS"),
        ("BSM_LEFT", "NEW_BSM_47B"),
        ("BSM_RIGHT", "NEW_BSM_47B"),
        ("BSM_LEFT_Light", "NEW_BSM_47B"),  
        ("BSM_RIGHT_Light", "NEW_BSM_47B"),   
        ("BRAKE_WARNING", "TRACTION"),
      ]

      checks += [
        ("ENGINE_DATA", 100),
        ("MSG_04", 50),
        ("CRZ_CTRL", 50),
        ("CRZ_EVENTS", 50),
        # CRZ_BTNS设置为0频率：允许消息缺失，但如果存在则会解析
        # 这样ACC关闭时消息缺失不会触发安全模式，但ACC开启时可以正常读取按键
        ("CRZ_BTNS", 0),  # 0 = 不强制检查频率，但必须在checks列表中
        ("PEDALS", 50),
        ("BRAKE", 50),
        ("SEATBELT", 10),
        ("DOORS", 10),
        ("GEAR", 20),
        ("NEW_BSM_47B", 10),
        ("TRACTION", 50),
      ]
    # 如果使用扭矩传感器获取实际驾驶员扭矩
    if CP.enableTorqueInterceptor:
      signals += [
        ("TI_TORQUE_SENSOR", "TI_FEEDBACK", 0),
        ("CHKSUM", "TI_FEEDBACK", 0),
        ("VERSION_NUMBER", "TI_FEEDBACK", 0),
        ("STATE", "TI_FEEDBACK", 0),
        ("VIOL", "TI_FEEDBACK", 0),
        ("ERROR", "TI_FEEDBACK", 0),
        ("RAMP_DOWN", "TI_FEEDBACK", 0),
      ]

      checks += [
        ("TI_FEEDBACK", 100),
      ]
      
    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 0)

  @staticmethod
  def get_cam_can_parser(CP):
    signals = []
    checks = []

    if CP.carFingerprint in GEN1:
      signals += [
        # 信号名, 信号地址
        ("LKAS_REQUEST", "CAM_LKAS"),
        ("CTR", "CAM_LKAS"),
        ("ERR_BIT_1", "CAM_LKAS"),
        ("LINE_NOT_VISIBLE", "CAM_LKAS"),
        ("LDW", "CAM_LKAS"),  # LDW信号：用于触发方向盘震动
        ("BIT_1", "CAM_LKAS"),
        ("ERR_BIT_2", "CAM_LKAS"),
        ("STEERING_ANGLE", "CAM_LKAS"),
        ("ANGLE_ENABLED", "CAM_LKAS"),
        ("CHKSUM", "CAM_LKAS"),

        ("LINE_VISIBLE", "CAM_LANEINFO"),
        ("LINE_NOT_VISIBLE", "CAM_LANEINFO"),
        ("LANE_LINES", "CAM_LANEINFO"),
        ("BIT1", "CAM_LANEINFO"),
        ("BIT2", "CAM_LANEINFO"),
        ("BIT3", "CAM_LANEINFO"),
        ("NO_ERR_BIT", "CAM_LANEINFO"),
        ("S1", "CAM_LANEINFO"),
        ("S1_HBEAM", "CAM_LANEINFO"),
        # LDW 车道偏离警告信号 - 必须声明才能被 CAN 解析器解析
        # LDW_WARN: 0=无警告, 1=左侧偏离, 3=右侧偏离
        ("LDW_WARN", "CAM_LANEINFO"),
      ]

      checks += [
        # 信号地址, 频率
        ("CAM_LANEINFO", 2),
        ("CAM_LKAS", 16),
      ]

    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 2)


  @staticmethod
  def get_body_can_parser(CP):
    # 此函数生成信号、消息和初始值的列表
    signals = []
    checks = []
    # 如果使用扭矩传感器获取实际驾驶员扭矩
    if CP.enableTorqueInterceptor:
      signals += [
        ("TI_TORQUE_SENSOR", "TI_FEEDBACK", 0),
        ("CHKSUM", "TI_FEEDBACK", 0),
        ("VERSION_NUMBER", "TI_FEEDBACK", 0),
        ("STATE", "TI_FEEDBACK", 0),
        ("VIOL", "TI_FEEDBACK", 0),
        ("ERROR", "TI_FEEDBACK", 0),
        ("RAMP_DOWN", "TI_FEEDBACK", 0),
      ]

      checks += [
        ("TI_FEEDBACK", 50),
      ]
      
    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 1) # 改回0因为我的OBD2端口可以工作
