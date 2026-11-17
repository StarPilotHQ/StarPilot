import assert from 'node:assert/strict'
import { weatherTemperature } from '../web/js/weather.js'

const data = {status: 'current', temperatureC: -5, observedAt: 10000}
assert.equal(weatherTemperature(data, true, 10000), '-5°C')
assert.equal(weatherTemperature(data, false, 10000), '23°F')
assert.equal(weatherTemperature({...data, temperatureC: 0}, true, 10000), '0°C')
assert.equal(weatherTemperature(data, true, 11801), 'Unavailable')
assert.equal(weatherTemperature({...data, status: 'loading'}, true, 10000), 'Unavailable')
assert.equal(weatherTemperature({...data, temperatureC: null}, true, 10000), 'Unavailable')
assert.equal(weatherTemperature({...data, temperatureC: NaN}, true, 10000), 'Unavailable')
console.log('Weather units, freezing temperatures and expiry passed')
