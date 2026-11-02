//! Polls `sources` and keeps exactly one SourceRunner per enabled source.
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::Duration;

use anyhow::Result;
use serde_json::{Value, json};
use tracing::{error, info};

use crate::behavior::RuleCache;
use crate::config::EngineConfig;
use crate::settings::Settings;
use crate::source::{Categories, RunnerCtx, SourceRunner};

pub struct WorkerManager {
    repo: Arc<dyn crate::repo::Repository>,
    settings: Settings,
    ctx: Arc<RunnerCtx>,
    categories: Categories,
    runners: Mutex<HashMap<String, Arc<SourceRunner>>>,
    pub stop: Arc<AtomicBool>,
}

impl WorkerManager {
    pub fn new(repo: Arc<dyn crate::repo::Repository>, settings: Settings, rules: Arc<RuleCache>) -> Arc<Self> {
        let categories: Categories = Arc::new(RwLock::new(HashMap::new()));
        let check_on = settings.classifier.enabled();
        let rules_for_check = rules.clone();
        let needs_check: crate::decision::NeedsCheck = Arc::new(move |label: &str| {
            check_on && rules_for_check.get().iter().any(|r| r.kind == "classifier" && r.applies_to.iter().any(|l| l == label))
        });
        let ctx = Arc::new(RunnerCtx {
            repo: repo.clone(), categories: categories.clone(), rules, needs_check, model: settings.model.clone().into(),
            ort_threads: settings.ort_threads, target_fps: settings.target_fps, stall: Duration::from_secs_f32(settings.stall_s),
        });
        Arc::new(Self { repo, settings, ctx, categories, runners: Mutex::new(HashMap::new()), stop: Arc::new(AtomicBool::new(false)) })
    }

    pub fn runner(&self, source_id: &str) -> Option<Arc<SourceRunner>> {
        self.runners.lock().unwrap().get(source_id).filter(|r| r.alive()).cloned()
    }

    pub fn running(&self) -> Vec<Value> {
        self.runners.lock().unwrap().iter().map(|(id, r)| json!({"id": id, "code": r.code, "status": r.status()})).collect()
    }

    pub fn worker_id(&self) -> &str {
        &self.settings.worker_id
    }

    pub fn model(&self) -> &str {
        &self.settings.model
    }

    pub fn tick(&self) -> Result<()> {
        let defaults = self.repo.engine_defaults().unwrap_or_else(|e| {
            error!("loading engine defaults failed: {e:#}");
            serde_json::Value::Null
        });
        crate::i18n::set_from_defaults(&defaults);
        let sources: HashMap<String, _> = self
            .repo
            .list_sources()?
            .into_iter()
            .map(|mut s| {
                s.config = EngineConfig::resolve(&defaults, &s.config_raw);
                // process-wide settings (env) fill what neither the dashboard defaults nor the camera set
                s.config.detector.target_fps.get_or_insert(self.settings.target_fps);
                if s.config.detector.intra_threads.is_none() {
                    s.config.detector.intra_threads = self.settings.ort_threads;
                }
                (s.id.clone(), s)
            })
            .collect();
        *self.categories.write().unwrap() = self.repo.list_categories()?;
        let current: Vec<(String, Arc<SourceRunner>)> = self.runners.lock().unwrap().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        for (sid, runner) in current {
            let src = sources.get(&sid);
            let gone = src.is_none_or(|s| !s.enabled || s.restart_key() != runner.restart_key || !runner.alive());
            if gone {
                runner.stop();
                self.runners.lock().unwrap().remove(&sid);
                if let Some(s) = src.filter(|s| !s.enabled) {
                    let _ = self.repo.update_source_state(&s.id, "pending", None, None, None, None);
                }
                info!("{}: runner removed", runner.code);
            } else if let Some(s) = src {
                if *s != runner.source() {
                    runner.set_source(s.clone());
                }
            }
        }
        for (sid, src) in &sources {
            if src.enabled && !self.runners.lock().unwrap().contains_key(sid) {
                let r = SourceRunner::start(src.clone(), self.ctx.clone());
                self.runners.lock().unwrap().insert(sid.clone(), r);
            }
        }
        let n = self.runners.lock().unwrap().len() as i32;
        self.repo.heartbeat(&self.settings.worker_id, n, &self.settings.model, self.settings.classifier.label().as_deref())?;
        Ok(())
    }

    pub fn run_forever(&self) {
        while !self.stop.load(Ordering::Relaxed) {
            if let Err(e) = self.tick() {
                error!("poll failed: {e:#}"); // keep polling through DB outages
            }
            let mut left = self.settings.poll_s;
            while left > 0.0 && !self.stop.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_millis(100));
                left -= 0.1;
            }
        }
    }

    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        let all: Vec<Arc<SourceRunner>> = self.runners.lock().unwrap().drain().map(|(_, r)| r).collect();
        for r in &all {
            r.request_stop();
        }
        for r in all {
            r.stop();
        }
    }
}
