#include "selfdrive/ui/qt/widgets/drive_stats.h"

#include <QDebug>
#include <QGridLayout>
#include <QJsonObject>
#include <QVBoxLayout>

#include "selfdrive/common/params.h"
#include "selfdrive/common/util.h"
#include "selfdrive/ui/qt/request_repeater.h"
#include "selfdrive/ui/qt/util.h"

static QLabel* newLabel(const QString& text, const QString &type) {
  QLabel* label = new QLabel(text);
  label->setProperty("type", type);
  return label;
}

DriveStats::DriveStats(QWidget* parent) : QFrame(parent) {
  metric_ = Params().getBool("IsMetric");

  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(50, 50, 50, 60);

  auto add_stats_layouts = [=](const QString &title, StatsLabels& labels) {
    QGridLayout* grid_layout = new QGridLayout;
    grid_layout->setVerticalSpacing(10);
    grid_layout->setContentsMargins(0, 10, 0, 10);

    int row = 0;
    grid_layout->addWidget(newLabel(title, "title"), row++, 0, 1, 3);
    grid_layout->addItem(new QSpacerItem(0, 50), row++, 0, 1, 1);

    grid_layout->addWidget(labels.routes = newLabel("0", "number"), row, 0, Qt::AlignLeft);
    grid_layout->addWidget(labels.distance = newLabel("0", "number"), row, 1, Qt::AlignLeft);
    grid_layout->addWidget(labels.hours = newLabel("0", "number"), row, 2, Qt::AlignLeft);

    grid_layout->addWidget(newLabel("行程", "unit"), row + 1, 0, Qt::AlignLeft);
    grid_layout->addWidget(labels.distance_unit = newLabel(getDistanceUnit(), "unit"), row + 1, 1, Qt::AlignLeft);
    grid_layout->addWidget(newLabel("小时", "unit"), row + 1, 2, Qt::AlignLeft);

    main_layout->addLayout(grid_layout);
  };

  add_stats_layouts("总计", all_);
  main_layout->addStretch();
  add_stats_layouts("过去一周", week_);

  // 只使用本地统计数据，不再从云端同步
  loadLocalStats();

  // 已移除云端同步逻辑
  // 原因：
  // 1. 不需要联网功能
  // 2. 本地统计（statsd_local.py）已经提供完整的离线统计功能
  // 3. 云端API可能需要认证/dongleId，增加复杂度

  setStyleSheet(R"(
    DriveStats {
      background-color: #333333;
      border-radius: 10px;
    }

    QLabel[type="title"] { font-size: 51px; font-weight: 500; }
    QLabel[type="number"] { font-size: 78px; font-weight: 500; }
    QLabel[type="unit"] { font-size: 51px; font-weight: 300; color: #A0A0A0; }
  )");
}

void DriveStats::loadLocalStats() {
  Params params;

  auto load = [&](const char* key) -> QJsonObject {
    QJsonObject obj;
    std::string data = params.get(key);
    if (!data.empty()) {
      QJsonDocument doc = QJsonDocument::fromJson(QByteArray::fromStdString(data));
      if (!doc.isNull() && doc.isObject()) {
        obj = doc.object();
      }
    }
    // 如果没有数据，返回默认值
    if (obj.isEmpty()) {
      obj["routes"] = 0;
      obj["distance"] = 0.0;
      obj["minutes"] = 0.0;
    }
    return obj;
  };

  QJsonObject local_all = load("StatsAllTime");
  QJsonObject local_week = load("StatsWeek");

  // 构造 stats_ 文档
  QJsonObject stats_obj;
  stats_obj["all"] = local_all;
  stats_obj["week"] = local_week;
  stats_ = QJsonDocument(stats_obj);

  updateStats();
}

void DriveStats::updateStats() {
  auto update = [=](const QJsonObject& obj, StatsLabels& labels) {
    labels.routes->setText(QString::number((int)obj["routes"].toDouble()));

    // 距离从米转换为公里或英里
    double distance_meters = obj["distance"].toDouble();
    double distance_km = distance_meters / 1000.0;  // 米转公里
    double distance_display = metric_ ? distance_km : (distance_km * KM_TO_MILE);  // 如果是英制，转换为英里

    labels.distance->setText(QString::number((int)distance_display));
    labels.distance_unit->setText(getDistanceUnit());
    labels.hours->setText(QString::number((int)(obj["minutes"].toDouble() / 60)));
  };

  QJsonObject json = stats_.object();
  update(json["all"].toObject(), all_);
  update(json["week"].toObject(), week_);
}

// 已移除 parseResponse 方法（不再需要云端同步）

void DriveStats::showEvent(QShowEvent* event) {
  bool metric = Params().getBool("IsMetric");
  if (metric_ != metric) {
    metric_ = metric;
    updateStats();
  }

  // 每次显示时重新加载本地数据（确保数据是最新的）
  loadLocalStats();
}
