//! Speed of tracked objects from calibrated geometry (port of the estimator in jenggo/poc-yolo-coco, adapted to normalised
//! coordinates and to the low analysis rate of a server-side worker).
//!
//! Time always comes from the stream's frame counter divided by its frame rate, never the wall clock, so a slow machine reports
//! the same speeds as a fast one for the same frames. The measured point is the bottom-centre of the box (where the object
//! touches the road). A speed is an estimate for a person to review: `distance_m` scales every speed linearly.
use std::collections::HashMap;

use anyhow::{Result, bail};
use tracing::debug;

use crate::config::{Axis, CalibrationCfg, Method};
use crate::tracker::Tracked;

const MPS_TO_KMH: f64 = 3.6;
/// Seconds of stream time the homography displacement is measured over.
const HOMOGRAPHY_SPAN: f64 = 0.5;
const HOMOGRAPHY_MAX_SAMPLES: usize = 64;
/// Fraction of the span that must be filled before a speed is reported; below it box jitter / a short interval is noise.
const HOMOGRAPHY_MIN_FILL: f64 = 0.9;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SpeedReading {
    pub track_id: i32,
    pub kmh: f32,
}

#[derive(Default)]
struct LineState {
    last: Option<(f64, f64)>, // (time, position)
    /// the line crossed first: (0 = line1 / 1 = line2, time, direction of motion +1 / -1). Crossing the same line again
    /// (jitter, an object lingering on the line) replaces it, so a standing object does not start the clock early.
    start: Option<(u8, f64, i8)>,
    end: Option<f64>,
}

#[derive(Default)]
struct HomographyState {
    samples: Vec<(f64, [f64; 2])>,
    smoothed: Option<f64>,
}

pub struct SpeedEstimator {
    cfg: CalibrationCfg,
    homography: Option<[[f64; 3]; 3]>,
    lines: HashMap<(String, i32), LineState>,
    homographies: HashMap<(String, i32), HomographyState>,
    readings: HashMap<(String, i32), f32>,
}

impl SpeedEstimator {
    /// `None` when the calibration is off or cannot be solved.
    pub fn new(cfg: &CalibrationCfg) -> Option<Result<Self>> {
        let homography = match cfg.method {
            Method::None => return None,
            Method::Line => None,
            Method::Homography => match solve_homography(&cfg.pixels, &cfg.world_m) {
                Ok(h) => Some(h),
                Err(e) => return Some(Err(e)),
            },
        };
        Some(Ok(Self { cfg: cfg.clone(), homography, lines: HashMap::new(), homographies: HashMap::new(), readings: HashMap::new() }))
    }

    /// Update with the tracks of one analysed frame at stream time `t` (seconds). Returns the reading of every track that has one.
    pub fn update(&mut self, tracked: &[Tracked], t: f64) -> Vec<(String, SpeedReading)> {
        for tr in tracked {
            let key = (tr.label.clone(), tr.track_id);
            let (x, y) = (f64::from((tr.bbox[0] + tr.bbox[2]) / 2.0), f64::from(tr.bbox[3]));
            let kmh = match self.cfg.method {
                Method::Line => self.update_line(&key, x, y, t),
                Method::Homography => self.update_homography(&key, x, y, t),
                Method::None => None,
            };
            if let Some(k) = kmh {
                self.readings.insert(key, k as f32);
            }
        }
        // forget tracks that are gone
        let live: Vec<(String, i32)> = tracked.iter().map(|t| (t.label.clone(), t.track_id)).collect();
        if self.lines.len() + self.homographies.len() > 500 {
            self.lines.retain(|k, _| live.contains(k));
            self.homographies.retain(|k, _| live.contains(k));
            self.readings.retain(|k, _| live.contains(k));
        }
        live.iter().filter_map(|k| self.readings.get(k).map(|&kmh| (k.0.clone(), SpeedReading { track_id: k.1, kmh }))).collect()
    }

    fn update_line(&mut self, key: &(String, i32), x: f64, y: f64, t: f64) -> Option<f64> {
        let pos = match self.cfg.line_axis {
            Axis::X => x,
            Axis::Y => y,
        };
        let lines = [f64::from(self.cfg.line1), f64::from(self.cfg.line2)];
        let st = self.lines.entry(key.clone()).or_default();
        let prev = st.last.replace((t, pos));
        debug!("speed: {} #{} t={t:.2} pos={pos:.3} (lines {:.2}/{:.2}) prev={prev:?} start={:?} end={:?}", key.0, key.1, lines[0], lines[1], st.start, st.end);
        if st.end.is_none() {
            if let Some((pt, pp)) = prev.filter(|(_, pp)| *pp != pos) {
                let dir: i8 = if pos > pp { 1 } else { -1 };
                for (i, line) in lines.iter().enumerate() {
                    if (pp - line).signum() == (pos - line).signum() {
                        continue; // did not cross this line between the two samples
                    }
                    let at = pt + (line - pp) / (pos - pp) * (t - pt); // linear interpolation between the analysed frames
                    match st.start {
                        None => st.start = Some((i as u8, at, dir)),
                        Some((si, _, _)) if si == i as u8 => st.start = Some((i as u8, at, dir)), // crossed the first line again
                        Some((_, t0, sdir)) if sdir == dir && at > t0 => st.end = Some(at),         // the other line, same direction
                        Some(_) => st.start = Some((i as u8, at, dir)),                              // came back across: start over
                    }
                }
            }
        }
        let (t0, t1) = (st.start?.1, st.end?);
        let elapsed = (t1 - t0).abs();
        (elapsed > 0.0).then(|| f64::from(self.cfg.distance_m) / elapsed * MPS_TO_KMH)
    }

