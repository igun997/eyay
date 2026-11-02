//! Database access behind two small traits so the decision logic can be tested with an in-memory fake.
//! `PgRepository` is the real thing: synchronous `postgres` clients from a tiny pool (no async runtime in the worker).
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use postgres::{Client, NoTls, Transaction};
use serde_json::Value;
use tracing::info;

use crate::behavior::{EventState, Flag, Rule, apply_flags};
use crate::classifier::{Classification, ClassifyJob};
use crate::model::{BBox, Category, Snapshot, SourceInfo};

pub struct NewEvent<'a> {
    pub source_id: &'a str,
    pub incident_id: Option<&'a str>,
    pub label: &'a str,
    pub confidence: f32,
    pub severity: &'a str,
    pub track_id: i32,
    pub bbox: BBox,
    pub title: &'a str,
    pub detected_at: DateTime<Utc>,
}

pub trait Tx {
    fn find_open_incident(&mut self, source_id: &str, label: &str, since: DateTime<Utc>) -> Result<Option<String>>;
    /// -> (id, code)
    fn create_incident(&mut self, title: &str, severity: &str, source_id: &str, label: &str, opened_at: DateTime<Utc>) -> Result<(String, String)>;
    fn insert_event(&mut self, e: &NewEvent) -> Result<String>;
    fn insert_snapshot(&mut self, event_id: &str, snap: &Snapshot, bbox: Option<&BBox>, offset_ms: i32, idx: i16) -> Result<()>;
    fn finalize_burst(&mut self, event_id: &str, burst_ms: i32, kind: &str, indication: &str) -> Result<()>;
    // behaviour rules (see behavior::apply_flags)
    fn lock_event(&mut self, event_id: &str) -> Result<Option<EventState>>;
    fn set_event_flags(&mut self, event_id: &str, flags: &[Value], suspicious: bool, severity: &str) -> Result<()>;
    fn set_incident_severity(&mut self, incident_id: &str, severity: &str) -> Result<()>;
    fn link_event_incident(&mut self, event_id: &str, incident_id: &str) -> Result<()>;
    fn add_timeline(&mut self, incident_id: &str, kind: &str, title: &str, detail: Option<&str>, event_id: Option<&str>, at: DateTime<Utc>) -> Result<()>;
}

pub trait Repository: Send + Sync {
    /// Run `f` in one transaction: commit when it returns Ok, roll back on Err.
    fn transaction(&self, f: &mut dyn FnMut(&mut dyn Tx) -> Result<()>) -> Result<()>;
    fn list_sources(&self) -> Result<Vec<SourceInfo>>;
    fn list_categories(&self) -> Result<HashMap<String, Category>>;
    fn list_behavior_rules(&self) -> Result<Vec<Rule>>;
    fn update_source_state(
        &self, source_id: &str, status: &str, last_error: Option<&str>, last_seen: Option<DateTime<Utc>>,
        fps: Option<f32>, inference_ms: Option<f32>,
    ) -> Result<()>;
    fn heartbeat(&self, worker_id: &str, sources: i32, model: &str, classifier: Option<&str>) -> Result<()>;

    // classifier (see classifier.rs); cls_at is the claim time while 'classifying', then the finish time
    fn claim_queued_event(&self, exclude: &[String], frames: usize) -> Result<Option<ClassifyJob>>;
    /// Store the class answer and merge behaviour flags in ONE transaction (row-locked).
    fn store_classification(&self, event_id: &str, result: &Classification, flags: &[Flag], error: Option<&str>) -> Result<()>;
    fn store_classification_failure(&self, event_id: &str, error: &str, requeue: bool) -> Result<()>;
    fn skip_all_queued(&self) -> Result<u64>;
    fn recover_stuck_classifying(&self, older_than_s: f64) -> Result<u64>;
    /// -> (queued, failed in the last 10 minutes)
    fn classifier_stats(&self) -> Result<(i64, i64)>;
}

// ---- PostgreSQL ---------------------------------------------------------------------------------------------------
pub struct PgRepository {
    dsn: String,
    idle: Mutex<Vec<Client>>,
    max_idle: usize,
}

impl PgRepository {
    pub fn new(dsn: &str, max_idle: usize) -> Self {
        Self { dsn: dsn.to_string(), idle: Mutex::new(Vec::new()), max_idle }
    }

    /// Block until Postgres answers and the schema (applied by the web app) exists.
    pub fn wait_ready(&self, stop: &std::sync::atomic::AtomicBool) {
        while !stop.load(std::sync::atomic::Ordering::Relaxed) {
            match self.schema_ready() {
                Ok(true) => return,
                Ok(false) => info!("database up, waiting for schema migration"),
                Err(e) => info!("waiting for database: {e:#}"),
            }
            std::thread::sleep(Duration::from_secs(2));
        }
    }

