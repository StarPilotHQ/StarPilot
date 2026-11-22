from pathlib import Path
import subprocess


def test_partial_dashboard_browser_contract():
  result = subprocess.run(['node', str(Path(__file__).with_suffix('.js'))], capture_output=True, text=True, timeout=30, check=False)
  assert result.returncode == 0, result.stdout + result.stderr
