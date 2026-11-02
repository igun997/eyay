//! Snapshot burst + indication (docs/ARCHITECTURE.md "Snapshot burst and indication").
//!
//! After an event is written its track is sampled for `categories.burst_s` seconds; up to `burst_frames` snapshots are stored
//! in total (each with the track's current box and its offset). Driven from the source's own frame loop (no thread); a failing
//! DB write only costs that one sample. When the burst ends (or the stream is lost) the worker writes `burst_ms`, `burst_done`,
//! `indication_kind`, `indication`, a timeline row and evaluates `indication` rules.
use std::collections::HashMap;
use std::sync::Arc;

use anyhow::Result;
use chrono::{DateTime, Utc};
use tracing::{error, info, warn};

use crate::behavior::{ObjectHits, Rule, apply_flags, indication_flags, object_flags};
use crate::model::{BBox, Category, EventRecord, Snapshot, SourceInfo};
use crate::repo::Repository;
use crate::tracker::Tracked;

const MOVE_THRESHOLD: f64 = 0.08; // normalised centre displacement
const LINGER_MIN_MS: i32 = 3000;
const CROWD_MIN: usize = 3;
const MAX_PENDING: usize = 20;
/// Re-associate a lost track id with the nearest same-label box within this centre distance.
const FOLLOW_MAX_DIST: f64 = 0.25;

fn plural(label: &str) -> String {
    match label {
        "person" => "people".into(),
        "bus" => "buses".into(),
        l => format!("{l}s"),
    }
}

fn box_of(t: &Tracked) -> BBox {
    BBox::from_xyxy(t.bbox)
}

fn dist(a: (f64, f64), b: (f64, f64)) -> f64 {
    ((a.0 - b.0).powi(2) + (a.1 - b.1).powi(2)).sqrt()
}

/// The detection of our track in this frame. The IoU tracker hands out a new id when an object moves a lot between frames
/// (common at 1-3 fps), so when the id is gone fall back to the nearest same-label box to where we last saw it.
pub fn follow<'a>(tracked: &'a [Tracked], label: &str, track_id: i32, last: Option<&BBox>) -> Option<&'a Tracked> {
    let same: Vec<&Tracked> = tracked.iter().filter(|t| t.label == label).collect();
    if let Some(t) = same.iter().find(|t| t.track_id == track_id) {
        return Some(t);
    }
    let last = last?.centre();
    let best = same.into_iter().min_by(|a, b| dist(box_of(a).centre(), last).total_cmp(&dist(box_of(b).centre(), last)))?;
    (dist(box_of(best).centre(), last) <= FOLLOW_MAX_DIST).then_some(best)
}

/// -> (indication_kind, sentence). `boxes` has one entry per stored snapshot (None = track not visible).
pub fn classify_burst(title: &str, label: &str, boxes: &[Option<BBox>], burst_ms: i32, last_count: usize) -> (&'static str, String) {
    let visible: Vec<&BBox> = boxes.iter().flatten().collect();
    let secs = format!("{:.1} s", f64::from(burst_ms) / 1000.0);
    if visible.len() >= 2 {
        let disp = dist(visible[0].centre(), visible[visible.len() - 1].centre());
        if disp > MOVE_THRESHOLD {
            return ("moving", format!("{title} moving across the frame for {secs}"));
        }
        if burst_ms >= LINGER_MIN_MS {
            return ("lingering", format!("{title} lingering in view for {secs}"));
        }
    }
    if last_count >= CROWD_MIN {
        return ("crowd", format!("{last_count} {} in view", plural(label)));
    }
    let n = visible.len().max(1);
    ("appeared", format!("{title} appeared (seen in {n} snapshot{})", if n != 1 { "s" } else { "" }))
}

