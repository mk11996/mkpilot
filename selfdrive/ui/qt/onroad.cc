#include "selfdrive/ui/qt/onroad.h"

#include <cmath>
#include <set>
#include <map>
#include <QSoundEffect>
#include <QUrl>
#include <QMouseEvent>

#include <QDebug>

#include "selfdrive/common/timing.h"
#include "selfdrive/ui/qt/util.h"
#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map.h"
#include "selfdrive/ui/qt/maps/map_helpers.h"
#endif

OnroadWindow::OnroadWindow(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout  = new QVBoxLayout(this);
  main_layout->setMargin(bdr_s);
  QStackedLayout *stacked_layout = new QStackedLayout;
  stacked_layout->setStackingMode(QStackedLayout::StackAll);
  main_layout->addLayout(stacked_layout);

  QStackedLayout *road_view_layout = new QStackedLayout;
  road_view_layout->setStackingMode(QStackedLayout::StackAll);
  nvg = new NvgWindow(VISION_STREAM_RGB_BACK, this);
  road_view_layout->addWidget(nvg);
  hud = new OnroadHud(this);
  hud->setAttribute(Qt::WA_TransparentForMouseEvents, true);  // 让hud透传鼠标事件
  road_view_layout->addWidget(hud);

  QWidget * split_wrapper = new QWidget;
  split = new QHBoxLayout(split_wrapper);
  split->setContentsMargins(0, 0, 0, 0);
  split->setSpacing(0);
  split->addLayout(road_view_layout);

  stacked_layout->addWidget(split_wrapper);

  alerts = new OnroadAlerts(this);
  alerts->setAttribute(Qt::WA_TransparentForMouseEvents, true);
  stacked_layout->addWidget(alerts);

  // 添加边框指示器（在最上层）
  border_indicators = new BorderIndicators(this);
  border_indicators->setAttribute(Qt::WA_TransparentForMouseEvents, true);
  stacked_layout->addWidget(border_indicators);

  // setup stacking order
  border_indicators->raise();  // 边框指示器在最上层
  alerts->raise();

  setAttribute(Qt::WA_OpaquePaintEvent);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &OnroadWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, this, &OnroadWindow::offroadTransition);
}

void OnroadWindow::updateState(const UIState &s) {
  QColor bgColor = bg_colors[s.status];
  Alert alert = Alert::get(*(s.sm), s.scene.started_frame);
  if (s.sm->updated("controlsState") || !alert.equal({})) {
    if (alert.type == "controlsUnresponsive") {
      bgColor = bg_colors[STATUS_ALERT];
    } else if (alert.type == "controlsUnresponsivePermanent") {
      bgColor = bg_colors[STATUS_DISENGAGED];
    }
    alerts->updateAlert(alert, bgColor);
  }

  hud->updateState(s);

  // 更新边框指示器
  border_indicators->updateState(s);

  // 横向控制按钮逻辑已移至 NvgWindow::paintGL，这里不再需要
  /*
  // 降低参数读取频率，每30帧（约0.5秒）读取一次
  button_param_read_counter++;
  if (button_param_read_counter >= 30) {
    lateral_only_button_enabled = Params().getBool("LateralOnlyControl");
    button_param_read_counter = 0;
  }

  // 检查车辆状态和ACC状态
  const SubMaster &sm = *(s.sm);
  if (sm.valid("carState")) {
    auto carState = sm["carState"].getCarState();
    auto gear = carState.getGearShifter();
    bool in_park = (gear == cereal::CarState::GearShifter::PARK);
    bool acc_enabled = carState.getCruiseState().getEnabled();  // 原车ACC是否启用

    // 退出条件：挂P档或原车ACC启用
    if (lateral_only_active && (in_park || acc_enabled)) {
      lateral_only_active = false;
      Params().remove("LateralOnlyActive");
    }
  }
  */

  // 更新底部状态信息：帧率、模型执行时间、CPU占用率
  // 降低更新频率以提升性能（每4秒更新一次）
  static int status_update_counter = 0;
  status_update_counter++;

  if (status_update_counter >= UI_FREQ * 4) {  // 4秒更新一次 (15 * 4 = 60帧)
    QString status_str = "";

    // 获取模型数据
    const SubMaster &sm = *(s.sm);
    if (sm.valid("modelV2")) {
      auto model = sm["modelV2"].getModelV2();
      float modelExecTime = model.getModelExecutionTime();
      // modelExecTime 单位是秒，转换为毫秒和 FPS
      float modelExecTimeMs = modelExecTime * 1000.0f;  // 转换为毫秒
      float fps = modelExecTime > 0 ? 1.0f / modelExecTime : 0.0f;  // FPS = 1 / 秒
      status_str += QString("FPS: %1  执行: %2ms").arg(fps, 0, 'f', 1).arg(modelExecTimeMs, 0, 'f', 1);
    }

    // 获取CPU占用率
    if (sm.valid("deviceState")) {
      auto deviceState = sm["deviceState"].getDeviceState();
      auto cpuUsage = deviceState.getCpuUsagePercent();
      if (cpuUsage.size() > 0) {
        int avgCpu = 0;
        for (auto usage : cpuUsage) {
          avgCpu += usage;
        }
        avgCpu /= cpuUsage.size();
        status_str += QString("  CPU: %1%").arg(avgCpu);
      }
    }

    // 只有在内容变化时才更新并触发重绘
    if (status_text != status_str) {
      status_text = status_str;
      update();  // 触发重绘
    }
    status_update_counter = 0;
  }

  if (bg != bgColor) {
    // repaint border
    bg = bgColor;
    update();
  }
}

void OnroadWindow::mousePressEvent(QMouseEvent* e) {
  // 横向控制按钮已由 NvgWindow::mousePressEvent 处理
  // 移除重复的按钮检测逻辑，避免同一个点击被处理两次导致模式快速开关

  // 正常处理地图显示
  if (map != nullptr) {
    bool sidebarVisible = geometry().x() > 0;
    map->setVisible(!sidebarVisible && !map->isVisible());
  }
  // propagation event to parent(HomeWindow)
  QWidget::mousePressEvent(e);
}

void OnroadWindow::offroadTransition(bool offroad) {
#ifdef ENABLE_MAPS
  if (!offroad) {
    if (map == nullptr && (uiState()->prime_type || !MAPBOX_TOKEN.isEmpty())) {
      MapWindow * m = new MapWindow(get_mapbox_settings());
      map = m;

      QObject::connect(uiState(), &UIState::offroadTransition, m, &MapWindow::offroadTransition);

      m->setFixedWidth(topWidget(this)->width() / 2);
      split->addWidget(m, 0, Qt::AlignRight);

      // Make map visible after adding to split
      m->offroadTransition(offroad);
    }
  }
#endif

  alerts->updateAlert({}, bg);

  // update stream type
  bool wide_cam = Hardware::TICI() && Params().getBool("EnableWideCamera");
  nvg->setStreamType(wide_cam ? VISION_STREAM_RGB_WIDE : VISION_STREAM_RGB_BACK);
}

