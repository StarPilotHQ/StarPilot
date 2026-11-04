import os
from pathlib import Path
import re
import subprocess
import time
import selectors
import signal
import tempfile

from openpilot.starpilot.galaxy.companion import phone_uplink_active
from openpilot.starpilot.software.history import MAX_COMMITS

from openpilot.common.vendor_manifest import validate_revision
from openpilot.common.prebuilt_manifest import revision_digest, valid_receipt


RECENT_VERSION_LIMIT = 20


class FastUpdateError(RuntimeError):
  pass


class FastUpdateIncompatible(FastUpdateError):
  pass


def required_os_version(launch):
  versions = re.findall(r'^\s*export\s+AGNOS_VERSION=["\']([^"\'\n]+)["\']\s*$', launch, re.MULTILINE)
  return versions[0] if len(versions) == 1 else None


def legacy_os_compatible(launch, current_os):
  required_os = required_os_version(launch)
  accepted = re.findall(r'^\s*export\s+AGNOS_ACCEPTED_VERSIONS=["\']([^"\'\n]+)["\']\s*$', launch, re.MULTILINE)
  accepted_os = {required_os}
  if len(accepted) == 1:
    accepted_os.update(accepted[0].split())
  return bool(required_os and current_os and current_os in accepted_os)


def validate_legacy_return(git, revision, branch):
  if branch not in ('Dom', 'StarPilot'):
    raise FastUpdateIncompatible('Legacy return is only available for Dom and StarPilot 6.x releases')
  entries = {}
  for row in git('ls-tree', '-rz', '--full-tree', revision).split('\0'):
    if row:
      meta, name = row.split('\t', 1)
      entries[name] = meta.split()[0]
  if 'upstream-sync.json' in entries or '.gitmodules' in entries or '160000' in entries.values():
    raise FastUpdateIncompatible('Legacy return requires a complete prebuilt release without submodules')
  required = {'prebuilt': ('100644',), 'launch_env.sh': ('100644', '100755'),
              'launch_openpilot.sh': ('100755',), 'launch_chffrplus.sh': ('100755',),
              'selfdrive/ui/lib/starpilot_version.py': ('100644',),
              'selfdrive/car/card.py': ('100644', '100755'),
              'selfdrive/modeld/modeld.py': ('100644', '100755')}
  if any(entries.get(name) not in modes for name, modes in required.items()):
    raise FastUpdateIncompatible('Legacy return requires a complete prebuilt StarPilot 6.x release')
  version = git('show', f'{revision}:selfdrive/ui/lib/starpilot_version.py')
  versions = re.findall(r'^STARPILOT_DISPLAY_VERSION = ["\'](6\.\d+\.\d+)["\']\s*$', version, re.MULTILINE)
  if len(versions) != 1:
    raise FastUpdateIncompatible('Legacy return requires a StarPilot 6.x release')
  return versions[0]


def _stop_process(process):
  try:
    os.killpg(process.pid, signal.SIGTERM)
  except ProcessLookupError:
    pass
  deadline = time.monotonic() + 5
  while time.monotonic() < deadline:
    process.poll()
    try:
      os.killpg(process.pid, 0)
    except ProcessLookupError:
      break
    time.sleep(0.05)
  try:
    os.killpg(process.pid, signal.SIGKILL)
  except ProcessLookupError:
    pass
  process.wait(timeout=5)


