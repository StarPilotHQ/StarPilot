"""Build a list of cars that work with StarPort from the drive reports in Bugsink (tags only, no device ids).

Usage: BUGSINK_TOKEN=... python -m openpilot.tools.starport.cars_md [--out CARS.md] [--base URL] [--project N]
"""

import argparse
import json
import os
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_BASE = "https://bugsink.firestar.link/api/canonical/0"
OVERRIDES = Path(__file__).with_name("cars_overrides.json")
TEST_HEAD_UNITS = ("desktop head unit", "androidauto androidauto", "hur", "headunitpad", "openheadunit")
UNKNOWN = ("", "unknown", "none")
WORKS, PARTLY, NOT_YET = "Works", "Partly", "Not working yet"


def fetch(base: str, token: str, path: str) -> dict:
  request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token, "User-Agent": "curl/8.7.1"})
  with urllib.request.urlopen(request, timeout=60) as response:
    return json.load(response)


def pages(base: str, token: str, path: str | None):
  while path:
    page = fetch(base, token, path)
    yield from page["results"]
    path = page["next"].replace(base, "") if page.get("next") else None


def drive_tags(base: str, token: str, project: int):
  for issue in pages(base, token, f"/issues/?project={project}"):
    for event in pages(base, token, f"/events/?issue={issue['id']}"):
      detail = fetch(base, token, f"/events/{event['id']}/")
      tags = detail.get("tags") or {}
      if isinstance(tags, list):
        tags = {item[0] if isinstance(item, (list, tuple)) else item["key"]: item[1] if isinstance(item, (list, tuple)) else item["value"] for item in tags}
      if tags.get("aa.report") == "drive":
        yield tags


def is_test_unit(label: str) -> bool:
  return any(name in label.lower() for name in TEST_HEAD_UNITS)


def classify(drives: list[dict]) -> str:
  """Works: streamed or recovered on at least two devices. Partly: some drive streamed. Otherwise not working yet."""
  good = {d.get("device_id", "") for d in drives if d.get("aa.outcome") in ("streamed", "recovered")}
  if len(good) >= 2:
    return WORKS
  return PARTLY if good else NOT_YET


def build(drives, overrides: dict | None = None) -> list[dict]:
  overrides = overrides or {}
  groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
  for tags in drives:
    car, unit = tags.get("aa.car", ""), tags.get("aa.head_unit", "")
    if car.lower() in UNKNOWN or is_test_unit(unit) or is_test_unit(car):
      continue
    groups[(overrides.get(car, car), unit)].append(tags)
  rows = []
  for (car, unit), items in sorted(groups.items()):
    stages = Counter(d.get("aa.stage") for d in items if d.get("aa.stage"))
    rows.append({"car": car, "head_unit": unit, "status": classify(items), "drives": len(items),
                 "transports": sorted({d.get("aa.transport", "") for d in items if d.get("aa.transport")}),
                 "adapters": sorted({d["aa.adapter_model"] for d in items if d.get("aa.adapter_model")}),
                 "stuck_at": stages.most_common(1)[0][0] if stages else ""})
  return rows


def render(rows: list[dict]) -> str:
  lines = ["| Car | Head unit | Status | Transport | Adapter | Drives | Stuck at |", "|---|---|---|---|---|---|---|"]
  for row in rows:
    cells = [row["car"], row["head_unit"], row["status"], ", ".join(row["transports"]), ", ".join(row["adapters"]), str(row["drives"]),
             row["stuck_at"] if row["status"] != WORKS else ""]
    lines.append("| " + " | ".join(cells) + " |")
  return "\n".join(lines) + "\n"


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--out")
  parser.add_argument("--base", default=DEFAULT_BASE)
  parser.add_argument("--project", type=int, default=3)
  args = parser.parse_args()
  token = os.environ.get("BUGSINK_TOKEN", "")
  if not token:
    parser.error("set BUGSINK_TOKEN")
  overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
  text = render(build(drive_tags(args.base, token, args.project), overrides))
  if args.out:
    Path(args.out).write_text(text)
  else:
    print(text, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