    fn update_homography(&mut self, key: &(String, i32), x: f64, y: f64, t: f64) -> Option<f64> {
        let h = self.homography?;
        let road = project(&h, x, y);
        let st = self.homographies.entry(key.clone()).or_default();
        st.samples.push((t, road));
        while st.samples.len() > 2 && st.samples[0].0 < t - HOMOGRAPHY_SPAN {
            st.samples.remove(0);
        }
        while st.samples.len() > HOMOGRAPHY_MAX_SAMPLES {
            st.samples.remove(0);
        }
        let (t0, p0) = *st.samples.first()?;
        let elapsed = t - t0;
        if elapsed < HOMOGRAPHY_SPAN * HOMOGRAPHY_MIN_FILL {
            return None;
        }
        let speed = ((road[0] - p0[0]).powi(2) + (road[1] - p0[1]).powi(2)).sqrt() / elapsed * MPS_TO_KMH;
        if !speed.is_finite() {
            return None;
        }
        let s = f64::from(self.cfg.smoothing).clamp(0.0, 1.0);
        let smoothed = st.smoothed.map_or(speed, |p| p + s * (speed - p));
        st.smoothed = Some(smoothed);
        Some(smoothed)
    }
}

/// Solve the 3x3 homography from four image/world correspondences (DLT, h33 = 1, Gauss-Jordan with partial pivoting).
pub fn solve_homography(pixels: &[[f32; 2]], world: &[[f32; 2]]) -> Result<[[f64; 3]; 3]> {
    if pixels.len() != 4 || world.len() != 4 {
        bail!("homography needs exactly 4 pixel points and 4 world points, got {} and {}", pixels.len(), world.len());
    }
    let mut a = [[0.0f64; 9]; 8];
    for i in 0..4 {
        let (x, y) = (f64::from(pixels[i][0]), f64::from(pixels[i][1]));
        let (u, v) = (f64::from(world[i][0]), f64::from(world[i][1]));
        a[2 * i] = [x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, u];
        a[2 * i + 1] = [0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, v];
    }
    for col in 0..8 {
        let mut pivot = col;
        for row in col + 1..8 {
            if a[row][col].abs() > a[pivot][col].abs() {
                pivot = row;
            }
        }
        if a[pivot][col].abs() < 1e-9 {
            bail!("homography points are degenerate: no unique solution exists");
        }
        a.swap(col, pivot);
        let scale = a[col][col];
        for k in 0..9 {
            a[col][k] /= scale;
        }
        for row in 0..8 {
            if row != col && a[row][col] != 0.0 {
                let f = a[row][col];
                for k in 0..9 {
                    a[row][k] -= f * a[col][k];
                }
            }
        }
    }
    Ok([[a[0][8], a[1][8], a[2][8]], [a[3][8], a[4][8], a[5][8]], [a[6][8], a[7][8], 1.0]])
}

