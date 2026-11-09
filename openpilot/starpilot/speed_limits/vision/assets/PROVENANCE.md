## Current STOP-head refinement (November 5, 2026)

The current joint detector is 9,725,049 bytes, SHA-256
`52bb30f73bf0d166342f33d1b32b29b726bf036004e0359fef10ef7bf1f5556f`.
It preserves all 483 graph nodes, operators, tensor shapes and output ABI.
All 204 initializers on the speed output's dependency path are byte-identical
to the previous qualified shared-feature model
`989aea753d776f4733a61f02a0ef50af96beb3cc8f456678768fa2fa497353d8`.
The numeric reader, RGB STOP rejector, scheduling and control policies are unchanged.
Both sign types still share one detector forward; STOP-only skips numeric reading.

Training updated 4,145 existing STOP-fusion/classification parameters while
freezing the entire shared/speed body and all batch-normalization statistics.
Seven new visually reviewed training objects comprise six complete front STOPs
and one non-STOP sign region. Source-drive identities were reconciled against
prior STOP/speed registries before assigning previously unseen routes using a
fixed identity hash. All segments, streams and augmentations inherit the route
split; the new reserved test route was excluded from training. Original footage,
teacher proposals, review receipts and immutable semantic overlays are retained.
The training set has 443 views. A training-only teacher-preservation penalty at
the existing 0.6 proposal boundary discourages upward crossings outside reviewed
objects without assigning unreviewed background negative labels. Runtime
thresholds and regression gates were not changed. Selected update 200 has SHA-256
`fa15db039e398b245c23f6b837383a2255d4813cc6182ac38e545d272f48d530`.

Paired evaluation on the same 1,375 held-out images and 388 corrected annotations
improved score-0.7 IoU-0.5 STOP hits from 152 to 154 with the same four false
proposals; score-0.6 hits improved from 172 to 176 with the same seven false
proposals. No previously detected annotation was lost and no new negative-image
read appeared at either threshold. Both models use the same retained immutable
validation correction described below. Validation images received no gradient
updates; repeated model selection and public pretraining ancestry limit claims.

Nine export/OpenCV parity cases and 153 current-source native producer, display,
model-identity and control tests passed. All 1,486 full-frame speed outputs were identical to the previous qualified
model, retaining 396 correct and two wrong regulatory reads with the same 6,137
numeric forwards. All 108 protected strong STOP objects and the 14 recorded
confirmation-phase tracks were retained, with no new known-warning confirmations. No fresh device timing or closed-loop road test is claimed.
Startup model unavailability, difficult missed scenes, ROI coverage and loaded
cadence remain limitations. The sections below retain predecessor history.

## Previous qualified shared-feature release

## Current shared-feature refinement (November 5, 2026)

The current joint detector is 9,725,066 bytes, SHA-256
`989aea753d776f4733a61f02a0ef50af96beb3cc8f456678768fa2fa497353d8`.
It retains the same 483 graph nodes, operators, tensor shapes and output ABI as
its predecessor `ac735518a3d32108dee9a112ec2f740c2ed573daac22798525ecbbbbc0ed6606`.
Only existing convolution parameters were refined; no model or inference pass
was added. STOP-only mode still skips the separate numeric reader entirely.
The numeric reader and standalone speed detector listed below are unchanged.

Training refined 15,585 existing parameters in early shared features and the
STOP branch, with remaining weights and batch-normalization statistics fixed.
The selected checkpoint is update 200, SHA-256
`e481118b0899c70e9f6dca761202459a0da0d15ac89189983cb047c88dbb8957`.
The 416 training views include reviewed STOP scenes and existing training-only
speed/advisory priors. Source-drive splits, originals and immutable annotation
overlays are retained; validation images received no gradient updates.

