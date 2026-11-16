from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FINGERPRINT_MAKE_TO_VALUES_DIR = {
  "acura": "honda",
  "audi": "volkswagen",
  "buick": "gm",
  "cadillac": "gm",
  "chevrolet": "gm",
  "chrysler": "chrysler",
  "cupra": "volkswagen",
  "dodge": "chrysler",
  "ford": "ford",
  "genesis": "hyundai",
  "gmc": "gm",
  "holden": "gm",
  "honda": "honda",
  "hyundai": "hyundai",
  "jeep": "chrysler",
  "kia": "hyundai",
  "lexus": "toyota",
  "lincoln": "ford",
  "man": "volkswagen",
  "mazda": "mazda",
  "nissan": "nissan",
  "peugeot": "psa",
  "polestar": "volvo",
  "ram": "chrysler",
  "rivian": "rivian",
  "seat": "volkswagen",
  "\u0161koda": "volkswagen",
  "subaru": "subaru",
  "tesla": "tesla",
  "toyota": "toyota",
  "volkswagen": "volkswagen",
  "volvo": "volvo",
}

_FINGERPRINT_CARDOCS_RE = re.compile(r'\w*CarDocs\w*\(\s*"([^"]+)"')
_FINGERPRINT_PLATFORM_RE = re.compile(r'(\w+)\s*=\s*\w+\s*\(\s*\[([\s\S]*?)\]\s*,')
_FINGERPRINT_PLATFORM_NAME_RE = re.compile(r'^[A-Z0-9_]+$')
_FINGERPRINT_VALID_NAME_RE = re.compile(r'^[A-Za-z0-9 \u0160.(),&\-]+$')


@dataclass(frozen=True)
class FingerprintModelOption:
  value: str
  label: str
  short_label: str
  option_label: str
  button_label: str


def format_fingerprint_value(value: str) -> str:
  formatted_tokens: list[str] = []
  for token in value.split("_"):
    if not token:
      continue

    if token.isupper() and (len(token) <= 4 or any(char.isdigit() for char in token)):
      formatted_tokens.append(token)
    else:
      formatted_tokens.append(token.title())

  return " ".join(formatted_tokens)


def shorten_model_label(make: str, label: str) -> str:
  prefix = f"{make} "
  if label.lower().startswith(prefix.lower()):
    return label[len(prefix):]
  return label


def compact_model_label(label: str) -> str:
  compact = re.sub(r'\s+\([^)]*\)', "", label)
  compact = re.sub(r'\s+-\s+.*$', "", compact)
  compact = re.sub(r'\s+(?:ASCM Harness|Camera Harness|SDGM Harness(?:.*)?|ACC w Pedal|Instrument Cluster)$', "", compact)
  compact = re.sub(r'\s{2,}', " ", compact).strip()
  return compact or label


def _get_openpilot_root() -> Path:
  for parent in Path(__file__).resolve().parents:
    if (parent / "opendbc" / "car").is_dir() or (parent / "selfdrive" / "car").is_dir():
      return parent

  return Path(__file__).resolve().parents[5]


def _extract_fingerprint_models_for_source(source_make: str, root: Path) -> dict[str, list[tuple[str, str, str]]]:
  values_candidates = (
    root / "opendbc" / "car" / source_make / "values.py",
    root / "selfdrive" / "car" / source_make / "values.py",
  )
  values_path = next((path for path in values_candidates if path.is_file()), None)
  if values_path is None:
    return {}

  try:
    content = values_path.read_text(encoding="utf-8", errors="replace")
  except Exception:
    return {}

  content = re.sub(r'#[^\n]*', "", content)
  content = re.sub(r'footnotes=\[[^\]]*\],\s*', "", content)

  models: dict[str, list[tuple[str, str, str]]] = {}
  seen: set[tuple[str, str]] = set()

  for platform_match in _FINGERPRINT_PLATFORM_RE.finditer(content):
    platform_name = platform_match.group(1)
    if not _FINGERPRINT_PLATFORM_NAME_RE.match(platform_name):
      continue

    platform_section = platform_match.group(2)
    for name_match in _FINGERPRINT_CARDOCS_RE.finditer(platform_section):
      car_name = name_match.group(1).strip()
      if " " not in car_name or not _FINGERPRINT_VALID_NAME_RE.match(car_name):
        continue

      make_label = car_name.split(" ", 1)[0]
      dedupe_key = (car_name, platform_name)
      if dedupe_key in seen:
        continue

      seen.add(dedupe_key)
      models.setdefault(make_label.lower(), []).append((platform_name, car_name, make_label))

  for entries in models.values():
    entries.sort(key=lambda entry: entry[1].lower())
  return models


def _extract_fingerprint_models_for_make(make_key: str) -> list[tuple[str, str, str]]:
  source_make = FINGERPRINT_MAKE_TO_VALUES_DIR.get(make_key, make_key)
  return _extract_fingerprint_models_for_source(source_make, _get_openpilot_root()).get(make_key, [])


@lru_cache(maxsize=1)
def get_fingerprint_catalog() -> tuple[
  tuple[str, ...],
  dict[str, tuple[FingerprintModelOption, ...]],
  dict[str, FingerprintModelOption],
  dict[str, str],
]:
  makes: list[str] = []
  models_by_make: dict[str, tuple[FingerprintModelOption, ...]] = {}
  model_by_value: dict[str, FingerprintModelOption] = {}
  make_by_model: dict[str, str] = {}

  root = _get_openpilot_root()
  sources: dict[str, dict[str, list[tuple[str, str, str]]]] = {}
  for make_key in sorted(FINGERPRINT_MAKE_TO_VALUES_DIR):
    source_make = FINGERPRINT_MAKE_TO_VALUES_DIR[make_key]
    if source_make not in sources:
      sources[source_make] = _extract_fingerprint_models_for_source(source_make, root)
    entries = sources[source_make].get(make_key, [])
    if not entries:
      continue

    make_label = entries[0][2]
    short_counts = Counter(shorten_model_label(make_label, label) for _, label, _ in entries)
    options: list[FingerprintModelOption] = []
    used_option_labels: set[str] = set()

    for value, label, _ in entries:
      short_label = shorten_model_label(make_label, label)
      compact_label = compact_model_label(short_label)

      # Prefer the shorter label in mici, but fall back when it would collide.
      option_label = short_label if short_counts[short_label] == 1 else label
      if option_label in used_option_labels:
        option_label = f"{option_label} ({value})"
      used_option_labels.add(option_label)
      button_label = compact_label if len(compact_label) < len(short_label) else short_label

      option = FingerprintModelOption(
        value=value,
        label=label,
        short_label=short_label,
        option_label=option_label,
        button_label=button_label,
      )
      options.append(option)
      model_by_value.setdefault(value, option)
      make_by_model.setdefault(value, make_label)

    makes.append(make_label)
    models_by_make[make_label] = tuple(options)

  makes.sort(key=str.lower)
  return tuple(makes), models_by_make, model_by_value, make_by_model
