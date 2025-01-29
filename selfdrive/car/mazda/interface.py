#!/usr/bin/env python3
from cereal import car
from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car.mazda.values import CAR, LKAS_LIMITS
from openpilot.selfdrive.car import STD_CARGO_KG, get_safety_config
from openpilot.selfdrive.car.interfaces import CarInterfaceBase
from openpilot.selfdrive import global_ti as TI

from openpilot.common.params import Params

ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName

class CarInterface(CarInterfaceBase):

  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, experimental_long, docs):
    ret.carName = "mazda"
    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.mazda)]
    ret.radarUnavailable = True

    ret.dashcamOnly = candidate not in (CAR.CX5_2022, CAR.CX9_2021)
    ret.dashcamOnly = False if ret.dashcamOnly and Params().get_bool("dp_car_dashcam_mode_removal") else ret.dashcamOnly

    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.8
    ret.tireStiffnessFactor = 0.70   # not optimized yet

    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)


    if ret.enableTorqueInterceptor:
      print("Adjusting PID parameters for TI")
      if candidate in (CAR.CX5, CAR.CX5_2022):
        ret.mass = 3655 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpV = [0.25,0.28]
        ret.lateralTuning.pid.kiV = [0.01,0.025]
        ret.lateralTuning.pid.kf = 0.00008

        ret.lateralTuning.init('indi')
        ret.lateralTuning.indi.innerLoopGainBP = [5.0, 35]
        ret.lateralTuning.indi.innerLoopGainV = [4.5, 6.0]
        ret.lateralTuning.indi.outerLoopGainBP = [5, 35]
        ret.lateralTuning.indi.outerLoopGainV = [3.0, 6]
        ret.lateralTuning.indi.timeConstantBP = [2, 35]
        ret.lateralTuning.indi.timeConstantV = [0.2, 1.5]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0, 25]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.0, 1]

      elif candidate in [CAR.CX9, CAR.CX9_2021]:
        ret.mass = 4217 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 3.1
        ret.steerRatio = 17.6
        ret.lateralTuning.pid.kiBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpV = [0.10,0.22]
        ret.lateralTuning.pid.kiV = [0.01,0.019]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate == CAR.MAZDA3:
        ret.mass = 2875 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 14.0

        ret.lateralTuning.pid.kiBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpV = [0.25,0.28]
        ret.lateralTuning.pid.kiV = [0.01,0.025]
        ret.lateralTuning.pid.kf = 0.00008

        ret.lateralTuning.init('indi')
        ret.lateralTuning.indi.innerLoopGainBP = [5.0, 35]
        ret.lateralTuning.indi.innerLoopGainV = [4.5, 6.0]
        ret.lateralTuning.indi.outerLoopGainBP = [5, 35]
        ret.lateralTuning.indi.outerLoopGainV = [3.0, 6]
        ret.lateralTuning.indi.timeConstantBP = [2, 35]
        ret.lateralTuning.indi.timeConstantV = [0.2, 1.5]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0, 25]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.0, 1]
      elif candidate == CAR.MAZDA6:
        ret.mass = 3443 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.83
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpV = [0.10,0.22]
        ret.lateralTuning.pid.kiV = [0.01,0.019]
        ret.lateralTuning.pid.kf = 0.00006
    else:
      if candidate in (CAR.CX5, CAR.CX5_2022):
        ret.mass = 3655 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate in [CAR.CX9, CAR.CX9_2021]:
        ret.mass = 4217 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 3.1
        ret.steerRatio = 17.6
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate == CAR.MAZDA3:
        ret.mass = 2875 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 14.0
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate == CAR.MAZDA6:
        ret.mass = 3443 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.83
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006

    if candidate not in (CAR.CX5_2022, ):
      ret.minSteerSpeed = LKAS_LIMITS.DISABLE_SPEED * CV.KPH_TO_MS

    ret.centerToFront = ret.wheelbase * 0.41

    return ret

  # returns a car.CarState
  def _update(self, c, can_strings):
    
    self.cp.update_strings(can_strings)
    self.cp_cam.update_strings(can_strings)
    self.cp_body.update_strings(can_strings)
    if self.CP.enableTorqueInterceptor and not TI.enabled:
      TI.enabled = True
      self.cp_body = self.CS.get_body_can_parser(self.CP)
    ret = self.CS.update(self.cp, self.cp_cam)
    ret.canValid = self.cp.can_valid and self.cp_cam.can_valid and (self.cp_body is None or self.cp_body.can_valid)

    # events
    events = self.create_common_events(ret)

    if self.CS.lkas_disabled:
      events.add(EventName.lkasDisabled)

    if not self.CS.acc_active_last and not self.CS.ti_lkas_allowed:
      events.add(EventName.steerTempUnavailable)

    ret.events = events.to_msg()
    self.CS.out = ret.as_reader()
    return self.CS.out

  def apply(self, c, now_nanos):
    ret = self.CC.update(c, self.CS, self.frame, now_nanos)
    self.frame += 1
    return ret

