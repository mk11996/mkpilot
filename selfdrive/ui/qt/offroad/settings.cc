#include "selfdrive/ui/qt/offroad/settings.h"

#include <cassert>
#include <cmath>
#include <string>

#include <QDebug>
#include <QProcess>
#include <QTextEdit>
#include <QVBoxLayout>
#include <QScrollBar>

#ifndef QCOM
#include "selfdrive/ui/qt/offroad/networking.h"
#endif

#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map_settings.h"
#endif

#include "selfdrive/common/params.h"
#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"
#include "selfdrive/ui/qt/widgets/toggle.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/qt_window.h"

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // param, title, desc, icon
  std::vector<std::tuple<QString, QString, QString, QString>> toggles{
    {
      "OpenpilotEnabledToggle",
      "启用 openpilot",
      "使用 openpilot 的自适应巡航功能和车道保持功能，开启后您需要保持注意力集中，设置变更在重新启动车辆后生效",
      "../assets/offroad/icon_openpilot.png",
    },
    {
      "IsLdwEnabled",
      "车道偏离预警",
      "车速在 50 km/h 以上，且未打转向灯的情況下，如果检查到车辆驶出目前车道线时，则会发出车道偏离警告。",
      "../assets/offroad/icon_warning.png",
    },
    {
      "IsRHD",
      "右舵模式",
      "允许 openpilot 遵守靠左行駛的交通规则，同時对右侧驾驶员进行监控",
      "../assets/offroad/icon_openpilot_mirrored.png",
    },
    {
      "IsMetric",
      "公制单位",
      "启用后，速度单位显示未 km/h，否则显示 mph。",
      "../assets/offroad/icon_metric.png",
    },
    {
      "RecordFront",
      "记录上传驾驶员录像",
      "上传前置镜头的影像来协助我们提升件使用检测的准确率。",
      "../assets/offroad/icon_monitoring.png",
    },
    {
      "EndToEndToggle",
      "\U0001f96c Disable use of lanelines (Alpha) \U0001f96c",
      "在此模式下，OpenPilot将忽略车道线，仅按照其认为人类驾驶员会采取的方式驾驶。",
      "../assets/offroad/icon_road.png",
    },
#ifdef ENABLE_MAPS
    {
      "NavSettingTime24h",
      "Show ETA in 24h format",
      "Use 24h format instead of am/pm",
      "../assets/offroad/icon_metric.png",
    },
#endif

  };

  Params params;

  if (params.getBool("DisableRadar_Allow")) {
    toggles.push_back({
      "DisableRadar",
      "openpilot Longitudinal Control",
      "openpilot will disable the car's radar and will take over control of gas and brakes. Warning: this disables AEB!",
      "../assets/offroad/icon_speed_limit.png",
    });
  }

  for (auto &[param, title, desc, icon] : toggles) {
    auto toggle = new ParamControl(param, title, desc, icon, this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    if (!locked) {
      // 修改这部分逻辑，不再完全依赖offroadTransition信号来控制开关启用状态
      // 保留原有的锁定逻辑，但允许某些非关键设置在onroad状态下也能修改
      QString paramCopy = param;  // 创建副本以在lambda中使用
      connect(uiState(), &UIState::offroadTransition, this, [toggle, paramCopy]() {
        // 对于某些特定的设置（如ShowCarInfoToggle, LeadCarAlertToggle等），允许在onroad状态下修改
        QString paramName = paramCopy;
        bool allowModifyOnroad = (paramName == "ShowCarInfoToggle" || paramName == "LeadCarAlertToggle" || 
                                 paramName == "EndToEndToggle" || paramName == "IsLdwEnabled" ||
                                 paramName == "RecordFront" || paramName == "IsRHD" || paramName == "IsMetric");
        
        bool isLocked = Params().getBool((paramCopy + "Lock").toStdString());
        toggle->setEnabled(!isLocked && (allowModifyOnroad || !uiState()->scene.started));
      });
    }
    addItem(toggle);
  }
}

