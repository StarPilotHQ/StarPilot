The joint traffic-sign detector contains the original StarPilot US speed graph
plus a separate STOP head. The vision producer uses one joint detector forward. Original speed graph weights,
speed decoding, numeric classifier and resource scheduling are preserved.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `traffic_signs_us_detector.onnx` | 9721639 | `e94f0ef60ce6c631fb69e449b86d50f07518d0031d7d4ec84db912618697d3ed` |
| `speed_limit_us_detector.onnx` | 9,605,948 | `82408b68c79c269296f0af942130c5383cace4ee06c78e2a4690e8488720116a` |
| `speed_limit_us_value_classifier.onnx` | 6,229,930 | `07c6696e530eb940d2757d5849b4bc0f1d785cda704e5296e18c0a94959f30a5` |

The joint detector accepts the existing 256×256 RGB letterbox. `output0` remains the
original `(1, 5, 1344)` speed output. `stop/output0` is `(1, 5, 4096)`, with four
box coordinates and one STOP score. The STOP threshold is 0.6. The classifier
still accepts 128×128 and emits 19 values.

The original 402 speed graph nodes, 204 initializers and speed output declaration
are preserved byte-for-byte from detector
`82408b68c79c269296f0af942130c5383cace4ee06c78e2a4690e8488720116a`.
The STOP branch joins existing layer 2 and layer 16 features: two 64→8 pointwise
projections, nearest-neighbor upsampling of layer 16, concatenation, a 16-channel
3×3 fusion convolution, and a small stride-four detection head. The appended branch uses Hardswish
activations (HardSigmoid × input) to avoid the cost of exponentials on the CPU.
Original speed activations are unchanged. Both outputs use
one detector forward; STOP never enters the numeric classifier.

Training used the recovered YOLO26n speed checkpoint
`bc0ea13f0da03250a834fbeaad50e92e531fef6998b601520484c1e30d15695e`,
with all backbone/speed weights frozen. LISA, GLARE, COCO and manually reviewed
comma training scenes supplied STOP/negative examples. Original recording splits
were retained for LISA/GLARE; augmented and low-light views inherited their source
split. COCO splits were assigned by image identity. Real driving approaches used
for evaluation were excluded from training. Public datasets overlap the pretrained
backbone's ancestry and do not establish whole-model unseen-scene generalization.

The STOP branch was trained for 30 epochs, selected epoch 20, then fine-tuned
with mobile Hardswish activations; selected fine-tuning epoch 10.
A final frozen-backbone hard-negative fine-tune selected epoch 4 of 12. Three
previous development false positives (Do Not Enter / road closure) were explicitly
promoted into training, with 195 augmented views; they are no longer held-out
evidence. The final STOP checkpoint SHA-256 is
`4fa350a2e7d177eb9b3f48cbeafd1d406560f7a7c77482eb3f61e90716f6f73c`, using Ultralytics 8.4.32, PyTorch 2.15.0, opset 12.
Original Ultralytics author/license metadata is retained in the detector. Dataset
source/license receipts and original recordings are retained with the training
artifacts; training images are not bundled here.

`TrafficSignModelCore.infer_signs(frame)` returns the existing per-frame speed read and
separate STOP boxes/confidences in original-frame pixels. The inherited `infer` and `observe` contracts remain intact.
The producer publishes independent STOP boxes with original camera frame, capture
time, dimensions and session. Heartbeats never renew that capture time. The current
right-side camera region is shared, so signs outside it are not covered. The vision
service remains opt-in; this does not enable vision or Force Stop preferences.

STOP confirmation requires spatially consistent, distinct camera frames at scores
>= 0.7 spanning at least 0.5 seconds. Negative, invalid, stale, reordered or changed
session evidence clears confirmation. The icon expires 1.5 seconds after its source
capture, independently of persistent speed-limit retention. Force Stop may acquire
a vision sign only within 750 ms of the confirmed source capture, with existing
longitudinal authority, model horizon (<50 m), speed (<17.88 m/s), turn, curve and
lead gates. Native/model/car freshness limits are unchanged. Once committed, the
existing HKG sign hold persists through sign loss until the existing RES/accelerator
release, disengagement or authority reset. Raw boxes never directly request braking.

Qualification: original speed tensors and decoded results were identical on 1,486
frames (6,108 classifier calls each, zero lost/gained exact reads). The final STOP
head recovered 36/46 visible GLARE test signs at IoU 0.5 with one false proposal;
50 negative test images had none. Development footage is used for model selection,
not an independent test: five training-disjoint approaches were detected, including
night. An additional 36-minute scan at 1 FPS yielded 21 true STOP proposals and one
orange-warning false proposal. A sequential 5 Hz check of that warning produced no
confirmation under the half-second rule. This does not prove false positives absent.
Recorded approach replay at 1 Hz across five sampling phases confirmed all daytime
approaches in every phase; night confirmed in three of five phases. These are
recorded, assumed-latency scenarios, not closed-loop or onroad cadence qualification.

The same architecture's earlier weights measured 4.06% median full-inference
overhead on the parked comma CPU (16 paired samples); final weights have not had
another device timing pass. A declared-cost replay showed small latency changes
can shift speed-sign sampling (73 to 69 correct reads in a 100-scene +5% cost
scenario). Frozen speed outputs preserve per-frame accuracy but do not establish
identical sampling, loaded-device performance or onroad stopping reliability.