void OnroadWindow::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.fillRect(rect(), QColor(bg.red(), bg.green(), bg.blue(), 255));

  // 在底部中间绘制状态信息（完全在边框的30像素内）
  if (!status_text.isEmpty()) {
    p.setPen(QColor(255, 255, 255, 200));  // 白色半透明
    p.setFont(QFont("Inter", 6, QFont::Normal));  // 缩小字体到6pt确保不超出边框
    // 位置：从底部30像素（边框顶部）开始，高度25像素，留5像素底部边距
    // 这样文字完全在30像素的边框区域内
    QRect textRect(5, height() - 30, width() - 10, 25);
    p.drawText(textRect, Qt::AlignCenter, status_text);
  }

  // 横向控制按钮已移至 NvgWindow 绘制，这里不再绘制
  // if (lateral_only_button_enabled) {
  //   qInfo() << "Drawing lateral button, enabled:" << lateral_only_button_enabled;
  //   drawLateralButton(p);
  // } else {
  //   qInfo() << "Lateral button NOT enabled:" << lateral_only_button_enabled;
  // }
}

// ***** onroad widgets *****

// OnroadAlerts
void OnroadAlerts::updateAlert(const Alert &a, const QColor &color) {
  if (!alert.equal(a) || color != bg) {
    alert = a;
    bg = color;
    update();
  }
}

void OnroadAlerts::paintEvent(QPaintEvent *event) {
  if (alert.size == cereal::ControlsState::AlertSize::NONE) {
    return;
  }
  static std::map<cereal::ControlsState::AlertSize, const int> alert_sizes = {
    {cereal::ControlsState::AlertSize::SMALL, 271},
    {cereal::ControlsState::AlertSize::MID, 420},
    {cereal::ControlsState::AlertSize::FULL, height()},
  };
  int h = alert_sizes[alert.size];
  QRect r = QRect(0, height() - h, width(), h);

  QPainter p(this);

  // draw background + gradient
  p.setPen(Qt::NoPen);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  p.setBrush(QBrush(bg));
  p.drawRect(r);

  QLinearGradient g(0, r.y(), 0, r.bottom());
  g.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.05));
  g.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0.35));

  p.setCompositionMode(QPainter::CompositionMode_DestinationOver);
  p.setBrush(QBrush(g));
  p.fillRect(r, g);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  // text
  const QPoint c = r.center();
  p.setPen(QColor(0xff, 0xff, 0xff));
  p.setRenderHint(QPainter::TextAntialiasing);
  if (alert.size == cereal::ControlsState::AlertSize::SMALL) {
    configFont(p, "Open Sans", 74, "SemiBold");
    p.drawText(r, Qt::AlignCenter, alert.text1);
  } else if (alert.size == cereal::ControlsState::AlertSize::MID) {
    configFont(p, "Open Sans", 88, "Bold");
    p.drawText(QRect(0, c.y() - 125, width(), 150), Qt::AlignHCenter | Qt::AlignTop, alert.text1);
    configFont(p, "Open Sans", 66, "Regular");
    p.drawText(QRect(0, c.y() + 21, width(), 90), Qt::AlignHCenter, alert.text2);
  } else if (alert.size == cereal::ControlsState::AlertSize::FULL) {
    bool l = alert.text1.length() > 15;
    configFont(p, "Open Sans", l ? 132 : 177, "Bold");
    p.drawText(QRect(0, r.y() + (l ? 240 : 270), width(), 600), Qt::AlignHCenter | Qt::TextWordWrap, alert.text1);
    configFont(p, "Open Sans", 88, "Regular");
    p.drawText(QRect(0, r.height() - (l ? 361 : 420), width(), 300), Qt::AlignHCenter | Qt::TextWordWrap, alert.text2);
  }
}

// OnroadHud
OnroadHud::OnroadHud(QWidget *parent) : QWidget(parent) {
  engage_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  dm_img = loadPixmap("../assets/img_driver_face.png", {img_size, img_size});

  // 初始化转向角度显示为0.0
  actualSteeringAngle = "0.0";
  desiredSteeringAngle = "0.0";

  connect(this, &OnroadHud::valueChanged, [=] { update(); });
}

void OnroadHud::updateState(const UIState &s) {
  const int SET_SPEED_NA = 255;
  const SubMaster &sm = *(s.sm);
  const auto cs = sm["controlsState"].getControlsState();
  auto car_state = sm["carState"].getCarState();

  float maxspeed = cs.getVCruise();
  bool cruise_set = maxspeed > 0 && (int)maxspeed != SET_SPEED_NA;
  if (cruise_set && !s.scene.is_metric) {
    maxspeed *= KM_TO_MILE;
  }
  QString maxspeed_str = cruise_set ? QString::number(std::nearbyint(maxspeed)) : "N/A";
  float cur_speed = std::max(0.0, car_state.getVEgo() * (s.scene.is_metric ? MS_TO_KPH : MS_TO_MPH));

  setProperty("is_cruise_set", cruise_set);
  setProperty("speed", QString::number(std::nearbyint(cur_speed)));
  setProperty("maxSpeed", maxspeed_str);

  // 读取转向角度
  float actual_angle = car_state.getSteeringAngleDeg();
  float desired_angle = 0.0f;

  // 方案：从lateralPlan读取曲率，直接计算预期转向角度
  // 这样不依赖任何控制器类型，更简单可靠
  if (sm.valid("lateralPlan") && sm.valid("carState")) {
    auto lateral_plan = sm["lateralPlan"].getLateralPlan();
    auto curvatures = lateral_plan.getCurvatures();

    if (curvatures.size() > 0) {
      // 取第一个曲率值（当前时刻的预测曲率）
      float curvature = curvatures[0];

      // 计算方向盘转向角度：steering_angle = atan(wheelbase * curvature) * steer_ratio * (180/pi)
      // wheelbase for Mazda is approximately 2.7m
      // steer_ratio for Mazda is approximately 15.5 (方向盘转角 / 前轮转角)
      // 注意：曲率取负号，与 latcontrol_angle.py 中的符号约定一致
      const float wheelbase = 2.7f;
      const float steer_ratio = 15.5f;  // Mazda 转向比
      const float rad_to_deg = 57.2957795f; // 180/pi

      // 前轮转角 × 转向比 = 方向盘转角
      // 曲率取负号：正曲率 = 左转 = 正转向角
      // 移除速度限制，即使静止时也显示预计转向角（便于调试）
      desired_angle = atan(wheelbase * (-curvature)) * rad_to_deg * steer_ratio;
    }
  }

  actualSteeringAngle = QString::number(actual_angle, 'f', 1);
  desiredSteeringAngle = QString::number(desired_angle, 'f', 1);
  setProperty("speedUnit", s.scene.is_metric ? "km/h" : "mph");
  setProperty("hideDM", cs.getAlertSize() != cereal::ControlsState::AlertSize::NONE);
  setProperty("status", s.status);

  // update engageability and DM icons at 2Hz
  if (sm.frame % (UI_FREQ / 2) == 0) {
    setProperty("engageable", cs.getEngageable() || cs.getEnabled());
    // 只有在驾驶员监控启用时才更新dmActive状态
    if (sm.valid("driverMonitoringState")) {
      setProperty("dmActive", sm["driverMonitoringState"].getDriverMonitoringState().getIsActiveMode());
    } else {
      setProperty("dmActive", false);
    }
  }
}

