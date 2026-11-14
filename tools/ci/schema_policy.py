#!/usr/bin/env python3
"""Enforce cereal's reserved-event contract against the pinned upstream schemas.

See openpilot/cereal/README.md, Custom forks. When syncing upstream, review and
update schema-policy.json alongside upstream-sync.json. Custom fields belong
inside reserved custom.capnp structs or the exact reviewed payload children
already shipped by this fork; those children grant no Event slots and their
existing bodies and reserved-carrier references are immutable. log.capnp may rename only their event
aliases and the reserved raw-data aliases, preserving ordinals and type IDs.
"""

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
POLICY = Path(__file__).with_name('schema-policy.json')
DECLARATION = re.compile(r'^struct (\w+) @(0x[0-9a-fA-F]+)\s*\{', re.MULTILINE)
SCHEMA_ROOTS = ('openpilot/cereal', 'opendbc_repo/opendbc/car')


def sha256(data):
  return hashlib.sha256(data).hexdigest()


def custom_structs(source):
  # Mask comments and strings before counting braces; quoted defaults may
  # contain braces without ending a message definition.
  masked = re.sub(r'"(?:\\.|[^"\\])*"|\#[^\n]*', lambda m: ' ' * len(m[0]), source)
  definitions, bodies, spans = {}, {}, []
  cursor = 0
  for match in DECLARATION.finditer(masked):
    if match.start() < cursor:
      continue
    depth, end = 1, match.end()
    while depth and end < len(masked):
      depth += (masked[end] == '{') - (masked[end] == '}')
      end += 1
    if depth:
      raise ValueError('Unclosed custom struct')
    name, type_id = match.groups()
    if type_id.lower() in definitions or name in definitions.values():
      raise ValueError('Duplicate custom type ID or name')
    definitions[type_id.lower()] = name
    bodies[type_id.lower()] = source[match.end():end - 1]
    spans.append((match.start(), end))
    cursor = end
  remainder = source
  for start, end in reversed(spans):
    remainder = remainder[:start] + remainder[end:]
  return definitions, bodies, remainder


def source_tokens(source):
  """Tokenize the reviewed schema subset, preserving quoted defaults exactly."""
  return re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z_0-9]*|0x[0-9a-fA-F]+|[0-9]+|[^\s]',
                    re.sub(r'"(?:\\.|[^"\\])*"|#[^\n]*',
                           lambda match: match[0] if match[0].startswith('"') else '', source))


