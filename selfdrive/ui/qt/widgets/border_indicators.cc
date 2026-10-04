#include "selfdrive/ui/qt/widgets/border_indicators.h"
#include "selfdrive/common/params.h"
#include "selfdrive/common/swaglog.h"
#include <QDateTime>

// 强制启用DEBUG日志
#undef LOGD
#define LOGD(fmt, ...) LOGW(fmt, ##__VA_ARGS__)

// 颜色定义
const QColor BorderIndicators::COLOR_BLINKER = QColor(0, 255, 0, 200);        // 绿色，半透明
const QColor BorderIndicators::COLOR_BLINDSPOT = QColor(255, 165, 0, 200);    // 橙色，半透明
const QColor BorderIndicators::COLOR_BLINDSPOT_WARN = QColor(255, 0, 0, 200); // 红色，半透明（盲点+转向灯警告）
const QColor BorderIndicators::COLOR_BRAKE = QColor(255, 0, 0, 180);          // 红色，半透明

BorderIndicators::BorderIndicators(QWidget *parent) : QWidget(parent) {
  // 设置窗口属性
  setAttribute(Qt::WA_TransparentForMouseEvents);  // 透传鼠标事件，不拦截点击
  setAttribute(Qt::WA_TranslucentBackground);      // 透明背景
  setAttribute(Qt::WA_NoSystemBackground);         // 不使用系统背景

  // 记录启动时间戳
  start_time_ms = QDateTime::currentMSecsSinceEpoch();

  // 设置刷新定时器（降低频率以提升性能）
  blink_timer = new QTimer(this);
  connect(blink_timer, &QTimer::timeout, [=]() {
    update();  // 触发重绘
  });
  blink_timer->start(100);  // 100ms 刷新一次（10 FPS），平衡性能和视觉效果
}

void BorderIndicators::updateState(const UIState &s) {
  // 定期读取参数（每60帧约6秒读一次），降低频率以提升性能
  param_read_counter++;
  if (param_read_counter >= 60) {
    bool prev_enabled = indicators_enabled;
    indicators_enabled = Params().getBool("BorderIndicatorsToggle");
    param_read_counter = 0;

    // 根据启用状态控制定时器
    if (indicators_enabled && !prev_enabled) {
      blink_timer->start(100);  // 启用时启动定时器（100ms）
    } else if (!indicators_enabled && prev_enabled) {
      blink_timer->stop();  // 禁用时停止定时器
    }
  }

  // 如果功能未启用，不更新状态
  if (!indicators_enabled) {
    // 清空所有状态
    left_blinker = false;
    right_blinker = false;
    left_blindspot = false;
    right_blindspot = false;
    brake_pressure = 0.0f;
    // 重置触发时间
    left_blindspot_trigger_time = -1;
    right_blindspot_trigger_time = -1;
    left_blinker_last_active_time = -1;
    right_blinker_last_active_time = -1;
    return;
  }

  // 获取车辆状态
  SubMaster &sm = *(s.sm);
  if (sm.valid("carState")) {
    auto car_state = sm["carState"].getCarState();

    // 读取转向灯状态
    bool prev_left_blinker = left_blinker;
    bool prev_right_blinker = right_blinker;
    left_blinker = car_state.getLeftBlinker();
    right_blinker = car_state.getRightBlinker();

    // 更新转向灯激活时间（用于判断转向灯是否在使用中）
    qint64 current_time = QDateTime::currentMSecsSinceEpoch() - start_time_ms;
    if (left_blinker) {
      left_blinker_last_active_time = current_time;
    }
    if (right_blinker) {
      right_blinker_last_active_time = current_time;
    }

    // 转向灯状态变化时立即触发重绘，并重置定时器相位，防止时钟漂移
    if (left_blinker != prev_left_blinker || right_blinker != prev_right_blinker) {
      LOGD("[Blinker] State changed: left=%d->%d right=%d->%d",
           (int)prev_left_blinker, (int)left_blinker,
           (int)prev_right_blinker, (int)right_blinker);
      update();  // 立即重绘
      blink_timer->start(100);  // 重置定时器，与CAN信号重新同步
    }

    // 读取盲点检测状态
    bool prev_left_blindspot = left_blindspot;
    bool prev_right_blindspot = right_blindspot;
    left_blindspot = car_state.getLeftBlindspot();
    right_blindspot = car_state.getRightBlindspot();

    // 调试日志：记录盲点信号变化（仅在状态改变时输出）
    if (left_blindspot != prev_left_blindspot) {
      LOGD("[Blindspot] Left blindspot changed: %d -> %d", (int)prev_left_blindspot, (int)left_blindspot);
    }
    if (right_blindspot != prev_right_blindspot) {
      LOGD("[Blindspot] Right blindspot changed: %d -> %d", (int)prev_right_blindspot, (int)right_blindspot);
    }

    // 更新盲点触发时间
    // 逻辑：
    // - 盲点信号为true：持续更新时间戳（表示"当前有盲点"）
    // - 盲点信号从true变false：记录消失时刻（开始500ms延长显示倒计时）
    // - 盲点信号持续为false：不更新时间戳（延长显示倒计时继续）
    if (left_blindspot) {
      left_blindspot_trigger_time = current_time;  // 有盲点：持续更新
    } else if (prev_left_blindspot) {
      // 盲点刚消失：记录消失时刻
      left_blindspot_trigger_time = current_time;
    }
    // 如果 !left_blindspot && !prev_left_blindspot：已经消失一段时间，不更新

    if (right_blindspot) {
      right_blindspot_trigger_time = current_time;  // 有盲点：持续更新
    } else if (prev_right_blindspot) {
      // 盲点刚消失：记录消失时刻
      right_blindspot_trigger_time = current_time;
    }


    // 读取制动压力
    // carstate.py已经返回归一化后的值(0.0-1.0)，直接使用即可
    brake_pressure = car_state.getBrake();
    // 限制在0.0-1.0范围内，避免超出范围导致显示异常
    brake_pressure = std::max(0.0f, std::min(1.0f, brake_pressure));

    // 调试日志：当刹车压力较高时记录值
    static int log_counter = 0;
    if (brake_pressure > 0.75f && (log_counter++ % 10 == 0)) {
      LOGD("[BorderIndicators] brake_pressure=%.3f", brake_pressure);
    }
  }
}