void OnroadHud::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  // Header gradient
  QLinearGradient bg(0, header_h - (header_h / 2.5), 0, header_h);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, width(), header_h, bg);

  // max speed
  QRect rc(bdr_s * 2, bdr_s * 1.5, 184, 202);
  p.setPen(QPen(QColor(0xff, 0xff, 0xff, 100), 10));
  p.setBrush(QColor(0, 0, 0, 100));
  p.drawRoundedRect(rc, 20, 20);
  p.setPen(Qt::NoPen);

  configFont(p, "Open Sans", 48, "Regular");
  drawText(p, rc.center().x(), 118, "MAX", is_cruise_set ? 200 : 100);
  if (is_cruise_set) {
    configFont(p, "Open Sans", 88, is_cruise_set ? "Bold" : "SemiBold");
    drawText(p, rc.center().x(), 212, maxSpeed, 255);
  } else {
    configFont(p, "Open Sans", 80, "SemiBold");
    drawText(p, rc.center().x(), 212, maxSpeed, 100);
  }

  // steering angles (实际和预计转向角度)
  if (!actualSteeringAngle.isEmpty() && !desiredSteeringAngle.isEmpty()) {
    QRect steer_rc(bdr_s * 2, bdr_s * 1.5 + 202 + 20, 184, 280);
    p.setPen(QPen(QColor(0xff, 0xff, 0xff, 100), 10));
    p.setBrush(QColor(0, 0, 0, 100));
    p.drawRoundedRect(steer_rc, 20, 20);
    p.setPen(Qt::NoPen);

    // 预计转向角度
    configFont(p, "Open Sans", 36, "Regular");
    drawText(p, steer_rc.center().x(), steer_rc.top() + 60, "预计", 200);
    configFont(p, "Open Sans", 56, "Bold");
    drawText(p, steer_rc.center().x(), steer_rc.top() + 120, desiredSteeringAngle, 255);

    // 实际转向角度
    configFont(p, "Open Sans", 36, "Regular");
    drawText(p, steer_rc.center().x(), steer_rc.top() + 180, "实际", 200);
    configFont(p, "Open Sans", 56, "Bold");
    drawText(p, steer_rc.center().x(), steer_rc.top() + 240, actualSteeringAngle, 255);
  }

  // current speed
  configFont(p, "Open Sans", 176, "Bold");
  drawText(p, rect().center().x(), 210, speed);
  configFont(p, "Open Sans", 66, "Regular");
  drawText(p, rect().center().x(), 290, speedUnit, 200);

  // engage-ability icon
  if (engageable) {
    drawIcon(p, rect().right() - radius / 2 - bdr_s * 2, radius / 2 + int(bdr_s * 1.5),
             engage_img, bg_colors[status], 1.0);
  }

  // dm icon
  if (!hideDM) {
    drawIcon(p, radius / 2 + (bdr_s * 2), rect().bottom() - footer_h / 2,
             dm_img, QColor(0, 0, 0, 70), dmActive ? 1.0 : 0.2);
  }
}

void OnroadHud::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QFontMetrics fm(p.font());
  QRect init_rect = fm.boundingRect(text);
  QRect real_rect = fm.boundingRect(init_rect, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});

  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void OnroadHud::drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity) {
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - radius / 2, y - radius / 2, radius, radius);
  p.setOpacity(opacity);
  p.drawPixmap(x - img_size / 2, y - img_size / 2, img);
}

// 在类定义中需要添加信号声明，但由于我们只能编辑现有文件内容
// 我们假设已经有适当的信号可以使用，或者使用现有的机制

// NvgWindow
void NvgWindow::initializeGL() {
  CameraViewWidget::initializeGL();
  qInfo() << "OpenGL version:" << QString((const char*)glGetString(GL_VERSION));
  qInfo() << "OpenGL vendor:" << QString((const char*)glGetString(GL_VENDOR));
  qInfo() << "OpenGL renderer:" << QString((const char*)glGetString(GL_RENDERER));
  qInfo() << "OpenGL language version:" << QString((const char*)glGetString(GL_SHADING_LANGUAGE_VERSION));

  prev_draw_t = millis_since_boot();
  setBackgroundColor(bg_colors[STATUS_DISENGAGED]);

  // 初始化前车起步提醒声音（参考soundd的做法）
  leadCarAlertSound = new QSoundEffect(this);
  QObject::connect(leadCarAlertSound, &QSoundEffect::statusChanged, [this]() {
    if (leadCarAlertSound->status() == QSoundEffect::Error) {
      qWarning() << "Failed to load lead car alert sound: 1.wav";
    } else if (leadCarAlertSound->status() == QSoundEffect::Ready) {
      qInfo() << "Lead car alert sound loaded successfully";
    }
  });
  leadCarAlertSound->setSource(QUrl::fromLocalFile("../assets/sounds/1.wav"));
  leadCarAlertSound->setVolume(0.8);
  leadCarAlertSound->setLoopCount(1);  // 只播放一次
}

void NvgWindow::updateFrameMat(int w, int h) {
  CameraViewWidget::updateFrameMat(w, h);

  UIState *s = uiState();
  s->fb_w = w;
  s->fb_h = h;
  auto intrinsic_matrix = s->wide_camera ? ecam_intrinsic_matrix : fcam_intrinsic_matrix;
  float zoom = ZOOM / intrinsic_matrix.v[0];
  if (s->wide_camera) {
    zoom *= 0.5;
  }
  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  // 2) Apply same scaling as video
  // 3) Put (0, 0) in top left corner of video
  s->car_space_transform.reset();
  s->car_space_transform.translate(w / 2, h / 2 + y_offset)
      .scale(zoom, zoom)
      .translate(-intrinsic_matrix.v[2], -intrinsic_matrix.v[5]);
}

void NvgWindow::drawLaneLines(QPainter &painter, const UIScene &scene) {
  if (!scene.end_to_end) {
    // lanelines
    for (int i = 0; i < std::size(scene.lane_line_vertices); ++i) {
      painter.setBrush(QColor::fromRgbF(1.0, 1.0, 1.0, scene.lane_line_probs[i]));
      painter.drawPolygon(scene.lane_line_vertices[i].v, scene.lane_line_vertices[i].cnt);
    }
    // road edges
    for (int i = 0; i < std::size(scene.road_edge_vertices); ++i) {
      painter.setBrush(QColor::fromRgbF(1.0, 0, 0, std::clamp<float>(1.0 - scene.road_edge_stds[i], 0.0, 1.0)));
      painter.drawPolygon(scene.road_edge_vertices[i].v, scene.road_edge_vertices[i].cnt);
    }
  }
  // paint path
  QLinearGradient bg(0, height(), 0, height() / 4);
  bg.setColorAt(0, scene.end_to_end ? redColor() : QColor(255, 255, 255));
  bg.setColorAt(1, scene.end_to_end ? redColor(0) : QColor(255, 255, 255, 0));
  painter.setBrush(bg);
  painter.drawPolygon(scene.track_vertices.v, scene.track_vertices.cnt);
}

