//! Test doubles: an in-memory `Repository` and a tiny System One stub server.
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use anyhow::Result;
use chrono::{DateTime, Utc};
use serde_json::{Value, json};

use crate::automation::{AutomationRow, Connection, EventView, TestRequest};
use crate::behavior::{EventState, Flag, Rule};
use crate::classifier::ClassifyJob;
use crate::model::{BBox, Category, Snapshot, SourceInfo};
use crate::repo::{NewEvent, Repository, Tx};

pub fn source(labels: &[&str], min_confidence: f32) -> SourceInfo {
    SourceInfo {
        id: "src-1".into(), code: "CAM-01".into(), name: "Dock 2".into(), url: "rtsp://x".into(), kind: "rtsp".into(),
        labels: labels.iter().map(|s| (*s).into()).collect(), min_confidence, enabled: true,
        config_raw: serde_json::Value::Null, config: crate::config::EngineConfig::default(),
    }
}

pub fn cat(label: &str, severity: &str, opens_incident: bool, cooldown_s: i32) -> Category {
    Category {
        label: label.into(), title: label[..1].to_uppercase() + &label[1..], severity: severity.into(), opens_incident, cooldown_s,
        enabled: true, burst_s: 4, burst_frames: 3,
    }
}

#[derive(Default, Clone, Debug)]
pub struct FEvent {
    pub label: String,
    pub severity: String,
    pub incident_id: Option<String>,
    pub flags: Vec<Value>,
    pub suspicious: bool,
    pub source_id: String,
    pub track_id: i32,
    pub burst_done: bool,
    pub indication_kind: Option<String>,
    pub indication: Option<String>,
    pub burst_ms: Option<i32>,
    pub speed_kmh: Option<f32>,
    pub pipeline: String,
    pub cls_label: Option<String>,
    pub cls_error: Option<String>,
    pub jpeg: Option<Vec<u8>>,
    pub bbox: Option<BBox>,
}

#[derive(Default, Clone, Debug)]
pub struct FIncident {
    pub severity: String,
    pub code: String,
    pub label: String,
    pub source_id: String,
    pub status: String,
    pub opened_at: Option<DateTime<Utc>>,
}

#[derive(Default, Clone, Debug)]
pub struct FRun {
    pub id: i64,
    pub automation_id: Option<String>,
    pub connection_id: Option<String>,
    pub event_id: Option<String>,
    pub source_id: Option<String>,
    pub mode: String,
    pub status: String,
    pub detail: Value,
}

#[derive(Default, Clone, Debug)]
pub struct FakeState {
    pub runs: Vec<FRun>,
    pub automations: Vec<AutomationRow>,
    pub connections: Vec<Connection>,
    pub incidents: BTreeMap<String, FIncident>,
    pub events: BTreeMap<String, FEvent>,
    /// (event_id, idx, offset_ms)
    pub snapshots: Vec<(String, i16, i32)>,
    /// (incident_id, kind, title)
    pub timeline: Vec<(String, String, String)>,
    pub next: u32,
}

#[derive(Default)]
pub struct FakeRepo {
    st: Mutex<FakeState>,
}

impl FakeRepo {
    pub fn state(&self) -> FakeState {
        self.st.lock().unwrap().clone()
    }

