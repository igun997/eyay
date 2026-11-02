//! Alerts and automations (docs/AUTOMATIONS.md): "when event A happens, simulate or send message B", in real time.
//!
//! The detection threads call `notify(event_id)` whenever an event is created or changes (classification, warning sign, speed). The
//! dispatcher thread loads the event, evaluates every enabled automation, and runs the first-time matches: simulated by default,
//! sent only for automations switched to `live`. A claimed (automation, event) row makes sure an event fires an automation once; the
//! per-camera quiet period and the hourly cap keep a stuck condition from flooding a target. Nothing here can slow detection down.
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, Sender, channel};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use chrono::{DateTime, Timelike, Utc};
use serde_json::{Value, json};
use tracing::{error, info, warn};

use crate::actions::{self, ActionResult, Ctx};
use crate::model::sev_rank;
use crate::repo::Repository;

static TX: OnceLock<Mutex<Sender<String>>> = OnceLock::new();

/// Create the channel the detection threads use to announce changed events. Call once, before they start.
pub fn init() -> Receiver<String> {
    let (tx, rx) = channel();
    let _ = TX.set(Mutex::new(tx));
    rx
}

/// Tell the dispatcher that this event was created or changed. Does nothing before `init` (tests, tools).
pub fn notify(event_id: &str) {
    if let Some(tx) = TX.get() {
        let _ = tx.lock().unwrap_or_else(|e| e.into_inner()).send(event_id.to_string());
    }
}

// ---- data -------------------------------------------------------------------------------------------------------------
#[derive(Debug, Clone)]
pub struct Connection {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub config: Value,
    pub enabled: bool,
}

#[derive(Debug, Clone)]
pub enum Condition {
    Camera(Vec<String>),
    Label(Vec<String>),
    SeverityAtLeast(String),
    Indicator(String),
    ClassifiedAs(String),
    SpeedAbove(f32),
    Suspicious(bool),
    TimeBetween { from_min: u32, to_min: u32, utc_offset_min: i32 },
}

#[derive(Debug, Clone)]
pub enum Action {
    Webhook { connection: String, body: Option<String> },
    Mqtt { connection: String, topic: String, payload: Option<String>, retain: bool },
    ModbusCoil { connection: String, address: u16, hold_s: u32 },
    ModbusRegister { connection: String, address: u16, value: u16 },
}

#[derive(Debug, Clone)]
pub struct Automation {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    pub mode: String,
    pub conditions: Vec<Condition>,
    pub actions: Vec<Action>,
    pub cooldown_s: i32,
    pub max_per_hour: i32,
}

/// Raw row as stored; parsed by `Automation::parse`.
#[derive(Debug, Clone)]
pub struct AutomationRow {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    pub mode: String,
    pub conditions: Value,
    pub actions: Value,
    pub cooldown_s: i32,
    pub max_per_hour: i32,
}

/// The state of one event as the conditions see it.
#[derive(Debug, Clone)]
pub struct EventView {
    pub id: String,
    pub code: String,
    pub label: String,
    pub severity: String,
    pub title: String,
    pub detected_at: DateTime<Utc>,
    pub source_id: String,
    pub camera_code: String,
    pub camera_name: String,
    pub speed_kmh: Option<f32>,
    pub suspicious: bool,
    pub cls_label: Option<String>,
    /// (rule key, title) of the warning signs that triggered
    pub indicators: Vec<(String, String)>,
    pub incident_code: Option<String>,
}

#[derive(Debug, Clone)]
pub struct TestRequest {
    pub run_id: i64,
    pub automation_id: Option<String>,
    pub connection_id: Option<String>,
    /// test_send | test_simulate
    pub mode: String,
}

// ---- parsing ----------------------------------------------------------------------------------------------------------
fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default()
}

fn hhmm(v: &Value) -> Option<u32> {
    let (h, m) = v.as_str()?.split_once(':')?;
    let (h, m) = (h.trim().parse::<u32>().ok()?, m.trim().parse::<u32>().ok()?);
    (h < 24 && m < 60).then_some(h * 60 + m)
}