void NvgWindow::drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data,
                         const cereal::ModelDataV2::XYZTData::Reader &line, const QPointF &vd, int radar_idx) {
  // 插入空白帧机制：每2帧一个周期，1帧显示文本，1帧空白
  // 这样可以：1. 让摄像头画面覆盖旧文本，避免重叠  2. 降低刷新率到10Hz，减少资源消耗
  lead_display_frame_counter++;
  bool should_display = (lead_display_frame_counter % 2) == 1;  // 奇数帧显示，偶数帧空白

  if (!should_display) {
    return;  // 空白帧，不绘制任何内容
  }

  if (lead_data.getProb() > .5) {
    // 获取自车速度
    UIState *s = uiState();
    float v_ego = (*s->sm)["carState"].getCarState().getVEgo();  // 自车速度 (m/s)

    // 摄像头到车头距离校正值（米）
    const float CAMERA_OFFSET = 1.52;  // 马自达摄像头在挡风玻璃位置，距离车头约1.52米

    // 获取前车数据（使用 modelV2 的 LeadsV3 数据）
    float x = lead_data.getX()[0];  // 纵向距离（米，在设备坐标系中）
    float y = lead_data.getY()[0];  // 横向偏移（米，在设备坐标系中）

    // 实际车距 = 模型距离 - 摄像头偏移
    float d_rel = x - CAMERA_OFFSET;

    // 只过滤掉负距离的检测（摄像头后方），不再设置0.5米的最小距离限制
    if (d_rel < 0) {
      return;  // 距离为负，前车在摄像头后方
    }

    float v_lead = lead_data.getV()[0];  // 前车绝对速度 (m/s)
    float v_rel = v_lead - v_ego;  // 相对速度 = 前车速度 - 自车速度 (m/s)

    // 获取匹配的雷达数据（通过radar_idx参数直接获取）
    float radar_d_rel = -1.0f;  // 雷达距离，-1表示无效
    float radar_v_rel = 0.0f;   // 雷达相对速度

    if (radar_idx >= 0 && s->sm->valid("liveTracks")) {
      auto live_tracks = (*s->sm)["liveTracks"].getLiveTracks();
      if (radar_idx < live_tracks.size()) {
        radar_d_rel = live_tracks[radar_idx].getDRel();
        radar_v_rel = live_tracks[radar_idx].getVRel();
      }
    }

    // 获取前车位置对应的路径高度（z坐标）
    // 使用路径数据来获取正确的z值
    const auto line_x = line.getX();
    const auto line_z = line.getZ();

    // 找到与前车距离最接近的路径点
    int path_idx = 0;
    for (int i = 0; i < line_x.size() && line_x[i] <= x; ++i) {
      path_idx = i;
    }
    float z = line_z[path_idx] + 1.22;  // 路径高度 + 车辆高度（假设前车中心高度约1.22米）

    // 使用系统的坐标转换函数将车辆坐标转换为屏幕坐标
    QPointF lead_screen_pos;
    bool visible = ui_project_point_to_screen(s, x, y, z, &lead_screen_pos);

    if (!visible) {
      // 前车不在可见区域内
      return;
    }

    // 绘制固定大小的红色三角箭头（向下指示，指向前车位置）
    const int arrow_size = 40;  // 固定的箭头大小，调大到40像素
    const int vertical_offset = 16;  // 整体向上偏移16像素（原6像素 + 新增10像素）
    QPointF arrow_top(lead_screen_pos.x(), lead_screen_pos.y() - vertical_offset);
    QPointF arrow_left(lead_screen_pos.x() - arrow_size / 2, lead_screen_pos.y() + arrow_size - vertical_offset);
    QPointF arrow_right(lead_screen_pos.x() + arrow_size / 2, lead_screen_pos.y() + arrow_size - vertical_offset);

    QPolygonF arrow;
    arrow << arrow_top << arrow_left << arrow_right;

    // 红色三角箭头，添加白色边框使其更清晰
    painter.setPen(QPen(QColor(255, 255, 255), 2));  // 白色边框
    painter.setBrush(QColor(255, 0, 0, 220));  // 纯红色填充
    painter.drawPolygon(arrow);

    // 绘制前车信息文本（在箭头下方）
    // v_rel > 0：前车更快（远离）; v_rel < 0：前车更慢（接近）
    // 构建显示文本：左边显示视觉距离，右边显示雷达距离
    QString lead_text;
    if (radar_d_rel > 0) {
      // 有雷达数据，显示视觉和雷达的距离及速度
      lead_text = QString("视觉: %1m  雷达: %2m\n速差: 视觉%3 雷达%4")
                      .arg(d_rel, 0, 'f', 1)
                      .arg(radar_d_rel, 0, 'f', 1)
                      .arg(v_rel, 0, 'f', 1)
                      .arg(radar_v_rel, 0, 'f', 1);
    } else {
      // 无雷达数据，显示调试信息
      bool lt_valid = s->sm->valid("liveTracks");
      int lt_size = 0;
      if (lt_valid) {
        auto live_tracks = (*s->sm)["liveTracks"].getLiveTracks();
        lt_size = live_tracks.size();
      }
      lead_text = QString("视觉: %1m\n速差: %2m/s\n[调试] LT: %3/%4")
                      .arg(d_rel, 0, 'f', 1)
                      .arg(v_rel, 0, 'f', 1)
                      .arg(lt_valid ? "有效" : "无效")
                      .arg(lt_size);
    }

    // 根据距离动态调整字体大小：近处大，远处小，但限制最小字体
    // 公式：基础字体20 - 距离*0.3，范围限制在[10, 20]之间（缩小2号）
    int font_size = std::clamp((int)(20 - d_rel * 0.3), 10, 20);
    QFont font = painter.font();
    font.setPointSize(font_size);
    font.setWeight(QFont::Light);  // 使用细体字体
    painter.setFont(font);

    QRectF text_rect = painter.boundingRect(QRectF(0, 0, 300, 100), Qt::AlignCenter, lead_text);

    // 文本位置：箭头底部下方-10像素（往上移动20像素，更靠近箭头）
    QPointF text_pos = QPointF(lead_screen_pos.x() - text_rect.width() / 2, lead_screen_pos.y() + arrow_size - 10 - vertical_offset);

    // 根据距离动态调整文字颜色：
    // 0-10米：纯红色 RGB(255, 0, 0)
    // 10-20米：红色→橙色（渐变）RGB(255, 0, 0) → RGB(255, 165, 0)
    // 20-40米：橙色→绿色（渐变）RGB(255, 165, 0) → RGB(0, 255, 0)
    // 40米以上：纯绿色 RGB(0, 255, 0)
    int red_value, green_value, blue_value = 0;

    if (d_rel <= 10.0) {
      // 0-10米：纯红色
      red_value = 255;
      green_value = 0;
    } else if (d_rel <= 20.0) {
      // 10-20米：红色→橙色，green从0增加到165
      red_value = 255;
      green_value = std::clamp((int)((d_rel - 10.0) * 16.5), 0, 165);
    } else if (d_rel <= 40.0) {
      // 20-40米：橙色→绿色，red从255减少到0，green从165增加到255
      red_value = std::clamp((int)(255 - (d_rel - 20.0) * 12.75), 0, 255);
      green_value = std::clamp((int)(165 + (d_rel - 20.0) * 4.5), 165, 255);
    } else {
      // 40米以上：纯绿色
      red_value = 0;
      green_value = 255;
    }

    painter.setPen(QColor(red_value, green_value, blue_value, 255));

    // 绘制文本，不添加背景
    painter.drawText(QRectF(text_pos, text_rect.size()), Qt::AlignCenter, lead_text);
  }
}

