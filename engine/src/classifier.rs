//! Server-side classification of event snapshots with a System One decision model.
//!
//! A separate thread claims events with `pipeline='queued'`, crops the stored snapshot around the detection box and asks
//! `POST {CLASSIFIER_URL}/v1/systemone` (Ollama serving e.g. Liquid AI `d1`) one `choice` question: person / vehicle /
//! false_alarm, plus one yes/no (`noul`) question per behaviour rule. The answer is only stored; it never dismisses an event or
//! touches incidents, and detection threads never wait on this module.
use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use base64::Engine as _;
use serde_json::{Value, json};
use tracing::{error, info, warn};

use crate::behavior::{Flag, Rule};
use crate::config::ClassifierTuning;
use crate::model::BBox;
use crate::render::crop_jpeg;
use crate::repo::Repository;

/// Hosted endpoints (e.g. the Runpod proxy sits behind Cloudflare) reject unidentified clients; always send our own.
pub const USER_AGENT: &str = "eyeai-worker/1.0";
pub const LABELS: [&str; 3] = ["person", "vehicle", "false_alarm"];
pub const STATE: &str = "Snapshot from a security camera, cropped around a possible detection.";
pub const CROP_MARGIN: f64 = 0.15;
pub const RULE_CROP_MARGIN: f64 = 0.5; // behaviour rules need hands and surroundings
const MIN_CROP_PX: i64 = 40;
const STATS_EVERY: Duration = Duration::from_secs(5);
const RECOVER_EVERY: Duration = Duration::from_secs(60);

/// Anything that makes one classification attempt fail (network, HTTP, malformed answer, bad image).
#[derive(Debug, Clone)]
pub struct ClassifierError(pub String);

impl fmt::Display for ClassifierError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ClassifierError {}

fn err<T>(msg: impl Into<String>) -> Result<T, ClassifierError> {
    Err(ClassifierError(msg.into()))
}

#[derive(Debug, Clone)]
pub struct ClassifierConfig {
    pub url: Option<String>,
    pub model: String,
    pub timeout_s: f64,
    pub poll_s: f64,
    pub max_attempts: u32,
    /// CLASSIFIER_BATCH=1: all rule questions in ONE request (Ollama d1 on CPU crashes with 2+)
    pub batch: bool,
    /// CLASSIFIER_RULES=0 disables behaviour rules
    pub rules_enabled: bool,
    /// CLASSIFIER_RULE_FRAMES: evaluate the first N snapshots, max probability wins
    pub rule_frames: usize,
    /// skip rules when more events than this are waiting
    pub rule_max_queue: i64,
}

impl Default for ClassifierConfig {
    fn default() -> Self {
        Self { url: None, model: "d1".into(), timeout_s: 60.0, poll_s: 2.0, max_attempts: 3, batch: false, rules_enabled: true, rule_frames: 1, rule_max_queue: 10 }
    }
}

impl ClassifierConfig {
    pub fn enabled(&self) -> bool {
        self.url.is_some()
    }

    /// Value of worker_heartbeats.classifier, e.g. "d1 @ http://classifier:11434".
    pub fn label(&self) -> Option<String> {
        self.url.as_ref().map(|u| format!("{} @ {u}", self.model))
    }

    pub fn from_env() -> Self {
        let vars: HashMap<String, String> = std::env::vars().collect();
        Self::from_map(&vars)
    }

