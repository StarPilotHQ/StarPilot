from typing import cast
from dataclasses import replace
from unittest import TestCase

from openpilot.starpilot.galaxy.tests import test_settings as fixtures


class TestRecoveryHttp(TestCase):
  def setUp(self):
    fixtures.SettingsHttpTest.setUp(cast(fixtures.SettingsHttpTest, self))
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
    self.context.value = replace(self.context.value, cp=cp)

  stop = fixtures.SettingsHttpTest.stop
  request = fixtures.SettingsHttpTest.request
  login = fixtures.SettingsHttpTest.login

  def selection(self, cookie, labels):
    status, value, _ = self.request('/api/settings/non-defaults', cookie=cookie)
    self.assertEqual(status, 200)
    selected = [row['id'] for row in value['rows'] if row['label'] in labels]
    self.assertEqual(len(selected), len(labels))
    return {'view': value['view'], 'selected': selected}

  def test_authenticated_individual_reset_requires_real_confirmation_and_refresh(self):
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    self.assertEqual(self.request('/api/settings/non-defaults')[0], 401)
    cookie = self.login()
    payload = self.selection(cookie, ['Wheel brake and acceleration colors'])
    self.assertEqual(self.request('/api/settings/recovery/preview', payload=payload)[0], 401)
    self.assertEqual(self.request('/api/settings/recovery/preview', cookie=cookie, payload=payload,
                                  origin='https://elsewhere.example')[0], 403)
    status, pending, _ = self.request('/api/settings/recovery/preview', cookie=cookie, payload=payload)
    self.assertEqual(status, 200)
    self.assertTrue(self.params.get_bool('ShowBrakeStatus'))
    body = {'intent': pending['intent'], 'confirmed': True}
    self.assertEqual(self.request('/api/settings/recovery/confirm', cookie=cookie, payload=body)[0], 200)
    self.assertFalse(self.params.get_bool('ShowBrakeStatus'))
    self.assertEqual(self.request('/api/settings/recovery/confirm', cookie=cookie, payload=body)[0], 409)
    rows = self.request('/api/settings/non-defaults', cookie=cookie)[1]['rows']
    self.assertNotIn('Wheel brake and acceleration colors', [row['label'] for row in rows])

  def test_confirmation_reset_restores_off(self):
    self.params.put('SLCConfirmation', 1, block=True)
    cookie = self.login()
    payload = self.selection(cookie, ['Confirm limits'])
    status, pending, _ = self.request('/api/settings/recovery/preview', cookie=cookie, payload=payload)
    self.assertEqual(status, 200)
    result = self.request('/api/settings/recovery/confirm', cookie=cookie,
                          payload={'intent': pending['intent'], 'confirmed': True})
    self.assertEqual(result[0], 200)
    self.assertTrue(result[1]['saved'])
    self.assertFalse(self.params.get('SLCConfirmation'))

  def test_wrong_selection_and_unconfirmed_requests_are_rejected(self):
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    cookie = self.login()
    payload = self.selection(cookie, ['Wheel brake and acceleration colors'])
    for invalid in (payload | {'selected': [True]}, payload | {'selected': [{}]}, payload | {'value': 'Off'}):
      self.assertEqual(self.request('/api/settings/recovery/preview', cookie=cookie, payload=invalid)[0], 400)
    pending = self.request('/api/settings/recovery/preview', cookie=cookie, payload=payload)[1]
    self.assertEqual(self.request('/api/settings/recovery/confirm', cookie=cookie,
                                  payload={'intent': pending['intent'], 'confirmed': False})[0], 400)
    self.assertTrue(self.params.get_bool('ShowBrakeStatus'))

  def test_vehicle_or_saved_source_change_revokes_batch_before_any_write(self):
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    cookie = self.login()
    payload = self.selection(cookie, ['Wheel brake and acceleration colors'])
    pending = self.request('/api/settings/recovery/preview', cookie=cookie, payload=payload)[1]
    self.context.value = replace(self.context.value, cp_raw=b'changed')
    self.assertEqual(self.request('/api/settings/recovery/confirm', cookie=cookie,
                                  payload={'intent': pending['intent'], 'confirmed': True})[0], 409)
    self.assertTrue(self.params.get_bool('ShowBrakeStatus'))

  def test_unsupported_rows_are_reported_and_available_reset_all_does_not_touch_them(self):
    self.params.put('SLCConfirmation', 1, block=True)
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    self.context.value = replace(self.context.value, cp=None, cp_raw=None)
    cookie = self.login()
    status, view, _ = self.request('/api/settings/non-defaults', cookie=cookie)
    self.assertEqual(status, 200)
    unsupported = next(row for row in view['rows'] if row['label'] == 'Confirm limits')
    self.assertFalse(unsupported['resetAvailable'])
    self.assertEqual(self.request('/api/settings/recovery/preview', cookie=cookie,
      payload={'view': view['view'], 'selected': [unsupported['id']]})[0], 409)
    selected = [row['id'] for row in view['rows'] if row['resetAvailable']]
    pending = self.request('/api/settings/recovery/preview', cookie=cookie,
      payload={'view': view['view'], 'selected': selected})[1]
    result = self.request('/api/settings/recovery/confirm', cookie=cookie,
      payload={'intent': pending['intent'], 'confirmed': True})
    self.assertEqual(result[0], 200)
    self.assertTrue(result[1]['saved'])
    self.assertFalse(self.params.get_bool('ShowBrakeStatus'))
    self.assertTrue(self.params.get('SLCConfirmation'))
