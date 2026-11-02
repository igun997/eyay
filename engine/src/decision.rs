//! Event decision logic (docs/ARCHITECTURE.md "Event flow" steps 1-3). Talks to a `Repository` only.
//!
//! An event is written for a label when the label is watched by the source, enabled in `categories` and has confidence
//! >= `source.min_confidence`; the track was seen in >= `min_hits` frames (drops one-frame flicker); and
//! `categories.cooldown_s` has passed since the last event of that label on this source. New tracks win over already
//! reported ones; a reported track is reported again once the cooldown elapses ("still there"). At most one event per
//! label per call.
use std::collections::HashMap;
use std::sync::Arc;

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use tracing::{error, info};

use crate::behavior::{Flag, apply_flags};
use crate::i18n;
use crate::model::{BBox, Category, EventRecord, Snapshot, SourceInfo};
use crate::repo::{NewEvent, Repository};
use crate::speed::SpeedReading;
use crate::tracker::Tracked;

/// Does this label have behaviour questions for the optional second AI check? (false when that check is off)
pub type NeedsCheck = Arc<dyn Fn(&str) -> bool + Send + Sync>;

/// What the camera AI says an event is, from its detector label.
pub fn classify_label(label: &str) -> &'static str {
    match label {
        "person" => "person",
        "car" | "truck" | "bus" | "motorcycle" | "bicycle" => "vehicle",
        _ => "other",
    }
}

pub struct EventDecider {
    repo: Arc<dyn Repository>,
    pub source: SourceInfo,
    needs_check: NeedsCheck,
    group_window: Duration,
    hits: HashMap<(String, i32), u32>,
    reported: HashMap<(String, i32), DateTime<Utc>>,
    last_event: HashMap<String, DateTime<Utc>>,
    /// latest event written for a track, so a speed that arrives later lands on it
    events_of: HashMap<(String, i32), String>,
    /// last speed stored per event (km/h)
    stored_speed: HashMap<String, f32>,
}

impl EventDecider {
    pub fn new(repo: Arc<dyn Repository>, source: SourceInfo, needs_check: NeedsCheck) -> Self {
        Self {
            repo, source, needs_check, group_window: Duration::seconds(300), hits: HashMap::new(), reported: HashMap::new(),
            last_event: HashMap::new(), events_of: HashMap::new(), stored_speed: HashMap::new(),
        }
    }

    pub fn process(
        &mut self, tracked: &[Tracked], cats: &HashMap<String, Category>, snapshot: &dyn Fn() -> Result<Snapshot>, now: DateTime<Utc>,
    ) -> Vec<EventRecord> {
        let mut by_label: HashMap<&str, Vec<&Tracked>> = HashMap::new();
        for t in tracked {
            let key = (t.label.clone(), t.track_id);
            let hits = {
                let h = self.hits.entry(key).or_insert(0);
                *h += 1;
                *h
            };
            let cat = cats.get(&t.label);
            if !self.source.labels.contains(&t.label) || cat.is_none_or(|c| !c.enabled) {
                continue;
            }
            if t.score < self.source.min_confidence || hits < self.source.config.tracker.min_hits {
                continue;
            }
            by_label.entry(&t.label).or_default().push(t);
        }
        if self.hits.len() > 2000 {
            // long-running source: forget stale tracks
            let live: Vec<(String, i32)> = tracked.iter().map(|t| (t.label.clone(), t.track_id)).collect();
            self.hits.retain(|k, _| live.contains(k));
            self.reported.retain(|k, _| live.contains(k));
            self.events_of.retain(|k, _| live.contains(k));
        }

        let mut labels: Vec<&str> = by_label.keys().copied().collect();
        labels.sort();
        let mut out = Vec::new();
        let mut snap: Option<Snapshot> = None;
        for label in labels {
            let cands = &by_label[label];
            let cat = &cats[label];
            let fresh: Vec<&&Tracked> = cands.iter().filter(|t| !self.reported.contains_key(&(t.label.clone(), t.track_id))).collect();
            let mut only_fresh = false;
            if let Some(last) = self.last_event.get(label) {
                let since_ms = (now - *last).num_milliseconds();
                if since_ms < i64::from(cat.cooldown_s) * 1000 {
                    // cooling down: only a track nobody has reported yet may break through, and only after the configured gap
                    let gap_ms = (f64::from(self.source.config.events.new_track_gap_s) * 1000.0) as i64;
                    if gap_ms <= 0 || since_ms < gap_ms || fresh.is_empty() {
                        continue;
                    }
                    only_fresh = true;
                }
            }
            let pool: Vec<&&Tracked> = if fresh.is_empty() && !only_fresh { cands.iter().collect() } else { fresh };
            let best = pool.into_iter().max_by(|a, b| a.score.total_cmp(&b.score)).expect("non-empty");
            if snap.is_none() {
                match snapshot() {
                    Ok(s) => snap = Some(s),
                    Err(e) => {
                        error!("{}: snapshot failed: {e:#}", self.source.code);
                        break;
                    }
                }
            }
            match self.write(cat, best, snap.as_ref().expect("set above"), now) {
                Ok(rec) => {
                    self.last_event.insert(label.to_string(), now);
                    self.reported.insert((best.label.clone(), best.track_id), now);
                    self.events_of.insert((best.label.clone(), best.track_id), rec.event_id.clone());
                    out.push(rec);
                }
                // leave state untouched so the next frame retries
                Err(e) => error!("{}: writing event failed: {e:#}", self.source.code),
            }
        }
        out
    }