Paired evaluation on 1,375 held-out images (388 STOP annotations) improved
IoU-0.5 detections at score 0.7 from 141 to 152, with four false proposals in
both models. At score 0.6, detections improved from 169 to 172 with seven false
proposals in both. No previously detected annotation was lost and no new
negative-image detection was introduced at either threshold. One visibly
present front STOP missing from the original validation annotations is included
through the same immutable semantic correction for both models; original
labels and raw evaluation receipts are preserved. Public pretraining ancestry
and repeated model selection limit generalization claims.

The 1,486-frame speed replay retained all 396 correct regulatory reads and the
same two wrong-value reads among 472 regulatory frames, with no new reviewed
negative reads. Numeric forwards were 6,137 versus 6,148. All 108 protected
strong STOP objects and existing recorded confirmation phases were retained;
known warning tracks gained no confirmations. Nine OpenCV/export parity cases
and 153 native source, producer, display and control tests passed. Confirmation,
expiry, clock, authority, model-agreement and Force Stop/HKG policies are unchanged.
These are offline results, not new device timing or closed-loop road proof.
Startup model unavailability, missed difficult scenes and loaded-device cadence
remain limitations. The sections below describe the predecessor training history;
their frozen-shared-weight statements and earlier counts are historical.

## Retained predecessor history

The joint traffic-sign detector contains the original StarPilot US speed graph
plus a separate STOP head. The vision producer uses one joint detector forward. Original speed graph weights,
speed decoding, numeric digit logits and resource scheduling are preserved. The classifier adds a learned rejection-only residual.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `traffic_signs_us_detector.onnx` | 9725014 | `ac735518a3d32108dee9a112ec2f740c2ed573daac22798525ecbbbbc0ed6606` |
| `speed_limit_us_detector.onnx` | 9,605,948 | `82408b68c79c269296f0af942130c5383cace4ee06c78e2a4690e8488720116a` |
| `speed_limit_us_value_classifier.onnx` | 6,240,936 | `5d861067ab8f50986488f983cbc24f78527775d37af0b9740c5545d5f3173902` |

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
split. COCO splits were assigned by image identity. Evaluation recordings were kept separate except for the development scenes
explicitly promoted into training below. Public datasets overlap the pretrained
backbone's ancestry and do not establish whole-model unseen-scene generalization.

The STOP branch was trained for 30 epochs, selected epoch 20, then fine-tuned
with mobile Hardswish activations; selected fine-tuning epoch 10.
The initial frozen-backbone hard-negative fine-tune selected epoch 4 of 12. Three
previous development false positives (Do Not Enter / road closure) were explicitly
promoted into training, with 195 augmented views; they are no longer held-out
evidence. That STOP checkpoint SHA-256 is
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


## Night and advisory rejection follow-up

The STOP head was fine-tuned with frozen backbone/speed weights and frozen batch
normalization statistics; selected epoch 6. Six reviewed night images and eight
reviewed construction-warning negatives were explicitly assigned to training.
Those recordings are development/training evidence, not independent test data.
All existing source splits were retained, and original recordings were unchanged.

A 313-parameter RGB rejection branch was added to the same detector forward:
4x4 average pooling, a 3-to-8 channel 3x3 convolution, Hardswish, an 8-channel
3x3 depthwise convolution, Hardswish, and an 8-to-1 pointwise convolution with
Softplus. Its nonnegative output is subtracted from STOP logits before their
sigmoid. The fine-tuned STOP head and box coordinates stay frozen. The rejection
branch used 3,000 initial updates followed by 2,000 updates with positive-score
margin preservation; no detector or confirmation threshold was relaxed.
The original 402 speed nodes and 204 initializers remain byte-for-byte identical.

In development replay at one source frame per second, five sampling phases and
400ms assumed completion latency, the previously missed dark approach confirmed
in all five phases. Existing approach confirmation was preserved. Two reviewed
warning-sign sequences produced no confirmed STOP observations; isolated raw
warning proposals can remain. The selected model retained all 108 reviewed strong
STOP detections in an additional backlog sample and improved a teacher-selected
difficult sample from 3 to 14 detections among 46 signs inside the existing crop.
These are selected development samples, not exhaustive road recall estimates.
The retained GLARE test split remained 36/46 IoU50 hits, one false detection,
and no detections on its 50 negative images. Public pretraining ancestry limits
whole-model generalization claims.

