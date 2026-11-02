# What this version can and cannot detect

Written 2026-11-02. It answers two questions: which settings are changeable from the dashboard, and whether this version can
detect the use of a knife (or other suspicious behaviour). Read the verdict first; the rest is how it was reached.

## Verdict

**Knife / sharp weapon.** The platform has two rules for it. `weapon_object` (works with the cameras alone, on by default): the detector's own
knife / scissors / baseball-bat classes seen next to a person during the follow-up photos, at certainty >= 0.35; these are COCO classes, so small or
hidden blades are often missed and kitchen or office scenes can trigger it. `weapon` (needs the optional second AI check):  *"Is the person holding a weapon or a weapon-like object such as a
gun, knife or crowbar?"*, threshold 0.70, severity CRITICAL, opens an incident). It is a question put to a 3B vision-language model
about a still crop of the person. **Nobody has measured how often it catches a real knife.** The only evidence in the repository is
negative: on ordinary footage it stayed below P = 0.15 (migration 004), meaning few false alarms, which says nothing about misses.
Treat it as an experimental indicator that can help a human reviewer on cameras where the hand is clearly visible, and **not** as a
safety control. Do not rely on it to tell you that nobody is armed.

**Other suspicious behaviour.**

| Behaviour | How it is detected | Status |
|---|---|---|
| Weapon-like object in hand | classifier question on the person crop | unvalidated recall (see above) |
| Tampering with lock, door, machine, camera | classifier question | unvalidated |
| Concealing an item in a bag or clothing | classifier question | unvalidated |
| Climbing a fence, wall or gate | classifier question | unvalidated |
| Face covered / disguised | classifier question | **off by default**: fired on hats and hoods (4 of 12 people at 0.75) |
| Licence plate covered | classifier question | **off by default**: depends on camera angle |
| Lingering / loitering | deterministic: the person stays put for the whole snapshot burst (>= 5 s) | works from the track, no model |
| Crowd (3+ in view), moving across the frame | deterministic indication from the burst | works from the track |
| Over speed limit | deterministic: calibrated geometry + frame counter | an estimate, scales linearly with the measured distance |
| **Intruder** (a person who should not be there: after hours, forbidden zone, no access record) | not implemented | needs zones, schedules and/or access data; a model cannot know who is authorised |
| Fighting / violence, falling, abandoned object | not implemented | no pose or temporal model |

Every result is an *indicator for a person to review*. Nothing is blocked, accused or actuated automatically, and the UI says
"indicator", never "fraud" or "weapon detected".

## How the weapon rule works, and where it can miss

Each step can lose the knife. In order:

1. **A person must be detected first.** YOLOX (COCO `person`) at 640 px input, confidence >= `min_confidence` (0.4 default), seen
   in >= `min_hits` frames (2). A knife is never detected on its own; there is no knife detector.
2. **An event must be created.** At most one event per label per camera per `categories.cooldown_s` (30 s for people). A second
   person who walks in 10 s after the first is not reported until the cooldown passes. New since this version: `events.new_track_gap_s`
   (dashboard) lets a track nobody has reported yet break through after N seconds. Default off.
3. **The snapshot is taken early.** The first snapshot is the frame in which the event is created, about two analysed frames after
   the person appears. A knife that comes out later is only seen if the rule looks at the burst: `classifier.rule_frames` (dashboard,
   1..6, default 1) makes it evaluate the first N snapshots and keep the highest probability.
4. **The crop is small.** The rule sees the person box plus 50 % margin of its larger side, from a snapshot stored at <= 1280 px wide
   (the person is usually far smaller than the frame). Published guidance asks for the object itself to be about 20 x 20 px or more
   for hand-held-object detectors, and 50 px outdoors / 20 px indoors for generic detection analytics. A knife in a hand a few
   pixels wide on a wide-angle camera is below that, and a 3B model is not a detector.
5. **A 3B model answers yes/no.** `d1` is an lfm2-family model of about 2.7B parameters (Ollama metadata). It was designed to make
   decisions, not to localise small objects, and its probabilities are not calibrated. The threshold (0.70) is a guess until measured.
6. **The queue can skip it.** Rules are skipped while more than `classifier.rule_max_queue` (10) events wait. On CPU a rule costs
   1-2 s per question per event; on a GPU far less (a run on an RTX 3090 and an H100 took about 0.6 s for the class question).
7. **A human has to look.** A trigger raises severity, flags the event as suspicious and opens an incident; someone must act on it.

## What the literature says

