//! Behaviour rules: indicators for a human to review, never verdicts (docs/ARCHITECTURE.md "Behaviour rules").
//!
//! Flags come from the classifier (kind `classifier`, one yes/no question to the System One model) and from the snapshot
//! burst (kind `indication`, derived from the track). They are merged into `events.cls_flags` in one row-locked transaction,
//! so either order works. A trigger marks the event suspicious, raises (never lowers) event and incident severity, adds a
//! timeline entry and may open an incident. Nothing here changes `events.status` or dismisses anything.
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::Result;
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use tracing::{error, info};

use crate::i18n::{self, Lang};
use crate::model::{higher, sev_rank};
use crate::repo::{Repository, Tx};

#[derive(Debug, Clone)]
pub struct Rule {
    pub key: String,
    pub title: String,
    /// `classifier` | `indication`
    pub kind: String,
    pub question: Option<String>,
    pub applies_to: Vec<String>,
    pub threshold: f32,
    pub config: Value,
    pub severity: String,
    pub opens_incident: bool,
    pub position: i16,
}

#[derive(Debug, Clone)]
pub struct Flag {
    pub rule: String,
    pub title: String,
    /// P(yes); `None` for indication rules and for requests that failed
    pub prob: Option<f32>,
    pub threshold: f32,
    pub severity: String,
    pub triggered: bool,
    // not stored in cls_flags:
    pub opens_incident: bool,
    pub detail: Option<String>,
}

impl Flag {
    pub fn to_json(&self) -> Value {
        json!({"rule": self.rule, "title": self.title, "prob": self.prob, "threshold": self.threshold,
               "severity": self.severity, "triggered": self.triggered})
    }
}

#[derive(Debug, Clone)]
pub struct EventState {
    pub id: String,
    pub incident_id: Option<String>,
    pub incident_severity: Option<String>,
    pub label: String,
    pub severity: String,
    pub source_id: String,
    pub source_name: String,
    pub flags: Vec<Value>,
    pub suspicious: bool,
}

/// Enabled rules, refreshed at most every `ttl`; keeps serving the last good list if the DB hiccups.
pub struct RuleCache {
    repo: Arc<dyn Repository>,
    ttl: Duration,
    state: Mutex<(Vec<Rule>, Option<Instant>)>,
}

impl RuleCache {
    pub fn new(repo: Arc<dyn Repository>, ttl: Duration) -> Self {
        Self { repo, ttl, state: Mutex::new((Vec::new(), None)) }
    }

    pub fn get(&self) -> Vec<Rule> {
        let mut st = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if st.1.is_none_or(|at| at.elapsed() >= self.ttl) {
            match self.repo.list_behavior_rules() {
                Ok(rules) => st.0 = rules,
                Err(e) => error!("loading behaviour rules failed; using the previous list: {e:#}"),
            }
            st.1 = Some(Instant::now());
        }
        st.0.clone()
    }
}

/// Evaluate kind=`indication` rules for a finished burst.
pub fn indication_flags(l: Lang, rules: &[Rule], label: &str, kind: &str, burst_ms: i32, sentence: &str) -> Vec<Flag> {
    rules
        .iter()
        .filter(|r| r.kind == "indication" && r.applies_to.iter().any(|l| l == label))
        .map(|r| {
            let want = r.config.get("indication_kind").and_then(Value::as_str);
            let min_ms = r.config.get("min_burst_ms").and_then(Value::as_i64).unwrap_or(0);
            let hit = want == Some(kind) && i64::from(burst_ms) >= min_ms;
            Flag {
                rule: r.key.clone(), title: r.title.clone(), prob: None, threshold: r.threshold, severity: r.severity.clone(),
                triggered: hit, opens_incident: r.opens_incident,
                detail: Some(i18n::pattern_rule_detail(l, sentence, min_ms as f64 / 1000.0)),
            }
        })
        .collect()
}

/// Best certainty per `object` rule key over the follow-up photos: (score, detector label of the object).
pub type ObjectHits = std::collections::HashMap<String, (f32, String)>;

