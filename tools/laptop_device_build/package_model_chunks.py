"""Package the compiled small-model outputs using established Dom model parts."""
import argparse
from pathlib import Path


# Resolve this checkout's stdlib-only owner, even for an absolute CLI invocation
# with no PYTHONPATH or installed workspace package.
import importlib.util
_spec = importlib.util.spec_from_file_location("model_file_chunker", Path(__file__).resolve().parents[2] / "openpilot/common/file_chunker.py")
_chunk_owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chunk_owner)
package_file = _chunk_owner.package_file

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--source', type=Path, required=True)
  parser.add_argument('--destination', type=Path, required=True)
  args = parser.parse_args()
  for name in ('dmonitoring_model_tinygrad.pkl', 'cheddar_driving_tinygrad.pkl'):
    source = (_chunk_owner.materialize_file_chunked(args.source / name)
              if name == 'cheddar_driving_tinygrad.pkl' else args.source / name)
    package_file(source, args.destination / name)

if __name__ == '__main__':
  main()