- No standard benchmark exists for knife detection on CCTV; the studies found use different datasets and are not comparable.
  A YOLOv8m study on guns and knives (>10,000 annotated images) reports mAP@0.5 of 0.852, precision 0.89 and recall 0.80, with
  false positives and class confusion ([source](https://core.ac.uk/outputs/661266214)); a Thai comparison of YOLO variants put YOLOv8
  at 0.874 validation / 0.848 test mAP ([source](https://ph02.tci-thaijo.org/index.php/mijet/article/download/254517/172345)). These are
  *trained* detectors on curated images; recall of 0.80 means one knife in five is still missed even there.
- Small objects are the core problem on CCTV: the weapon is tiny relative to the scene and public datasets cover CCTV poorly. Tiling
  the image before training gave a large gain in one study ([source](https://www.epistemonikos.org/en/documents/086480aef1fac07ed2d8efb2c3e9af400818084a));
  a Tampere thesis likewise reports that tiling and a high input resolution help ([source](https://trepo.tuni.fi/handle/10024/152501)).
- A hand-held-knife product states that the knife must be at least 20 x 20 px and the person fairly close
  ([source](https://getscw.com/survail/computer-vision/model-zoo/person-detection/object-in-hand/knife)); Genetec's guidance for
  object-detection analytics is more than 50 px outdoors and 20 px indoors
  ([source](https://techdocs.genetec.com/r/en-US/KiwiVisionTM-User-Guide-for-Security-Center-5.12.0.0/Camera-requirements-for-object-detection-analytics)).
- Zero-shot vision-language models can classify *violence* in surveillance clips at roughly 86-96 % on two benchmarks, but the
  authors stress sensitivity to domain shift ([source](https://arxiv.org/pdf/2510.17651)). **No study found measures a zero-shot VLM on
  knives**, so there is no published number for this approach.
- Pose-based violence detection suffers from poor generalisation, lighting, angle and occlusion, and false alarms; one 2026 preprint
  found pose representations did not beat coarse box geometry ([source](https://arxiv.org/pdf/2608.27879)). Nothing here is
  field-validated.
- Open-vocabulary detectors (Grounding DINO, YOLO-World) can find objects from a text prompt, but no knife-specific zero-shot numbers
  were found; YOLO-World is GPL-3.0 ([source](https://playground.roboflow.com/models/tencent%20ai%20lab/yolo-world)).
- Liquid AI's documentation says grounding / object detection is best supported by its LFM2.5-VL-3B model
  ([source](https://docs.liquid.ai/lfm/key-concepts/vision-capabilities)). Whether our `d1` has that ability was not established.

## How to find out for your cameras

1. **Measure it.** `scripts/eval_rule.py` scores one rule on your own labelled images (positives with a person really holding a
   knife, negatives with ordinary people, tools, phones, umbrellas) and prints recall and false positives per threshold.
   Use prop knives, with consent and safely, at 3 distances x 3 angles x day/night on the cameras you will deploy. Decide the
   acceptable misses first, then pick the threshold.
2. **Use the settings that help**: `classifier.rule_frames` 3-4, `events.new_track_gap_s` 5-10 s where several people pass, a shorter
   `cooldown_s` for people, and a camera that shows the hands (closer, higher resolution, lower mounting angle).
3. **If recall is not good enough, add a detector rather than a bigger prompt**: a two-stage design (person crop, then a knife/weapon
   detector at higher resolution or with tiling) is what the literature supports. Check the licence first: Ultralytics YOLO weights are
   AGPL-3.0 and YOLO-World is GPL-3.0, which is why this project uses YOLOX (Apache-2.0).
4. **Intruder logic is separate work**: zones per camera, schedules, and optionally an access-log feed. It needs rules that look at
   *where and when*, not a better model.

## What can be set from the dashboard

Admin only; the worker applies it within one poll (5 s), the classifier tuning within 30 s, without a restart unless noted.

| Where | Setting |
|---|---|
| Sources (per camera) | URL, enabled, watched labels, minimum confidence |
| Sources > Engine settings (per camera, inherits the defaults) | detector NMS overlap, analysed frames per second, ONNX threads (reconnects); tracker overlap, max age, frames before an event; **new-track gap**; speed calibration (two lines or four corners drawn on the snapshot, real distance, limit, severity, show geometry) |
| Sources > Engine defaults (global) | the same keys for every camera, plus **classifier tuning**: rules on/off, frames evaluated per event, queue limit, batch |
| Rules | create / edit / enable / delete behaviour rules: question, applies-to labels, threshold, severity, whether it opens an incident, order; indication rules by kind and minimum duration |
| Rules > Categories | title, severity, opens-incident, enabled, event cooldown, burst length and snapshot count per label |

Not in the dashboard (environment / build): the classifier URL, model and timeout (`CLASSIFIER_*`), the YOLOX model file, GPU provider,
the poll interval and the worker id.

Sources: see the links above. Search was done on 2026-11-02 with standard web search; results were summaries, not full papers.
