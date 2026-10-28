"""Hardware-free regression tests; no cereal binaries or CAN sockets required."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[4]
MAZDA = ROOT / 'selfdrive/car/mazda'


def load(name, file):
  spec = importlib.util.spec_from_file_location(name, file)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


startup = load('mazda_startup_test_subject', MAZDA / 'startup.py')


def module(name, **values):
  result = types.ModuleType(name)
  result.__dict__.update(values)
  return result


class EnumNames:
  def __getattr__(self, name):
    return name


class Clock:
  now = 0.0

  def __call__(self):
    return self.now


class BaseState:
  def __init__(self, cp):
    self.CP = cp


class Params:
  data = {'KeepIStopDisabled': b'1', 'EnableLdwVibration': b'0'}

  def get(self, key):
    return self.data.get(key)


class Packer:
  """Capture requested fields; byte-level validation is a separate device-lib check."""
  addresses = {'CAM_LKAS': 0x243, 'CAM_LKAS2': 0x249, 'CAM_LANEINFO': 0x440, 'CRZ_BTNS': 0x9d}

  def __init__(self, dbc):
    pass

  def make_can_msg(self, name, bus, values):
    return [self.addresses[name], 0, dict(values), bus]


class ParserConfig:
  def __init__(self, dbc, signals, checks, bus):
    self.signals, self.checks, self.bus = signals, checks, bus


car = types.SimpleNamespace(
  CarParams=types.SimpleNamespace(Ecu=EnumNames()),
  CarControl=types.SimpleNamespace(HUDControl=types.SimpleNamespace(VisualAlert=types.SimpleNamespace(ldw=1))),
  CarState=types.SimpleNamespace(ButtonEvent=types.SimpleNamespace(Type=EnumNames())))
car_helpers = module('selfdrive.car', dbc_dict=lambda pt, radar=None: {'pt': pt, 'radar': radar})
car_helpers.apply_std_steer_torque_limits = lambda desired, last, driver, limits: desired
car_helpers.apply_ti_steer_torque_limits = lambda desired, last, driver, limits: min(max(desired, last - limits.TI_STEER_DELTA_DOWN), last + limits.TI_STEER_DELTA_UP)
logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
stubs = {
  'cereal': module('cereal', car=car),
  'selfdrive.car': car_helpers,
  'selfdrive.car.mazda.startup': startup,
  'common.params': module('common.params', Params=Params),
  'opendbc.can.packer': module('opendbc.can.packer', CANPacker=Packer),
  'opendbc.can.parser': module('opendbc.can.parser', CANParser=ParserConfig),
  'opendbc.can.can_define': module('opendbc.can.can_define', CANDefine=lambda dbc: types.SimpleNamespace(dv={'GEAR': {'GEAR': {}}})),
  'selfdrive.car.interfaces': module('selfdrive.car.interfaces', CarStateBase=BaseState),
  'selfdrive.config': module('selfdrive.config', Conversions=types.SimpleNamespace()),
  'selfdrive.swaglog': module('selfdrive.swaglog', cloudlog=logger),
}
with patch.dict(sys.modules, stubs):
  values = load('mazda_values_test_subject', MAZDA / 'values.py')
  sys.modules['selfdrive.car.mazda.values'] = values
  can = load('mazda_can_test_subject', MAZDA / 'mazdacan.py')
  sys.modules['selfdrive.car.mazda'] = module('selfdrive.car.mazda', mazdacan=can)
  Controller = load('mazda_controller_test_subject', MAZDA / 'carcontroller.py').CarController
  State = load('mazda_state_test_subject', MAZDA / 'carstate.py').CarState


class Actuators(types.SimpleNamespace):
  def copy(self):
    return Actuators(**vars(self))


class TestStartupAndLdw(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.cp = types.SimpleNamespace(carFingerprint=values.CAR.MAZDA3, enableTorqueInterceptor=True)
    self.controller = Controller('mazda_2017', self.cp, None)
    self.controller.istop_scheduler.clock = self.clock
    self.controller.ti_probe.clock = self.clock
    self.c = types.SimpleNamespace(active=False, enabled=False, actuators=Actuators(steer=0.5),
                                   cruiseControl=types.SimpleNamespace(cancel=False), hudControl=types.SimpleNamespace(visualAlert=0))
    self.cs = types.SimpleNamespace(CP=self.cp, ti_feedback_valid=False, ti_lkas_allowed=True, istop_ready=True, cam_lkas_fresh=True,
                                    cam_lkas={'LDW': 1, 'BIT_1': 0, 'ERR_BIT_1': 0, 'ERR_BIT_2': 0, 'LKAS_REQUEST': 500},
                                    cam_laneinfo={}, out=types.SimpleNamespace(standstill=False, steeringTorque=0, vEgo=0,
                                    cruiseState=types.SimpleNamespace(enabled=False), brakePressed=False, steerWarning=False))

  def sends(self, frame=1):
    return self.controller.update(self.c, self.cs, frame)[1]

  def test_ldw_four_switch_cases_never_copy_camera_torque(self):
    for active, switch, expected in [(False, False, 0), (False, True, 1), (True, False, 0), (True, True, 0)]:
      with self.subTest(active=active, switch=switch):
        self.c.active = self.c.enabled = active
        self.controller.ldw_vibration_enabled = switch
        command = next(m for m in self.sends() if m[0] == 0x243)
        self.assertEqual(command[2]['LDW'], expected)
        self.assertEqual(command[2]['LKAS_REQUEST'], 0)

  def test_ldw_ends_immediately_and_stale_camera_does_not_repeat(self):
    self.controller.ldw_vibration_enabled = True
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x243)[2]['LDW'], 1)
    self.cs.cam_lkas['LDW'] = 0
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x243)[2]['LDW'], 0)
    self.cs.cam_lkas['LDW'] = 1
    self.cs.cam_lkas_fresh = False
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x243)[2]['LDW'], 0)

  def test_enabled_without_active_control_still_obeys_ldw_switch(self):
    self.c.enabled = True
    self.c.active = False
    self.controller.ldw_vibration_enabled = False
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x243)[2]['LDW'], 0)
    self.controller.ldw_vibration_enabled = True
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x243)[2]['LDW'], 1)

  def test_moving_before_safety_ready_locks_ti_out_without_probing(self):
    self.controller.panda_ready = False
    self.cs.out.vEgo = 1
    self.assertFalse(any(m[0] == 0x249 for m in self.sends()))
    self.assertFalse(self.cp.enableTorqueInterceptor)
    self.assertEqual(self.controller.ti_probe.reason, 'vehicle_moving')
    self.cs.out.vEgo = 0
    self.controller.panda_ready = self.cs.ti_feedback_valid = True
    self.assertFalse(any(m[0] == 0x249 for m in self.sends()))

  def test_startup_probe_waits_for_panda_then_times_out_permanently(self):
    for frame in range(1, 151):
      self.clock.now = frame * 0.01
      self.assertFalse(any(m[0] == 0x249 for m in self.sends(frame)))
    self.controller.panda_ready = True
    for step in range(100):
      self.clock.now = 1.5 + step * 0.01
      command = next(m for m in self.sends(151 + step) if m[0] == 0x249)
      self.assertEqual(command[2]['LKAS_REQUEST'], 0)
      self.assertEqual(command[2]['CHKSUM'], 0)
      self.assertEqual(command[2]['KEY'], 3294744160)
    self.clock.now = 2.5
    self.assertFalse(any(m[0] == 0x249 for m in self.sends()))
    self.assertFalse(self.cp.enableTorqueInterceptor)
    self.cs.ti_feedback_valid = True
    self.c.active = True
    self.clock.now = 100
    self.assertFalse(any(m[0] == 0x249 for m in self.sends()))
    self.assertFalse(self.controller.ti_detected)
    self.assertEqual(self.controller.ti_apply_steer_last, 0)

  def test_confirmed_ti_retains_author_stream_and_zeroes_unavailable_torque(self):
    self.controller.panda_ready = self.cs.ti_feedback_valid = True
    self.sends()
    self.assertTrue(self.controller.ti_detected)
    self.c.active = True
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x249)[2]['LKAS_REQUEST'], values.CarControllerParams.TI_STEER_DELTA_UP)
    self.cs.ti_feedback_valid = False
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x249)[2]['LKAS_REQUEST'], 0)
    self.cs.ti_feedback_valid = True
    self.assertEqual(next(m for m in self.sends() if m[0] == 0x249)[2]['LKAS_REQUEST'], values.CarControllerParams.TI_STEER_DELTA_UP)

  def test_late_discovery_after_vehicle_motion_is_locked_out(self):
    self.cs.out.vEgo = 1
    self.controller.panda_ready = self.cs.ti_feedback_valid = True
    self.assertFalse(any(m[0] == 0x249 for m in self.sends()))
    self.assertEqual(self.controller.ti_probe.state, 'disabled')
    self.cs.out.vEgo = 0
    self.assertFalse(any(m[0] == 0x249 for m in self.sends()))

  def test_failed_probe_cannot_switch_to_stock_steering_while_active(self):
    self.c.active = self.controller.panda_ready = self.cs.ti_feedback_valid = True
    commands = self.sends()
    self.assertFalse(any(m[0] == 0x249 for m in commands))
    self.assertEqual(next(m for m in commands if m[0] == 0x243)[2]['LKAS_REQUEST'], 0)
    self.assertTrue(self.cp.enableTorqueInterceptor)
    self.c.active = False
    self.sends()
    self.assertFalse(self.cp.enableTorqueInterceptor)

  def test_no_host_generated_241(self):
    self.assertFalse(any(m[0] == 0x241 for m in self.sends()))

  def test_istop_starts_after_continuous_readiness_and_is_bounded(self):
    self.controller.istop_enabled = True
    self.clock.now = 20
    self.assertFalse(any(m[0] == 0x9e for m in self.sends()))
    self.controller.panda_ready = True
    self.sends()
    self.clock.now = 24.99
    self.assertFalse(any(m[0] == 0x9e for m in self.sends()))
    self.clock.now = 25
    self.assertEqual(sum(any(m[0] == 0x9e for m in self.sends()) for _ in range(180)), 160)
    self.cs.istop_ready = False
    self.sends()
    self.cs.istop_ready = True
    self.sends()
    self.clock.now = 29.99
    self.assertFalse(any(m[0] == 0x9e for m in self.sends()))
    self.clock.now = 30
    self.assertTrue(any(m[0] == 0x9e for m in self.sends()))

  def test_ldw_checksum_changes_with_bit_without_changing_request(self):
    a = can.create_steering_control(Packer('mazda_2017'), self.cp.carFingerprint, 4, 0, self.cs.cam_lkas, False)
    b = can.create_steering_control(Packer('mazda_2017'), self.cp.carFingerprint, 4, 0, self.cs.cam_lkas, True)
    self.assertEqual((a[2]['CHKSUM'] - b[2]['CHKSUM']) % 256, 128)


class TestFeedbackState(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.cp = types.SimpleNamespace(carFingerprint=values.CAR.MAZDA3, enableTorqueInterceptor=True)
    self.state = State(self.cp)
    for watcher in [self.state._cam_lkas, self.state._engine_data]:
      watcher.clock = self.clock
    self.main = types.SimpleNamespace(can_valid=True, vl={'MSG_04': {'START_STOP': 2}}, vl_all={'ENGINE_DATA': {'RPM': [800]}})
    self.camera = types.SimpleNamespace(can_valid=True, vl_all={'CAM_LKAS': {'CTR': [0]}})
    self.feedback = {'TI_TORQUE_SENSOR': 0, 'CHKSUM': 0, 'STATE': 3, 'VERSION_NUMBER': 2, 'RAMP_DOWN': 0, 'VIOL': 0, 'ERROR': 0}
    self.body = types.SimpleNamespace(can_valid=True, vl={'TI_FEEDBACK': self.feedback}, vl_all={})

  def update(self):
    self.state._update_startup_state(self.main, self.camera, self.body)

  def test_empty_parser_cannot_confirm_ti(self):
    self.update()
    self.assertFalse(self.state.ti_feedback_valid)
    self.assertTrue(self.state.istop_ready)

  def test_fresh_feedback_timeout_corruption_and_recovery(self):
    self.body.vl_all = {'TI_FEEDBACK': {'STATE': [3]}}
    self.update()
    self.assertTrue(self.state.ti_lkas_allowed)
    self.body.vl_all = {}
    self.body.can_valid = False
    self.update()
    self.assertFalse(self.state.ti_feedback_valid)
    self.body.can_valid = True
    self.body.vl_all = {'TI_FEEDBACK': {'STATE': [3]}}
    self.update()
    self.assertTrue(self.state.ti_lkas_allowed)
    self.feedback['CHKSUM'] = 1
    self.update()
    self.assertFalse(self.state.ti_feedback_valid)

  def test_author_run_and_ramp_down_gate_is_preserved(self):
    self.body.vl_all = {'TI_FEEDBACK': {'STATE': [3]}}
    for field, value in [('RAMP_DOWN', 1), ('STATE', 0), ('STATE', 1), ('STATE', 2)]:
      with self.subTest(field=field, value=value):
        old = self.feedback[field]
        self.feedback[field] = value
        self.update()
        self.assertFalse(self.state.ti_lkas_allowed)
        self.feedback[field] = old
    self.feedback.update(VIOL=1, ERROR=1)
    self.update()
    self.assertTrue(self.state.ti_lkas_allowed)

  def test_camera_and_ecu_freshness_use_wall_clock(self):
    self.update()
    self.assertTrue(self.state.cam_lkas_fresh)
    self.assertTrue(self.state.istop_ready)
    self.camera.vl_all = self.main.vl_all = {}
    self.clock.now = 0.21
    self.update()
    self.assertFalse(self.state.cam_lkas_fresh)
    self.assertFalse(self.state.istop_ready)

  def test_feedback_required_on_bus_one_only(self):
    main = State.get_can_parser(self.cp)
    body = State.get_body_can_parser(self.cp)
    self.assertFalse(any(signal[1] == 'TI_FEEDBACK' for signal in main.signals))
    self.assertEqual(body.bus, 1)
    self.assertIn(('TI_FEEDBACK', 50), body.checks)


class TestSafetyReadiness(unittest.TestCase):
  def test_safety_interruption_does_not_restart_probe_deadline(self):
    clock = Clock()
    probe = startup.TiStartupProbe(clock=clock)
    self.assertEqual(probe.update(True, False, False, False), 'probing')
    clock.now = 0.3
    self.assertEqual(probe.update(False, False, False, False), 'probing')
    clock.now = 1.1
    self.assertEqual(probe.update(True, True, False, False), 'disabled')
    clock.now = 2
    self.assertEqual(probe.update(True, True, False, False), 'disabled')

  def test_rejects_stale_wrong_mode_param_and_unsafe_flags(self):
    config = types.SimpleNamespace(safetyModel=13, safetyParam=1)
    state = types.SimpleNamespace(safetyModel=13, safetyParam=1, unsafeMode=0)
    self.assertTrue(startup.panda_safety_ready([state], [config], 0, True))
    self.assertFalse(startup.panda_safety_ready([state], [config], 0, False))
    for field, value in [('safetyModel', 3), ('safetyParam', 0), ('unsafeMode', 1)]:
      old = getattr(state, field)
      setattr(state, field, value)
      self.assertFalse(startup.panda_safety_ready([state], [config], 0, True))
      setattr(state, field, old)
    self.assertFalse(startup.panda_safety_ready([], [config], 0, True))

  def test_inactive_extra_panda_is_allowed_but_another_active_mode_is_not(self):
    config = types.SimpleNamespace(safetyModel=13, safetyParam=1)
    state = types.SimpleNamespace(safetyModel=13, safetyParam=1, unsafeMode=0)
    extra = types.SimpleNamespace(safetyModel=0)
    self.assertTrue(startup.panda_safety_ready([state, extra], [config], 0, True, (0, 19)))
    extra.safetyModel = 3
    self.assertFalse(startup.panda_safety_ready([state, extra], [config], 0, True, (0, 19)))


if __name__ == '__main__':
  unittest.main()
