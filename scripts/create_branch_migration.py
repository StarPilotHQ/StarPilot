#!/usr/bin/env python3
"""Prepare a pinned, title-only migration commit in a local review ref; never push."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


HOOK = '''
if [ -f "$DIR/scripts/branch-migration.json" ]; then
  python3 "$DIR/scripts/branch_migration.py" --repo "$DIR" || echo "Branch migration deferred; keeping installed software."
fi
'''


def prepare(repo, source, destination, base, review_ref, token=None):
  repo = Path(repo).resolve()
  if (source, destination) not in (('Dom', 'testing'), ('StarPilot', 'stable')):
    raise ValueError('Only Dom -> testing and StarPilot -> stable are supported')
  token = token or uuid.uuid4().hex
  with tempfile.TemporaryDirectory(prefix='branch-migration-index-') as temporary:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / 'index'))

    def git(*args, data=None):
      return subprocess.check_output(['git', '--no-optional-locks', '-c', 'gc.auto=0', '-c',
                                      'maintenance.auto=false', '-C', str(repo), *args],
                                     input=data, env=env, timeout=30).decode().strip()

    git('check-ref-format', review_ref)
    if not review_ref.startswith('refs/heads/review/'):
      raise ValueError('Use a new local refs/heads/review/ ref')
    parent = git('rev-parse', '--verify', base + '^{commit}')
    if source == 'StarPilot':
      version = git('show', parent + ':selfdrive/ui/lib/starpilot_version.py')
      if not re.search(r'^STARPILOT_DISPLAY_VERSION = [\"\']6\.\d+\.\d+[\"\']$', version, re.MULTILINE):
        raise ValueError('The stable bridge must preserve a reviewed StarPilot 6.x release')
    elif not git('ls-tree', parent, '--', 'prebuilt.json'):
      raise ValueError('The testing bridge requires the reviewed rebase build receipt')
    if git('ls-tree', parent, '--', 'prebuilt').split()[:1] != ['100644']:
      raise ValueError('The source must be a precompiled release')
    git('read-tree', parent)
    launch = git('show', parent + ':launch_chffrplus.sh') + '\n'
    anchor = 'source "$DIR/launch_env.sh"\n'
    if launch.count(anchor) != 1 or 'scripts/branch-migration.json' in launch:
      raise ValueError('Review the source launcher before creating its migration')
    files = {
      'launch_chffrplus.sh': ('100755', launch.replace(anchor, anchor + HOOK)),
      'scripts/branch_migration.py': ('100644', Path(__file__).with_name('branch_migration.py').read_text()),
      'scripts/branch-migration.json': ('100644', json.dumps({
        'source': source, 'destination': destination, 'parent': parent, 'token': token,
      }, sort_keys=True) + '\n'),
    }
    for name, (mode, content) in files.items():
      blob = git('hash-object', '-w', '--stdin', data=content.encode())
      git('update-index', '--add', '--cacheinfo', mode + ',' + blob + ',' + name)
    tree = git('write-tree', '--missing-ok')
    if git('ls-tree', parent, '--', 'prebuilt.json'):
      # Use the canonical inventory rules, including modes and exact Git blobs.
      spec = importlib.util.spec_from_file_location('canonical_receipt', Path(__file__).parents[1] / 'openpilot/common/prebuilt_manifest.py')
      canonical = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(canonical)
      if not canonical.valid_receipt(git, parent):
        raise ValueError('Source release has no valid canonical receipt')
      receipt = {'schema_version': 1, 'protected_inventory_sha256': canonical.revision_digest(git, tree)}
      blob = git('hash-object', '-w', '--stdin', data=(json.dumps(receipt, sort_keys=True) + '\n').encode())
      git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',prebuilt.json')
      tree = git('write-tree', '--missing-ok')
    title = f'Branch-Migration: {source} -> {destination} ({token})'
    commit = git('commit-tree', tree, '-p', parent, data=(title + '\n').encode())
    git('update-ref', review_ref, commit, '0' * 40)
    return {'source': source, 'destination': destination, 'parent': parent,
            'commit': commit, 'review_ref': review_ref, 'token': token}


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repo', type=Path, default=Path.cwd())
  parser.add_argument('--source', choices=['Dom', 'StarPilot'], required=True)
  parser.add_argument('--destination', choices=['testing', 'stable'], required=True)
  parser.add_argument('--base', required=True, help='Exact reviewed source commit')
  parser.add_argument('--review-ref', required=True)
  args = parser.parse_args()
  print(json.dumps(prepare(args.repo, args.source, args.destination, args.base, args.review_ref), indent=2))