    fn schema_ready(&self) -> Result<bool> {
        let mut c = Client::connect(&self.dsn, NoTls).context("connecting")?;
        // schema = migrations 001-004 (sources, events.pipeline/burst_done/cls_flags, behavior_rules), applied by the web app
        let row = c.query_one(
            "SELECT to_regclass('public.sources') IS NOT NULL AND to_regclass('public.behavior_rules') IS NOT NULL AND \
             (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='events' \
              AND column_name IN ('pipeline','burst_done','cls_flags')) = 3",
            &[],
        )?;
        Ok(row.get(0))
    }

    fn with<R>(&self, f: impl FnOnce(&mut Client) -> Result<R>) -> Result<R> {
        let pooled = self.idle.lock().unwrap_or_else(|e| e.into_inner()).pop();
        let mut client = match pooled {
            Some(c) if !c.is_closed() => c,
            _ => Client::connect(&self.dsn, NoTls).context("connecting to the database")?,
        };
        let out = f(&mut client);
        if !client.is_closed() {
            let mut idle = self.idle.lock().unwrap_or_else(|e| e.into_inner());
            if idle.len() < self.max_idle {
                idle.push(client);
            }
        }
        out
    }
}

struct PgTx<'a, 'c>(&'a mut Transaction<'c>);

impl Tx for PgTx<'_, '_> {
    fn find_open_incident(&mut self, source_id: &str, label: &str, since: DateTime<Utc>) -> Result<Option<String>> {
        let row = self.0.query_opt(
            "SELECT id::text FROM incidents WHERE source_id=$1::text::uuid AND label=$2 AND status='open' AND opened_at >= $3 \
             ORDER BY opened_at DESC LIMIT 1",
            &[&source_id, &label, &since],
        )?;
        Ok(row.map(|r| r.get(0)))
    }

    fn create_incident(&mut self, title: &str, severity: &str, source_id: &str, label: &str, opened_at: DateTime<Utc>) -> Result<(String, String)> {
        let r = self.0.query_one(
            "INSERT INTO incidents (title, severity, source_id, label, opened_at) VALUES ($1,$2,$3::text::uuid,$4,$5) \
             RETURNING id::text, code",
            &[&title, &severity, &source_id, &label, &opened_at],
        )?;
        Ok((r.get(0), r.get(1)))
    }

    fn insert_event(&mut self, e: &NewEvent) -> Result<String> {
        let bbox = serde_json::to_value(e.bbox)?;
        let r = self.0.query_one(
            "INSERT INTO events (source_id, incident_id, label, confidence, severity, track_id, bbox, title, detected_at) \
             VALUES ($1::text::uuid, $2::text::uuid, $3,$4,$5,$6,$7,$8,$9) RETURNING id::text",
            &[&e.source_id, &e.incident_id, &e.label, &e.confidence, &e.severity, &e.track_id, &bbox, &e.title, &e.detected_at],
        )?;
        Ok(r.get(0))
    }

    fn insert_snapshot(&mut self, event_id: &str, snap: &Snapshot, bbox: Option<&BBox>, offset_ms: i32, idx: i16) -> Result<()> {
        let bbox = bbox.map(serde_json::to_value).transpose()?;
        self.0.execute(
            "INSERT INTO event_snapshots (event_id, idx, jpeg, width, height, bbox, offset_ms) VALUES ($1::text::uuid,$2,$3,$4,$5,$6,$7)",
            &[&event_id, &idx, &snap.jpeg, &snap.width, &snap.height, &bbox, &offset_ms],
        )?;
        Ok(())
    }

    fn finalize_burst(&mut self, event_id: &str, burst_ms: i32, kind: &str, indication: &str) -> Result<()> {
        self.0.execute(
            "UPDATE events SET burst_ms=$1, indication_kind=$2, indication=$3, burst_done=true WHERE id=$4::text::uuid",
            &[&burst_ms, &kind, &indication, &event_id],
        )?;
        Ok(())
    }

    fn lock_event(&mut self, event_id: &str) -> Result<Option<EventState>> {
        let row = self.0.query_opt(
            "SELECT e.id::text, e.incident_id::text, i.severity, e.label, e.severity, e.source_id::text, s.name, e.cls_flags, e.suspicious \
             FROM events e JOIN sources s ON s.id=e.source_id LEFT JOIN incidents i ON i.id=e.incident_id \
             WHERE e.id=$1::text::uuid FOR UPDATE OF e",
            &[&event_id],
        )?;
        Ok(row.map(|r| {
            let flags: Value = r.get(7);
            EventState {
                id: r.get(0), incident_id: r.get(1), incident_severity: r.get(2), label: r.get(3), severity: r.get(4),
                source_id: r.get(5), source_name: r.get(6), flags: flags.as_array().cloned().unwrap_or_default(), suspicious: r.get(8),
            }
        }))
    }

