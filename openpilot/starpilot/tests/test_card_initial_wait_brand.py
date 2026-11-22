"""The Card Volkswagen initial-wait lookup is never entered for other brands."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('brand', ['ford', 'hyundai', 'honda', 'toyota', 'gm', 'subaru', 'volkswagen'])
@pytest.mark.parametrize('waiting', [False, True])
def test_card_initial_wait_lookup_is_brand_scoped(brand, waiting):
  path = Path(__file__).resolve().parents[2] / 'selfdrive/car/card.py'
  tree = ast.parse(path.read_text())
  expressions = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'volkswagen_initial_wait' for target in node.targets)]
  assert len(expressions) == 1
  expression = expressions[0]
  lookup = next(node for node in ast.walk(expression) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'volkswagen_initial_request')
  # Keep the actual production guard/execution; only replace arguments with sentinels.
  lookup.args = []
  lookup.keywords = []
  probe = Mock(return_value=waiting)
  code = compile(ast.fix_missing_locations(ast.Expression(expression)), str(path), 'eval')
  result = eval(code, {'self': SimpleNamespace(CP=SimpleNamespace(brand=brand)), 'volkswagen_initial_request': probe})
  assert result == (brand == 'volkswagen' and waiting)
  assert probe.call_count == int(brand == 'volkswagen')
