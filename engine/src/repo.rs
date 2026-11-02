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

use crate::automation::{AutomationRow, Connection, EventView, TestRequest};
use crate::behavior::{EventState, Flag, Rule, apply_flags};
use crate::classifier::ClassifyJob;
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
    /// the camera AI's own classification: person | vehicle | other
    pub cls_label: &'a str,
    /// 'ready' normally; 'queued' when the optional second AI check has behaviour questions to answer for this event
    pub pipeline: &'a str,
}

pub trait Tx {
    fn find_open_incident(&mut self, source_id: &str, label: &str, since: DateTime<Utc>) -> Result<Option<String>>;
    /// -> (id, code)
    fn create_incident(&mut self, title: &str, severity: &str, source_id: &str, label: &str, opened_at: DateTime<Utc>) -> Result<(String, String)>;
    fn insert_event(&mut self, e: &NewEvent) -> Result<String>;
    fn insert_snapshot(&mut self, event_id: &str, snap: &Snapshot, bbox: Option<&BBox>, offset_ms: i32, idx: i16) -> Result<()>;
    fn finalize_burst(&mut self, event_id: &str, burst_ms: i32, kind: &str, indication: &str) -> Result<()>;
    fn set_event_speed(&mut self, event_id: &str, kmh: f32) -> Result<()>;
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
    /// `engine_settings` row 'defaults' (docs/ENGINE_CONFIG.md)
    fn engine_defaults(&self) -> Result<Value>;
    fn update_source_state(
        &self, source_id: &str, status: &str, last_error: Option<&str>, last_seen: Option<DateTime<Utc>>,
        fps: Option<f32>, inference_ms: Option<f32>,
    ) -> Result<()>;
    fn heartbeat(&self, worker_id: &str, sources: i32, model: &str, classifier: Option<&str>) -> Result<()>;

    // second AI check (see classifier.rs); cls_at is the claim time while 'classifying', then the finish time
    fn claim_queued_event(&self, exclude: &[String], frames: usize) -> Result<Option<ClassifyJob>>;
    /// Mark the event 'ready' and merge the behaviour flags it produced in ONE transaction (row-locked).
    fn store_rule_result(&self, event_id: &str, flags: &[Flag], error: Option<&str>, ms: u64) -> Result<()>;
    fn store_classification_failure(&self, event_id: &str, error: &str, requeue: bool) -> Result<()>;
    fn skip_all_queued(&self) -> Result<u64>;
    fn recover_stuck_classifying(&self, older_than_s: f64) -> Result<u64>;
    /// -> (queued, failed in the last 10 minutes)
    fn classifier_stats(&self) -> Result<(i64, i64)>;