pub fn parse_conditions(v: &Value) -> Result<Vec<Condition>, String> {
    let Some(arr) = v.as_array() else { return Err("conditions must be a list".into()) };
    arr.iter()
        .map(|c| {
            let field = c["field"].as_str().unwrap_or("");
            Ok(match field {
                "camera" => Condition::Camera(strings(&c["in"])),
                "label" => Condition::Label(strings(&c["in"])),
                "severity" => {
                    let s = c["at_least"].as_str().unwrap_or("");
                    if !["LOW", "MEDIUM", "HIGH", "CRITICAL"].contains(&s) {
                        return Err(format!("severity needs at_least LOW|MEDIUM|HIGH|CRITICAL, got {s:?}"));
                    }
                    Condition::SeverityAtLeast(s.into())
                }
                "indicator" => Condition::Indicator(c["triggered"].as_str().filter(|s| !s.is_empty()).ok_or("indicator needs 'triggered'")?.into()),
                "classified_as" => Condition::ClassifiedAs(c["is"].as_str().filter(|s| !s.is_empty()).ok_or("classified_as needs 'is'")?.into()),
                "speed_kmh" => Condition::SpeedAbove(c["above"].as_f64().ok_or("speed_kmh needs a number 'above'")? as f32),
                "suspicious" => Condition::Suspicious(c["is"].as_bool().ok_or("suspicious needs true or false")?),
                "time" => {
                    let b = c["between"].as_array().filter(|b| b.len() == 2).ok_or("time needs between: [\"HH:MM\", \"HH:MM\"]")?;
                    Condition::TimeBetween {
                        from_min: hhmm(&b[0]).ok_or("time 'from' must be HH:MM")?,
                        to_min: hhmm(&b[1]).ok_or("time 'to' must be HH:MM")?,
                        utc_offset_min: c["utc_offset_min"].as_i64().unwrap_or(0).clamp(-720, 840) as i32,
                    }
                }
                other => return Err(format!("unknown condition field {other:?}")),
            })
        })
        .collect()
}

pub fn parse_actions(v: &Value) -> Result<Vec<Action>, String> {
    let Some(arr) = v.as_array() else { return Err("actions must be a list".into()) };
    arr.iter()
        .map(|a| {
            let conn = a["connection"].as_str().filter(|c| !c.is_empty()).ok_or("every action needs a connection")?.to_string();
            let addr = |a: &Value| a["address"].as_u64().filter(|n| *n <= 65535).map(|n| n as u16).ok_or("address must be 0..65535");
            Ok(match a["type"].as_str().unwrap_or("") {
                "webhook" => Action::Webhook { connection: conn, body: a["body"].as_str().map(String::from) },
                "mqtt" => Action::Mqtt {
                    connection: conn, topic: a["topic"].as_str().filter(|t| !t.is_empty()).ok_or("mqtt needs a topic")?.into(),
                    payload: a["payload"].as_str().map(String::from), retain: a["retain"].as_bool().unwrap_or(false),
                },
                "modbus" => match a["function"].as_str().unwrap_or("") {
                    "write_coil" => Action::ModbusCoil {
                        connection: conn, address: addr(a)?, hold_s: a["hold_s"].as_u64().filter(|h| (1..=60).contains(h)).ok_or("a coil write needs hold_s 1..60 (it always switches back off)")? as u32,
                    },
                    "write_register" => Action::ModbusRegister {
                        connection: conn, address: addr(a)?, value: a["value"].as_u64().filter(|n| *n <= 65535).ok_or("register value must be 0..65535")? as u16,
                    },
                    other => return Err(format!("unknown modbus function {other:?}")),
                },
                other => return Err(format!("unknown action type {other:?}")),
            })
        })
        .collect()
}

impl Automation {
    pub fn parse(row: &AutomationRow) -> Result<Self, String> {
        Ok(Self {
            id: row.id.clone(), name: row.name.clone(), enabled: row.enabled, mode: row.mode.clone(),
            conditions: parse_conditions(&row.conditions)?, actions: parse_actions(&row.actions)?, cooldown_s: row.cooldown_s, max_per_hour: row.max_per_hour,
        })
    }
}

