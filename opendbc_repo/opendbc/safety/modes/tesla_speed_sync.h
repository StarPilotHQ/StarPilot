#pragma once
// Speed sync: experimental wheel-only capability. No press/tilt/stalk authority.
// Disabled unless the separate debug-only flag AND longitudinal mode are set.
static bool tesla_sync_enabled = false;
static bool tesla_sync_seen = false;
static bool tesla_sync_di_seen = false;
static bool tesla_sync_di_valid = false;
static unsigned int tesla_sync_counter = 0U;
static bool tesla_sync_neutral = false;
static bool tesla_sync_blocked = false;
static bool tesla_sync_pending = false;
static bool tesla_sync_drive = false;
static bool tesla_sync_occupant = false;
static bool tesla_sync_native_active = false;
static uint8_t tesla_sync_template[8];
static uint32_t tesla_sync_wheel_ts = 0U;
static uint32_t tesla_sync_di_ts = 0U;
static uint32_t tesla_sync_last_template = 0U;
static uint32_t tesla_sync_sent_ts = 0U;
// A request the Tesla never answered is released after this long, so the host
// can try again instead of waiting for the driver to change the speed.
#define TESLA_SYNC_PENDING_TIMEOUT_US 800000U
static unsigned int tesla_sync_steps = 0U;
// Rate limit instead of a per-session cap: a burst of up to 30 requests, then
// one more every second. Normal syncing never runs out; a runaway host is held to
// one request per second instead of being stopped for the rest of the drive.
#define TESLA_SYNC_TOKENS_MAX 30U
#define TESLA_SYNC_TOKEN_REFILL_US 1000000U
static unsigned int tesla_sync_tokens = TESLA_SYNC_TOKENS_MAX;
static uint32_t tesla_sync_refill_ts = 0U;
static int tesla_sync_speed = 0;
static unsigned int tesla_sync_units = 0U;
static unsigned int tesla_sync_sent_units = 0U;

static bool tesla_sync_wheel_neutral(const uint8_t *d) {
  return ((d[0] & 3U) == 1U) &&
    (((d[0] >> 3U) & 3U) == 1U) && (((d[0] >> 5U) & 3U) == 1U) &&
    ((d[1] & 3U) == 1U) && (((d[1] >> 2U) & 3U) == 1U) &&
    (((d[1] >> 4U) & 3U) == 1U) && (((d[1] >> 6U) & 3U) == 1U) &&
    ((d[2] & 63U) == 0U) && ((d[3] & 63U) == 0U) && ((d[4] & 15U) == 0U);
}

static void tesla_sync_configure(uint16_t param, bool longitudinal) {
  tesla_sync_enabled = false;
#ifdef ALLOW_DEBUG
  tesla_sync_enabled = longitudinal && GET_FLAG(param, 4096U);
#else
  (void)param;
  (void)longitudinal;
#endif
  tesla_sync_seen = false;
  tesla_sync_di_seen = false;
  tesla_sync_di_valid = false;
  tesla_sync_neutral = false;
  tesla_sync_blocked = false;
  tesla_sync_pending = false;
  tesla_sync_drive = false;
  tesla_sync_occupant = false;
  tesla_sync_native_active = false;
  tesla_sync_wheel_ts = 0U;
  tesla_sync_di_ts = 0U;
  tesla_sync_last_template = 0U;
  tesla_sync_steps = 0U;
  tesla_sync_tokens = TESLA_SYNC_TOKENS_MAX;
  tesla_sync_refill_ts = microsecond_timer_get();
}

static void tesla_sync_refill(uint32_t now) {
  uint32_t elapsed = safety_get_ts_elapsed(now, tesla_sync_refill_ts);
  while ((tesla_sync_tokens < TESLA_SYNC_TOKENS_MAX) && (elapsed >= TESLA_SYNC_TOKEN_REFILL_US)) {
    tesla_sync_tokens++;
    elapsed -= TESLA_SYNC_TOKEN_REFILL_US;
    tesla_sync_refill_ts += TESLA_SYNC_TOKEN_REFILL_US;
  }
  if (tesla_sync_tokens >= TESLA_SYNC_TOKENS_MAX) {
    tesla_sync_refill_ts = now;
  }
}

static void tesla_sync_optional_rx(const CANPacket_t *msg) {
  if (tesla_sync_enabled && msg_matches(msg, 0x3C2U, 1U)) {
    if (GET_LEN(msg) != 8U) { tesla_sync_seen = false; return; }
    unsigned int mux = msg->data[0] & 3U;
    if ((mux == 2U) || (mux == 3U)) {
      tesla_sync_seen = false;
      tesla_sync_neutral = false;
      return;
    }
    if (mux == 1U) {
      tesla_sync_seen = true;
      tesla_sync_wheel_ts = microsecond_timer_get();
      tesla_sync_neutral = tesla_sync_wheel_neutral(msg->data);
      if (tesla_sync_neutral) {
        for (unsigned int i = 0U; i < 8U; i++) {
          tesla_sync_template[i] = msg->data[i];
        }
      }
      // Driver input during a request no longer latches the sync off. The
      // request stays open until the speed changes or it times out, and the
      // host recomputes from the observed speed.
    }
  }
}

