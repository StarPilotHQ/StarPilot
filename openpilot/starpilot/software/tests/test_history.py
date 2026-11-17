from pathlib import Path
from types import SimpleNamespace
import subprocess
import shutil

from openpilot.starpilot.software.history import MAX_COMMITS, commit_history, downloaded_versions, release_notes, recent_versions, previous_version


def test_history_is_bounded_and_pinned_to_the_requested_commit(tmp_path):
  def git(*args):
    return subprocess.run(['git', '-C', str(tmp_path), '-c', 'core.hooksPath=/dev/null',
                           '-c', 'user.name=Test', '-c', 'user.email=test@example.com', *args],
                          check=True, capture_output=True, text=True).stdout.strip()
  git('init', '-q')
  for index in range(MAX_COMMITS + 3):
    git('commit', '--allow-empty', '-qm', f'Change {index:02d}')
  commit = git('rev-parse', 'HEAD~1')
  rows = commit_history(tmp_path, commit)
  assert len(rows) == MAX_COMMITS
  assert rows[0]['hash'] == commit and rows[0]['subject'] == f'Change {MAX_COMMITS + 1}'
  assert rows[-1]['subject'] == 'Change 02'
  assert all(row['date'] for row in rows)
  assert commit_history(tmp_path, '--all') == []
  assert commit_history(tmp_path, 'f' * 40) == []
  assert recent_versions(tmp_path, 'Dom')['entries'] == []
  git('update-ref', 'refs/starpilot/recent/Dom', commit)
  recent = recent_versions(tmp_path, 'Dom')
  assert recent['head'] == commit and recent['branch'] == 'Dom' and recent['entries'] == rows
  assert recent_versions(tmp_path, '../escape')['entries'] == []
  assert recent_versions(tmp_path, 'Dom')['entries'][0]['hash'] == commit
  assert previous_version(tmp_path) is None
  git('update-ref', 'refs/starpilot/previous', commit)
  assert previous_version(tmp_path) == rows[0]
  git('config', '--local', 'starpilot.previousVersion', f'{commit} old-branch')
  previous = previous_version(tmp_path)
  assert previous is not None
  assert previous['branch'] == 'old-branch'
  git('update-ref', '-d', 'refs/starpilot/previous')
  assert previous_version(tmp_path) is None


def test_downloaded_versions_require_all_files_and_accept_a_staging_cache(tmp_path):
  repo = tmp_path / 'repo'
  repo.mkdir()
  def git(*args):
    return subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=Test',
                                    '-c', 'user.email=test@example.com', *args], text=True).strip()
  git('init', '-q')
  (repo / 'file').write_text('old')
  (repo / 'nested').mkdir()
  (repo / 'nested/shared').write_text('shared subtree')
  git('add', '.')
  git('commit', '-qm', 'Old')
  old = git('rev-parse', 'HEAD')
  (repo / 'file').write_text('new')
  git('commit', '-qam', 'New')
  new = git('rev-parse', 'HEAD')
  cache = tmp_path / 'cache'
  shutil.copytree(repo, cache)
  assert downloaded_versions((repo,), [old, new, '--all']) == [old, new]
  blob = git('rev-parse', 'HEAD:file')
  (repo / '.git/objects' / blob[:2] / blob[2:]).unlink()
  assert downloaded_versions((repo,), [old, new]) == [old]
  assert downloaded_versions((repo, cache), [old, new]) == [old, new]
  (repo / '.git/objects' / blob[:2] / blob[2:]).write_bytes((cache / '.git/objects' / blob[:2] / blob[2:]).read_bytes())
  tree = git('rev-parse', 'HEAD^{tree}')
  (repo / '.git/objects' / tree[:2] / tree[2:]).unlink()
  assert downloaded_versions((repo,), [old, new]) == [old]


def test_release_notes_are_bounded_plain_text(tmp_path):
  params = SimpleNamespace(get_param_path=lambda key: str(tmp_path / key))
  path = Path(params.get_param_path('UpdaterCurrentReleaseNotes'))
  assert release_notes(params, path.name) is None
  path.write_text('<h1>StarPilot</h1><ul><li>First &amp; second</li></ul><script>danger()</script><style>.bad{}</style>')
  assert release_notes(params, path.name) == 'StarPilot\n\nFirst & second'
  path.write_bytes(b'x' * 32769)
  assert release_notes(params, path.name) is None
  path.write_bytes(b'\xff')
  assert release_notes(params, path.name) is None
  for empty in ('', ' \n\t', '# StarPilot 7.0 — Domathon development\n', '<h1>StarPilot 7.0</h1>', '<h2>Version</h2><p> </p>'):
    path.write_text(empty)
    assert release_notes(params, path.name) is None
  path.write_text('# Version\n\n* Fixed updates')
  assert release_notes(params, path.name) == '# Version\n\n* Fixed updates'