AdvancedPanel::AdvancedPanel(SettingsWindow *parent) : ListWidget(parent) {
  // 添加"仅横向控制"开关
  std::vector<std::tuple<QString, QString, QString, QString>> advanced_toggles{
    {
      "LateralOnlyControl",
      "仅横向控制",
      "启用后可以在驾驶时手动激活仅横向控制模式，openpilot只控制转向，加减速由驾驶员控制",
      "../assets/offroad/icon_road.png",
    },
    {
      "DriverMonitoringEnabled",
      "驾驶员监控",
      "启用驾驶员注意力监控系统。关闭后可节省系统资源（约10% CPU），但会降低安全性。⚠️ 警告：关闭后系统将无法检测驾驶员注意力状态",
      "../assets/offroad/icon_warning.png",
    },
    {
      "BorderIndicatorsToggle",
      "边框指示器",
      "在屏幕边框显示转向灯（绿色闪烁）、盲点检测（橙色常亮）、盲点报警（红色闪烁）和刹车状态（顶部红色条）",
      "../assets/offroad/icon_warning.png",
    },
    {
      "ShowCarInfoToggle",
      "前车信息显示",
      "开启后将在屏幕上显示前车距离和相对速度信息",
      "../assets/offroad/icon_speed_limit.png",
    },
    {
      "LeadCarAlertToggle",
      "前车起步提醒",
      "当前车距离小于3米且停止超过20秒后起步，发出视觉和声音警告提醒",
      "../assets/offroad/icon_network.png",
    },
    {
      "KeepIStopDisabled",
      "保持I-STOP关闭",
      "马自达车型专用：启用后在车辆启动时自动关闭i-STOP自动启停功能，openpilot启动1分钟后开始生效",
      "../assets/offroad/icon_openpilot.png",
    },
    {
      "EnableLdwVibration",
      "车道偏离方向盘震动",
      "马自达车型专用：启用后当检测到车道偏离时，会触发方向盘震动警告（需要车辆支持此功能）",
      "../assets/offroad/icon_warning.png",
    },
  };

  Params params;
  for (auto &[param, title, desc, icon] : advanced_toggles) {
    auto toggle = new ParamControl(param, title, desc, icon, this);
    addItem(toggle);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(50);
  addItem(new LabelControl("Dongle ID", getDongleId().value_or("N/A")));
  addItem(new LabelControl("序列号", params.get("HardwareSerial").c_str()));

  // offroad-only buttons

  auto dcamBtn = new ButtonControl("驾驶员摄像头", "预览",
                                   "预览驾驶员监控摄像头画面，方便调整装置装载位置，更好地使用驾驶员监控功能（仅限停车时使用）。");
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });
  addItem(dcamBtn);

  auto resetCalibBtn = new ButtonControl("重新校准", "重置", " ");
  connect(resetCalibBtn, &ButtonControl::showDescription, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm("确定要重新校准吗 ?", this)) {
      params.remove("CalibrationParams");
    }
  });
  addItem(resetCalibBtn);

  auto clearStatsBtn = new ButtonControl("清除本地行程统计", "清除", "清除所有本地保存的行程统计数据（总计和周统计）");
  connect(clearStatsBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm("确定要清除所有本地行程统计数据吗？\n此操作不可恢复！", this)) {
      params.remove("StatsAllTime");
      params.remove("StatsWeek");
      params.remove("StatsLastReset");
      ConfirmationDialog::alert("本地行程统计已清除", this);
    }
  });
  addItem(clearStatsBtn);

  auto flashPandaBtn = new ButtonControl("刷新 Panda 固件", "刷写",
                                         "重新编译并刷写Panda固件。此操作会停止相关进程，需要几分钟时间。");
  flashPandaBtn->setObjectName("flashPandaBtn");  // 设置对象名以便在禁用逻辑中识别
  connect(flashPandaBtn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm("确定要刷写Panda固件吗？\n此操作需要几分钟时间，请确保车辆已熄火。", this)) {
      flashPanda();
    }
  });
  addItem(flashPandaBtn);

  if (!params.getBool("Passive")) {
    auto retrainingBtn = new ButtonControl("回顾教程", "查看", "查看 OpenPilot 的规则、功能和限制");
    connect(retrainingBtn, &ButtonControl::clicked, [=]() {
      if (ConfirmationDialog::confirm("确认要查看操作教程吗？", this)) {
        emit reviewTrainingGuide();
      }
    });
    addItem(retrainingBtn);
  }

  if (Hardware::TICI()) {
    auto regulatoryBtn = new ButtonControl("Regulatory", "VIEW", "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file("../assets/offroad/fcc.html");
      RichTextDialog::alert(QString::fromStdString(txt), this);
    });
    addItem(regulatoryBtn);
  }

  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    for (auto btn : findChildren<ButtonControl *>()) {
      // 排除 Panda 刷写按钮，允许在任何状态下执行（但建议熄火操作）
      if (btn->objectName() != "flashPandaBtn") {
        btn->setEnabled(offroad);
      }
    }
  });

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  QPushButton *reboot_btn = new QPushButton("重启");
  reboot_btn->setObjectName("reboot_btn");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::clicked, this, &DevicePanel::reboot);

  QPushButton *poweroff_btn = new QPushButton("关机");
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, this, &DevicePanel::poweroff);

  if (Hardware::TICI()) {
    connect(uiState(), &UIState::offroadTransition, poweroff_btn, &QPushButton::setVisible);
  }

  setStyleSheet(R"(
    #reboot_btn { height: 120px; border-radius: 15px; background-color: #393939; }
    #reboot_btn:pressed { background-color: #4a4a4a; }
    #poweroff_btn { height: 120px; border-radius: 15px; background-color: #E22C2C; }
    #poweroff_btn:pressed { background-color: #FF2424; }
  )");
  addItem(power_layout);
}

