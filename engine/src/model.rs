//! Plain data shared by the worker modules (mirrors `worker/eyeai_worker/models.py`).
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq)]
pub struct SourceInfo {
    pub id: String,
    pub code: String,
    pub name: String,
    pub url: String,
    pub kind: String,
    pub labels: Vec<String>,
    pub min_confidence: f32,
    pub enabled: bool,
}

impl SourceInfo {
    /// Changing any of these needs a new connection / detector.
    pub fn restart_key(&self) -> (String, String, Vec<String>, i64) {
        let mut labels = self.labels.clone();
        labels.sort();
        (self.url.clone(), self.kind.clone(), labels, (f64::from(self.min_confidence) * 10_000.0).round() as i64)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Category {
    pub label: String,
    pub title: String,
    pub severity: String,
    pub opens_incident: bool,
    pub cooldown_s: i32,
    pub enabled: bool,
    pub burst_s: i32,
    pub burst_frames: i32,
}

/// Normalised box on the snapshot: `{x, y, w, h}` in 0..1.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct BBox {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl BBox {
    pub fn from_xyxy(b: [f32; 4]) -> Self {
        let (x1, y1, x2, y2) = (f64::from(b[0]), f64::from(b[1]), f64::from(b[2]), f64::from(b[3]));
        Self { x: x1, y: y1, w: x2 - x1, h: y2 - y1 }
    }

    pub fn centre(&self) -> (f64, f64) {
        (self.x + self.w / 2.0, self.y + self.h / 2.0)
    }
}

#[derive(Debug, Clone)]
pub struct EventRecord {
    pub event_id: String,
    pub incident_id: Option<String>,
    pub incident_code: Option<String>,
    pub label: String,
    pub track_id: i32,
    /// Attached to an already-open incident.
    pub grouped: bool,
}

/// A snapshot ready to store: JPEG plus its size.
#[derive(Debug, Clone)]
pub struct Snapshot {
    pub jpeg: Vec<u8>,
    pub width: i32,
    pub height: i32,
}

pub const SEV_ORDER: [&str; 4] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

pub fn sev_rank(s: &str) -> usize {
    SEV_ORDER.iter().position(|x| *x == s).unwrap_or(0)
}

pub fn higher<'a>(a: &'a str, b: &'a str) -> &'a str {
    if sev_rank(a) >= sev_rank(b) { a } else { b }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn severity_only_goes_up() {
        assert_eq!(higher("MEDIUM", "CRITICAL"), "CRITICAL");
        assert_eq!(higher("HIGH", "LOW"), "HIGH");
    }

    #[test]
    fn restart_key_ignores_label_order_and_name() {
        let mk = |labels: &[&str], name: &str| SourceInfo {
            id: "1".into(), code: "C".into(), name: name.into(), url: "u".into(), kind: "rtsp".into(),
            labels: labels.iter().map(|s| s.to_string()).collect(), min_confidence: 0.4, enabled: true,
        };
        assert_eq!(mk(&["car", "person"], "a").restart_key(), mk(&["person", "car"], "b").restart_key());
    }
}
