import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.remote import RemotePairing
from openpilot.starpilot.galaxy.server import make_remote_server, make_server
from openpilot.starpilot.navigation.owner import NavigationOwner


PLACE = {'name': 'Library', 'latitude': 41.1, 'longitude': -88.2}


class NavigationHttpTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    self.access = GalaxyAccessOwner(root / 'access')
    self.pairing = RemotePairing(root / 'pairing')
    self.navigation = NavigationOwner(root / 'navigation', runtime_source=lambda: None, transient_root=root/'boot-navigation')
    self.parked = True
    self.local = make_server(port=0, owner=self.access, remote_pairing=self.pairing, navigation=self.navigation,
                             parked=lambda: self.parked)
    self.remote = make_remote_server(self.local, port=0)
    self.servers = []
    for server in (self.local, self.remote):
      thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      thread.start()
      self.servers.append((server, thread))
    self.addCleanup(self.close)
    self.local_cookie = self.request()[2]['Set-Cookie'].split(';', 1)[0]
    self.access.configure('password123', lambda: True)
    self.slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    record = self.pairing.read()
    assert record is not None
    self.remote_cookie = 'galaxy_session=' + base64.urlsafe_b64encode(json.dumps({self.slug: record['session']}).encode()).decode().rstrip('=')

  def close(self):
    for server, thread in reversed(self.servers):
      server.shutdown()
      thread.join(timeout=2)
      server.server_close()

  def request(self, path='/api/auth/session', *, payload=None, remote=False, cookie=None, origin=None):
    server = self.remote if remote else self.local
    host = f'{self.slug}.devices.local' if remote else f'127.0.0.1:{server.server_port}'
    headers = {'Host': host}
    if cookie is not None:
      headers['Cookie'] = cookie
    if payload is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': origin or ('https://galaxy.firestar.link' if remote else f'http://{host}')})
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    try:
      connection.request('POST' if payload is not None else 'GET', path,
                         json.dumps(payload) if payload is not None else None, headers)
      response = connection.getresponse()
      return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
      connection.close()

  def test_map_tiles_recheck_authentication_after_provider(self):
    def tile(*args):
      self.pairing.unpair()
      return b'fixture tile'
    self.navigation.map_tile = Mock(side_effect=tile)
    connection = http.client.HTTPConnection('127.0.0.1', self.remote.server_port, timeout=2)
    try:
      connection.request('GET', '/api/navigation/map/tiles/0/0/0.png', headers={'Host': f'{self.slug}.devices.local', 'Cookie': self.remote_cookie})
      response = connection.getresponse()
      self.assertNotEqual(response.status, 200)
      self.assertNotIn(b'fixture tile', response.read())
    finally:
      connection.close()

  def test_map_theme_is_validated_and_forwarded(self):
    self.navigation.map_tile = Mock(return_value=b'fixture tile')
    for theme in ('light', 'dark'):
      connection = http.client.HTTPConnection('127.0.0.1', self.local.server_port, timeout=2)
      try:
        connection.request('GET', f'/api/navigation/map/tiles/0/0/0.png?theme={theme}',
                           headers={'Host': f'127.0.0.1:{self.local.server_port}', 'Cookie': self.local_cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b'fixture tile')
        self.navigation.map_tile.assert_called_with(0, 0, 0, theme)
      finally:
        connection.close()
    self.navigation.map_tile.reset_mock()
    for query in ('theme=invalid', 'theme=', 'theme=light&theme=dark', 'style=custom'):
      status, _, _ = self.request('/api/navigation/map/tiles/0/0/0.png?' + query, cookie=self.local_cookie)
      self.assertEqual(status, 400)
    self.navigation.map_tile.assert_not_called()

  def action(self, action, *, remote=False, **values):
    return self.request('/api/navigation/action', payload=dict(action=action, revision=self.navigation.snapshot()['revision'], **values),
                         remote=remote, cookie=self.remote_cookie if remote else self.local_cookie)

  def test_local_and_remote_share_real_destination_and_saved_places_without_secrets(self):
    for remote in (False, True):
      with self.subTest(remote=remote):
        result = self.action('configure', remote=remote, patch={'enabled': True, 'token': 'pk.secret-test-token'})
        self.assertEqual(result[0], 200)
        self.assertTrue(result[1]['hasKey'])
        self.assertNotIn('pk.secret-test-token', json.dumps(result))
        result = self.action('select', remote=remote, destination=PLACE)
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]['destination']['name'], 'Library')
        self.assertEqual(result[1]['status'], 'waitingForLocation')
        self.assertEqual(self.action('favorite', remote=remote, destination=PLACE)[0], 200)
        result = self.action('clear', remote=remote)
        self.assertIsNone(result[1]['destination'])
        self.assertEqual(len(result[1]['favorites']), 1)
        self.assertEqual(self.action('removeFavorite', remote=remote, id=result[1]['favorites'][0]['id'])[1]['favorites'], [])

  def test_autocomplete_search_requests(self):
    self.action('configure', patch={'enabled': True, 'token': 'pk.test'})
    search = {'query': 'cof', 'searchId': str(uuid.uuid4()), 'clientId': str(uuid.uuid4())}
    with patch.object(self.navigation, 'search_places', return_value=[]) as places:
      self.assertEqual(self.request('/api/navigation/search', payload={**search, 'autocomplete': True}, cookie=self.local_cookie)[0], 200)
      self.assertTrue(places.call_args.kwargs['autocomplete'])
      self.assertEqual(self.request('/api/navigation/search', payload=search, cookie=self.local_cookie)[0], 200)
      self.assertFalse(places.call_args.kwargs['autocomplete'])
      self.assertEqual(self.request('/api/navigation/search', payload={**search, 'autocomplete': 'yes'}, cookie=self.local_cookie)[0], 400)

  def test_alternative_route_action_uses_authenticated_revision(self):
    from openpilot.starpilot.navigation.route_engine import NavigationRoute
    route = NavigationRoute({'distance':100., 'duration':20., 'geometry':{'coordinates':[[0.,0.],[.001,0.]]},
                             'legs':[{'steps':[{'distance':100.,'duration':20.,'maneuver':{'type':'depart','instruction':'Continue'}}]}]})
    self.action('configure', patch={'enabled':True,'token':'pk.test'})
    self.action('select', destination=PLACE)
    revision = self.navigation.read()['revision']
    self.navigation.record_routes(revision, [route,route])
    result = self.action('selectRoute', remote=True, index=1)
    self.assertEqual(result[0],200)
    self.assertEqual(result[1]['selectedRoute'],1)
    self.assertNotIn('pk.test',json.dumps(result))
    self.assertEqual(self.request('/api/navigation/action', payload={'action':'selectRoute','revision':revision,'index':0},
                                  remote=True,cookie=self.remote_cookie)[0],409)

  def test_gateway_auth_and_origin_are_required(self):
    self.assertEqual(self.request('/api/navigation/status', remote=True)[0], 401)
    self.assertEqual(self.request('/api/navigation/status', remote=True, cookie=self.local_cookie)[0], 401)
    payload = {'action': 'configure', 'revision': '0', 'patch': {'enabled': True}}
    self.assertEqual(self.request('/api/navigation/action', payload=payload, remote=True,
                                  cookie=self.remote_cookie, origin='https://wrong.example')[0], 403)
    self.assertFalse(self.navigation.snapshot()['enabled'])

  def test_keys_require_parked_but_destinations_do_not(self):
    self.parked = False
    self.assertEqual(self.action('configure', patch={'token': 'pk.new-key'})[0], 409)
    self.assertFalse(self.navigation.snapshot()['hasKey'])
    self.assertEqual(self.action('configure', patch={'enabled': True})[0], 200)
    self.assertEqual(self.action('select', destination=PLACE)[0], 200)

  def test_google_key_needs_parked_and_test_key_reports_without_leaking(self):
    key = 'AIzaSyFixtureKey_0123456789abcdef'
    self.parked = False
    self.assertEqual(self.action('configure', patch={'googleKey': key})[0], 409)
    self.assertFalse(self.navigation.snapshot()['hasGoogleKey'])
    self.parked = True
    status, body, _ = self.action('configure', patch={'googleKey': key, 'mapProvider': 'google'})
    self.assertEqual((status, body['hasGoogleKey'], body['mapProvider']), (200, True, 'google'))
    self.assertNotIn(key, json.dumps(self.request('/api/navigation/status', cookie=self.local_cookie)[1]))
    with (patch('openpilot.starpilot.navigation.google_tiles.test_key', return_value={'ok': False, 'message': 'Billing is off'}) as tested,
          patch('openpilot.starpilot.navigation.google_routes.test_routes', return_value={'ok': True, 'failure': None, 'message': 'routes'}),
          patch('openpilot.starpilot.navigation.google_places.test_places', return_value={'ok': True, 'failure': None, 'message': 'places'})):
      self.parked = False
      status, body, _ = self.action('testGoogleKey')
      self.assertEqual((status, body['googleKeyTest']['message'], body['hasGoogleKey']), (200, 'Billing is off', True))
      self.assertEqual((body['googleKeyTest']['routes']['ok'], body['googleKeyTest']['places']['message']), (True, 'places'))
      self.assertEqual(self.action('testGoogleKey', key='AIzaSyOtherKey_0123456789abcdefgh')[0], 200)
    self.assertEqual([call.args[0] for call in tested.call_args_list], [key, 'AIzaSyOtherKey_0123456789abcdefgh'])
    self.assertEqual(self.action('testGoogleKey', key='bad key')[0], 400)
    self.assertEqual(self.action('testGoogleKey', other=True)[0], 400)

  def test_shared_short_link_is_followed_on_the_comma(self):
    with patch.object(type(self.navigation), 'resolve_share', return_value='https://www.google.com/maps/place/X/data=!3d36.1!4d-115.1') as resolved:
      status, body, _ = self.action('resolveShare', text='Bellagio https://maps.app.goo.gl/abc123')
    self.assertEqual((status, body['sharedText']), (200, 'https://www.google.com/maps/place/X/data=!3d36.1!4d-115.1'))
    self.assertIn('revision', body)
    resolved.assert_called_once_with('Bellagio https://maps.app.goo.gl/abc123')
    self.assertEqual(self.action('resolveShare')[0], 400)

  def test_shared_place_turns_navigation_on_and_starts_the_route(self):
    self.assertEqual(self.action('share', text='geo:40.5,-89.5')[1], {'error': 'Add your Mapbox access token first'})
    self.navigation.configure({'token': 'pk.test'}, self.navigation.read()['revision'], True)
    self.assertFalse(self.navigation.snapshot()['enabled'])
    status, body, _ = self.action('share', remote=True, text='https://www.google.com/maps/place/Bellagio/data=!3d36.1126!4d-115.1767')
    self.assertEqual(status, 200)
    self.assertEqual(body['shared'], {'name': 'Bellagio', 'latitude': 36.1126, 'longitude': -115.1767})
    self.assertTrue(body['enabled'] and body['hasKey'])
    self.assertEqual((body['destination']['latitude'], body['revision']), (36.1126, self.navigation.snapshot()['revision']))
    self.assertNotIn('pk.test', json.dumps(body))
    feature = {'features': [{'properties': {'name': '3600 S Las Vegas Blvd', 'full_address': '3600 S Las Vegas Blvd, Las Vegas, NV'},
                             'geometry': {'coordinates': [-115.17, 36.11]}}]}
    with patch('openpilot.starpilot.navigation.owner.response_json', return_value=feature) as geocode:
      status, body, _ = self.action('share', text='https://www.google.com/maps/place/Bellagio,+3600+S+Las+Vegas+Blvd,+Las+Vegas,+NV/data=!4m2!3m1!1s0x1:0x2')
    self.assertEqual((status, body['shared']), (200, {'name': 'Bellagio', 'latitude': 36.11, 'longitude': -115.17}))
    self.assertEqual(geocode.call_args.args[2]['q'], '3600 S Las Vegas Blvd, Las Vegas, NV')
    with patch('openpilot.starpilot.navigation.owner.response_json', return_value={'features': []}):
      self.assertEqual(self.action('share', text='https://maps.google.com/?q=Nowhere,+Atlantis')[1],
                       {'error': 'No map location found for Nowhere', 'query': 'Nowhere, Atlantis'})
    self.assertEqual(self.action('share', text='Starbucks')[:2], (400, {'error': 'Open StarPilot to search for Starbucks', 'query': 'Starbucks'}))
    revision = self.navigation.read()['revision']
    stale = {'action': 'share', 'revision': '0', 'text': 'geo:40.5,-89.5'}
    self.assertEqual(self.request('/api/navigation/action', payload=stale, cookie=self.local_cookie)[0], 409)
    self.assertEqual(self.navigation.read()['revision'], revision)
    self.assertEqual(self.action('share')[0], 400)
    self.assertNotIn('query', self.action('share', text='x' * 8193)[1])

  def test_shared_intent_retry_after_cancel_does_not_restart_navigation(self):
    self.navigation.configure({'token': 'pk.test'}, self.navigation.read()['revision'], True)
    request = {'action': 'share', 'revision': self.navigation.read()['revision'],
               'text': 'geo:40.5,-89.5', 'intentId': 'durable-phone-share'}
    first = self.request('/api/navigation/action', payload=request, remote=True, cookie=self.remote_cookie)
    self.assertEqual(first[0], 200)
    self.assertIsNotNone(first[1]['destination'])
    cancelled = self.action('clear', remote=True)
    replay = self.request('/api/navigation/action', payload=request, remote=True, cookie=self.remote_cookie)
    self.assertEqual(replay[0], 200)
    self.assertIsNone(replay[1]['destination'])
    self.assertEqual(replay[1]['revision'], cancelled[1]['revision'])
    changed = dict(request, text='geo:41,-89.5')
    self.assertEqual(self.request('/api/navigation/action', payload=changed, remote=True, cookie=self.remote_cookie)[0], 400)

  def test_invalid_shared_intent_is_rejected(self):
    self.navigation.configure({'token': 'pk.test'}, self.navigation.read()['revision'], True)
    for intent in ('../bad', '', 'x' * 65, 7):
      request = {'action': 'share', 'revision': self.navigation.read()['revision'], 'text': 'geo:40.5,-89.5', 'intentId': intent}
      self.assertEqual(self.request('/api/navigation/action', payload=request, remote=True, cookie=self.remote_cookie)[0], 400)
    self.assertIsNone(self.navigation.read()['destination'])

  def test_stale_or_invalid_actions_never_overwrite_configuration(self):
    self.action('configure', patch={'enabled': True})
    before = self.navigation.path.read_bytes()
    status = self.request('/api/navigation/action', payload={'action': 'clear', 'revision': '0'}, cookie=self.local_cookie)[0]
    self.assertEqual(status, 409)
    for payload in ({'action': 'configure', 'revision': '0', 'patch': {'OtherParam': True}},
                    {'action': 'clear', 'revision': '0', 'unwanted': True}, {'action': [], 'revision': '0'},
                    {'action': 'select', 'revision': '0', 'destination': {'name': 'bad', 'latitude': 95, 'longitude': 0}}):
      self.assertIn(self.request('/api/navigation/action', payload=payload, cookie=self.local_cookie)[0], (400, 409))
    self.assertEqual(self.navigation.path.read_bytes(), before)

  def test_poi_search_then_go_retrieves_only_selected_place(self):
    self.action('configure', patch={'token':'pk.synthetic','enabled':True})
    identity = 'poi/id'
    search = {'query':'coffee', 'searchId':str(uuid.uuid4()), 'clientId':str(uuid.uuid4())}
    suggestion = {'suggestions':[{'mapbox_id':identity,'name':'Coffee Shop','feature_type':'poi'}]}
    feature = {'features':[{'properties':{'mapbox_id':identity,'name':'Coffee Shop'},
                            'geometry':{'coordinates':[-90.,40.]}}]}
    with patch('openpilot.starpilot.navigation.owner.response_json',side_effect=[suggestion,feature]) as provider:
      status, result, _ = self.request('/api/navigation/search',payload=search,cookie=self.local_cookie)
      self.assertEqual(status,200)
      self.assertEqual(provider.call_count,1)
      self.assertNotIn('latitude',result['results'][0])
      status, result, _ = self.action('selectPlace',id=identity,searchId=search['searchId'])
      self.assertEqual(status,200)
      self.assertEqual(result['destination']['name'],'Coffee Shop')
      self.assertEqual(provider.call_count,2)
      self.assertEqual(provider.call_args_list[0].args[2]['session_token'],provider.call_args_list[1].args[2]['session_token'])
    self.assertIsNone(self.navigation.read()['destination'])
    self.assertEqual(result['destination']['temporary'],True)
    self.assertEqual(result['recents'],[])
    self.assertNotIn('Coffee Shop',self.navigation.path.read_text())
    self.assertNotIn('pk.synthetic',json.dumps(result))
    self.assertEqual(self.action('selectPlace',id=identity,searchId=search['searchId'])[0],400)
    self.assertEqual(self.action('favorite',destination=result['destination'],label='work')[0],400)
    status, saved, _ = self.action('favorite',destination={'name':'Work address','latitude':41.,'longitude':-91.},label='work')
    self.assertEqual(status,200)
    self.assertEqual(saved['favorites'][0]['label'],'work')
    place = saved['favorites'][0]['id']
    status, labeled, _ = self.action('labelFavorite',id=place,label='home')
    self.assertEqual((status,labeled['favorites'][0]['label']),(200,'home'))
    self.assertEqual(self.action('labelFavorite',id=place,label='gym')[0],400)
    self.assertEqual(self.action('labelFavorite',id=place,label=None)[1]['favorites'][0].get('label'),None)
    self.assertEqual(self.action('removeRecent',id=place)[1]['recents'],[])
    self.assertEqual(self.action('clearRecents')[1]['recents'],[])
    self.assertEqual(self.action('removeFavorite',id=place)[1]['favorites'],[])

  def test_search_names_the_provider_that_found_the_places(self):
    self.action('configure', patch={'token':'pk.synthetic','enabled':True})
    def search():
      return {'query':'bellagio', 'searchId':str(uuid.uuid4()), 'clientId':str(uuid.uuid4())}
    suggestion = {'suggestions':[{'mapbox_id':'poi/id','name':'Coffee Shop','feature_type':'poi'}]}
    with patch('openpilot.starpilot.navigation.owner.response_json', return_value=suggestion):
      self.assertEqual(self.request('/api/navigation/search',payload=search(),cookie=self.local_cookie)[1]['provider'], 'mapbox')
    key = 'AIzaSyFixtureKey_0123456789abcdef'
    self.action('configure', patch={'googleKey':key, 'routeProvider':'google'})
    found = [{'name':'Bellagio','address':'3600 S Las Vegas Blvd','latitude':36.1126,'longitude':-115.1767}]
    with patch('openpilot.starpilot.navigation.google_places.text_search', return_value=found) as google:
      status, result, _ = self.request('/api/navigation/search',payload=search(),cookie=self.local_cookie)
    self.assertEqual((status, result['provider'], result['results'][0]['name']), (200, 'google', 'Bellagio'))
    self.assertNotIn('searchId', result['results'][0])
    self.assertEqual(google.call_args.args[1], key)
    self.assertNotIn(key, json.dumps(result))
    from openpilot.starpilot.navigation.google_places import GooglePlacesFailed
    with (patch('openpilot.starpilot.navigation.google_places.text_search', side_effect=GooglePlacesFailed('budget')),
          patch('openpilot.starpilot.navigation.owner.response_json', return_value=suggestion)):
      self.assertEqual(self.request('/api/navigation/search',payload=search(),cookie=self.local_cookie)[1]['provider'], 'mapbox')

  def test_temporary_search_result_cannot_be_saved(self):
    self.action('configure', patch={'token':'pk.synthetic','enabled':True})
    search = {'query':'coffee', 'searchId':str(uuid.uuid4()), 'clientId':str(uuid.uuid4())}
    suggestion = {'suggestions':[{'mapbox_id':'poi/id','name':'Coffee Shop','feature_type':'poi'}]}
    with patch('openpilot.starpilot.navigation.owner.response_json',return_value=suggestion) as provider:
      self.assertEqual(self.request('/api/navigation/search',payload=search,cookie=self.local_cookie)[0],200)
      status, body, _ = self.action('favoritePlace',id='poi/id',searchId=search['searchId'],label='home')
      self.assertEqual(status,400)
      self.assertIn('cannot be saved',body['error'])
      self.assertEqual(provider.call_count,1)
    self.assertEqual(self.navigation.read()['favorites'],[])

  def test_status_poll_with_route_key_omits_unchanged_route(self):
    self.action('configure', patch={'token':'pk.synthetic','enabled':True})
    status, full, _ = self.request('/api/navigation/status', cookie=self.local_cookie)
    self.assertEqual(status,200)
    status, light, _ = self.request('/api/navigation/status?routeKey=' + full['routeKey'], cookie=self.local_cookie)
    self.assertEqual((status,light['routeUnchanged']),(200,True))
    self.assertNotIn('route',light)
    status, ignored, _ = self.request('/api/navigation/status?routeKey=bogus&x=1', cookie=self.local_cookie)
    self.assertEqual(status,200)
    self.assertIn('route',ignored)

  def test_slow_poi_retrieve_does_not_block_other_settings_actions(self):
    self.action('configure',patch={'token':'pk.synthetic','enabled':True})
    search = {'query':'coffee','searchId':str(uuid.uuid4()),'clientId':str(uuid.uuid4())}
    with patch('openpilot.starpilot.navigation.owner.response_json',return_value={
        'suggestions':[{'mapbox_id':'poi/id','name':'Coffee Shop','feature_type':'poi'}]}):
      self.request('/api/navigation/search',payload=search,cookie=self.local_cookie)
    entered, release, changed = threading.Event(), threading.Event(), threading.Event()
    results = {}
    def slow(*args):
      entered.set()
      release.wait(3)
      return {'features':[{'properties':{'mapbox_id':'poi/id','name':'Coffee Shop'},'geometry':{'coordinates':[-90.,40.]}}]}
    def retrieve():
      results['retrieve'] = self.action('selectPlace',id='poi/id',searchId=search['searchId'])[0]
    def configure():
      results['configure'] = self.action('configure',patch={'token':'pk.changed'})[0]
      changed.set()
    with patch('openpilot.starpilot.navigation.owner.response_json',side_effect=slow):
      worker = threading.Thread(target=retrieve)
      worker.start()
      self.assertTrue(entered.wait(2))
      other = threading.Thread(target=configure)
      other.start()
      try:
        self.assertTrue(changed.wait(1),'Provider retrieval blocked unrelated Galaxy actions')
      finally:
        release.set()
        worker.join(3)
        other.join(3)
    self.assertEqual(results,{'configure':200,'retrieve':409})
    self.assertIsNone(self.navigation.snapshot()['destination'])

  def test_browser_cancel_makes_suggestion_unselectable(self):
    self.action('configure',patch={'token':'pk.synthetic','enabled':True})
    search = {'query':'coffee','searchId':str(uuid.uuid4()),'clientId':str(uuid.uuid4())}
    with patch('openpilot.starpilot.navigation.owner.response_json',return_value={
        'suggestions':[{'mapbox_id':'poi/id','name':'Coffee Shop','feature_type':'poi'}]}) as provider:
      self.assertEqual(self.request('/api/navigation/search',payload=search,cookie=self.local_cookie)[0],200)
      self.assertEqual(self.action('cancelSearch',searchId=search['searchId'])[0],200)
      self.assertEqual(self.action('selectPlace',id='poi/id',searchId=search['searchId'])[0],400)
      self.assertEqual(provider.call_count,1)

  def test_poi_retrieve_rechecks_auth_before_temporary_selection(self):
    self.action('configure',patch={'token':'pk.synthetic','enabled':True})
    search = {'query':'coffee','searchId':str(uuid.uuid4()),'clientId':str(uuid.uuid4())}
    with patch('openpilot.starpilot.navigation.owner.response_json',return_value={
        'suggestions':[{'mapbox_id':'poi/id','name':'Coffee Shop','feature_type':'poi'}]}):
      self.assertEqual(self.request('/api/navigation/search',payload=search,remote=True,cookie=self.remote_cookie)[0],200)
    def revoked(*args):
      self.pairing.unpair()
      return {'features':[{'properties':{'mapbox_id':'poi/id','name':'Coffee Shop'},'geometry':{'coordinates':[-90.,40.]}}]}
    before = self.navigation.path.read_bytes()
    with patch('openpilot.starpilot.navigation.owner.response_json',side_effect=revoked):
      status, result, _ = self.action('selectPlace',id='poi/id',searchId=search['searchId'],remote=True)
    self.assertEqual(status,401)
    self.assertEqual(self.navigation.path.read_bytes(),before)
    self.assertNotIn('Coffee Shop',json.dumps(result))
    self.assertEqual(list(self.navigation.transient_root.glob('*.json')),[])

  def test_search_checks_session_again_after_provider_returns(self):
    self.navigation.search = Mock(side_effect=lambda query: (self.pairing.unpair(), [PLACE])[1])
    result = self.request('/api/navigation/search', payload={'query': 'Library'}, remote=True, cookie=self.remote_cookie)
    self.assertEqual(result[0], 401)
    self.assertNotIn('Library', json.dumps(result[1]))
