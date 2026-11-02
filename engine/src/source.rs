//! One source: reconnecting capture -> latest-frame reader -> detector -> tracker -> EventDecider / BurstManager.
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use chrono::Utc;
use tracing::{error, info, warn};

use crate::behavior::RuleCache;
use crate::burst::BurstManager;
use crate::config::{CalibrationCfg, Method};
use crate::decision::EventDecider;
use crate::decode::{Decoder, Frame, redact};
use crate::detector::Yolox;
use crate::model::{Category, SourceInfo};
use crate::render::{Geometry, LiveDet, snapshot_jpeg};
use crate::repo::Repository;
use crate::speed::SpeedEstimator;
use crate::tracker::IouTracker;

/// Boxes older than this are not drawn on live frames.
const DETS_TTL: Duration = Duration::from_millis(1500);
const SNAPSHOT_MAX_WIDTH: u32 = 1280;
const SNAPSHOT_QUALITY: u8 = 85;

pub type Categories = Arc<RwLock<HashMap<String, Category>>>;

pub struct RunnerCtx {
    pub repo: Arc<dyn Repository>,
    pub categories: Categories,
    pub rules: Arc<RuleCache>,
    /// does this label have second-AI-check questions (false when that check is not configured)
    pub needs_check: crate::decision::NeedsCheck,
    pub model: PathBuf,
    pub ort_threads: Option<usize>,
    pub target_fps: f32,
    pub stall: Duration,
}

/// What the live endpoints need from a running source.
#[derive(Default)]
struct Live {
    seq: u64,
    frame: Option<Arc<Frame>>,
    dets: Vec<LiveDet>,
    geometry: Option<Geometry>,
    dets_at: Option<Instant>,
}

/// Latest decoded frame, written by the reader thread and read by the detector loop.
#[derive(Default)]
struct Latest {
    seq: u64,
    frame: Option<Arc<Frame>>,
}

pub struct SourceRunner {
    shared: Arc<Mutex<SourceInfo>>,
    stop: Arc<AtomicBool>,
    live: Arc<Mutex<Live>>,
    status: Arc<Mutex<String>>,
    thread: Mutex<Option<JoinHandle<()>>>,
    thread_done: Arc<AtomicBool>,
    pub restart_key: (String, String, Option<usize>),
    pub code: String,
}

impl SourceRunner {
    pub fn start(source: SourceInfo, ctx: Arc<RunnerCtx>) -> Arc<Self> {
        let shared = Arc::new(Mutex::new(source.clone()));
        let this = Arc::new(Self {
            shared: shared.clone(), stop: Arc::new(AtomicBool::new(false)), live: Arc::new(Mutex::new(Live::default())),
            status: Arc::new(Mutex::new("connecting".into())), thread: Mutex::new(None), thread_done: Arc::new(AtomicBool::new(false)),
            restart_key: source.restart_key(), code: source.code.clone(),
        });
        let worker = Worker {
            ctx, shared, stop: this.stop.clone(), live: this.live.clone(), status: this.status.clone(),
        };
        let done = this.thread_done.clone();
        let handle = thread::Builder::new()
            .name(format!("src-{}", source.code))
            .spawn(move || {
                worker.run(source);
                done.store(true, Ordering::SeqCst);
            })
            .expect("spawn source thread");
        *this.thread.lock().unwrap() = Some(handle);
        this
    }

    pub fn alive(&self) -> bool {
        !self.thread_done.load(Ordering::SeqCst)
    }

    pub fn status(&self) -> String {
        self.status.lock().unwrap().clone()
    }

    /// Apply changes that do not need a reconnect (name, enabled flag).
    pub fn set_source(&self, source: SourceInfo) {
        *self.shared.lock().unwrap() = source;
    }

    pub fn source(&self) -> SourceInfo {
        self.shared.lock().unwrap().clone()
    }

    pub fn request_stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub fn stop(&self) {
        self.request_stop();
        if let Some(h) = self.thread.lock().unwrap().take() {
            let _ = h.join();
        }
    }

