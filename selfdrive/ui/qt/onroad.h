#pragma once

#include <QStackedLayout>
#include <QWidget>
#include <QSoundEffect>

#include "selfdrive/ui/qt/widgets/cameraview.h"
#include "selfdrive/ui/qt/widgets/border_indicators.h"
#include "selfdrive/ui/ui.h"


// ***** onroad widgets *****

class OnroadHud : public QWidget {
  Q_OBJECT
  Q_PROPERTY(QString speed MEMBER speed NOTIFY valueChanged);
  Q_PROPERTY(QString speedUnit MEMBER speedUnit NOTIFY valueChanged);
  Q_PROPERTY(QString maxSpeed MEMBER maxSpeed NOTIFY valueChanged);
  Q_PROPERTY(bool is_cruise_set MEMBER is_cruise_set NOTIFY valueChanged);
  Q_PROPERTY(bool engageable MEMBER engageable NOTIFY valueChanged);
  Q_PROPERTY(bool dmActive MEMBER dmActive NOTIFY valueChanged);
  Q_PROPERTY(bool hideDM MEMBER hideDM NOTIFY valueChanged);
  Q_PROPERTY(int status MEMBER status NOTIFY valueChanged);

public:
  explicit OnroadHud(QWidget *parent);
  void updateState(const UIState &s);

private:
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void paintEvent(QPaintEvent *event) override;

  QPixmap engage_img;
  QPixmap dm_img;
  const int radius = 192;
  const int img_size = (radius / 2) * 1.5;
  QString speed;
  QString speedUnit;
  QString maxSpeed;
  bool is_cruise_set = false;
  bool engageable = false;
  bool dmActive = false;
  bool hideDM = false;
  int status = STATUS_DISENGAGED;
  QString actualSteeringAngle;
  QString desiredSteeringAngle;

signals:
  void valueChanged();
};

class OnroadAlerts : public QWidget {
  Q_OBJECT

public:
  OnroadAlerts(QWidget *parent = 0) : QWidget(parent) {};
  void updateAlert(const Alert &a, const QColor &color);

protected:
  void paintEvent(QPaintEvent*) override;

private:
  QColor bg;
  Alert alert = {};
};

// container window for the NVG UI
class NvgWindow : public CameraViewWidget {
  Q_OBJECT

public:
  explicit NvgWindow(VisionStreamType type, QWidget* parent = 0) : CameraViewWidget("camerad", type, true, parent) {}
  bool isPointInLateralButton(const QPoint &pos);  // 判断点击是否在按钮内（需要public以便OnroadWindow调用）
  void handleLateralButtonClick();  // 处理横向按钮点击（public方法）

  // 横向控制按钮相关变量（public以便OnroadWindow访问）
  bool lateral_only_button_enabled = false;  // 设置中是否启用按钮
  int button_param_read_counter = 0;         // 参数读取计数器
  QRect lateral_button_rect;                 // 按钮位置矩形

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat(int w, int h) override;
  void drawLaneLines(QPainter &painter, const UIScene &scene);
  void drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data,
                const cereal::ModelDataV2::XYZTData::Reader &line, const QPointF &vd, int radar_idx);
  std::map<int, int> drawRadarTargets(QPainter &painter, const cereal::ModelDataV2::XYZTData::Reader &line,
                                       const capnp::List<cereal::ModelDataV2::LeadDataV3>::Reader &leads);  // 绘制所有雷达目标，返回视觉到雷达的映射
  void mousePressEvent(QMouseEvent* e) override;   // 处理点击事件

  // 添加前车起步提醒功能相关变量
  bool showCarInfo = false;  // 前车信息显示开关（缓存）
  bool leadCarAlertEnabled = false;  // 前车起步提醒开关（缓存）
  bool lateral_only_active = false;  // 横向模式激活状态（缓存）
  int param_read_counter = 0;  // 参数读取计数器
  int lead_display_frame_counter = 0;  // 前车信息显示帧计数器（用于插入空白帧）
  int radar_display_frame_counter = 0;  // 雷达目标显示帧计数器（用于插入空白帧）
  bool leadCarStopped = false;
  double leadCarStopTime = 0.0;
  bool leadCarMoving = false;  // 前车是否开始移动（但还没移动够2米）
  float leadCarStartMovingDistance = 0.0;  // 前车开始移动时的距离
  bool leadCarAlertTriggered = false;
  double leadCarAlertStartTime = 0.0;
  bool showLeadCarAlert = false;

  // 前车起步提醒声音
  QSoundEffect *leadCarAlertSound = nullptr;

  // 车距滤波相关变量
  float filtered_d_rel = -1.0;  // 滤波后的车距，-1表示未初始化
  float filtered_v_rel = 0.0;   // 滤波后的相对速度
  int lead_stable_count = 0;    // 前车稳定检测计数

  // 添加前车起步提醒检测方法
  void checkLeadCarAlert(const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, double current_time);
  void drawLeadCarAlert(QPainter &painter);
  void drawLateralButton(QPainter &painter);  // 绘制横向控制按钮
  inline QColor redColor(int alpha = 255) { return QColor(201, 34, 49, alpha); }
  double prev_draw_t = 0;
};

// container for all onroad widgets
class OnroadWindow : public QWidget {
  Q_OBJECT

public:
  OnroadWindow(QWidget* parent = 0);
  bool isMapVisible() const { return map && map->isVisible(); }

private:
  void paintEvent(QPaintEvent *event);
  void mousePressEvent(QMouseEvent* e) override;
  OnroadHud *hud;
  OnroadAlerts *alerts;
  NvgWindow *nvg;
  BorderIndicators *border_indicators;  // 边框指示器组件
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QWidget *map = nullptr;
  QHBoxLayout* split;

  // 底部状态信息
  QString status_text;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
