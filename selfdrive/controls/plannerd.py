#!/usr/bin/env python3
from cereal import car
from common.params import Params
from common.realtime import Priority, config_realtime_process
from selfdrive.swaglog import cloudlog
from selfdrive.controls.lib.longitudinal_planner import Planner
from selfdrive.controls.lib.lateral_planner import LateralPlanner
from selfdrive.hardware import TICI
import cereal.messaging as messaging


def plannerd_thread(sm=None, pm=None):
  config_realtime_process(5 if TICI else 2, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = car.CarParams.from_bytes(params.get("CarParams", block=True))
  cloudlog.info("plannerd got CarParams: %s", CP.carName)

  use_lanelines = not params.get_bool('EndToEndToggle')
  wide_camera = params.get_bool('EnableWideCamera') if TICI else False

  cloudlog.event("e2e mode", on=use_lanelines)

  longitudinal_planner = Planner(CP)
  lateral_planner = LateralPlanner(CP, use_lanelines=use_lanelines, wide_camera=wide_camera)

  if sm is None:
    sm = messaging.SubMaster(['carState', 'controlsState', 'radarState', 'modelV2'],
                             poll=['radarState', 'modelV2'], ignore_avg_freq=['radarState'])

  if pm is None:
    pm = messaging.PubMaster(['longitudinalPlan', 'lateralPlan'])

  frame = 0
  last_log_frame = 0
  cloudlog.info("[plannerd] Starting main loop")

  while True:
    sm.update()

    if sm.updated['modelV2']:
      # 定期记录关键服务状态（每30秒记录一次）
      if frame - last_log_frame >= 600:  # 假设 20Hz，600帧 = 30秒
        carState_alive = sm.alive.get('carState', False)
        carState_valid = sm.valid.get('carState', False)
        controlsState_alive = sm.alive.get('controlsState', False)
        controlsState_valid = sm.valid.get('controlsState', False)
        modelV2_alive = sm.alive.get('modelV2', False)
        modelV2_valid = sm.valid.get('modelV2', False)
        cloudlog.info(f"[plannerd] Services status: carState(alive={carState_alive}, valid={carState_valid}), "
                     f"controlsState(alive={controlsState_alive}, valid={controlsState_valid}), "
                     f"modelV2(alive={modelV2_alive}, valid={modelV2_valid})")
        last_log_frame = frame

      lateral_planner.update(sm)
      lateral_planner.publish(sm, pm)
      longitudinal_planner.update(sm)
      longitudinal_planner.publish(sm, pm)
      frame += 1


def main(sm=None, pm=None):
  plannerd_thread(sm, pm)


if __name__ == "__main__":
  main()
