#pragma once

#include <QWidget>
#include <QPainter>
#include <QTimer>
#include "cereal/messaging/messaging.h"
#include "selfdrive/ui/ui.h"

/**
 * BorderIndicators - 边框指示器组件
 *
 * 在屏幕边框显示车辆状态指示：
 * - 左/右转向灯：橙色边框闪烁
 * - 左/右盲点检测：红色边框闪烁（优先级高于转向灯）
 * - 刹车状态：顶部红色条
 *
 * 特点：
 * - 独立组件，职责单一
 * - 透明背景，不影响其他UI元素
 * - 可通过设置开关启用/禁用
 */
class BorderIndicators : public QWidget {
  Q_OBJECT

public:
  explicit BorderIndicators(QWidget *parent = nullptr);

  /**
   * 更新车辆状态
   * 从 UIState 中读取转向灯、盲点、刹车等状态
   */
  void updateState(const UIState &s);

protected:
  /**
   * 绘制边框指示器
   */
  void paintEvent(QPaintEvent *event) override;

private:
  // ========== 车辆状态 ==========
  bool left_blinker = false;      // 左转向灯
  bool right_blinker = false;     // 右转向灯
  bool left_blindspot = false;    // 左盲点检测
  bool right_blindspot = false;   // 右盲点检测
  float brake_pressure = 0.0f;    // 制动压力（0.0-1.0）

  // ========== 盲点触发时间 ==========
  // 每个BSM信号都有独立的时间戳和延长显示时间
  qint64 left_blindspot_trigger_time = -1;   // 左盲点基本信号(BSM_LEFT)最后触发时间
  qint64 right_blindspot_trigger_time = -1;  // 右盲点基本信号(BSM_RIGHT)最后触发时间
  qint64 left_blindspot_light_trigger_time = -1;   // 左盲点报警信号(BSM_LEFT_Light)最后触发时间
  qint64 right_blindspot_light_trigger_time = -1;  // 右盲点报警信号(BSM_RIGHT_Light)最后触发时间
  static constexpr int BLINDSPOT_DISPLAY_DURATION = 600;  // 盲点警告延长显示时间（毫秒），每个信号消失后独立延长

  // ========== 转向灯激活状态 ==========
  qint64 left_blinker_last_active_time = -1;   // 左转向灯最后激活时间
  qint64 right_blinker_last_active_time = -1;  // 右转向灯最后激活时间
  static constexpr int BLINKER_BRIDGE_DURATION = 500;  // 转向灯OFF阶段桥接时间（毫秒），用于桥接转向灯闪烁的OFF阶段

  // ========== 动画控制 ==========
  QTimer *blink_timer;            // 动画定时器
  qint64 start_time_ms = 0;       // 启动时间戳（毫秒）

  // ========== 配置参数 ==========
  bool indicators_enabled = true; // 功能总开关
  int param_read_counter = 0;     // 参数读取计数器（降低读取频率）

  // ========== 绘制方法 ==========
  /**
   * 绘制左侧指示器（转向灯或盲点）
   */
  void drawLeftIndicator(QPainter &p, qint64 current_time_ms);

  /**
   * 绘制右侧指示器（转向灯或盲点）
   */
  void drawRightIndicator(QPainter &p, qint64 current_time_ms);

  /**
   * 绘制刹车指示器（顶部红色条，根据制动压力动态显示）
   * - 压力0-70%：从中间向两边延伸，动态显示长度
   * - 压力>75%：红色指示条闪烁
   */
  void drawBrakeIndicator(QPainter &p, qint64 current_time_ms);

  // ========== 样式配置 ==========
  static constexpr int BORDER_WIDTH = 25;              // 边框宽度（像素）
  static constexpr int BRAKE_HEIGHT = 15;              // 刹车指示条高度（像素）
  static constexpr int BLINKER_INTERVAL = 5;           // 转向灯闪烁间隔（帧）- 每秒2次
  static constexpr int BLINDSPOT_WARN_INTERVAL = 2;    // 盲点警告闪烁间隔（帧）- 每秒5次
  static constexpr int BRAKE_BLINK_INTERVAL = 3;       // 刹车闪烁间隔（帧）- 每秒约3次

  // 颜色定义
  static const QColor COLOR_BLINKER;         // 转向灯颜色（绿色）
  static const QColor COLOR_BLINDSPOT;       // 盲点颜色（橙色，常亮）
  static const QColor COLOR_BLINDSPOT_WARN;  // 盲点警告颜色（红色，闪烁）
  static const QColor COLOR_BRAKE;           // 刹车颜色（红色）
};