void DevicePanel::updateCalibDescription() {
  QString desc =
      "openpilot requires the device to be mounted within 4° left or right and "
      "within 5° up or 8° down. openpilot is continuously calibrating, resetting is rarely required.";
  std::string calib_bytes = Params().get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != 0) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        desc += QString(" Your device is pointed %1° %2 and %3° %4.")
                    .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? "down" : "up",
                         QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? "left" : "right");
      }
    } catch (kj::Exception) {
      qInfo() << "invalid CalibrationParams";
    }
  }
  qobject_cast<ButtonControl *>(sender())->setDescription(desc);
}

void DevicePanel::reboot() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm("确认要重启吗?", this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        Params().putBool("DoReboot", true);
      }
    }
  } else {
    ConfirmationDialog::alert("Disengage to Reboot", this);
  }
}

void DevicePanel::poweroff() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm("确认要关机吗?", this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        Params().putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert("Disengage to Power Off", this);
  }
}

void DevicePanel::flashPanda() {
  // 简化检测逻辑：仅提示用户确认，由刷写脚本自行处理连接问题
  
  // 创建一个对话框显示刷写过程
  QDialog *dialog = new QDialog(this);
  dialog->setWindowTitle("刷写 Panda 固件");
  dialog->setMinimumSize(1400, 900);  // 增大窗口：1400x900
  dialog->setModal(true);

  QVBoxLayout *layout = new QVBoxLayout(dialog);

  QLabel *statusLabel = new QLabel("正在准备刷写...", dialog);
  statusLabel->setStyleSheet("font-size: 32px; padding: 20px; font-weight: bold;");  // 增大字体：32px
  layout->addWidget(statusLabel);

  QTextEdit *outputText = new QTextEdit(dialog);
  outputText->setReadOnly(true);
  outputText->setStyleSheet("font-family: monospace; font-size: 20px; background-color: #000; color: #0f0; padding: 10px;");  // 增大字体：20px
  layout->addWidget(outputText);

  QPushButton *closeBtn = new QPushButton("关闭", dialog);
  closeBtn->setEnabled(false);
  closeBtn->setStyleSheet("font-size: 24px; padding: 20px; min-height: 80px;");  // 增大按钮字体和高度
  layout->addWidget(closeBtn);

  connect(closeBtn, &QPushButton::clicked, dialog, &QDialog::close);

  dialog->show();

  // 创建进程执行刷写脚本
  QProcess *process = new QProcess(dialog);

  // 添加进程启动错误处理
  connect(process, &QProcess::errorOccurred, [=](QProcess::ProcessError error) {
    QString errorMsg;
    switch(error) {
      case QProcess::FailedToStart:
        errorMsg = "进程启动失败！请检查 /bin/sh 是否存在。";
        break;
      case QProcess::Crashed:
        errorMsg = "进程崩溃！";
        break;
      case QProcess::Timedout:
        errorMsg = "进程超时！";
        break;
      case QProcess::WriteError:
        errorMsg = "写入错误！";
        break;
      case QProcess::ReadError:
        errorMsg = "读取错误！";
        break;
      default:
        errorMsg = QString("未知错误: %1").arg(error);
    }
    statusLabel->setText("✗ 进程错误");
    statusLabel->setStyleSheet("font-size: 24px; padding: 20px; color: #f00; font-weight: bold;");
    outputText->append(QString("\n<span style='color: #f00; font-weight: bold;'>错误: %1</span>").arg(errorMsg));
    closeBtn->setEnabled(true);
  });

  // 添加进程启动成功监控
  connect(process, &QProcess::started, [=]() {
    outputText->append("<span style='color: #0f0;'>✓ 进程已启动</span>\n");
  });

  connect(process, &QProcess::readyReadStandardOutput, [=]() {
    QString output = process->readAllStandardOutput();
    outputText->append(output);
    outputText->verticalScrollBar()->setValue(outputText->verticalScrollBar()->maximum());
  });

  connect(process, &QProcess::readyReadStandardError, [=]() {
    QString output = process->readAllStandardError();
    outputText->append("<span style='color: #f00;'>" + output + "</span>");
    outputText->verticalScrollBar()->setValue(outputText->verticalScrollBar()->maximum());
  });

  connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
          [=](int exitCode, QProcess::ExitStatus exitStatus) {
    if (exitCode == 0 && exitStatus == QProcess::NormalExit) {
      statusLabel->setText("✓ 刷写完成！");
      statusLabel->setStyleSheet("font-size: 24px; padding: 20px; color: #0f0; font-weight: bold;");
      outputText->append("\n<span style='color: #0f0; font-weight: bold; font-size: 18px;'>========================================</span>");
      outputText->append("\n<span style='color: #0f0; font-weight: bold; font-size: 18px;'>刷写成功完成！进程将自动重启。</span>");
      outputText->append("\n<span style='color: #0f0; font-weight: bold; font-size: 18px;'>========================================</span>");
    } else {
      statusLabel->setText("✗ 刷写失败");
      statusLabel->setStyleSheet("font-size: 24px; padding: 20px; color: #f00; font-weight: bold;");
      outputText->append(QString("\n<span style='color: #f00; font-weight: bold; font-size: 18px;'>刷写失败，退出代码: %1</span>").arg(exitCode));
    }
    closeBtn->setEnabled(true);
    closeBtn->setStyleSheet("font-size: 20px; padding: 15px; min-height: 60px; background-color: #4a4a4a;");
  });

  // 执行刷写脚本
  statusLabel->setText("正在刷写固件...");
  outputText->append("========================================\n");
  outputText->append("开始刷写 Panda 固件...\n");
  outputText->append("========================================\n\n");

  // 使用sh执行完整的刷写流程，添加详细日志和错误处理
  QString script =
    "#!/bin/sh\n"
    "set -e  # 遇到错误立即退出\n"
    "set -x  # 打印每条执行的命令\n"
    "\n"
    "exec 2>&1  # 合并 stderr 到 stdout\n"
    "\n"
    "echo '>>> 步骤 1/5: 停止相关进程...'\n"
    "# 先尝试优雅终止\n"
    "pkill -f controlsd || true\n"
    "pkill -f pandad || true\n"
    "pkill -f boardd || true\n"
    "sleep 3\n"
    "# 检查进程是否还在运行，如果在则强制终止\n"
    "if pgrep -f 'pandad|boardd|controlsd' > /dev/null; then\n"
    "  echo '进程未完全退出，强制终止...'\n"
    "  pkill -9 -f controlsd || true\n"
    "  pkill -9 -f pandad || true\n"
    "  pkill -9 -f boardd || true\n"
    "  sleep 2\n"
    "fi\n"
    "echo '✓ 进程已停止'\n"
    "echo ''\n"
    "\n"
    "echo '========================================'\n"
    "echo '>>> 步骤 2/5: 检查 Panda 连接...'\n"
    "echo '========================================'\n"
    "if lsusb | grep -q 'bbaa:'; then\n"
    "  echo '✓ Panda USB设备已检测到'\n"
    "  lsusb | grep 'bbaa:'\n"
    "else\n"
    "  echo '✗ 错误：未检测到Panda设备！'\n"
    "  echo '请检查USB连接后重试'\n"
    "  exit 1\n"
    "fi\n"
    "echo ''\n"
    "\n"
    "echo '========================================'\n"
    "echo '>>> 步骤 3/5: 编译 Panda 固件...'\n"
    "echo '========================================'\n"
    "cd /data/openpilot\n"
    "echo '执行命令: scons -j2 panda/ (使用2个并行任务避免内存不足)'\n"
    "scons -j2 panda/ > /tmp/panda_build.log 2>&1\n"
    "if [ $? -ne 0 ]; then\n"
    "  cat /tmp/panda_build.log\n"
    "  echo ''\n"
    "  echo '✗ 编译失败！查看完整日志: cat /tmp/panda_build.log'\n"
    "  exit 1\n"
    "fi\n"
    "cat /tmp/panda_build.log\n"
    "echo '✓ 编译成功'\n"
    "echo ''\n"
    "\n"
    "echo '========================================'\n"
    "echo '>>> 步骤 4/5: 刷写固件...'\n"
    "echo '========================================'\n"
    "cd /data/openpilot/panda/board\n"
    "echo '执行命令: ./flash.sh'\n"
    "echo '提示: 刷写过程可能需要30-60秒，请耐心等待...'\n"
    "timeout 120 ./flash.sh > /tmp/panda_flash.log 2>&1\n"
    "FLASH_STATUS=$?\n"
    "cat /tmp/panda_flash.log\n"
    "if [ $FLASH_STATUS -ne 0 ]; then\n"
    "  echo ''\n"
    "  if [ $FLASH_STATUS -eq 124 ]; then\n"
    "    echo '✗ 刷写超时（超过120秒）！'\n"
    "    echo '可能原因: Panda设备无响应或USB连接不稳定'\n"
    "  elif [ $FLASH_STATUS -eq 143 ]; then\n"
    "    echo '✗ 刷写进程被终止（错误143）！'\n"
    "    echo '可能原因: 内存不足或进程冲突'\n"
    "  else\n"
    "    echo \"✗ 刷写失败！退出代码: $FLASH_STATUS\"\n"
    "    echo '查看完整日志: cat /tmp/panda_flash.log'\n"
    "  fi\n"
    "  exit 1\n"
    "fi\n"
    "echo '✓ 刷写成功'\n"
    "echo ''\n"
    "\n"
    "echo '========================================'\n"
    "echo '>>> 步骤 5/5: 重启服务...'\n"
    "echo '========================================'\n"
    "echo '等待3秒后自动重启openpilot服务...'\n"
    "sleep 3\n"
    "echo '✓ 刷写完成！系统将自动重启。'\n";

  // 强制刷新输出缓冲区（必须在 start() 之前设置）
  process->setProcessChannelMode(QProcess::MergedChannels);

  // 使用 /bin/sh（openpilot 系统中确认存在）
  process->start("/bin/sh", QStringList() << "-c" << script);
}