// ---- conditions -------------------------------------------------------------------------------------------------------
pub fn matches(conds: &[Condition], ev: &EventView, now: DateTime<Utc>) -> bool {
    conds.iter().all(|c| match c {
        Condition::Camera(ids) => ids.is_empty() || ids.contains(&ev.source_id),
        Condition::Label(ls) => ls.is_empty() || ls.contains(&ev.label),
        Condition::SeverityAtLeast(s) => sev_rank(&ev.severity) >= sev_rank(s),
        Condition::Indicator(k) => if k == "*" { !ev.indicators.is_empty() } else { ev.indicators.iter().any(|(rule, _)| rule == k) },
        Condition::ClassifiedAs(l) => ev.cls_label.as_deref() == Some(l.as_str()),
        Condition::SpeedAbove(v) => ev.speed_kmh.is_some_and(|s| s > *v),
        Condition::Suspicious(b) => ev.suspicious == *b,
        Condition::TimeBetween { from_min, to_min, utc_offset_min } => {
            let local = now + chrono::Duration::minutes(i64::from(*utc_offset_min));
            let m = local.hour() * 60 + local.minute();
            if from_min <= to_min { (*from_min..*to_min).contains(&m) } else { m >= *from_min || m < *to_min } // wraps midnight
        }
    })
}

// ---- payloads ---------------------------------------------------------------------------------------------------------
pub fn vars(ev: &EventView, automation: &str, mode: &str) -> HashMap<String, String> {
    let s = |k: &str, v: String| (k.to_string(), v);
    HashMap::from([
        s("event.id", ev.id.clone()), s("event.code", ev.code.clone()), s("event.label", ev.label.clone()),
        s("event.severity", ev.severity.clone()), s("event.title", ev.title.clone()), s("event.detected_at", ev.detected_at.to_rfc3339()),
        s("event.speed_kmh", ev.speed_kmh.map_or(String::new(), |k| format!("{k:.1}"))), s("event.suspicious", ev.suspicious.to_string()),
        s("event.classified_as", ev.cls_label.clone().unwrap_or_default()),
        s("event.indicators", ev.indicators.iter().map(|(_, t)| t.as_str()).collect::<Vec<_>>().join(", ")),
        s("camera.id", ev.source_id.clone()), s("camera.code", ev.camera_code.clone()), s("camera.name", ev.camera_name.clone()),
        s("incident.code", ev.incident_code.clone().unwrap_or_default()), s("automation.name", automation.to_string()), s("mode", mode.to_string()),
    ])
}

pub fn default_body(ev: &EventView, automation_id: &str, automation: &str, mode: &str, test: bool, now: DateTime<Utc>) -> String {
    json!({
        "eyeai": 1, "mode": mode, "test": test, "sent_at": now.to_rfc3339(),
        "automation": {"id": automation_id, "name": automation},
        "event": {
            "id": ev.id, "code": ev.code, "label": ev.label, "severity": ev.severity, "title": ev.title, "detected_at": ev.detected_at.to_rfc3339(),
            "speed_kmh": ev.speed_kmh, "suspicious": ev.suspicious, "classified_as": ev.cls_label,
            "indicators": ev.indicators.iter().map(|(_, t)| t).collect::<Vec<_>>(),
        },
        "camera": {"id": ev.source_id, "code": ev.camera_code, "name": ev.camera_name},
        "incident": {"code": ev.incident_code},
    })
    .to_string()
}

/// A made-up event for previews and test sends, shaped by the automation's own conditions.
pub fn sample_event(conds: &[Condition], now: DateTime<Utc>) -> EventView {
    let label = conds.iter().find_map(|c| if let Condition::Label(l) = c { l.first().cloned() } else { None }).unwrap_or_else(|| "person".into());
    let severity = conds.iter().find_map(|c| if let Condition::SeverityAtLeast(s) = c { Some(s.clone()) } else { None }).unwrap_or_else(|| "HIGH".into());
    let speed = conds.iter().find_map(|c| if let Condition::SpeedAbove(v) = c { Some(v + 5.0) } else { None });
    EventView {
        id: "00000000-0000-0000-0000-000000000000".into(), code: "evt_test".into(), cls_label: Some(crate::decision::classify_label(&label).into()),
        label, severity, title: "Sample event (this is a test)".into(), detected_at: now, source_id: "sample".into(), camera_code: "CAM-TEST".into(),
        camera_name: "Test camera".into(), speed_kmh: speed, suspicious: true, indicators: vec![("sample".into(), "Sample warning sign".into())],
        incident_code: Some("INC-TEST".into()),
    }
}

// ---- dispatcher -------------------------------------------------------------------------------------------------------
struct PendingOff {
    due: Instant,
    connection: Value,
    address: u16,
}