// ============================================================================
// 雷达多目标显示功能
// 功能：在屏幕上绘制雷达检测到的多个目标（最多6个）
// 特性：
//   1. 视觉+雷达融合：如果视觉和雷达检测到同一目标，记录匹配关系
//   2. 雷达独立目标：雷达单独检测到的其他目标，用蓝色圆圈单独绘制
//   3. 显示距离、速度、目标编号
// 返回：视觉目标索引 -> 雷达目标索引的映射
// ============================================================================
std::map<int, int> NvgWindow::drawRadarTargets(QPainter &painter, const cereal::ModelDataV2::XYZTData::Reader &line,
                                                const capnp::List<cereal::ModelDataV2::LeadDataV3>::Reader &leads) {
  std::map<int, int> vision_to_radar_map;  // 返回值：视觉索引 -> 雷达索引

  UIState *s = uiState();

  // 检查 liveTracks 消息是否有效
  if (!s->sm->valid("liveTracks")) {
    return vision_to_radar_map;
  }

  // 获取所有雷达目标
  auto live_tracks = (*s->sm)["liveTracks"].getLiveTracks();
  if (live_tracks.size() == 0) {
    return vision_to_radar_map;
  }

  // 获取视觉检测到的前车位置（用于判断是否融合显示）
  struct VisionTarget {
    float d_rel;  // 纵向距离
    float y_rel;  // 横向位置
    int lead_idx; // 视觉目标索引
  };
  std::vector<VisionTarget> vision_targets;

  const float CAMERA_OFFSET = 1.52;  // 摄像头到车头距离

  for (int i = 0; i < leads.size() && i < 3; i++) {
    if (leads[i].getProb() > .5) {
      float x = leads[i].getX()[0];
      float y = leads[i].getY()[0];
      float d_rel = x - CAMERA_OFFSET;
      if (d_rel > 0) {
        vision_targets.push_back({d_rel, y, i});
      }
    }
  }

  // ========== 一对一最优匹配算法 ==========
  // 对每个视觉目标，找到最接近的雷达目标进行融合
  // 这样可以避免多个雷达目标都被同一个视觉目标"吃掉"
  std::set<int> fused_radar_indices;  // 记录哪些雷达目标已被匹配

  for (const auto& vision : vision_targets) {
    int best_match_idx = -1;
    float best_match_score = std::numeric_limits<float>::max();

    // 遍历所有雷达目标，找到最接近的一个
    for (int i = 0; i < live_tracks.size() && i < 6; i++) {
      float radar_d_rel = live_tracks[i].getDRel();
      float radar_y_rel = live_tracks[i].getYRel();

      // 跳过无效目标
      if (radar_d_rel <= 0 || radar_d_rel > 150) {
        continue;
      }

      // 计算纵向和横向距离差
      float distance_diff = std::abs(radar_d_rel - vision.d_rel);
      float lateral_diff = std::abs(radar_y_rel - vision.y_rel);

      // 计算方位角差（用于远距离判断）
      float radar_angle = std::atan2(radar_y_rel, radar_d_rel) * 180.0f / M_PI;  // 转换为度
      float vision_angle = std::atan2(vision.y_rel, vision.d_rel) * 180.0f / M_PI;
      float angle_diff = std::abs(radar_angle - vision_angle);

      // 根据距离动态调整阈值和判断策略
      float distance_threshold;
      float lateral_threshold;
      float angle_threshold;
      bool use_angle_check;  // 是否使用角度判断

      if (radar_d_rel < 15.0f) {
        // 近距离：主要用横向位置判断，不用角度（角度误差大）
        distance_threshold = 2.0f;
        lateral_threshold = 2.0f;
        angle_threshold = 999.0f;  // 不限制角度
        use_angle_check = false;
      } else if (radar_d_rel < 40.0f) {
        // 中距离：纵向阈值增大，同时使用横向位置和角度判断
        distance_threshold = 2.0f + (radar_d_rel - 15.0f) * 0.12f;  // 2米 -> 5米
        lateral_threshold = 2.0f;
        angle_threshold = 5.0f;  // 角度差<5度
        use_angle_check = true;
      } else {
        // 远距离：主要用角度判断，横向位置阈值放宽
        distance_threshold = 5.0f + (radar_d_rel - 40.0f) * 0.05f;  // 5米 -> 8米
        distance_threshold = std::min(distance_threshold, 8.0f);
        lateral_threshold = 3.0f;  // 放宽到3米
        angle_threshold = 4.0f;    // 角度差<4度
        use_angle_check = true;
      }

      // 检查是否在阈值范围内
      bool distance_ok = distance_diff < distance_threshold;
      bool lateral_ok = lateral_diff < lateral_threshold;
      bool angle_ok = !use_angle_check || (angle_diff < angle_threshold);

      if (distance_ok && lateral_ok && angle_ok) {
        // 计算匹配分数（距离越近分数越低，越优）
        // 权重：距离差1.0，横向位置差2.0（最重要），角度差0.5
        float match_score;
        if (use_angle_check) {
          match_score = distance_diff + lateral_diff * 2.0f + angle_diff * 0.5f;
        } else {
          match_score = distance_diff + lateral_diff * 2.0f;
        }

        if (match_score < best_match_score) {
          best_match_score = match_score;
          best_match_idx = i;
        }
      }
    }

    // 如果找到了最佳匹配，记录映射关系
    if (best_match_idx >= 0) {
      fused_radar_indices.insert(best_match_idx);
      vision_to_radar_map[vision.lead_idx] = best_match_idx;  // 记录视觉索引 -> 雷达索引
    }
  }

  // 遍历所有雷达目标，绘制未被融合的目标
  for (int i = 0; i < live_tracks.size() && i < 6; i++) {
    float radar_d_rel = live_tracks[i].getDRel();
    float radar_y_rel = live_tracks[i].getYRel();
    float radar_v_rel = live_tracks[i].getVRel();

    // 过滤无效目标
    if (radar_d_rel <= 0 || radar_d_rel > 150) {
      continue;
    }

    // 如果这个雷达目标已被匹配（与视觉融合），跳过（已经在drawLead()中显示）
    if (fused_radar_indices.count(i) > 0) {
      continue;
    }

    // ========== 绘制雷达独立检测到的目标 ==========

    // 计算目标的3D位置
    float x = radar_d_rel;
    float y = radar_y_rel;

    // 获取路径高度（z坐标）
    const auto line_x = line.getX();
    const auto line_z = line.getZ();

    int path_idx = 0;
    for (int j = 0; j < line_x.size() && line_x[j] <= x; ++j) {
      path_idx = j;
    }
    float z = line_z[path_idx] + 1.22;

    // 将3D坐标转换为屏幕坐标
    QPointF screen_pos;
    bool visible = ui_project_point_to_screen(s, x, y, z, &screen_pos);

    if (!visible) {
      continue;
    }

    // 绘制蓝色圆圈（表示雷达目标）
    painter.setPen(QPen(QColor(0, 150, 255, 200), 3));
    painter.setBrush(QColor(0, 150, 255, 50));
    float circle_size = 30;
    painter.drawEllipse(screen_pos, circle_size, circle_size);

    // 绘制中心点
    painter.setBrush(QColor(0, 150, 255, 255));
    painter.drawEllipse(screen_pos, 5, 5);

    // 绘制距离和速度信息
    // 根据距离动态调整字体大小：近处大，远处小，但限制最小字体
    // 公式：基础字体20 - 距离*0.3，范围限制在[10, 20]之间
    int font_size = std::clamp((int)(20 - radar_d_rel * 0.3), 10, 20);
    QFont font = painter.font();
    font.setPointSize(font_size);
    font.setWeight(QFont::Light);  // 使用细体字体
    painter.setFont(font);
    painter.setPen(QColor(0, 150, 255, 255));

    QString radar_text = QString("R%1: %2m\n%3 km/h")
                            .arg(i + 1)
                            .arg(radar_d_rel, 0, 'f', 1)
                            .arg(radar_v_rel * 3.6, 0, 'f', 1);

    QRectF text_rect = painter.boundingRect(QRectF(0, 0, 200, 80), Qt::AlignCenter, radar_text);
    QPointF text_pos = QPointF(screen_pos.x() - text_rect.width() / 2,
                                screen_pos.y() + circle_size + 10);

    // 绘制文字（不添加背景，与视觉前车信息保持一致）
    painter.drawText(QRectF(text_pos, text_rect.size()), Qt::AlignCenter, radar_text);
  }

  return vision_to_radar_map;  // 返回视觉索引 -> 雷达索引的映射
}

