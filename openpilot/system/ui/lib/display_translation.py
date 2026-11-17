"""Translate already formatted display text without changing interpolated values."""

import re
from string import Formatter


class DisplayText(str):
  """A translated or literal display value; never translate it a second time."""
  def format(self, *args, **kwargs):
    return DisplayText(super().format(*args, **kwargs))

  def lower(self):
    return DisplayText(super().lower())

  def upper(self):
    return DisplayText(super().upper())

  def replace(self, old, new, count=-1):
    return DisplayText(super().replace(old, new, count))

  def __getitem__(self, key):
    return DisplayText(super().__getitem__(key))

  def split(self, sep=None, maxsplit=-1):
    return [DisplayText(value) for value in super().split(sep, maxsplit)]


def parts(text):
  result, auto = [], 0
  for literal, field, _, _ in Formatter().parse(text):
    key = str(auto) if field == '' else field
    result.append((literal, key))
    if field == '':
      auto += 1
  return result


def display_templates(messages):
  grouped = {}
  for source, translated in sorted(messages.items(), key=lambda item: (-len(re.sub(r"\{[^{}]*\}", "", item[0])), len(re.findall(r"\{[^{}]*\}", item[0])))):
    if not translated or '{' not in source:
      continue
    try:
      source_parts, target_parts = parts(source), parts(translated)
    except ValueError:
      continue
    keys = [key for _, key in source_parts if key is not None]
    if not keys or sorted(keys) != sorted(key for _, key in target_parts if key is not None):
      continue
    prefix = source_parts[0][0]
    pattern = ''.join(re.escape(literal) + (r'([^\n]*?)' if key is not None else '') for literal, key in source_parts)
    words = re.findall(r"[a-z]+", ''.join(literal for literal, _ in source_parts).lower())
    generic = not any(word not in {'to', 'of', 'in', 'on', 'at', 'by', 'for', 'or', 'and', 'a', 'the'} for word in words)
    grouped.setdefault(prefix[:1], []).append((prefix, re.compile('^' + pattern + '$'), keys, target_parts, generic))
  return grouped


def translate_display(text, messages, templates):
  if isinstance(text, DisplayText):
    return text
  if messages.get(text):
    return DisplayText(messages[text])
  if len(text) <= 1024:
    for prefix, pattern, keys, target, generic in (*templates.get(text[:1], ()), *templates.get('', ())):
      if text.startswith(prefix) and (matched := pattern.fullmatch(text)):
        if generic and not any(re.search(r"\d", value) for value in matched.groups()):
          continue
        values = dict(zip(keys, matched.groups(), strict=True))
        if "label" in values:
          values["label"] = messages.get(values["label"], values["label"])
        return DisplayText(''.join(literal + (values[key] if key is not None else '') for literal, key in target))
  segments = re.split(r'(\n|\s+·\s+)', text)
  if len(segments) > 1:
    translated = [translate_display(part, messages, templates) if index % 2 == 0 else part for index, part in enumerate(segments)]
    if translated != segments:
      return DisplayText(''.join(translated))
  return text


def native_text(text):
  # The web drag-grip icon is absent from the bundled text fonts.
  return DisplayText(text.replace("⠿", "::"))