static void tesla_sync_rx(const CANPacket_t *msg) {
  if (tesla_sync_enabled) {
    if (msg_matches(msg, 0x118U, 0U, 8U)) {
      tesla_sync_drive = ((msg->data[2] >> 5U) & 7U) == 4U;
    }
    if (msg_matches(msg, 0x311U, 0U, 7U)) {
      // Occupant status is already part of the validated required RX set.
      // Exact bit positions are shared with tesla_screen_rx.
      tesla_sync_occupant = GET_BIT(msg, 13U) && !GET_BIT(msg, 28U);
    }
    if (msg_matches(msg, 0x286U, 0U, 8U)) {
      unsigned int counter = msg->data[1] & 15U;
      bool advancing = !tesla_sync_di_seen || (((counter - tesla_sync_counter) & 15U) == 1U);
      tesla_sync_counter = counter;
      tesla_sync_di_seen = true;
      tesla_sync_di_valid = advancing;
      if (!advancing) { return; }
      unsigned int state = (msg->data[1] >> 4U) & 7U;
      int speed = ((msg->data[2] << 1U) | (msg->data[1] >> 7U)); // half display units
      unsigned int units = msg->data[3] & 1U;
      tesla_sync_di_ts = microsecond_timer_get();
      tesla_sync_native_active = (state == 2U) || (state == 4U);
      if ((state == 0U) || (state == 1U)) {
        tesla_sync_pending = false;
        tesla_sync_blocked = false;
        tesla_sync_steps = 0U;
      }
      if (tesla_sync_pending && (units != tesla_sync_sent_units)) {
        tesla_sync_blocked = true;
      }
      if (tesla_sync_pending && (speed != tesla_sync_speed)) {
        // Feedback releases one request; it does not establish its origin.
        // The host must recompute direction from this newly observed speed.
        tesla_sync_pending = false;
      }
      tesla_sync_speed = speed;
      tesla_sync_units = units;
    }
  }
}

static bool tesla_sync_tx(const CANPacket_t *msg, bool stock_aeb, bool autopark, bool stock_lkas) {
  uint32_t now = microsecond_timer_get();
  if (tesla_sync_pending && (safety_get_ts_elapsed(now, tesla_sync_sent_ts) > TESLA_SYNC_PENDING_TIMEOUT_US)) {
    tesla_sync_pending = false;
  }
  tesla_sync_refill(now);
  bool valid = tesla_sync_enabled && msg_matches(msg, 0x3C2U, 1U, 8U) &&
    controls_allowed && heartbeat_engaged && aol_rx_healthy() && !relay_malfunction &&
    vehicle_moving && !brake_pressed && !stock_aeb && !autopark && !stock_lkas &&
    tesla_sync_drive && tesla_sync_occupant && tesla_sync_native_active && tesla_sync_di_valid &&
    tesla_sync_seen && tesla_sync_neutral && !tesla_sync_blocked && !tesla_sync_pending &&
    (tesla_sync_tokens > 0U) && (tesla_sync_speed > 2) && (tesla_sync_speed < 508) &&
    (safety_get_ts_elapsed(now, tesla_sync_wheel_ts) <= 250000U) &&
    (safety_get_ts_elapsed(now, tesla_sync_di_ts) <= 250000U) &&
    ((tesla_sync_steps == 0U) || (tesla_sync_wheel_ts != tesla_sync_last_template));
  unsigned int ticks = msg->data[3] & 63U;
  // One click (+-1), or one fast flick (+-5) that moves the Tesla to the next
  // multiple of 5. Both are a single frame, as the driver's own wheel sends.
  valid = valid && ((ticks == 1U) || (ticks == 63U) || (ticks == 5U) || (ticks == 59U));
  for (unsigned int i = 0U; i < 8U; i++) {
    uint8_t mask = (i == 3U) ? 0xC0U : 0xFFU;
    valid = valid && ((msg->data[i] & mask) == (tesla_sync_template[i] & mask));
  }
  if (valid) {
    tesla_sync_pending = true;
    tesla_sync_sent_units = tesla_sync_units;
    tesla_sync_last_template = tesla_sync_wheel_ts;
    tesla_sync_sent_ts = now;
    tesla_sync_tokens--;
    if (tesla_sync_steps < 0xFFFFU) {
      tesla_sync_steps++;  // only marks that a request was sent this session
    }
  }
  return valid;
}

static bool tesla_sync_param_valid(uint16_t param) {
  bool valid = false;
#ifdef ALLOW_DEBUG
  // Scroll speed sync is 4096 on a word with LONG_CONTROL: base 1/257, screen AOL 513/769/1537/1793 and ordinary AOL 2049/2305/3073/3329.
  const uint16_t base = (uint16_t)(param & ~4096U);
  valid = GET_FLAG(param, 4096U) &&
          ((base == 1U) || (base == 257U) || (base == 513U) || (base == 769U) || (base == 1537U) || (base == 1793U) ||
           (base == 2049U) || (base == 2305U) || (base == 3073U) || (base == 3329U));
#else
  (void)param;
#endif
  return valid;
}