def source_scope(source, kind='struct'):
  """Parse fields, enum values and nested scopes; unsupported syntax fails shut.

  This is a source identity contract, not a replacement for compiled wire-layout
  checks. In particular no annotations, aliases, groups or implicit unions are
  accepted by this subset without a separate grammar/contract review.
  """
  tokens = source_tokens(source)
  position = 0

  def parse(scope_kind, nested=False):
    nonlocal position
    fields, children, unions = [], {}, []
    while position < len(tokens) and tokens[position] != '}':
      word = tokens[position]
      if word in ('struct', 'enum', 'union'):
        position += 1
        if word == 'union':
          name, type_id = '', None
        else:
          if position >= len(tokens) or not re.fullmatch(r'[A-Za-z_]\w*', tokens[position]):
            raise ValueError('Invalid nested type name')
          name, type_id = tokens[position], None
          position += 1
          if position < len(tokens) and tokens[position] == '@':
            position += 1
            if position >= len(tokens) or not re.fullmatch(r'0x[0-9a-fA-F]+', tokens[position]):
              raise ValueError('Invalid nested type ID')
            type_id = tokens[position].lower()
            position += 1
        if position >= len(tokens) or tokens[position] != '{' or scope_kind == 'enum':
          raise ValueError('Unsupported nested declaration')
        position += 1
        child = {'kind': word, 'type_id': type_id, 'body': parse(word, True)}
        if word == 'union':
          unions.append(child)
        elif name in children:
          raise ValueError('Duplicate nested type name')
        else:
          children[name] = child
        continue
      start = position
      while position < len(tokens) and tokens[position] not in (';', '{', '}'):
        position += 1
      if position >= len(tokens) or tokens[position] != ';':
        raise ValueError('Unsupported or unterminated member declaration')
      member = tokens[start:position]
      position += 1
      if (len(member) < 3 or not re.fullmatch(r'[A-Za-z_]\w*', member[0]) or
          member[1] != '@' or not member[2].isdigit()):
        raise ValueError('Unsupported member declaration')
      if scope_kind == 'enum':
        if len(member) != 3:
          raise ValueError('Unsupported enumerant declaration')
      else:
        if len(member) < 5 or member[3] != ':':
          raise ValueError('Unsupported field declaration')
        type_end = member.index('=') if '=' in member else len(member)
        descriptor = ''.join(member[4:type_end])
        # No aliases/annotations/inline defaults can silently affect resolution.
        type_pattern = r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*'
        while descriptor.startswith('List(') and descriptor.endswith(')'):
          descriptor = descriptor[5:-1]
        if not re.fullmatch(type_pattern, descriptor):
          raise ValueError('Unsupported field type')
        if type_end < len(member):
          default = ''.join(member[type_end + 1:])
          if not re.fullmatch(r'"(?:\\.|[^"\\])*"|true|false|[-+]?[0-9]+(?:\.[0-9]+)?', default):
            raise ValueError('Unsupported field default')
      fields.append(member)
    if nested:
      if position >= len(tokens):
        raise ValueError('Unclosed nested declaration')
      position += 1
    elif position != len(tokens):
      raise ValueError('Unexpected closing brace')
    names = [field[0] for field in fields]
    ordinals = [int(field[2]) for field in fields]
    # Anonymous-union fields share their containing struct's ordinal space.
    for union in unions:
      names.extend(field[0] for field in union['body']['fields'])
      ordinals.extend(int(field[2]) for field in union['body']['fields'])
    if len(names) != len(set(names)) or len(ordinals) != len(set(ordinals)):
      raise ValueError('Duplicate member name or ordinal')
    if set(names) & set(children):
      raise ValueError('Field shadows nested type')
    return {'fields': fields, 'children': children, 'unions': unions}

  return parse(kind)


def preserves_scope(current, historical):
  """Allow recursive appends while preserving every historical declaration."""
  old_fields, new_fields = historical['fields'], current['fields']
  if new_fields[:len(old_fields)] != old_fields or current['unions'] != historical['unions']:
    return False
  if old_fields and any(int(field[2]) <= max(int(old[2]) for old in old_fields)
                        for field in new_fields[len(old_fields):]):
    return False
  for name, old in historical['children'].items():
    new = current['children'].get(name)
    if (new is None or new['kind'] != old['kind'] or new['type_id'] != old['type_id'] or
        not preserves_scope(new['body'], old['body'])):
      return False
  return True


