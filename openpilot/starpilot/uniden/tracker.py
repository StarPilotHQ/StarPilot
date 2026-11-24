"""Finds where a fixed source is from how an alert behaves as the car passes it."""

GAP_S, MAX_S, MIN_SPEED = 2.0, 90.0, 3.0


class PassTracker:
  def __init__(self):
    self.tracks = {}

  def update(self, alerts, context, now, wall):
    """Returns pass events: a fixed source was abeam of the car at the reported position."""
    events = []
    current = {}
    for alert in alerts:
      key = (alert['band'], alert['frequency'])
      if key not in current or alert['strength'] > current[key]['strength']:
        current[key] = alert
    for key in [k for k, t in self.tracks.items() if k not in current and now - t['last'] >= GAP_S]:
      events += self.finish(key, self.tracks.pop(key), wall)
    for key, alert in current.items():
      track = self.tracks.setdefault(key, {'start': now, 'first_dir': alert['direction'], 'fired': False, 'peak': None,
                                                'sudden': bool(alert.get('instant_on'))})
      track['last'], track['strength'] = now, alert['strength']
      here = self.position(context, now)
      if here and (track['peak'] is None or alert['strength'] > track['peak']['strength']):
        track['peak'] = {**here, 'strength': alert['strength']}
      if (here and not track['fired'] and not track['sudden'] and track['first_dir'] == 'front' and alert['direction'] in ('side', 'rear')
          and now - track['start'] <= MAX_S):
        track['fired'] = True
        events.append(self.event(key, here, alert['strength'], wall))
    return events

  @staticmethod
  def position(context, now):
    if context is None or context['speed'] < MIN_SPEED or not 0 <= now - context['at'] < 1:
      return None
    return {'lat': context['lat'], 'lon': context['lon']}

  def finish(self, key, track, wall):
    # Without direction, a rise then a fall in strength marks the closest approach.
    peak = track['peak']
    if track['fired'] or track['sudden'] or track['first_dir'] or peak is None or peak['strength'] < 2 or track['strength'] > peak['strength'] - 2:
      return []
    return [self.event(key, peak, peak['strength'], wall)]

  @staticmethod
  def event(key, here, strength, wall):
    return {'band': key[0], 'frequency': key[1], 'lat': here['lat'], 'lon': here['lon'], 'strength': strength, 'at': wall}
