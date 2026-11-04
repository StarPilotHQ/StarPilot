The joint traffic-sign detector contains the original StarPilot US speed graph
plus a separate STOP head. The existing speed producer, detector and numeric
classifier remain unchanged; the joint core is available for explicit integrations.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `traffic_signs_us_detector.onnx` | 9721631 | `9812029a5b2307596388ee5aef7580edc6a6798debade1fae7a4f700afd1510a` |
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
The final STOP checkpoint SHA-256 is
`dcf84e6e2491e1fdf70621dce5b5c96d08e6a48289673dd2c97a2de9ed45d80f`, using Ultralytics 8.4.32, PyTorch 2.15.0, opset 12.
Original Ultralytics author/license metadata is retained in the detector. Dataset
source/license receipts and original recordings are retained with the training
artifacts; training images are not bundled here.

`TrafficSignModelCore.infer_signs(frame)` returns the existing per-frame speed read and
separate STOP boxes/confidences in original-frame pixels. The inherited `infer` and `observe` contracts remain intact.
The existing producer continues using `VisionModelCore` and its original assets. STOP proposals clear on every inference
and reset; they are not a persistent speed value or a confirmed stop request.
The current right-side camera region is shared, so signs outside it are not covered.
The existing vision service remains development opt-in. Automatic-stop behavior
requires its own observation/confirmation and control qualification.

Joint inference adds approximately 2.25% median inference time in a paired host
benchmark. A declared-cost replay showed that even small latency changes can
shift speed-sign sampling; adoption by an onroad producer needs its own cadence
and co-load qualification. Raw proposals include known Do Not Enter and road-
closure confusions and must not directly command braking.