def payload_child_contract(definitions, bodies, reviewed):
  """The nine shipped payload helpers are not additional Event reservations."""
  errors, trees, ids = [], {}, set()
  for type_id, name in definitions.items():
    trees[name] = source_scope(bodies[type_id])
    ids.add(type_id)
  for name, helper in reviewed.items():
    type_id = helper['type_id']
    if (definitions.get(type_id) != name or
        trees.get(name) != source_scope(helper['body'])):
      errors.append(f'custom.capnp: reviewed payload child {name} changed')

  scopes = {}
  def visit(name, tree):
    scopes[name] = tree
    for child_name, child in tree['children'].items():
      child_id = child['type_id']
      if child_id:
        if child_id in ids:
          raise ValueError('Duplicate explicit nested type ID')
        ids.add(child_id)
      visit(name + '.' + child_name, child['body'])
  for name, tree in trees.items():
    visit(name, tree)

  def resolved(scope, descriptor):
    while descriptor.startswith('List(') and descriptor.endswith(')'):
      descriptor = descriptor[5:-1]
    parts = scope.split('.')
    while parts:
      candidate = '.'.join(parts) + '.' + descriptor
      if candidate in scopes:
        return candidate
      parts.pop()
    return descriptor if descriptor in scopes else None

  incoming = {name: [] for name in reviewed}
  # Lexical nesting alone never makes a type reachable. Only an
  # actual field reference from an already-reachable scope does.
  graph = {scope: set() for scope in scopes}
  for scope, tree in scopes.items():
    fields = tree['fields'] + [field for union in tree['unions'] for field in union['body']['fields']]
    for field in fields:
      end = field.index('=') if '=' in field else len(field)
      target = resolved(scope, ''.join(field[4:end]))
      if target is None:
        continue
      source_root, target_root = scope.split('.')[0], target.split('.')[0]
      graph[scope].add(target)
      if target_root in reviewed and source_root not in reviewed:
        incoming[target_root].append({'scope': scope, 'field': field[0], 'ordinal': int(field[2]),
                                      'target': target, 'declaration': ''.join(field)})
  def ordered(entries):
    return sorted(entries, key=lambda entry: (entry['scope'], entry['ordinal']))

  for name, helper in reviewed.items():
    if ordered(incoming[name]) != ordered(helper['owners']):
      errors.append(f'custom.capnp: reviewed payload child {name} carrier references changed')
  pending, reachable = list(set(trees) - set(reviewed)), set()
  while pending:
    name = pending.pop()
    if name in reachable:
      continue
    reachable.add(name)
    pending.extend(graph[name] - reachable)
  if set(reviewed) - reachable:
    errors.append('custom.capnp: reviewed payload children must remain reachable from reserved carriers')
  return errors


