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

use crate::model::{BBox, Category, EventRecord, Snapshot, SourceInfo};
use crate::repo::{NewEvent, Repository};
use crate::tracker::Tracked;

pub struct EventDecider {
    repo: Arc<dyn Repository>,
    pub source: SourceInfo,
    min_hits: u32,
    group_window: Duration,
    hits: HashMap<(String, i32), u32>,
    reported: HashMap<(String, i32), DateTime<Utc>>,
    last_event: HashMap<String, DateTime<Utc>>,
}

impl EventDecider {
    pub fn new(repo: Arc<dyn Repository>, source: SourceInfo) -> Self {
        Self {
            repo, source, min_hits: 2, group_window: Duration::seconds(300), hits: HashMap::new(),
            reported: HashMap::new(), last_event: HashMap::new(),
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
            if t.score < self.source.min_confidence || hits < self.min_hits {
                continue;
            }
            by_label.entry(&t.label).or_default().push(t);
        }
        if self.hits.len() > 2000 {
            // long-running source: forget stale tracks
            let live: Vec<(String, i32)> = tracked.iter().map(|t| (t.label.clone(), t.track_id)).collect();
            self.hits.retain(|k, _| live.contains(k));
            self.reported.retain(|k, _| live.contains(k));
        }

        let mut labels: Vec<&str> = by_label.keys().copied().collect();
        labels.sort();
        let mut out = Vec::new();
        let mut snap: Option<Snapshot> = None;
        for label in labels {
            let cands = &by_label[label];
            let cat = &cats[label];
            if let Some(last) = self.last_event.get(label) {
                if (now - *last).num_milliseconds() < i64::from(cat.cooldown_s) * 1000 {
                    continue;
                }
            }
            let fresh: Vec<&&Tracked> = cands.iter().filter(|t| !self.reported.contains_key(&(t.label.clone(), t.track_id))).collect();
            let pool: Vec<&&Tracked> = if fresh.is_empty() { cands.iter().collect() } else { fresh };
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
                    out.push(rec);
                }
                // leave state untouched so the next frame retries
                Err(e) => error!("{}: writing event failed: {e:#}", self.source.code),
            }
        }
        out
    }

    fn write(&self, cat: &Category, t: &Tracked, snap: &Snapshot, now: DateTime<Utc>) -> Result<EventRecord> {
        let src = &self.source;
        let bbox = BBox::from_xyxy(t.bbox);
        let title = format!("{} detected at {}", cat.title, src.name);
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
                title: &title, detected_at: now,
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
        let dec = EventDecider::new(repo.clone(), source(&["person", "car"], 0.4));
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
}