void NvgWindow::paintGL() {
  CameraViewWidget::paintGL();

  UIState *s = uiState();
  if (s->worldObjectsVisible()) {
    QPainter painter(this);
    // 移除抗锯齿以提升性能，减少摄像头画面延迟
    // painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(Qt::NoPen);

    drawLaneLines(painter, s->scene);

    // 定期读取参数（每30帧约0.5秒读一次），避免每帧都创建 Params() 对象
    param_read_counter++;
    if (param_read_counter >= 30) {
      showCarInfo = Params().getBool("ShowCarInfoToggle");
      leadCarAlertEnabled = Params().getBool("LeadCarAlertToggle");
      bool prev_lateral_only = lateral_only_active;
      lateral_only_active = Params().getBool("LateralOnlyActive");  // 缓存横向模式状态
      if (prev_lateral_only != lateral_only_active) {
        LOG("[ButtonDebug] LateralOnlyActive changed: %d -> %d", prev_lateral_only, lateral_only_active);
      }
      param_read_counter = 0;
    }

    // 使用缓存的参数值，不再每帧创建 Params() 对象
    if (showCarInfo || leadCarAlertEnabled) {
      // 确保modelV2消息存在且有效
      if (s->sm->updated("modelV2")) {
        auto model_data = (*s->sm)["modelV2"].getModelV2();
        auto leads = model_data.getLeadsV3();
        auto line = model_data.getPosition();  // 获取路径数据

        // 确保leads数据存在且至少有一个元素
        if (leads.size() > 0) {
          double current_time = millis_since_boot(); // 获取当前时间

          // ========== 先执行雷达融合判断，获取匹配结果 ==========
          std::map<int, int> vision_to_radar_map;
          if (showCarInfo) {
            vision_to_radar_map = drawRadarTargets(painter, line, leads);
          }

          // ========== 再绘制视觉检测的前车信息（顶层） ==========
          // 遍历所有检测到的前车（最多3辆）
          for (int i = 0; i < leads.size() && i < 3; i++) {
            if (leads[i].getProb() > .5 && leads[i].getX()[0] > 0) {
              // 只对第一辆车（最近的）检查起步提醒
              if (i == 0 && leadCarAlertEnabled) {
                checkLeadCarAlert(leads[i], current_time);
              }

              // 显示所有检测到的前车信息
              if (showCarInfo) {
                // 传递路径数据和对应的 lead_vertices，以及匹配的雷达索引
                QPointF vd = (i < 2) ? s->scene.lead_vertices[i] : QPointF(0, 0);
                int radar_idx = (vision_to_radar_map.count(i) > 0) ? vision_to_radar_map[i] : -1;
                drawLead(painter, leads[i], line, vd, radar_idx);
              }
            }
          }
        }
      }
    }

    // 绘制前车起步提醒
    if (showLeadCarAlert) {
      drawLeadCarAlert(painter);
    }

    // 读取横向控制按钮开关（降低频率，每30帧读取一次）
    button_param_read_counter++;
    if (button_param_read_counter >= 30) {
      bool prev_enabled = lateral_only_button_enabled;
      lateral_only_button_enabled = Params().getBool("LateralOnlyControl");
      if (prev_enabled != lateral_only_button_enabled) {
        LOG("[ButtonDebug] LateralOnlyControl changed: %d -> %d", prev_enabled, lateral_only_button_enabled);
      }
      button_param_read_counter = 0;
    }

    // 绘制横向控制按钮
    if (lateral_only_button_enabled) {
      drawLateralButton(painter);
    }
  }

  double cur_draw_t = millis_since_boot();
  double dt = cur_draw_t - prev_draw_t;
  if (dt > 66) {
    // warn on sub 15fps
    // 降低日志频率：每10秒最多记录一次慢帧警告，但记录最大值
    static double last_slow_frame_warning = 0;
    static double max_frame_time = 0;

    // 记录10秒内的最大帧时间
    if (dt > max_frame_time) {
      max_frame_time = dt;
    }

    // 每10秒输出一次，显示这段时间内的最大帧时间
    if (cur_draw_t - last_slow_frame_warning > 10000) {
      LOGW("slow frame time: %.2f (max in last 10s: %.2f)", dt, max_frame_time);
      last_slow_frame_warning = cur_draw_t;
      max_frame_time = 0;  // 重置最大值
    }
  }
  prev_draw_t = cur_draw_t;
}

void NvgWindow::showEvent(QShowEvent *event) {
  CameraViewWidget::showEvent(event);

  ui_update_params(uiState());
  prev_draw_t = millis_since_boot();
}