pub struct Dispatcher {
    repo: Arc<dyn Repository>,
    automations: Vec<Automation>,
    connections: HashMap<String, Connection>,
    loaded: Option<Instant>,
    pending_off: Vec<PendingOff>,
    refresh_every: Duration,
}

impl Dispatcher {
    pub fn new(repo: Arc<dyn Repository>) -> Self {
        Self { repo, automations: Vec::new(), connections: HashMap::new(), loaded: None, pending_off: Vec::new(), refresh_every: Duration::from_secs(3) }
    }

    fn refresh(&mut self, force: bool) {
        if !force && self.loaded.is_some_and(|t| t.elapsed() < self.refresh_every) {
            return;
        }
        self.loaded = Some(Instant::now());
        match (self.repo.list_automations(), self.repo.list_connections()) {
            (Ok(rows), Ok(conns)) => {
                self.automations = rows
                    .iter()
                    .filter_map(|r| match Automation::parse(r) {
                        Ok(a) => Some(a),
                        Err(e) => {
                            warn!("automation {:?} is invalid and will not fire: {e}", r.name);
                            None
                        }
                    })
                    .collect();
                self.connections = conns.into_iter().map(|c| (c.id.clone(), c)).collect();
            }
            (Err(e), _) | (_, Err(e)) => error!("loading automations failed; keeping the previous ones: {e:#}"),
        }
    }

    /// Evaluate every enabled automation for this event and run the ones that match for the first time.
    pub fn handle_event(&mut self, event_id: &str, now: DateTime<Utc>) -> anyhow::Result<()> {
        self.refresh(false);
        if !self.automations.iter().any(|a| a.enabled) {
            return Ok(());
        }
        let Some(view) = self.repo.automation_event(event_id)? else { return Ok(()) };
        let candidates: Vec<Automation> = self.automations.iter().filter(|a| a.enabled && matches(&a.conditions, &view, now)).cloned().collect();
        for a in candidates {
            let Some(run_id) = self.repo.try_start_run(&a.id, &view.id, &view.source_id, &a.mode)? else { continue }; // already fired for this event
            let (recent_on_camera, recent_hour) = self.repo.recent_run_counts(&a.id, &view.source_id, i64::from(a.cooldown_s))?;
            let (status, detail) = if a.cooldown_s > 0 && recent_on_camera > 0 {
                ("skipped", json!({"reason": format!("quiet period: it already fired for this camera in the last {} s", a.cooldown_s), "actions": []}))
            } else if recent_hour >= i64::from(a.max_per_hour) {
                ("skipped", json!({"reason": format!("hourly limit of {} reached", a.max_per_hour), "actions": []}))
            } else {
                let simulate = a.mode != "live";
                let mode = if simulate { "simulate" } else { "live" };
                let ctx = Ctx { vars: vars(&view, &a.name, mode), default_body: default_body(&view, &a.id, &a.name, mode, false, now) };
                let results = self.execute(&a, &ctx, simulate);
                finish_status(&results, simulate)
            };
            if let Err(e) = self.repo.finish_run(run_id, status, &detail) {
                error!("recording automation run {run_id} failed: {e:#}");
            }
            info!("automation '{}' for event {} ({}): {status}", a.name, view.code, a.mode);
        }
        Ok(())
    }

    fn execute(&mut self, a: &Automation, ctx: &Ctx, simulate: bool) -> Vec<Value> {
        let mut out = Vec::new();
        for action in &a.actions {
            let (kind, conn_id) = match action {
                Action::Webhook { connection, .. } => ("webhook", connection),
                Action::Mqtt { connection, .. } => ("mqtt", connection),
                Action::ModbusCoil { connection, .. } | Action::ModbusRegister { connection, .. } => ("modbus", connection),
            };
            let conn = self.connections.get(conn_id).filter(|c| c.enabled && c.kind == kind).cloned();
            let Some(conn) = conn else {
                out.push(ActionResult { ok: false, message: "the connection is missing, switched off, or of another kind".into(), would_send: String::new(), ms: 0 }.to_json(kind, conn_id));
                continue;
            };
            let res = match action {
                Action::Webhook { body, .. } => actions::webhook(&conn.config, body.as_deref(), ctx, simulate),
                Action::Mqtt { topic, payload, retain, .. } => actions::mqtt_publish(&conn.config, topic, payload.as_deref(), *retain, ctx, simulate),
                Action::ModbusCoil { address, hold_s, .. } => {
                    let r = actions::modbus_write(&conn.config, "write_coil", *address, 1, &format!(", then OFF after {hold_s} s"), simulate);
                    if r.ok && !simulate {
                        self.pending_off.push(PendingOff { due: Instant::now() + Duration::from_secs(u64::from(*hold_s)), connection: conn.config.clone(), address: *address });
                    }
                    r
                }
                Action::ModbusRegister { address, value, .. } => actions::modbus_write(&conn.config, "write_register", *address, *value, "", simulate),
            };
            out.push(res.to_json(kind, &conn.name));
        }
        out
    }

