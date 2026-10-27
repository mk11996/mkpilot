import time


class MessageFreshness:
  def __init__(self, timeout, clock=time.monotonic):
    self.timeout = timeout
    self.clock = clock
    self.last_received = None

  def update(self, received, valid=True):
    if not valid:
      self.last_received = None
    elif received:
      self.last_received = self.clock()
    return self.last_received is not None and self.clock() - self.last_received <= self.timeout


class IStopScheduler:
  def __init__(self, delay=5.0, burst_frames=160, clock=time.monotonic):
    self.delay = delay
    self.burst_frames = burst_frames
    self.clock = clock
    self.ready_since = None
    self.sent = 0

  def update(self, enabled, ready):
    if not ready:
      self.ready_since = None
      self.sent = 0
      return False
    now = self.clock()
    if self.ready_since is None:
      self.ready_since = now
    if not enabled:
      self.sent = 0
      return False
    if now - self.ready_since < self.delay or self.sent >= self.burst_frames:
      return False
    self.sent += 1
    return True


class TiStartupProbe:
  """Choose TI once at startup; a failed probe cannot enable TI later this run."""
  def __init__(self, window=1.0, clock=time.monotonic):
    self.window = window
    self.clock = clock
    self.started = None
    self.state = 'waiting'

  def update(self, ready, feedback, active, moving):
    if self.state in ('confirmed', 'disabled'):
      return self.state
    # Do not discover a new steering device after control or vehicle motion starts.
    if active or moving:
      self.state = 'disabled'
      return self.state
    now = self.clock()
    if self.started is None:
      if not ready:
        return self.state
      self.started = now
      self.state = 'probing'
    # A safety interruption must not reset or extend the one-time deadline.
    if now - self.started >= self.window:
      self.state = 'disabled'
    elif ready and feedback:
      self.state = 'confirmed'
    return self.state

  @property
  def detected(self):
    return self.state == 'confirmed'

  @property
  def sending(self):
    return self.state in ('probing', 'confirmed')


def stock_ldw_allowed(active, enabled, camera_fresh):
  return camera_fresh and (not active or enabled)


def panda_safety_ready(states, configs, unsafe_mode, valid, ignored_modes=()):
  if not valid or not configs or len(states) < len(configs):
    return False
  for state, config in zip(states, configs):
    if (state.safetyModel != config.safetyModel or state.safetyParam != config.safetyParam or
        state.unsafeMode != unsafe_mode):
      return False
  return all(state.safetyModel in ignored_modes for state in states[len(configs):])