struct Pending {
    event_id: String,
    incident_id: Option<String>,
    label: String,
    title: String,
    track_id: i32,
    t0: DateTime<Utc>,
    frames: i32,
    burst_s: f64,
    taken: i32, // snapshot 0 is stored with the event
    boxes: Vec<Option<BBox>>,
    last_ms: i32,
    last_count: usize,
    errors: u32,
    last_box: Option<BBox>,
    /// best certainty of the objects the `object` rules look for, seen next to the person in any analysed frame of the burst
    objects: ObjectHits,
}

impl Pending {
    fn due_ms(&self) -> f64 {
        f64::from(self.taken) * self.burst_s * 1000.0 / f64::from(self.frames - 1)
    }
}

pub struct BurstManager {
    repo: Arc<dyn Repository>,
    pub source: SourceInfo,
    rules: Box<dyn Fn() -> Vec<Rule> + Send>,
    max_pending: usize,
    pending: HashMap<String, Pending>,
}

impl BurstManager {
    pub fn new(repo: Arc<dyn Repository>, source: SourceInfo, rules: Box<dyn Fn() -> Vec<Rule> + Send>) -> Self {
        Self { repo, source, rules, max_pending: MAX_PENDING, pending: HashMap::new() }
    }

    pub fn pending(&self) -> usize {
        self.pending.len()
    }

    /// Register the burst of a freshly written event (its snapshot 0 is already stored).
    pub fn start(&mut self, rec: &EventRecord, tracked: &[Tracked], cats: &HashMap<String, Category>, now: DateTime<Utc>) {
        let cat = cats.get(&rec.label);
        let frames = cat.map_or(1, |c| c.burst_frames.max(1));
        let burst_s = cat.map_or(0.0, |c| f64::from(c.burst_s));
        let track = tracked.iter().find(|t| t.label == rec.label && t.track_id == rec.track_id);
        let p = Pending {
            event_id: rec.event_id.clone(), incident_id: rec.incident_id.clone(), label: rec.label.clone(),
            title: cat.map_or_else(|| rec.label.clone(), |c| c.title.clone()), track_id: rec.track_id, t0: now, frames, burst_s,
            taken: 1, boxes: vec![track.map(box_of)], last_ms: 0,
            last_count: tracked.iter().filter(|t| t.label == rec.label).count(), errors: 0, last_box: track.map(box_of),
            objects: ObjectHits::new(),
        };
        let mut p = p;
        self.observe_objects(&mut p, tracked);
        if frames <= 1 || burst_s <= 0.0 || self.pending.len() >= self.max_pending {
            if self.pending.len() >= self.max_pending && frames > 1 && burst_s > 0.0 {
                warn!("{}: {} bursts pending, event {} keeps a single snapshot", self.source.code, self.pending.len(), &rec.event_id[..8.min(rec.event_id.len())]);
            }
            self.finalize(&p, now);
            return;
        }
        self.pending.insert(rec.event_id.clone(), p);
    }

    /// Look for the objects of `object` rules next to the person of this burst in the current frame (every analysed frame, not only
    /// when a follow-up photo is due, so a knife that shows for a moment is still noticed).
    fn observe_objects(&self, p: &mut Pending, tracked: &[Tracked]) {
        let rules = (self.rules)();
        for r in rules.iter().filter(|r| r.kind == "object" && r.applies_to.contains(&p.label)) {
            let (Some(person), Some(objs)) = (p.last_box, r.config.get("objects").and_then(|v| v.as_array())) else { continue };
            let margin = r.config.get("margin").and_then(|v| v.as_f64()).unwrap_or(0.3).clamp(0.0, 1.0);
            let (x0, x1) = (person.x - margin * person.w, person.x + person.w * (1.0 + margin));
            let (y0, y1) = (person.y - margin * person.h, person.y + person.h * (1.0 + margin));
            for t in tracked.iter().filter(|t| objs.iter().any(|o| o.as_str() == Some(t.label.as_str()))) {
                let (cx, cy) = box_of(t).centre();
                if (x0..=x1).contains(&cx) && (y0..=y1).contains(&cy) {
                    let best = p.objects.entry(r.key.clone()).or_insert((0.0, t.label.clone()));
                    if t.score > best.0 {
                        *best = (t.score, t.label.clone());
                    }
                }
            }
        }
    }