    /// Switch coils back off when their time is up (`force`: all of them now, at shutdown).
    pub fn tick_offs(&mut self, force: bool) {
        let now = Instant::now();
        let (due, rest): (Vec<_>, Vec<_>) = std::mem::take(&mut self.pending_off).into_iter().partition(|p| force || p.due <= now);
        self.pending_off = rest;
        for p in due {
            let r = actions::modbus_write(&p.connection, "write_coil", p.address, 0, " (end of pulse)", false);
            if !r.ok {
                error!("switching coil {} back off failed: {}", p.address, r.message);
                if !force {
                    self.pending_off.push(PendingOff { due: now + Duration::from_secs(2), ..p }); // keep trying
                }
            }
        }
    }

    /// Run previews, test sends and connection checks queued by the web app. Returns how many were handled.
    pub fn run_queued_tests(&mut self) -> anyhow::Result<usize> {
        let mut n = 0;
        while let Some(req) = self.repo.claim_queued_test()? {
            self.refresh(true); // tests should see what was just saved
            let (status, detail) = self.run_test(&req);
            self.repo.finish_run(req.run_id, status, &detail)?;
            n += 1;
        }
        Ok(n)
    }

    fn run_test(&mut self, req: &TestRequest) -> (&'static str, Value) {
        let simulate = req.mode == "test_simulate";
        if let Some(cid) = &req.connection_id {
            let Some(conn) = self.connections.get(cid).cloned() else { return ("failed", json!({"reason": "connection not found", "actions": []})) };
            let r = match conn.kind.as_str() {
                "webhook" => actions::webhook_check(&conn.config),
                "mqtt" => actions::mqtt_check(&conn.config),
                _ => actions::modbus_check(&conn.config),
            };
            return (if r.ok { "sent" } else { "failed" }, json!({"reason": "connection check", "actions": [r.to_json(&conn.kind, &conn.name)]}));
        }
        let Some(a) = req.automation_id.as_ref().and_then(|id| self.automations.iter().find(|a| &a.id == id)).cloned() else {
            return ("failed", json!({"reason": "automation not found or invalid", "actions": []}));
        };
        let now = Utc::now();
        let view = sample_event(&a.conditions, now);
        let mode = if simulate { "simulate" } else { "live" };
        let ctx = Ctx { vars: vars(&view, &a.name, mode), default_body: default_body(&view, &a.id, &a.name, mode, true, now) };
        let results = self.execute(&a, &ctx, simulate);
        finish_status(&results, simulate)
    }

