// Lateral-only mode configuration (set via safety param at initialization)
// 用户设置：是否允许在ACC关闭时进行转向控制
// - true: 允许（用户启用了LateralOnlyControl设置）
// - false: 不允许（只能在ACC激活时转向）
static bool lateral_only_allowed = false;

// CAN msgs we care about
#define MAZDA_LKAS          0x243
#define MAZDA_LKAS2         0x249
#define MAZDA_LKAS_HUD      0x440
#define MAZDA_CRZ_CTRL      0x21c
#define MAZDA_CRZ_BTNS      0x09d
#define MAZDA_ISTOP_DISABLE 0x9e
#define TI_STEER_TORQUE     0x24A
#define MAZDA_STEER_TORQUE  0x240
#define MAZDA_STEER_RATE    0x241
#define MAZDA_ENGINE_DATA   0x202
#define MAZDA_PEDALS        0x165

// CAN bus numbers
#define MAZDA_MAIN 0
#define MAZDA_AUX  1
#define MAZDA_CAM  2

#define MAZDA_MAX_STEER 2048U

// max delta torque allowed for real time checks
#define MAZDA_MAX_RT_DELTA 940
// 250ms between real time checks
#define MAZDA_RT_INTERVAL 250000
#define MAZDA_MAX_RATE_UP 10
#define MAZDA_MAX_RATE_DOWN 25
#define MAZDA_DRIVER_TORQUE_ALLOWANCE 15
#define MAZDA_DRIVER_TORQUE_FACTOR 1
#define MAZDA_MAX_TORQUE_ERROR 350

const CanMsg MAZDA_TX_MSGS[] = {
  {MAZDA_LKAS, 0, 8},
  {MAZDA_CRZ_BTNS, 0, 8},
  {MAZDA_LKAS2, 1, 8},
  {MAZDA_LKAS_HUD, 0, 8},
  {MAZDA_ISTOP_DISABLE, 0, 8},  // I-STOP关闭命令
  {MAZDA_STEER_RATE, 2, 8}      // STEER_RATE (0x241) 发送到总线2（摄像头）
};

AddrCheckStruct mazda_addr_checks[] = {
  {.msg = {{MAZDA_CRZ_CTRL,     0, 8, .expected_timestep = 20000U}, { 0 }, { 0 }}},
  {.msg = {{MAZDA_CRZ_BTNS,     0, 8, .expected_timestep = 100000U}, { 0 }, { 0 }}},
  {.msg = {{MAZDA_STEER_TORQUE, 0, 8, .expected_timestep = 12000U}, { 0 }, { 0 }}},
  {.msg = {{MAZDA_ENGINE_DATA,  0, 8, .expected_timestep = 10000U}, { 0 }, { 0 }}},
  {.msg = {{MAZDA_PEDALS,       0, 8, .expected_timestep = 20000U}, { 0 }, { 0 }}},
};
#define MAZDA_ADDR_CHECKS_LEN (sizeof(mazda_addr_checks) / sizeof(mazda_addr_checks[0]))
addr_checks mazda_rx_checks = {mazda_addr_checks, MAZDA_ADDR_CHECKS_LEN};

AddrCheckStruct mazda_ti_addr_checks[] = {
  {.msg = {{TI_STEER_TORQUE,    1, 8, .expected_timestep = 22000U}}},
  // TI_STEER_TORQUE expected_timestep should be the same as the tx rate of MAZDA_LKAS2
};
#define MAZDA_TI_ADDR_CHECKS_LEN (sizeof(mazda_ti_addr_checks) / sizeof(mazda_ti_addr_checks[0]))
addr_checks mazda_ti_rx_checks = {mazda_ti_addr_checks, MAZDA_TI_ADDR_CHECKS_LEN};

