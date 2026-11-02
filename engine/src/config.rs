//! Per-camera engine configuration: built-in defaults < `engine_settings 'defaults'` < `sources.config`, merged leaf by leaf
//! (docs/ENGINE_CONFIG.md). A leaf that is absent, null or out of range is skipped, so a bad edit can never stop a stream.
use serde_json::Value;
use tracing::warn;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    None,
    Line,
    Homography,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Axis {
    X,
    Y,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DetectorCfg {
    pub nms_iou: f32,
    /// None = the process default (EYEAI_TARGET_FPS)
    pub target_fps: Option<f32>,
    /// None = the process default (EYEAI_ORT_THREADS); Some(0) = runtime decides
    pub intra_threads: Option<usize>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TrackerCfg {
    pub iou_threshold: f32,
    pub max_age: u32,
    pub min_hits: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct EventsCfg {
    /// 0 = off. A track that never produced an event may open one this many seconds after the last event of its label even
    /// while the category cooldown runs.
    pub new_track_gap_s: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CalibrationCfg {
    pub method: Method,
    pub speed_limit_kmh: f32,
    pub speed_severity: String,
    pub show_geometry: bool,
    pub line_axis: Axis,
    pub line1: f32,
    pub line2: f32,
    pub distance_m: f32,
    /// four image corners as fractions 0..1, clockwise from bottom-left
    pub pixels: Vec<[f32; 2]>,
    pub world_m: Vec<[f32; 2]>,
    pub smoothing: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct EngineConfig {
    pub detector: DetectorCfg,
    pub tracker: TrackerCfg,
    pub events: EventsCfg,
    pub calibration: CalibrationCfg,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            detector: DetectorCfg { nms_iou: 0.45, target_fps: None, intra_threads: None },
            tracker: TrackerCfg { iou_threshold: 0.3, max_age: 10, min_hits: 2 },
            events: EventsCfg { new_track_gap_s: 0.0 },
            calibration: CalibrationCfg {
                method: Method::None, speed_limit_kmh: 50.0, speed_severity: "HIGH".into(), show_geometry: true, line_axis: Axis::X,
                line1: 0.4, line2: 0.7, distance_m: 20.0, pixels: Vec::new(), world_m: Vec::new(), smoothing: 0.4,
            },
        }
    }
}

fn num(v: &Value, path: &[&str], lo: f64, hi: f64) -> Option<f64> {
    let mut cur = v;
    for p in path {
        cur = cur.get(*p)?;
    }
    let n = cur.as_f64()?;
    if (lo..=hi).contains(&n) {
        Some(n)
    } else {
        warn!("config {} = {n} is outside {lo}..{hi}; ignored", path.join("."));
        None
    }
}

fn points(v: &Value, path: &[&str], lo: f64, hi: f64) -> Option<Vec<[f32; 2]>> {
    let mut cur = v;
    for p in path {
        cur = cur.get(*p)?;
    }
    let arr = cur.as_array()?;
    if arr.len() != 4 {
        warn!("config {} needs exactly 4 points; ignored", path.join("."));
        return None;
    }
    let mut out = Vec::new();
    for p in arr {
        let xy = p.as_array().filter(|a| a.len() == 2)?;
        let (x, y) = (xy[0].as_f64()?, xy[1].as_f64()?);
        if !(lo..=hi).contains(&x) || !(lo..=hi).contains(&y) {
            warn!("config {} has a point outside {lo}..{hi}; ignored", path.join("."));
            return None;
        }
        out.push([x as f32, y as f32]);
    }
    Some(out)
}

impl EngineConfig {
    /// Apply one layer of JSON over `self`. Call with the global defaults first, then the per-camera config.
    pub fn apply(mut self, layer: &Value) -> Self {
        let (d, t, c, e) = (&mut self.detector, &mut self.tracker, &mut self.calibration, &mut self.events);
        if let Some(v) = num(layer, &["events", "new_track_gap_s"], 0.0, 300.0) {
            e.new_track_gap_s = v as f32;
        }
        if let Some(v) = num(layer, &["detector", "nms_iou"], 0.1, 0.9) {
            d.nms_iou = v as f32;
        }
        if let Some(v) = num(layer, &["detector", "target_fps"], 0.5, 15.0) {
            d.target_fps = Some(v as f32);
        }
        if let Some(v) = num(layer, &["detector", "intra_threads"], 0.0, 32.0) {
            d.intra_threads = Some(v as usize);
        }
        if let Some(v) = num(layer, &["tracker", "iou_threshold"], 0.05, 0.9) {
            t.iou_threshold = v as f32;
        }
        if let Some(v) = num(layer, &["tracker", "max_age"], 0.0, 60.0) {
            t.max_age = v as u32;
        }
        if let Some(v) = num(layer, &["tracker", "min_hits"], 1.0, 10.0) {
            t.min_hits = v as u32;
        }
        match layer["calibration"]["method"].as_str() {
            Some("none") => c.method = Method::None,
            Some("line") => c.method = Method::Line,
            Some("homography") => c.method = Method::Homography,
            Some(other) => warn!("config calibration.method {other:?} is not none|line|homography; ignored"),
            None => {}
        }
        if let Some(v) = num(layer, &["calibration", "speed_limit_kmh"], 5.0, 300.0) {
            c.speed_limit_kmh = v as f32;
        }
        if let Some(s) = layer["calibration"]["speed_severity"].as_str() {
            if ["CRITICAL", "HIGH", "MEDIUM", "LOW"].contains(&s) {
                c.speed_severity = s.into();
            } else {
                warn!("config calibration.speed_severity {s:?} ignored");
            }
        }
        if let Some(b) = layer["calibration"]["show_geometry"].as_bool() {
            c.show_geometry = b;
        }
        match layer["calibration"]["line_axis"].as_str() {
            Some("x") => c.line_axis = Axis::X,
            Some("y") => c.line_axis = Axis::Y,
            Some(other) => warn!("config calibration.line_axis {other:?} is not x|y; ignored"),
            None => {}
        }
        if let Some(v) = num(layer, &["calibration", "line1"], 0.0, 1.0) {
            c.line1 = v as f32;
        }
        if let Some(v) = num(layer, &["calibration", "line2"], 0.0, 1.0) {
            c.line2 = v as f32;
        }
        if let Some(v) = num(layer, &["calibration", "distance_m"], 0.5, 1000.0) {
            c.distance_m = v as f32;
        }
        if let Some(p) = points(layer, &["calibration", "pixels"], 0.0, 1.0) {
            c.pixels = p;
        }
        if let Some(p) = points(layer, &["calibration", "world_m"], -100_000.0, 100_000.0) {
            c.world_m = p;
        }
        if let Some(v) = num(layer, &["calibration", "smoothing"], 0.0, 1.0) {
            c.smoothing = v as f32;
        }
        self
    }

    /// Defaults, then the global layer, then the per-camera layer; finally demotes a calibration that cannot work
    /// (lines on top of each other, missing corners) to `none`, with a warning.
    pub fn resolve(global: &Value, camera: &Value) -> Self {
        let mut cfg = Self::default().apply(global).apply(camera);
        let c = &mut cfg.calibration;
        match c.method {
            Method::Line if (c.line1 - c.line2).abs() < 0.02 => {
                warn!("calibration lines are less than 0.02 apart; speed is off");
                c.method = Method::None;
            }
            Method::Homography if c.pixels.len() != 4 || c.world_m.len() != 4 => {
                warn!("homography needs 4 pixel and 4 world points; speed is off");
                c.method = Method::None;
            }
            _ => {}
        }
        cfg
    }
}

/// `ClassifierTuning` from the database, refreshed at most every `ttl`; keeps the last good value if the DB hiccups.
pub struct TuningCache {
    repo: std::sync::Arc<dyn crate::repo::Repository>,
    ttl: std::time::Duration,
    state: std::sync::Mutex<(ClassifierTuning, Option<std::time::Instant>)>,
}

impl TuningCache {
    pub fn new(repo: std::sync::Arc<dyn crate::repo::Repository>, ttl: std::time::Duration) -> Self {
        Self { repo, ttl, state: std::sync::Mutex::new((ClassifierTuning::default(), None)) }
    }

    pub fn get(&self) -> ClassifierTuning {
        let mut st = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if st.1.is_none_or(|at| at.elapsed() >= self.ttl) {
            match self.repo.engine_defaults() {
                Ok(v) => st.0 = ClassifierTuning::from_defaults(&v),
                Err(e) => warn!("loading classifier tuning failed; keeping the previous values: {e:#}"),
            }
            st.1 = Some(std::time::Instant::now());
        }
        st.0.clone()
    }
}

/// Classifier settings from `engine_settings 'defaults'` under `classifier` (global only). Unset keys fall back to the environment.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ClassifierTuning {
    pub rules_enabled: Option<bool>,
    pub rule_frames: Option<usize>,
    pub rule_max_queue: Option<i64>,
    pub batch: Option<bool>,
}

impl ClassifierTuning {
    pub fn from_defaults(defaults: &Value) -> Self {
        let c = &defaults["classifier"];
        Self {
            rules_enabled: c["rules_enabled"].as_bool(),
            rule_frames: num(defaults, &["classifier", "rule_frames"], 1.0, 6.0).map(|v| v as usize),
            rule_max_queue: num(defaults, &["classifier", "rule_max_queue"], 0.0, 200.0).map(|v| v as i64),
            batch: c["batch"].as_bool(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn camera_overrides_global_overrides_defaults_leaf_by_leaf() {
        let global = json!({"tracker": {"max_age": 20, "min_hits": 3}, "detector": {"nms_iou": 0.5}});
        let camera = json!({"tracker": {"max_age": 5}});
        let c = EngineConfig::resolve(&global, &camera);
        assert_eq!((c.tracker.max_age, c.tracker.min_hits), (5, 3));
        assert_eq!(c.detector.nms_iou, 0.5);
        assert_eq!(c.tracker.iou_threshold, 0.3, "untouched leaf keeps the built-in default");
    }

    #[test]
    fn null_and_out_of_range_leaves_are_skipped() {
        let c = EngineConfig::resolve(&json!({"tracker": {"max_age": 20}}), &json!({"tracker": {"max_age": null, "min_hits": 99}, "detector": {"target_fps": 0.01}}));
        assert_eq!(c.tracker.max_age, 20, "null inherits");
        assert_eq!(c.tracker.min_hits, 2, "out of range ignored");
        assert_eq!(c.detector.target_fps, None);
    }

    #[test]
    fn a_calibration_that_cannot_work_is_turned_off() {
        let same = json!({"calibration": {"method": "line", "line1": 0.5, "line2": 0.51}});
        assert_eq!(EngineConfig::resolve(&json!({}), &same).calibration.method, Method::None);
        let ok = json!({"calibration": {"method": "line", "line1": 0.2, "line2": 0.6, "distance_m": 12, "line_axis": "y"}});
        let c = EngineConfig::resolve(&json!({}), &ok).calibration;
        assert_eq!((c.method, c.line_axis, c.distance_m), (Method::Line, Axis::Y, 12.0));
        let h = json!({"calibration": {"method": "homography"}});
        assert_eq!(EngineConfig::resolve(&json!({}), &h).calibration.method, Method::None, "no corners");
    }

    #[test]
    fn points_need_four_pairs_inside_the_frame() {
        let c = EngineConfig::resolve(&json!({}), &json!({"calibration": {"method": "homography",
            "pixels": [[0.1, 0.9], [0.1, 0.5], [0.9, 0.5], [0.9, 0.9]], "world_m": [[0, 0], [0, 30], [7, 30], [7, 0]]}})).calibration;
        assert_eq!((c.method, c.pixels.len(), c.world_m.len()), (Method::Homography, 4, 4));
        let bad = EngineConfig::resolve(&json!({}), &json!({"calibration": {"pixels": [[0.1, 1.5], [0.1, 0.5], [0.9, 0.5], [0.9, 0.9]]}})).calibration;
        assert!(bad.pixels.is_empty());
    }

    #[test]
    fn new_track_gap_is_bounded_and_classifier_tuning_reads_only_valid_keys() {
        let c = EngineConfig::resolve(&json!({"events": {"new_track_gap_s": 8}}), &json!({}));
        assert_eq!(c.events.new_track_gap_s, 8.0);
        assert_eq!(EngineConfig::resolve(&json!({"events": {"new_track_gap_s": 999}}), &json!({})).events.new_track_gap_s, 0.0);
        let t = ClassifierTuning::from_defaults(&json!({"classifier": {"rule_frames": 3, "batch": true, "rule_max_queue": 500}}));
        assert_eq!((t.rule_frames, t.batch, t.rule_max_queue, t.rules_enabled), (Some(3), Some(true), None, None));
        assert_eq!(ClassifierTuning::from_defaults(&json!({})), ClassifierTuning::default());
    }
}