SoftwarePanel::SoftwarePanel(QWidget* parent) : ListWidget(parent) {
  gitBranchLbl = new LabelControl("Git Branch");
  gitCommitLbl = new LabelControl("Git Commit");
  osVersionLbl = new LabelControl("OS Version");
  versionLbl = new LabelControl("Version", "", QString::fromStdString(params.get("ReleaseNotes")).trimmed());
  lastUpdateLbl = new LabelControl("Last Update Check", "", "The last time openpilot successfully checked for an update. The updater only runs while the car is off.");
  updateBtn = new ButtonControl("Check for Update", "");
  connect(updateBtn, &ButtonControl::clicked, [=]() {
    if (params.getBool("IsOffroad")) {
      fs_watch->addPath(QString::fromStdString(params.getParamPath("LastUpdateTime")));
      fs_watch->addPath(QString::fromStdString(params.getParamPath("UpdateFailedCount")));
      updateBtn->setText("CHECKING");
      updateBtn->setEnabled(false);
    }
    std::system("pkill -1 -f selfdrive.updated");
  });


  auto uninstallBtn = new ButtonControl("卸载 " + getBrand(), "UNINSTALL");
  connect(uninstallBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm("确认要卸载吗?", this)) {
      params.putBool("DoUninstall", true);
    }
  });
  connect(uiState(), &UIState::offroadTransition, uninstallBtn, &QPushButton::setEnabled);

  QWidget *widgets[] = {versionLbl, lastUpdateLbl, updateBtn, gitBranchLbl, gitCommitLbl, osVersionLbl, uninstallBtn};
  for (QWidget* w : widgets) {
    addItem(w);
  }

  fs_watch = new QFileSystemWatcher(this);
  QObject::connect(fs_watch, &QFileSystemWatcher::fileChanged, [=](const QString path) {
    if (path.contains("UpdateFailedCount") && std::atoi(params.get("UpdateFailedCount").c_str()) > 0) {
      lastUpdateLbl->setText("failed to fetch update");
      updateBtn->setText("CHECK");
      updateBtn->setEnabled(true);
    } else if (path.contains("LastUpdateTime")) {
      updateLabels();
    }
  });
}

