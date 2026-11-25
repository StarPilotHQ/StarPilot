#include "opendbc/safety/safety.h"

// this file is checked by cppcheck

extern uint32_t microsecond_timer_get(void);

// Ignore misra-c2012-8.7 as these functions are only called from libsafety
SAFETY_UNUSED(heartbeat_engaged);

SAFETY_UNUSED(safety_rx_hook);
SAFETY_UNUSED(safety_tx_hook);
SAFETY_UNUSED(safety_fwd_hook);
SAFETY_UNUSED(safety_fwd_packet_hook);
SAFETY_UNUSED(safety_uses_camera_slot);
SAFETY_UNUSED(safety_camera_release_current);
SAFETY_UNUSED(safety_camera_release_delay);
SAFETY_UNUSED(safety_stage_tx_hook);
SAFETY_UNUSED(safety_fwd_output_hook);
SAFETY_UNUSED(safety_tick);
SAFETY_UNUSED(set_safety_hooks);
// Exported to Panda's USB status handler and the native safety test library.
SAFETY_UNUSED(aol_get_request_mask);
SAFETY_UNUSED(aol_get_permission_mask);
