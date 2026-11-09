from openpilot.tools.starport import cars_md


def drive(car, unit, outcome, device, **extra):
  return {"aa.report": "drive", "aa.car": car, "aa.head_unit": unit, "aa.outcome": outcome, "device_id": device, **extra}


def test_status_needs_two_devices_to_work_and_hides_tests_and_unknowns():
  drives = [
    drive("Honda Civic", "Alpine", "streamed", "a", **{"aa.transport": "wireless"}),
    drive("Honda Civic", "Alpine", "recovered", "b"),
    drive("Chevrolet myChevrolet", "Chevrolet", "streamed", "a"),
    drive("Chevrolet myChevrolet", "Chevrolet", "failed", "c", **{"aa.stage": "negotiating"}),
    drive("Kia EV6", "Kia", "failed", "a", **{"aa.stage": "joining_wifi"}),
    drive("unknown", "Alpine", "streamed", "a"),
    drive("Google DHU", "Desktop Head Unit", "streamed", "a"),
  ]
  rows = {row["car"]: row for row in cars_md.build(drives)}
  assert set(rows) == {"Honda Civic", "Chevrolet myChevrolet", "Kia EV6"}
  assert (rows["Honda Civic"]["status"], rows["Chevrolet myChevrolet"]["status"], rows["Kia EV6"]["status"]) == (
    cars_md.WORKS, cars_md.PARTLY, cars_md.NOT_YET)
  assert rows["Kia EV6"]["stuck_at"] == "joining_wifi"


def test_overrides_relabel_cars_and_output_has_no_device_ids():
  rows = cars_md.build([drive("Civic??", "Alpine", "streamed", "dev-123")], {"Civic??": "Honda Civic"})
  text = cars_md.render(rows)
  assert "Honda Civic" in text and "dev-123" not in text
