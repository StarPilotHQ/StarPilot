#pragma once

static uint16_t toyota_pedal_parent_param(uint16_t param);

// Bare stock profiles are distinct from every adapter and normalize only exact words.
static bool toyota_factory_stock = false;
static bool toyota_factory_eps5 = false;
static bool toyota_factory_alt_main = false;

static bool toyota_factory_stock_param(uint16_t param) {
  return (param == 0x5249U) || (param == 0x5349U) || (param == 0x5358U) || (param == 0x5364U) ||
         (param == 0x564DU) || (param == 0x5749U) || (param == 0x6242U) || (param == 0x6249U);
}

static uint16_t toyota_factory_stock_base(uint16_t param) {
  uint16_t result = param;
  if (toyota_factory_stock_param(param)) {
    result = param & 0x3FFU;
    if ((param == 0x564DU) || (param == 0x5749U)) {
      result |= 0x4000U;
    }
  }
  return result;
}

static bool toyota_dsu_bypass = false;
static bool toyota_bypass_eps5 = false;
static bool toyota_bypass_alt_main = false;
static bool toyota_bypass_ambiguous = false;
static bool toyota_bypass_control = false;
static bool toyota_bypass_aeb = false;

static bool toyota_bypass_param(uint16_t param) {
  return (param == 0x8949U) || (param == 0x8B49U) || (param == 0x8849U) || (param == 0x8A49U) || (param == 0x9849U) || (param == 0x9A49U) || (param == 0x9842U) || (param == 0x9A42U) || (param == 0x8958U) || (param == 0x8B58U) || (param == 0x8964U) || (param == 0x8B64U) || (param == 0xC84DU) || (param == 0xCA4DU) || (param == 0xC949U) || (param == 0xCB49U);
}

static uint16_t toyota_bypass_base_param(uint16_t param) {
  return toyota_bypass_param(param) ? (uint16_t)(param & 0x3FFU) : param;
}

static bool toyota_prius_sdsu_param(uint16_t param) {
  return (param == 0x8042U) || (param == 0x8242U);
}

static bool toyota_prius_sdsu(void) {
  return toyota_prius_sdsu_param(toyota_pedal_parent_param(current_safety_param));
}

static bool toyota_prius_retrofit_advert_param(uint16_t param) {
  return (param == 0xD049U) || (param == 0xD249U);
}

static bool toyota_prius_advert_param(uint16_t param) {
  return (param == 0xC042U) || (param == 0xC242U) || toyota_prius_retrofit_advert_param(param);
}

static bool toyota_prius_advert(void) {
  return toyota_prius_advert_param(toyota_pedal_parent_param(current_safety_param));
}

static bool toyota_prius_filter_param(uint16_t param) {
  return toyota_prius_sdsu_param(param) || toyota_prius_advert_param(param);
}

static bool toyota_prius_filter(void) {
  return toyota_prius_filter_param(toyota_pedal_parent_param(current_safety_param));
}

// Disjoint original Prius owners share this sticky receiver identity latch.
static bool toyota_prius_sdsu_ambiguous = false;

static bool toyota_camera_2aa_param(uint16_t param) {
  return (param == 0xC049U) || (param == 0xC249U);
}

static bool toyota_camera_2aa(void) {
  return toyota_camera_2aa_param(toyota_pedal_parent_param(current_safety_param));
}

static bool toyota_camera_2aa_ambiguous = false;

static bool toyota_legacy_advert_param(uint16_t param) {
  return (param == 0x9449U) || (param == 0x9549U) || (param == 0x9558U) || (param == 0x9564U) || (param == 0x9649U) || (param == 0x9749U) || (param == 0x9758U) || (param == 0x9764U) || (param == 0xD44DU) || (param == 0xD549U) || (param == 0xD64DU) || (param == 0xD749U);
}

static bool toyota_legacy_advert(void) {
  return toyota_legacy_advert_param(toyota_pedal_parent_param(current_safety_param));
}


static bool toyota_aol_enabled = false;
static bool toyota_pedal = false;
static bool toyota_pedal_composite = false;
static bool toyota_composite_pcm_gas = false;
static bool toyota_composite_pedal_gas = false;