def validate(schemas, policy, sync):
  errors = []
  expected_paths = {*policy['unchanged_schemas'], 'openpilot/cereal/custom.capnp', 'openpilot/cereal/log.capnp'}
  for path in sorted(set(schemas) - expected_paths):
    errors.append(f'{path}: schema has no explicit policy coverage')
  for path in sorted(expected_paths - set(schemas)):
    errors.append(f'{path}: policy-covered schema is missing')
  if expected_paths - set(schemas):
    return errors
  dependencies = {entry['path']: entry['commit'] for entry in sync['dependencies']}
  if sync['upstream']['commit'] != policy['upstream_commit'] or dependencies.get('opendbc_repo') != policy['opendbc_commit']:
    errors.append('Schema policy must be reviewed against the current upstream sync pins')
  for path, expected in policy['unchanged_schemas'].items():
    reviewed = policy.get('reviewed_enum_additions', {}).get(path)
    candidate = schemas[path]
    if reviewed:
      source = candidate.decode()
      pattern = rf'(enum {re.escape(reviewed["enum"])}\s*\{{)([^}}]*)(\}})'
      match = re.search(pattern, source)
      fields = reviewed.get('fields', [reviewed.get('field')])
      if match is None or any(len(re.findall(rf'(?m)^\s*{re.escape(field)}\s*$', match[2])) != 1 for field in fields):
        errors.append(f'{path}: reviewed enum addition is missing or changed')
      else:
        body = match[2]
        for field in fields:
          body = re.sub(rf'(?m)^[ \t]*{re.escape(field)}[ \t]*\n', '', body)
        source = source[:match.start(2)] + body + source[match.end(2):]
        candidate = source.encode()
    if sha256(candidate) != expected:
      errors.append(f'{path}: upstream-owned schema changed; use custom.capnp reserved structs')
  try:
    definitions, bodies, remainder = custom_structs(schemas['openpilot/cereal/custom.capnp'].decode())
  except (UnicodeDecodeError, ValueError) as error:
    return [*errors, f'custom.capnp: {error}']
  reviewed_children = policy.get('reviewed_payload_children', {})
  expected_ids = ({slot['type_id'] for slot in policy['reserved']} |
                  {helper['type_id'] for helper in reviewed_children.values()})
  if set(definitions) != expected_ids:
    errors.append('custom.capnp: reserved type IDs were removed/changed or unreserved top-level types were added')
  reserved_by_name = {slot['struct']: slot for slot in policy['reserved']}
  for index in policy['historical_empty_slots']:
    original_name = f'CustomReserved{index}'
    type_id = reserved_by_name[original_name]['type_id']
    body = re.sub(r'#[^\n]*', '', bodies.get(type_id, ''))
    if definitions.get(type_id) != original_name or body.strip():
      errors.append(f'custom.capnp: historical slot {index} must retain its original name and empty body')
  for index, restored in policy.get('historical_restored', {}).items():
    type_id = restored['type_id']
    body = re.sub(r'#[^\n]*', '', bodies.get(type_id, ''))
    field = restored['field']
    if not re.search(rf'(?m)^\s*{re.escape(field)}\s*$', body):
      errors.append(f'custom.capnp: historical slot {index} must preserve {field}')
    slot = next((entry for entry in policy['reserved'] if entry['type_id'] == type_id), None)
    if slot is None or slot['ordinal'] != restored['ordinal']:
      errors.append(f'custom.capnp: historical slot {index} type ID or event ordinal changed')
  for restored in policy.get('historical_prefixes', {}).values():
    try:
      preserved = (definitions.get(restored['type_id']) == restored['name'] and
                   preserves_scope(source_scope(bodies.get(restored['type_id'], '')), source_scope(restored['body'])))
    except ValueError as error:
      # Invalid syntax/duplicate ordinals inside an occupied historical scope
      # also violate that scope's contract; retain this diagnostic boundary.
      errors.append(f'custom.capnp: historical occupied prefix changed: {error}')
    else:
      if not preserved:
        errors.append('custom.capnp: historical occupied prefix changed')
  try:
    errors.extend(payload_child_contract(definitions, bodies, reviewed_children))
  except ValueError as error:
    errors.append(f'custom.capnp: source contract rejected: {error}')
  if remainder.strip() != policy['custom_header'].strip():
    errors.append('custom.capnp: edits outside the reserved structs')
  log = schemas['openpilot/cereal/log.capnp'].decode()
  for slot in policy['reserved']:
    name = definitions.get(slot['type_id'])
    if name is None:
      continue
    # Changing the name is permitted; changing its type or ordinal is not.
    pattern = rf'(?m)^(\s*)\w+ @{slot["ordinal"]} :Custom\.{re.escape(name)};'
    log, count = re.subn(pattern, rf'\g<1>{slot["field"]} @{slot["ordinal"]} :Custom.{slot["struct"]};', log)
    if count != 1:
      errors.append(f'log.capnp: reserved event @{slot["ordinal"]} no longer points to its original custom type ID')
  for ordinal, name in policy['raw_reserved'].items():
    log, count = re.subn(rf'(?m)^(\s*)\w+ @{ordinal} :Data;', rf'\g<1>{name} @{ordinal} :Data;', log)
    if count != 1:
      errors.append(f'log.capnp: reserved raw event @{ordinal} changed type or ordinal')
  if policy.get('reviewed_log_additions') or policy.get('reviewed_log_ordinal_remaps'):
    errors.append('log.capnp: core schema exceptions are not permitted; use custom.capnp reserved structs')
  if sha256(log.encode()) != policy['log_sha256']:
    errors.append('log.capnp: changes outside the permitted reserved-event aliases')
  return errors


def read_inputs(root=ROOT):
  policy = json.loads(POLICY.read_text())
  paths = {path.relative_to(root).as_posix() for schema_root in SCHEMA_ROOTS for path in (root / schema_root).rglob('*.capnp')}
  return {path: (root / path).read_bytes() for path in paths}, policy, json.loads((root / 'upstream-sync.json').read_text())


def main():
  errors = validate(*read_inputs())
  print(json.dumps({'passed': not errors, 'errors': errors}, indent=2))
  return int(bool(errors))


if __name__ == '__main__':
  raise SystemExit(main())