    pub fn from_map(e: &HashMap<String, String>) -> Self {
        let get = |k: &str| e.get(k).map(|v| v.trim()).filter(|v| !v.is_empty());
        let d = Self::default();
        Self {
            url: get("CLASSIFIER_URL").map(|u| u.trim_end_matches('/').to_string()).filter(|u| !u.is_empty()),
            model: get("CLASSIFIER_MODEL").map_or(d.model, str::to_string),
            timeout_s: get("CLASSIFIER_TIMEOUT_S").and_then(|v| v.parse().ok()).unwrap_or(d.timeout_s),
            poll_s: get("CLASSIFIER_POLL_S").and_then(|v| v.parse().ok()).unwrap_or(d.poll_s),
            max_attempts: get("CLASSIFIER_MAX_ATTEMPTS").and_then(|v| v.parse().ok()).unwrap_or(d.max_attempts),
            batch: get("CLASSIFIER_BATCH") == Some("1"),
            rules_enabled: get("CLASSIFIER_RULES") != Some("0"),
            rule_frames: get("CLASSIFIER_RULE_FRAMES").and_then(|v| v.parse::<usize>().ok()).unwrap_or(1).max(1),
            rule_max_queue: get("CLASSIFIER_RULE_MAX_QUEUE").and_then(|v| v.parse().ok()).unwrap_or(d.rule_max_queue),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ClassifyJob {
    pub event_id: String,
    pub bbox: Option<BBox>,
    pub jpeg: Option<Vec<u8>>,
    /// detector label of the event (rule `applies_to` filter)
    pub label: String,
    /// first N (jpeg, bbox), for behaviour rules
    pub snapshots: Vec<(Vec<u8>, Option<BBox>)>,
    /// events still queued when this one was claimed
    pub queue_depth: i64,
}

#[derive(Debug, Clone)]
pub struct Classification {
    pub label: String,
    /// probability of `label`
    pub confidence: f32,
    pub probs: BTreeMap<String, f32>,
    pub model: String,
    pub ms: u64,
}

pub fn crop_for_classification(jpeg: &[u8], bbox: Option<&BBox>, margin: f64) -> Result<Vec<u8>, ClassifierError> {
    crop_jpeg(jpeg, bbox, margin, MIN_CROP_PX).map_err(|e| ClassifierError(format!("{e:#}")))
}

/// Minimal client for the System One wire (`/v1/systemone`) with one image.
pub struct DecisionClient {
    cfg: ClassifierConfig,
    agent: ureq::Agent,
}

impl DecisionClient {
    pub fn new(cfg: &ClassifierConfig) -> anyhow::Result<Self> {
        anyhow::ensure!(cfg.url.is_some(), "CLASSIFIER_URL is not set");
        let agent = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs_f64(cfg.timeout_s))
            .user_agent(USER_AGENT)
            .build();
        Ok(Self { cfg: cfg.clone(), agent })
    }

    fn images(jpeg: &[u8]) -> Value {
        json!([base64::engine::general_purpose::STANDARD.encode(jpeg)])
    }

    pub fn request_body(&self, jpeg: &[u8]) -> Value {
        json!({
            "model": self.cfg.model,
            "state": STATE,
            "images": Self::images(jpeg),
            "questions": {"class": {
                "type": "choice",
                "instructions": "What does this security-camera snapshot show?",
                "criteria": {
                    "person": "A person is clearly visible",
                    "vehicle": "A car, truck, bus or motorcycle is clearly visible",
                    "false_alarm": "No person or vehicle: shadow, lighting, animal, goods or an empty scene",
                },
            }},
        })
    }

    /// yes/no (`noul`) questions about the same snapshot; same state wording as the class call.
    pub fn rule_request_body(&self, jpeg: &[u8], questions: &[(String, String)]) -> Value {
        let qs: serde_json::Map<String, Value> =
            questions.iter().map(|(k, q)| (k.clone(), json!({"type": "noul", "instructions": q}))).collect();
        json!({"model": self.cfg.model, "state": STATE, "images": Self::images(jpeg), "questions": qs})
    }

    /// -> {rule key: P(yes)}. One request for all questions (batch) or exactly one question per call.
    pub fn ask_rules(&self, jpeg: &[u8], questions: &[(String, String)]) -> Result<HashMap<String, f32>, ClassifierError> {
        let (payload, _) = self.post(&self.rule_request_body(jpeg, questions))?;
        let mut out = HashMap::new();
        for (key, _) in questions {
            let Some(p) = payload["answers"][key.as_str()]["noul"].as_f64() else {
                return err(format!("unexpected answer for rule {key}: {}", trunc(&payload.to_string(), 150)));
            };
            if !(0.0..=1.0).contains(&p) {
                return err(format!("probability out of range for rule {key}: {p}"));
            }
            out.insert(key.clone(), p as f32);
        }
        Ok(out)
    }

    pub fn classify(&self, jpeg: &[u8]) -> Result<Classification, ClassifierError> {
        let (payload, ms) = self.post(&self.request_body(jpeg))?;
        self.parse(&payload, ms)
    }

    fn post(&self, body: &Value) -> Result<(Value, u64), ClassifierError> {
        let url = format!("{}/v1/systemone", self.cfg.url.as_deref().unwrap_or_default());
        let t0 = Instant::now();
        let resp = match self.agent.post(&url).set("Content-Type", "application/json").send_json(body) {
            Ok(r) => r,
            Err(ureq::Error::Status(code, r)) => {
                let detail = r.into_string().unwrap_or_default();
                return err(format!("HTTP {code} from classifier {}", trunc(&detail, 200)).trim().to_string());
            }
            Err(ureq::Error::Transport(t)) => return err(format!("classifier unreachable: {t}")),
        };
        let payload: Value = match resp.into_json() {
            Ok(v) => v,
            Err(e) => return err(format!("classifier returned invalid JSON: {e}")),
        };
        Ok((payload, t0.elapsed().as_millis() as u64))
    }

    pub fn parse(&self, payload: &Value, ms: u64) -> Result<Classification, ClassifierError> {
        let bad = || ClassifierError(format!("unexpected classifier answer: {}", trunc(&payload.to_string(), 150)));
        let ans = payload.get("answers").and_then(|a| a.get("class")).ok_or_else(bad)?;
        let choice = ans.get("choice").and_then(Value::as_str).ok_or_else(bad)?;
        let probs: BTreeMap<String, f32> = ans
            .get("probabilities")
            .and_then(Value::as_object)
            .ok_or_else(bad)?
            .iter()
            .map(|(k, v)| v.as_f64().map(|f| (k.clone(), f as f32)).ok_or_else(bad))
            .collect::<Result<_, _>>()?;
        if !LABELS.contains(&choice) || !probs.contains_key(choice) {
            return err(format!("unknown class {choice:?}"));
        }
        Ok(Classification { label: choice.to_string(), confidence: probs[choice], probs: probs.clone(), model: self.cfg.model.clone(), ms })
    }
}

fn trunc(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// The classifier thread. `run_once` is one claim+classify step (what the tests drive).
pub struct ClassifierService {
    repo: Arc<dyn Repository>,
    pub cfg: ClassifierConfig,
    client: Option<DecisionClient>,
    rules: Box<dyn Fn() -> Vec<Rule> + Send + Sync>,
    tuning: Box<dyn Fn() -> ClassifierTuning + Send + Sync>,
    attempts: HashMap<String, u32>,
    retry_at: HashMap<String, Instant>,
    stats: Arc<Mutex<(i64, i64)>>,
}

impl ClassifierService {
    pub fn new(
        repo: Arc<dyn Repository>, cfg: ClassifierConfig, rules: Box<dyn Fn() -> Vec<Rule> + Send + Sync>,
        tuning: Box<dyn Fn() -> ClassifierTuning + Send + Sync>,
    ) -> anyhow::Result<Self> {
        let client = if cfg.enabled() { Some(DecisionClient::new(&cfg)?) } else { None };
        Ok(Self { repo, cfg, client, rules, tuning, attempts: HashMap::new(), retry_at: HashMap::new(), stats: Arc::new(Mutex::new((0, 0))) })
    }

    /// A handle for /health that keeps working after the service moved into its thread.
    pub fn status_fn(&self) -> Arc<dyn Fn() -> Value + Send + Sync> {
        let (cfg, stats) = (self.cfg.clone(), self.stats.clone());
        Arc::new(move || {
            let (queued, failed) = *stats.lock().unwrap_or_else(|e| e.into_inner());
            json!({
                "enabled": cfg.enabled(), "model": cfg.enabled().then(|| cfg.model.clone()), "url": cfg.url,
                "queued": queued, "failed_recent": failed,
            })
        })
    }

    /// Classify at most one queued event. Returns true when an event was processed (success or failure).
    pub fn run_once(&mut self, now: Instant) -> anyhow::Result<bool> {
        let backoff: Vec<String> = self.retry_at.iter().filter(|(_, t)| **t > now).map(|(i, _)| i.clone()).collect();
        // dashboard settings (engine_settings 'defaults' -> classifier) override the environment, live
        let t = (self.tuning)();
        let mut cfg = self.cfg.clone();
        cfg.rules_enabled = t.rules_enabled.unwrap_or(cfg.rules_enabled);
        cfg.rule_frames = t.rule_frames.unwrap_or(cfg.rule_frames);
        cfg.rule_max_queue = t.rule_max_queue.unwrap_or(cfg.rule_max_queue);
        cfg.batch = t.batch.unwrap_or(cfg.batch);
        let frames = if cfg.rules_enabled { cfg.rule_frames } else { 1 };
        let Some(job) = self.repo.claim_queued_event(&backoff, frames)? else { return Ok(false) };
        let Some(jpeg) = job.jpeg.as_deref() else {
            self.repo.store_classification_failure(&job.event_id, "event has no snapshot", false)?;
            return Ok(true);
        };
        let client = self.client.as_ref().expect("run_once needs an enabled classifier");
        let started = Instant::now();
        let result = crop_for_classification(jpeg, job.bbox.as_ref(), CROP_MARGIN).and_then(|c| client.classify(&c));
        let mut result = match result {
            Ok(r) => r,
            Err(e) => {
                let n = self.attempts.get(&job.event_id).copied().unwrap_or(0) + 1;
                if n < self.cfg.max_attempts {
                    self.attempts.insert(job.event_id.clone(), n);
                    let wait = (self.cfg.poll_s * f64::from(1u32 << n)).min(30.0);
                    self.retry_at.insert(job.event_id.clone(), now + Duration::from_secs_f64(wait));
                    warn!("event {} classification failed (attempt {n}/{}): {e}", job.event_id, self.cfg.max_attempts);
                    self.repo.store_classification_failure(&job.event_id, &e.to_string(), true)?;
                } else {
                    error!("event {} classification failed for good after {n} attempts: {e}", job.event_id);
                    self.attempts.remove(&job.event_id);
                    self.retry_at.remove(&job.event_id);
                    self.repo.store_classification_failure(&job.event_id, &e.to_string(), false)?;
                }
                return Ok(true);
            }
        };
        self.attempts.remove(&job.event_id);
        self.retry_at.remove(&job.event_id);
        let class_ms = result.ms;
        let (flags, rule_error) = self.evaluate_rules(&cfg, &job, &result);
        result.ms = started.elapsed().as_millis() as u64; // cls_ms = class + rules
        self.repo.store_classification(&job.event_id, &result, &flags, rule_error.as_deref())?;
        let hits: Vec<&str> = flags.iter().filter(|f| f.triggered).map(|f| f.rule.as_str()).collect();
        info!(
            "event {} -> {} {:.2} (class {class_ms} ms, total {} ms, {} rules{})", job.event_id, result.label, result.confidence,
            result.ms, flags.len(), if hits.is_empty() { String::new() } else { format!(", indicators: {}", hits.join(",")) }
        );
        Ok(true)
    }

    /// Ask the yes/no rule questions. Never fails and never loses the class result (a failed rule has prob None).
    fn evaluate_rules(&self, cfg: &ClassifierConfig, job: &ClassifyJob, result: &Classification) -> (Vec<Flag>, Option<String>) {
        let client = self.client.as_ref().expect("enabled");
        if !cfg.rules_enabled || result.label == "false_alarm" {
            return (vec![], None);
        }
        if job.queue_depth > cfg.rule_max_queue {
            info!("event {}: {} events queued, behaviour rules skipped", &job.event_id[..8.min(job.event_id.len())], job.queue_depth);
            return (vec![], None);
        }
        let rules: Vec<Rule> = (self.rules)()
            .into_iter()
            .filter(|r| r.kind == "classifier" && r.question.is_some() && r.applies_to.contains(&job.label))
            .collect();
        if rules.is_empty() {
            return (vec![], None);
        }
        let mut probs: HashMap<&str, Option<f32>> = rules.iter().map(|r| (r.key.as_str(), None)).collect();
        let mut errors: Vec<String> = Vec::new();
        let fallback = vec![(job.jpeg.clone().unwrap_or_default(), job.bbox)];
        let shots = if job.snapshots.is_empty() { &fallback } else { &job.snapshots };
        for (jpeg, bbox) in shots.iter().take(cfg.rule_frames) {
            if jpeg.is_empty() {
                continue;
            }
            let ctx = match crop_for_classification(jpeg, bbox.as_ref(), RULE_CROP_MARGIN) {
                Ok(c) => c,
                Err(e) => {
                    errors.push(e.to_string());
                    continue;
                }
            };
            let all: Vec<(String, String)> = rules.iter().map(|r| (r.key.clone(), r.question.clone().unwrap_or_default())).collect();
            let groups: Vec<Vec<(String, String)>> = if cfg.batch { vec![all] } else { all.into_iter().map(|q| vec![q]).collect() };
            for qs in groups {
                match client.ask_rules(&ctx, &qs) {
                    Ok(got) => {
                        for (k, p) in got {
                            let slot = probs.get_mut(k.as_str()).expect("asked");
                            *slot = Some(slot.map_or(p, |old| old.max(p)));
                        }
                    }
                    Err(e) => {
                        warn!("event {}: rule request failed: {e}", &job.event_id[..8.min(job.event_id.len())]);
                        errors.push(e.to_string());
                    }
                }
            }
        }
        let flags: Vec<Flag> = rules
            .iter()
            .map(|r| {
                let p = probs[r.key.as_str()];
                let hit = p.is_some_and(|p| p >= r.threshold);
                Flag {
                    rule: r.key.clone(), title: r.title.clone(), prob: p.map(|p| (p * 10_000.0).round() / 10_000.0), threshold: r.threshold,
                    severity: r.severity.clone(), triggered: hit, opens_incident: r.opens_incident,
                    detail: hit.then(|| format!("P(yes) {:.2} >= {:.2} - model estimate, needs human review", p.unwrap_or_default(), r.threshold)),
                }
            })
            .collect();
        let err = (!errors.is_empty() && probs.values().all(Option::is_none)).then(|| format!("behaviour rules unavailable: {}", errors[0]));
        (flags, err)
    }

    pub fn run_forever(mut self, stop: Arc<AtomicBool>) {
        let (mut last_stats, mut last_recover) = (Instant::now() - STATS_EVERY, Instant::now());
        if self.cfg.enabled() {
            match self.repo.recover_stuck_classifying(2.0 * self.cfg.timeout_s) {
                Ok(n) if n > 0 => info!("requeued {n} events stuck in 'classifying'"),
                Ok(_) => {}
                Err(e) => error!("recovering stuck events failed: {e:#}"),
            }
        }
        while !stop.load(Ordering::Relaxed) {
            let busy = self.iterate(&mut last_recover).unwrap_or_else(|e| {
                error!("classifier iteration failed: {e:#}");
                false
            });
            if last_stats.elapsed() > STATS_EVERY {
                if let Ok(s) = self.repo.classifier_stats() {
                    *self.stats.lock().unwrap_or_else(|e| e.into_inner()) = s;
                }
                last_stats = Instant::now();
            }
            if !busy {
                std::thread::sleep(Duration::from_secs_f64(self.cfg.poll_s));
            }
        }
    }

    fn iterate(&mut self, last_recover: &mut Instant) -> anyhow::Result<bool> {
        if !self.cfg.enabled() {
            let n = self.repo.skip_all_queued()?;
            if n > 0 {
                info!("no classifier configured: {n} events marked 'skipped'");
            }
            return Ok(false);
        }
        let busy = self.run_once(Instant::now())?;
        if last_recover.elapsed() > RECOVER_EVERY {
            self.repo.recover_stuck_classifying(2.0 * self.cfg.timeout_s)?;
            *last_recover = Instant::now();
        }
        Ok(busy)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::render::encode_jpeg;
    use crate::testutil::{FakeRepo, StubServer};
    use image::RgbImage;

    fn jpeg() -> Vec<u8> {
        encode_jpeg(&RgbImage::from_pixel(640, 360, image::Rgb([40, 80, 120])), 85).unwrap()
    }

    fn cfg(url: &str) -> ClassifierConfig {
        ClassifierConfig { url: Some(url.into()), poll_s: 0.01, timeout_s: 5.0, ..Default::default() }
    }

    fn rule(key: &str, threshold: f32) -> Rule {
        Rule {
            key: key.into(), title: key.into(), kind: "classifier".into(), question: Some(format!("Is there a {key}?")),
            applies_to: vec!["person".into()], threshold, config: json!({}), severity: "CRITICAL".into(), opens_incident: true, position: 0,
        }
    }

    fn service(repo: &Arc<FakeRepo>, cfg: ClassifierConfig, rules: Vec<Rule>) -> ClassifierService {
        ClassifierService::new(repo.clone(), cfg, Box::new(move || rules.clone()), Box::new(ClassifierTuning::default)).unwrap()
    }

    fn queued(repo: &FakeRepo) -> String {
        repo.add_queued(Some(jpeg()), Some(BBox { x: 0.3, y: 0.3, w: 0.2, h: 0.4 }), "person")
    }

    #[test]
    fn config_defaults_disabled_and_from_env_map() {
        let c = ClassifierConfig::from_map(&HashMap::new());
        assert!(!c.enabled() && c.label().is_none() && c.model == "d1" && c.timeout_s == 60.0 && c.poll_s == 2.0 && c.max_attempts == 3);
        let m: HashMap<String, String> = [("CLASSIFIER_URL", "http://classifier:11434/"), ("CLASSIFIER_MODEL", "clef"), ("CLASSIFIER_TIMEOUT_S", "5"),
            ("CLASSIFIER_BATCH", "1"), ("CLASSIFIER_RULES", "0")].iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let c = ClassifierConfig::from_map(&m);
        assert!(c.enabled() && c.url.as_deref() == Some("http://classifier:11434") && c.label().as_deref() == Some("clef @ http://classifier:11434"));
        assert!(c.batch && !c.rules_enabled && c.timeout_s == 5.0);
    }

    #[test]
    fn request_sends_an_explicit_user_agent() {
        // Cloudflare in front of hosted endpoints (e.g. the Runpod proxy) answers 403/1010 to default client User-Agents.
        let s = StubServer::start();
        DecisionClient::new(&cfg(&s.url)).unwrap().classify(b"\xff\xd8fakejpeg").unwrap();
        assert_eq!(s.requests.lock().unwrap()[0].user_agent.as_deref(), Some("eyeai-worker/1.0"));
    }

    #[test]
    fn request_body_matches_the_spec_exactly() {
        let s = StubServer::start();
        let r = DecisionClient::new(&cfg(&s.url)).unwrap().classify(b"\xff\xd8fakejpeg").unwrap();
        let req = s.requests.lock().unwrap()[0].clone();
        assert_eq!(req.path, "/v1/systemone");
        assert_eq!(req.body, json!({
            "model": "d1",
            "state": "Snapshot from a security camera, cropped around a possible detection.",
            "images": [base64::engine::general_purpose::STANDARD.encode(b"\xff\xd8fakejpeg")],
            "questions": {"class": {
                "type": "choice",
                "instructions": "What does this security-camera snapshot show?",
                "criteria": {
                    "person": "A person is clearly visible",
                    "vehicle": "A car, truck, bus or motorcycle is clearly visible",
                    "false_alarm": "No person or vehicle: shadow, lighting, animal, goods or an empty scene",
                },
            }},
        }));
        assert!(r.label == "person" && (r.confidence - 0.91).abs() < 1e-6 && (r.probs["false_alarm"] - 0.07).abs() < 1e-6 && r.model == "d1");
    }

    #[test]
    fn malformed_answers_raise() {
        for bad in [r#"{"answers":{}}"#, r#"{"answers":{"class":{"choice":"banana","probabilities":{"banana":1}}}}"#,
                    r#"{"answers":{"class":{"choice":"person"}}}"#, r#"{"nope":1}"#, "[]"] {
            let s = StubServer::start();
            s.cfg.lock().unwrap().body = Some(bad.into());
            assert!(DecisionClient::new(&cfg(&s.url)).unwrap().classify(b"x").is_err(), "{bad}");
        }
    }

    #[test]
    fn http_500_invalid_json_and_unreachable_are_classifier_errors() {
        let s = StubServer::start();
        {
            let mut c = s.cfg.lock().unwrap();
            c.status = 500;
            c.body = Some(r#"{"error":"llama-server terminated"}"#.into());
        }
        let client = DecisionClient::new(&cfg(&s.url)).unwrap();
        assert!(client.classify(b"x").unwrap_err().0.contains("HTTP 500"));
        {
            let mut c = s.cfg.lock().unwrap();
            c.status = 200;
            c.body = Some("<html>".into());
        }
        assert!(client.classify(b"x").unwrap_err().0.contains("invalid JSON"));
        let dead = DecisionClient::new(&ClassifierConfig { timeout_s: 2.0, ..cfg("http://127.0.0.1:1") }).unwrap();
        assert!(dead.classify(b"x").unwrap_err().0.contains("unreachable"));
    }

    #[test]
    fn success_stores_the_result_and_the_classifier_gets_the_crop() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        let id = queued(&repo);
        let mut svc = service(&repo, cfg(&s.url), vec![]);
        assert!(svc.run_once(Instant::now()).unwrap());
        let sent = base64::engine::general_purpose::STANDARD.decode(s.requests.lock().unwrap()[0].body["images"][0].as_str().unwrap()).unwrap();
        let img = image::load_from_memory(&sent).unwrap();
        assert!(img.width() < 640, "classifier should receive the crop, not the full frame");
        let e = &repo.state().events[&id];
        assert!(e.pipeline == "ready" && e.cls_label.as_deref() == Some("person") && e.cls_model.as_deref() == Some("d1"));
        assert!(!svc.run_once(Instant::now()).unwrap(), "nothing left");
    }

    #[test]
    fn failure_retries_with_backoff_then_fails_for_good() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        s.cfg.lock().unwrap().status = 500;
        let id = queued(&repo);
        let mut svc = service(&repo, ClassifierConfig { max_attempts: 3, ..cfg(&s.url) }, vec![]);
        let t = Instant::now();
        assert!(svc.run_once(t).unwrap());
        assert_eq!(repo.state().events[&id].pipeline, "queued");
        assert!(repo.state().events[&id].cls_error.as_deref().unwrap().contains("HTTP 500"));
        assert!(!svc.run_once(t + Duration::from_millis(1)).unwrap(), "still in backoff");
        assert_eq!(s.hits(), 1);
        assert!(svc.run_once(t + Duration::from_secs(100)).unwrap());
        assert_eq!(repo.state().events[&id].pipeline, "queued");
        assert!(svc.run_once(t + Duration::from_secs(200)).unwrap());
        assert_eq!(repo.state().events[&id].pipeline, "failed");
        assert_eq!(s.hits(), 3);
        assert!(!svc.run_once(t + Duration::from_secs(300)).unwrap(), "failed events are not retried");
    }

    #[test]
    fn event_without_snapshot_fails_without_calling_the_model() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        let id = repo.add_queued(None, None, "person");
        service(&repo, cfg(&s.url), vec![]).run_once(Instant::now()).unwrap();
        assert_eq!(s.hits(), 0);
        let e = &repo.state().events[&id];
        assert!(e.pipeline == "failed" && e.cls_error.as_deref().unwrap().contains("no snapshot"));
    }

    #[test]
    fn rules_trigger_at_the_threshold_and_open_the_consequences() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        s.cfg.lock().unwrap().noul = HashMap::from([("weapon".to_string(), 0.8), ("mask".to_string(), 0.3)]);
        let id = queued(&repo);
        let mut svc = service(&repo, cfg(&s.url), vec![rule("weapon", 0.7), rule("mask", 0.7)]);
        svc.run_once(Instant::now()).unwrap();
        let e = repo.state().events[&id].clone();
        assert!(e.suspicious && e.severity == "CRITICAL");
        assert_eq!(e.flags.len(), 2);
        assert!(e.flags.iter().any(|f| f["rule"] == "weapon" && f["triggered"] == json!(true)));
        assert!(e.flags.iter().any(|f| f["rule"] == "mask" && f["triggered"] == json!(false)));
        assert_eq!(s.hits(), 3, "class question + one request per rule by default");
    }

    #[test]
    fn batch_mode_asks_all_rules_in_one_request() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        queued(&repo);
        let mut svc = service(&repo, ClassifierConfig { batch: true, ..cfg(&s.url) }, vec![rule("weapon", 0.7), rule("mask", 0.7)]);
        svc.run_once(Instant::now()).unwrap();
        assert_eq!(s.hits(), 2);
        assert_eq!(s.requests.lock().unwrap()[1].body["questions"].as_object().unwrap().len(), 2);
    }

