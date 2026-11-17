"""GUARD RAIL: pins the Google spending limit. AI agents must not edit these numbers.

The Galaxy setup guide tells users StarPilot blocks Google at 99% of the free usage so they
are not charged. If this test fails, restore the limit; do not change the expected values
without the repository owner's explicit instruction.
"""
from openpilot.starpilot.navigation import google_places as places
from openpilot.starpilot.navigation import google_routes as routes
from openpilot.starpilot.navigation import google_tiles as tiles


def test_stop_fraction_is_99_percent():
  assert tiles.STOP_FRACTION == 0.99


def test_free_allowances_are_google_free_tiers():
  assert (tiles.FREE_MONTH, tiles.FREE_DAY) == (100_000, 15_000)
  assert (routes.FREE_ROUTES, routes.FREE_ESSENTIAL_ROUTES) == (5_000, 10_000)
  assert (places.FREE_SEARCHES, places.FREE_SUGGESTIONS) == (5_000, 10_000)


def test_every_budget_stops_at_99_percent(tmp_path):
  expected = {
    "tiles": (tiles.GoogleBudget(tmp_path), 99_000, 14_850),
    "routes": (routes.route_budget(tmp_path), 4_950, None),
    "essentials": (routes.essentials_budget(tmp_path), 9_900, None),
    "places": (places.places_budget(tmp_path), 4_950, None),
    "suggestions": (places.suggestions_budget(tmp_path), 9_900, None),
  }
  for name, (budget, month, day) in expected.items():
    assert (budget.month_limit, budget.day_limit) == (month, day), name