    /// (sequence, latest frame, boxes still fresh enough to draw, calibration geometry to overlay)
    pub fn live(&self) -> (u64, Option<Arc<Frame>>, Vec<LiveDet>, Option<Geometry>) {
        let l = self.live.lock().unwrap();
        let fresh = l.dets_at.is_some_and(|t| t.elapsed() < DETS_TTL);
        (l.seq, l.frame.clone(), if fresh { l.dets.clone() } else { Vec::new() }, l.geometry.clone())
    }
}

struct Worker {
    ctx: Arc<RunnerCtx>,
    shared: Arc<Mutex<SourceInfo>>,
    stop: Arc<AtomicBool>,
    live: Arc<Mutex<Live>>,
    status: Arc<Mutex<String>>,
}

impl Worker {
    fn state(&self, src: &SourceInfo, status: &str, error: Option<&str>, seen: bool, fps: Option<f32>, ms: Option<f32>) {
        *self.status.lock().unwrap() = status.into();
        let seen_at = seen.then(Utc::now);
        if let Err(e) = self.ctx.repo.update_source_state(&src.id, status, error, seen_at, fps, ms) {
            error!("{}: state update failed: {e:#}", src.code); // a DB hiccup must not kill the stream
        }
    }

    fn sleep(&self, d: Duration) {
        let end = Instant::now() + d;
        while !self.stop.load(Ordering::Relaxed) && Instant::now() < end {
            thread::sleep(Duration::from_millis(50).min(end.saturating_duration_since(Instant::now())));
        }
    }

    fn run(&self, src: SourceInfo) {
        info!("{}: starting ({} {}, labels={})", src.code, src.kind, redact(&src.url), src.labels.join(","));
        self.state(&src, "connecting", None, false, None, None);
        let mut detector: Option<Yolox> = None;
        let (mut was_online, mut backoff) = (false, 2.0_f64);
        let rules_cache = self.ctx.rules.clone();
        let mut bursts = BurstManager::new(self.ctx.repo.clone(), src.clone(), Box::new(move || rules_cache.get()));
        let mut decider = EventDecider::new(self.ctx.repo.clone(), src.clone(), self.ctx.needs_check.clone());
        while !self.stop.load(Ordering::Relaxed) {
            if detector.is_none() {
                let cur = self.shared.lock().unwrap().clone();
                match Yolox::load(&self.ctx.model, cur.min_confidence, cur.config.detector.nms_iou, &cur.labels, cur.config.detector.intra_threads) {
                    Ok(d) => detector = Some(d),
                    Err(e) => {
                        error!("{}: detector unavailable: {e:#}", src.code);
                        self.state(&src, "error", Some(&format!("detector: {e}")), false, None, None);
                        self.sleep(Duration::from_secs(30));
                        continue;
                    }
                }
            }
            let decoder = match Decoder::open(&src.url) {
                Ok(d) => d,
                Err(e) => {
                    let msg = format!("{e:#}");
                    warn!("{}: {msg} (retry in {backoff:.0}s)", src.code);
                    self.state(&src, if was_online { "offline" } else { "error" }, Some(&msg), false, None, None);
                    self.sleep(Duration::from_secs_f64(backoff));
                    backoff = (backoff * 2.0).min(30.0);
                    continue;
                }
            };
            let latest = Arc::new(Mutex::new(Latest::default()));
            let ended = Arc::new(AtomicBool::new(false));
            let reader_stop = Arc::new(AtomicBool::new(false));
            let fps = decoder.fps();
            let pace = (src.kind == "m3u8").then(|| fps.clamp(1.0, 60.0) * 1.1);
            let reader = {
                let (latest, ended, reader_stop) = (latest.clone(), ended.clone(), reader_stop.clone());
                thread::spawn(move || read_loop(decoder, pace, latest, ended, reader_stop))
            };
            let online = self.serve(&latest, &ended, fps, detector.as_mut().expect("loaded"), &mut decider, &mut bursts);
            bursts.flush(Utc::now()); // stream lost / stopping: finish bursts with what we have
            reader_stop.store(true, Ordering::SeqCst);
            let _ = reader.join();
            self.live.lock().unwrap().frame = None;
            if self.stop.load(Ordering::Relaxed) {
                break;
            }
            if online {
                was_online = true;
                backoff = 2.0;
            }
            warn!("{}: stream lost, reconnecting", src.code);
            self.state(&src, "offline", Some("stream lost"), false, None, None);
            self.sleep(Duration::from_secs_f64(backoff));
        }
        info!("{}: stopped", src.code);
    }