    #[test]
    fn false_alarms_skip_rules_and_a_crashing_rule_request_keeps_the_class_result() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        s.cfg.lock().unwrap().body = Some(GOOD_FALSE_ALARM.into());
        queued(&repo);
        service(&repo, cfg(&s.url), vec![rule("weapon", 0.7)]).run_once(Instant::now()).unwrap();
        assert_eq!(s.hits(), 1, "no rule questions for a false alarm");

        let (s2, repo2) = (StubServer::start(), Arc::new(FakeRepo::default()));
        s2.cfg.lock().unwrap().crash_multi = true;
        let id = queued(&repo2);
        let mut svc = service(&repo2, ClassifierConfig { batch: true, ..cfg(&s2.url) }, vec![rule("weapon", 0.7), rule("mask", 0.7)]);
        svc.run_once(Instant::now()).unwrap();
        let e = &repo2.state().events[&id];
        assert!(e.pipeline == "ready" && e.cls_label.as_deref() == Some("person"), "class result survives");
        assert!(e.cls_error.as_deref().unwrap().contains("behaviour rules unavailable"));
        assert!(!e.suspicious);
    }

    #[test]
    fn rules_are_skipped_when_the_queue_is_long() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        for _ in 0..4 {
            queued(&repo);
        }
        let mut svc = service(&repo, ClassifierConfig { rule_max_queue: 2, ..cfg(&s.url) }, vec![rule("weapon", 0.7)]);
        svc.run_once(Instant::now()).unwrap();
        assert_eq!(s.hits(), 1);
    }

    const GOOD_FALSE_ALARM: &str = r#"{"model":"d1","answers":{"class":{"type":"choice","choice":"false_alarm","probabilities":{"person":0.05,"vehicle":0.02,"false_alarm":0.93},"confidence":0.8}}}"#;

    #[test]
    fn dashboard_tuning_overrides_the_environment_live() {
        let (s, repo) = (StubServer::start(), Arc::new(FakeRepo::default()));
        queued(&repo);
        let tuning = Arc::new(Mutex::new(ClassifierTuning { rules_enabled: Some(false), ..Default::default() }));
        let t2 = tuning.clone();
        let mut svc = ClassifierService::new(repo.clone(), cfg(&s.url), Box::new(|| vec![rule("weapon", 0.7)]), Box::new(move || t2.lock().unwrap().clone())).unwrap();
        svc.run_once(Instant::now()).unwrap();
        assert_eq!(s.hits(), 1, "rules switched off from the dashboard: only the class question");
        queued(&repo);
        *tuning.lock().unwrap() = ClassifierTuning::default();
        svc.run_once(Instant::now()).unwrap();
        assert_eq!(s.hits(), 3, "switched back on: class + the weapon question");
    }
}
