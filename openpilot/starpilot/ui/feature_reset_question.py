"""Confirmation text for native feature reset dialogs."""
from openpilot.starpilot.ui.long_profile_feature import long_confirm_question
from openpilot.starpilot.ui.controller_feature import SETUP_ACTION, SETUP_QUESTION


def feature_reset_question(action_key, row):
  return {"torque_adopt": "Use the new torque editor for this vehicle model and configuration? Existing saved values will be kept but no longer used.",
              SETUP_ACTION: SETUP_QUESTION,
              "torque_reset": "Reset invalid torque profiles? Existing legacy values will be kept but remain inactive.",
              "torque_reset_profile": "Reset this vehicle model's custom torque profile to Vehicle/learned? Other models and legacy values remain saved.",
              "torque_gain_rebase": "Apply the saved steering response to the selected controller and current vehicle tune? " +
                                   "Friction and other models stay unchanged.",
              "torque_rebase": f"Reapply saved torque values ({row.value}) with the current vehicle tune? " +
                               "Saved values must pass current limits.",
              "slc_reset": f"Reset speed-limit offsets to zero? {row.value}. " +
                           "Saved SLC control may resume if its switch is On; the switch will not change.",
              "curve_reset": "Reset saved Curve learning? Your Curve Speed Controller On/Off choice will stay the same.",
              "reset_profiles": "Reset invalid saved Long Planner profiles to defaults?"}.get(action_key, long_confirm_question(row))
