"""Galaxy settings, device services and software management."""

import argparse
import hashlib
from contextlib import nullcontext
from http.cookies import SimpleCookie
import json
import os
import re
import secrets
import select
import socket
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit

from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.galaxy.batch_routes import change_drive_state, delete_records, non_default_settings, preview_layout, preview_settings_recovery
from openpilot.starpilot.galaxy.access import AccessStatus, default_owner
from openpilot.starpilot.galaxy.auth import LocalSessions
from openpilot.starpilot.galaxy.http_server import (_LocalHTTPHandler, _LocalHTTPServer, allowed_authority,
                                                  direct_local_connection, install_origin_allowed, static_asset)
from openpilot.starpilot.galaxy.camera_snapshot import CameraSnapshot, SnapshotDenied, SnapshotUnavailable
from openpilot.starpilot.galaxy.remote import default_remote_pairing, gateway_cookie_valid
from openpilot.starpilot.galaxy.crash_reports import CrashChanged, CrashMissing, CrashReports, CrashUnavailable
from openpilot.starpilot.galaxy.device_name import DeviceName
from openpilot.starpilot.galaxy.device_state import DeviceStateSource
from openpilot.starpilot.galaxy.recording_library import RecordingLibrary
from openpilot.starpilot.galaxy.drive_history import DriveHistory, DriveHistoryUnavailable, SegmentInUse, SegmentMissing, recording_details
from openpilot.starpilot.galaxy.drive_stats import DriveStatsOwner
from openpilot.starpilot.galaxy.recording_media import RecordingMedia, RecordingMediaUnavailable
from openpilot.starpilot.galaxy.recording_http import camera_video, send_recording
from openpilot.starpilot.galaxy.replay_http import ReplayHTTP, ReplayHTTPError
from openpilot.starpilot.galaxy.route_inventory import CatalogError
from openpilot.starpilot.galaxy.segment_summary import SegmentSummary, SegmentSummaryChanged, SegmentSummaryUnavailable
from openpilot.starpilot.galaxy.flm_training_results import FlmTrainingResults
from openpilot.starpilot.galaxy.sentry_events import SentryEvents, SentryEventsUnavailable
from openpilot.starpilot.sentry_mode.notifications import NotificationOwner, NotificationUnavailable
from openpilot.starpilot.galaxy.map_status import MapStatus, MapUnavailable
from openpilot.starpilot.galaxy.map_operations import MapOperationError, MapOperations, validate_action
from openpilot.starpilot.galaxy.plots import Plots
from openpilot.starpilot.galaxy.software_status import SoftwareStatus, SoftwareUnavailable
from openpilot.starpilot.galaxy.web_manifest import web_manifest
from openpilot.starpilot.galaxy.software_operations import SoftwareOperations, SoftwareOperationError
from openpilot.starpilot.galaxy.settings import PAGES, SettingsGateway, SettingsChanged, SettingsUnavailable
from openpilot.starpilot.galaxy.onroad_layout import LayoutChanged, OnroadLayoutOwner
from openpilot.starpilot.ui.layout_preview_transport import (request_profile)
from openpilot.starpilot.favorites.owner import FavoritesChanged
from openpilot.starpilot.galaxy.favorites import FavoritesGateway
from openpilot.starpilot.galaxy.system_monitor import SystemMonitor
from openpilot.starpilot.galaxy.local_access import LocalAccess
from openpilot.starpilot.galaxy.tmux_live import TmuxLive
from openpilot.starpilot.galaxy.vehicle_selection import VehicleSelectionChanged, VehicleSelectionGateway, VehicleSelectionUnverified
from openpilot.starpilot.bluetooth.owner import BluetoothOwner, BluetoothRejected, BluetoothUnavailable, normalized_address
from openpilot.starpilot.controllers.transport import (ControllerBusy, ControllerDenied, ControllerInvalid,
                                                      ControllerUnavailable, request_action as controller_action,
                                                      request_status as controller_status)
from openpilot.starpilot.models.runtime import ModelStatusSource
from openpilot.starpilot.audio.downloads import SoundDownloads, SoundDownloadError


WEB = Path(__file__).resolve().parent / 'web'
INSTANCE = secrets.token_hex(8)


def phone_claim_view():
  from openpilot.starpilot.galaxy_bluetooth.bridge import claim
  return claim.view(claim.CLAIM_PATH)


def link_music_phone(address):
  """Music paired in the window an app claimed (either order): remember which device is the app's phone."""
  try:
    from openpilot.starpilot.galaxy_bluetooth.bridge import claim, pairing
    claim.note_paired(address)
    linked = claim.take_link()
    if linked:
      pairing.link_address(*linked)
  except (OSError, ValueError):
    pass


def forget_app_keys(address):
  """Forgetting a Bluetooth device also forgets the app key that was paired with it."""
  try:
    from openpilot.starpilot.galaxy_bluetooth.bridge import pairing
    pairing.remove_by_address(address)
  except (OSError, ValueError):
    pass


