"""Use the deployed cereal/pycapnp types; no CAN, USB or control processes."""
import importlib.util
import unittest
from pathlib import Path

from cereal import car, log


spec = importlib.util.spec_from_file_location('mazda_startup_capnp_subject', Path(__file__).resolve().parents[1] / 'startup.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class TestPandaReadinessCapnp(unittest.TestCase):
  def messages(self, modes=('mazda',)):
    cp = car.CarParams.new_message()
    configs = cp.init('safetyConfigs', 1)
    configs[0].safetyModel = 'mazda'
    configs[0].safetyParam = 1
    event = log.Event.new_message()
    states = event.init('pandaStates', len(modes))
    for state, mode in zip(states, modes):
      state.safetyModel = mode
      state.safetyParam = 1
      state.unsafeMode = 1
    return event, cp

  def ready(self, event, cp, valid=True):
    return startup.panda_safety_ready(event.as_reader().pandaStates, cp.as_reader().safetyConfigs,
                                      1, valid, (car.CarParams.SafetyModel.silent,))

  def test_single_panda_reader(self):
    self.assertTrue(self.ready(*self.messages()))

  def test_only_ignored_extra_pandas_allowed(self):
    self.assertTrue(self.ready(*self.messages(('mazda', 'silent'))))
    self.assertFalse(self.ready(*self.messages(('mazda', 'mazda'))))

  def test_missing_panda_or_invalid_message_is_not_ready(self):
    self.assertFalse(self.ready(*self.messages(())))
    event, cp = self.messages()
    self.assertFalse(self.ready(event, cp, valid=False))

  def test_safety_mode_param_and_unsafe_flags_must_match(self):
    for field, value in (('safetyModel', 'silent'), ('safetyParam', 0), ('unsafeMode', 0)):
      with self.subTest(field=field):
        event, cp = self.messages()
        setattr(event.pandaStates[0], field, value)
        self.assertFalse(self.ready(event, cp))


if __name__ == '__main__':
  unittest.main()
