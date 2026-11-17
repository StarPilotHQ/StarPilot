"""Bounded local history and plain-text notes from the existing updater."""

from datetime import datetime
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import subprocess

from openpilot.starpilot.saved_source import read_saved

COMMIT = re.compile(r'[0-9a-f]{40}\Z')
MAX_COMMITS = 100
MAX_NOTES_BYTES = 32768
METADATA_ROOT = Path(os.environ.get('UPDATER_METADATA_ROOT', '/data/starpilot/software/metadata.git'))


def commit_history(path: Path, commit: str, *, first_parent: bool = False) -> list[dict]:
  if not COMMIT.fullmatch(commit):
    return []
  try:
    output = subprocess.run(
      ['git', '-C', str(path), '--no-pager', 'log', '--no-decorate', '--no-color', '--no-show-signature',
       *(['--first-parent'] if first_parent else []), f'-{MAX_COMMITS}', '--format=%H%x00%cI%x00%<(240,trunc)%s%x00', commit, '--'],
      check=True, capture_output=True, text=True, timeout=2).stdout
    if len(output.encode()) > MAX_COMMITS * 800:
      return []
    fields = output.split('\0')
    rows = []
    for index in range(0, len(fields) - 2, 3):
      sha, date, subject = fields[index].strip(), fields[index + 1], fields[index + 2].rstrip()
      if not COMMIT.fullmatch(sha) or len(subject) > 500:
        return []
      parsed = datetime.fromisoformat(date)
      if parsed.tzinfo is None:
        return []
      rows.append({'hash': sha, 'date': parsed.isoformat(), 'subject': ''.join(c for c in subject if c.isprintable())})
    return rows[:MAX_COMMITS]
  except (OSError, ValueError, subprocess.SubprocessError):
    return []


def previous_version(path: Path) -> dict | None:
  try:
    commit = subprocess.run(['git', '-C', str(path), 'rev-parse', '--verify', 'refs/starpilot/previous^{commit}'],
                            check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    rows = commit_history(path, commit)
    if not rows:
      return None
    result = rows[0]
    saved = subprocess.run(['git', '-C', str(path), 'config', '--local', '--get', 'starpilot.previousVersion'],
                           capture_output=True, text=True, timeout=2).stdout.strip().split(' ', 1)
    if len(saved) == 2 and saved[0] == commit and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,127}', saved[1]):
      result['branch'] = saved[1]
    return result
  except (OSError, subprocess.SubprocessError):
    return None


def downloaded_versions(paths, commits):
  """Check local trees in batches, sharing checks of unchanged subtrees."""
  requested = list(dict.fromkeys(commit for commit in commits if COMMIT.fullmatch(commit)))
  available = set()
  for path in paths:
    def git(*args, data=None, path=path):
      return subprocess.run(['git', '-C', str(path), *args], input=data, check=True,
                            capture_output=True, timeout=2).stdout

    def objects(hashes):
      output = git('cat-file', '--batch', data=''.join(sha + '\n' for sha in hashes).encode())
      result = {}
      offset = 0
      while offset < len(output):
        end = output.index(b'\n', offset)
        sha, kind, size = output[offset:end].split()
        offset = end + 1
        size = int(size)
        result[sha.decode()] = (kind, output[offset:offset + size])
        offset += size + 1
      return result

    try:
      # Enumerate local objects first. Never ask cat-file for a missing object,
      # which could cause a partial clone to fetch it just to render a badge.
      existing = set(git('cat-file', '--batch-all-objects', '--batch-check=%(objectname)').decode().splitlines())
      pending = [sha for sha in requested if sha not in available and sha in existing]
      if not pending:
        continue
      roots = {sha: body.split(b'\n', 1)[0][5:].decode() for sha, (kind, body) in objects(pending).items()
               if kind == b'commit' and body.startswith(b'tree ')}
      trees = {}
      todo = set(roots.values()) & existing
      while todo:
        following = set()
        for sha, (kind, body) in objects(todo).items():
          if kind != b'tree':
            continue
          children = []
          offset = 0
          while offset < len(body):
            space = body.index(b' ', offset)
            end = body.index(b'\0', space)
            child = body[end + 1:end + 21].hex()
            is_tree = body[offset:space] == b'40000'
            children.append((child, is_tree))
            if is_tree and child in existing:
              following.add(child)
            offset = end + 21
          trees[sha] = children
        todo = following - trees.keys()
      complete = {}
      def has_tree(sha, complete=complete, trees=trees, existing=existing):
        if sha not in complete:
          children = trees.get(sha)
          complete[sha] = children is not None and all(child in existing and (not tree or has_tree(child))
                                                       for child, tree in children)
        return complete[sha]
      available.update(sha for sha, root in roots.items() if has_tree(root))
    except (OSError, ValueError, subprocess.SubprocessError):
      continue
  return [sha for sha in requested if sha in available]


class _NotesText(HTMLParser):
  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.parts = []
    self.hidden = 0
    self.heading = 0
    self.content = []

  def handle_starttag(self, tag, attrs):
    if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
      self.heading += 1
    if tag in ('script', 'style'):
      self.hidden += 1
    elif not self.hidden and tag in ('br', 'p', 'li', 'h1', 'h2', 'h3'):
      self.parts.append('\n')

  def handle_endtag(self, tag):
    if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
      self.heading = max(0, self.heading - 1)
    if tag in ('script', 'style'):
      self.hidden = max(0, self.hidden - 1)
    elif not self.hidden and tag in ('p', 'li', 'h1', 'h2', 'h3'):
      self.parts.append('\n')

  def handle_data(self, data):
    if not self.hidden:
      self.parts.append(data)
      if not self.heading:
        self.content.append(data)


def release_notes(params, key: str) -> str | None:
  raw, readable = read_saved(params, key, MAX_NOTES_BYTES)
  if not readable or raw is None:
    return None
  try:
    parser = _NotesText()
    parser.feed(raw.decode('utf-8'))
    parser.close()
    if not re.sub(r'^\s*#{1,6}\s+.*$', '', ''.join(parser.content), flags=re.MULTILINE).strip():
      return None
    return re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip()
  except (UnicodeError, ValueError):
    return None


def recent_versions(path: Path, branch: str | None) -> dict:
  empty = {'branch': branch, 'head': None, 'entries': []}
  if type(branch) is not str or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,127}', branch):
    return empty
  ref = f'refs/starpilot/recent/{branch}'
  try:
    subprocess.run(['git', '-C', str(path), 'check-ref-format', ref], check=True, capture_output=True, timeout=2)
    head = subprocess.run(['git', '-C', str(path), 'rev-parse', '--verify', ref + '^{commit}'],
                          check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    if not COMMIT.fullmatch(head):
      return empty
    return {'branch': branch, 'head': head, 'entries': commit_history(path, head, first_parent=True)}
  except (OSError, subprocess.SubprocessError):
    return empty
