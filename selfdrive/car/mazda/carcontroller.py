from cereal import car
from opendbc.can.packer import CANPacker
from openpilot.selfdrive.car import apply_driver_steer_torque_limits, apply_ti_steer_torque_limits
from openpilot.selfdrive.car.mazda import mazdacan
from openpilot.selfdrive.car.mazda.values import CarControllerParams, Buttons

VisualAlert = car.CarControl.HUDControl.VisualAlert

class CarController:
    def __init__(self, dbc_name, CP, VM):
        self.CP = CP
        self.apply_steer_last = 0
        self.ti_apply_steer_last = 0  # 添加一个变量来跟踪上次的TI转向
        self.packer = CANPacker(dbc_name)
        self.brake_counter = 0
        self.frame = 0

    def update(self, CC, CS, now_nanos):
        can_sends = []

        apply_steer = 0
        ti_apply_steer = ti_new_steer = 0  # 初始化TI转向的变量

        if CC.latActive:
            # 计算转向，并根据驾驶员扭矩设定限制
            new_steer = int(round(CC.actuators.steer * CarControllerParams.STEER_MAX))
            apply_steer = apply_driver_steer_torque_limits(new_steer, self.apply_steer_last,
                                                           CS.out.steeringTorque, CarControllerParams)

            if CS.CP.enableTorqueInterceptor:  # 检查是否启用了转向拦截器
                if CS.ti_lkas_allowed:  # 检查TI是否允许介入
                    # 计算TI转向扭矩
                    ti_new_steer = int(round(CC.actuators.steer * CarControllerParams.TI_STEER_MAX))
                    # 应用TI转向扭矩限制
                    ti_apply_steer = apply_ti_steer_torque_limits(ti_new_steer, self.ti_apply_steer_last,
                                                                  CS.out.steeringTorque, CarControllerParams)

        if CC.cruiseControl.cancel:
            # 如果踩下刹车，让我们等待 >70 毫秒再尝试禁用crz，以避免
            # 与原厂系统的竞争条件，其中第二次从 openpilot 取消
            # 将禁用 crz 'main on'。crz ctrl msg 以 50hz 运行。70 毫秒允许我们
            # 读取 3 条消息并最有可能在尝试取消之前同步状态。
            self.brake_counter = self.brake_counter + 1
            if self.frame % 10 == 0 and not (CS.out.brakePressed and self.brake_counter < 7):
                # 如果在 OP 解除时启用了 Stock ACC，则取消它
                # 以 10 赫兹的频率发送，直到与 ACC 状态同步
                can_sends.append(mazdacan.create_button_cmd(self.packer, self.CP.carFingerprint, CS.crz_btns_counter, Buttons.CANCEL))
        else:
            self.brake_counter = 0
            if CC.cruiseControl.resume and self.frame % 5 == 0:
                # Mazda Stop and Go 需要按下 RES 按钮（或油门），如果汽车停止超过 3 秒
                # 当规划者希望车辆移动时发送恢复按钮
                can_sends.append(mazdacan.create_button_cmd(self.packer, self.CP.carFingerprint, CS.crz_btns_counter, Buttons.RESUME))

        self.apply_steer_last = apply_steer
        self.ti_apply_steer_last = ti_apply_steer  # 更新最后一次应用的TI转向

        # 发送HUD警告
        if self.frame % 50 == 0:
            ldw = CC.hudControl.visualAlert == VisualAlert.ldw
            steer_required = CC.hudControl.visualAlert == VisualAlert.steerRequired
            # TODO: 找到一种方法来消除可听警告，以便我们可以添加更多的HUD警告
            steer_required = steer_required and CS.lkas_allowed_speed
            can_sends.append(mazdacan.create_alert_command(self.packer, CS.cam_laneinfo, ldw, steer_required))

        # 发送转向命令（如果启用了TI转向拦截器，则同时发送TI转向命令）
        # 始终发送给原厂系统
        can_sends.append(mazdacan.create_steering_control(self.packer, self.CP.carFingerprint,
                                                          self.frame, apply_steer, CS.cam_lkas))

        # 如果启用了转向拦截器，也发送TI转向控制
        if CS.CP.enableTorqueInterceptor and CS.ti_lkas_allowed:
            can_sends.extend(mazdacan.create_ti_steering_control(self.packer, self.CP.carFingerprint, self.frame, ti_apply_steer))

        new_actuators = CC.actuators.copy()
        new_actuators.steer = apply_steer / CarControllerParams.STEER_MAX
        new_actuators.steerOutputCan = apply_steer

        self.frame += 1
        return new_actuators, can_sends
