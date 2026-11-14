"""SLC presentation groups shared by native controls and Galaxy.

Only navigation changes here. Rows retain the original saved sources, capability,
compare-and-save dependencies, edit permissions and confirmation requirements.
"""

from openpilot.starpilot.ui.feature_settings_state import FeatureRow

SECTIONS = (
  (
    "slc/sources",
    "Limit sources",
    "Where the speed limit comes from",
    frozenset(("SLCPriority1", "SLCPriority2", "SLCPriority3", "SLCFallbackDelay", "SLCLastLimitFallback")),
  ),
  (
    "slc/offsets",
    "Speed-limit offsets",
    "Drive above or below the limit",
    frozenset(("SLCOffsetType", "SLCOffsetPercent", "slc_reset", *(f"slc:offset:{i}" for i in range(1, 8)))),
  ),
  (
    "slc/changes",
    "Limit changes",
    "When to speed up or slow down for a new limit",
    frozenset(
      (
        "SLCConfirmation",
        "SLCAdvanceHigherSeconds",
        "SLCAdvanceLowerSeconds",
        "SLCHigherLimitMode",
        "SLCHigherLimitDistance",
        "SLCHigherLimitTime",
        "SLCHigherLimitOver",
        "SLCHigherWarningLeadTime",
        "SLCLowerLimitMode",
        "SLCLowerLimitDistance",
        "SLCLowerLimitTime",
        "SLCLowerLimitTiming",
        "SLCLowerWarningLeadTime",
        "SLCLimitAcceleration",
        "SLCLimitBraking",
      )
    ),
  ),
  (
    "slc/reengagement",
    "Overrides and engaging",
    "Your own set speed, pedal overrides and engaging",
    frozenset(
      (
        "SLCSetsSetSpeed",
        "SLCManualSpeedMemory",
        "SLCPedalSetsSpeed",
        "SLCReengageSpeed",
        "SLCReengageKeepFaster",
        "SLCOverrideMode",
        "SLCOnEngage",
        "SLCWheelOverride",
      )
    ),
  ),
)
SLC_PAGES = tuple(section[0] for section in SECTIONS)


def menu_rows(page, rows):
  if page == "slc":
    # Keep unknown future settings visible until explicitly assigned to a group.
    grouped = frozenset().union(*(section[3] for section in SECTIONS))
    root = [row for row in rows if row.key and row.key not in grouped]
    root.extend(FeatureRow("", title, description, page=child, available=True) for child, title, description, _ in SECTIONS)
    return "Speed Limit Controller", root
  for child, title, _, keys in SECTIONS:
    if page == child:
      chosen = [row for row in rows if row.key in keys or (child == "slc/offsets" and not row.key)]
      if child == "slc/changes":
        # Confirm limits leads the page (the warning lead times below gray out while it asks), then the Adopt limits
        # early times (a direction's start mode below grays out while its time is set).
        lead = ("SLCConfirmation", "SLCAdvanceHigherSeconds", "SLCAdvanceLowerSeconds")
        chosen.sort(key=lambda row: lead.index(row.key) if row.key in lead else len(lead))
      return title, chosen
  raise ValueError("Unknown SLC submenu")