    /// Store the speed of tracks that have an event (and raise the `speeding` indicator above the limit). A track that is over the limit
    /// but has no event yet gets one of its own right away, even while the category's quiet period runs: the fast vehicle is exactly
    /// what should be reported, and the track that happened to win the last event slot (often something parked) is not it. Only
    /// changes of 1 km/h or more are written. Returns the events created here so the caller can start their follow-up photos.
    pub fn apply_speeds(
        &mut self, readings: &[(String, SpeedReading)], tracked: &[Tracked], cats: &HashMap<String, Category>,
        snapshot: &dyn Fn() -> Result<Snapshot>, now: DateTime<Utc>,
    ) -> Vec<EventRecord> {
        let cal = self.source.config.calibration.clone();
        let mut created = Vec::new();
        let mut snap: Option<Snapshot> = None;
        for (label, r) in readings {
            let key = (label.clone(), r.track_id);
            let over = r.kmh > cal.speed_limit_kmh;
            if over && !self.events_of.contains_key(&key) {
                let (cat, track) = (cats.get(label), tracked.iter().find(|t| &t.label == label && t.track_id == r.track_id));
                if let (Some(cat), Some(track)) = (cat, track) {
                    if cat.enabled && self.source.labels.contains(label) && track.score >= self.source.min_confidence {
                        if snap.is_none() {
                            snap = snapshot().ok();
                        }
                        if let Some(shot) = snap.as_ref() {
                            match self.write(cat, track, shot, now) {
                                Ok(rec) => {
                                    self.last_event.insert(label.clone(), now);
                                    self.reported.insert(key.clone(), now);
                                    self.events_of.insert(key.clone(), rec.event_id.clone());
                                    created.push(rec);
                                }
                                Err(e) => error!("{}: writing the speeding event failed: {e:#}", self.source.code),
                            }
                        }
                    }
                }
            }
            let Some(event_id) = self.events_of.get(&key).cloned() else { continue };
            if self.stored_speed.get(&event_id).is_some_and(|s| (s - r.kmh).abs() < 1.0) {
                continue;
            }
            let flag = Flag {
                rule: "speeding".into(), title: i18n::speeding_title(i18n::lang()), prob: None, threshold: 0.0, severity: cal.speed_severity.clone(),
                triggered: true, opens_incident: true,
                detail: Some(i18n::speeding_detail(i18n::lang(), r.kmh, cal.speed_limit_kmh)),
            };
            let res = self.repo.transaction(&mut |tx| {
                tx.set_event_speed(&event_id, r.kmh)?;
                if over {
                    apply_flags(tx, &event_id, std::slice::from_ref(&flag), now)?;
                }
                Ok(())
            });
            match res {
                Ok(()) => {
                    self.stored_speed.insert(event_id.clone(), r.kmh);
                    crate::automation::notify(&event_id);
                    info!("{}: event {} speed {:.0} km/h{}", self.source.code, &event_id[..8.min(event_id.len())], r.kmh, if over { " (over limit)" } else { "" });
                }
                Err(e) => error!("{}: storing speed failed: {e:#}", self.source.code),
            }
        }
        created
    }

