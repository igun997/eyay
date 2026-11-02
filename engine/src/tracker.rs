//! Per-label IoU tracker: maps detections across frames to stable track ids (port of `eyeai.detect.tracker`).
//!
//! Greedy association by descending IoU (the Python version used the Hungarian algorithm; for the handful of objects
//! of one label per frame the result is the same except in contrived ties). Ids only go up and are never reused.
use std::collections::HashMap;

use crate::detector::Detection;

#[derive(Debug, Clone)]
pub struct Tracked {
    pub track_id: i32,
    pub label: String,
    pub score: f32,
    /// x1,y1,x2,y2 normalised 0..1
    pub bbox: [f32; 4],
}

struct Track {
    id: i32,
    label: String,
    bbox: [f32; 4],
    missed: u32,
}

pub fn iou(a: &[f32; 4], b: &[f32; 4]) -> f32 {
    let iw = (a[2].min(b[2]) - a[0].max(b[0])).max(0.0);
    let ih = (a[3].min(b[3]) - a[1].max(b[1])).max(0.0);
    let inter = iw * ih;
    let union = (a[2] - a[0]).max(0.0) * (a[3] - a[1]).max(0.0) + (b[2] - b[0]).max(0.0) * (b[3] - b[1]).max(0.0) - inter;
    if union > 0.0 { inter / union } else { 0.0 }
}

pub struct IouTracker {
    iou_threshold: f32,
    max_missed: u32,
    tracks: Vec<Track>,
    next_id: i32,
}

impl Default for IouTracker {
    fn default() -> Self {
        Self::new(0.3, 10)
    }
}

impl IouTracker {
    pub fn new(iou_threshold: f32, max_missed: u32) -> Self {
        Self { iou_threshold, max_missed, tracks: Vec::new(), next_id: 1 }
    }

    pub fn set_params(&mut self, iou_threshold: f32, max_missed: u32) {
        (self.iou_threshold, self.max_missed) = (iou_threshold, max_missed);
    }

    /// One `Tracked` per input detection, in input order.
    pub fn update(&mut self, dets: &[Detection]) -> Vec<Tracked> {
        let mut assigned: Vec<i32> = vec![-1; dets.len()];
        let mut survivors: Vec<Track> = Vec::new();
        let mut labels: Vec<String> = dets.iter().map(|d| d.label.to_string()).chain(self.tracks.iter().map(|t| t.label.clone())).collect();
        labels.sort_unstable();
        labels.dedup();

        let mut old: HashMap<String, Vec<Track>> = HashMap::new();
        for t in self.tracks.drain(..) {
            old.entry(t.label.clone()).or_default().push(t);
        }
        for label in labels {
            let mut tracks = old.remove(&label).unwrap_or_default();
            let det_idx: Vec<usize> = (0..dets.len()).filter(|&i| dets[i].label == label.as_str()).collect();
            let mut pairs: Vec<(f32, usize, usize)> = Vec::new();
            for (ti, t) in tracks.iter().enumerate() {
                for (di, &i) in det_idx.iter().enumerate() {
                    let v = iou(&t.bbox, &dets[i].bbox);
                    if v >= self.iou_threshold {
                        pairs.push((v, ti, di));
                    }
                }
            }
            pairs.sort_by(|a, b| b.0.total_cmp(&a.0));
            let (mut used_t, mut used_d) = (vec![false; tracks.len()], vec![false; det_idx.len()]);
            for (_, ti, di) in pairs {
                if used_t[ti] || used_d[di] {
                    continue;
                }
                used_t[ti] = true;
                used_d[di] = true;
                tracks[ti].bbox = dets[det_idx[di]].bbox;
                tracks[ti].missed = 0;
                assigned[det_idx[di]] = tracks[ti].id;
            }
            for (ti, mut t) in tracks.into_iter().enumerate() {
                if used_t[ti] {
                    survivors.push(t);
                } else {
                    t.missed += 1;
                    if t.missed <= self.max_missed {
                        survivors.push(t);
                    }
                }
            }
            for (di, &i) in det_idx.iter().enumerate() {
                if used_d[di] {
                    continue;
                }
                let id = self.next_id;
                self.next_id += 1;
                survivors.push(Track { id, label: label.clone(), bbox: dets[i].bbox, missed: 0 });
                assigned[i] = id;
            }
        }
        self.tracks = survivors;
        dets.iter()
            .enumerate()
            .map(|(i, d)| Tracked { track_id: assigned[i], label: d.label.to_string(), score: d.score, bbox: d.bbox })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn det(label: &'static str, b: [f32; 4]) -> Detection {
        Detection { label, class_id: 0, score: 0.9, bbox: b }
    }

    #[test]
    fn same_object_keeps_its_id_and_new_object_gets_a_new_one() {
        let mut t = IouTracker::default();
        let a = t.update(&[det("person", [0.1, 0.1, 0.3, 0.5])]);
        let b = t.update(&[det("person", [0.12, 0.1, 0.32, 0.5]), det("person", [0.6, 0.1, 0.8, 0.5])]);
        assert_eq!(a[0].track_id, 1);
        assert_eq!(b[0].track_id, 1);
        assert_eq!(b[1].track_id, 2);
    }

    #[test]
    fn labels_never_share_a_track() {
        let mut t = IouTracker::default();
        t.update(&[det("person", [0.1, 0.1, 0.3, 0.5])]);
        let r = t.update(&[det("car", [0.1, 0.1, 0.3, 0.5])]);
        assert_eq!(r[0].track_id, 2);
    }

    #[test]
    fn tracks_survive_a_short_gap_and_are_dropped_after_max_missed() {
        let mut t = IouTracker::new(0.3, 2);
        t.update(&[det("car", [0.1, 0.1, 0.3, 0.5])]);
        t.update(&[]);
        t.update(&[]);
        assert_eq!(t.update(&[det("car", [0.1, 0.1, 0.3, 0.5])])[0].track_id, 1);
        for _ in 0..3 {
            t.update(&[]);
        }
        assert_eq!(t.update(&[det("car", [0.1, 0.1, 0.3, 0.5])])[0].track_id, 2, "ids are never reused");
    }

    #[test]
    fn iou_of_disjoint_identical_and_half_overlap() {
        assert_eq!(iou(&[0.0, 0.0, 1.0, 1.0], &[2.0, 2.0, 3.0, 3.0]), 0.0);
        assert!((iou(&[0.0, 0.0, 1.0, 1.0], &[0.0, 0.0, 1.0, 1.0]) - 1.0).abs() < 1e-6);
        assert!((iou(&[0.0, 0.0, 1.0, 1.0], &[0.5, 0.0, 1.5, 1.0]) - 1.0 / 3.0).abs() < 1e-5);
    }
}