    /// Returns true if at least one frame was processed before the stream dropped.
    fn serve(
        &self, latest: &Mutex<Latest>, ended: &AtomicBool, fps: f32, detector: &mut Yolox, decider: &mut EventDecider,
        bursts: &mut BurstManager,
    ) -> bool {
        let mut tracker = IouTracker::default();
        let (mut last_seq, mut online) = (0u64, false);
        let mut last_infer = Instant::now() - Duration::from_secs(3600);
        let mut last_new = Instant::now();
        let (mut window_start, mut window_n, mut window_ms) = (Instant::now(), 0u32, 0.0f32);
        let mut applied: Option<(f32, f32, Vec<String>)> = None; // detector settings currently in effect
        let mut calibration: Option<CalibrationCfg> = None;       // calibration the estimator was built from
        let mut speed: Option<SpeedEstimator> = None;
        let fps = f64::from(fps.max(1.0));
        while !self.stop.load(Ordering::Relaxed) {
            let (seq, frame) = {
                let l = latest.lock().unwrap();
                (l.seq, l.frame.clone())
            };
            let now = Instant::now();
            if seq != last_seq {
                last_new = now;
            } else if ended.load(Ordering::SeqCst) || now - last_new > self.ctx.stall {
                break;
            }
            let Some(frame) = frame else {
                thread::sleep(Duration::from_millis(10));
                continue;
            };
            let src = self.shared.lock().unwrap().clone();
            let min_gap = Duration::from_secs_f32(1.0 / src.config.detector.target_fps.unwrap_or(self.ctx.target_fps).max(0.1));
            if seq == last_seq || now - last_infer < min_gap {
                thread::sleep(Duration::from_millis(10));
                continue;
            }
            last_seq = seq;
            last_infer = now;
            if decider.source != src {
                decider.source = src.clone();
                bursts.source = src.clone();
            }
            // live settings: no reconnect needed. The detector also looks for the objects of `object` rules (a knife next to a person),
            // with their own certainty floor; detections of watched labels below the camera's certainty are dropped again below.
            let object_rules: Vec<_> = self.ctx.rules.get().into_iter().filter(|r| r.kind == "object").collect();
            let mut labels = src.labels.clone();
            let mut floor = src.min_confidence;
            for r in &object_rules {
                floor = floor.min(r.threshold);
                for o in r.config.get("objects").and_then(|v| v.as_array()).into_iter().flatten().filter_map(|o| o.as_str()) {
                    if !labels.iter().any(|l| l == o) {
                        labels.push(o.to_string());
                    }
                }
            }
            labels.sort();
            let want = (floor, src.config.detector.nms_iou, labels);
            if applied.as_ref() != Some(&want) {
                match detector.reconfigure(want.0, want.1, &want.2) {
                    Ok(()) => applied = Some(want),
                    Err(e) => {
                        error!("{}: detector settings rejected: {e:#}", src.code);
                        applied = Some(want); // do not retry every frame; the next edit triggers another attempt
                    }
                }
            }
            tracker.set_params(src.config.tracker.iou_threshold, src.config.tracker.max_age);
            if calibration.as_ref() != Some(&src.config.calibration) {
                calibration = Some(src.config.calibration.clone());
                speed = match SpeedEstimator::new(&src.config.calibration) {
                    None => None,
                    Some(Ok(e)) => Some(e),
                    Some(Err(e)) => {
                        warn!("{}: speed calibration unusable: {e:#}", src.code);
                        None
                    }
                };
            }
            if !online {
                online = true;
                self.state(&src, "online", None, true, None, None);
            }
            let t0 = Instant::now();
            let dets = match detector.detect(&frame) {
                Ok(d) => d,
                Err(e) => {
                    error!("{}: detection failed: {e:#}", src.code);
                    thread::sleep(Duration::from_millis(200));
                    continue;
                }
            };
            let infer_ms = t0.elapsed().as_secs_f32() * 1000.0;
            let dets: Vec<_> = dets.into_iter().filter(|d| !src.labels.iter().any(|l| l == d.label) || d.score >= src.min_confidence).collect();
            let tracked = tracker.update(&dets);
            let readings = speed.as_mut().map(|e| e.update(&tracked, seq as f64 / fps)).unwrap_or_default();
            let cal = &src.config.calibration;
            {
                let mut l = self.live.lock().unwrap();
                l.seq = seq;
                l.frame = Some(frame.clone());
                l.dets = tracked
                    .iter()
                    .map(|t| {
                        let kmh = readings.iter().find(|(l, r)| *l == t.label && r.track_id == t.track_id).map(|(_, r)| r.kmh);
                        LiveDet {
                            label: t.label.clone(), score: t.score, bbox: t.bbox, speed_kmh: kmh,
                            over_limit: kmh.is_some_and(|k| k > cal.speed_limit_kmh),
                        }
                    })
                    .collect();
                l.geometry = geometry(cal, speed.is_some());
                l.dets_at = Some(Instant::now());
            }
            let now_utc = Utc::now();
            let cats = self.ctx.categories.read().unwrap().clone();
            let snap = || snapshot_jpeg(&frame, SNAPSHOT_MAX_WIDTH, SNAPSHOT_QUALITY);
            bursts.step(&tracked, &snap, now_utc); // next due snapshots of running bursts
            for rec in decider.process(&tracked, &cats, &snap, now_utc) {
                bursts.start(&rec, &tracked, &cats, now_utc);
            }
            for rec in decider.apply_speeds(&readings, &tracked, &cats, &snap, now_utc) {
                bursts.start(&rec, &tracked, &cats, now_utc);
            }
            window_n += 1;
            window_ms += infer_ms;
            if window_start.elapsed() >= Duration::from_secs(5) {
                let el = window_start.elapsed().as_secs_f32();
                self.state(&src, "online", None, true, Some((window_n as f32 / el * 100.0).round() / 100.0), Some((window_ms / window_n as f32 * 10.0).round() / 10.0));
                (window_start, window_n, window_ms) = (Instant::now(), 0, 0.0);
            }
        }
        online
    }
}