    fn write(&self, cat: &Category, t: &Tracked, snap: &Snapshot, now: DateTime<Utc>) -> Result<EventRecord> {
        let src = &self.source;
        let bbox = BBox::from_xyxy(t.bbox);
        let title = i18n::detected_at(i18n::lang(), &cat.title, &src.name);
        let mut rec: Option<EventRecord> = None;
        self.repo.transaction(&mut |tx| {
            let (mut incident_id, mut incident_code, mut grouped) = (None, None, false);
            if cat.opens_incident {
                incident_id = tx.find_open_incident(&src.id, &t.label, now - self.group_window)?;
                if incident_id.is_none() {
                    let (id, code) = tx.create_incident(&title, &cat.severity, &src.id, &t.label, now)?;
                    incident_id = Some(id);
                    incident_code = Some(code);
                } else {
                    grouped = true;
                }
            }
            let event_id = tx.insert_event(&NewEvent {
                source_id: &src.id, incident_id: incident_id.as_deref(), label: &t.label,
                confidence: (t.score * 10_000.0).round() / 10_000.0, severity: &cat.severity, track_id: t.track_id, bbox,
                title: &title, detected_at: now, cls_label: classify_label(&t.label),
                pipeline: if (self.needs_check)(&t.label) { "queued" } else { "ready" },
            })?;
            tx.insert_snapshot(&event_id, snap, Some(&bbox), 0, 0)?;
            if let Some(inc) = &incident_id {
                let detail = format!("{} · {} {:.2} · track {}", src.code, t.label.to_uppercase(), t.score, t.track_id);
                tx.add_timeline(inc, "camera", &title, Some(&detail), Some(&event_id), now)?;
            }
            rec = Some(EventRecord { event_id, incident_id, incident_code, label: t.label.clone(), track_id: t.track_id, grouped });
            Ok(())
        })?;
        let rec = rec.expect("transaction ran");
        crate::automation::notify(&rec.event_id);
        info!(
            "{}: event {} {} {:.2} track={} incident={}{}", src.code, &rec.event_id[..8.min(rec.event_id.len())], t.label, t.score,
            t.track_id, rec.incident_code.as_deref().or(rec.incident_id.as_deref()).unwrap_or("-"), if rec.grouped { " (grouped)" } else { "" }
        );
        Ok(rec)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{FakeRepo, cat, source};

    fn tr(label: &str, id: i32, score: f32) -> Tracked {
        Tracked { track_id: id, label: label.into(), score, bbox: [0.1, 0.1, 0.3, 0.5] }
    }

    fn snap() -> Result<Snapshot> {
        Ok(Snapshot { jpeg: vec![1, 2, 3], width: 10, height: 10 })
    }

    fn setup() -> (Arc<FakeRepo>, EventDecider, HashMap<String, Category>) {
        let repo = Arc::new(FakeRepo::default());
        let dec = EventDecider::new(repo.clone(), source(&["person", "car"], 0.4), Arc::new(|l: &str| l == "person"));
        let cats = HashMap::from([
            ("person".to_string(), cat("person", "HIGH", true, 30)),
            ("car".to_string(), cat("car", "MEDIUM", true, 60)),
            ("bus".to_string(), cat("bus", "LOW", false, 60)),
        ]);
        (repo, dec, cats)
    }

    #[test]
    fn a_track_must_be_seen_twice_before_it_becomes_an_event() {
        let (repo, mut d, cats) = setup();
        let now = Utc::now();
        assert!(d.process(&[tr("person", 1, 0.9)], &cats, &snap, now).is_empty());
        let r = d.process(&[tr("person", 1, 0.9)], &cats, &snap, now);
        assert_eq!(r.len(), 1);
        let s = repo.state();
        assert_eq!(s.events.len(), 1);
        assert_eq!(s.incidents.len(), 1);
        assert_eq!(s.snapshots.len(), 1);
        assert_eq!(s.timeline.len(), 1);
    }

    #[test]
    fn unwatched_disabled_and_low_confidence_detections_are_ignored() {
        let (repo, mut d, mut cats) = setup();
        let now = Utc::now();
        cats.get_mut("car").unwrap().enabled = false;
        for _ in 0..3 {
            d.process(&[tr("bus", 1, 0.9), tr("car", 2, 0.9), tr("person", 3, 0.2)], &cats, &snap, now);
        }
        assert!(repo.state().events.is_empty());
    }

    #[test]
    fn cooldown_blocks_a_second_event_until_it_elapses_and_the_same_incident_is_grouped() {
        let (repo, mut d, cats) = setup();
        let t0 = Utc::now();
        d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0);
        assert_eq!(d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0).len(), 1);
        assert!(d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0 + Duration::seconds(10)).is_empty());
        let r = d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0 + Duration::seconds(31));
        assert_eq!(r.len(), 1);
        assert!(r[0].grouped, "second event of the same label attaches to the open incident");
        let s = repo.state();
        assert_eq!((s.events.len(), s.incidents.len()), (2, 1));
    }

    #[test]
    fn categories_that_do_not_open_incidents_still_write_events() {
        let (repo, mut d, mut cats) = setup();
        cats.insert("person".into(), cat("person", "HIGH", false, 30));
        let now = Utc::now();
        d.process(&[tr("person", 1, 0.9)], &cats, &snap, now);
        d.process(&[tr("person", 1, 0.9)], &cats, &snap, now);
        let s = repo.state();
        assert_eq!((s.events.len(), s.incidents.len()), (1, 0));
    }

    #[test]
    fn a_new_track_wins_over_an_already_reported_one() {
        let (repo, mut d, cats) = setup();
        let t0 = Utc::now();
        d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0);
        d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0);
        // track 2 appears while track 1 is still cooling down, so both are eligible once the cooldown has passed
        for secs in [10, 20] {
            assert!(d.process(&[tr("person", 1, 0.95), tr("person", 2, 0.5)], &cats, &snap, t0 + Duration::seconds(secs)).is_empty());
        }
        let r = d.process(&[tr("person", 1, 0.95), tr("person", 2, 0.5)], &cats, &snap, t0 + Duration::seconds(40));
        assert_eq!(r[0].track_id, 2);
        assert_eq!(repo.state().events.len(), 2);
    }

    #[test]
    fn speed_lands_on_the_tracks_event_and_only_over_the_limit_raises_the_indicator() {
        let (repo, mut d, cats) = setup();
        let now = Utc::now();
        d.source.config.calibration.speed_limit_kmh = 50.0;
        d.process(&[tr("car", 7, 0.9)], &cats, &snap, now);
        let r = d.process(&[tr("car", 7, 0.9)], &cats, &snap, now);
        let ev = r[0].event_id.clone();
        // 40 km/h: stored, no indicator
        d.apply_speeds(&[("car".into(), SpeedReading { track_id: 7, kmh: 40.0 })], &[tr("car", 7, 0.9)], &cats, &snap, now);
        assert_eq!(repo.state().events[&ev].speed_kmh, Some(40.0));
        assert!(!repo.state().events[&ev].suspicious);
        // within 1 km/h of what is stored: nothing written
        d.apply_speeds(&[("car".into(), SpeedReading { track_id: 7, kmh: 40.5 })], &[tr("car", 7, 0.9)], &cats, &snap, now);
        assert_eq!(repo.state().events[&ev].speed_kmh, Some(40.0));
        // 70 km/h: stored and flagged
        d.apply_speeds(&[("car".into(), SpeedReading { track_id: 7, kmh: 70.0 })], &[tr("car", 7, 0.9)], &cats, &snap, now);
        let s = repo.state();
        assert_eq!(s.events[&ev].speed_kmh, Some(70.0));
        assert!(s.events[&ev].suspicious);
        assert!(s.events[&ev].flags.iter().any(|f| f["rule"] == "speeding"));
        // a slow track without an event is ignored (it is not worth an event of its own)
        d.apply_speeds(&[("car".into(), SpeedReading { track_id: 98, kmh: 30.0 })], &[tr("car", 98, 0.9)], &cats, &snap, now);
        assert_eq!(repo.state().events.len(), 1);
        // a fast track without an event gets its own, right away, even inside the car quiet period
        let made = d.apply_speeds(&[("car".into(), SpeedReading { track_id: 99, kmh: 90.0 })], &[tr("car", 99, 0.9)], &cats, &snap, now);
        assert_eq!(made.len(), 1);
        let s = repo.state();
        assert_eq!(s.events.len(), 2);
        let fast = s.events.values().find(|e| e.speed_kmh == Some(90.0)).expect("event with the speed");
        assert!(fast.suspicious && fast.flags.iter().any(|f| f["rule"] == "speeding"));
    }

    #[test]
    fn a_new_track_may_break_the_cooldown_after_the_gap_but_a_reported_one_may_not() {
        let (repo, mut d, cats) = setup();
        d.source.config.events.new_track_gap_s = 5.0;
        let t0 = Utc::now();
        d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0);
        assert_eq!(d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0).len(), 1);
        let two = [tr("person", 1, 0.9), tr("person", 2, 0.8)];
        d.process(&two, &cats, &snap, t0 + Duration::seconds(2)); // track 2 seen once, still inside the gap
        assert!(d.process(&two, &cats, &snap, t0 + Duration::seconds(3)).is_empty(), "gap of 5 s has not passed");
        let r = d.process(&two, &cats, &snap, t0 + Duration::seconds(6));
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].track_id, 2, "only the new track breaks through the 30 s cooldown");
        assert!(d.process(&[tr("person", 1, 0.9)], &cats, &snap, t0 + Duration::seconds(12)).is_empty(), "a reported track waits for the cooldown");
        assert_eq!(repo.state().events.len(), 2);
    }

    #[test]
    fn the_camera_ai_classifies_every_event_and_only_queues_those_with_questions_to_answer() {
        let (repo, mut d, cats) = setup();
        let now = Utc::now();
        for label in ["person", "car"] {
            d.process(&[tr(label, 1, 0.9)], &cats, &snap, now);
            d.process(&[tr(label, 1, 0.9)], &cats, &snap, now);
        }
        let s = repo.state();
        let by = |l: &str| s.events.values().find(|e| e.label == l).unwrap().clone();
        assert_eq!((by("person").cls_label.as_deref(), by("person").pipeline.as_str()), (Some("person"), "queued"));
        assert_eq!((by("car").cls_label.as_deref(), by("car").pipeline.as_str()), (Some("vehicle"), "ready"));
        assert_eq!(classify_label("dog"), "other");
    }
}