    /// Take the next due sample of every pending burst from the current frame.
    pub fn step(&mut self, tracked: &[Tracked], snapshot: &dyn Fn() -> Result<Snapshot>, now: DateTime<Utc>) {
        if self.pending.is_empty() {
            return;
        }
        let ids: Vec<String> = self.pending.keys().cloned().collect();
        for id in ids {
            let Some(mut p) = self.pending.remove(&id) else { continue };
            if let Some(t) = follow(tracked, &p.label, p.track_id, p.last_box.as_ref()) {
                p.track_id = t.track_id;
                p.last_box = Some(box_of(t));
            }
            self.observe_objects(&mut p, tracked);
            self.pending.insert(id, p);
        }
        let mut snap: Option<Snapshot> = None;
        let ids: Vec<String> = self.pending.keys().cloned().collect();
        for id in ids {
            let Some(mut p) = self.pending.remove(&id) else { continue };
            let elapsed = (now - p.t0).num_milliseconds() as f64;
            if elapsed < p.due_ms() {
                self.pending.insert(id, p);
                continue;
            }
            if snap.is_none() {
                match snapshot() {
                    Ok(s) => snap = Some(s),
                    Err(e) => {
                        error!("{}: burst snapshot failed: {e:#}", self.source.code);
                        self.pending.insert(id, p);
                        continue;
                    }
                }
            }
            let track = follow(tracked, &p.label, p.track_id, p.last_box.as_ref());
            let bbox = track.map(box_of);
            if let Some(t) = track {
                p.track_id = t.track_id;
                p.last_box = bbox;
            }
            let count = tracked.iter().filter(|t| t.label == p.label).count();
            let (offset, idx) = (elapsed as i32, p.taken as i16);
            let shot = snap.as_ref().expect("set above");
            let stored = self.repo.transaction(&mut |tx| tx.insert_snapshot(&p.event_id, shot, bbox.as_ref(), offset, idx));
            match stored {
                Ok(()) => {
                    p.boxes.push(bbox);
                    p.last_ms = offset;
                    p.last_count = count;
                }
                Err(e) => {
                    error!("{}: storing burst snapshot failed: {e:#}", self.source.code);
                    p.errors += 1;
                }
            }
            p.taken += 1;
            if p.taken >= p.frames || p.errors >= 3 {
                self.finalize(&p, now);
            } else {
                self.pending.insert(id, p);
            }
        }
    }

    /// Stream lost / worker stopping: finish every pending burst with the snapshots it has.
    pub fn flush(&mut self, now: DateTime<Utc>) {
        let all: Vec<Pending> = self.pending.drain().map(|(_, p)| p).collect();
        for p in all {
            self.finalize(&p, now);
        }
    }

