#include "selfdrive/ui/qt/sidebar.h"

#include <QMouseEvent>

#include "selfdrive/ui/qt/util.h"

void Sidebar::drawMetric(QPainter &p, const QString &label, QColor c, int y) {
  drawMetric(p, label, c, y, 35);  // 默认字体大小35
}

void Sidebar::drawMetric(QPainter &p, const QString &label, QColor c, int y, int fontSize) {
  const QRect rect = {30, y, 240, label.contains("\n") ? 124 : 100};

  p.setPen(Qt::NoPen);
  p.setBrush(QBrush(c));
  p.setClipRect(rect.x() + 6, rect.y(), 18, rect.height(), Qt::ClipOperation::ReplaceClip);
  p.drawRoundedRect(QRect(rect.x() + 6, rect.y() + 6, 100, rect.height() - 12), 10, 10);
  p.setClipping(false);

  QPen pen = QPen(QColor(0xff, 0xff, 0xff, 0x55));
  pen.setWidth(2);
  p.setPen(pen);
  p.setBrush(Qt::NoBrush);
  p.drawRoundedRect(rect, 20, 20);

  p.setPen(QColor(0xff, 0xff, 0xff));
  configFont(p, "Open Sans", fontSize, "Bold");
  const QRect r = QRect(rect.x() + 30, rect.y(), rect.width() - 40, rect.height());
  p.drawText(r, Qt::AlignCenter, label);
}

Sidebar::Sidebar(QWidget *parent) : QFrame(parent) {
  home_img = loadPixmap("../assets/images/button_home.png", {180, 180});
  settings_img = loadPixmap("../assets/images/button_settings.png", settings_btn.size(), Qt::IgnoreAspectRatio);

  connect(this, &Sidebar::valueChanged, [=] { update(); });

  setAttribute(Qt::WA_OpaquePaintEvent);
  setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
  setFixedWidth(300);

  QObject::connect(uiState(), &UIState::uiUpdate, this, &Sidebar::updateState);
}

void Sidebar::mouseReleaseEvent(QMouseEvent *event) {
  if (settings_btn.contains(event->pos())) {
    emit openSettings();
  }
}

void Sidebar::updateState(const UIState &s) {
  if (!isVisible()) return;

  auto &sm = *(s.sm);

  auto deviceState = sm["deviceState"].getDeviceState();
  setProperty("netType", network_type[deviceState.getNetworkType()]);
  int strength = (int)deviceState.getNetworkStrength();
  setProperty("netStrength", strength > 0 ? strength + 1 : 0);

  ItemStatus connectStatus;
  auto last_ping = deviceState.getLastAthenaPingTime();
  if (last_ping == 0) {
    connectStatus = params.getBool("PrimeRedirected") ? ItemStatus{"NO\nPRIME", danger_color} : ItemStatus{"服务\n离线", warning_color};
  } else {
    connectStatus = nanos_since_boot() - last_ping < 80e9 ? ItemStatus{"服务\n在线", good_color} : ItemStatus{"CONNECT\nERROR", danger_color};
  }
  setProperty("connectStatus", QVariant::fromValue(connectStatus));

  // 获取CPU温度（取最大值）
  auto cpuTemps = deviceState.getCpuTempC();
  int maxTemp = 0;
  if (cpuTemps.size() > 0) {
    for (auto temp : cpuTemps) {
      if (temp > maxTemp) maxTemp = (int)temp;
    }
  }

  // 根据温度状态选择颜色，直接显示温度数值
  auto ts = deviceState.getThermalStatus();
  QColor tempColor = danger_color;
  if (ts == cereal::DeviceState::ThermalStatus::GREEN) {
    tempColor = good_color;
  } else if (ts == cereal::DeviceState::ThermalStatus::YELLOW) {
    tempColor = warning_color;
  }

  ItemStatus tempStatus = {QString("%1°C").arg(maxTemp), tempColor};
  setProperty("tempStatus", QVariant::fromValue(tempStatus));

  ItemStatus pandaStatus = {"车辆\n在线", good_color};
  if (s.scene.pandaType == cereal::PandaState::PandaType::UNKNOWN) {
    pandaStatus = {"无\nPANDA", danger_color};
    gps_accuracy_update_counter = 0;  // 重置计数器
    last_gps_accuracy_status = {"车辆\n在线", good_color};  // 重置缓存
  } else if (s.scene.started && !sm["liveLocationKalman"].getLiveLocationKalman().getGpsOK()) {
    pandaStatus = {"GPS\n搜索中", warning_color};
    gps_accuracy_update_counter = 0;  // 重置计数器
    last_gps_accuracy_status = {"车辆\n在线", good_color};  // 重置缓存
  } else if (s.scene.started && sm.valid("gpsLocationExternal")) {
    // Panda正常 + GPS正常 → 显示GPS定位精度（优先级最低）
    // 每4秒更新一次，降低资源消耗
    gps_accuracy_update_counter++;
    if (gps_accuracy_update_counter >= UI_FREQ * 4) {  // 4秒更新一次 (15 * 4 = 60帧)
      gps_accuracy_update_counter = 0;

      float accuracy = sm["gpsLocationExternal"].getGpsLocationExternal().getAccuracy();
      // 统一使用白色显示，不根据精度区分颜色
      last_gps_accuracy_status = {QString("GPS\n%1m").arg(accuracy, 0, 'f', 1), good_color};
    }
    // 使用缓存的GPS精度显示（常驻显示）
    pandaStatus = last_gps_accuracy_status;
  } else {
    gps_accuracy_update_counter = 0;  // 其他情况重置计数器
    last_gps_accuracy_status = {"车辆\n在线", good_color};  // 重置缓存
  }
  setProperty("pandaStatus", QVariant::fromValue(pandaStatus));
}

void Sidebar::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setPen(Qt::NoPen);
  p.setRenderHint(QPainter::Antialiasing);

  p.fillRect(rect(), QColor(57, 57, 57));

  // static imgs
  p.setOpacity(0.65);
  p.drawPixmap(settings_btn.x(), settings_btn.y(), settings_img);
  p.setOpacity(1.0);
  p.drawPixmap(60, 1080 - 180 - 40, home_img);

  // network
  int x = 58;
  const QColor gray(0x54, 0x54, 0x54);
  for (int i = 0; i < 5; ++i) {
    p.setBrush(i < net_strength ? Qt::white : gray);
    p.drawEllipse(x, 196, 27, 27);
    x += 37;
  }

  configFont(p, "Open Sans", 35, "Regular");
  p.setPen(QColor(0xff, 0xff, 0xff));
  const QRect r = QRect(50, 247, 100, 50);
  p.drawText(r, Qt::AlignCenter, net_type);

  // metrics
  drawMetric(p, temp_status.first, temp_status.second, 338, 45);  // 温度使用45号字体
  drawMetric(p, panda_status.first, panda_status.second, 496);
  drawMetric(p, connect_status.first, connect_status.second, 654);
}