    fn lock(&self) -> MutexGuard<'_, FakeState> {
        self.st.lock().unwrap()
    }

    /// An existing event (and an open incident when `incident_severity` is given), already detected and classified-nothing.
    pub fn seed_event(&self, label: &str, severity: &str, incident_severity: Option<&str>) -> (String, Option<String>) {
        let mut s = self.lock();
        s.next += 1;
        let n = s.next;
        let id = format!("evt-{n:04}-aaaa");
        let inc = incident_severity.map(|sev| format!("inc-{n:04}"));
        if let (Some(iid), Some(sev)) = (&inc, incident_severity) {
            s.incidents.insert(iid.clone(), FIncident { severity: sev.into(), code: format!("INC-{n:04}"), label: label.into(), source_id: "src-1".into(), status: "open".into(), opened_at: Some(Utc::now()) });
        }
        s.events.insert(id.clone(), FEvent { label: label.into(), severity: severity.into(), incident_id: inc.clone(), source_id: "src-1".into(), pipeline: "ready".into(), ..Default::default() });
        (id, inc)
    }

    pub fn set_automations(&self, rows: Vec<AutomationRow>, conns: Vec<Connection>) {
        let mut s = self.lock();
        s.automations = rows;
        s.connections = conns;
    }

    /// Queue a preview / test send (automation) or a connection check, as the web app would.
    pub fn queue_test(&self, automation: Option<&str>, connection: Option<&str>, mode: &str) {
        let mut s = self.lock();
        let id = s.runs.len() as i64 + 1;
        s.runs.push(FRun {
            id, automation_id: automation.map(Into::into), connection_id: connection.map(Into::into), mode: mode.into(), status: "queued".into(), ..Default::default()
        });
    }

    /// A queued event with a snapshot, for the classifier tests.
    pub fn add_queued(&self, jpeg: Option<Vec<u8>>, bbox: Option<BBox>, label: &str) -> String {
        let mut s = self.lock();
        s.next += 1;
        let id = format!("evt-{:04}-queued", s.next);
        s.events.insert(id.clone(), FEvent { label: label.into(), severity: "MEDIUM".into(), source_id: "src-1".into(), pipeline: "queued".into(), jpeg, bbox, ..Default::default() });
        id
    }
}

struct FakeTx<'a>(&'a mut FakeState);

impl Tx for FakeTx<'_> {
    fn find_open_incident(&mut self, source_id: &str, label: &str, since: DateTime<Utc>) -> Result<Option<String>> {
        Ok(self.0.incidents.iter().filter(|(_, i)| i.source_id == source_id && i.label == label && i.status == "open" && i.opened_at.is_none_or(|t| t >= since)).map(|(k, _)| k.clone()).next())
    }

    fn create_incident(&mut self, _title: &str, severity: &str, source_id: &str, label: &str, opened_at: DateTime<Utc>) -> Result<(String, String)> {
        self.0.next += 1;
        let (id, code) = (format!("inc-{:04}", self.0.next), format!("INC-{:04}", self.0.next));
        self.0.incidents.insert(id.clone(), FIncident { severity: severity.into(), code: code.clone(), label: label.into(), source_id: source_id.into(), status: "open".into(), opened_at: Some(opened_at) });
        Ok((id, code))
    }

    fn insert_event(&mut self, e: &NewEvent) -> Result<String> {
        self.0.next += 1;
        let id = format!("evt-{:04}-bbbb", self.0.next);
        self.0.events.insert(id.clone(), FEvent {
            label: e.label.into(), severity: e.severity.into(), incident_id: e.incident_id.map(Into::into), source_id: e.source_id.into(),
            track_id: e.track_id, pipeline: e.pipeline.into(), cls_label: Some(e.cls_label.into()), bbox: Some(e.bbox), ..Default::default()
        });
        Ok(id)
    }

    fn insert_snapshot(&mut self, event_id: &str, _snap: &Snapshot, _bbox: Option<&BBox>, offset_ms: i32, idx: i16) -> Result<()> {
        self.0.snapshots.push((event_id.into(), idx, offset_ms));
        Ok(())
    }

    fn finalize_burst(&mut self, event_id: &str, burst_ms: i32, kind: &str, indication: &str) -> Result<()> {
        let e = self.0.events.get_mut(event_id).expect("event");
        e.burst_done = true;
        e.burst_ms = Some(burst_ms);
        e.indication_kind = Some(kind.into());
        e.indication = Some(indication.into());
        Ok(())
    }

    fn set_event_speed(&mut self, event_id: &str, kmh: f32) -> Result<()> {
        self.0.events.get_mut(event_id).expect("event").speed_kmh = Some(kmh);
        Ok(())
    }

    fn lock_event(&mut self, event_id: &str) -> Result<Option<EventState>> {
        Ok(self.0.events.get(event_id).map(|e| EventState {
            id: event_id.into(), incident_id: e.incident_id.clone(),
            incident_severity: e.incident_id.as_ref().map(|i| self.0.incidents[i].severity.clone()), label: e.label.clone(),
            severity: e.severity.clone(), source_id: e.source_id.clone(), source_name: "Dock 2".into(), flags: e.flags.clone(), suspicious: e.suspicious,
        }))
    }

    fn set_event_flags(&mut self, event_id: &str, flags: &[Value], suspicious: bool, severity: &str) -> Result<()> {
        let e = self.0.events.get_mut(event_id).expect("event");
        e.flags = flags.to_vec();
        e.suspicious = suspicious;
        e.severity = severity.into();
        Ok(())
    }

    fn set_incident_severity(&mut self, incident_id: &str, severity: &str) -> Result<()> {
        self.0.incidents.get_mut(incident_id).expect("incident").severity = severity.into();
        Ok(())
    }

    fn link_event_incident(&mut self, event_id: &str, incident_id: &str) -> Result<()> {
        self.0.events.get_mut(event_id).expect("event").incident_id = Some(incident_id.into());
        Ok(())
    }

    fn add_timeline(&mut self, incident_id: &str, kind: &str, title: &str, _detail: Option<&str>, _event_id: Option<&str>, _at: DateTime<Utc>) -> Result<()> {
        self.0.timeline.push((incident_id.into(), kind.into(), title.into()));
        Ok(())
    }
}