    fn set_event_flags(&mut self, event_id: &str, flags: &[Value], suspicious: bool, severity: &str) -> Result<()> {
        let flags = Value::Array(flags.to_vec());
        self.0.execute(
            "UPDATE events SET cls_flags=$1, suspicious=$2, severity=$3 WHERE id=$4::text::uuid",
            &[&flags, &suspicious, &severity, &event_id],
        )?;
        Ok(())
    }

    fn set_incident_severity(&mut self, incident_id: &str, severity: &str) -> Result<()> {
        self.0.execute("UPDATE incidents SET severity=$1 WHERE id=$2::text::uuid", &[&severity, &incident_id])?;
        Ok(())
    }

    fn link_event_incident(&mut self, event_id: &str, incident_id: &str) -> Result<()> {
        self.0.execute("UPDATE events SET incident_id=$1::text::uuid WHERE id=$2::text::uuid", &[&incident_id, &event_id])?;
        Ok(())
    }

    fn add_timeline(&mut self, incident_id: &str, kind: &str, title: &str, detail: Option<&str>, event_id: Option<&str>, at: DateTime<Utc>) -> Result<()> {
        self.0.execute(
            "INSERT INTO incident_timeline (incident_id, kind, title, detail, event_id, at) VALUES ($1::text::uuid,$2,$3,$4,$5::text::uuid,$6)",
            &[&incident_id, &kind, &title, &detail, &event_id, &at],
        )?;
        Ok(())
    }
}

impl Repository for PgRepository {
    fn transaction(&self, f: &mut dyn FnMut(&mut dyn Tx) -> Result<()>) -> Result<()> {
        self.with(|c| {
            let mut t = c.transaction()?;
            f(&mut PgTx(&mut t))?; // dropping `t` without commit rolls back
            t.commit()?;
            Ok(())
        })
    }

    fn list_sources(&self) -> Result<Vec<SourceInfo>> {
        self.with(|c| {
            let rows = c.query("SELECT id::text, code, name, url, kind, labels, min_confidence, enabled FROM sources ORDER BY code", &[])?;
            Ok(rows
                .iter()
                .map(|r| SourceInfo {
                    id: r.get(0), code: r.get(1), name: r.get(2), url: r.get(3), kind: r.get(4), labels: r.get(5),
                    min_confidence: r.get(6), enabled: r.get(7),
                })
                .collect())
        })
    }

    fn list_categories(&self) -> Result<HashMap<String, Category>> {
        self.with(|c| {
            let rows = c.query("SELECT label, title, severity, opens_incident, cooldown_s, enabled, burst_s, burst_frames FROM categories", &[])?;
            Ok(rows
                .iter()
                .map(|r| {
                    let cat = Category {
                        label: r.get(0), title: r.get(1), severity: r.get(2), opens_incident: r.get(3), cooldown_s: r.get(4),
                        enabled: r.get(5), burst_s: r.get(6), burst_frames: i32::from(r.get::<_, i16>(7)),
                    };
                    (cat.label.clone(), cat)
                })
                .collect())
        })
    }

    fn list_behavior_rules(&self) -> Result<Vec<Rule>> {
        self.with(|c| {
            let rows = c.query(
                "SELECT key, title, kind, question, applies_to, threshold, config, severity, opens_incident, position \
                 FROM behavior_rules WHERE enabled ORDER BY position, key",
                &[],
            )?;
            Ok(rows
                .iter()
                .map(|r| Rule {
                    key: r.get(0), title: r.get(1), kind: r.get(2), question: r.get(3), applies_to: r.get(4), threshold: r.get(5),
                    config: r.get(6), severity: r.get(7), opens_incident: r.get(8), position: r.get(9),
                })
                .collect())
        })
    }

    fn update_source_state(
        &self, source_id: &str, status: &str, last_error: Option<&str>, last_seen: Option<DateTime<Utc>>, fps: Option<f32>,
        inference_ms: Option<f32>,
    ) -> Result<()> {
        self.with(|c| {
            c.execute(
                "UPDATE sources SET status=$1, last_error=$2, last_seen=COALESCE($3, last_seen), fps=$4, inference_ms=$5, updated_at=now() \
                 WHERE id=$6::text::uuid",
                &[&status, &last_error, &last_seen, &fps, &inference_ms, &source_id],
            )?;
            Ok(())
        })
    }

    fn heartbeat(&self, worker_id: &str, sources: i32, model: &str, classifier: Option<&str>) -> Result<()> {
        self.with(|c| {
            c.execute(
                "INSERT INTO worker_heartbeats (worker_id, last_seen, sources, model, classifier) VALUES ($1, now(), $2, $3, $4) \
                 ON CONFLICT (worker_id) DO UPDATE SET last_seen=now(), sources=EXCLUDED.sources, model=EXCLUDED.model, classifier=EXCLUDED.classifier",
                &[&worker_id, &sources, &model, &classifier],
            )?;
            Ok(())
        })
    }