static bool toyota_pedal_composite_param(uint16_t param) {
  return (param == 0xE449U) || (param == 0xE649U) || (param == 0x3049U) || (param == 0x3249U) || (param == 0x3449U) || (param == 0x3649U) || (param == 0xA042U) || (param == 0xA049U) || (param == 0xA242U) || (param == 0xA249U) || (param == 0xA449U) || (param == 0xA649U) || (param == 0xA849U) || (param == 0xA949U) || (param == 0xA958U) || (param == 0xA964U) || (param == 0xAA49U) || (param == 0xAB49U) || (param == 0xAB58U) || (param == 0xAB64U) || (param == 0xB049U) || (param == 0xB149U) || (param == 0xB158U) || (param == 0xB164U) || (param == 0xB249U) || (param == 0xB349U) || (param == 0xB358U) || (param == 0xB364U) || (param == 0xB449U) || (param == 0xB549U) || (param == 0xB558U) || (param == 0xB564U) || (param == 0xB649U) || (param == 0xB749U) || (param == 0xB758U) || (param == 0xB764U) || (param == 0xB842U) || (param == 0xB849U) || (param == 0xBA42U) || (param == 0xBA49U) || (param == 0xE042U) || (param == 0xE049U) || (param == 0xE242U) || (param == 0xE249U) || (param == 0xE84DU) || (param == 0xE949U) || (param == 0xEA4DU) || (param == 0xEB49U) || (param == 0xF049U) || (param == 0xF04DU) || (param == 0xF149U) || (param == 0xF249U) || (param == 0xF24DU) || (param == 0xF349U) || (param == 0xF44DU) || (param == 0xF549U) || (param == 0xF64DU) || (param == 0xF749U);
}

static uint16_t toyota_pedal_parent_param(uint16_t param) {
  uint16_t parent;
  if ((param == 0xE449U) || (param == 0xE649U)) {
    parent = (param == 0xE449U) ? 0x9849U : 0x9A49U;
  } else {
    parent = toyota_pedal_composite_param(param) ? (uint16_t)(param & ~0x2000U) : param;
  }
  return parent;
}

static bool toyota_alt_cruise = false;

static bool toyota_aol_secoc = false;
static bool toyota_aol_stock = false;
static bool toyota_aol_lta = false;
static bool toyota_aol_rav4h_filter = false;
static bool toyota_sdsu_status = false;
static bool toyota_eps8_sdsu = false;
static bool toyota_legacy_sdsu = false;
static bool toyota_sdsu_control = false;
static bool toyota_aol_main = false;
static bool toyota_aol_drive = false;
static bool toyota_aol_doors_closed = false;
static bool toyota_aol_belt = false;
static bool toyota_aol_eps = false;
static bool toyota_aol_seen[4] = {false, false, false, false};
static uint32_t toyota_aol_ts[4] = {0U, 0U, 0U, 0U};

static void toyota_aol_reset(void) {
  toyota_sdsu_control = false;
  toyota_aol_main = false;
  toyota_aol_drive = false;
  toyota_aol_doors_closed = false;
  toyota_aol_belt = false;
  toyota_aol_eps = false;
  for (unsigned int i = 0U; i < 4U; i++) {
    toyota_aol_seen[i] = false;
    toyota_aol_ts[i] = 0U;
  }
}