void NvgWindow::checkLeadCarAlert(const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, double current_time) {
  // 获取自车速度
  UIState *s = uiState();
  float v_ego = (*s->sm)["carState"].getCarState().getVEgo();  // 自车速度 (m/s)

  // 摄像头到车头距离校正值（米）
  const float CAMERA_OFFSET = 1.52;  // 马自达摄像头在挡风玻璃位置，距离车头约1.52米

  float d_rel_camera = lead_data.getX()[0];  // 摄像头到前车的距离
  float d_rel_raw = d_rel_camera - CAMERA_OFFSET;  // 实际车距（原始值）
  float v_rel_raw = lead_data.getV()[0];  // 相对速度（原始值）
  float prob = lead_data.getProb();   // 检测概率

  // === 指数移动平均（EMA）滤波，减少数据抖动 ===
  const float ALPHA_DISTANCE = 0.4;  // 车距滤波系数（0-1，越大响应越快）
  const float ALPHA_VELOCITY = 0.5;  // 速度滤波系数

  if (filtered_d_rel < 0) {
    // 首次检测，直接使用原始值初始化
    filtered_d_rel = d_rel_raw;
    filtered_v_rel = v_rel_raw;
    lead_stable_count = 0;
  } else {
    // 应用指数移动平均滤波
    filtered_d_rel = ALPHA_DISTANCE * d_rel_raw + (1.0 - ALPHA_DISTANCE) * filtered_d_rel;
    filtered_v_rel = ALPHA_VELOCITY * v_rel_raw + (1.0 - ALPHA_VELOCITY) * filtered_v_rel;

    // 稳定性检测：如果滤波值变化不大，增加稳定计数
    if (fabs(d_rel_raw - filtered_d_rel) < 0.5 && fabs(v_rel_raw - filtered_v_rel) < 0.3) {
      lead_stable_count = std::min(lead_stable_count + 1, 10);
    } else {
      lead_stable_count = std::max(lead_stable_count - 1, 0);
    }
  }

  // === 前车起步提醒逻辑（使用滤波后的数据） ===
  // 距离范围说明：
  // - 开始监测：0.5m < d_rel < 4.6m（严格条件，确保近距离才开始记录）
  // - 保持监测：0.5m < d_rel < 7.0m（宽松条件，允许溜车等情况下的距离波动）
  // 要求：检测概率高、数据稳定、本车静止

  // 判断是否在保持监测范围内（允许已记录的状态继续有效）
  bool in_monitoring_range = (prob > 0.5 && filtered_d_rel > 0.5 && filtered_d_rel < 7.0 &&
                              lead_stable_count > 3 && v_ego < 0.5);

  // 判断是否在开始监测范围内（严格条件，用于开始新的停止记录）
  bool in_start_range = (prob > 0.5 && filtered_d_rel > 0.5 && filtered_d_rel < 4.6 &&
                         lead_stable_count > 3 && v_ego < 0.5);

  if (in_monitoring_range) {
    // 本车车速接近0（小于0.5 m/s，约1.8 km/h）
    if (filtered_v_rel < 0.5) {  // 前车相对静止或缓慢移动（前车速度 <= 自车速度）
      if (!leadCarStopped && in_start_range) {
        // 只有在严格范围内才开始记录新的停止状态
        leadCarStopped = true;
        leadCarStopTime = current_time;
        qInfo() << "Lead car stopped at distance:" << filtered_d_rel << "m";
      }
      // 前车保持停止，重置移动相关状态
      leadCarMoving = false;
      leadCarStartMovingDistance = 0.0;

      // 如果前车已经停止超过20秒，重置提醒状态，准备下一次提醒
      if (leadCarStopped && current_time - leadCarStopTime > 20000 && leadCarAlertTriggered) {
        leadCarAlertTriggered = false;
      }
    } else if (filtered_v_rel > 1.0) {  // 前车开始移动（相对速度 > 1.0 m/s）
      if (leadCarStopped && current_time - leadCarStopTime > 20000) {  // 前车之前停止且停止时间超过20秒
        if (!leadCarMoving) {
          // 前车刚开始移动，记录起始距离
          leadCarMoving = true;
          leadCarStartMovingDistance = filtered_d_rel;
          qInfo() << "Lead car started moving, initial distance:" << leadCarStartMovingDistance;
        } else {
          // 前车持续移动，检查是否移动了超过2米
          // 注意：前车远离时，filtered_d_rel会增大
          float distance_moved = filtered_d_rel - leadCarStartMovingDistance;
          if (distance_moved > 2.0) {  // 前车远离超过2米
            if (!leadCarAlertTriggered) {
              // 触发前车起步提醒
              leadCarAlertTriggered = true;
              leadCarAlertStartTime = current_time;
              showLeadCarAlert = true;
              qInfo() << "Lead car alert triggered! Distance moved:" << distance_moved << "m";

              // 播放预加载的声音（参考soundd的做法）
              if (leadCarAlertSound && leadCarAlertSound->status() == QSoundEffect::Ready) {
                leadCarAlertSound->play();
                qInfo() << "Playing lead car alert sound";
              } else {
                qWarning() << "Lead car alert sound not ready, status:" << (leadCarAlertSound ? leadCarAlertSound->status() : -1);
              }
            }
            // 提醒触发后，重置状态
            leadCarStopped = false;
            leadCarMoving = false;
          }
        }
      } else {
        // 前车移动但条件不满足（没有停止过或停止时间不够），重置状态
        leadCarStopped = false;
        leadCarMoving = false;
        leadCarStartMovingDistance = 0.0;
      }
    } else {
      // 相对速度在0.5-1.0之间，可能是缓慢移动或数据抖动，保持当前状态不变
      // 不重置leadCarStopped和leadCarMoving，继续观察
    }
  } else {
    // 距离太远（>7m）、检测概率低、本车在移动、或数据不稳定，重置状态
    if (prob < 0.3 || filtered_d_rel > 8.0 || filtered_d_rel < 0 || v_ego > 0.5) {
      // 完全丢失前车或本车开始移动，或距离过远（>8米），重置滤波器和所有状态
      filtered_d_rel = -1.0;
      filtered_v_rel = 0.0;
      lead_stable_count = 0;
      leadCarStopped = false;
      leadCarMoving = false;
      leadCarStartMovingDistance = 0.0;
      leadCarAlertTriggered = false;
    }
    // 注意：不在这里重置 showLeadCarAlert，让它显示完整的3秒
  }

  // 检查是否需要隐藏提醒（显示3秒后隐藏）
  if (showLeadCarAlert && current_time - leadCarAlertStartTime > 3000) {
    showLeadCarAlert = false;
  }
}

void NvgWindow::drawLeadCarAlert(QPainter &painter) {
  int w = width();
  int h = height();

  // 计算背景位置和大小 - 更大的背景和更宽松的布局
  int bg_y = h/3 - 110;  // 背景起点，向上移动110像素
  int bg_height = 440;   // 背景高度440像素，非常宽松

  // 绘制半透明绿色背景
  painter.fillRect(0, bg_y, w, bg_height, QColor(0, 0x80, 0, 255));

  // 设置抗锯齿
  painter.setRenderHint(QPainter::TextAntialiasing);

  // === 第一行：大字"前车起步" ===
  painter.setPen(QColor(0xff, 0xff, 0xff));  // 白色文字
  QFont font = painter.font();
  font.setPointSize(36);  // 36号字体
  font.setBold(true);
  painter.setFont(font);

  // 第一行文字区域：从背景顶部100像素开始，高度180像素（给足空间）
  // 使用 Qt::AlignVCenter 垂直居中对齐，避免被裁剪
  QString alertText = "前车起步";
  QRectF textRect(0, bg_y + 100, w, 180);
  painter.drawText(textRect, Qt::AlignHCenter | Qt::AlignVCenter, alertText);

  // === 第二行：小字"请注意跟车距离" ===
  font.setPointSize(18);  // 18号字体
  font.setBold(false);
  painter.setFont(font);
  painter.setPen(QColor(0xff, 0xff, 0xff));

  // 第二行文字区域：从300像素位置开始，高度120像素，完全在背景内
  QString detailText = "请注意跟车距离";
  QRectF detailRect(0, bg_y + 300, w, 120);
  painter.drawText(detailRect, Qt::AlignHCenter | Qt::AlignVCenter, detailText);
}