    pub fn run_forever(mut self, rx: Receiver<String>, stop: Arc<AtomicBool>) {
        let mut last_tests = Instant::now() - Duration::from_secs(5);
        while !stop.load(Ordering::Relaxed) {
            let mut ids: HashSet<String> = HashSet::new();
            match rx.recv_timeout(Duration::from_millis(250)) {
                Ok(id) => {
                    ids.insert(id);
                    ids.extend(rx.try_iter());
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
            for id in ids {
                if let Err(e) = self.handle_event(&id, Utc::now()) {
                    error!("automation dispatch for event {id} failed: {e:#}");
                }
            }
            self.tick_offs(false);
            if last_tests.elapsed() >= Duration::from_secs(1) {
                last_tests = Instant::now();
                if let Err(e) = self.run_queued_tests() {
                    error!("running queued tests failed: {e:#}");
                }
            }
        }
        self.tick_offs(true); // never leave an output switched on
    }
}

fn finish_status(results: &[Value], simulate: bool) -> (&'static str, Value) {
    let all_ok = results.iter().all(|r| r["ok"] == json!(true));
    let status = if !all_ok { "failed" } else if simulate { "simulated" } else { "sent" };
    (status, json!({"reason": if results.is_empty() { "the automation has no actions" } else { "" }, "actions": results}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::FakeRepo;
    use chrono::TimeZone;

    fn view() -> EventView {
        EventView {
            id: "e1".into(), code: "evt_1".into(), label: "person".into(), severity: "HIGH".into(), title: "Person detected at Dock 2".into(),
            detected_at: Utc.with_ymd_and_hms(2026, 11, 2, 15, 30, 0).unwrap(), source_id: "src-1".into(), camera_code: "CAM-01".into(), camera_name: "Dock 2".into(),
            speed_kmh: Some(72.0), suspicious: true, cls_label: Some("person".into()), indicators: vec![("weapon_object".into(), "Knife".into())], incident_code: Some("INC-0007".into()),
        }
    }

    #[test]
    fn parsing_accepts_the_documented_shapes_and_rejects_the_rest() {
        let c = parse_conditions(&json!([
            {"field": "camera", "in": ["a"]}, {"field": "label", "in": ["person"]}, {"field": "severity", "at_least": "HIGH"},
            {"field": "indicator", "triggered": "*"}, {"field": "classified_as", "is": "person"}, {"field": "speed_kmh", "above": 60},
            {"field": "suspicious", "is": true}, {"field": "time", "between": ["22:00", "06:00"], "utc_offset_min": 420},
        ])).unwrap();
        assert_eq!(c.len(), 8);
        assert!(parse_conditions(&json!([{"field": "weather"}])).is_err());
        assert!(parse_conditions(&json!([{"field": "severity", "at_least": "URGENT"}])).is_err());
        assert!(parse_conditions(&json!([{"field": "time", "between": ["25:00", "06:00"]}])).is_err());
        let a = parse_actions(&json!([
            {"type": "webhook", "connection": "c1"}, {"type": "mqtt", "connection": "c2", "topic": "a/b"},
            {"type": "modbus", "connection": "c3", "function": "write_coil", "address": 0, "hold_s": 5},
            {"type": "modbus", "connection": "c3", "function": "write_register", "address": 10, "value": 1},
        ])).unwrap();
        assert_eq!(a.len(), 4);
        assert!(parse_actions(&json!([{"type": "modbus", "connection": "c3", "function": "write_coil", "address": 0}])).is_err(), "a coil needs a pulse length");
        assert!(parse_actions(&json!([{"type": "modbus", "connection": "c3", "function": "write_coil", "address": 0, "hold_s": 600}])).is_err());
        assert!(parse_actions(&json!([{"type": "webhook"}])).is_err());
    }

    #[test]
    fn conditions_all_have_to_hold() {
        let now = Utc.with_ymd_and_hms(2026, 11, 2, 15, 30, 0).unwrap(); // 22:30 at UTC+7
        let ok = |v: Value| matches(&parse_conditions(&v).unwrap(), &view(), now);
        assert!(ok(json!([])));
        assert!(ok(json!([{"field": "camera", "in": ["src-1"]}, {"field": "label", "in": ["person"]}, {"field": "severity", "at_least": "MEDIUM"}])));
        assert!(!ok(json!([{"field": "camera", "in": ["other"]}])));
        assert!(!ok(json!([{"field": "severity", "at_least": "CRITICAL"}])));
        assert!(ok(json!([{"field": "indicator", "triggered": "weapon_object"}])) && !ok(json!([{"field": "indicator", "triggered": "tamper"}])));
        assert!(ok(json!([{"field": "speed_kmh", "above": 60}])) && !ok(json!([{"field": "speed_kmh", "above": 80}])));
        assert!(ok(json!([{"field": "classified_as", "is": "person"}])) && !ok(json!([{"field": "classified_as", "is": "vehicle"}])));
        // 22:30 local: inside 22:00-06:00 (wraps midnight), outside 08:00-17:00
        assert!(ok(json!([{"field": "time", "between": ["22:00", "06:00"], "utc_offset_min": 420}])));
        assert!(!ok(json!([{"field": "time", "between": ["08:00", "17:00"], "utc_offset_min": 420}])));
        assert!(!ok(json!([{"field": "time", "between": ["22:00", "06:00"], "utc_offset_min": 0}])), "15:30 UTC is not night");
    }

    fn row(id: &str, mode: &str, conds: Value, actions: Value) -> AutomationRow {
        AutomationRow { id: id.into(), name: format!("auto {id}"), enabled: true, mode: mode.into(), conditions: conds, actions, cooldown_s: 60, max_per_hour: 30 }
    }

    fn webhook_conn(id: &str, url: &str) -> Connection {
        Connection { id: id.into(), name: format!("hook {id}"), kind: "webhook".into(), config: json!({"url": url}), enabled: true }
    }

    fn setup(rows: Vec<AutomationRow>, conns: Vec<Connection>) -> (Arc<FakeRepo>, Dispatcher) {
        let repo = Arc::new(FakeRepo::default());
        repo.set_automations(rows, conns);
        (repo.clone(), Dispatcher::new(repo))
    }

    fn event(repo: &FakeRepo, label: &str, sev: &str) -> String {
        repo.seed_event(label, sev, None).0
    }

    #[test]
    fn simulation_logs_what_would_be_sent_once_per_event() {
        let (repo, mut d) = setup(
            vec![row("a1", "simulate", json!([{"field": "label", "in": ["person"]}]), json!([{"type": "webhook", "connection": "c1", "body": "{{event.label}} at {{camera.name}}"}]))],
            vec![webhook_conn("c1", "http://10.255.255.1/hook")],
        );
        let ev = event(&repo, "person", "HIGH");
        d.handle_event(&ev, Utc::now()).unwrap();
        d.handle_event(&ev, Utc::now()).unwrap(); // a second notification of the same event
        let runs = repo.state().runs;
        assert_eq!(runs.len(), 1, "an event fires an automation once");
        assert_eq!(runs[0].status, "simulated");
        assert!(runs[0].detail["actions"][0]["would_send"].as_str().unwrap().contains("person at Dock 2"));
        let car = event(&repo, "car", "HIGH");
        d.handle_event(&car, Utc::now()).unwrap();
        assert_eq!(repo.state().runs.len(), 1, "a car does not match");
    }

    #[test]
    fn the_quiet_period_and_the_hourly_cap_skip_instead_of_flooding() {
        let (repo, mut d) = setup(
            vec![row("a1", "simulate", json!([]), json!([{"type": "webhook", "connection": "c1"}]))],
            vec![webhook_conn("c1", "http://10.255.255.1/hook")],
        );
        d.handle_event(&event(&repo, "person", "HIGH"), Utc::now()).unwrap();
        d.handle_event(&event(&repo, "person", "HIGH"), Utc::now()).unwrap();
        let s = repo.state().runs;
        assert_eq!((s[0].status.as_str(), s[1].status.as_str()), ("simulated", "skipped"));
        assert!(s[1].detail["reason"].as_str().unwrap().contains("quiet period"));

        let mut capped = row("a2", "simulate", json!([]), json!([{"type": "webhook", "connection": "c1"}]));
        capped.cooldown_s = 0;
        capped.max_per_hour = 2;
        let (repo2, mut d2) = setup(vec![capped], vec![webhook_conn("c1", "http://10.255.255.1/hook")]);
        for _ in 0..3 {
            d2.handle_event(&event(&repo2, "person", "HIGH"), Utc::now()).unwrap();
        }
        let st: Vec<String> = repo2.state().runs.iter().map(|r| r.status.clone()).collect();
        assert_eq!(st, vec!["simulated", "simulated", "skipped"]);
    }

    #[test]
    fn disabled_automations_and_disabled_connections_do_nothing() {
        let mut off = row("a1", "live", json!([]), json!([{"type": "webhook", "connection": "c1"}]));
        off.enabled = false;
        let (repo, mut d) = setup(vec![off], vec![webhook_conn("c1", "http://10.255.255.1/hook")]);
        d.handle_event(&event(&repo, "person", "HIGH"), Utc::now()).unwrap();
        assert!(repo.state().runs.is_empty());

        let mut conn = webhook_conn("c1", "http://10.255.255.1/hook");
        conn.enabled = false;
        let (repo2, mut d2) = setup(vec![row("a1", "live", json!([]), json!([{"type": "webhook", "connection": "c1"}]))], vec![conn]);
        d2.handle_event(&event(&repo2, "person", "HIGH"), Utc::now()).unwrap();
        let r = &repo2.state().runs[0];
        assert_eq!(r.status, "failed", "a live automation never silently drops an action");
        assert!(r.detail["actions"][0]["message"].as_str().unwrap().contains("missing, switched off"));
    }

    #[test]
    fn live_mode_really_sends_and_a_failing_target_is_reported_as_failed() {
        let server = Arc::new(tiny_http::Server::http("127.0.0.1:0").unwrap());
        let url = format!("http://{}/hook", server.server_addr().to_ip().unwrap());
        let got: Arc<Mutex<Vec<String>>> = Arc::default();
        let (s2, g2) = (server.clone(), got.clone());
        std::thread::spawn(move || {
            for mut req in s2.incoming_requests() {
                let mut b = String::new();
                let _ = std::io::Read::read_to_string(req.as_reader(), &mut b);
                g2.lock().unwrap().push(b);
                let _ = req.respond(tiny_http::Response::from_string("ok"));
            }
        });
        let (repo, mut d) = setup(vec![row("a1", "live", json!([]), json!([{"type": "webhook", "connection": "c1"}]))], vec![webhook_conn("c1", &url)]);
        d.handle_event(&event(&repo, "person", "HIGH"), Utc::now()).unwrap();
        assert_eq!(repo.state().runs[0].status, "sent");
        let body: Value = serde_json::from_str(&got.lock().unwrap()[0]).unwrap();
        assert_eq!(body["mode"], "live");
        assert_eq!(body["event"]["label"], "person");
        server.unblock();

        let (repo2, mut d2) = setup(vec![row("a1", "live", json!([]), json!([{"type": "webhook", "connection": "c1"}]))], vec![webhook_conn("c1", "http://127.0.0.1:1/x")]);
        d2.handle_event(&event(&repo2, "person", "HIGH"), Utc::now()).unwrap();
        assert_eq!(repo2.state().runs[0].status, "failed");
    }

    #[test]
    fn previews_send_nothing_and_connection_checks_are_run_from_the_queue() {
        let (repo, mut d) = setup(
            vec![row("a1", "live", json!([{"field": "label", "in": ["car"]}, {"field": "speed_kmh", "above": 60}]), json!([{"type": "webhook", "connection": "c1"}]))],
            vec![webhook_conn("c1", "http://127.0.0.1:1/hook")],
        );
        repo.queue_test(Some("a1"), None, "test_simulate");
        repo.queue_test(None, Some("c1"), "test_send");
        assert_eq!(d.run_queued_tests().unwrap(), 2);
        let runs = repo.state().runs;
        assert_eq!(runs[0].status, "simulated");
        let body: Value = serde_json::from_str(runs[0].detail["actions"][0]["would_send"].as_str().unwrap().split_once('\n').unwrap().1).unwrap();
        assert_eq!((body["test"].as_bool(), body["event"]["label"].as_str(), body["event"]["speed_kmh"].as_f64()), (Some(true), Some("car"), Some(65.0)), "the sample follows the conditions");
        assert_eq!(runs[1].status, "failed", "an unreachable address fails the check");
    }

    #[test]
    fn a_coil_is_pulsed_and_always_switched_back_off_even_at_shutdown() {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let frames: Arc<Mutex<Vec<[u8; 12]>>> = Arc::default();
        let f2 = frames.clone();
        std::thread::spawn(move || {
            for c in l.incoming().flatten().take(2) {
                let mut c = c;
                let mut req = [0u8; 12];
                if std::io::Read::read_exact(&mut c, &mut req).is_ok() {
                    f2.lock().unwrap().push(req);
                    let _ = std::io::Write::write_all(&mut c, &req);
                }
            }
        });
        let conn = Connection { id: "plc".into(), name: "PLC".into(), kind: "modbus".into(), config: json!({"host": "127.0.0.1", "port": port, "unit_id": 1}), enabled: true };
        let (repo, mut d) = setup(vec![row("a1", "live", json!([]), json!([{"type": "modbus", "connection": "plc", "function": "write_coil", "address": 3, "hold_s": 30}]))], vec![conn]);
        d.handle_event(&event(&repo, "person", "HIGH"), Utc::now()).unwrap();
        assert_eq!(repo.state().runs[0].status, "sent");
        assert_eq!(&frames.lock().unwrap()[0][8..], &[0, 3, 0xFF, 0x00], "coil ON");
        d.tick_offs(false);
        assert_eq!(frames.lock().unwrap().len(), 1, "30 s have not passed");
        d.tick_offs(true); // shutdown: never leave an output on
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(&frames.lock().unwrap()[1][8..], &[0, 3, 0, 0], "coil OFF");
    }
}
