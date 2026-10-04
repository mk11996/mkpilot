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


def create_steer_rate_cmd(packer, steer_rate_msg: dict, cam_lkas_msg: dict, ldw_active: bool = False):
  """
  创建 STEER_RATE (0x241) 消息，发送到 Bus 2（摄像头）

  功能：转发原车EPS的STEER_RATE消息到摄像头
        当LDW激活时，使用原车摄像头的LKAS_REQUEST值，确保消息一致性

  参数:
    packer: CANPacker 实例
    steer_rate_msg: 原车 STEER_RATE 消息字典（从总线0的EPS读取）
    cam_lkas_msg: 原车摄像头的 CAM_LKAS 消息字典（从总线2读取）
    ldw_active: LDW是否激活

  返回:
    CAN 消息（发送到 Bus 2，给摄像头）
  """
  # 复制原车EPS的STEER_RATE消息的所有字段
  values = copy.copy(steer_rate_msg)

  # 只在LDW激活时修改LKAS_REQUEST字段
  # 原因：openpilot未激活时发送的CAM_LKAS中LKAS_REQUEST=0（因为TI设置），
  #       导致EPS回显的STEER_RATE中LKAS_REQUEST也是0，
  #       但摄像头期望看到它自己发送的LKAS_REQUEST值（非0）
  if ldw_active:
    cam_lkas_request = cam_lkas_msg.get("LKAS_REQUEST", 0)
    values.update({
      "LKAS_REQUEST": cam_lkas_request,  # 使用原车摄像头的值
    })

  return packer.make_can_msg("STEER_RATE", 2, values)


def create_steer_rate_raw(steer_angle_rate, lkas_block, frame):
  """
  直接构建 STEER_RATE 消息的原始字节（不使用 packer）
  
  前提条件：你需要知道完整的消息结构和校验和算法
  
  参数:
    steer_angle_rate: 转向角速度
    lkas_block: LKAS阻止标志
    frame: 帧计数器
  
  返回:
    [CAN_ID, bus, data_bytes, bus] 格式的消息
  """
  # 示例：假设你知道以下信息（需要根据实际情况调整）
  # Byte 0-1: STEER_ANGLE_RATE (16位，需要确定缩放因子和偏移)
  # Byte 2: LKAS_BLOCK 和其他标志位
  # Byte 3: CTR (计数器)
  # Byte 4-7: 其他字段或填充
  # Byte 7: CHKSUM (校验和)
  
  ctr = frame % 16
  
  # 转换转向角速度为原始值（示例，需要根据实际DBC定义调整）
  # 假设：STEER_ANGLE_RATE = value * 0.1 - 100，范围 -100 到 +100
  raw_rate = int((steer_angle_rate + 100) / 0.1)
  rate_hi = (raw_rate >> 8) & 0xFF
  rate_lo = raw_rate & 0xFF
  
  # 设置 LKAS_BLOCK 标志（假设在 byte 2 的 bit 0）
  flags = 1 if lkas_block else 0
  
  # 计算校验和（示例算法，需要根据实际算法调整）
  # 常见算法：所有字节的异或、求和取模等
  chksum = (rate_hi ^ rate_lo ^ flags ^ ctr) & 0xFF
  
  # 构造完整的 8 字节数据
  data = bytes([
    rate_lo,      # Byte 0: 转向角速度低字节
    rate_hi,      # Byte 1: 转向角速度高字节
    flags,        # Byte 2: 标志位（包含 LKAS_BLOCK）
    ctr,          # Byte 3: 计数器
    0x00,         # Byte 4: 保留/填充
    0x00,         # Byte 5: 保留/填充
    0x00,         # Byte 6: 保留/填充
    chksum        # Byte 7: 校验和
  ])
  
  # 返回格式与 make_can_msg 一致
  return [0x241, 0, data, 0]


def create_steer_rate_0x241(frame, byte2_value, byte3_value):
  """
  根据逆向工程的校验和算法构建 0x241 消息
  
  校验和算法: checksum = (245 - counter - byte2 - byte3) % 256
  
  参数:
    frame: 帧计数器（用于生成递减的计数器）
    byte2_value: Byte2 的值（通常是 0x7F 或 0x80）
    byte3_value: Byte3 的值（变化的数据，可能是转向速率相关）
  
  返回:
    [CAN_ID, bus, data_bytes, bus] 格式的消息
  """
  # 生成递减的计数器（从 11 开始，每次减 1，循环 0-15）
  # 根据你的数据：B8(11), A8(10), 98(9), 88(8), 78(7), 68(6), 58(5), 48(4), 38(3)
  counter = (11 - (frame % 16)) % 16
  
  # Byte0 = 计数器 << 4 | 0x8 (固定低4位为 8)
  byte0 = (counter << 4) | 0x8
  
  # 计算校验和
  checksum = (245 - counter - byte2_value - byte3_value) % 256
  
  # 构造完整的 8 字节数据
  # 根据你的数据模式：
  # Byte0: 计数器（高4位）+ 0x8（低4位）
  # Byte1: 0x00（固定）
  # Byte2: 变化（0x7F 或 0x80）
  # Byte3: 变化（可能是转向速率）
  # Byte4: 0x80（固定）
  # Byte5-6: 0x00（固定）
  # Byte7: 校验和
  data = bytes([
    byte0,              # Byte 0: 计数器
    0x00,               # Byte 1: 固定
    byte2_value,        # Byte 2: 变化值
    byte3_value,        # Byte 3: 变化值（转向速率？）
    0x80,               # Byte 4: 固定
    0x00,               # Byte 5: 固定
    0x00,               # Byte 6: 固定
    checksum            # Byte 7: 校验和
  ])
  
  # 返回格式与 make_can_msg 一致
  return [0x241, 0, data, 0]