impl Repository for FakeRepo {
    fn transaction(&self, f: &mut dyn FnMut(&mut dyn Tx) -> Result<()>) -> Result<()> {
        let mut guard = self.lock();
        let mut work = guard.clone();
        f(&mut FakeTx(&mut work))?; // an Err leaves the committed state untouched (rollback)
        *guard = work;
        Ok(())
    }

    fn list_sources(&self) -> Result<Vec<SourceInfo>> {
        Ok(vec![])
    }

    fn list_categories(&self) -> Result<HashMap<String, Category>> {
        Ok(HashMap::new())
    }

    fn list_behavior_rules(&self) -> Result<Vec<Rule>> {
        Ok(vec![])
    }

    fn engine_defaults(&self) -> Result<Value> {
        Ok(json!({}))
    }

    fn update_source_state(&self, _: &str, _: &str, _: Option<&str>, _: Option<DateTime<Utc>>, _: Option<f32>, _: Option<f32>) -> Result<()> {
        Ok(())
    }

    fn heartbeat(&self, _: &str, _: i32, _: &str, _: Option<&str>) -> Result<()> {
        Ok(())
    }

    fn claim_queued_event(&self, exclude: &[String], _frames: usize) -> Result<Option<ClassifyJob>> {
        let mut s = self.lock();
        let depth = s.events.values().filter(|e| e.pipeline == "queued").count() as i64;
        let Some((id, e)) = s.events.iter_mut().find(|(id, e)| e.pipeline == "queued" && !exclude.contains(id)) else { return Ok(None) };
        e.pipeline = "classifying".into();
        let shots = e.jpeg.clone().map(|j| vec![(j, e.bbox)]).unwrap_or_default();
        Ok(Some(ClassifyJob { event_id: id.clone(), bbox: e.bbox, jpeg: e.jpeg.clone(), label: e.label.clone(), snapshots: shots, queue_depth: depth }))
    }