void NvgWindow::drawLateralButton(QPainter &painter) {
  // 按钮位置：右下角，距离边缘100像素
  const int button_size = 180;
  const int margin = 100;

  int x = width() - margin - button_size / 2;
  int y = height() - margin - button_size / 2;

  // 更新按钮区域供点击检测使用
  lateral_button_rect = QRect(x - button_size / 2, y - button_size / 2, button_size, button_size);

  // 直接读取系统实际状态（不读参数）
  UIState *s = uiState();
  const SubMaster &sm = *(s->sm);
  bool acc_enabled = false;
  bool engageable = true;
  bool controls_enabled = false;  // 系统实际控制状态
  bool is_lateral_only = lateral_only_active;  // 使用缓存的参数值，避免每帧读取文件系统

  if (sm.valid("carState")) {
    acc_enabled = sm["carState"].getCarState().getCruiseState().getEnabled();
  }

  if (sm.valid("controlsState")) {
    auto cs = sm["controlsState"].getControlsState();
    engageable = cs.getEngageable();
    controls_enabled = cs.getEnabled();
    // 从 controlsState 读取横向模式状态（由 controlsd 设置）
    // TODO: 需要在 controlsState 中添加 lateralOnly 字段
  }

  // 事件驱动日志：只在状态变化时记录，不使用固定间隔
  static bool last_is_lateral_only = false;
  static bool last_controls_enabled = false;
  static bool last_acc_enabled = false;
  static bool last_engageable = true;
  static bool first_log = true;

  if (first_log ||
      is_lateral_only != last_is_lateral_only ||
      controls_enabled != last_controls_enabled ||
      acc_enabled != last_acc_enabled ||
      engageable != last_engageable) {

    LOG("[ButtonDebug] State changed: lateral_only=%d->%d, controls=%d->%d, acc=%d->%d, engageable=%d->%d",
         last_is_lateral_only, is_lateral_only,
         last_controls_enabled, controls_enabled,
         last_acc_enabled, acc_enabled,
         last_engageable, engageable);

    last_is_lateral_only = is_lateral_only;
    last_controls_enabled = controls_enabled;
    last_acc_enabled = acc_enabled;
    last_engageable = engageable;
    first_log = false;
  }

  // is_lateral_only 已经在上面使用缓存的 lateral_only_active 赋值，不再需要每帧读取参数

  // 设置抗锯齿
  painter.setRenderHint(QPainter::Antialiasing);

  // 按钮颜色基于横向模式的实际可用性
  QColor fillColor;
  QString buttonText;

  // 判断横向模式是否可用：
  // 1. ACC未启用（避免冲突）
  // 2. 系统可启用 或 已经在横向模式中
  bool lateral_available = !acc_enabled && (engageable || is_lateral_only);

  if (is_lateral_only && controls_enabled) {
    // 横向模式激活中
    fillColor = QColor(144, 238, 144, 150);  // 半透明浅绿色
    buttonText = "横向\n激活";
  } else if (acc_enabled) {
    // ACC启用时不可用
    fillColor = QColor(255, 165, 0, 100);  // 半透明橙色
    buttonText = "横向\n控制";
  } else if (!lateral_available) {
    // 横向模式不可用（系统有严重错误）
    fillColor = QColor(255, 0, 0, 100);  // 半透明红色
    buttonText = "横向\n控制";
  } else {
    // 未激活但可用状态
    fillColor = QColor(200, 200, 200, 100);  // 半透明浅灰色
    buttonText = "横向\n控制";
  }

  // 绘制圆形按钮
  painter.setPen(QPen(QColor(255, 255, 255), 6));  // 增加边框厚度到6像素（原来3像素）
  painter.setBrush(fillColor);
  painter.drawEllipse(QPoint(x, y), button_size / 2, button_size / 2);

  // 绘制按钮文字
  painter.setPen(QColor(255, 255, 255));
  QFont font = painter.font();
  font.setPointSize(11);  // 缩小3号（原来14，现在11）
  font.setWeight(QFont::Light);  // 使用细字体
  painter.setFont(font);

  painter.drawText(QRect(x - button_size / 2, y - button_size / 2, button_size, button_size),
                   Qt::AlignCenter, buttonText);
}

bool NvgWindow::isPointInLateralButton(const QPoint &pos) {
  if (lateral_button_rect.isEmpty()) {
    return false;
  }

  // 计算点到圆心的距离
  int cx = lateral_button_rect.center().x();
  int cy = lateral_button_rect.center().y();
  int radius = lateral_button_rect.width() / 2;

  int dx = pos.x() - cx;
  int dy = pos.y() - cy;
  int distance_sq = dx * dx + dy * dy;

  return distance_sq <= radius * radius;
}

void NvgWindow::handleLateralButtonClick() {
  // 点击按钮：检查前置条件，然后切换参数
  UIState *s = uiState();
  const SubMaster &sm = *(s->sm);
  bool current_active = Params().getBool("LateralOnlyActive");

  LOG("[ButtonDebug] Button clicked: current_active=%d", current_active);

  if (current_active) {
    // 当前已激活，点击取消（无条件允许）
    Params().remove("LateralOnlyActive");
    LOG("[ButtonDebug] Lateral-only mode: user requested deactivation");
  } else {
    // 当前未激活，检查前置条件
    bool acc_enabled = false;
    if (sm.valid("carState")) {
      auto carState = sm["carState"].getCarState();
      acc_enabled = carState.getCruiseState().getEnabled();

      // 检查是否挂P档
      auto gear = carState.getGearShifter();
      bool in_park = (gear == cereal::CarState::GearShifter::PARK);

      if (in_park) {
        LOGW("[ButtonDebug] Cannot activate lateral-only mode: vehicle in PARK");
        return;
      }
    }

    if (acc_enabled) {
      LOGW("[ButtonDebug] Cannot activate lateral-only mode: ACC already enabled");
      return;
    }

    // 前置条件满足，写入参数请求激活
    Params().putBool("LateralOnlyActive", true);
    LOG("[ButtonDebug] Lateral-only mode: user requested activation");
  }

  update();  // 重绘界面
}

void NvgWindow::mousePressEvent(QMouseEvent* e) {
  LOGD("[ButtonDebug] Mouse pressed at (%d, %d), button_enabled=%d",
       e->pos().x(), e->pos().y(), lateral_only_button_enabled);

  // 检查是否点击了横向控制按钮
  if (lateral_only_button_enabled && isPointInLateralButton(e->pos())) {
    LOG("[ButtonDebug] Lateral button clicked!");
    handleLateralButtonClick();
    return;  // 不传递事件
  }

  // 如果不是点击按钮，传递事件给父类处理
  CameraViewWidget::mousePressEvent(e);
}

// OnroadWindow 的按钮绘制和点击检测已移至 NvgWindow，这里不再需要
/*
void OnroadWindow::drawLateralButton(QPainter &p) {
  // 按钮位置：右下角，距离边缘100像素
  const int button_size = 120;  // 按钮直径
  const int margin = 100;       // 距离边缘的距离

  int x = width() - margin - button_size / 2;
  int y = height() - margin - button_size / 2;

  // 更新按钮区域供点击检测使用
  lateral_button_rect = QRect(x - button_size / 2, y - button_size / 2, button_size, button_size);

  // 检查系统状态
  const SubMaster &sm = *(uiState()->sm);
  bool acc_enabled = false;
  bool engageable = true;

  if (sm.valid("carState")) {
    acc_enabled = sm["carState"].getCarState().getCruiseState().getEnabled();
  }

  if (sm.valid("controlsState")) {
    engageable = sm["controlsState"].getControlsState().getEngageable();
  }

  // 设置抗锯齿
  p.setRenderHint(QPainter::Antialiasing);

  // 根据状态设置颜色
  QColor fillColor;
  if (acc_enabled) {
    // 原车ACC启用时，橙色不可用
    fillColor = QColor(255, 165, 0, 100);  // 半透明橙色
  } else if (!engageable) {
    // TI不可用时，红色警告
    fillColor = QColor(255, 0, 0, 100);  // 半透明红色
  } else if (lateral_only_active) {
    // 激活状态：半透明浅绿色
    fillColor = QColor(144, 238, 144, 150);  // 半透明浅绿色，增加透明度
  } else {
    // 未激活状态：半透明浅灰色
    fillColor = QColor(200, 200, 200, 100);  // 半透明浅灰色
  }

  // 绘制圆形背景
  p.setPen(QPen(Qt::white, 3));  // 白色边框，宽度3像素
  p.setBrush(fillColor);
  p.drawEllipse(QPoint(x, y), button_size / 2, button_size / 2);

  // 绘制文字："横向"
  p.setPen(Qt::white);
  QFont font = p.font();
  font.setPointSize(20);
  font.setBold(true);
  p.setFont(font);

  QRect textRect(x - button_size / 2, y - 20, button_size, 40);
  p.drawText(textRect, Qt::AlignCenter, "横向");
}

bool OnroadWindow::isPointInLateralButton(const QPoint &pos) {
  if (lateral_button_rect.isEmpty()) {
    return false;
  }

  // 计算点到圆心的距离
  int cx = lateral_button_rect.center().x();
  int cy = lateral_button_rect.center().y();
  int radius = lateral_button_rect.width() / 2;

  int dx = pos.x() - cx;
  int dy = pos.y() - cy;
  int distance_sq = dx * dx + dy * dy;

  return distance_sq <= radius * radius;
}
*/
