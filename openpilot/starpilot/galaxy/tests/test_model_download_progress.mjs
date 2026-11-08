import assert from "node:assert/strict"
import { ModelsPage, modelDownloadProgress, downloadSpeed, formatSpeed, nextDownloadSamples } from "../web/js/models.js"

// The model being downloaded gets a progress bar with its percent and speed (8 Nov 2026).
const models = [{ value: "light", label: "Lightspeed", artifactSize: 60e6 }, { value: "other", label: "Other", artifactSize: 50e6 }]

// The comma reports "<model name>: NN%" while downloading; the bar goes on that model.
assert.deepEqual(modelDownloadProgress({ downloading: true, progress: "Lightspeed: 45%", modelToDownload: "light" }, models),
  { model: "light", percent: 45, size: 60e6, label: "Downloading" })
// Download all: the named model is the one in progress.
assert.equal(modelDownloadProgress({ downloading: true, downloadAll: true, progress: "Other: 3%" }, models).model, "other")
// Before the first percent: a moving bar on the chosen model ("Starting download").
assert.deepEqual(modelDownloadProgress({ downloading: true, progress: "Starting download", modelToDownload: "other" }, models),
  { model: "other", percent: null, size: 50e6, label: "Starting download" })
// eGPU variant: labelled, and no size (its speed is unknown).
assert.deepEqual(modelDownloadProgress({ downloading: true, downloadVariant: "amd", progress: "Lightspeed: 10%", modelToDownload: "light" }, models),
  { model: "light", percent: 10, size: 0, label: "Downloading · eGPU variant" })
// No bar when nothing downloads, while the catalog refreshes, or for an unknown name during Download all.
assert.equal(modelDownloadProgress({ downloading: false, progress: "Downloaded!" }, models), null)
assert.equal(modelDownloadProgress({ downloading: true, refreshing: true, progress: "Refreshing catalog" }, models), null)
assert.equal(modelDownloadProgress({ downloading: true, downloadAll: true, progress: "Starting download" }, models), null)
assert.equal(modelDownloadProgress({ downloading: true, progress: "Lightspeed: 250%", modelToDownload: "light" }, models).percent, 100)

// Speed: percent moved x size over the time between the oldest and newest sample.
assert.equal(downloadSpeed([{ t: 0, percent: 40 }, { t: 10000, percent: 45 }], 60e6), 300e3)
assert.equal(downloadSpeed([{ t: 0, percent: 40 }, { t: 2000, percent: 45 }], 60e6), null, "under 3 s is too short to say")
assert.equal(downloadSpeed([{ t: 0, percent: 40 }, { t: 10000, percent: 45 }], 0), null, "no size, no speed")
assert.equal(formatSpeed(2.45e6), "2.5 MB/s")
assert.equal(formatSpeed(300e3), "300 KB/s")
assert.equal(formatSpeed(null), "")

// Samples: kept for the same model over the last 15 seconds, started again for another model or a fresh download.
let samples = []
samples = nextDownloadSamples(samples, { model: "light", percent: 1 }, 0)
samples = nextDownloadSamples(samples, { model: "light", percent: 5 }, 10000)
samples = nextDownloadSamples(samples, { model: "light", percent: 9 }, 20000)
assert.deepEqual(samples.map(s => s.percent), [5, 9], "older than 15 seconds dropped")
assert.deepEqual(nextDownloadSamples(samples, { model: "other", percent: 2 }, 21000).map(s => s.model), ["other"])
assert.deepEqual(nextDownloadSamples(samples, { model: "light", percent: 0 }, 21000).map(s => s.percent), [0], "a restarted download starts again")
assert.deepEqual(nextDownloadSamples(samples, null, 21000), [])

// The page: the bar sits on the model's row with the label, speed and percent.
const vm = { status: { downloading: true, progress: "Lightspeed: 45%", modelToDownload: "light" }, models,
  downloadSamples: [{ t: 0, model: "light", percent: 40 }, { t: 10000, model: "light", percent: 45 }] }
assert.deepEqual(ModelsPage.computed.downloadBar.call(vm), { model: "light", percent: 45, size: 60e6, label: "Downloading", speed: "300 KB/s" })
assert.match(ModelsPage.template, /v-if="downloadBar\?\.model === m\.value" class="gx-update-progress gx-model-progress"/)
assert.match(ModelsPage.template, /role="progressbar" :aria-label="'Downloading ' \+ m\.label"/)
console.log("Model download progress: bar, percent and speed on the downloading model")