    fn store_rule_result(&self, event_id: &str, flags: &[Flag], error: Option<&str>, _ms: u64) -> Result<()> {
        self.transaction(&mut |tx| crate::behavior::apply_flags(tx, event_id, flags, Utc::now()))?; // the production path for flags
        let mut s = self.lock();
        let e = s.events.get_mut(event_id).expect("event");
        e.pipeline = "ready".into();
        e.cls_error = error.map(Into::into);
        Ok(())
    }

    fn store_classification_failure(&self, event_id: &str, error: &str, requeue: bool) -> Result<()> {
        let mut s = self.lock();
        let e = s.events.get_mut(event_id).expect("event");
        e.pipeline = if requeue { "queued" } else { "failed" }.into();
        e.cls_error = Some(error.into());
        Ok(())
    }

    fn skip_all_queued(&self) -> Result<u64> {
        let mut n = 0;
        for e in self.lock().events.values_mut().filter(|e| e.pipeline == "queued") {
            e.pipeline = "skipped".into();
            n += 1;
        }
        Ok(n)
    }

    fn recover_stuck_classifying(&self, _: f64) -> Result<u64> {
        Ok(0)
    }

    fn classifier_stats(&self) -> Result<(i64, i64)> {
        Ok((0, 0))
    }

    fn list_automations(&self) -> Result<Vec<AutomationRow>> {
        Ok(self.lock().automations.clone())
    }

    fn list_connections(&self) -> Result<Vec<Connection>> {
        Ok(self.lock().connections.clone())
    }

    fn automation_event(&self, event_id: &str) -> Result<Option<EventView>> {
        let s = self.lock();
        Ok(s.events.get(event_id).map(|e| EventView {
            id: event_id.into(), code: format!("evt_{}", &event_id[4..8.min(event_id.len())]), label: e.label.clone(), severity: e.severity.clone(),
            title: format!("{} detected at Dock 2", e.label), detected_at: Utc::now(), source_id: e.source_id.clone(), camera_code: "CAM-01".into(),
            camera_name: "Dock 2".into(), speed_kmh: e.speed_kmh, suspicious: e.suspicious, cls_label: e.cls_label.clone(),
            indicators: e.flags.iter().filter(|f| f["triggered"] == json!(true)).map(|f| (f["rule"].as_str().unwrap_or("").to_string(), f["title"].as_str().unwrap_or("").to_string())).collect(),
            incident_code: e.incident_id.as_ref().map(|i| s.incidents[i].code.clone()),
        }))
    }

    fn try_start_run(&self, automation_id: &str, event_id: &str, source_id: &str, mode: &str) -> Result<Option<i64>> {
        let mut s = self.lock();
        if s.runs.iter().any(|r| r.automation_id.as_deref() == Some(automation_id) && r.event_id.as_deref() == Some(event_id)) {
            return Ok(None);
        }
        let id = s.runs.len() as i64 + 1;
        s.runs.push(FRun {
            id, automation_id: Some(automation_id.into()), event_id: Some(event_id.into()), source_id: Some(source_id.into()), mode: mode.into(), status: "running".into(),
            ..Default::default()
        });
        Ok(Some(id))
    }

    fn recent_run_counts(&self, automation_id: &str, source_id: &str, _cooldown_s: i64) -> Result<(i64, i64)> {
        let s = self.lock();
        let real = |r: &&FRun| r.automation_id.as_deref() == Some(automation_id) && r.event_id.is_some() && ["sent", "simulated", "failed"].contains(&r.status.as_str());
        let all = s.runs.iter().filter(real).count() as i64;
        let cam = s.runs.iter().filter(real).filter(|r| r.source_id.as_deref() == Some(source_id)).count() as i64;
        Ok((cam, all))
    }

    fn finish_run(&self, run_id: i64, status: &str, detail: &Value) -> Result<()> {
        let mut s = self.lock();
        let r = s.runs.iter_mut().find(|r| r.id == run_id).expect("run");
        r.status = status.into();
        r.detail = detail.clone();
        Ok(())
    }