/// What to draw over the live view for this calibration (nothing when speed is off or `show_geometry` is false).
fn geometry(cal: &CalibrationCfg, active: bool) -> Option<Geometry> {
    if !active || !cal.show_geometry {
        return None;
    }
    match cal.method {
        Method::Line => Some(Geometry::Lines { axis: cal.line_axis, line1: cal.line1, line2: cal.line2 }),
        Method::Homography if cal.pixels.len() == 4 => Some(Geometry::Quad([cal.pixels[0], cal.pixels[1], cal.pixels[2], cal.pixels[3]])),
        _ => None,
    }
}

/// Drains the decoder as fast as it delivers so inference always sees the newest frame.
fn read_loop(mut decoder: Decoder, pace_fps: Option<f32>, latest: Arc<Mutex<Latest>>, ended: Arc<AtomicBool>, stop: Arc<AtomicBool>) {
    while !stop.load(Ordering::Relaxed) {
        match decoder.next_frame() {
            Ok(Some(frame)) => {
                let mut l = latest.lock().unwrap();
                l.seq += 1;
                l.frame = Some(Arc::new(frame));
            }
            Ok(None) => break,
            Err(e) => {
                warn!("reader: {e:#}");
                break;
            }
        }
        // HLS delivers a whole segment at once; without pacing the latest-frame reader would see one frame per segment.
        if let Some(fps) = pace_fps {
            thread::sleep(Duration::from_secs_f32(1.0 / fps));
        }
    }
    ended.store(true, Ordering::SeqCst);
}