    fn claim_queued_event(&self, exclude: &[String], frames: usize) -> Result<Option<ClassifyJob>> {
        self.with(|c| {
            let mut t = c.transaction()?; // one transaction: lock, flip to 'classifying', read snapshots + queue depth
            let exclude: Vec<String> = exclude.to_vec();
            let Some(row) = t.query_opt(
                "SELECT e.id::text, e.bbox, e.label FROM events e WHERE e.pipeline='queued' AND NOT (e.id::text = ANY($1)) \
                 ORDER BY e.detected_at FOR UPDATE OF e SKIP LOCKED LIMIT 1",
                &[&exclude],
            )? else {
                return Ok(None);
            };
            let id: String = row.get(0);
            let event_bbox: Option<Value> = row.get(1);
            let label: String = row.get(2);
            t.execute("UPDATE events SET pipeline='classifying', cls_at=now() WHERE id=$1::text::uuid", &[&id])?;
            let limit = frames.max(1) as i64;
            let snaps = t.query("SELECT jpeg, bbox FROM event_snapshots WHERE event_id=$1::text::uuid ORDER BY idx LIMIT $2", &[&id, &limit])?;
            let depth: i64 = t.query_one("SELECT count(*) FROM events WHERE pipeline='queued'", &[])?.get(0);
            t.commit()?;
            // idx 0 predates per-snapshot boxes on old rows: fall back to the event's own box
            let to_box = |v: Option<Value>| v.and_then(|v| serde_json::from_value::<BBox>(v).ok());
            let shots: Vec<(Vec<u8>, Option<BBox>)> = snaps
                .iter()
                .enumerate()
                .map(|(i, r)| {
                    let own = to_box(r.get(1));
                    (r.get(0), own.or_else(|| if i == 0 { to_box(event_bbox.clone()) } else { None }))
                })
                .collect();
            Ok(Some(ClassifyJob {
                event_id: id, bbox: to_box(event_bbox), jpeg: shots.first().map(|s| s.0.clone()), label, snapshots: shots,
                queue_depth: depth,
            }))
        })
    }

    fn store_classification(&self, event_id: &str, result: &Classification, flags: &[Flag], error: Option<&str>) -> Result<()> {
        let probs = serde_json::to_value(&result.probs)?;
        let error: Option<String> = error.map(|e| e.chars().take(300).collect());
        let ms = result.ms as i32;
        self.with(|c| {
            let mut t = c.transaction()?;
            t.execute(
                "UPDATE events SET pipeline='ready', cls_label=$1, cls_confidence=$2, cls_probs=$3, cls_model=$4, cls_ms=$5, cls_error=$6, \
                 cls_at=now() WHERE id=$7::text::uuid",
                &[&result.label, &result.confidence, &probs, &result.model, &ms, &error, &event_id],
            )?;
            if !flags.is_empty() {
                apply_flags(&mut PgTx(&mut t), event_id, flags, Utc::now())?;
            }
            t.commit()?;
            Ok(())
        })
    }

    fn store_classification_failure(&self, event_id: &str, error: &str, requeue: bool) -> Result<()> {
        let error: String = error.chars().take(300).collect();
        self.with(|c| {
            c.execute(
                "UPDATE events SET pipeline=$1, cls_error=$2, cls_at=now() WHERE id=$3::text::uuid",
                &[&if requeue { "queued" } else { "failed" }, &error, &event_id],
            )?;
            Ok(())
        })
    }

    fn skip_all_queued(&self) -> Result<u64> {
        self.with(|c| Ok(c.execute("UPDATE events SET pipeline='skipped' WHERE pipeline='queued'", &[])?))
    }

    fn recover_stuck_classifying(&self, older_than_s: f64) -> Result<u64> {
        self.with(|c| {
            Ok(c.execute(
                "UPDATE events SET pipeline='queued' WHERE pipeline='classifying' AND (cls_at IS NULL OR cls_at < now() - make_interval(secs => $1))",
                &[&older_than_s],
            )?)
        })
    }

    fn classifier_stats(&self) -> Result<(i64, i64)> {
        self.with(|c| {
            let r = c.query_one(
                "SELECT count(*) FILTER (WHERE pipeline IN ('queued','classifying')), \
                 count(*) FILTER (WHERE pipeline='failed' AND cls_at > now() - interval '10 minutes') \
                 FROM events WHERE pipeline IN ('queued','classifying','failed')",
                &[],
            )?;
            Ok((r.get(0), r.get(1)))
        })
    }
}