def run(command, cwd, progress=None, *, timeout=None, download=False, cancelled=None, stdin=None):
  downloading = download or 'fetch' in command or 'pull' in command or 'clone' in command
  if downloading and phone_uplink_active():
    raise FastUpdateError('Software downloads are disabled on the phone uplink')
  environment = dict(os.environ, GIT_LFS_SKIP_SMUDGE='1', GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/bin/false', SSH_ASKPASS='/bin/false')
  if timeout is None:
    timeout = 120 if "fetch" in command or "reset" in command or "checkout" in command else 30
  output = bytearray()
  with subprocess.Popen(command, cwd=cwd, env=environment, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True) as process:
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
      selector.register(process.stdout, selectors.EVENT_READ)
      deadline = time.monotonic() + timeout
      pending = b""
      progress_at = float('-inf')
      pending_progress = None
      try:
        while selector.get_map():
          if cancelled is not None and cancelled():
            raise FastUpdateError('Updater operation stopped')
          if downloading and phone_uplink_active():
            raise FastUpdateError('Phone uplink activated; software download stopped')
          if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(command, timeout, output=bytes(output))
          if progress is not None and pending_progress is not None and time.monotonic() - progress_at >= 0.25:
            progress(pending_progress)
            pending_progress = None
            progress_at = time.monotonic()
          for key, _ in selector.select(min(0.1, max(0.0, deadline - time.monotonic()))):
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
              selector.unregister(key.fileobj)
              continue
            output.extend(chunk)
            pending += chunk
            lines = re.split(b'[\r\n]', pending)
            pending = lines.pop()
            for line in lines:
              detail = line.decode('utf-8', errors='replace')
              pattern = r'(?:remote: )?(?:Enumerating objects|Counting objects|Compressing objects|Receiving objects|Resolving deltas|Total|Updating files):'
              if progress is not None and re.match(pattern, detail):
                # Progress consumers persist Params synchronously. Git can emit
                # hundreds of lines in a burst; keep draining its pipe instead
                # of forcing a disk sync for each percentage point.
                pending_progress = detail[-512:]
                now = time.monotonic()
                if now - progress_at >= 0.25:
                  progress(pending_progress)
                  pending_progress = None
                  progress_at = now
        result = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if progress is not None and pending_progress is not None:
          progress(pending_progress)
        if result:
          raise subprocess.CalledProcessError(result, command, output=bytes(output))
      except BaseException:
        _stop_process(process)
        raise
  return output.decode('utf-8', errors='replace')


_DEFAULT_RUN = run


def fetch_files(git, revision):
  missing = [line[1:] for line in git('rev-list', '--objects', '--no-object-names', '--missing=print',
                                    '--no-walk', revision, '--').splitlines() if line.startswith('?')]
  if missing:
    # Match Git's partial-clone bulk fetch, with visible progress and no argv-size limit.
    with tempfile.TemporaryFile() as requested:
      requested.write(('\n'.join(missing) + '\n').encode())
      requested.seek(0)
      git('-c', 'fetch.negotiationAlgorithm=noop', 'fetch', '--progress', '--no-tags', '--no-write-fetch-head',
          '--no-recurse-submodules', '--filter=blob:none', 'origin', '--stdin', stdin=requested)


def fast_update(repo, branch, *, params, parked, expected_commit, current_os=None, invalidate=None, run=run, rollback=False,
                progress=None, sleep=None, selected_commit=None, versions_only=False):
  repo = Path(repo)

  def notify(stage, detail):
    if progress is not None:
      progress(stage, detail)

  if (selected_commit is not None and (type(selected_commit) is not str or not re.fullmatch(r'[0-9a-f]{40}', selected_commit)) or
      rollback and (selected_commit is not None or versions_only) or versions_only and selected_commit is not None):
    raise FastUpdateError('Invalid recent version request')

  notify('preparing', 'Resolving active branch...')

  def git(*arguments, stdin=None):
    command = [
      'git',
      '-c',
      'gc.auto=0',
      '-c',
      'maintenance.auto=false',
      '-c',
      'core.hooksPath=/dev/null',
      '-c',
      'submodule.recurse=false',
      '-c',
      'filter.lfs.required=false',
      '-c',
      'filter.lfs.smudge=',
      '-c',
      'filter.lfs.process=',
      *arguments,
    ]
    if run is _DEFAULT_RUN:
      return run(command, cwd=str(repo), download=not rollback and any(action in arguments for action in ('show', 'checkout', 'reset')),
                 progress=(lambda detail: notify('fetching', detail)) if 'fetch' in arguments else None, stdin=stdin)
    return run(command, cwd=str(repo), **({'stdin': stdin} if stdin is not None else {}))

  def admitted():
    if not parked():
      raise FastUpdateError('Fast Update requires fresh parked state')
    if git('symbolic-ref', '--short', 'HEAD').strip() != initial_branch:
      raise FastUpdateError('Installed branch changed; refresh and try again')

  git('check-ref-format', '--branch', branch)
  initial_branch = git('symbolic-ref', '--short', 'HEAD').strip()
  admitted()
  previous = git('rev-parse', 'HEAD^{commit}').strip()
  if not isinstance(expected_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_commit) or previous != expected_commit:
    raise FastUpdateError('Approved source revision changed; refresh and try again')
  if rollback:
    target = git('rev-parse', '--verify', 'refs/starpilot/previous^{commit}').strip()
    saved = git('config', '--local', '--get', 'starpilot.previousVersion').strip().split(' ', 1)
    if len(saved) != 2 or saved[0] != target:
      raise FastUpdateError('Previous version receipt is incomplete; use a full validated update')
    branch = saved[1]
    git('check-ref-format', '--branch', branch)
    notify('fetching', 'Previous installed version is available locally.')
  else:
    recent = versions_only or selected_commit is not None
    history_limit = MAX_COMMITS if selected_commit is not None else RECENT_VERSION_LIMIT
    notify('fetching', f'Fetching {"recent versions" if recent else "latest shallow commit"} on {branch}...')
    git('fetch', '--progress', f'--depth={history_limit if recent else 1}', '--filter=blob:none',
        '--no-tags', '--no-recurse-submodules', 'origin', f'refs/heads/{branch}')
    target = git('rev-parse', 'FETCH_HEAD^{commit}').strip()
    if recent:
      members = git('log', '--first-parent', f'-{history_limit}', '--format=%H', target, '--').splitlines()
      if selected_commit is not None:
        installed_history = git('log', '--first-parent', f'-{MAX_COMMITS}', '--format=%H', previous, '--').splitlines() if branch == initial_branch else []
        if selected_commit not in members and selected_commit not in installed_history:
          raise FastUpdateError('Selected version is no longer in recent branch history; refresh and try again')
        target = selected_commit
      else:
        admitted()
        if git('rev-parse', 'HEAD^{commit}').strip() != previous:
          raise FastUpdateError('Source changed during version download')
        ref = f'refs/starpilot/recent/{branch}'
        git('check-ref-format', ref)
        git('update-ref', ref, target)
        notify('complete', 'Recent versions are ready to review.')
        return 'versions-listed'
  notify('validating', 'Checking downloaded operating system and native artifacts...')
  legacy_return = branch in ('Dom', 'StarPilot') and not git('ls-tree', target, '--', 'upstream-sync.json').strip()
  if legacy_return:
    version = validate_legacy_return(git, target, branch)
    notify('validating', f'Returning to {branch} {version}; checking the legacy prebuilt release...')
  else:
    # Validation reads the manifest directly, so guard any initial blob download.
    if git('ls-tree', target, '--', 'upstream-sync.json').strip().split()[:1] == ['100644']:
      git('show', f'{target}:upstream-sync.json')
    validate_revision(repo, target)
  launch = git('show', f'{target}:launch_env.sh')
  required_os = required_os_version(launch)
  if not (legacy_os_compatible(launch, current_os) if legacy_return else required_os and current_os == required_os):
    raise FastUpdateIncompatible('Update requires a different or unverified operating system')
  if target == previous and branch == initial_branch and not git('status', '--porcelain', '--untracked-files=no').strip():
    notify('complete', 'Already up to date.')
    return 'up-to-date'
  for revision in (previous, target):
    if git('ls-tree', revision, '--', 'prebuilt').strip().split()[:1] != ['100644']:
      raise FastUpdateIncompatible('Fast Update requires prebuilt releases; use a full validated update')
  if not legacy_return and not valid_receipt(git, target) and revision_digest(git, previous) != revision_digest(git, target):
    raise FastUpdateIncompatible('Native build inputs or artifacts changed without a matching build receipt; use a full validated update')
  tracked = set(git('ls-tree', '-rz', '--name-only', target).split('\0')) - {''}
  indexed = set(git('ls-files', '-z').split('\0')) - {''}
  candidates = tracked - indexed
  for name in tuple(candidates):
    for parent in Path(name).parents:
      if str(parent) != '.' and str(parent) not in indexed:
        local = repo / parent
        if local.is_symlink() or local.exists() and not local.is_dir():
          candidates.add(str(parent))
  paths = sorted(candidates)
  untracked = set()
  for offset in range(0, len(paths), 256):
    untracked.update(git('--literal-pathspecs', 'ls-files', '--others', '-z', '--', *paths[offset:offset + 256]).split('\0'))
  untracked.discard('')
  directories = {str(parent) for name in tracked for parent in Path(name).parents if str(parent) != '.'} if untracked else set()
  for name in untracked:
    if name in tracked or name in directories or any(str(parent) in tracked for parent in Path(name).parents):
      raise FastUpdateError('Update would overwrite untracked data')
  fetch_files(git, target)
  admitted()
  if git('rev-parse', 'HEAD^{commit}').strip() != previous:
    raise FastUpdateError('Source changed during download')
  if invalidate is None:
    raise FastUpdateError('Update staging invalidation is unavailable')

  def optional(*arguments):
    try:
      return git(*arguments).strip()
    except subprocess.CalledProcessError:
      return None

  retained = optional('rev-parse', '--verify', 'refs/starpilot/previous^{commit}')
  retained_branch = optional('config', '--local', '--get', 'starpilot.previousVersion')
  local_backup = None
  if git('status', '--porcelain', '--untracked-files=no').strip():
    notify('preparing', 'Saving local tracked changes before installation...')
    local_backup = git('stash', 'create').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', local_backup):
      raise FastUpdateError('Could not back up local tracked changes')
    backup_ref = f'refs/starpilot/local-changes/{time.time_ns()}'
    git('update-ref', backup_ref, local_backup)
    notify('preparing', f'Local changes saved as {backup_ref}.')
  git('update-ref', 'refs/starpilot/previous', previous)
  git('config', '--local', 'starpilot.previousVersion', f'{previous} {initial_branch}')
  try:
    invalidate()
    admitted()
    if git('rev-parse', 'HEAD^{commit}').strip() != previous:
      raise FastUpdateError('Source changed before installation')
    notify('applying', 'Applying fetched commit...')
    if branch != initial_branch:
      git('checkout', '--force', '--no-recurse-submodules', '-B', branch, target)
    else:
      git('reset', '--hard', '--no-recurse-submodules', target)
    if git('rev-parse', 'HEAD^{commit}').strip() != target:
      raise FastUpdateError('Applied source revision does not match download')
    if legacy_return:
      validate_legacy_return(git, 'HEAD', branch)
    else:
      validate_revision(repo, 'HEAD')
    if not parked():
      raise FastUpdateError('Parked state changed before restart')
    notify('submodules', 'No submodules configured; dependencies are vendored in this release.')
    notify('rebooting', 'Update complete. Device is rebooting; please wait for reconnection.')
    (sleep or time.sleep)(0.25)
    if not parked():
      raise FastUpdateError('Parked state changed before restart')
    notify('complete', 'Update installed. Device is restarting; please wait for reconnection.')
    params.put_bool('DoReboot', True, block=True)
  except Exception:
    notify('error', 'Update failed; restoring the previous installed version.')
    if git('symbolic-ref', '--short', 'HEAD').strip() != initial_branch:
      git('checkout', '--force', '--no-recurse-submodules', '-B', initial_branch, previous)
    else:
      git('reset', '--hard', '--no-recurse-submodules', previous)
    if git('rev-parse', 'HEAD^{commit}').strip() != previous:
      raise FastUpdateError('Source rollback failed') from None
    if local_backup is not None:
      git('stash', 'apply', '--index', local_backup)
    if retained is None:
      git('update-ref', '-d', 'refs/starpilot/previous')
    else:
      git('update-ref', 'refs/starpilot/previous', retained)
    if retained_branch is None:
      optional('config', '--local', '--unset', 'starpilot.previousVersion')
    else:
      git('config', '--local', 'starpilot.previousVersion', retained_branch)
    raise
  return 'reboot-requested'
