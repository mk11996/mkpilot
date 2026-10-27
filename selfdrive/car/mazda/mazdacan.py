import copy

from selfdrive.car.mazda.values import GEN1, Buttons, CAR


def create_steering_control(packer, car_fingerprint, frame, apply_steer, lkas, ldw_active=False):

  tmp = apply_steer + 2048

  lo = tmp & 0xFF
  hi = tmp >> 8

  # copy values from camera
  b1 = int(lkas["BIT_1"])
  er1 = int(lkas["ERR_BIT_1"])
  lnv = 0
  # LDW信号：当有车道偏离警告时设置为1，用于触发方向盘震动
  ldw = 1 if ldw_active else 0
  er2 = int(lkas["ERR_BIT_2"])

  steering_angle = 0
  b2 = 0

  tmp = steering_angle + 2048
  ahi = tmp >> 10
  amd = (tmp & 0x3FF) >> 2
  amd = (amd >> 4) | (( amd & 0xF) << 4)
  alo = (tmp & 0x3) << 2

  ctr = frame % 16
  # bytes:     [    1  ] [ 2 ] [             3               ]  [           4         ]
  csum = 249 - ctr - hi - lo - (lnv << 3) - er1 - (ldw << 7) - ( er2 << 4) - (b1 << 5)

  # bytes      [ 5 ] [ 6 ] [    7   ]
  csum = csum - ahi - amd - alo - b2

  if ahi == 1:
    csum = csum + 15

  if csum < 0:
    if csum < -256:
      csum = csum + 512
    else:
      csum = csum + 256

  csum = csum % 256

  if car_fingerprint in GEN1:
    values = {
      "LKAS_REQUEST": apply_steer,
      "CTR": ctr,
      "ERR_BIT_1": er1,
      "LINE_NOT_VISIBLE" : lnv,
      "LDW": ldw,
      "BIT_1": b1,
      "ERR_BIT_2": er2,
      "STEERING_ANGLE": steering_angle,
      "ANGLE_ENABLED": b2,
      "CHKSUM": csum
    }

  return packer.make_can_msg("CAM_LKAS", 0, values)

def create_ti_steering_control(packer, car_fingerprint, frame, apply_steer):

  commands = []

  key = 3294744160
  chksum = apply_steer

  if car_fingerprint in GEN1:
    values = {
        "LKAS_REQUEST"     : apply_steer,
        "CHKSUM"           : chksum,
        "KEY"              : key
     }
  # TODO 
  # 1. Add new CAR values for MDARS Mazdas so that we can change the rate of the message. This will take some work.
  # 2. Listen for reply's on both CAN buses if not MDARS version of 
  # Mazda (2021+ or m3 2019+) and warn the user if there is a bad connection
  # but do not cause disengagment

  # Write to both busses for *future* redundancy, but we only check bus 1 for a response in carstate and safey_mazda.h for now.
  # if (frame % 2 == 0):
  #  commands.append(packer.make_can_msg("CAM_LKAS2", 0, values))

  commands.append(packer.make_can_msg("CAM_LKAS2", 1, values))
  return commands


def create_alert_command(packer, cam_msg: dict, ldw: bool, steer_required: bool, ldw_vibration_enabled: bool, op_active: bool):
  values = copy.copy(cam_msg)

  # 车道偏离警告逻辑：
  # 1. 如果openpilot激活，强制禁用LDW（防止干扰方向控制）
  # 2. 如果openpilot未激活且开关打开，转发原车摄像头的LDW信号
  # 3. 如果openpilot未激活且开关关闭，禁用LDW（原车总线不需要openpilot的检测结果）
  # LDW_WARN信号值: 0=无警告, 1=左侧偏离, 3=右侧偏离
  if op_active:
    # openpilot激活时禁用LDW
    ldw_warn = 0
  elif ldw_vibration_enabled:
    # 开关打开时转发原车摄像头的LDW检测结果
    ldw_warn = cam_msg.get("LDW_WARN", 0)
  else:
    # 开关关闭时禁用LDW，原车总线不需要openpilot的LDW检测结果
    ldw_warn = 0

  values.update({
    # TODO: what's the difference between all these? do we need to send all?
    "HANDS_WARN_3_BITS": 0b111 if steer_required else 0,
    "HANDS_ON_STEER_WARN": steer_required,
    "HANDS_ON_STEER_WARN_2": steer_required,

    # 车道偏离警告
    "LDW_WARN": ldw_warn,
  })
  return packer.make_can_msg("CAM_LANEINFO", 0, values)


def create_button_cmd(packer, car_fingerprint, counter, button):

  can = int(button == Buttons.CANCEL)
  res = int(button == Buttons.RESUME)

  if car_fingerprint in GEN1:
    values = {
      "CAN_OFF": can,
      "CAN_OFF_INV": (can + 1) % 2,

      "SET_P": 0,
      "SET_P_INV": 1,

      "RES": res,
      "RES_INV": (res + 1) % 2,

      "SET_M": 0,
      "SET_M_INV": 1,

      "DISTANCE_LESS": 0,
      "DISTANCE_LESS_INV": 1,

      "DISTANCE_MORE": 0,
      "DISTANCE_MORE_INV": 1,

      "MODE_X": 0,
      "MODE_X_INV": 1,

      "MODE_Y": 0,
      "MODE_Y_INV": 1,

      "BIT1": 1,
      "BIT2": 1,
      "BIT3": 1,
      "CTR": (counter + 1) % 16,
    }

    return packer.make_can_msg("CRZ_BTNS", 0, values)


def create_istop_disable_cmd():
  """
  创建I-STOP关闭命令
  CAN ID: 0x9E (158)
  数据: D4 00 00 00 00 00 00 00
  总线: 0 (MAIN)
  """
  # 返回格式：[address, 0, data, bus]
  # 与 make_can_msg() 格式一致
  return [0x9E, 0, b'\xd4\x00\x00\x00\x00\x00\x00\x00', 0]