    // alerts and automations (see automation.rs)
    fn list_automations(&self) -> Result<Vec<AutomationRow>>;
    fn list_connections(&self) -> Result<Vec<Connection>>;
    fn automation_event(&self, event_id: &str) -> Result<Option<EventView>>;
    /// Claim (automation, event): Some(run id) the first time, None when this event already fired this automation.
    fn try_start_run(&self, automation_id: &str, event_id: &str, source_id: &str, mode: &str) -> Result<Option<i64>>;
    /// -> (runs of this automation on this camera within `cooldown_s`, runs of this automation in the last hour); real runs only
    fn recent_run_counts(&self, automation_id: &str, source_id: &str, cooldown_s: i64) -> Result<(i64, i64)>;
    fn finish_run(&self, run_id: i64, status: &str, detail: &Value) -> Result<()>;
    /// Next preview / test send / connection check queued by the web app (marks it 'running').
    fn claim_queued_test(&self) -> Result<Option<TestRequest>>;
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
        // schema = migrations 001-007 (sources.config, engine_settings, events.pipeline/burst_done/cls_flags/speed_kmh, behavior_rules), applied by the web app
        let row = c.query_one(
            "SELECT to_regclass('public.sources') IS NOT NULL AND to_regclass('public.behavior_rules') IS NOT NULL AND \
             to_regclass('public.engine_settings') IS NOT NULL AND to_regclass('public.automation_runs') IS NOT NULL AND \
             (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='events' \
              AND column_name IN ('pipeline','burst_done','cls_flags','speed_kmh')) = 4 AND \
             EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='sources' AND column_name='config')",
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
        let probs = serde_json::json!({ e.cls_label: e.confidence });
        let r = self.0.query_one(
            "INSERT INTO events (source_id, incident_id, label, confidence, severity, track_id, bbox, title, detected_at, \
             pipeline, cls_label, cls_confidence, cls_probs, cls_model, cls_at) \
             VALUES ($1::text::uuid, $2::text::uuid, $3,$4,$5,$6,$7,$8,$9, $10,$11,$4,$12,'detector', now()) RETURNING id::text",
            &[&e.source_id, &e.incident_id, &e.label, &e.confidence, &e.severity, &e.track_id, &bbox, &e.title, &e.detected_at, &e.pipeline, &e.cls_label, &probs],
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

    fn set_event_speed(&mut self, event_id: &str, kmh: f32) -> Result<()> {
        self.0.execute("UPDATE events SET speed_kmh=$1 WHERE id=$2::text::uuid", &[&kmh, &event_id])?;
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
            let rows = c.query("SELECT id::text, code, name, url, kind, labels, min_confidence, enabled, config FROM sources ORDER BY code", &[])?;
            Ok(rows
                .iter()
                .map(|r| SourceInfo {
                    id: r.get(0), code: r.get(1), name: r.get(2), url: r.get(3), kind: r.get(4), labels: r.get(5),
                    min_confidence: r.get(6), enabled: r.get(7), config_raw: r.get(8), config: crate::config::EngineConfig::default(),
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

    fn engine_defaults(&self) -> Result<Value> {
        self.with(|c| Ok(c.query_opt("SELECT value FROM engine_settings WHERE key='defaults'", &[])?.map_or(Value::Null, |r| r.get(0))))
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

    fn store_rule_result(&self, event_id: &str, flags: &[Flag], error: Option<&str>, ms: u64) -> Result<()> {
        let error: Option<String> = error.map(|e| e.chars().take(300).collect());
        let ms = ms as i32;
        self.with(|c| {
            let mut t = c.transaction()?;
            t.execute(
                "UPDATE events SET pipeline='ready', cls_ms=$1, cls_error=$2, cls_at=now() WHERE id=$3::text::uuid",
                &[&ms, &error, &event_id],
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

    fn list_automations(&self) -> Result<Vec<AutomationRow>> {
        self.with(|c| {
            let rows = c.query("SELECT id::text, name, enabled, mode, conditions, actions, cooldown_s, max_per_hour FROM automations ORDER BY position, created_at", &[])?;
            Ok(rows
                .iter()
                .map(|r| AutomationRow {
                    id: r.get(0), name: r.get(1), enabled: r.get(2), mode: r.get(3), conditions: r.get(4), actions: r.get(5), cooldown_s: r.get(6),
                    max_per_hour: r.get(7),
                })
                .collect())
        })
    }

    fn list_connections(&self) -> Result<Vec<Connection>> {
        self.with(|c| {
            let rows = c.query("SELECT id::text, name, kind, config, enabled FROM connections", &[])?;
            Ok(rows.iter().map(|r| Connection { id: r.get(0), name: r.get(1), kind: r.get(2), config: r.get(3), enabled: r.get(4) }).collect())
        })
    }

    fn automation_event(&self, event_id: &str) -> Result<Option<EventView>> {
        self.with(|c| {
            let row = c.query_opt(
                "SELECT e.id::text, e.code, e.label, e.severity, e.title, e.detected_at, e.source_id::text, s.code, s.name, e.speed_kmh, e.suspicious, \
                 e.cls_label, e.cls_flags, i.code FROM events e JOIN sources s ON s.id=e.source_id LEFT JOIN incidents i ON i.id=e.incident_id \
                 WHERE e.id=$1::text::uuid",
                &[&event_id],
            )?;
            Ok(row.map(|r| {
                let flags: Value = r.get(12);
                let indicators = flags
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|f| f["triggered"] == Value::Bool(true))
                    .filter_map(|f| Some((f["rule"].as_str()?.to_string(), f["title"].as_str().unwrap_or_default().to_string())))
                    .collect();
                EventView {
                    id: r.get(0), code: r.get(1), label: r.get(2), severity: r.get(3), title: r.get(4), detected_at: r.get(5), source_id: r.get(6),
                    camera_code: r.get(7), camera_name: r.get(8), speed_kmh: r.get(9), suspicious: r.get(10), cls_label: r.get(11), indicators,
                    incident_code: r.get(13),
                }
            }))
        })
    }

    fn try_start_run(&self, automation_id: &str, event_id: &str, source_id: &str, mode: &str) -> Result<Option<i64>> {
        self.with(|c| {
            let row = c.query_opt(
                "INSERT INTO automation_runs (automation_id, event_id, source_id, mode, status) \
                 VALUES ($1::text::uuid, $2::text::uuid, $3::text::uuid, $4, 'running') \
                 ON CONFLICT (automation_id, event_id) WHERE event_id IS NOT NULL DO NOTHING RETURNING id",
                &[&automation_id, &event_id, &source_id, &mode],
            )?;
            Ok(row.map(|r| r.get(0)))
        })
    }

    fn recent_run_counts(&self, automation_id: &str, source_id: &str, cooldown_s: i64) -> Result<(i64, i64)> {
        self.with(|c| {
            let secs = cooldown_s as f64;
            let r = c.query_one(
                "SELECT count(*) FILTER (WHERE source_id=$2::text::uuid AND at > now() - make_interval(secs => $3)), \
                 count(*) FILTER (WHERE at > now() - interval '1 hour') FROM automation_runs \
                 WHERE automation_id=$1::text::uuid AND event_id IS NOT NULL AND status IN ('sent','simulated','failed')",
                &[&automation_id, &source_id, &secs],
            )?;
            Ok((r.get(0), r.get(1)))
        })
    }

    fn finish_run(&self, run_id: i64, status: &str, detail: &Value) -> Result<()> {
        self.with(|c| {
            c.execute("UPDATE automation_runs SET status=$2, detail=$3, finished_at=now() WHERE id=$1", &[&run_id, &status, detail])?;
            Ok(())
        })
    }

    fn claim_queued_test(&self) -> Result<Option<TestRequest>> {
        self.with(|c| {
            let mut t = c.transaction()?;
            let Some(r) = t.query_opt(
                "SELECT id, automation_id::text, connection_id::text, mode FROM automation_runs WHERE status='queued' ORDER BY at FOR UPDATE SKIP LOCKED LIMIT 1",
                &[],
            )? else {
                return Ok(None);
            };
            let id: i64 = r.get(0);
            t.execute("UPDATE automation_runs SET status='running' WHERE id=$1", &[&id])?;
            t.commit()?;
            Ok(Some(TestRequest { run_id: id, automation_id: r.get(1), connection_id: r.get(2), mode: r.get(3) }))
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

/// Integration tests against a real Postgres with migrations 001-005 applied. They run only when DATABASE_URL is set (CI sets it).
#[cfg(test)]
mod pg_tests {
    use super::*;
    use crate::model::Snapshot;
    use serde_json::json;
    use std::sync::Mutex as StdMutex;

    /// The claim query looks at the whole `events` table, so these tests must not overlap.
    static SERIAL: StdMutex<()> = StdMutex::new(());

    fn repo() -> Option<PgRepository> {
        std::env::var("DATABASE_URL").ok().map(|d| PgRepository::new(&d, 4))
    }

    fn seed_source(r: &PgRepository) -> String {
        let code = format!("T-{}", Utc::now().timestamp_nanos_opt().unwrap_or_default());
        r.with(|c| {
            Ok(c.query_one(
                "INSERT INTO sources (code, name, url, kind, config) VALUES ($1, 'Test', 'rtsp://x', 'rtsp', '{\"tracker\":{\"max_age\":7}}') RETURNING id::text",
                &[&code],
            )?
            .get(0))
        })
        .unwrap()
    }

    fn cleanup(r: &PgRepository, source: &str) {
        r.with(|c| {
            c.execute("UPDATE events SET pipeline='skipped' WHERE source_id=$1::text::uuid", &[&source])?;
            c.execute("DELETE FROM incidents WHERE source_id=$1::text::uuid", &[&source])?;
            c.execute("DELETE FROM sources WHERE id=$1::text::uuid", &[&source])?;
            Ok(())
        })
        .unwrap();
    }

    fn count(r: &PgRepository, sql: &str, source: &str) -> i64 {
        r.with(|c| Ok(c.query_one(sql, &[&source])?.get(0))).unwrap()
    }

    fn new_event<'a>(source: &'a str, incident: Option<&'a str>) -> NewEvent<'a> {
        NewEvent {
            source_id: source, incident_id: incident, label: "person", confidence: 0.9, severity: "HIGH", track_id: 1,
            bbox: BBox { x: 0.1, y: 0.1, w: 0.2, h: 0.4 }, title: "Person detected at Test", detected_at: Utc::now(),
            cls_label: "person", pipeline: "queued",
        }
    }

    const SNAP: Snapshot = Snapshot { jpeg: Vec::new(), width: 4, height: 4 };

    #[test]
    fn a_transaction_commits_on_ok_and_rolls_back_on_err() {
        let Some(r) = repo() else { return };
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        r.wait_ready(&std::sync::atomic::AtomicBool::new(false));
        let src = seed_source(&r);
        let snap = Snapshot { jpeg: vec![0xff, 0xd8, 1, 2], ..SNAP };
        r.transaction(&mut |tx| {
            let (inc, _code) = tx.create_incident("t", "HIGH", &src, "person", Utc::now())?;
            let ev = tx.insert_event(&new_event(&src, Some(&inc)))?;
            tx.insert_snapshot(&ev, &snap, Some(&BBox { x: 0.1, y: 0.1, w: 0.2, h: 0.4 }), 0, 0)?;
            tx.add_timeline(&inc, "camera", "t", Some("d"), Some(&ev), Utc::now())?;
            tx.set_event_speed(&ev, 42.5)?;
            Ok(())
        })
        .unwrap();
        assert_eq!(count(&r, "SELECT count(*) FROM events WHERE source_id=$1::text::uuid AND speed_kmh = 42.5", &src), 1);
        assert_eq!(count(&r, "SELECT count(*) FROM event_snapshots s JOIN events e ON e.id=s.event_id WHERE e.source_id=$1::text::uuid", &src), 1);
        let failed = r.transaction(&mut |tx| {
            tx.insert_event(&new_event(&src, None))?;
            anyhow::bail!("boom")
        });
        assert!(failed.is_err());
        assert_eq!(count(&r, "SELECT count(*) FROM events WHERE source_id=$1::text::uuid", &src), 1, "the failed transaction left nothing behind");
        cleanup(&r, &src);
    }

    #[test]
    fn claim_classify_and_flag_roundtrip() {
        let Some(r) = repo() else { return };
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        r.wait_ready(&std::sync::atomic::AtomicBool::new(false));
        let src = seed_source(&r);
        let mut mine = String::new();
        r.transaction(&mut |tx| {
            mine = tx.insert_event(&new_event(&src, None))?;
            tx.insert_snapshot(&mine, &Snapshot { jpeg: vec![0xff, 0xd8, 9], ..SNAP }, None, 0, 0)
        })
        .unwrap();
        // ignore queued events that belong to someone else (a dev database may have some)
        let others: Vec<String> = r
            .with(|c| Ok(c.query("SELECT id::text FROM events WHERE pipeline='queued' AND source_id <> $1::text::uuid", &[&src])?.iter().map(|x| x.get(0)).collect()))
            .unwrap();
        let job = r.claim_queued_event(&others, 1).unwrap().expect("my event is queued");
        assert_eq!(job.event_id, mine);
        assert_eq!(job.jpeg.as_deref(), Some(&[0xff, 0xd8, 9][..]));
        assert!(r.claim_queued_event(&others, 1).unwrap().is_none(), "claimed events are not handed out twice");

        let flag = Flag { rule: "weapon".into(), title: "Weapon".into(), prob: Some(0.8), threshold: 0.7, severity: "CRITICAL".into(), triggered: true, opens_incident: true, detail: Some("x".into()) };
        r.store_rule_result(&mine, &[flag], None, 12).unwrap();
        let row = r
            .with(|c| Ok(c.query_one("SELECT pipeline, suspicious, severity, incident_id IS NOT NULL, cls_flags, cls_label, cls_model FROM events WHERE id=$1::text::uuid", &[&mine])?))
            .unwrap();
        assert_eq!(row.get::<_, String>(0), "ready");
        assert!(row.get::<_, bool>(1));
        assert_eq!(row.get::<_, String>(2), "CRITICAL");
        assert!(row.get::<_, bool>(3), "the indicator opened an incident for an event that had none");
        assert_eq!(row.get::<_, Value>(4)[0]["rule"], json!("weapon"));
        assert_eq!((row.get::<_, String>(5), row.get::<_, String>(6)), ("person".to_string(), "detector".to_string()), "the camera AI classified it when the event was written");
        cleanup(&r, &src);
    }

    #[test]
    fn sources_carry_their_config_and_defaults_are_readable() {
        let Some(r) = repo() else { return };
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        r.wait_ready(&std::sync::atomic::AtomicBool::new(false));
        let src = seed_source(&r);
        let listed = r.list_sources().unwrap().into_iter().find(|s| s.id == src).expect("listed");
        assert_eq!(listed.config_raw["tracker"]["max_age"], json!(7));
        assert!(r.engine_defaults().unwrap().is_object());
        assert!(r.list_categories().unwrap().contains_key("person"));
        assert!(!r.list_behavior_rules().unwrap().is_empty());
        r.heartbeat("test-worker", 1, "m", Some("d1 @ x")).unwrap();
        cleanup(&r, &src);
    }

    #[test]
    fn automation_runs_fire_once_per_event_and_tests_are_claimed_from_the_queue() {
        let Some(r) = repo() else { return };
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        r.wait_ready(&std::sync::atomic::AtomicBool::new(false));
        let src = seed_source(&r);
        let mut ev = String::new();
        r.transaction(&mut |tx| {
            ev = tx.insert_event(&new_event(&src, None))?;
            Ok(())
        })
        .unwrap();
        let auto: String = r
            .with(|c| Ok(c.query_one("INSERT INTO automations (name, conditions, actions) VALUES ('t', '[]', '[]') RETURNING id::text", &[])?.get(0)))
            .unwrap();
        let run = r.try_start_run(&auto, &ev, &src, "simulate").unwrap().expect("first claim");
        assert!(r.try_start_run(&auto, &ev, &src, "simulate").unwrap().is_none(), "the same event never fires the same automation twice");
        r.finish_run(run, "simulated", &json!({"actions": []})).unwrap();
        assert_eq!(r.recent_run_counts(&auto, &src, 60).unwrap(), (1, 1));
        let view = r.automation_event(&ev).unwrap().expect("view");
        assert_eq!((view.label.as_str(), view.cls_label.as_deref()), ("person", Some("person")));
        assert!(r.list_automations().unwrap().iter().any(|a| a.id == auto));

        r.with(|c| {
            c.execute("INSERT INTO automation_runs (automation_id, mode, status) VALUES ($1::text::uuid, 'test_simulate', 'queued')", &[&auto])?;
            Ok(())
        })
        .unwrap();
        let t = r.claim_queued_test().unwrap().expect("queued test");
        assert_eq!((t.automation_id.as_deref(), t.mode.as_str()), (Some(auto.as_str()), "test_simulate"));
        assert!(r.claim_queued_test().unwrap().is_none());
        r.finish_run(t.run_id, "simulated", &json!({})).unwrap();
        r.with(|c| {
            c.execute("DELETE FROM automations WHERE id=$1::text::uuid", &[&auto])?;
            Ok(())
        })
        .unwrap();
        cleanup(&r, &src);
    }
}