void SoftwarePanel::showEvent(QShowEvent *event) {
  updateLabels();
}

void SoftwarePanel::updateLabels() {
  QString lastUpdate = "";
  auto tm = params.get("LastUpdateTime");
  if (!tm.empty()) {
    lastUpdate = timeAgo(QDateTime::fromString(QString::fromStdString(tm + "Z"), Qt::ISODate));
  }

  versionLbl->setText(getBrandVersion());
  lastUpdateLbl->setText(lastUpdate);
  updateBtn->setText("CHECK");
  updateBtn->setEnabled(true);
  gitBranchLbl->setText(QString::fromStdString(params.get("GitBranch")));
  gitCommitLbl->setText(QString::fromStdString(params.get("GitCommit")).left(10));
  osVersionLbl->setText(QString::fromStdString(Hardware::get_os_version()).trimmed());
}

C2NetworkPanel::C2NetworkPanel(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 0, 50, 0);

  ListWidget *list = new ListWidget();
  list->setSpacing(30);
  // wifi + tethering buttons
#ifdef QCOM
  auto wifiBtn = new ButtonControl("WiFi设置", "设置");
  QObject::connect(wifiBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_wifi(); });
  list->addItem(wifiBtn);

  auto tetheringBtn = new ButtonControl("热点设置", "设置");
  QObject::connect(tetheringBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_tethering(); });
  list->addItem(tetheringBtn);
