import re
from urllib.parse import parse_qs, unquote_plus, urlsplit

# Same rules as galaxy/web/js/share-destination.js: destination coordinates before Google's viewport (@lat,lng).
NUMBER = r'(-?\d+(?:\.\d+)?)'
COORDINATES = (re.compile(rf'!3d{NUMBER}!4d{NUMBER}'), re.compile(rf'[?&:](?:q|query|daddr|destination|ll|coordinate)={NUMBER},\s*{NUMBER}'),
               re.compile(rf'geo:{NUMBER},\s*{NUMBER}'), re.compile(rf'@{NUMBER},\s*{NUMBER}'))
QUERY = re.compile(r'[?&:](?:q|query|daddr|destination)=([^&#]+)')
QUERY_KEYS = ('q', 'query', 'daddr', 'destination')
LINK = re.compile(r'https://[^\s<>"]+')
MAP_HOST = re.compile(r'(?:[a-z0-9-]+\.)*google\.com|(?:maps\.app\.)?goo\.gl|maps\.apple(?:\.com)?')


def place(label: str, latitude: float, longitude: float, address: str | None = None) -> dict:
  name, _, rest = label.partition(',')
  return {'name': name.strip()[:256] or 'Shared destination', 'latitude': latitude, 'longitude': longitude,
          'address': address or rest.strip() or None}


def shared_coordinates(text: str) -> dict | None:
  decoded = unquote_plus(text.strip())
  for pattern in COORDINATES:
    match = pattern.search(decoded)
    if match is None:
      continue
    latitude, longitude = float(match[1]), float(match[2])
    if abs(latitude) > 90 or abs(longitude) > 180:
      continue
    # geo:0,0?q=an address is a search, not a destination in the ocean.
    if match[0].startswith('geo:0,0') and re.search(r'[?&:]q=', decoded):
      continue
    label = (re.search(r'\(([^()]{1,120})\)', decoded) or re.search(r'/place/([^/?#&\n]+)', decoded) or
             re.search(r'[?&](?:name|q)=([^&#\n]*[^\d\s,.&#-][^&#\n]*)', decoded))
    return place(label[1] if label else 'Shared destination', latitude, longitude)
  return None


def map_link_query(text: str) -> str | None:
  """The place a Google or Apple Maps link names, for links without coordinates."""
  for link in LINK.findall(text):
    parts = urlsplit(link)
    if not MAP_HOST.fullmatch(parts.hostname or ''):
      continue
    named = re.search(r'/place/([^/]+)', parts.path)
    values = parse_qs(parts.query)
    query = unquote_plus(named[1]) if named else next((values[key][0] for key in QUERY_KEYS if key in values), '')
    query = ' '.join(query.split())
    if query:
      return query[:256]
  return None


def search_text(text: str) -> str:
  match = QUERY.search(unquote_plus(text.strip()))
  return ' '.join((match[1] if match else LINK.sub(' ', text)).split())[:200]