static uint8_t toyota_aol_request(void) {
  uint8_t request = 0U;
  if (heartbeat_engaged && aol_rx_healthy() && !relay_malfunction &&
      (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    request = aol_host_axis_mask & (toyota_aol_stock ? 1U : 3U);
  }
  return request;
}

static uint8_t toyota_aol_permission(void) {
  const uint8_t request = toyota_aol_request();
  static const uint32_t source_timeout[4] = {303031U, 10000000U, 3333334U, 400000U};
  bool fresh = true;
  for (unsigned int i = 0U; i < 4U; i++) {
    const uint32_t timeout = ((i == 1U) && toyota_aol_secoc) ? 200000U : source_timeout[i];
    fresh &= toyota_aol_seen[i] && (safety_get_ts_elapsed(microsecond_timer_get(), toyota_aol_ts[i]) <= timeout);
  }
  uint8_t permission = 0U;
  if (fresh && toyota_aol_main && toyota_aol_drive && toyota_aol_doors_closed && toyota_aol_eps &&
      (!toyota_camera_2aa() || !toyota_camera_2aa_ambiguous) &&
      (!toyota_prius_filter() || !toyota_prius_sdsu_ambiguous) && (!toyota_dsu_bypass || !toyota_bypass_ambiguous)) {
    permission = request & 1U;
    if (!toyota_aol_stock && controls_allowed && toyota_aol_belt && !gas_pressed && !brake_pressed &&
        (!toyota_sdsu_status || toyota_sdsu_control) && (!toyota_dsu_bypass || (toyota_bypass_control && !toyota_bypass_aeb))) {
      permission |= request & 2U;
    }
  }
  return permission;
}

static void toyota_aol_observe(const CANPacket_t *msg) {
  const uint32_t gear_addr = toyota_aol_secoc ? 0x127U : 0x3BCU;
  const uint32_t main_addr = toyota_alt_cruise ? 0x365U : 0x1D3U;
  unsigned int expected_len = 8U;
  if (toyota_alt_cruise && (msg->addr == main_addr)) {
    expected_len = 7U;
  } else if ((toyota_factory_eps5 || (toyota_dsu_bypass && toyota_bypass_eps5) || toyota_aol_rav4h_filter || toyota_alt_cruise || toyota_legacy_sdsu || toyota_legacy_advert() || (current_safety_param == 856U)) && (msg->addr == 0x262U)) {
    expected_len = 5U;
  } else {
  }
  if ((toyota_dsu_bypass || toyota_pedal_composite || toyota_aol_enabled || toyota_legacy_advert() || toyota_camera_2aa() || toyota_prius_filter()) && (msg->bus == 0U) && (GET_LEN(msg) != expected_len)) {
    unsigned int index = 4U;
    if (msg->addr == main_addr) {
      index = 0U;
    } else if (msg->addr == gear_addr) {
      index = 1U;
    } else if (msg->addr == 0x620U) {
      index = 2U;
    } else if (msg->addr == 0x262U) {
      index = 3U;
    } else {
    }
    if (index < 4U) {
      toyota_aol_seen[index] = false;
    }
  } else if ((toyota_dsu_bypass || toyota_pedal_composite || toyota_aol_enabled || toyota_legacy_advert() || toyota_camera_2aa() || toyota_prius_filter()) && (msg->bus == 0U) && (GET_LEN(msg) == expected_len)) {
    unsigned int index = 4U;
    if (msg->addr == main_addr) {
      index = 0U;
      toyota_aol_main = toyota_alt_cruise ? GET_BIT(msg, 0U) : (GET_BIT(msg, 15U) && !GET_BIT(msg, 47U));
    } else if (msg->addr == gear_addr) {
      index = 1U;
      if (toyota_aol_secoc) {
        const unsigned int gear = msg->data[5] >> 4U;
        toyota_aol_drive = (gear == 3U) || (gear == 4U);
      } else {
        const unsigned int gear = msg->data[1] & 0x3FU;
        toyota_aol_drive = (gear == 0U) || (gear == 1U);
      }
    } else if (msg->addr == 0x620U) {
      index = 2U;
      toyota_aol_doors_closed = (msg->data[5] & 0x3CU) == 0U;
      toyota_aol_belt = !GET_BIT(msg, 62U);
    } else if (msg->addr == 0x262U) {
      index = 3U;
      const unsigned int state = (msg->data[3] >> 1U) & 0x7FU;
      toyota_aol_eps = (state != 0U) && (state != 9U) && (state != 11U) && (state != 21U) &&
                       (state != 25U) && (state != 3U) && (state != 17U);
      if (toyota_aol_lta) {
        const unsigned int lta_state = (msg->data[1] >> 3U) & 0x1FU;
        toyota_aol_eps = toyota_aol_eps && (lta_state != 0U) && (lta_state != 9U) && (lta_state != 11U) && (lta_state != 21U) &&
                          (lta_state != 25U) && (lta_state != 3U) && (lta_state != 17U);
      }
    } else {
    }
    if (index < 4U) {
      toyota_aol_seen[index] = true;
      toyota_aol_ts[index] = microsecond_timer_get();
    }
  } else {
  }
}

static void toyota_aol_configure(uint16_t param) {
  const bool aol_requested = (alternative_experience == 32) || (alternative_experience == 160) || (alternative_experience == 288);
  toyota_aol_reset();
  toyota_camera_2aa_ambiguous = false;
  toyota_prius_sdsu_ambiguous = false;
  toyota_eps8_sdsu = (param == 0x8049U) || (param == 0x8249U) || (param == 0x8449U) || (param == 0x8649U);
  toyota_legacy_sdsu = (param == 0x9158U) || (param == 0x9358U) || (param == 0x9149U) || (param == 0x9349U) || (param == 0x9164U) || (param == 0x9364U) || (param == 0xd149U) || (param == 0xd349U) || (param == 0xd04dU) || (param == 0xd24dU);
  toyota_alt_cruise = toyota_factory_alt_main || toyota_bypass_alt_main || (param == 0x424DU) || ((toyota_legacy_sdsu || toyota_legacy_advert_param(param)) && ((param & 0x4000U) != 0U));
  toyota_aol_lta = (param == 0x2449U) || (param == 0x2649U) || (param == 0x449U) || (param == 0x649U) || (param == 0x1449U) || (param == 0x1649U) || (param == 0x8449U) || (param == 0x8649U);
  toyota_pedal = (param == 0x2049U) || (param == 0x2249U) || (param == 0x2449U) || (param == 0x2649U);
  toyota_aol_rav4h_filter = (param == 0x9049U) || (param == 0x9249U);
  toyota_sdsu_status = toyota_aol_rav4h_filter || toyota_eps8_sdsu || toyota_legacy_sdsu || toyota_prius_sdsu_param(param);
#ifdef ALLOW_DEBUG
  toyota_aol_secoc = (param == 2121U) || (param == 2633U) || (param == 6217U);
#else
  toyota_aol_secoc = false;
#endif
  toyota_aol_stock = toyota_factory_stock || (toyota_dsu_bypass && ((param & 0x200U) != 0U)) || (param == 0x2649U) || (param == 585U) || (param == 856U) || (param == 4681U) || (param == 0x9249U) || (param == 0x2249U) || (param == 0x424DU) || (param == 0x649U) || (param == 0x1649U) || (param == 0x8249U) || (param == 0x8649U) || ((toyota_legacy_sdsu || toyota_legacy_advert_param(param) || toyota_camera_2aa_param(param) || toyota_prius_filter_param(param)) && ((param & 0x200U) != 0U)) || (toyota_aol_secoc && (param == 2633U));
  toyota_aol_enabled = (toyota_dsu_bypass && aol_requested) || ((toyota_eps8_sdsu || toyota_legacy_sdsu || toyota_legacy_advert_param(param) || toyota_camera_2aa_param(param) || toyota_prius_filter_param(param)) && aol_requested) || (toyota_aol_lta && aol_requested) || (toyota_aol_stock && (!(toyota_aol_secoc || toyota_factory_stock) || (alternative_experience == 32)) && aol_requested) ||
#ifdef ALLOW_DEBUG
                       (toyota_aol_secoc && (alternative_experience == 32)) ||
#endif
                       ((param == 0x9049U) && (aol_requested || (alternative_experience == 160))) ||
                       ((param == 0x2049U) && aol_requested) ||
                       ((param == 4169U) && aol_requested) ||
                       ((param == 73U) && (aol_requested || (alternative_experience == 160) || (alternative_experience == 288)));
  if (toyota_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = toyota_aol_reset,
      .request_mask = toyota_aol_request,
      .permission_mask = toyota_aol_permission,
      .rx_invalid = toyota_aol_reset,
    };
    aol_policy = &policy;
  }
}