pub fn project(h: &[[f64; 3]; 3], x: f64, y: f64) -> [f64; 2] {
    let w = h[2][0] * x + h[2][1] * y + h[2][2];
    if w.abs() < 1e-12 {
        return [f64::NAN, f64::NAN];
    }
    [(h[0][0] * x + h[0][1] * y + h[0][2]) / w, (h[1][0] * x + h[1][1] * y + h[1][2]) / w]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::EngineConfig;
    use serde_json::json;

    fn tr(id: i32, x: f32, y: f32) -> Tracked {
        // box centred on x with its bottom at y
        Tracked { track_id: id, label: "car".into(), score: 0.9, bbox: [x - 0.05, y - 0.1, x + 0.05, y] }
    }

    fn cfg(v: serde_json::Value) -> CalibrationCfg {
        EngineConfig::resolve(&json!({}), &json!({ "calibration": v })).calibration
    }

    #[test]
    fn line_crossing_gives_distance_over_elapsed_time_even_between_frames() {
        let c = cfg(json!({"method": "line", "line_axis": "x", "line1": 0.4, "line2": 0.7, "distance_m": 20}));
        let mut e = SpeedEstimator::new(&c).unwrap().unwrap();
        // moves 0.1 of the frame per second: crosses 0.4 at t=1.0 (between samples) and 0.7 at t=4.0 -> 20 m / 3 s = 24 km/h
        let mut last = Vec::new();
        for (t, x) in [(0.0, 0.3), (0.5, 0.35), (1.0, 0.4001), (2.0, 0.5), (3.5, 0.65), (4.5, 0.75)] {
            last = e.update(&[tr(1, x, 0.8)], t);
        }
        let k = last[0].1.kmh;
        assert!((k - 24.0).abs() < 0.2, "{k}");
    }

    #[test]
    fn no_speed_before_both_lines_are_crossed() {
        let c = cfg(json!({"method": "line", "line1": 0.4, "line2": 0.7}));
        let mut e = SpeedEstimator::new(&c).unwrap().unwrap();
        e.update(&[tr(1, 0.3, 0.8)], 0.0);
        assert!(e.update(&[tr(1, 0.5, 0.8)], 1.0).is_empty(), "only line 1 crossed");
    }

    #[test]
    fn vertical_axis_measures_up_down_motion_of_the_bottom_edge() {
        let c = cfg(json!({"method": "line", "line_axis": "y", "line1": 0.5, "line2": 0.8, "distance_m": 10}));
        let mut e = SpeedEstimator::new(&c).unwrap().unwrap();
        let mut last = Vec::new();
        for (t, y) in [(0.0, 0.4), (1.0, 0.6), (2.0, 0.9)] {
            last = e.update(&[tr(1, 0.5, y)], t);
        }
        // crosses 0.5 at t=0.5, 0.8 at t=1.667 -> 10 m / 1.1667 s = 30.86 km/h
        assert!((last[0].1.kmh - 30.86).abs() < 0.5, "{}", last[0].1.kmh);
    }

    #[test]
    fn homography_recovers_a_known_scale() {
        // image square (0.2..0.8) maps to a 10 m x 10 m road patch
        let c = cfg(json!({"method": "homography", "smoothing": 1.0,
            "pixels": [[0.2, 0.8], [0.2, 0.2], [0.8, 0.2], [0.8, 0.8]], "world_m": [[0, 0], [0, 10], [10, 10], [10, 0]]}));
        let mut e = SpeedEstimator::new(&c).unwrap().unwrap();
        let mut last = Vec::new();
        // moves 0.06 of the frame (= 1 m) per 0.25 s -> 4 m/s = 14.4 km/h
        for i in 0..8 {
            let t = f64::from(i) * 0.25;
            last = e.update(&[tr(1, 0.3 + 0.06 * i as f32, 0.7)], t);
        }
        assert!((last[0].1.kmh - 14.4).abs() < 0.3, "{}", last[0].1.kmh);
    }

    #[test]
    fn degenerate_points_are_rejected_and_off_means_none() {
        assert!(solve_homography(&[[0.0, 0.0]; 4], &[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0]]).is_err());
        assert!(SpeedEstimator::new(&cfg(json!({"method": "none"}))).is_none());
    }

    #[test]
    fn an_object_standing_on_the_first_line_does_not_start_the_clock_early() {
        let c = cfg(json!({"method": "line", "line_axis": "x", "line1": 0.4, "line2": 0.7, "distance_m": 20}));
        let mut e = SpeedEstimator::new(&c).unwrap().unwrap();
        // jitters across line 1 for a while, then walks to line 2: 0.4 at t=10, 0.7 at t=13 -> 20 m / 3 s = 24 km/h
        let mut last = Vec::new();
        for (t, x) in [(0.0, 0.39), (1.0, 0.41), (2.0, 0.39), (3.0, 0.41), (9.0, 0.39), (10.5, 0.45), (12.0, 0.55), (13.5, 0.70), (14.0, 0.75)] {
            last = e.update(&[tr(1, x, 0.8)], t);
        }
        // last crossing of line 1 (upwards) lies between t=9 (0.39) and t=10.5 (0.45): at t=9.25 ; line 2 at ~13.5
        let k = last[0].1.kmh;
        assert!((k - 20.0 / (13.5 - 9.25) * 3.6).abs() < 1.5, "{k}");
    }

    #[test]
    fn motion_from_line_two_to_line_one_is_measured_too_and_a_u_turn_starts_over() {
        let c = cfg(json!({"method": "line", "line_axis": "x", "line1": 0.4, "line2": 0.7, "distance_m": 30}));
        let mut e = SpeedEstimator::new(&c).unwrap().unwrap();
        let mut last = Vec::new();
        for (t, x) in [(0.0, 0.8), (1.0, 0.6), (2.0, 0.5), (3.0, 0.3)] {
            last = e.update(&[tr(1, x, 0.8)], t);
        }
        // crosses 0.7 at t=0.5 and 0.4 at t=2.5: 30 m / 2 s = 54 km/h
        assert!((last[0].1.kmh - 54.0).abs() < 0.5, "{}", last[0].1.kmh);

        let mut e2 = SpeedEstimator::new(&c).unwrap().unwrap();
        let mut got = Vec::new();
        for (t, x) in [(0.0, 0.3), (1.0, 0.5), (2.0, 0.3), (3.0, 0.5)] {
            got = e2.update(&[tr(2, x, 0.8)], t);
        }
        assert!(got.is_empty(), "going back and forth over line 1 is not a measurement");
    }
}