#endif
  ipaddress = new LabelControl("IP 地址", "");
  list->addItem(ipaddress);

  // SSH key management
  list->addItem(new SshToggle());
  list->addItem(new SshControl());
  layout->addWidget(list);
  layout->addStretch(1);
}

void C2NetworkPanel::showEvent(QShowEvent *event) {
  ipaddress->setText(getIPAddress());
}

QString C2NetworkPanel::getIPAddress() {
  std::string result = util::check_output("ifconfig wlan0");
  if (result.empty()) return "";

  const std::string inetaddrr = "inet addr:";
  std::string::size_type begin = result.find(inetaddrr);
  if (begin == std::string::npos) return "";

  begin += inetaddrr.length();
  std::string::size_type end = result.find(' ', begin);
  if (end == std::string::npos) return "";

  return result.substr(begin, end - begin).c_str();
}

QWidget *network_panel(QWidget *parent) {
#ifdef QCOM
  return new C2NetworkPanel(parent);
#else
  return new Networking(parent);
#endif
}

void SettingsWindow::showEvent(QShowEvent *event) {
  panel_widget->setCurrentIndex(0);
  nav_btns->buttons()[0]->setChecked(true);
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {

  // setup two main layouts
  sidebar_widget = new QWidget;
  QVBoxLayout *sidebar_layout = new QVBoxLayout(sidebar_widget);
  sidebar_layout->setMargin(0);
  panel_widget = new QStackedWidget();
  panel_widget->setStyleSheet(R"(
    border-radius: 30px;
    background-color: #292929;
  )");

  // close button
  QPushButton *close_btn = new QPushButton("×");
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 140px;
      padding-bottom: 20px;
      font-weight: bold;
      border 1px grey solid;
      border-radius: 100px;
      background-color: #292929;
      font-weight: 400;
    }
    QPushButton:pressed {
      background-color: #3B3B3B;
    }
  )");
  close_btn->setFixedSize(200, 200);
  sidebar_layout->addSpacing(15);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignLeft);
  QObject::connect(close_btn, &QPushButton::clicked, this, &SettingsWindow::closeSettings);

  // setup panels
  DevicePanel *device = new DevicePanel(this);
  QObject::connect(device, &DevicePanel::reviewTrainingGuide, this, &SettingsWindow::reviewTrainingGuide);
  QObject::connect(device, &DevicePanel::showDriverView, this, &SettingsWindow::showDriverView);

  QList<QPair<QString, QWidget *>> panels = {
    {"设备", device},
    {"网络", network_panel(this)},
    {"开关", new TogglesPanel(this)},
    {"高级", new AdvancedPanel(this)},
    {"软件", new SoftwarePanel(this)},
  };