void BorderIndicators::paintEvent(QPaintEvent *event) {
  // 如果功能未启用，不绘制
  if (!indicators_enabled) {
    return;
  }

  // 只调用一次时间戳函数，避免重复调用
  qint64 current_time_ms = QDateTime::currentMSecsSinceEpoch() - start_time_ms;

  QPainter p(this);
  // 移除抗锯齿以提升性能（绘制矩形不需要抗锯齿）

  // 绘制各个指示器，传递时间戳避免重复计算
  drawLeftIndicator(p, current_time_ms);
  drawRightIndicator(p, current_time_ms);
  drawBrakeIndicator(p, current_time_ms);
}

void BorderIndicators::drawLeftIndicator(QPainter &p, qint64 current_time_ms) {
  // 优先级：盲点+转向灯激活（红色闪烁警告） > 盲点（橙色常亮） > 转向灯（绿色）

  // 转向灯：直接使用CAN信号状态，完全跟随原车闪烁节奏
  bool blinker_active = left_blinker;

  // 转向灯使用中：当前正在闪烁，或最近500ms内有闪烁（桥接OFF阶段）
  bool blinker_in_use = blinker_active ||
                        ((left_blinker_last_active_time >= 0) &&
                         (current_time_ms - left_blinker_last_active_time < BLINKER_BRIDGE_DURATION));

  // 盲点激活状态判断：
  // - 如果当前有盲点信号：直接激活（不依赖时间戳）
  // - 如果盲点信号消失但在500ms内：激活（延长显示）
  // - 超过500ms：不激活
  bool blindspot_active = left_blindspot;  // 当前有盲点信号，直接激活
  if (!blindspot_active && left_blindspot_trigger_time >= 0) {
    // 盲点信号已消失，检查是否在延长显示期内
    blindspot_active = (current_time_ms - left_blindspot_trigger_time < BLINDSPOT_DISPLAY_DURATION);
  }

  // 调试日志：记录红色闪烁状态变化
  static bool prev_red_blink_active = false;
  bool red_blink_active = (blindspot_active && blinker_in_use);
  if (red_blink_active != prev_red_blink_active) {
    LOGD("[Blindspot] Left RED blink changed: %d -> %d blindspot_active=%d blinker_in_use=%d left_blindspot=%d left_blinker=%d",
         (int)prev_red_blink_active, (int)red_blink_active, (int)blindspot_active, (int)blinker_in_use,
         (int)left_blindspot, (int)left_blinker);
    prev_red_blink_active = red_blink_active;
  }

  if (blindspot_active && blinker_in_use) {
    // 盲点有车且转向灯正在使用：红色快速闪烁警告（每秒5次）
    // 闪烁周期：200ms（亮100ms，灭100ms）
    bool should_draw = (current_time_ms % 200) < 100;
    if (should_draw) {
      p.fillRect(0, 0, BORDER_WIDTH, height(), COLOR_BLINDSPOT_WARN);
    }
    // 注意：闪烁的"灭"阶段不绘制任何内容，不要fallthrough到下面的else if
  } else if (blindspot_active) {
    // 盲点有车但转向灯未使用：橙色常亮
    p.fillRect(0, 0, BORDER_WIDTH, height(), COLOR_BLINDSPOT);
  } else if (blinker_active) {
    // 只有转向灯：绿色，完全跟随原车CAN信号
    p.fillRect(0, 0, BORDER_WIDTH, height(), COLOR_BLINKER);
  }
}

