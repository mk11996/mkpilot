#pragma once

#include <QJsonDocument>
#include <QLabel>

class DriveStats : public QFrame {
  Q_OBJECT

public:
  explicit DriveStats(QWidget* parent = 0);

private:
  void showEvent(QShowEvent *event) override;
  void loadLocalStats();
  void updateStats();
  inline QString getDistanceUnit() const { return metric_ ? "公里" : "英里"; }

  bool metric_;
  QJsonDocument stats_;
  struct StatsLabels {
    QLabel *routes, *distance, *distance_unit, *hours;
  } all_, week_;

  // 已移除云端同步相关的 parseResponse slot
};