// track msgs coming from OP so that we know what CAM msgs to drop and what to forward
static int mazda_rx_hook(CANPacket_t *to_push) {
  bool valid = addr_safety_check(to_push, &mazda_rx_checks, NULL, NULL, NULL);

  if (((GET_ADDR(to_push) == TI_STEER_TORQUE)) &&
      ((GET_BYTE(to_push, 0) == GET_BYTE(to_push, 1)))) {
    torque_interceptor_detected = 1;
    valid &= addr_safety_check(to_push, &mazda_ti_rx_checks, NULL, NULL, NULL);
  }

  if (valid && (GET_BUS(to_push) == MAZDA_MAIN)) {
    int addr = GET_ADDR(to_push);

    if (addr == MAZDA_ENGINE_DATA) {
      // sample speed: scale by 0.01 to get kph
      int speed = (GET_BYTE(to_push, 2) << 8) | GET_BYTE(to_push, 3);
      vehicle_moving = speed > 10; // moving when speed > 0.1 kph
    }

    if (!torque_interceptor_detected) {
      if (addr == MAZDA_STEER_TORQUE) {
        int torque_driver_new = GET_BYTE(to_push, 0) - 127;
        update_sample(&torque_driver, torque_driver_new);
      }
    }

    // enter controls on rising edge of ACC, exit controls on ACC off
    // MODIFIED: Support lateral-only mode
    if (addr == MAZDA_CRZ_CTRL) {
      bool cruise_engaged = GET_BYTE(to_push, 0) & 0x8U;
      if (cruise_engaged) {
        // ACC激活：允许控制（完整模式）
        if (!cruise_engaged_prev) {
          controls_allowed = 1;
        }
      } else {
        // ACC关闭：根据用户设置决定是否允许控制
        if (lateral_only_allowed) {
          // 用户允许仅横向模式：保持控制允许（软件决定是否输出纵向控制）
          controls_allowed = 1;
        } else {
          // 用户不允许：禁用控制
          controls_allowed = 0;
        }
      }
      cruise_engaged_prev = cruise_engaged;
    }

    if (addr == MAZDA_ENGINE_DATA) {
      gas_pressed = (GET_BYTE(to_push, 4) || (GET_BYTE(to_push, 5) & 0xF0U));
    }

    if (addr == MAZDA_PEDALS) {
      brake_pressed = (GET_BYTE(to_push, 0) & 0x10U);
    }

    // Pedal checks: different behavior based on whether lateral-only is allowed
    if (lateral_only_allowed && !cruise_engaged_prev) {
      // 仅横向模式（ACC未激活）：踏板不会禁用转向控制
      // 驾驶员手动控制速度，踩刹车/油门不应该禁用转向辅助
      gas_pressed_prev = gas_pressed;
      brake_pressed_prev = brake_pressed;

      // Check for stock ECU (same as generic_rx_checks)
      if ((addr == MAZDA_LKAS) && (safety_mode_cnt > RELAY_TRNS_TIMEOUT)) {
        relay_malfunction_set();
      }
    } else {
      // 正常模式（ACC激活）或不允许仅横向模式：使用标准检查
      generic_rx_checks((addr == MAZDA_LKAS));
    }
  }
  
  if (valid && (GET_BUS(to_push) == MAZDA_AUX)) {
    int addr = GET_ADDR(to_push);
    if (addr == TI_STEER_TORQUE) {
      int torque_driver_new = GET_BYTE(to_push, 0) - 126;
      update_sample(&torque_driver, torque_driver_new);
    }
  }

  return valid;
}