    fn finalize(&self, p: &Pending, now: DateTime<Utc>) {
        let (kind, sentence) = classify_burst(&p.title, &p.label, &p.boxes, p.last_ms, p.last_count);
        let n = p.boxes.len();
        let rules = (self.rules)();
        let res = self.repo.transaction(&mut |tx| {
            tx.finalize_burst(&p.event_id, p.last_ms, kind, &sentence)?;
            if let Some(inc) = &p.incident_id {
                let detail = format!("{n} snapshot{} over {:.1} s", if n != 1 { "s" } else { "" }, f64::from(p.last_ms) / 1000.0);
                tx.add_timeline(inc, "system", &format!("Indication: {sentence}"), Some(&detail), Some(&p.event_id), now)?;
            }
            let mut flags = indication_flags(&rules, &p.label, kind, p.last_ms, &sentence);
            flags.extend(object_flags(&rules, &p.label, &p.objects));
            apply_flags(tx, &p.event_id, &flags, now)
        });
        match res {
            Ok(()) => {
                crate::automation::notify(&p.event_id);
                info!("{}: burst {} done: {sentence} ({n} snapshots, {} ms)", self.source.code, &p.event_id[..8.min(p.event_id.len())], p.last_ms);
            }
            Err(e) => error!("{}: finalizing burst of {} failed: {e:#}", self.source.code, &p.event_id[..8.min(p.event_id.len())]),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{FakeRepo, cat, source};
    use chrono::Duration;

    fn b(x: f64, y: f64) -> Option<BBox> {
        Some(BBox { x, y, w: 0.1, h: 0.2 })
    }

    fn tr(label: &str, id: i32, x: f32) -> Tracked {
        Tracked { track_id: id, label: label.into(), score: 0.9, bbox: [x, 0.1, x + 0.1, 0.3] }
    }

    fn snap() -> Result<Snapshot> {
        Ok(Snapshot { jpeg: vec![9], width: 4, height: 4 })
    }

    #[test]
    fn classify_moving_lingering_crowd_appeared() {
        assert_eq!(classify_burst("Person", "person", &[b(0.1, 0.1), b(0.5, 0.1)], 4000, 1).0, "moving");
        assert_eq!(classify_burst("Person", "person", &[b(0.1, 0.1), b(0.11, 0.1)], 5200, 1).0, "lingering");
        assert_eq!(classify_burst("Person", "person", &[b(0.1, 0.1), b(0.11, 0.1)], 1000, 3).1, "3 people in view");
        let (k, s) = classify_burst("Car", "car", &[b(0.1, 0.1)], 0, 1);
        assert_eq!((k, s.as_str()), ("appeared", "Car appeared (seen in 1 snapshot)"));
    }

    #[test]
    fn follow_falls_back_to_the_nearest_box_when_the_id_changes() {
        let last = BBox { x: 0.30, y: 0.1, w: 0.1, h: 0.2 };
        let frame = [tr("person", 7, 0.33), tr("person", 8, 0.8)];
        assert_eq!(follow(&frame, "person", 3, Some(&last)).unwrap().track_id, 7);
        assert!(follow(&[tr("person", 8, 0.9)], "person", 3, Some(&last)).is_none(), "too far to be the same object");
        assert_eq!(follow(&frame, "person", 8, None).unwrap().track_id, 8, "exact id wins");
    }

    fn rec(inc: Option<&str>, ev: &str) -> EventRecord {
        EventRecord { event_id: ev.into(), incident_id: inc.map(Into::into), incident_code: None, label: "person".into(), track_id: 1, grouped: false }
    }

    #[test]
    fn a_burst_takes_its_snapshots_on_schedule_then_writes_the_indication() {
        let repo = Arc::new(FakeRepo::default());
        let (ev, inc) = repo.seed_event("person", "HIGH", Some("HIGH"));
        let cats = HashMap::from([("person".to_string(), Category { burst_s: 6, burst_frames: 4, ..cat("person", "HIGH", true, 30) })]);
        let mut m = BurstManager::new(repo.clone(), source(&["person"], 0.4), Box::new(Vec::new));
        let t0 = Utc::now();
        m.start(&rec(inc.as_deref(), &ev), &[tr("person", 1, 0.1)], &cats, t0);
        assert_eq!(m.pending(), 1);
        m.step(&[tr("person", 1, 0.2)], &snap, t0 + Duration::milliseconds(500)); // not due yet (first at 2 s)
        assert_eq!(repo.state().snapshots.len(), 0);
        m.step(&[tr("person", 1, 0.2)], &snap, t0 + Duration::seconds(2));
        m.step(&[tr("person", 1, 0.4)], &snap, t0 + Duration::seconds(4));
        m.step(&[tr("person", 1, 0.7)], &snap, t0 + Duration::seconds(6));
        assert_eq!(m.pending(), 0);
        let s = repo.state();
        assert_eq!(s.snapshots.len(), 3);
        let e = &s.events[&ev];
        assert!(e.burst_done);
        assert_eq!(e.indication_kind.as_deref(), Some("moving"));
        assert_eq!(e.burst_ms, Some(6000));
        assert!(s.timeline.iter().any(|t| t.2.starts_with("Indication:")));
    }

    #[test]
    fn single_snapshot_categories_finish_immediately_and_flush_finishes_pending_ones() {
        let repo = Arc::new(FakeRepo::default());
        let (ev, _) = repo.seed_event("person", "HIGH", None);
        let single = HashMap::from([("person".to_string(), Category { burst_s: 0, burst_frames: 1, ..cat("person", "HIGH", true, 30) })]);
        let mut m = BurstManager::new(repo.clone(), source(&["person"], 0.4), Box::new(Vec::new));
        m.start(&rec(None, &ev), &[tr("person", 1, 0.1)], &single, Utc::now());
        assert!(repo.state().events[&ev].burst_done);

        let (ev2, _) = repo.seed_event("person", "HIGH", None);
        let multi = HashMap::from([("person".to_string(), Category { burst_s: 6, burst_frames: 4, ..cat("person", "HIGH", true, 30) })]);
        m.start(&rec(None, &ev2), &[tr("person", 1, 0.1)], &multi, Utc::now());
        assert!(!repo.state().events[&ev2].burst_done);
        m.flush(Utc::now());
        assert!(repo.state().events[&ev2].burst_done);
        assert_eq!(m.pending(), 0);
    }

    fn knife_rule() -> Rule {
        Rule {
            key: "weapon_object".into(), title: "Knife".into(), kind: "object".into(), question: None, applies_to: vec!["person".into()],
            threshold: 0.35, config: serde_json::json!({"objects": ["knife"], "margin": 0.3}), severity: "HIGH".into(), opens_incident: true, position: 0,
        }
    }

    fn obj(label: &str, x: f32, y: f32, score: f32) -> Tracked {
        Tracked { track_id: 99, label: label.into(), score, bbox: [x, y, x + 0.03, y + 0.03] }
    }

    #[test]
    fn a_knife_next_to_the_person_during_the_burst_raises_a_warning_sign_and_one_far_away_does_not() {
        for (knife, expect) in [(obj("knife", 0.35, 0.25, 0.5), true), (obj("knife", 0.9, 0.9, 0.9), false), (obj("knife", 0.35, 0.25, 0.2), false)] {
            let repo = Arc::new(FakeRepo::default());
            let (ev, inc) = repo.seed_event("person", "HIGH", Some("HIGH"));
            let cats = HashMap::from([("person".to_string(), Category { burst_s: 6, burst_frames: 4, ..cat("person", "HIGH", true, 30) })]);
            let mut m = BurstManager::new(repo.clone(), source(&["person"], 0.4), Box::new(|| vec![knife_rule()]));
            let t0 = Utc::now();
            let person = tr("person", 1, 0.3); // box x 0.3..0.4, y 0.1..0.3
            m.start(&rec(inc.as_deref(), &ev), std::slice::from_ref(&person), &cats, t0);
            // the knife shows for a single analysed frame between follow-up photos
            m.step(&[person.clone(), knife], &snap, t0 + Duration::milliseconds(700));
            for secs in [2, 4, 6] {
                m.step(std::slice::from_ref(&person), &snap, t0 + Duration::seconds(secs));
            }
            let s = repo.state();
            let flag = s.events[&ev].flags.iter().find(|f| f["rule"] == "weapon_object").expect("rule was checked");
            assert_eq!(flag["triggered"] == serde_json::json!(true), expect, "{flag}");
            assert_eq!(s.events[&ev].suspicious, expect);
        }
    }
}
