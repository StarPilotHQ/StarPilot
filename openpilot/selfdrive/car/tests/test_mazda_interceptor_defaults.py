"""Mazda interceptor settings must default to off.

The manager writes a key's default when it is missing, and the Mazda interface reads TorqueInterceptorEnabled to
pick the GEN1 Torque Interceptor configuration (GEN1 | TORQUE_INTERCEPTOR safety, TI feedback required). A default
of on silently turned every stock GEN1 car into a TI car on upgrade and took it out of stock always-on lateral
(independent review, P1). Read from the sources so the test runs without the native params library.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INTERCEPTOR_KEYS = ("TorqueInterceptorEnabled", "RadarInterceptorEnabled")


def test_params_keys_default_off():
  text = (ROOT / "common" / "params_keys.h").read_text()
  for key in INTERCEPTOR_KEYS:
    match = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}\}', text)
    assert match, key
    fields = [f.strip() for f in match.group(1).split(",")]
    assert len(fields) == 2 or fields[2] in ('"0"', '""'), f"{key} defaults on: {fields}"


def test_starpilot_settings_default_off():
  tree = ast.parse((ROOT / "starpilot" / "ui" / "feature_settings_owner.py").read_text())
  defaults = next(node.value for node in ast.walk(tree)
                  if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "BOOL_DEFAULTS" for t in node.targets))
  values = {k.value: v.value for k, v in zip(defaults.keys, defaults.values, strict=True) if isinstance(k, ast.Constant)}
  for key in INTERCEPTOR_KEYS:
    assert values[key] is False, key