def make_server(*, port=8082, host='127.0.0.1', monitor=None, owner=None, crashes=None, software=None, maps=None, models=None, settings=None,
                plots=None, map_operations=None, recordings=None, recording_media=None, segment_summary=None,
                sentry_events=None, notifications=None, training_results=None, bluetooth=None, vehicle_selection=None,
                model_manager=None, layouts=None, favorites=None,
                sounds=None, software_operations=None, drive_stats=None, layout_preview_socket=None, controllers_socket=None,
                remote_pairing=None, parked=None, camera_snapshot=None, clock=time.monotonic, starport_setup=None,
                starport_client=None, navigation=None, offline_maps=None, drive_state=None, cloud_provider=None, cloud_offroad=None, projection_layout=None,
                local_access=None, tmux_live=None, starport_logs=None, starport_log_sender=None, hotspot=None, toyota_tools=None, replay_client=None, replay_root=None,
                spotify_setup=None, companion=None, phone_location=None, reverse_proxy=None):
  from openpilot.starpilot.galaxy.companion import CompanionRegistry, APK_PATH
  from openpilot.starpilot.gps.phone import PhoneGpsPublisher
  companion_source = companion if companion is not None else CompanionRegistry()
  if training_results is None:
    from openpilot.common.params import Params
    training_results = FlmTrainingResults(params=Params())
  training_results_source = training_results
  phone_location_source = phone_location if phone_location is not None else PhoneGpsPublisher()
  cloud = cloud_provider
  hotspot_source = hotspot
  hotspot_source_lock = threading.Lock()

  def hotspot_owner():
    nonlocal hotspot_source
    with hotspot_source_lock:
      if hotspot_source is None:
        from openpilot.starpilot.galaxy.hotspot import HotspotSettings
        hotspot_source = HotspotSettings()
    return hotspot_source

  def cloud_status():
    from openpilot.starpilot.connect.provider import status
    return cloud.status() if cloud is not None else status()

  def cloud_select(name, revision, authorized):
    from openpilot.starpilot.connect.provider import select_provider
    return cloud.select(name, revision, authorized) if cloud is not None else select_provider(name, revision, authorized)

  address_source = local_access if local_access is not None else LocalAccess(port)
  console_source = tmux_live if tmux_live is not None else TmuxLive()
  reports = crashes if crashes is not None else CrashReports()
  crash_reports_lock = threading.Lock()
  starport_logs_source = starport_logs

  def starport_logs():
    nonlocal starport_logs_source
    if starport_logs_source is None:
      from openpilot.starpilot.galaxy.starport_logs import StarPortLogs
      starport_logs_source = StarPortLogs()
    return starport_logs_source
  starport_log_sender_source = starport_log_sender
  starport_log_sender_lock = threading.Lock()

  def starport_log_sender():
    nonlocal starport_log_sender_source
    with starport_log_sender_lock:
      if starport_log_sender_source is None:
        from openpilot.starpilot.galaxy.starport_send import StarPortLogSender, device_summary
        source = starport_logs()
        starport_log_sender_source = StarPortLogSender(source.bundle, summary=lambda: device_summary(source.directory))
    return starport_log_sender_source
  software_source = software
  software_operations_source = software_operations
  software_lock = threading.Lock()
  map_source = maps if maps is not None else MapStatus()
  navigation_source = navigation
  navigation_lock = threading.Lock()
  operations = map_operations if map_operations is not None else MapOperations()
  drive_control = drive_state
  drive_lock = threading.Lock()
  replay_api = ReplayHTTP(replay_client, replay_root)
  history = recordings if recordings is not None else DriveHistory()
  drive_stats_source = drive_stats
  drive_stats_lock = threading.Lock()
  recording_library = RecordingLibrary(history)
  recordings_lock = threading.Lock()
  recording_media_source = recording_media
  recording_media_lock = threading.Lock()
  summary_source = segment_summary
  summary_lock = threading.Lock()
  motion_events = sentry_events if sentry_events is not None else SentryEvents()
  sentry_events_lock = threading.Lock()
  last_simulation = [float("-inf")]
  notification_owner = notifications
  snapshots = camera_snapshot if camera_snapshot is not None else CameraSnapshot()
  access = owner if owner is not None else default_owner()
  from openpilot.starpilot.galaxy.reverse_proxy import ReverseProxySettings
  proxy_settings = ReverseProxySettings(access.root)
  pairing = remote_pairing if remote_pairing is not None else default_remote_pairing()
  pairing_authority = None
  device_name = DeviceName(pairing.root)

  def remote_generation(generation, record):
    return hashlib.sha256((generation or b'') + bytes.fromhex(record['session'])).digest()
  evidence_source = None
  if parked is None:
    from openpilot.starpilot.galaxy.evidence import EvidenceSource
    evidence_source = EvidenceSource()

  monitor_source = monitor if monitor is not None else SystemMonitor(messages=evidence_source)
  weather_source = None
  weather_lock = threading.Lock()

  def weather_status():
    nonlocal weather_source
    with weather_lock:
      if weather_source is None:
        from openpilot.starpilot.galaxy.weather import WeatherSource
        weather_source = WeatherSource()
        server.weather_source = weather_source
      return weather_source.sample()
  vehicle_display = DeviceStateSource(evidence_source)

  def context_source(params):
    from openpilot.starpilot.galaxy.settings import LiveContextSource
    return LiveContextSource(params, messages=evidence_source, evidence_wait_ms=0) if evidence_source is not None else LiveContextSource(params)

  if parked is None:
    from openpilot.common.params import Params
    try:
      pairing_authority = context_source(Params())
      parked = pairing_authority.parked
      parked()
    except BaseException:
      if evidence_source is not None:
        evidence_source.close()
      raise
  configuration_allowed = pairing_authority.configuration_allowed if pairing_authority is not None else parked
  def cloud_allowed():
    from openpilot.common.params import Params
    return bool(parked()) and bool(cloud_offroad() if cloud_offroad is not None else Params().get_bool('IsOffroad'))

  sessions = LocalSessions(clock)
  local_generation = secrets.token_bytes(32)
  session_lock = threading.Lock()
  sample_lock = threading.Lock()
  effect_lock = threading.Lock()
  settings_lock = threading.Lock()
  models_lock = threading.Lock()
  settings_gateway = settings
  recovery_gateway = None
  vehicle_gateway = vehicle_selection
  toyota_source = toyota_tools
  model_source = models
  model_manager_source = model_manager
  plot_source = plots
  plots_lock = threading.Lock()
  bluetooth_lock = threading.Lock()
  bluetooth_source = bluetooth
  starport_setup_source = starport_setup
  spotify_setup_source = spotify_setup
  starport_client_source = starport_client
  starport_source_lock = threading.Lock()
  starport_registry_source = None
  projection_layout_source = projection_layout
  layout_source = layouts
  layout_lock = threading.Lock()
  favorites_source = favorites
  favorites_lock = threading.Lock()
  sound_source = sounds
  sounds_lock = threading.Lock()

  def drive_owner():
    nonlocal drive_control
    with drive_lock:
      if drive_control is None:
        from openpilot.common.params import Params
        from openpilot.starpilot.storage import starpilot_storage_root
        from openpilot.starpilot.drive_state.owner import DriveStateOwner
        from openpilot.starpilot.drive_state.evidence import PhysicalSource
        from openpilot.starpilot.drive_state.control import DriveStateControl
        physical = PhysicalSource(evidence_source)
        drive_control = DriveStateControl(DriveStateOwner(Params(), starpilot_storage_root() / 'drive-state'), physical)
        server.drive_physical_source = physical
      return drive_control

  def navigation_owner():
    nonlocal navigation_source
    with navigation_lock:
      if navigation_source is None:
        from openpilot.starpilot.navigation.owner import NavigationOwner
        navigation_source = NavigationOwner()
      server.navigation_source = navigation_source
      return navigation_source

  offline_maps_source = offline_maps

  def offline_maps_owner():
    nonlocal offline_maps_source
    with navigation_lock:
      if offline_maps_source is None:
        from openpilot.starpilot.navigation.offline_owner import OfflineMapsOwner
        offline_maps_source = OfflineMapsOwner(position=lambda: navigation_owner().position_store.read())
      return offline_maps_source

  def statistics_owner():
    nonlocal drive_stats_source
    with drive_stats_lock:
      if drive_stats_source is None:
        from openpilot.common.params import Params
        params = Params()
        from openpilot.starpilot.drive_state.evidence import PhysicalSource
        authority = PhysicalSource(evidence_source)
        server.drive_stats_authority = authority
        def analysis_allowed():
          # Use the effective device lifecycle, including Force Offroad.
          authority.allowed()  # Refresh paired clocks and the existing evidence snapshot.
          return params.get_bool('IsOffroad') and authority.effective() is False
        drive_stats_source = DriveStatsOwner(root=history.root, permitted=analysis_allowed,
                                             metric=lambda: params.get_bool('IsMetric'))
      server.drive_stats_source = drive_stats_source
      return drive_stats_source

  def software_owner():
    nonlocal software_operations_source
    with software_lock:
      if software_operations_source is None:
        software_operations_source = SoftwareOperations(parked=parked, refresh=evidence_source.refresh if evidence_source is not None else None)
      server.software_operations_source = software_operations_source
      return software_operations_source

  def software_snapshot(*, include_history=True):
    result = (software_source if software_source is not None else SoftwareStatus()).snapshot()
    if software_source is None or software_operations_source is not None:
      operations = software_owner().snapshot() if include_history else software_owner().snapshot(include_history=False)
      result = {**result, 'operations': operations}
    return result

  def sound_owner():
    nonlocal sound_source
    with sounds_lock:
      if sound_source is None:
        from openpilot.common.params import Params
        authority = context_source(Params())
        sound_source = SoundDownloads(parked=authority.parked)
        server.sound_authority = authority
      server.sound_source = sound_source
      return sound_source

  def favorites_owner():
    nonlocal favorites_source
    with favorites_lock:
      if favorites_source is None:
        from openpilot.common.params import Params
        params = Params()
        favorites_source = FavoritesGateway(params, context_source(params))
        server.favorites_source = favorites_source
      return favorites_source

  def layout_owner():
    nonlocal layout_source
    with layout_lock:
      if layout_source is None:
        from openpilot.common.params import Params
        params = Params()
        authority = context_source(params)
        server.layout_authority = authority
        layout_source = OnroadLayoutOwner(params, authority.parked)
      return layout_source

  def projection_layout_owner():
    nonlocal projection_layout_source
    with layout_lock:
      if projection_layout_source is None:
        from openpilot.common.params import Params
        from openpilot.starpilot.galaxy.projection_layout import ProjectionLayoutOwner, host_owner
        params = Params()
        projection_layout_source = host_owner(params, configuration_allowed) or ProjectionLayoutOwner(params, configuration_allowed)
      return projection_layout_source

  def bluetooth_session_valid(identity):
    token, generation = identity
    with session_lock:
      current = access.current_generation()
      if (generation == local_generation or current == generation) and sessions.valid(token, generation):
        return True
      record = pairing.read()
      return record is not None and generation == remote_generation(current, record) and \
             (sessions.valid(token, generation) or gateway_cookie_valid(token, record))

  def bluetooth_paired(address, name, uuids):
    link_music_phone(address)
    spotify_owner().adopt_phone(address, name, uuids)

  def bluetooth_owner():
    nonlocal bluetooth_source
    with bluetooth_lock:
      if bluetooth_source is None:
        from openpilot.common.params import Params
        authority = context_source(Params())
        bluetooth_source = BluetoothOwner(authority.configuration_allowed, session_valid=bluetooth_session_valid,
                                          on_paired=bluetooth_paired)
        server.bluetooth_authority = authority
        server.bluetooth_source = bluetooth_source
    return bluetooth_source

  def starport_setup_owner():
    nonlocal starport_setup_source
    with bluetooth_lock:
      if starport_setup_source is None:
        from openpilot.common.params import Params
        from openpilot.starpilot.galaxy.starport_setup import StarPortSetup
        from openpilot.starpilot.system.starport.settings import enabled as starport_enabled, set_enabled as set_starport_enabled
        def bluetooth_ready():
          snapshot = bluetooth_owner().snapshot()
          return bool(snapshot['available'] and snapshot['powered'] and snapshot['errorCode'] is None)
        def install_ready():
          runtime = Path(__file__).resolve().parents[1] / 'system/starport'
          return ((runtime / 'hw/libstarport_encoder.so').is_file() and (runtime / 'current_car_ui.py').is_file() and
                  Path('/dev/dri/renderD128').exists())
        def service_ready():
          try:
            return bool(starport_client_owner().call('status')['status'])
          except (OSError, RuntimeError, KeyError, TypeError):
            return False
        def enable_bluetooth(identity):
          bluetooth_owner().request('power', enabled=True, session=identity, power_on_for_starport=True)
        starport_setup_source = StarPortSetup(parked=configuration_allowed, enabled=lambda: starport_enabled(Params()),
                                          session_valid=bluetooth_session_valid, bluetooth_enabled=bluetooth_ready,
                                          install_ready=install_ready, service_ready=service_ready,
                                          set_enabled=lambda value: set_starport_enabled(Params(), value),
                                          enable_bluetooth=enable_bluetooth)
        server.starport_setup_source = starport_setup_source
    return starport_setup_source

  def spotify_owner():
    nonlocal spotify_setup_source
    with bluetooth_lock:
      if spotify_setup_source is None:
        from openpilot.starpilot.galaxy.spotify_setup import SpotifySetup
        def paired_devices(identity):
          return bluetooth_owner().snapshot(session=identity).get('devices', [])
        spotify_setup_source = SpotifySetup(parked=configuration_allowed, session_valid=bluetooth_session_valid,
                                            paired_devices=paired_devices)
        server.spotify_setup_source = spotify_setup_source
    return spotify_setup_source

  def starport_source_registry():
    nonlocal starport_registry_source
    with starport_source_lock:
      if starport_registry_source is None:
        from openpilot.starpilot.galaxy.starport_source import GalaxySourceRegistry
        starport_registry_source = GalaxySourceRegistry(lambda identity: bluetooth_session_valid(identity) and starport_setup_owner().enabled(), clock)
        server.starport_source_registry = starport_registry_source
    return starport_registry_source

  def starport_client_owner():
    nonlocal starport_client_source
    with starport_source_lock:
      if starport_client_source is None:
        from openpilot.starpilot.system.starport.protocol import StarPortClient
        starport_client_source = StarPortClient()
    return starport_client_source

  def plot_status():
    nonlocal plot_source
    with plots_lock:
      if plot_source is None:
        plot_source = Plots()
        server.plots_source = plot_source
    return plot_source.snapshot()

  def local_media():
    nonlocal recording_media_source
    with recording_media_lock:
      if recording_media_source is None:
        recording_media_source = RecordingMedia(getattr(history, 'root', None))
        server.recording_media_source = recording_media_source
    return recording_media_source

  def local_summary():
    nonlocal summary_source
    with summary_lock:
      if summary_source is None:
        summary_source = SegmentSummary(history.root)
    return summary_source

  def feature_settings():
    nonlocal settings_gateway
    with settings_lock:
      if settings_gateway is None:
        from openpilot.common.params import Params
        params = Params()
        settings_gateway = SettingsGateway(params, context_source(params), starport=starport_client_owner)
        server.settings_source = settings_gateway
      return settings_gateway

  def settings_recovery():
    nonlocal recovery_gateway
    gateway = feature_settings()
    with settings_lock:
      if recovery_gateway is None:
        from openpilot.starpilot.galaxy.defaults_recovery import DefaultsRecovery
        recovery_gateway = DefaultsRecovery(gateway)
      return recovery_gateway

  def toyota_owner():
    nonlocal toyota_source
    with settings_lock:
      if toyota_source is None:
        from openpilot.common.params import Params
        from openpilot.starpilot.galaxy.toyota_tools import ToyotaTools
        params = Params()
        toyota_source = ToyotaTools(params, context_source(params))
        server.toyota_tools_source = toyota_source
      return toyota_source

  def vehicle_choices():
    nonlocal vehicle_gateway
    with settings_lock:
      if vehicle_gateway is None:
        from openpilot.common.params import Params
        params = Params()
        vehicle_gateway = VehicleSelectionGateway(params, context_source(params))
        server.vehicle_selection_source = vehicle_gateway
      return vehicle_gateway

  def model_status():
    nonlocal model_source
    with models_lock:
      if model_source is None:
        from openpilot.common.params import Params
        model_source = ModelStatusSource(Params())
        server.model_source = model_source
      return model_source.json()

  def model_owner():
    nonlocal model_manager_source
    with models_lock:
      if model_manager_source is None:
        from openpilot.common.params import Params
        from openpilot.starpilot.models.manager import ModelManager
        authority = context_source(Params())
        model_manager_source = ModelManager(parked=authority.parked, refresh_catalog=True)
        server.model_authority = authority
      server.model_manager_source = model_manager_source
      return model_manager_source

  def model_laboratory():
    from openpilot.starpilot.models.laboratory import ModelLaboratory
    return ModelLaboratory(model_owner())

  def login(password):
    # Keep throttling, verifier work and session creation in one transaction.
    # No socket reads or writes run under this lock.
    with session_lock:
      if access.status().status != AccessStatus.CONFIGURED_LOCAL:
        return 503, {'error': 'Local Galaxy access is unavailable'}, None
      if sessions.throttled():
        return 429, {'error': 'Try again shortly'}, None
      generation = access.authenticate_generation(password)
      if generation is None and password != password.strip():
        generation = access.authenticate_generation(password.strip())
      if generation is None:
        sessions.failed_login()
        return 401, {'error': 'Sign in failed'}, None
      return 200, {'authenticated': True}, sessions.create(generation)

  class Handler(_LocalHTTPHandler):
    def camera_video(self, encoded_name: str, *, combined=False):
      camera_video(self, encoded_name, media=local_media, combined=combined)

    def send_recording(self, lease, content_type='video/mp4'):
      send_recording(self, lease, content_type)

    def local_request(self, *, mutation=False):
      proxy = reverse_proxy if reverse_proxy is not None else proxy_settings.current
      host = self.headers.get('Host', '')
      origin = self.headers.get('Origin')
      local_address = self.connection.getsockname()[0]
      if getattr(self.server, 'remote_transport', False):
        record = pairing.read()
        valid_host = record is not None and host in (f'{record["slug"]}.devices.local', 'galaxy.firestar.link')
        valid_origin = origin is None or origin == 'https://galaxy.firestar.link'
        if not valid_host or not valid_origin or mutation and origin != 'https://galaxy.firestar.link':
          self.json(403, {'error': 'Remote Galaxy request denied'})
          return False
        prefix = f'/{record["slug"]}'
        if self.path == prefix and self.command in ('GET', 'HEAD'):
          self.send_response(308)
          self.send_header('Location', prefix + '/')
          self.send_header('Content-Length', '0')
          self.send_header('Cache-Control', 'no-store')
          self.end_headers()
          return False
        if self.path.startswith(prefix + '/') or self.path == prefix:
          self.path = self.path[len(prefix):] or '/'
        return True
      if proxy is not None and proxy.allows(self.client_address[0], host, origin, mutation=mutation):
        return True
      if not allowed_authority(host, local_address, self.server.server_port) or \
         (mutation and origin != f'http://{host}') or \
         (origin is not None and origin != f'http://{host}'):
        self.json(403, {'error': 'Local access only'})
        return False
      return True

    def session_token(self):
      try:
        cookie = SimpleCookie()
        cookie.load(self.headers.get('Cookie', ''))
        return cookie['galaxy_session'].value if 'galaxy_session' in cookie else None
      except Exception:
        return None

    def credential_state(self):
      return access.status().status

    def direct_local(self):
      proxy = reverse_proxy if reverse_proxy is not None else proxy_settings.current
      return not getattr(self.server, 'remote_transport', False) and \
             not (proxy is not None and proxy.matches_peer(self.client_address[0])) and \
             direct_local_connection(self.client_address[0], self.connection.getsockname()[0], self.headers)

    def spotify_install_allowed(self):
      proxy = reverse_proxy if reverse_proxy is not None else proxy_settings.current
      if proxy is not None and proxy.matches_peer(self.client_address[0]):
        return False
      return install_origin_allowed(self.client_address[0], self.connection.getsockname()[0], self.headers,
                                    getattr(self.server, 'remote_transport', False))

    def authenticated(self):
      return self.settings_session() is not None

    def require_session(self):
      if self.authenticated():
        return True
      if self.direct_local():
        self.json(401, {'error': 'Refresh the local Galaxy session'})
        return False
      if getattr(self.server, 'remote_transport', False):
        self.json(401, {'error': 'Sign in through the Galaxy gateway'})
        return False
      status = self.credential_state()
      if status != AccessStatus.CONFIGURED_LOCAL:
        self.json(503, {'error': 'Local Galaxy access is unavailable' if status == AccessStatus.UNAVAILABLE else
                         'Remote Galaxy access is not configured',
                        'code': 'access_unavailable' if status == AccessStatus.UNAVAILABLE else 'setup_required'})
        return False
      self.json(401, {'error': 'Sign in to Galaxy'})
      return False

    def settings_session(self):
      token = self.session_token()
      with session_lock:
        if self.direct_local() and sessions.valid(token, local_generation):
          return token, local_generation
        generation = access.current_generation()
        if getattr(self.server, 'remote_transport', False):
          record = pairing.read()
          if record is None:
            return None
          paired_generation = remote_generation(generation, record)
          if gateway_cookie_valid(token, record):
            return token, paired_generation
          generation = paired_generation
        return (token, generation) if sessions.valid(token, generation) else None

    def starport_command(self, identity, command, **arguments):
      """Bind one daemon command to this live Galaxy session."""
      if self.settings_session() != identity:
        raise ValueError('Galaxy session changed')
      source = starport_source_registry().mint(identity)
      try:
        if self.settings_session() != identity:
          raise ValueError('Galaxy session changed')
        return starport_client_owner().call(command, source=source, **arguments)
      finally:
        starport_source_registry().revoke_source(source)

    def phone_claim(self, operation, result):
      """The same window that lets a phone pair for music also lets the StarPilot app claim its key."""
      from openpilot.starpilot.galaxy_bluetooth.bridge import claim
      try:
        if operation == 'cancel_pair':
          claim.clear()
        elif type(result) is dict:
          claim.arm(result.get('adapterAddress', ''), result.get('adapterName', ''))
      except OSError:
        pass

    def starport_phone_window(self, identity, operation, payload=None):
      """The phone pairing window inside a running Android Auto projection; handled False when none holds the adapter."""
      registry = starport_source_registry()
      minted = operation in ('accept_pair', 'pair', 'scan', 'stop_scan')
      source = registry.mint(identity) if minted else registry.current(identity)
      if source is None:
        return {'handled': False}
      arguments = {'address': payload['address']} if operation == 'pair' and payload is not None else {}
      if operation == 'pairing_response' and payload is not None:
        arguments = {'prompt_id': payload['promptId'], 'accepted': payload['accepted'], 'value': payload.get('value', '')}
      try:
        if self.settings_session() != identity:
          raise ValueError('Galaxy session changed')
        delegated = starport_client_owner().call('phone_window', source=source, operation=operation, **arguments)
      except (OSError, RuntimeError) as error:
        # Falling back to this process cannot work while Android Auto holds the adapter, so say why it was skipped.
        cloudlog.warning(f'android auto phone window {operation} not delegated: {error!r}')
        delegated = {'handled': False}
      if type(delegated) is not dict or type(delegated.get('handled')) is not bool:
        delegated = {'handled': False}
      if minted and (not delegated['handled'] or 'error_code' in delegated or operation in ('scan', 'stop_scan')) or \
         operation == 'cancel_pair' and delegated['handled']:
        registry.revoke_source(source)
      found = delegated.get('bluetooth')
      paired = found.pop('windowPaired', None) if type(found) is dict else None
      if type(paired) is dict:
        link_music_phone(paired.get('address', ''))
      if type(paired) is dict and configuration_allowed():
        try:
          spotify_owner().adopt_phone(paired['address'], paired['name'], tuple(paired['uuids']))
        except Exception:
          pass
      return delegated

    def do_GET(self):
      if not self.local_request():
        return
      path = urlsplit(self.path).path
      if path.startswith(('/api/replay/', '/api/recordings/')) and not self.direct_local():
        self.json(403, {'error': 'Routes require a local Galaxy connection', 'code': 'local_only'})
        return
      if path.startswith('/api/replay/'):
        replay_api.get(self, path)
        return
      if path.startswith('/api/recordings/route/'):
        if urlsplit(self.path).query:
          self.json(400, {'error': 'Unexpected media query'})
        else:
          self.camera_video(path.removeprefix('/api/recordings/route/'), combined=True)
        return
      if path.startswith('/api/recordings/media/'):
        if urlsplit(self.path).query:
          self.json(400, {'error': 'Unexpected media query'})
        else:
          self.camera_video(path.removeprefix('/api/recordings/media/'))
        return
      if path == '/api/connect/proxy':
        if not self.require_session():
          return
        try:
          self.json(200, {**proxy_settings.snapshot(), 'canEdit': cloud_allowed(),
                          'passwordConfigured': access.status().status == AccessStatus.CONFIGURED_LOCAL})
        except (OSError, ValueError):
          self.json(503, {'error': 'Reverse proxy configuration is unavailable'})
        return
      if path == '/api/connect/provider':
        if not self.require_session():
          return
        try:
          result = cloud_status()
          result['canSelect'] = cloud_allowed()
        except (OSError, ValueError, TypeError):
          self.json(503, {'error': 'Cloud provider configuration is unavailable'})
        else:
          self.json(200, result)
        return
      if path == '/api/auth/session':
        if self.direct_local():
          with session_lock:
            token = self.session_token()
            if not sessions.valid(token, local_generation):
              token = sessions.create(local_generation, reset_failures=False)
          self.respond(200, b'{"authenticated":true,"state":"configured","localAccess":true}',
                       cookie=f'galaxy_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={sessions.LIFETIME}')
          return
        if getattr(self.server, 'remote_transport', False):
          self.json(200, {'authenticated': self.authenticated(), 'localAccess': False, 'gatewayAccess': True,
                          'state': 'configured' if pairing.read() is not None else 'setup_required'})
          return
        status = self.credential_state()
        self.json(200, {'authenticated': status == AccessStatus.CONFIGURED_LOCAL and self.authenticated(),
                        'localAccess': False,
                        **({'gatewayAccess': True} if getattr(self.server, 'remote_transport', False) else {}),
                        'state': 'configured' if status == AccessStatus.CONFIGURED_LOCAL else
                                 'unavailable' if status == AccessStatus.UNAVAILABLE else 'setup_required'})
      elif path == '/api/localization':
        if not self.require_session():
          return
        from openpilot.common.params import Params
        from openpilot.starpilot.galaxy.localization import language_snapshot
        previous = parse_qs(urlsplit(self.path).query).get('language', [''])[0]
        self.json(200, language_snapshot(Params(), previous))
      elif path == '/api/galaxy/device-name':
        if not self.require_session():
          return
        try:
          result = {'name': device_name.read()}
        except (OSError, ValueError, UnicodeError):
          self.json(503, {'error': 'The saved comma name could not be read'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path in ('/app.apk', '/api/companion/app.apk'):
        if getattr(self.server, 'remote_transport', False):
          self.json(403, {'error': 'Download the app over local Wi-Fi'})
          return
        if not self.direct_local() and not self.require_session():
          return
        try:
          handle = APK_PATH.open('rb')
        except OSError:
          self.json(404, {'error': 'Companion APK is not installed'})
          return
        with handle:
          self.send_response(200)
          self.send_header('Content-Type', 'application/vnd.android.package-archive')
          self.send_header('Content-Length', str(os.fstat(handle.fileno()).st_size))
          self.send_header('Content-Disposition', 'attachment; filename="starpilot-companion.apk"')
          self.send_header('Cache-Control', 'no-store')
          self.end_headers()
          if self.command != 'HEAD':
            while chunk := handle.read(65536):
              self.wfile.write(chunk)
      elif path == '/api/companion/status':
        if self.require_session():
          try:
            self.json(200, companion_source.snapshot())
          except (OSError, ValueError):
            self.json(503, {'error': 'Companion settings unavailable'})
      elif path == '/api/galaxy/hotspot':
        if not self.require_session():
          return
        try:
          result = hotspot_owner().snapshot(parked())
        except (OSError, ValueError):
          self.json(503, {'error': 'Hotspot settings are unavailable.'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path in ('/api/galaxy/session', '/api/galaxy/session/qr.svg'):
        record = pairing.read()
        remote = getattr(self.server, 'remote_transport', False)
        if not self.direct_local() and not (remote and record and gateway_cookie_valid(self.session_token(), record)):
          self.json(403, {'error': 'A direct connection or matching gateway cookie is required'})
          return
        if not self.require_session():
          return
        if record is None:
          self.json(200, {'paired': False})
          return
        token = quote(f"{record['slug']}:{record['session']}", safe='')
        if path.endswith('/qr.svg'):
          from openpilot.common.qrcode import _Qr, _capacity
          raw = ('starpilot-remote://add?key=' + token).encode()
          version = next(version for version in range(1, 21) if 4 + 8 + len(raw) * 8 <= _capacity(version) * 8)
          matrix = _Qr(version, raw).modules
          size = len(matrix) + 8
          cells = ''.join(f'<path d="M{x + 4} {y + 4}h1v1h-1z"/>' for y, row in enumerate(matrix) for x, active in enumerate(row) if active)
          svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">' +
                 f'<rect width="100%" height="100%" fill="white"/><g fill="black">{cells}</g></svg>')
          self.respond(200, svg.encode(), 'image/svg+xml', cache_control='no-store')
        else:
          self.json(200, {'paired': True, 'slug': record['slug'], 'url': pairing.url(record['slug']),
                          'cookieName': 'galaxy_session', 'sessionToken': token})
      elif path == '/api/galaxy/status':
        if not self.require_session():
          return
        if pairing_authority is not None:
          parked()
        from openpilot.starpilot.galaxy.remote_service import frpc_binary
        record = pairing.read()
        legacy_available = access.legacy_available()
        self.json(200, {'paired': record is not None, 'url': pairing.url(record['slug']) if record else '',
                        'tunnelClientAvailable': frpc_binary() is not None,
                        'legacyPassword': access.status().status == AccessStatus.LEGACY_IMPORT_AVAILABLE,
                        'legacyPairingAvailable': legacy_available is True})
      elif path == '/api/galaxy/qr.svg':
        if not self.require_session():
          return
        record = pairing.read()
        if record is None:
          self.json(404, {'error': 'Galaxy is not paired'})
          return
        from openpilot.common.qrcode import _Qr, _capacity
        raw = pairing.url(record['slug']).encode()
        version = next(version for version in range(1, 21)
                       if 4 + 8 + len(raw) * 8 <= _capacity(version) * 8)
        matrix = _Qr(version, raw).modules
        size = len(matrix) + 8
        cells = ''.join(f'<path d="M{x + 4} {y + 4}h1v1h-1z"/>' for y, row in enumerate(matrix)
                        for x, active in enumerate(row) if active)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">' +
               f'<rect width="100%" height="100%" fill="white"/><g fill="black">{cells}</g></svg>')
        self.respond(200, svg.encode(), 'image/svg+xml')
      elif path in ('/data/runtime.json', '/api/companion/runtime'):
        self.json(200, {'schemaVersion': 1, 'monitor': 'local', 'instance': INSTANCE})
      elif path == '/api/companion/web-manifest':
        self.json(200, web_manifest(WEB))
      elif path == '/api/companion/bluetooth-ready':
        if not self.require_session():
          return
        from openpilot.starpilot.galaxy.pairing_ready import bridge_state, pairing_readiness
        self.json(200, pairing_readiness(parked=configuration_allowed, snapshot=lambda: bluetooth_owner().snapshot(), bridge=bridge_state))
      elif path in ('/api/local-access', '/api/tmux/live', '/api/troubleshoot'):
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if path == '/api/local-access':
            result = address_source.snapshot()
          elif path == '/api/tmux/live':
            result = console_source.snapshot()
          else:
            result = feature_settings().diagnostics()
            result['device'] = vehicle_display.sample()
        except (OSError, RuntimeError, ValueError, SettingsUnavailable):
          self.json(503, {'error': 'Device diagnostics could not be read. Refresh or open System Monitor.'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/weather':
        if not self.require_session():
          return
        result = weather_status()
        if self.require_session():
          self.json(200, result)
      elif path == '/api/system/monitor':
        if not self.require_session():
          return
        try:
          # CPU deltas and PID reuse tracking have one shared sample history.
          with sample_lock:
            result = monitor_source.sample()
        except OSError:
          self.json(503, {'error': 'System telemetry is unavailable'})
        else:
          self.json(200, result)
      elif path == '/api/crash-reports' or path.startswith('/api/crash-reports/'):
        if not self.require_session():
          return
        try:
          result = reports.list() if path == '/api/crash-reports' else reports.preview(path.removeprefix('/api/crash-reports/'))
        except CrashMissing:
          self.json(404, {'error': 'Crash report is no longer available'})
        except CrashChanged:
          self.json(409, {'error': 'Crash report changed; refresh the list'})
        except CrashUnavailable:
          self.json(503, {'error': 'Crash reports are unavailable'})
        else:
          # A session can be revoked while a directory scan or file read runs.
          # Keep filesystem work outside the session lock, then check again
          # immediately before sending potentially sensitive content.
          if self.require_session():
            self.json(200, result)
      elif path == '/api/starport/logs/send':
        if self.require_session():
          self.json(200, starport_log_sender().status())
      elif path == '/api/starport/logs' or path.startswith('/api/starport/logs/'):
        if not self.require_session():
          return
        from openpilot.starpilot.galaxy.starport_logs import LogMissing, LogsUnavailable
        name = unquote(path.removeprefix('/api/starport/logs/file/')) if path.startswith('/api/starport/logs/file/') else None
        if name is not None and not re.fullmatch(r'[\w.-]{1,128}', name):
          self.json(400, {'error': 'Invalid log name'})
          return
        if path not in ('/api/starport/logs', '/api/starport/logs/bundle') and name is None:
          self.json(404, {'error': 'Not found'})
          return
        try:
          if path == '/api/starport/logs':
            result = starport_logs().list()
          elif name is None:
            result = starport_logs().bundle()
          else:
            result = starport_logs().file(name)
        except LogMissing:
          self.json(404, {'error': 'That log is no longer kept; refresh the list'})
        except LogsUnavailable:
          self.json(503, {'error': 'Android Auto logs are unavailable'})
        else:
          # The session can be revoked while logs are read; check again right before sending them.
          if not self.require_session():
            return
          if path == '/api/starport/logs':
            self.json(200, result)
          else:
            filename, body = result
            self.attachment(filename, body, 'application/zip' if filename.endswith('.zip') else 'text/plain; charset=utf-8')
      elif path.startswith('/api/navigation/map/tiles/'):
        if not self.require_session():
          return
        match = re.fullmatch(r'/api/navigation/map/tiles/([0-9]{1,2})/([0-9]{1,6})/([0-9]{1,6})\.png', path)
        if match is None:
          self.json(400, {'error': 'Invalid map tile'})
          return
        try:
          query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
          theme = query.get('theme', ['light'])[0]
          if set(query) - {'theme'} or theme not in ('light', 'dark'):
            raise ValueError('Invalid map theme')
        except ValueError:
          self.json(400, {'error': 'Invalid map theme'})
          return
        try:
          tile = navigation_owner().map_tile(*(int(value) for value in match.groups()), theme)
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Map tiles are unavailable'})
        else:
          if self.require_session():
            # Map imagery is not private data; let the browser keep it while panning back and forth.
            self.respond(200, tile, 'image/png', cache_control='private, max-age=900')
      elif path == '/api/navigation/offline':
        if not self.require_session():
          return
        try:
          result = offline_maps_owner().snapshot()
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Offline maps are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/navigation/status':
        if not self.require_session():
          return
        # A poll that already holds the drawn route sends its key and skips the route lines.
        try:
          route_key = parse_qs(urlsplit(self.path).query, max_num_fields=1).get('routeKey', [''])[0]
        except ValueError:
          route_key = ''
        try:
          result = navigation_owner().snapshot(route_key if re.fullmatch(r'[0-9a-f]{16}', route_key) else None)
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Navigation status is unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/software/status':
        identity = self.settings_session()
        if identity is None:
          if not self.require_session():
            return
        try:
          result = software_snapshot(include_history=parse_qs(urlsplit(self.path).query).get('history') != ['0'])
        except (SoftwareUnavailable, SoftwareOperationError, OSError):
          self.json(503, {'error': 'Software status is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, {**result, 'instance': INSTANCE})
          else:
            if self.require_session():
              self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/bluetooth/status':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = bluetooth_owner().snapshot(session=identity)
          result['claim'] = phone_claim_view()
          if result.get('pairing') is None and starport_setup_owner().enabled():
            delegated = self.starport_phone_window(identity, 'status')
            if delegated['handled'] and type(delegated.get('bluetooth')) is dict:
              result['pairing'] = delegated['bluetooth'].get('pairing')
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Bluetooth is unavailable', 'code': 'unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/starport/setup':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = starport_setup_owner().status(identity)
        except Exception:
          self.json(503, {'error': 'StarPort setup is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/spotify/setup':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = {**spotify_owner().status(identity), 'uploadAllowed': self.spotify_install_allowed()}
        except Exception:
          self.json(503, {'error': 'Spotify setup is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/starport/pairing/status':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        source = starport_source_registry().current(identity)
        try:
          result: dict = starport_client_owner().call('pairing_status', source=source) if source and configuration_allowed() else \
                   {'pairing': {'active': False, 'receiver': None, 'prompt': None, 'approved': False}}
        except (OSError, RuntimeError):
          self.json(503, {'error': 'StarPort pairing service is unavailable'})
        else:
          try:
            runtime = starport_client_owner().call('status')['status']
          except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            runtime = None
          result['selectedReceiver'] = None
          if runtime is not None and runtime.get('receiver_address'):
            try:
              address = normalized_address(runtime['receiver_address'])
              result['selectedReceiver'] = {'address': address, 'name': str(runtime.get('receiver_name') or address)[:80]}
            except ValueError:
              pass
          result['runtime'] = runtime
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/starport/receivers':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          runtime = starport_client_owner().call('status')['status']
          if runtime.get('pairing_ready'):
            raise ValueError('Finish or cancel pairing before choosing a car')
          if not starport_setup_owner().enabled():
            raise ValueError('Enable StarPort first')
          result = self.starport_command(identity, 'devices')
          devices = [{'address': normalized_address(item['address']), 'name': str(item['name'])[:80]}
                     for item in result['devices'] if item.get('paired') and item.get('starport')]
        except ValueError as error:
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError, KeyError, TypeError):
          self.json(503, {'error': 'Paired Android Auto cars are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, {'receivers': devices})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path.startswith('/api/starport/source/'):
        source_id = path.removeprefix('/api/starport/source/')
        # The daemon queries this short-lived opaque source over loopback.
        if getattr(self.server, 'remote_transport', False) or self.client_address[0] != '127.0.0.1' or \
           self.connection.getsockname()[0] != '127.0.0.1' or not self.direct_local():
          self.json(403, {'valid': False})
        elif starport_source_registry().valid(source_id):
          self.json(200, {'valid': True})
        else:
          self.json(410, {'valid': False})
      elif path == '/api/controllers/status':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          layout_owner().parked()
          result = controller_status(controllers_socket)
        except (ControllerUnavailable, ControllerInvalid, OSError):
          self.json(503, {'error': 'Controller buttons are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/toyota-tools':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = toyota_owner().page(*identity)
        except (OSError, RuntimeError, ValueError):
          self.json(503, {'error': 'Toyota settings are unavailable'})
        else:
          self.json(200, result) if self.settings_session() == identity else self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/vehicle-selection':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = vehicle_choices().page(*identity)
        except (OSError, RuntimeError, ValueError):
          if self.settings_session() == identity:
            self.json(503, {'error': 'Vehicle selection is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/drives/stats':
        if not self.require_session():
          return
        try:
          query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
          if query and set(query) != {'timezone'}:
            raise ValueError('Invalid timezone query')
          result = statistics_owner().snapshot(timezone=query['timezone'][0]) if query else statistics_owner().snapshot()
        except ValueError:
          if self.require_session():
            self.json(400, {'error': 'Invalid timezone'})
        except (OSError, RuntimeError):
          if self.require_session():
            self.json(503, {'error': 'Driving history is unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/drive-state/status':
        if self.require_session():
          result = drive_owner().snapshot()
          if self.require_session():
            self.json(200, result)
      elif path == '/api/device/state':
        if self.require_session():
          state = vehicle_display.sample()
          if self.require_session():
            self.json(200, state)
      elif path.startswith('/api/recordings/logs/'):
        if not self.require_session():
          return
        try:
          with recordings_lock:
            lease = recording_library.open_archive(unquote(path.removeprefix('/api/recordings/logs/')), permitted=self.authenticated)
        except (OSError, ValueError, RecordingMediaUnavailable, DriveHistoryUnavailable):
          self.json(404, {'error': 'Drive logs unavailable; download individual segment logs'})
        else:
          self.send_recording(lease, 'application/x-tar')
      elif path.startswith('/api/recordings/files/'):
        if not self.require_session():
          return
        name, _, filename = unquote(path.removeprefix('/api/recordings/files/')).partition('/')
        if filename not in {'rlog.zst', 'rlog.bz2', 'qlog.zst', 'qlog.bz2'}:
          self.json(400, {'error': 'Invalid log file'})
          return
        try:
          lease = recording_library.open_log(name, filename)
        except (OSError, ValueError, RecordingMediaUnavailable):
          self.json(404, {'error': 'Closed recording log unavailable'})
        else:
          self.send_recording(lease, 'application/octet-stream')
      elif path == '/api/recordings/detail':
        if not self.require_session():
          return
        try:
          query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
          if set(query) != {'routeId'} or len(query['routeId']) != 1:
            raise ValueError('Choose one route')
          detail = history.detail(query['routeId'][0])
          from openpilot.starpilot.galaxy.recording_media import MAX_SOURCE_BYTES
          detail['footageDownloads'] = {camera: detail['inventoryComplete'] and 0 < size <= MAX_SOURCE_BYTES
                                        for camera, size in detail['videoBytes'].items()}
          log_bytes = sum(sum(segment.get('logBytes', {}).values()) for segment in detail['segments'])
          detail['logArchiveAvailable'] = detail['inventoryComplete'] and 0 < log_bytes <= MAX_SOURCE_BYTES
          detail['segments'] = [{key: value for key, value in segment.items() if key != 'sourceFiles'}
                                for segment in detail['segments']]
        except (CatalogError, ValueError, DriveHistoryUnavailable) as error:
          if self.require_session():
            self.json(409, {'error': str(error) or 'Route inventory unavailable'})
        else:
          if self.require_session():
            self.json(200, {'schemaVersion': 1, 'source': 'local', 'route': detail})
      elif path == '/api/recordings/local':
        if not self.require_session():
          return
        try:
          with recordings_lock:
            result = recording_library.describe(history.snapshot())
            dongle, dates, device_ids = None, {}, {}
            try:
              from openpilot.common.params import Params
              from openpilot.starpilot.saved_source import read_saved
              from openpilot.starpilot.connect.provider import PROVIDERS, recording_device_id
              params = Params()
              device_ids = {name: recording_device_id(name, params) for name in PROVIDERS}
              raw, readable = read_saved(params, 'DongleId', 32)
              dongle = raw.decode('ascii', errors='replace') if readable and raw is not None else None
              dates = statistics_owner().recording_dates(route['routeId'] for route in result['routes'])
            except (OSError, RuntimeError, AttributeError, ValueError):
              pass  # Optional metadata must not hide usable local recordings.
            result = recording_details(result, dates, dongle, device_ids=device_ids)
        except DriveHistoryUnavailable:
          if self.require_session():
            self.json(503, {'error': 'Local recordings are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/recordings/segment-summary':
        if not self.require_session():
          return
        try:
          query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=2)
          if set(query) != {'segmentName'} or len(query['segmentName']) != 1:
            raise ValueError('Invalid segment selection')
          result = local_summary().snapshot(query['segmentName'][0], permitted=lambda: True)
        except ValueError:
          if self.require_session():
            self.json(400, {'error': 'Invalid segment selection'})
        except SegmentSummaryChanged:
          if self.require_session():
            self.json(409, {'error': 'Recording changed; refresh the inventory'})
        except SegmentSummaryUnavailable:
          if self.require_session():
            self.json(503, {'error': 'Recorded segment details are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/flm/training-results':
        if not self.require_session():
          return
        if urlsplit(self.path).query:
          self.json(400, {'error': 'Unexpected training-results query'})
          return
        result = training_results_source.snapshot()
        if self.require_session():
          self.json(200, result)
      elif path.startswith('/api/flm/'):
        if self.require_session():
          self.json(404, {'error': 'Not found'})
      elif path == '/api/sentry/notifications':
        if not self.require_session():
          return
        try:
          result = notification_owner.snapshot()
        except NotificationUnavailable:
          if self.require_session():
            self.json(503, {'error': 'Sentry notifications are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path.startswith('/api/sentry/image/'):
        if not self.require_session():
          return
        parts = path.removeprefix('/api/sentry/image/').split('/')
        if len(parts) != 2:
          self.json(400, {'error': 'Choose an event image'})
          return
        try:
          body = motion_events.image(parts[0], parts[1])
        except SentryEventsUnavailable:
          self.json(404, {'error': 'Event image unavailable'})
        else:
          if self.require_session():
            self.respond(200, body, 'image/jpeg')
      elif path == '/api/sentry/events':
        if not self.require_session():
          return
        try:
          with sentry_events_lock:
            result = motion_events.snapshot()
        except SentryEventsUnavailable:
          if self.require_session():
            self.json(503, {'error': 'Local motion events are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path in ('/api/maps/status', '/api/maps/catalog', '/api/maps/operation', '/api/maps/setup'):
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = map_source.snapshot() if path.endswith('/status') else operations.request(
            'catalog' if path.endswith('/catalog') else 'setup' if path.endswith('/setup') else 'status')
        except (MapUnavailable, MapOperationError) as error:
          if self.settings_session() != identity:
            self.json(401, {'error': 'Sign in to Galaxy'})
          elif isinstance(error, MapOperationError):
            self.json(error.status, {'error': 'Map management is unavailable', 'code': error.code})
          else:
            self.json(503, {'error': 'Map observation is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/sounds':
        if not self.require_session():
          return
        try:
          result = sound_owner().snapshot()
        except SoundDownloadError as error:
          self.json(error.status, {'error': str(error)})
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Sound packs are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path in ('/api/models/status', '/api/models/manager', '/api/models/laboratory'):
        if not self.require_session():
          return
        try:
          result = model_status() if path.endswith('/status') else (
            model_laboratory().snapshot() if path.endswith('/laboratory') else model_owner().snapshot())
        except (OSError, ValueError, RuntimeError):
          if self.require_session():
            self.json(503, {'error': 'Model status is unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/plots/live':
        if not self.require_session():
          return
        try:
          result = plot_status()
        except (OSError, ValueError, RuntimeError):
          if self.require_session():
            self.json(503, {'error': 'Live plots are unavailable'})
        else:
          if self.require_session():
            self.json(200, result)
      elif path == '/api/favorites/slots':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = favorites_owner().snapshot()
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Saved Favorites are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/starport/layout':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = projection_layout_owner().snapshot()
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'StarPort layout is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/ui/layout':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = layout_owner().snapshot()
          result['activeProfile'] = request_profile(layout_preview_socket)
          if result['activeProfile'] is None:
            from openpilot.common.hardware import HARDWARE
            result['activeProfile'] = {'mici': 'compact', 'tici': 'large', 'tizi': 'large'}.get(HARDWARE.get_device_type())
          result['supportedProfiles'] = [result['activeProfile']] if result['activeProfile'] is not None else []
          if not result['supportedProfiles']:
            result['editable'] = False
          try:
            projection_status = projection_layout_owner().snapshot()
            result['projectionAvailable'] = True
            result['projectionReason'] = projection_status.get('reason')
          except (OSError, ValueError, RuntimeError):
            result['projectionAvailable'] = False
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Saved layout is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/settings/backup':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          from openpilot.starpilot.galaxy.toggle_backup import ToggleBackup
          result = ToggleBackup(feature_settings(), layout_owner()).export()
        except (SettingsUnavailable, SettingsChanged, OSError, RuntimeError, ValueError):
          self.json(503, {'error': 'Toggle backup is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/settings/non-defaults':
        non_default_settings(self, settings_recovery)
      elif path.startswith('/api/settings/pages/'):
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          result = feature_settings().page(unquote(path.removeprefix('/api/settings/pages/')), *identity)
        except SettingsUnavailable:
          self.json(404, {'error': 'Settings page unavailable'})
        except SettingsChanged:
          if self.settings_session() == identity:
            self.json(409, {'error': 'Saved settings changed; refresh the page'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except (OSError, ValueError, RuntimeError):
          if self.require_session():
            self.json(503, {'error': 'Saved settings are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path.startswith('/api/'):
        if self.require_session():
          self.json(404, {'error': 'Capability unavailable'})
      else:
        try:
          target = (WEB / unquote(path).lstrip('/')).resolve()
          if path == '/':
            target = WEB / 'index.html'
          if not target.is_relative_to(WEB) or not target.is_file():
            self.json(404, {'error': 'Page unavailable'})
            return
          stat = target.stat()
          body, packed, content_type = static_asset(str(target), (stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except (OSError, ValueError):
          self.json(404, {'error': 'Page unavailable'})
          return
        encodings = [entry.strip() for entry in self.headers.get('Accept-Encoding', '').lower().split(',')]
        compressed = 'gzip' in encodings and packed is not body
        payload = packed if compressed else body
        etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
        headers = {'ETag': etag, 'Vary': 'Accept-Encoding'}
        if compressed:
          headers['Content-Encoding'] = 'gzip'
        unchanged = self.headers.get('If-None-Match') == etag
        self.respond(304 if unchanged else 200, b'' if unchanged else payload, content_type, asset_headers=headers)

    def do_HEAD(self):
      self.do_GET()

    def do_POST(self):
      if not self.local_request(mutation=True):
        return
      path = urlsplit(self.path).path
      if path.startswith(('/api/replay/', '/api/recordings/')) and not self.direct_local():
        self.json(403, {'error': 'Routes require a local Galaxy connection', 'code': 'local_only'})
        return
      if path == '/api/starport/upload':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        if self.headers.get('Transfer-Encoding') is not None or \
           self.headers.get('Content-Type', '').lower() != 'application/octet-stream':
          self.json(415, {'error': 'Binary APK/XAPK upload required'})
          return
        try:
          size = int(self.headers.get('Content-Length', ''))
        except ValueError:
          size = -1
        from openpilot.starpilot.system.starport.apk_identity import MAX_FILE_BYTES
        if not 0 < size <= MAX_FILE_BYTES:
          self.json(413, {'error': 'StarPort package size is invalid'})
          return
        from openpilot.starpilot.galaxy.starport_setup import SetupRejected
        old_timeout = self.connection.gettimeout()
        try:
          self.connection.settimeout(30.0)
          result = starport_setup_owner().upload(identity, self.rfile, size)
        except SetupRejected as error:
          self.json(409, {'error': str(error)})
        except (OSError, TimeoutError):
          self.json(503, {'error': 'Android Auto upload failed'})
        else:
          if self.settings_session() == identity:
            self.json(202, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        finally:
          self.connection.settimeout(old_timeout)
        return
      if path == '/api/spotify/upload':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        if not self.spotify_install_allowed():
          self.json(403, {'error': 'Install Spotify over your local network or the Galaxy tunnel'})
          return
        if self.headers.get('Transfer-Encoding') is not None or \
           self.headers.get('Content-Type', '').lower() != 'application/octet-stream':
          self.json(415, {'error': 'Binary .tar.gz upload required'})
          return
        try:
          size = int(self.headers.get('Content-Length', ''))
        except ValueError:
          size = -1
        from openpilot.starpilot.soloist.package import MAX_UPLOAD_BYTES
        if not 0 < size <= MAX_UPLOAD_BYTES:
          self.json(413, {'error': 'Soloist download size is invalid'})
          return
        from openpilot.starpilot.galaxy.spotify_setup import SetupRejected as SpotifyRejected
        old_timeout = self.connection.gettimeout()
        try:
          self.connection.settimeout(30.0)
          result = spotify_owner().upload(identity, self.rfile, size)
        except SpotifyRejected as error:
          self.json(409, {'error': str(error)})
        except (OSError, TimeoutError):
          self.json(503, {'error': 'Soloist upload failed'})
        else:
          if self.settings_session() == identity:
            self.json(202, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        finally:
          self.connection.settimeout(old_timeout)
        return
      if path.startswith('/api/flm/'):
        self.close_connection = True
        if self.require_session():
          self.json(404, {'error': 'Not found'})
        return
      if path not in ('/api/companion/uplink', '/api/companion/settings', '/api/companion/location',
                      '/api/connect/proxy', '/api/connect/provider', '/api/auth/login', '/api/auth/logout', '/api/settings/preview', '/api/settings/confirm',
                      '/api/settings/recovery/preview', '/api/settings/recovery/confirm',
                      '/api/settings/reset-default', '/api/settings/restore', '/api/recordings/action',
                      '/api/replay/start', '/api/replay/control', '/api/replay/stop',
                      '/api/replay/export-start',
                      '/api/galaxy/pair', '/api/galaxy/unpair', '/api/galaxy/device-name', '/api/galaxy/hotspot', '/api/cameras/snapshot',
                      '/api/starport/layout', '/api/starport/enable', '/api/starport/control', '/api/starport/pairing',
                      '/api/starport/logs/send',
                      '/api/starport/pairing/response', '/api/starport/pairing/cancel', '/api/starport/pairing/select',
                      '/api/spotify/enable', '/api/spotify/key', '/api/spotify/preferences', '/api/spotify/control', '/api/spotify/maintenance',
                      '/api/ui/layout', '/api/ui/layout/preview', '/api/favorites/slots',
                      '/api/maps/start', '/api/maps/cancel',
                        '/api/bluetooth/action', '/api/controllers/action',
                      '/api/models/active', '/api/models/preferences', '/api/models/download', '/api/models/download_all',
                      '/api/models/cancel', '/api/models/delete', '/api/models/refresh_manifest', '/api/models/jetlink',
                      '/api/models/laboratory', '/api/models/laboratory/download', '/api/models/laboratory/delete',
                      '/api/sounds/download', '/api/sounds/cancel', '/api/software/action', '/api/drives/ignore',
                      '/api/recordings/delete-videos', '/api/sentry/notifications', '/api/sentry/events/delete', '/api/crash-reports/delete',
                      '/api/navigation/search', '/api/navigation/action', '/api/navigation/offline', '/api/drive-state/action',
                      '/api/toyota-tools/action', '/api/vehicle-selection/preview', '/api/vehicle-selection/confirm'):
        self.json(405, {'error': 'Method unavailable'})
        return
      if (path.startswith(('/api/companion/', '/api/replay/', '/api/recordings/', '/api/connect/', '/api/cameras/', '/api/settings/', '/api/maps/',
                           '/api/bluetooth/',
                           '/api/starport/', '/api/spotify/', '/api/controllers/', '/api/vehicle-selection/', '/api/toyota-tools/',
                           '/api/models/', '/api/ui/', '/api/favorites/', '/api/sounds/', '/api/software/',
                           '/api/drives/', '/api/navigation/', '/api/drive-state/', '/api/sentry/', '/api/crash-reports/')) and
          not self.require_session()):
        return
      if self.headers.get('Transfer-Encoding') is not None or \
         self.headers.get('Content-Type', '').lower() != 'application/json':
        self.json(415, {'error': 'JSON required'})
        return
      try:
        size = int(self.headers.get('Content-Length', ''))
      except ValueError:
        size = -1
      max_size = 256 * 1024 if path == '/api/settings/restore' else 17408 if path in ('/api/ui/layout', '/api/ui/layout/preview') else 4096
      if not 0 < size <= max_size:
        self.json(413, {'error': 'Invalid request size'})
        return
      try:
        def unique_object(pairs):
          result = {}
          for name, value in pairs:
            if name in result:
              raise ValueError('Duplicate request field')
            result[name] = value
          return result

        payload = json.loads(self.rfile.read(size), object_pairs_hook=unique_object)
      except (ValueError, UnicodeError, RecursionError):
        self.json(400, {'error': 'Invalid request'})
        return
      if path.startswith('/api/replay/'):
        replay_api.post(self, path, payload)
        return
      if path == '/api/companion/location':
        if getattr(self.server, 'remote_transport', False) or not self.direct_local():
          self.json(403, {'error': 'Phone location requires a direct local connection'})
          return
        try:
          self.json(200, phone_location_source.publish(payload))
        except ValueError as error:
          self.json(400, {'error': str(error)})
        return
      if path in ('/api/companion/uplink', '/api/companion/settings'):
        if getattr(self.server, 'remote_transport', False) or not self.direct_local():
          self.json(403, {'error': 'Companion controls require a direct local connection'})
          return
        try:
          result = (companion_source.heartbeat(payload, self.client_address[0]) if path.endswith('/uplink')
                    else companion_source.save(payload))
        except PermissionError as error:
          self.json(403, {'error': str(error)})
        except FileExistsError as error:
          self.json(409, {'error': str(error)})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except OSError:
          self.json(503, {'error': 'Companion settings unavailable'})
        else:
          self.json(200, result)
        return
      if path == '/api/galaxy/hotspot':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            result = hotspot_owner().save(payload, lambda: self.settings_session() == identity and parked())
        except PermissionError:
          self.json(409, {'error': 'Park before changing the Galaxy hotspot.'})
        except FileExistsError as error:
          self.json(409, {'error': str(error)})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except OSError:
          self.json(503, {'error': 'Hotspot settings could not be saved.'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/recordings/action':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with recordings_lock:
            result = recording_library.action(payload, permitted=lambda: self.settings_session() == identity and parked(),
                                              before_delete=lambda route_id: replay_api.clear_route(self, identity, route_id))
        except ReplayHTTPError as error:
          self.json(error.status, {'error': error.message})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except PermissionError as error:
          self.json(409, {'error': str(error)})
        except (OSError, DriveHistoryUnavailable):
          self.json(503, {'error': 'Recording action could not be completed'})
        else:
          if self.require_session():
            self.json(200, result)
        return
      if path == '/api/sentry/notifications':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if payload.get('action') == 'simulate':
            with sentry_events_lock:
              if set(payload) != {'action'}:
                raise ValueError('Invalid simulation request')
              if not parked():
                self.json(403, {'error': 'Park the car before simulating an event'})
                return
              if clock() - last_simulation[0] < 10:
                self.json(429, {'error': 'Wait ten seconds before simulating another event'})
                return
              notification_owner.store.record('warning', time.monotonic_ns(), simulated=True,
                                               permitted=lambda: self.settings_session() == identity and parked())
              last_simulation[0] = clock()
              result = notification_owner.snapshot()
          else:
            result = notification_owner.action(payload, permitted=lambda: self.settings_session() == identity)
        except LookupError:
          self.json(404, {'error': 'Phone subscription not found'})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except PermissionError:
          self.json(401, {'error': 'Sign in to Galaxy'})
        except (NotificationUnavailable, OSError):
          self.json(503, {'error': 'Sentry notifications are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/connect/proxy':
        identity = self.settings_session()
        try:
          def authorized():
            return (identity is not None and self.settings_session() == identity and cloud_allowed() and
                    (not payload.get('enabled') or access.status().status == AccessStatus.CONFIGURED_LOCAL))
          result = proxy_settings.save(payload, authorized)
        except PermissionError as error:
          self.json(403, {'error': str(error)})
        except (ValueError, TypeError) as error:
          self.json(409, {'error': str(error)})
        except OSError:
          self.json(503, {'error': 'Reverse proxy settings could not be saved'})
        else:
          self.json(200, {**result, 'canEdit': cloud_allowed(),
                          'passwordConfigured': access.status().status == AccessStatus.CONFIGURED_LOCAL})
        return
      if path == '/api/connect/provider':
        identity = self.settings_session()
        try:
          if (type(payload) is not dict or set(payload) != {'provider', 'revision', 'confirmed'} or
              type(payload['provider']) is not str or type(payload['revision']) is not str or payload['confirmed'] is not True):
            raise ValueError('Confirm the next-boot cloud provider')
          with effect_lock:
            def authorized():
              return identity is not None and self.settings_session() == identity and cloud_allowed()
            if not authorized():
              self.json(403, {'error': 'Turn off the vehicle before changing cloud providers'})
              return
            result = cloud_select(payload['provider'], payload['revision'], authorized)
        except ValueError as error:
          self.json(409, {'error': str(error)})
        except (OSError, TypeError):
          self.json(503, {'error': 'The cloud provider selection could not be saved'})
        else:
          if self.settings_session() == identity:
            self.json(200, {**result, 'canSelect': cloud_allowed()})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/navigation/offline':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            result = offline_maps_owner().action(payload)
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except OSError:
          self.json(503, {'error': 'Offline maps could not be saved. Try again shortly.'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path in ('/api/navigation/search', '/api/navigation/action'):
        from openpilot.starpilot.navigation.owner import ConflictError, ValidationError
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if type(payload) is not dict:
            raise ValidationError('Invalid navigation request')
          if path == '/api/navigation/search':
            if set(payload) not in ({'query'}, {'query', 'searchId', 'clientId'}, {'query', 'searchId', 'clientId', 'autocomplete'}):
              raise ValidationError('Enter a destination to search')
            if 'autocomplete' in payload and payload['autocomplete'] is not True:
              raise ValidationError('Enter a destination to search')
            result = {'results': (navigation_owner().search_places(payload['query'], identity, payload['searchId'], payload['clientId'],
                                                                   autocomplete='autocomplete' in payload)
                                  if 'searchId' in payload else navigation_owner().search(payload['query']))}
          else:
            fields = {'configure': {'patch'}, 'select': {'destination'}, 'selectPlace': {'id', 'searchId'},
                      'cancelSearch': {'searchId'}, 'clear': set(), 'favorite': {'destination'},
                      'favoritePlace': {'id', 'searchId'}, 'removeFavorite': {'id'}, 'labelFavorite': {'id', 'label'},
                      'removeRecent': {'id'}, 'clearRecents': set(), 'selectRoute': {'index'}}
            action = payload.get('action')
            optional = {'label'} if action in ('favorite', 'favoritePlace') and 'label' in payload else set()
            if type(action) is not str or action not in fields or set(payload) != {'action', 'revision'} | fields[action] | optional:
              raise ValidationError('Invalid navigation action')
            needs_park = action == 'configure' and isinstance(payload['patch'], dict) and 'token' in payload['patch']
            def authorized():
              return self.settings_session() == identity and (not needs_park or configuration_allowed())
            with (nullcontext() if action in ('selectPlace', 'favoritePlace') else effect_lock):
              if not authorized():
                raise PermissionError('Park your car before changing the Mapbox key' if needs_park else 'Sign in to Galaxy')
              nav = navigation_owner()
              keywords = {'expected_revision': payload['revision'], 'authorized': authorized}
              if action == 'configure':
                result = nav.configure(payload['patch'], **keywords)
              elif action == 'selectPlace':
                result = nav.select_place(payload['id'], payload['searchId'], identity, **keywords)
              elif action == 'cancelSearch':
                nav.cancel_search(identity, payload['searchId'])
                result = nav.snapshot()
              elif action == 'select':
                result = nav.select(payload['destination'], **keywords)
              elif action == 'clear':
                result = nav.clear(**keywords)
              elif action == 'selectRoute':
                result = nav.select_route(payload['index'], **keywords)
              elif action == 'favorite':
                result = nav.favorite(payload['destination'], **keywords, label=payload.get('label'))
              elif action == 'favoritePlace':
                result = nav.favorite_place(payload['id'], payload['searchId'], identity, **keywords, label=payload.get('label'))
              elif action == 'clearRecents':
                result = nav.clear_recents(**keywords)
              else:
                if type(payload['id']) is not str or not 1 <= len(payload['id']) <= 256:
                  raise ValidationError('Choose a saved place')
                if action == 'removeRecent':
                  result = nav.remove_recent(payload['id'], **keywords)
                elif action == 'labelFavorite':
                  result = nav.label_favorite(payload['id'], payload['label'], **keywords)
                else:
                  result = nav.remove_favorite(payload['id'], **keywords)
        except ConflictError as error:
          self.json(409, {'error': str(error)})
        except ValidationError as error:
          self.json(400, {'error': str(error)})
        except PermissionError as error:
          if self.require_session():
            self.json(409, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Navigation is unavailable. Try again shortly.'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/cameras/snapshot':
        if type(payload) is not dict or set(payload) != {'camera'} or type(payload['camera']) is not str:
          self.json(400, {'error': 'Choose a camera'})
          return
        identity = self.settings_session()
        def permitted():
          readable, _, _ = select.select([self.connection], [], [], 0)
          if readable and not self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT):
            return False
          return identity is not None and self.settings_session() == identity and parked()
        try:
          body = snapshots.capture(payload['camera'], permitted=permitted)
          if not permitted():
            raise SnapshotDenied
        except ValueError:
          self.json(400, {'error': 'Choose a supported camera'})
        except SnapshotDenied:
          if self.require_session():
            self.json(409, {'error': 'A fresh parked vehicle connection is required'})
        except SnapshotUnavailable:
          if self.require_session():
            self.json(503, {'error': 'No fresh camera frame. Try taking a new snapshot.'})
        else:
          self.respond(200, body, 'image/jpeg')
        return
      if path == '/api/galaxy/device-name':
        if not self.require_session():
          return
        identity = self.settings_session()
        try:
          if type(payload) is not dict or set(payload) != {'name'}:
            raise ValueError('Invalid comma name')
          with effect_lock:
            if identity is None or self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            result = {'name': device_name.save(payload['name'])}
        except (ValueError, UnicodeError):
          self.json(400, {'error': 'Use a name of up to 40 characters without control characters'})
        except OSError:
          self.json(503, {'error': 'The comma name could not be saved'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path in ('/api/galaxy/pair', '/api/galaxy/unpair'):
        if getattr(self.server, 'remote_transport', False) or not self.direct_local():
          self.json(403, {'error': 'Pair from the local device network'})
          return
        if not self.require_session():
          return
        if not parked():
          self.json(409, {'error': 'Turn off the vehicle before changing Galaxy pairing'})
          return
        with effect_lock:
          if path == '/api/galaxy/pair':
            if pairing.read() is not None:
              self.json(409, {'error': 'Galaxy is already paired'})
              return
            raw_password = payload.get('password') if type(payload) is dict and set(payload) == {'password'} else None
            password = raw_password.strip() if type(raw_password) is str else None
            if password is None or not 6 <= len(password) <= 255:
              self.json(400, {'error': 'Password must be at least 6 characters'})
              return
            status = access.status().status
            try:
              previous_access = access.snapshot_for_pairing()
            except (OSError, ValueError, UnicodeError):
              self.json(503, {'error': 'Remote password storage is unavailable'})
              return
            legacy_root = access.legacy_root if status == AccessStatus.LEGACY_IMPORT_AVAILABLE else None
            if status == AccessStatus.UNCONFIGURED:
              if len(password) < 8:
                self.json(400, {'error': 'New Galaxy passwords must be at least 8 characters'})
                return
              if not access.configure(password, parked):
                self.json(503, {'error': 'Could not configure remote password'})
                return
            elif status == AccessStatus.LEGACY_IMPORT_AVAILABLE:
              if not access.import_legacy(password, parked):
                self.json(403, {'error': 'Galaxy password does not match existing pairing'})
                return
            elif status == AccessStatus.CONFIGURED_LOCAL:
              if access.legacy_available() is None:
                self.json(503, {'error': 'Existing Galaxy pairing storage is unavailable'})
                return
              if access.import_legacy(password, parked):
                legacy_root = access.legacy_root
              else:
                if len(password) < 8:
                  self.json(400, {'error': 'New Galaxy passwords must be at least 8 characters'})
                  return
                if not access.replace_for_pairing(password, parked):
                  self.json(503, {'error': 'Could not configure remote password'})
                  return
            else:
              self.json(503, {'error': 'Remote password storage is unavailable'})
              return
            if not parked():
              generation = access.current_generation()
              restored = generation is not None and access.restore_failed_pairing(previous_access, generation)
              self.json(409 if restored else 503, {'error': 'Turn off the vehicle before changing Galaxy pairing' if restored else
                                                    'Galaxy pairing stopped; remote password storage needs review'})
              return
            try:
              slug = pairing.pair(hashlib.sha256(password.encode()).hexdigest(), legacy_root=legacy_root)
            except (OSError, ValueError):
              slug = None
            if slug is None:
              generation = access.current_generation()
              restored = generation is not None and access.restore_failed_pairing(previous_access, generation)
              self.json(409 if restored else 503, {'error': 'Galaxy pairing storage is unavailable' if restored else
                                                    'Galaxy pairing could not be saved; remote password storage needs review'})
              return
            self.json(200, {'paired': True, 'url': pairing.url(slug)})
          else:
            if payload != {}:
              self.json(400, {'error': 'Invalid request'})
              return
            if not parked():
              self.json(409, {'error': 'Turn off the vehicle before changing Galaxy pairing'})
              return
            if not pairing.unpair():
              self.json(409, {'error': 'Galaxy is not paired'})
              return
            self.json(200, {'paired': False})
        return
      if path == '/api/drives/ignore':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if (type(payload) is not dict or set(payload) != {'routeId', 'ignored'} or
              type(payload['routeId']) is not str or len(payload['routeId']) > 180 or type(payload['ignored']) is not bool):
            raise ValueError('Invalid drive selection')
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            result = statistics_owner().ignore(payload['routeId'], payload['ignored'],
                                                authorized=lambda: self.settings_session() == identity)
        except PermissionError:
          self.json(401, {'error': 'Sign in to Galaxy'})
        except ValueError:
          if self.settings_session() == identity:
            self.json(400, {'error': 'Invalid drive selection'})
        except (OSError, RuntimeError):
          if self.settings_session() == identity:
            self.json(503, {'error': 'Driving history could not be saved'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path in ('/api/sentry/events/delete', '/api/crash-reports/delete'):
        delete_records(self, path, payload, sentry_events_lock, crash_reports_lock, notification_owner, motion_events, reports)
      elif path == '/api/recordings/delete-videos':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if type(payload) is not dict or set(payload) != {'segmentName'} or type(payload['segmentName']) is not str:
            raise ValueError('Invalid segment selection')
          with recordings_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            if not parked():
              self.json(409, {'error': 'Turn off the vehicle before managing recordings'})
              return
            replay_api.clear_route(self, identity, payload['segmentName'].rsplit('--', 1)[0].replace('_', '|'))
            result = history.delete_videos(payload['segmentName'])
        except ReplayHTTPError as error:
          self.json(error.status, {'error': error.message})
        except ValueError:
          self.json(400, {'error': 'Invalid segment selection'})
        except SegmentMissing:
          self.json(404, {'error': 'Recording no longer exists; refresh the inventory'})
        except SegmentInUse:
          self.json(409, {'error': 'This segment is still recording'})
        except (OSError, AttributeError):
          self.json(503, {'error': 'Videos could not be deleted'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
      elif path == '/api/software/action':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if type(payload) is not dict or type(payload.get('action')) is not str:
            raise ValueError('Invalid software request')
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            result = software_owner().action(payload['action'], payload,
                                              authorized=lambda: self.settings_session() == identity)
          result = {**(software_source if software_source is not None else SoftwareStatus()).snapshot(), 'operations': result}
        except SoftwareOperationError as error:
          if self.settings_session() == identity:
            self.json(error.status, {'error': str(error)})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except (SoftwareUnavailable, OSError, RuntimeError):
          self.json(503, {'error': 'Software updates are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/favorites/slots':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            result = favorites_owner().save(payload, session_valid=lambda: self.settings_session() == identity)
        except FavoritesChanged as error:
          self.json(409, {'error': str(error)})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Saved Favorites are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/starport/layout':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            result = projection_layout_owner().save(payload, session_valid=lambda: self.settings_session() == identity)
            if result['document']['widgets'].get('nav_map', {}).get('enabled') is True:
              try:
                result['mapDisabledElsewhere'] = bool(layout_owner().disable_map(lambda: self.settings_session() == identity))
              except (OSError, RuntimeError, ValueError, LayoutChanged):
                result['mapDisabledElsewhere'] = False
        except LayoutChanged as error:
          self.json(409, {'error': str(error)})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'StarPort layout could not be saved'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/ui/layout':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            result = layout_owner().save(payload, session_valid=lambda: self.settings_session() == identity)
            if result['document']['layouts']['large'].get('nav_map', {}).get('enabled') is True:
              try:
                result['mapDisabledElsewhere'] = bool(projection_layout_owner().disable_map(session_valid=lambda: self.settings_session() == identity))
              except (OSError, RuntimeError, ValueError, LayoutChanged):
                result['mapDisabledElsewhere'] = False
        except LayoutChanged as error:
          self.json(409, {'error': str(error)})
        except ValueError as error:
          self.json(400, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Saved layout is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/ui/layout/preview':
        preview_layout(self, payload, projection_layout_owner, layout_owner, layout_preview_socket)
        return
      if path == '/api/drive-state/action':
        change_drive_state(self, payload, effect_lock, drive_owner)
        return
      if path.startswith('/api/sounds/'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            result = sound_owner().action(path.rsplit('/', 1)[-1], payload)
        except SoundDownloadError as error:
          self.json(error.status, {'error': str(error)})
        except (OSError, ValueError, RuntimeError):
          self.json(503, {'error': 'Sound packs are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path.startswith('/api/models/'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            if path.startswith('/api/models/laboratory'):
              action = 'configure' if path.endswith('/laboratory') else path.rsplit('/', 1)[-1]
              result = model_laboratory().action(action, payload)
            else:
              result = model_owner().action(path.rsplit('/', 1)[-1], payload)
        except ValueError as error:
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Model management is unavailable'})
        else:
          self.json(200, result)
        return
      if path in ('/api/auth/login', '/api/auth/logout') and getattr(self.server, 'remote_transport', False):
        self.json(409, {'error': 'Sign in through Galaxy', 'code': 'gateway_auth_required'})
        return
      if path == '/api/auth/logout':
        if payload != {}:
          self.json(400, {'error': 'Invalid request'})
          return
        with effect_lock:
          identity = self.settings_session()
          with session_lock:
            token = self.session_token()
            sessions.revoke(token)
          if identity is not None and bluetooth_source is not None:
            bluetooth_source.cancel_session(identity)
          if identity is not None and starport_registry_source is not None:
            starport_registry_source.revoke(identity)
        self.respond(200, b'{"authenticated":false}', cookie='galaxy_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        return
      if path.startswith('/api/spotify/'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        from openpilot.starpilot.galaxy.spotify_setup import SetupRejected as SpotifyRejected
        try:
          owner = spotify_owner()
          if path == '/api/spotify/enable' and type(payload) is dict and set(payload) == {'enabled'}:
            result = owner.enable(identity, payload['enabled'])
          elif path == '/api/spotify/key' and type(payload) is dict and set(payload) == {'key'}:
            result = owner.set_key(identity, payload['key'])
          elif path == '/api/spotify/preferences' and type(payload) is dict:
            result = owner.save_preferences(identity, payload)
          elif path == '/api/spotify/control' and type(payload) is dict and set(payload) in ({'action'}, {'action', 'args'}):
            result = owner.control(identity, payload['action'], payload.get('args', {}))
          elif path == '/api/spotify/maintenance' and type(payload) is dict and set(payload) == {'action'}:
            result = owner.maintenance(identity, payload['action'])
          else:
            self.json(400, {'error': 'Invalid Spotify request'})
            return
        except SpotifyRejected as error:
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Spotify is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/starport/logs/send':
        if type(payload) is not dict or set(payload) != {'message'}:
          self.json(400, {'error': 'Invalid StarPort log send request'})
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        from openpilot.starpilot.galaxy.starport_send import SendBusy, SendRejected
        try:
          result = starport_log_sender().start(payload['message'])
        except SendRejected as error:
          self.json(400, {'error': str(error)})
        except SendBusy as error:
          self.json(409, {'error': str(error)})
        else:
          if self.settings_session() == identity:
            self.json(202, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/starport/enable':
        if type(payload) is not dict or set(payload) not in ({'enabled'}, {'enabled', 'enableBluetooth'}) or \
           type(payload['enabled']) is not bool or type(payload.get('enableBluetooth', False)) is not bool or \
           payload.get('enableBluetooth', False) and not payload['enabled']:
          self.json(400, {'error': 'Invalid Android Auto enable request'})
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        from openpilot.starpilot.galaxy.starport_setup import SetupRejected
        try:
          result = starport_setup_owner().enable(identity, payload['enabled'], enable_bluetooth=payload.get('enableBluetooth', False))
        except SetupRejected as error:
          self.json(409, {'error': str(error)})
        except (BluetoothRejected, BluetoothUnavailable) as error:
          self.json(409 if isinstance(error, BluetoothRejected) else 503,
                    {'error': 'Bluetooth could not be enabled; StarPort was not turned on', 'code': error.code})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Android Auto setting is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/starport/control':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        if type(payload) is not dict or not isinstance(payload.get('action'), str):
          self.json(400, {'error': 'Invalid Android Auto control request'})
          return
        action = payload['action']
        if action == 'stop' and set(payload) == {'action'}:
          try:
            self.starport_command(identity, 'stop')
            current = starport_client_owner().call('status').get('status', {})
          except ValueError as error:
            self.json(409, {'error': str(error)})
          except (OSError, RuntimeError):
            self.json(503, {'error': 'Android Auto service is unavailable'})
          else:
            if self.settings_session() == identity:
              self.json(200, {'status': current})
            else:
              self.json(401, {'error': 'Sign in to Galaxy'})
          return
        if not starport_setup_owner().enabled():
          self.json(409, {'error': 'Enable StarPort first'})
          return
        if action == 'start' and set(payload) == {'action'}:
          command, arguments = 'start', {}
        elif action == 'auto_connect' and set(payload) == {'action', 'enabled'} and type(payload['enabled']) is bool:
          if not configuration_allowed():
            self.json(403, {'error': 'Park before changing automatic connection'})
            return
          command, arguments = 'set_auto_connect', {'enabled': payload['enabled']}
        elif (action == 'connection' and set(payload) == {'action', 'connection'} and
              payload['connection'] in ('wireless', 'wired', 'headunitpad', 'openheadunit')):
          if not configuration_allowed():
            self.json(403, {'error': 'Park before changing the connection type'})
            return
          command, arguments = 'set_connection', {'connection': payload['connection']}
        elif (action == 'car_type' and set(payload) == {'action', 'carType'} and
              payload['carType'] in ('builtin', 'adapter', 'usb', 'app')):
          if not configuration_allowed():
            self.json(403, {'error': 'Park before changing the setup type'})
            return
          command, arguments = 'set_car_type', {'car_type': payload['carType']}
        elif action == 'car_profile' and set(payload) == {'action', 'profile'} and isinstance(payload['profile'], str):
          from openpilot.starpilot.system.starport.car_profiles import valid_override
          if not valid_override(payload['profile']):
            self.json(400, {'error': 'Invalid car profile'})
            return
          if not configuration_allowed():
            self.json(403, {'error': 'Park before changing car settings'})
            return
          command, arguments = 'set_car_profile', {'profile': payload['profile']}
        elif action == 'select_receiver' and set(payload) == {'action', 'address'} and isinstance(payload['address'], str):
          if not configuration_allowed():
            self.json(403, {'error': 'Park before changing the car'})
            return
          try:
            address = normalized_address(payload['address'])
          except ValueError:
            self.json(400, {'error': 'Invalid car address'})
            return
          command, arguments = 'select_receiver', {'address': address}
        else:
          self.json(400, {'error': 'Invalid Android Auto control request'})
          return
        try:
          runtime = starport_client_owner().call('status')['status']
          if runtime.get('pairing_ready'):
            raise ValueError('Finish or cancel pairing first')
          if command == 'start' and not runtime.get('receiver_address') and runtime.get('connection') == 'wireless':
            raise ValueError('Select a car first')
          if command == 'start' and not starport_setup_owner().identity_status().get('installed'):
            raise ValueError('Verify your Android Auto package first')
          self.starport_command(identity, command, **arguments)
          current = starport_client_owner().call('status')['status']
        except ValueError as error:
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError, KeyError, TypeError):
          self.json(503, {'error': 'Android Auto control is unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, {'status': current})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/starport/pairing':
        if payload != {}:
          self.json(400, {'error': 'Invalid Android Auto pairing request'})
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        if not configuration_allowed():
          self.json(403, {'error': 'Park before pairing the car'})
          return
        if not starport_setup_owner().enabled():
          self.json(403, {'error': 'Enable StarPort first'})
          return
        try:
          setup_state = starport_setup_owner().status(identity)
        except (OSError, RuntimeError):
          self.json(503, {'error': 'StarPort setup is unavailable'})
          return
        if not setup_state['installReady'] or not setup_state['serviceReady']:
          self.json(503, {'error': 'Android Auto display service is unavailable'})
          return
        if not setup_state['bluetoothEnabled']:
          self.json(409, {'error': 'Turn on Bluetooth before pairing the car'})
          return
        # Bluetooth pairing and the Wi-Fi bootstrap need no identity; only projection does.
        source = None
        try:
          source = starport_source_registry().mint(identity)
          if self.settings_session() != identity or not configuration_allowed():
            raise ValueError('Galaxy session or Park state changed')
          starport_client_owner().call('prepare_pairing', source=source)
        except ValueError as error:
          if source is not None:
            starport_source_registry().revoke_source(source)
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError):
          if source is not None:
            starport_source_registry().revoke_source(source)
          self.json(503, {'error': 'StarPort pairing service is unavailable'})
        else:
          if self.settings_session() == identity and configuration_allowed():
            self.json(200, {'pairing': True, 'seconds': 180})
          else:
            starport_source_registry().revoke_source(source)
            self.json(409, {'error': 'Galaxy session or Park state changed'})
        return
      if path in ('/api/starport/pairing/response', '/api/starport/pairing/cancel', '/api/starport/pairing/select'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        if not configuration_allowed():
          self.json(403, {'error': 'Park before pairing the car'})
          return
        source = starport_source_registry().current(identity)
        if source is None:
          self.json(409, {'error': 'Pairing window expired'})
          return
        if path.endswith('/cancel'):
          if payload != {}:
            self.json(400, {'error': 'Invalid pairing cancellation'})
            return
          command, arguments = 'cancel_pairing', {}
        elif path.endswith('/select'):
          try:
            if set(payload) != {'address'}:
              raise ValueError('Invalid car selection')
            address = normalized_address(payload['address'])
          except (TypeError, ValueError):
            self.json(400, {'error': 'Invalid car address'})
            return
          command, arguments = 'pair_device', {'address': address}
        else:
          if (set(payload) != {'prompt_id', 'accepted', 'value'} or
              not isinstance(payload['prompt_id'], str) or len(payload['prompt_id']) != 32 or
              type(payload['accepted']) is not bool or not isinstance(payload['value'], str) or len(payload['value']) > 16):
            self.json(400, {'error': 'Invalid pairing response'})
            return
          command, arguments = 'pairing_response', payload
        try:
          if self.settings_session() != identity or not configuration_allowed():
            raise ValueError('Galaxy session or Park state changed')
          starport_client_owner().call(command, source=source, **arguments)
        except ValueError as error:
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(409, {'error': 'Car connection could not start. Refresh the list and try again.' if command == 'pair_device' else
                                   'Pairing prompt expired or changed'})
        else:
          if command == 'cancel_pairing':
            starport_source_registry().revoke_source(source)
          self.json(200, {'ok': True})
        return
      if path == '/api/toyota-tools/action':
        from openpilot.starpilot.galaxy.toyota_tools import ToyotaToolsChanged
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            result = toyota_owner().action(payload, *identity, session_valid=lambda: self.settings_session() == identity)
        except ValueError:
          self.json(400, {'error': 'Invalid Toyota request'})
        except ToyotaToolsChanged:
          self.json(409, {'error': 'Toyota settings changed; refresh saved values'})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Toyota settings are unavailable'})
        else:
          self.json(200, result) if self.settings_session() == identity else self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path.startswith('/api/vehicle-selection/'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if path.endswith('/preview'):
            if type(payload) is not dict or set(payload) != {'view', 'platform'} or \
               type(payload['view']) is not str or not 1 <= len(payload['view']) <= 64 or \
               (payload['platform'] is not None and type(payload['platform']) is not str):
              raise ValueError('Invalid vehicle request')
            result = vehicle_choices().preview(payload['view'], payload['platform'], *identity)
          else:
            if type(payload) is not dict or set(payload) != {'intent', 'confirmed'} or \
               type(payload['intent']) is not str or not 1 <= len(payload['intent']) <= 64 or payload['confirmed'] is not True:
              raise ValueError('Invalid vehicle request')
            with effect_lock:
              if self.settings_session() != identity:
                raise VehicleSelectionChanged('Session changed')
              saved = vehicle_choices().confirm(payload['intent'], *identity,
                                                session_valid=lambda: self.settings_session() == identity)
            result = {'saved': saved}
        except ValueError:
          if self.settings_session() == identity:
            self.json(400, {'error': 'Invalid vehicle request'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except VehicleSelectionChanged:
          if self.settings_session() == identity:
            self.json(409, {'error': 'Vehicle selection changed; refresh the page'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except VehicleSelectionUnverified:
          if self.settings_session() == identity:
            self.json(409, {'error': 'Save could not be confirmed; refresh saved values', 'code': 'unverified'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except (OSError, RuntimeError):
          if self.settings_session() == identity:
            self.json(503, {'error': 'Vehicle selection is unavailable'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/bluetooth/action':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if type(payload) is not dict or set(payload) not in ({'operation'}, {'operation', 'address'}, {'operation', 'enabled'},
                                                               {'operation', 'promptId', 'accepted'},
                                                               {'operation', 'promptId', 'accepted', 'value'}) or \
             type(payload.get('operation')) is not str or payload['operation'] not in BluetoothOwner.OPERATIONS:
            raise ValueError('Invalid Bluetooth request')
          if payload['operation'] == 'power':
            if set(payload) != {'operation', 'enabled'} or type(payload['enabled']) is not bool:
              raise ValueError('Invalid Bluetooth request')
          elif payload['operation'] in ('scan', 'stop_scan', 'cancel_pair', 'accept_pair'):
            if set(payload) != {'operation'}:
              raise ValueError('Invalid Bluetooth request')
          elif payload['operation'] == 'pairing_response':
            if (set(payload) not in ({'operation', 'promptId', 'accepted'}, {'operation', 'promptId', 'accepted', 'value'}) or
                type(payload['accepted']) is not bool or type(payload['promptId']) is not str or
                not re.fullmatch('[0-9a-f]{32}', payload['promptId']) or
                type(payload.get('value', '')) is not str or len(payload.get('value', '')) > 16):
              raise ValueError('Invalid Bluetooth request')
          elif set(payload) != {'operation', 'address'}:
            raise ValueError('Invalid Bluetooth request')
          else:
            normalized_address(payload['address'])
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            delegated = {'handled': False}
            if payload['operation'] == 'accept_pair' and configuration_allowed():
              spotify_owner().prepare_phone_pairing()
            if payload['operation'] in ('connect', 'disconnect', 'forget') and starport_setup_owner().enabled():
              try:
                delegated = self.starport_command(identity, 'bluetooth_action', operation=payload['operation'],
                                            address=normalized_address(payload['address']))
              except (OSError, RuntimeError):
                # Without a running Android Auto service nothing else holds the adapter, so forgetting is local.
                if payload['operation'] != 'forget':
                  raise
                delegated = {'handled': False}
              if type(delegated) is not dict or type(delegated.get('handled')) is not bool:
                raise BluetoothUnavailable('service_unavailable')
              if 'error_code' in delegated:
                code = delegated['error_code']
                if type(code) is str and code in ('busy', 'park_required', 'changed', 'session_expired', 'projection_active'):
                  raise BluetoothRejected('Android Auto could not complete the Bluetooth change', code=code)
                unavailable = ('service_unavailable', 'adapter_unavailable', 'radio_unavailable', 'radio_preference_unavailable')
                raise BluetoothUnavailable('Android Auto Bluetooth service is unavailable',
                                           code=code if type(code) is str and code in unavailable else 'service_unavailable')
            elif payload['operation'] == 'power' and starport_setup_owner().enabled():
              # Android Auto may hold the adapter in its own process; it releases that hold before the radio stops.
              try:
                delegated = self.starport_command(identity, 'bluetooth_power', enabled=payload['enabled'])
              except (OSError, RuntimeError):
                delegated = {'handled': False}
              if type(delegated) is not dict or type(delegated.get('handled')) is not bool:
                delegated = {'handled': False}
              if 'error_code' in delegated:
                code = delegated['error_code']
                if type(code) is str and code in ('busy', 'park_required', 'changed', 'session_expired', 'projection_active'):
                  why = delegated.get('message')
                  raise BluetoothRejected(f'Android Auto: {why}' if type(why) is str and why else
                                          'Android Auto could not complete the Bluetooth change', code=code)
                raise BluetoothUnavailable('Android Auto Bluetooth service is unavailable')
            elif payload['operation'] in ('accept_pair', 'pair', 'scan', 'stop_scan', 'pairing_response', 'cancel_pair') and starport_setup_owner().enabled():
              delegated = self.starport_phone_window(identity, payload['operation'], payload)
              if 'error_code' in delegated:
                code = delegated['error_code']
                if type(code) is str and code in ('busy', 'park_required', 'changed', 'session_expired'):
                  why = delegated.get('message')
                  raise BluetoothRejected(f'Android Auto: {why}' if type(why) is str and why else
                                          'Android Auto could not complete the Bluetooth change', code=code)
                raise BluetoothUnavailable('Android Auto Bluetooth service is unavailable')
            if delegated['handled']:
              result = delegated.get('bluetooth')
              if type(result) is not dict:
                raise BluetoothUnavailable('service_unavailable')
            else:
              result = bluetooth_owner().request(payload['operation'], address=payload.get('address'), enabled=payload.get('enabled'),
                                                 session=identity, prompt_id=payload.get('promptId'),
                                                 accepted=payload.get('accepted'), value=payload.get('value', ''))
            if payload['operation'] in ('accept_pair', 'cancel_pair'):
              self.phone_claim(payload['operation'], result)
            elif payload['operation'] == 'forget':
              forget_app_keys(payload['address'])
        except ValueError:
          if self.settings_session() == identity:
            self.json(400, {'error': 'Invalid Bluetooth request'})
        except BluetoothRejected as error:
          cloudlog.warning(f'bluetooth {payload.get("operation")} refused ({error.code}): {error}')
          if self.settings_session() == identity:
            self.json(409, {'error': 'Bluetooth change was not accepted', 'code': error.code})
        except BluetoothUnavailable as error:
          cloudlog.warning(f'bluetooth {payload.get("operation")} unavailable ({error.code}): {error}')
          if self.settings_session() == identity:
            self.json(503, {'error': 'Bluetooth service could not complete the request', 'code': error.code})
        except (OSError, RuntimeError):
          if self.settings_session() == identity:
            self.json(503, {'error': 'Bluetooth service could not complete the request', 'code': 'service_unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path == '/api/controllers/action':
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            if not layout_owner().parked():
              self.json(403, {'error': 'Turn off the vehicle to change controller buttons'})
              return
            result = controller_action(payload, controllers_socket)
        except ControllerInvalid:
          self.json(400, {'error': 'Invalid controller request'})
        except ControllerDenied:
          self.json(403, {'error': 'Turn off the vehicle to change controller buttons'})
        except ControllerBusy:
          self.json(429, {'error': 'Controller is busy'})
        except (ControllerUnavailable, OSError, RuntimeError):
          self.json(503, {'error': 'Controller buttons are unavailable'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path.startswith('/api/maps/'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        operation = path.rsplit('/', 1)[1]
        try:
          validate_action(operation, payload)
          # Serialize command dispatch with logout. The independent owner
          # rechecks actual parked evidence throughout the accepted operation.
          with effect_lock:
            if self.settings_session() != identity:
              self.json(401, {'error': 'Sign in to Galaxy'})
              return
            result = operations.request(operation, payload)
        except ValueError:
          if self.require_session():
            self.json(400, {'error': 'Invalid map operation'})
        except MapOperationError as error:
          if self.settings_session() == identity:
            self.json(error.status, {'error': 'Map operation was not accepted', 'code': error.code})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if path.startswith('/api/settings/'):
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        try:
          if path == '/api/settings/restore':
            from openpilot.starpilot.galaxy.toggle_backup import ToggleBackup
            with effect_lock:
              result = ToggleBackup(feature_settings(), layout_owner()).restore(
                payload, identity, authorized=lambda: self.settings_session() == identity)
          elif path == '/api/settings/recovery/preview':
            result = preview_settings_recovery(payload, settings_recovery, identity)
          elif path == '/api/settings/recovery/confirm':
            if (not isinstance(payload, dict) or set(payload) != {'intent', 'confirmed'} or
                not isinstance(payload['intent'], str) or len(payload['intent']) > 64 or payload['confirmed'] is not True):
              raise ValueError('Invalid settings recovery confirmation')
            with effect_lock:
              if self.settings_session() != identity:
                raise SettingsChanged('Session changed')
              result = settings_recovery().confirm(payload['intent'], *identity,
                                                   session_valid=lambda: self.settings_session() == identity)
          elif path == '/api/settings/reset-default':
            if not isinstance(payload, dict) or set(payload) != {'view', 'row'} or \
               not isinstance(payload['view'], str) or len(payload['view']) > 64 or type(payload['row']) is not int:
              raise ValueError('Invalid default reset request')
            result = feature_settings().preview(payload['view'], payload['row'], 0, *identity, reset_default=True)
          elif path == '/api/settings/preview':
            if not isinstance(payload, dict) or set(payload) not in ({'view', 'row', 'direction'},
                                                                       {'view', 'row', 'direction', 'draft'}, {'view', 'row', 'value'}) or \
               not isinstance(payload['view'], str) or len(payload['view']) > 64 or \
               type(payload['row']) is not int or \
               ('direction' in payload and (type(payload['direction']) is not int or payload['direction'] not in (-1, 0, 1))) or \
               ('value' in payload and type(payload['value']) not in (str, int, float)) or \
               ('draft' in payload and type(payload['draft']) is not dict):
              raise ValueError('Invalid preview request')
            result = feature_settings().preview(payload['view'], payload['row'], payload.get('direction', 0), *identity,
                                                draft=payload.get('draft'), value=payload.get('value'))
          else:
            if not isinstance(payload, dict) or set(payload) not in ({'intent', 'confirmed'}, {'intent', 'confirmed', 'page'}) or \
               not isinstance(payload['intent'], str) or len(payload['intent']) > 64 or payload['confirmed'] is not True:
              raise ValueError('Invalid confirmation request')
            if 'page' in payload and (type(payload['page']) is not str or payload['page'] not in PAGES):
              raise ValueError('Invalid settings page')
            with effect_lock:
              if self.settings_session() != identity:
                raise SettingsChanged('Session changed')
              saved = feature_settings().confirm(payload['intent'], *identity,
                                                  session_valid=lambda: self.settings_session() == identity)
            if not saved:
              raise SettingsChanged('Saved source or vehicle state changed')
            result = {'saved': True}
            if 'page' in payload:
              try:
                result['page'] = feature_settings().page(payload['page'], *identity)
              except (SettingsChanged, SettingsUnavailable, OSError, RuntimeError):
                pass
        except ValueError:
          if self.settings_session() == identity:
            self.json(400, {'error': 'Invalid request'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except SettingsChanged:
          if self.settings_session() == identity:
            self.json(409, {'error': 'Saved settings changed; refresh the page'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        except (SettingsUnavailable, OSError, RuntimeError):
          if self.settings_session() == identity:
            self.json(503, {'error': 'Saved settings are unavailable'})
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if not isinstance(payload, dict) or set(payload) != {'password'} or \
         not isinstance(payload['password'], str) or not 6 <= len(payload['password']) <= 255:
        self.json(400, {'error': 'Invalid request'})
        return
      status, result, token = login(payload['password'])
      cookie = None if token is None else f'galaxy_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=1800'
      self.respond(status, json.dumps(result).encode(), cookie=cookie)

    def do_rejected_method(self):
      if self.local_request(mutation=True):
        self.json(405, {'error': 'Method unavailable'})

    def do_DELETE(self):
      if not self.local_request(mutation=True):
        return
      if urlsplit(self.path).path == '/api/spotify':
        if not self.require_session():
          return
        identity = self.settings_session()
        if identity is None:
          self.json(401, {'error': 'Sign in to Galaxy'})
          return
        from openpilot.starpilot.galaxy.spotify_setup import SetupRejected as SpotifyRejected
        try:
          result = spotify_owner().remove(identity)
        except SpotifyRejected as error:
          self.json(409, {'error': str(error)})
        except (OSError, RuntimeError):
          self.json(503, {'error': 'Spotify data could not be removed'})
        else:
          if self.settings_session() == identity:
            self.json(200, result)
          else:
            self.json(401, {'error': 'Sign in to Galaxy'})
        return
      if urlsplit(self.path).path != '/api/starport/identity':
        self.json(405, {'error': 'Method unavailable'})
        return
      if not self.require_session():
        return
      identity = self.settings_session()
      if identity is None:
        self.json(401, {'error': 'Sign in to Galaxy'})
        return
      from openpilot.starpilot.galaxy.starport_setup import SetupRejected
      def disconnect():
        if self.settings_session() != identity:
          raise SetupRejected('Galaxy session expired; sign in again')
        client = starport_client_owner()
        if client.available:
          runtime = client.call('status')['status']
          if not isinstance(runtime, dict) or type(runtime.get('running')) is not bool:
            raise RuntimeError('Android Auto connection status is unavailable')
          if runtime['running']:
            if self.settings_session() != identity:
              raise SetupRejected('Galaxy session expired; sign in again')
            # Stop is permitted with Android Auto off; this DELETE already authenticated the caller.
            client.call('stop')
            current = client.call('status')['status']
            if not isinstance(current, dict) or current.get('running') is not False:
              raise SetupRejected('Android Auto is still disconnecting; try deleting the package again')
        if self.settings_session() != identity:
          raise SetupRejected('Galaxy session expired; sign in again')
      try:
        result = starport_setup_owner().remove(identity, disconnect=disconnect)
      except SetupRejected as error:
        self.json(409, {'error': str(error)})
      except (OSError, RuntimeError, KeyError, TypeError):
        self.json(503, {'error': 'Android Auto could not be disconnected or its package removed'})
      else:
        if self.settings_session() == identity:
          self.json(200, result)
        else:
          self.json(401, {'error': 'Sign in to Galaxy'})

    do_PUT = do_PATCH = do_OPTIONS = do_rejected_method

  try:
    server = _LocalHTTPServer((host, port), Handler)
    if evidence_source is not None:
      evidence_source.start()
    if notification_owner is None:
      notification_owner = NotificationOwner()
    server.notification_source = notification_owner
    notification_owner.start()
  except BaseException:
    if notification_owner is not None:
      notification_owner.close()
    if pairing_authority is not None:
      pairing_authority.close()
    if evidence_source is not None:
      evidence_source.close()
    if "server" in locals():
      server.server_close(close_sources=False)
    raise
  if local_access is None:
    address_source.port = server.server_port
  server.device_state_source = vehicle_display
  if pairing_authority is not None:
    server.pairing_authority = pairing_authority
  server.evidence_source = evidence_source
  server.map_source = map_source
  if recording_media_source is not None:
    server.recording_media_source = recording_media_source
  if model_source is not None:
    server.model_source = model_source
  if plot_source is not None:
    server.plots_source = plot_source
  if settings_gateway is not None:
    server.settings_source = settings_gateway
  if toyota_source is not None:
    server.toyota_tools_source = toyota_source
  if bluetooth_source is not None:
    server.bluetooth_source = bluetooth_source
  if starport_setup_source is not None:
    server.starport_setup_source = starport_setup_source
  if software_operations_source is not None:
    server.software_operations_source = software_operations_source
  if drive_stats_source is not None:
    server.drive_stats_source = drive_stats_source
  legacy_google_removed = False
  def maintain_starport():
    nonlocal legacy_google_removed
    if not legacy_google_removed:
      legacy_google_removed = True
      from openpilot.starpilot.galaxy.starport_setup import remove_legacy_google_files
      remove_legacy_google_files()
  server.maintain_starport = maintain_starport
  server.start_drive_history = lambda: statistics_owner().start()
  return server


def make_remote_server(local_server, *, port=8084):
  """Share local owners and sessions while forcing all tunnel requests through password auth."""
  server = _LocalHTTPServer(('127.0.0.1', port), local_server.RequestHandlerClass)
  server.remote_transport = True
  return server


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--port', type=int, default=8082)
  parser.add_argument('--host', default='127.0.0.1', choices=('127.0.0.1', '0.0.0.0'))
  args = parser.parse_args()
  with make_server(port=args.port, host=args.host) as server:
    server.start_drive_history()
    print(f'Galaxy listening on {args.host}:{server.server_port}', flush=True)
    try:
      server.serve_forever()
    except KeyboardInterrupt:
      pass


if __name__ == '__main__':
  main()