    fn claim_queued_test(&self) -> Result<Option<TestRequest>> {
        let mut s = self.lock();
        let Some(r) = s.runs.iter_mut().find(|r| r.status == "queued") else { return Ok(None) };
        r.status = "running".into();
        Ok(Some(TestRequest { run_id: r.id, automation_id: r.automation_id.clone(), connection_id: r.connection_id.clone(), mode: r.mode.clone() }))
    }
}

// ---- System One stub ----------------------------------------------------------------------------------------------
pub const GOOD: &str = r#"{"model":"d1","answers":{"class":{"type":"choice","choice":"person","probabilities":{"person":0.91,"vehicle":0.02,"false_alarm":0.07},"confidence":0.83}},"usage":{"input_tokens":310,"output_tokens":0}}"#;

#[derive(Clone, Debug)]
pub struct Req {
    pub path: String,
    pub body: Value,
    pub user_agent: Option<String>,
}

#[derive(Default)]
pub struct StubCfg {
    pub status: u16,
    pub body: Option<String>,
    /// P(yes) per rule key (default 0.05)
    pub noul: HashMap<String, f64>,
    /// HTTP 500 when a request has 2+ questions (what Ollama d1 on CPU does)
    pub crash_multi: bool,
}

pub struct StubServer {
    pub url: String,
    pub cfg: Arc<Mutex<StubCfg>>,
    pub requests: Arc<Mutex<Vec<Req>>>,
    server: Arc<tiny_http::Server>,
    hits: Arc<AtomicUsize>,
}

impl StubServer {
    pub fn start() -> Self {
        let server = Arc::new(tiny_http::Server::http("127.0.0.1:0").expect("bind"));
        let url = format!("http://{}", server.server_addr().to_ip().expect("ip"));
        let cfg = Arc::new(Mutex::new(StubCfg { status: 200, ..Default::default() }));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let hits = Arc::new(AtomicUsize::new(0));
        let (s2, c2, r2, h2) = (server.clone(), cfg.clone(), requests.clone(), hits.clone());
        std::thread::spawn(move || {
            for mut req in s2.incoming_requests() {
                let mut raw = String::new();
                let _ = req.as_reader().read_to_string(&mut raw);
                let body: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
                let ua = req.headers().iter().find(|h| h.field.equiv("User-Agent")).map(|h| h.value.to_string());
                r2.lock().unwrap().push(Req { path: req.url().to_string(), body: body.clone(), user_agent: ua });
                h2.fetch_add(1, Ordering::SeqCst);
                let c = c2.lock().unwrap();
                let qs = body["questions"].as_object().cloned().unwrap_or_default();
                let (mut status, mut payload) = (c.status, c.body.clone().unwrap_or_else(|| GOOD.to_string()));
                if c.body.is_none() && status == 200 {
                    if c.crash_multi && qs.len() > 1 {
                        status = 500;
                        payload = r#"{"error":"llama-server process has terminated"}"#.into();
                    } else if qs.values().any(|q| q["type"] == "noul") {
                        let mut answers = serde_json::Map::new();
                        for (k, q) in &qs {
                            if q["type"] == "noul" {
                                answers.insert(k.clone(), json!({"type": "noul", "noul": c.noul.get(k).copied().unwrap_or(0.05)}));
                            }
                        }
                        if qs.contains_key("class") {
                            answers.insert("class".into(), serde_json::from_str::<Value>(GOOD).unwrap()["answers"]["class"].clone());
                        }
                        payload = json!({"model": "d1", "answers": answers}).to_string();
                    }
                }
                drop(c);
                let _ = req.respond(tiny_http::Response::from_string(payload).with_status_code(status));
            }
        });
        Self { url, cfg, requests, server, hits }
    }

    pub fn hits(&self) -> usize {
        self.hits.load(Ordering::SeqCst)
    }
}

impl Drop for StubServer {
    fn drop(&mut self) {
        self.server.unblock();
    }
}