The numeric classifier preserves all 147 original nodes preceding the final
softmax and all 80 original initializers from classifier
`07c6696e530eb940d2757d5849b4bc0f1d785cda704e5296e18c0a94959f30a5`.
A 2,204-parameter residual uses frozen classifier features and original logits:
a 256-to-8 pointwise convolution, Hardswish, concatenated original logits, a
linear output and Softplus. The residual, scaled by 0.9, is added only to the
reject logit. Original numeric logits and their ordering are unchanged for the
same crop; numeric probabilities can decrease. Full-frame proposal selection
and classifier workload are therefore evaluated separately.

Classifier training retained the original numeric weights and used reviewed
advisory negatives plus an immutable semantic-label overlay. Yellow advisory
numerals previously labeled as regulatory speeds were corrected by visual sign
identity, while warm-white regulatory and school signs were retained. Original
images and train/validation assignments were not changed; validation examples
were not used for gradient updates. The selected rejection checkpoint is update
1,800. Additional validation annotation review was used for model selection.

The 1,486-frame development replay retained all 395 previously correct reads
among 472 regulatory frames and the same three wrong-value reads. Advisory false
reads dropped from 137 to 43 among 167 advisory frames. No new numeric reads were
introduced in the 845 reviewed reject-frame sample. All 1,858 previously accepted
regulatory crops in the reviewed classifier validation set were retained.
Classifier forwards were 6,108 before and 6,148 after in the full-frame replay;
static accuracy preservation does not establish unchanged processing cadence.
Resource placement, processing-cost cooldown, source freshness, confirmation,
STOP expiry and braking policy remain unchanged. Closed-loop road behavior and
co-load performance are not established by offline replay.


The runtime retrieves the original classifier logits in the same classifier
forward. A learned advisory veto can stop further crop expansions only for a
non-regulatory-looking class-0 proposal whose original numeric probability is
at least 0.98 and new reject probability at least 0.90. Previous tentative reads
for that object are discarded. Duplicate class-0 proposals with IoU at least
0.75 are skipped within that frame; other objects, school proposals and the next
camera frame remain eligible. White regulatory crops keep their existing
expansion fallback. Five focused tests cover the real classifier ABI, same-forward logits, invalid/generic
rejections, duplicate-object scope and regulatory fallback. This reduces repeated
classifier work after a confident advisory rejection; it does not change
acceptance, confirmation, expiry or processing-cost cooldown thresholds.


## STOP-only execution graph

`traffic_signs_us_stop_detector.onnx` (6,239,163 bytes, SHA-256
`a744df1b585bfd80a2a983229509f52bf2f09714d9df011109ad1b8b875aaecc`)
is an ancestor-only extraction of `stop/output0` from the joint detector
`52bb30f73bf0d166342f33d1b32b29b726bf036004e0359fef10ef7bf1f5556f`.
Its 303 retained nodes and 146 retained initializers are byte-identical to the
source graph. Input is float32 RGB `[1, 3, 256, 256]`; the only output is
`stop/output0`, float32 `[1, 5, 4096]`. No weights were trained or numerically
converted for this extraction. Original detector provenance, copyright notices
and license obligations continue to apply; this extraction adds no license grant.
The separate `traffic_sign_identity.LICENSE` and identity network are unchanged.

Combined mode uses the original joint detector and reader. STOP-only mode uses
the extracted graph and the same STOP identity veto, without loading the numeric
reader or requesting the Speed identity head. Both modes verify the joint graph,
extracted graph, reader and identity network. Their composite runtime identity
binds all four SHA-256 values; it differs from the previous three-asset identity.
Mode changes validate replacement networks before use and discard previous
evidence. Scheduling, processing cooldown, source clocks, thresholds and
confirmation rules are unchanged. This execution change does not establish
loaded Controls performance or a camera frame-rate guarantee.
