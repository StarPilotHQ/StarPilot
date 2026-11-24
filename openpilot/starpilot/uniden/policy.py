"""Decides which alerts are locked out, quiet for the posted limit, or sudden."""
from openpilot.starpilot.uniden.locks import matches_lock
from openpilot.starpilot.uniden.protocol import FREQUENCY_BANDS, matches_rule

INSTANT_STRENGTH = 5
CONTEXT_AGE_S = 1.0


def fresh_context(context, now):
  return context if context is not None and 0 <= now - context['at'] < CONTEXT_AGE_S else None


def apply(alerts, rules, situation, previous, now):
  """Marks each alert filtered (ruled or locked), quiet and instant_on. Returns the state to pass in on the next read."""
  context = fresh_context(situation.get('context'), now)
  quiet = situation.get('quiet')
  locks = situation.get('locks', [])
  timed = situation.get('timed')
  known = previous[1] if previous is not None and 0 <= now - previous[0] < CONTEXT_AGE_S else None
  seen = {}
  within = (quiet is not None and context is not None and context['limit'] is not None and
            context['speed'] <= context['limit'] + quiet['margin'])
  for alert in alerts:
    key = (alert['band'], alert['frequency'])
    entry = seen.get(key) or (known or {}).get(key)
    if entry is None:
      entry = {'instant': known is not None and alert['band'] in FREQUENCY_BANDS and alert['strength'] >= INSTANT_STRENGTH,
               'since': now, 'loud': False}
    chosen = bool(timed and alert['band'] in timed['bands'])
    timing = timed.get('settings', {}).get(alert['band'], timed) if timed else {}
    # Once a signal reaches the strength it stays loud until it clears, so it cannot flap at the threshold.
    # A strength of 0 means never warn again: the signal stays muted however strong it gets.
    seen[key] = entry = {**entry, 'loud': entry['loud'] or (chosen and timing['strength'] > 0 and alert['strength'] >= timing['strength'])}
    instant = entry['instant']
    alert['instant_on'] = instant
    alert['timed'] = chosen and not instant and not entry['loud'] and now - entry['since'] >= timing['seconds']
    alert['breakthrough'] = chosen and entry['loud']
    alert['ruled'] = matches_rule(alert, rules)
    alert['locked'] = any(matches_lock(alert, lock, context) for lock in locks)
    alert['filtered'] = alert['ruled'] or alert['locked']
    alert['quiet'] = bool(within and alert['band'] in quiet['bands'] and not instant)
  return now, seen