void BorderIndicators::drawRightIndicator(QPainter &p, qint64 current_time_ms) {
  // 优先级：盲点+转向灯激活（红色闪烁警告） > 盲点（橙色常亮） > 转向灯（绿色）

  // 转向灯：直接使用CAN信号状态，完全跟随原车闪烁节奏
  bool blinker_active = right_blinker;

  // 转向灯使用中：当前正在闪烁，或最近500ms内有闪烁（桥接OFF阶段）
  bool blinker_in_use = blinker_active ||
                        ((right_blinker_last_active_time >= 0) &&
                         (current_time_ms - right_blinker_last_active_time < BLINKER_BRIDGE_DURATION));

  // 盲点激活状态判断：
  // - 如果当前有盲点信号：直接激活（不依赖时间戳）
  // - 如果盲点信号消失但在500ms内：激活（延长显示）
  // - 超过500ms：不激活
  bool blindspot_active = right_blindspot;  // 当前有盲点信号，直接激活
  if (!blindspot_active && right_blindspot_trigger_time >= 0) {
    // 盲点信号已消失，检查是否在延长显示期内
    blindspot_active = (current_time_ms - right_blindspot_trigger_time < BLINDSPOT_DISPLAY_DURATION);
  }

  // 调试日志：记录红色闪烁状态变化
  static bool prev_red_blink_active = false;
  bool red_blink_active = (blindspot_active && blinker_in_use);
  if (red_blink_active != prev_red_blink_active) {
    LOGD("[Blindspot] Right RED blink changed: %d -> %d blindspot_active=%d blinker_in_use=%d right_blindspot=%d right_blinker=%d",
         (int)prev_red_blink_active, (int)red_blink_active, (int)blindspot_active, (int)blinker_in_use,
         (int)right_blindspot, (int)right_blinker);
    prev_red_blink_active = red_blink_active;
  }

  if (blindspot_active && blinker_in_use) {
    // 盲点有车且转向灯正在使用：红色快速闪烁警告（每秒5次）
    // 闪烁周期：200ms（亮100ms，灭100ms）
    bool should_draw = (current_time_ms % 200) < 100;
    if (should_draw) {
      p.fillRect(width() - BORDER_WIDTH, 0, BORDER_WIDTH, height(), COLOR_BLINDSPOT_WARN);
    }
    // 注意：闪烁的"灭"阶段不绘制任何内容，不要fallthrough到下面的else if
  } else if (blindspot_active) {
    // 盲点有车但转向灯未使用：橙色常亮
    p.fillRect(width() - BORDER_WIDTH, 0, BORDER_WIDTH, height(), COLOR_BLINDSPOT);
  } else if (blinker_active) {
    // 只有转向灯：绿色，完全跟随原车CAN信号
    p.fillRect(width() - BORDER_WIDTH, 0, BORDER_WIDTH, height(), COLOR_BLINKER);
  }
}

void BorderIndicators::drawBrakeIndicator(QPainter &p, qint64 current_time_ms) {
  // 刹车指示：根据制动压力动态显示
  // - 初始大小为1/4屏幕宽度
  // - 压力0-70%：从中间向两边延伸
  // - 压力70-84%：全屏常亮
  // - 压力>84%：整个指示条闪烁（出现/消失）

  if (brake_pressure <= 0.0f) {
    return;  // 无制动压力，不显示
  }

  int screen_width = width();
  int min_width = screen_width / 4;  // 初始大小：1/4屏幕宽度

  // 计算指示条宽度
  int bar_width;
  if (brake_pressure <= 0.70f) {
    // 压力0-70%：线性映射到 1/4 到全屏宽度
    // brake_pressure: 0.0 -> 0.70
    // bar_width: min_width -> screen_width
    float ratio = brake_pressure / 0.70f;  // 0.0 -> 1.0
    bar_width = min_width + (int)((screen_width - min_width) * ratio);
  } else {
    // 压力>70%：使用全屏宽度
    bar_width = screen_width;
  }

  // 计算居中位置（从中间向两边延伸）
  int x_pos = (screen_width - bar_width) / 2;

  // 压力>84%时整个指示条闪烁（出现/消失），70-84%时常亮
  bool should_draw = true;
  if (brake_pressure > 0.84f) {
    // 闪烁周期：600ms（显示300ms，隐藏300ms）
    should_draw = (current_time_ms % 600) < 300;
  }
  // 70-84%时 should_draw 保持为 true（常亮）

  // 调试日志：当刹车压力较高时记录绘制状态
  static int draw_log_counter = 0;
  if (brake_pressure > 0.65f && (draw_log_counter++ % 10 == 0)) {
    LOGD("[BorderIndicators] Drawing brake: pressure=%.3f, bar_width=%d, x_pos=%d, should_draw=%d",
           brake_pressure, bar_width, x_pos, should_draw);
  }

  if (should_draw) {
    p.fillRect(x_pos, 0, bar_width, BRAKE_HEIGHT, COLOR_BRAKE);
  }
}