/// Evaluate kind=`object` rules (a knife, scissors, ... seen next to the person) for a finished burst. A rule that saw nothing yields
/// an untriggered flag with no certainty, so the event shows it was checked.
pub fn object_flags(l: Lang, rules: &[Rule], label: &str, hits: &ObjectHits) -> Vec<Flag> {
    rules
        .iter()
        .filter(|r| r.kind == "object" && r.applies_to.iter().any(|l| l == label))
        .map(|r| {
            let best = hits.get(&r.key);
            let hit = best.is_some_and(|(s, _)| *s >= r.threshold);
            Flag {
                rule: r.key.clone(), title: r.title.clone(), prob: best.map(|(s, _)| (s * 10_000.0).round() / 10_000.0), threshold: r.threshold,
                severity: r.severity.clone(), triggered: hit, opens_incident: r.opens_incident,
                detail: best.filter(|_| hit).map(|(s, o)| i18n::object_detail(l, o, *s)),
            }
        })
        .collect()
}

/// Merge `new` flags into the event (row-locked), then apply the consequences of the triggered ones.
pub fn apply_flags(tx: &mut dyn Tx, event_id: &str, new: &[Flag], now: DateTime<Utc>) -> Result<()> {
    if new.is_empty() {
        return Ok(());
    }
    let Some(st) = tx.lock_event(event_id)? else { return Ok(()) };
    let keys: Vec<&str> = new.iter().map(|f| f.rule.as_str()).collect();
    let before: Vec<&str> = st.flags.iter().filter(|f| f["triggered"] == json!(true)).filter_map(|f| f["rule"].as_str()).collect();
    let mut merged: Vec<Value> = st.flags.iter().filter(|f| !f["rule"].as_str().is_some_and(|r| keys.contains(&r))).cloned().collect();
    merged.extend(new.iter().map(Flag::to_json));
    let triggered: Vec<&Flag> = new.iter().filter(|f| f.triggered).collect();
    if triggered.is_empty() {
        return tx.set_event_flags(event_id, &merged, st.suspicious, &st.severity);
    }
    let fresh: Vec<&Flag> = triggered.iter().copied().filter(|f| !before.contains(&f.rule.as_str())).collect(); // no duplicate timeline
    let top = triggered.iter().max_by_key(|f| sev_rank(&f.severity)).expect("non-empty");
    let severity = higher(&st.severity, &top.severity).to_string();
    let mut incident_id = st.incident_id.clone();
    if let Some(id) = &incident_id {
        if sev_rank(&top.severity) > sev_rank(st.incident_severity.as_deref().unwrap_or("LOW")) {
            tx.set_incident_severity(id, &top.severity)?;
        }
    } else if let Some(o) = triggered.iter().filter(|f| f.opens_incident).max_by_key(|f| sev_rank(&f.severity)) {
        let (id, code) = tx.create_incident(
            &i18n::suspicious_incident(i18n::lang(), &o.title, &st.source_name), &o.severity, &st.source_id, &st.label, now,
        )?;
        tx.link_event_incident(event_id, &id)?;
        info!("event {}: indicator '{}' opened {code}", &event_id[..8.min(event_id.len())], o.title);
        incident_id = Some(id);
    }
    tx.set_event_flags(event_id, &merged, true, &severity)?;
    if let Some(id) = incident_id {
        for f in fresh {
            tx.add_timeline(&id, "system", &i18n::warning_sign_timeline(i18n::lang(), &f.title), f.detail.as_deref(), Some(event_id), now)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::FakeRepo;

    fn flag(rule: &str, sev: &str, triggered: bool, opens: bool) -> Flag {
        Flag { rule: rule.into(), title: rule.into(), prob: Some(0.9), threshold: 0.7, severity: sev.into(), triggered,
               opens_incident: opens, detail: Some("d".into()) }
    }

    fn apply(repo: &FakeRepo, id: &str, flags: &[Flag]) {
        repo.transaction(&mut |tx| apply_flags(tx, id, flags, Utc::now())).unwrap();
    }

    #[test]
    fn trigger_marks_suspicious_raises_severity_and_logs_once() {
        let repo = FakeRepo::default();
        let (ev, inc) = repo.seed_event("person", "MEDIUM", Some("MEDIUM"));
        apply(&repo, &ev, &[flag("weapon", "CRITICAL", true, true)]);
        apply(&repo, &ev, &[flag("weapon", "CRITICAL", true, true)]);
        let s = repo.state();
        assert!(s.events[&ev].suspicious);
        assert_eq!(s.events[&ev].severity, "CRITICAL");
        assert_eq!(s.incidents[inc.as_ref().unwrap()].severity, "CRITICAL");
        assert_eq!(s.timeline.len(), 1, "re-evaluating the same trigger must not duplicate the timeline entry");
    }

    #[test]
    fn untriggered_flags_are_stored_but_change_nothing() {
        let repo = FakeRepo::default();
        let (ev, _) = repo.seed_event("person", "MEDIUM", Some("MEDIUM"));
        apply(&repo, &ev, &[flag("weapon", "CRITICAL", false, true)]);
        let s = repo.state();
        assert_eq!(s.events[&ev].flags.len(), 1);
        assert!(!s.events[&ev].suspicious);
        assert_eq!(s.events[&ev].severity, "MEDIUM");
        assert!(s.timeline.is_empty());
    }

    #[test]
    fn severity_never_goes_down() {
        let repo = FakeRepo::default();
        let (ev, inc) = repo.seed_event("person", "HIGH", Some("HIGH"));
        apply(&repo, &ev, &[flag("lingering", "LOW", true, true)]);
        let s = repo.state();
        assert_eq!(s.events[&ev].severity, "HIGH");
        assert_eq!(s.incidents[inc.as_ref().unwrap()].severity, "HIGH");
    }

    #[test]
    fn opens_an_incident_for_an_event_that_had_none_only_if_the_rule_says_so() {
        let repo = FakeRepo::default();
        let (ev, _) = repo.seed_event("bus", "LOW", None);
        apply(&repo, &ev, &[flag("a", "HIGH", true, false)]);
        assert!(repo.state().events[&ev].incident_id.is_none());
        apply(&repo, &ev, &[flag("b", "HIGH", true, true)]);
        let s = repo.state();
        let id = s.events[&ev].incident_id.clone().expect("incident opened");
        assert_eq!(s.incidents[&id].severity, "HIGH");
    }

    #[test]
    fn indication_rule_needs_the_kind_and_the_minimum_duration() {
        let rule = Rule {
            key: "lingering".into(), title: "Lingering".into(), kind: "indication".into(), question: None,
            applies_to: vec!["person".into()], threshold: 0.5, config: json!({"indication_kind": "lingering", "min_burst_ms": 5000}),
            severity: "MEDIUM".into(), opens_incident: true, position: 0,
        };
        let f = |kind, ms| indication_flags(Lang::En, std::slice::from_ref(&rule), "person", kind, ms, "s")[0].triggered;
        assert!(f("lingering", 5200));
        assert!(!f("lingering", 4000));
        assert!(!f("moving", 9000));
        assert!(indication_flags(Lang::En, std::slice::from_ref(&rule), "car", "lingering", 9000, "s").is_empty());
    }

    #[test]
    fn object_rule_triggers_on_the_best_certainty_and_reports_which_object() {
        let rule = Rule {
            key: "weapon_object".into(), title: "Knife".into(), kind: "object".into(), question: None, applies_to: vec!["person".into()],
            threshold: 0.35, config: json!({"objects": ["knife"], "margin": 0.3}), severity: "HIGH".into(), opens_incident: true, position: 0,
        };
        let hits = ObjectHits::from([("weapon_object".to_string(), (0.52, "knife".to_string()))]);
        let f = object_flags(Lang::En, std::slice::from_ref(&rule), "person", &hits);
        assert!(f[0].triggered && f[0].prob == Some(0.52) && f[0].detail.as_deref().unwrap().starts_with("knife seen next to the person"));
        let weak = ObjectHits::from([("weapon_object".to_string(), (0.2, "knife".to_string()))]);
        assert!(!object_flags(Lang::En, std::slice::from_ref(&rule), "person", &weak)[0].triggered);
        let none = object_flags(Lang::En, std::slice::from_ref(&rule), "person", &ObjectHits::new());
        assert!(!none[0].triggered && none[0].prob.is_none(), "checked, nothing seen");
        assert!(object_flags(Lang::En, std::slice::from_ref(&rule), "car", &hits).is_empty());
    }
}
