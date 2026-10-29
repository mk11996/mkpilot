"""Pure-memory checks using rebuilt native CAN libraries; no CAN/USB access."""
import unittest

from opendbc.can.packer import CANPacker
from opendbc.can.parser import CANParser
from selfdrive.boardd.boardd import can_list_to_can_capnp
from selfdrive.car.mazda import mazdacan


class TestHudNative(unittest.TestCase):
  def setUp(self):
    self.packer = CANPacker('mazda_2017')
    self.cam = {'LDW_WARN_LL': 0, 'LDW_WARN_RL': 0, 'TJA': 5, 'TJA_TRANSITION': 2,
                'LANE_LINES': 3, 'LINE_VISIBLE': 1, 'NO_ERR_BIT': 1, 'BIT1': 1, 'BIT3': 1}

  def packet(self, left, right, hands=False, fresh=True, stock_bit56=0):
    cam = dict(self.cam, LDW_WARN_LL=left, LDW_WARN_RL=right, HANDS_ON_STEER_WARN=stock_bit56)
    return mazdacan.create_alert_command(self.packer, cam, hands, fresh)

  def test_stock_warning_group_is_preserved_despite_hands_override(self):
    for left in (0, 1):
      for right in (0, 1):
        for bit56 in (0, 1):
          for hands in (False, True):
            with self.subTest(left=left, right=right, bit56=bit56, hands=hands):
              msg = self.packet(left, right, hands, stock_bit56=bit56)
              self.assertEqual((msg[0], msg[3], len(msg[2])), (0x440, 0, 8))
              self.assertEqual(msg[2][7] & 0x07, (left << 1) | (right << 2) | bit56)
              self.assertEqual(msg[2][7] & 0x08, int(hands) << 3)
              self.assertEqual(msg[2][6] & 0x0e, 0x0e if hands else 0)

  def test_distinct_original_four_and_five_are_not_merged(self):
    for hands in (False, True):
      a = self.packet(0, 1, hands, stock_bit56=0)[2]
      b = self.packet(0, 1, hands, stock_bit56=1)[2]
      self.assertEqual((a[7] & 7, b[7] & 7), (4, 5))
      self.assertEqual(a[:7], b[:7])
      self.assertEqual(a[7] ^ b[7], 1)

  def test_stale_side_warning_clears_without_erasing_lane_context(self):
    fresh = self.packet(1, 1, stock_bit56=1)[2]
    stale = self.packet(1, 1, fresh=False, stock_bit56=1)[2]
    self.assertEqual(fresh[:7], stale[:7])
    self.assertEqual(fresh[7] ^ stale[7], 0x07)
    self.assertEqual(stale[7] & 0x07, 0)
    self.assertEqual(stale[4] & 0x70, 5 << 4)  # TJA
    self.assertEqual(stale[3] & 0x0c, 2 << 2)  # TJA_TRANSITION
    self.assertEqual(stale[1] & 0x40, 0x40)   # NO_ERR_BIT
    self.assertEqual(stale[7] & 0x40, 0x40)   # BIT3

  def test_parser_decodes_raw_sides_separately_from_hands(self):
    signals = [(name, 'CAM_LANEINFO') for name in ('LDW_WARN_LL', 'LDW_WARN_RL', 'HANDS_ON_STEER_WARN')]
    parser = CANParser('mazda_2017', signals, [('CAM_LANEINFO', 0)], 2)
    for last_byte in range(8):
      packet = [0x440, 0, bytes(7) + bytes([last_byte]), 2]
      parser.update_strings([can_list_to_can_capnp([packet])])
      values = parser.vl['CAM_LANEINFO']
      self.assertEqual(values['LDW_WARN_LL'], (last_byte >> 1) & 1)
      self.assertEqual(values['LDW_WARN_RL'], (last_byte >> 2) & 1)
      self.assertEqual(values['HANDS_ON_STEER_WARN'], last_byte & 1)


if __name__ == '__main__':
  unittest.main()
