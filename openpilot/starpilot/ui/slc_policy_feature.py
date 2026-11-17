from openpilot.starpilot.speed_limits.runtime_settings import (
  ADVANCE_BYTES, ADVANCE_KEYS, ADVANCE_SECONDS, CONFIRMATION_BYTES, CONFIRMATION_CHOICES,
  PEDAL_OVERRIDE_BYTES, PEDAL_OVERRIDE_CHOICES, PEDAL_OVERRIDE_KEY, PedalOverride,
)
from openpilot.starpilot.ui.feature_settings_state import FeatureRow

SLC_ADVANCE_CHOICES = ('Off', *(f'{seconds} s' for seconds in ADVANCE_SECONDS[1:]))


def policy_rows(read_value, readable, *, allowed):
  rows = []
  _, raw_confirmation, readable_confirmation = read_value('SLCConfirmation')
  valid_confirmation = readable_confirmation and raw_confirmation in (None, *CONFIRMATION_BYTES)
  confirmation = CONFIRMATION_CHOICES[int(raw_confirmation or b'0')] if valid_confirmation else 'Invalid'
  rows.append(FeatureRow('SLCConfirmation', 'Confirm limits', confirmation, raw_confirmation,
    tuple(CONFIRMATION_CHOICES.values()), available=allowed and readable('SLCConfirmation'), default_value='Off',
    repair_value='' if valid_confirmation else 'Both',
    reason='Off adopts limits automatically. For prompts, + accepts and − declines within 30 seconds'
      if valid_confirmation else 'Invalid saved confirmation mode; choose a confirmation policy'))
  for key, direction in zip(ADVANCE_KEYS, ('higher', 'lower'), strict=True):
    advance, raw_advance, readable_advance = read_value(key)
    valid_advance = readable_advance and (raw_advance is None or raw_advance in ADVANCE_BYTES)
    rows.append(FeatureRow(key, f'Adopt {direction} limits early',
      SLC_ADVANCE_CHOICES[int(advance)] if valid_advance else f'Saved {advance}', raw_advance,
      SLC_ADVANCE_CHOICES if valid_advance else ('Off',), available=allowed, default_value='Off',
      reason=f'Offer or adopt {direction} mapped limits this many seconds early at the current speed'
        if valid_advance else 'Invalid saved advance time; restore Off'))
  _, raw_override, readable_override = read_value(PEDAL_OVERRIDE_KEY)
  resume_limit = PEDAL_OVERRIDE_CHOICES[PedalOverride.OFF]
  default_override = PEDAL_OVERRIDE_CHOICES[PedalOverride.SPEED_REACHED]
  valid_override = readable_override and (raw_override is None or raw_override in PEDAL_OVERRIDE_BYTES)
  rows.append(FeatureRow(PEDAL_OVERRIDE_KEY, 'After an accelerator override',
    default_override if raw_override is None else PEDAL_OVERRIDE_CHOICES[int(raw_override)] if valid_override else 'Invalid',
    raw_override, PEDAL_OVERRIDE_CHOICES if valid_override else (resume_limit,),
    available=allowed and readable(PEDAL_OVERRIDE_KEY), default_value=default_override,
    repair_value='' if valid_override else resume_limit,
    reason='Cruise uses this target when you lift off the accelerator. Held speed stays at or below SET.'))
  return rows
