// Host-only harness: exercise the real Mazda safety header without USB/CAN I/O.
// Generic torque-limit helpers are stubs; this tests forwarding and TX filtering.
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

typedef struct { int addr, bus, len; unsigned char data[8]; } CANPacket_t;
typedef struct { int addr, bus, len; } CanMsg;
typedef struct { int addr, bus, len; unsigned int expected_timestep; } CheckMsg;
typedef struct { CheckMsg msg[3]; } AddrCheckStruct;
typedef struct { AddrCheckStruct *checks; unsigned int len; } addr_checks;
typedef struct {
  const addr_checks *(*init)(int16_t);
  int (*rx)(CANPacket_t *);
  int (*tx)(CANPacket_t *);
  int (*tx_lin)(int, unsigned char *, int);
  int (*fwd)(int, CANPacket_t *);
} safety_hooks;

#define GET_ADDR(p) ((p)->addr)
#define GET_BUS(p) ((p)->bus)
#define GET_BYTE(p, n) ((p)->data[n])
#define RELAY_TRNS_TIMEOUT 1

static int torque_interceptor_detected, vehicle_moving, torque_driver;
static int controls_allowed, cruise_engaged_prev, gas_pressed, gas_pressed_prev;
static int brake_pressed, brake_pressed_prev, safety_mode_cnt;
static int desired_torque_last, rt_torque_last;
static uint32_t ts_last;
static bool addr_safety_check(CANPacket_t *p, addr_checks *checks, void *a, void *b, void *c) { return true; }
static void update_sample(int *sample, int value) { *sample = value; }
static void relay_malfunction_set(void) {}
static void relay_malfunction_reset(void) {}
static void generic_rx_checks(bool stock) {}
static uint32_t microsecond_timer_get(void) { return 0; }
static uint32_t get_ts_elapsed(uint32_t a, uint32_t b) { return a - b; }
static bool max_limit_check(int desired, int max, int min) { return false; }
static bool driver_limit_check(int a, int b, int *c, int d, int e, int f, int g, int h) { return false; }
static bool rt_rate_limit_check(int a, int b, int c) { return false; }
static int nooutput_tx_lin_hook(int a, unsigned char *b, int c) { return 0; }
static bool msg_allowed(CANPacket_t *p, const CanMsg *allowed, unsigned int count) {
  for (unsigned int i = 0; i < count; ++i) {
    if (p->addr == allowed[i].addr && p->bus == allowed[i].bus && p->len == allowed[i].len) return true;
  }
  return false;
}

#include "../../../../panda/board/safety/safety_mazda.h"

int main(void) {
  CANPacket_t eps = { .addr = 0x241, .bus = 0, .len = 8, .data = {0x38,0,0x80,0,0x80,0xa0,0xe0,0x5a} };
  CANPacket_t before = eps;
  assert(mazda_fwd_hook(0, &eps) == 2);
  assert(eps.addr == before.addr && eps.bus == before.bus && eps.len == before.len);
  assert(memcmp(before.data, eps.data, sizeof(eps.data)) == 0);
  for (int bus = 0; bus < 3; ++bus) { eps.bus = bus; assert(mazda_tx_hook(&eps) == 0); }
  CANPacket_t camera = { .addr = 0x243, .bus = 2, .len = 8 };
  assert(mazda_fwd_hook(2, &camera) == -1);
  camera.addr = 0x440;
  assert(mazda_fwd_hook(2, &camera) == -1);
  camera.addr = 0x300;
  assert(mazda_fwd_hook(2, &camera) == 0);
  assert(mazda_fwd_hook(1, &camera) == -1);

  CANPacket_t ti = { .addr = 0x249, .bus = 1, .len = 8 };
  assert(mazda_tx_hook(&ti) == 1);
  ti.bus = 0;
  assert(mazda_tx_hook(&ti) == 0);
  CANPacket_t stop = { .addr = 0x9e, .bus = 0, .len = 8, .data = {0xd4} };
  assert(mazda_tx_hook(&stop) == 1);
  stop.data[1] = 1;
  assert(mazda_tx_hook(&stop) == 0);
  puts("Mazda forwarding and TX filtering: PASS");
  return 0;
}
