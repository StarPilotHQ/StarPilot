from openpilot.starpilot.navigation.share import map_link_query, shared_coordinates


def test_waze_short_link_decodes_geohash():
  found = shared_coordinates('https://waze.com/ul/h9q8yy4')
  assert (found['latitude'], found['longitude']) == (37.762756, -122.426147)


def test_waze_coordinate_links():
  assert shared_coordinates('https://waze.com/ul?ll=37.7749,-122.4194&navigate=yes')['longitude'] == -122.4194
  assert shared_coordinates('https://waze.com/live-map/directions?to=ll.37.77%2C-122.41')['latitude'] == 37.77


def test_waze_named_link_is_a_search():
  assert shared_coordinates('https://waze.com/ul?q=Starbucks%20Main%20St&navigate=yes') is None
  assert map_link_query('https://waze.com/ul?q=Starbucks%20Main%20St&navigate=yes') == 'Starbucks Main St'