static int mazda_tx_hook(CANPacket_t *to_send) {
  int tx = 1;
  int addr = GET_ADDR(to_send);
  int bus = GET_BUS(to_send);

  if (!msg_allowed(to_send, MAZDA_TX_MSGS, sizeof(MAZDA_TX_MSGS)/sizeof(MAZDA_TX_MSGS[0]))) {
    tx = 0;
  }

  // Check if msg is sent on the main BUS
  if (bus == MAZDA_MAIN) {
    // steer cmd checks
    if (addr == MAZDA_LKAS) {
      int desired_torque = (((GET_BYTE(to_send, 0) & 0x0FU) << 8) | GET_BYTE(to_send, 1)) - MAZDA_MAX_STEER;
      bool violation = 0;
      uint32_t ts = microsecond_timer_get();

      if (controls_allowed) {

        // *** global torque limit check ***
        violation |= max_limit_check(desired_torque, MAZDA_MAX_STEER, -MAZDA_MAX_STEER);

        // *** torque rate limit check ***
        violation |= driver_limit_check(desired_torque, desired_torque_last, &torque_driver,
                                        MAZDA_MAX_STEER, MAZDA_MAX_RATE_UP, MAZDA_MAX_RATE_DOWN,
                                        MAZDA_DRIVER_TORQUE_ALLOWANCE, MAZDA_DRIVER_TORQUE_FACTOR);

        // used next time
        desired_torque_last = desired_torque;

        // *** torque real time rate limit check ***
        violation |= rt_rate_limit_check(desired_torque, rt_torque_last, MAZDA_MAX_RT_DELTA);

        // every RT_INTERVAL set the new limits
        uint32_t ts_elapsed = get_ts_elapsed(ts, ts_last);
        if (ts_elapsed > ((uint32_t) MAZDA_RT_INTERVAL)) {
          rt_torque_last = desired_torque;
          ts_last = ts;
        }
      }

      // no torque if controls is not allowed
      if (!controls_allowed && (desired_torque != 0)) {
        violation = 1;
      }

      // reset to 0 if either controls is not allowed or there's a violation
      if (violation || !controls_allowed) {
        desired_torque_last = 0;
        rt_torque_last = 0;
        ts_last = ts;
      }

      if (violation) {
        tx = 0;
      }
    }

    // cruise buttons check
    if (addr == MAZDA_CRZ_BTNS) {
      // allow resume spamming while controls allowed, but
      // only allow cancel while contrls not allowed
      bool cancel_cmd = (GET_BYTE(to_send, 0) == 0x1U);
      if (!controls_allowed && !cancel_cmd) {
        tx = 0;
      }
    }

    // I-STOP disable command check
    if (addr == MAZDA_ISTOP_DISABLE) {
      // 验证数据必须是 D4 00 00 00 00 00 00 00
      bool valid_data = (GET_BYTE(to_send, 0) == 0xD4U) &&
                        (GET_BYTE(to_send, 1) == 0x00U) &&
                        (GET_BYTE(to_send, 2) == 0x00U) &&
                        (GET_BYTE(to_send, 3) == 0x00U) &&
                        (GET_BYTE(to_send, 4) == 0x00U) &&
                        (GET_BYTE(to_send, 5) == 0x00U) &&
                        (GET_BYTE(to_send, 6) == 0x00U) &&
                        (GET_BYTE(to_send, 7) == 0x00U);

      // I-STOP控制不依赖controls_allowed，但必须数据正确
      if (!valid_data) {
        tx = 0;
      }
    }
  }

  return tx;
}

static int mazda_fwd_hook(int bus, CANPacket_t *to_fwd) {
  int bus_fwd = -1;
  int addr = GET_ADDR(to_fwd);

  if (bus == MAZDA_MAIN) {
    // 阻止转发以下消息到总线2，避免与原车摄像头冲突：
    // - CAM_LKAS (0x243): 转向控制命令
    // - CAM_LANEINFO (0x440): 车道信息
    // - STEER_RATE (0x241): 阻止转发，摄像头不需要此消息也能正常工作
    bool block = (addr == MAZDA_LKAS) || (addr == MAZDA_LKAS_HUD) || (addr == MAZDA_STEER_RATE);
    if (!block) {
      bus_fwd = MAZDA_CAM;
    }
  } else if (bus == MAZDA_CAM) {
    // 阻止摄像头的以下消息转发到总线0：
    // - CAM_LKAS (0x243): 原车摄像头的转向命令
    // - CAM_LANEINFO (0x440): 原车摄像头的车道信息
    // - STEER_RATE (0x241): 阻止转发（虽然摄像头不发送此消息）
    // openpilot 直接从总线2读取这些消息
    bool block = (addr == MAZDA_LKAS) || (addr == MAZDA_LKAS_HUD) || (addr == MAZDA_STEER_RATE);
    if (!block) {
      bus_fwd = MAZDA_MAIN;
    }
  } else {
    // don't fwd
  }

  return bus_fwd;
}

static const addr_checks* mazda_init(int16_t param) {
  relay_malfunction_reset();
  torque_interceptor_detected = 0;

  // param bit 0: 是否允许在ACC关闭时进行转向控制
  // 0 = 不允许（只能在ACC激活时转向）
  // 1 = 允许（可以在ACC关闭时转向，即仅横向模式）
  lateral_only_allowed = (param & 1);

  // 初始化 controls_allowed:
  // - 如果允许仅横向模式：初始就允许控制
  // - 如果不允许：需要等待ACC激活
  controls_allowed = lateral_only_allowed;

  return &mazda_rx_checks;
}

const safety_hooks mazda_hooks = {
  .init = mazda_init,
  .rx = mazda_rx_hook,
  .tx = mazda_tx_hook,
  .tx_lin = nooutput_tx_lin_hook,
  .fwd = mazda_fwd_hook,
};