#ifdef ENABLE_MAPS
  auto map_panel = new MapPanel(this);
  panels.push_back({"Navigation", map_panel});
  QObject::connect(map_panel, &MapPanel::closeSettings, this, &SettingsWindow::closeSettings);
#endif

  const int padding = panels.size() > 3 ? 25 : 35;

  nav_btns = new QButtonGroup(this);
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(QString(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 65px;
        font-weight: 500;
        padding-top: %1px;
        padding-bottom: %1px;
      }
      QPushButton:checked {
        color: white;
      }
      QPushButton:pressed {
        color: #ADADAD;
      }
    )").arg(padding));

    nav_btns->addButton(btn);
    sidebar_layout->addWidget(btn, 0, Qt::AlignRight);

    const int lr_margin = name != "Network" ? 50 : 0;  // Network panel handles its own margins
    panel->setContentsMargins(lr_margin, 25, lr_margin, 25);

    ScrollView *panel_frame = new ScrollView(panel, this);
    panel_widget->addWidget(panel_frame);

    QObject::connect(btn, &QPushButton::clicked, [=, w = panel_frame]() {
      btn->setChecked(true);
      panel_widget->setCurrentWidget(w);
    });
  }
  sidebar_layout->setContentsMargins(50, 50, 100, 50);

  // main settings layout, sidebar + main panel
  QHBoxLayout *main_layout = new QHBoxLayout(this);

  sidebar_widget->setFixedWidth(500);
  main_layout->addWidget(sidebar_widget);
  main_layout->addWidget(panel_widget);

  setStyleSheet(R"(
    * {
      color: white;
      font-size: 50px;
    }
    SettingsWindow {
      background-color: black;
    }
  )");
}

void SettingsWindow::hideEvent(QHideEvent *event) {
#ifdef QCOM
  HardwareEon::close_activities();
#endif
}
