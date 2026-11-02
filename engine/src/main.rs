//! eyeai-engine: the EyeAI worker. Decode -> YOLOX -> track -> events/incidents/snapshots in Postgres, server-side
//! classification, annotated live MJPEG. See docs/ARCHITECTURE.md.
mod api;
mod behavior;
mod burst;
mod classifier;
mod coco;
mod config;
mod decision;
mod decode;
mod detector;
mod manager;
mod model;
mod render;
mod repo;
mod settings;
mod source;
mod speed;
mod tracker;

#[cfg(test)]
mod testutil;

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use anyhow::{Context, Result};
use tracing::{error, info};
use tracing_subscriber::EnvFilter;

use crate::behavior::RuleCache;
use crate::classifier::ClassifierService;
use crate::manager::WorkerManager;
use crate::repo::{PgRepository, Repository};
use crate::settings::Settings;

/// `eyeai-engine --healthcheck`: GET /health on the local port, exit 0 when it answers (used by the container HEALTHCHECK).
fn healthcheck() -> ! {
    let port = std::env::var("PORT").unwrap_or_else(|_| "8090".into());
    let ok = ureq::get(&format!("http://127.0.0.1:{port}/health")).timeout(Duration::from_secs(2)).call().is_ok();
    std::process::exit(if ok { 0 } else { 1 })
}

fn main() -> Result<()> {
    if std::env::args().nth(1).as_deref() == Some("--healthcheck") {
        healthcheck();
    }
    tracing_subscriber::fmt().with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"))).init();
    let settings = Settings::from_env()?;
    decode::init()?;

    let stop = Arc::new(AtomicBool::new(false));
    {
        let stop = stop.clone();
        ctrlc::set_handler(move || stop.store(true, Ordering::SeqCst)).context("installing signal handler")?;
    }

    let repo = Arc::new(PgRepository::new(&settings.database_url, 8));
    // the classifier gets its own small pool so detection and classification never contend for connections
    let cls_repo = Arc::new(PgRepository::new(&settings.database_url, 2));
    let rules = Arc::new(RuleCache::new(repo.clone(), Duration::from_secs(30)));
    let manager = WorkerManager::new(repo.clone(), settings.clone(), rules.clone());

    let cls_rules = rules.clone();
    let classifier = ClassifierService::new(cls_repo.clone(), settings.classifier.clone(), Box::new(move || cls_rules.get()))?;
    let status = classifier.status_fn();

    let mgr_thread = {
        let (repo, mgr, stop) = (repo.clone(), manager.clone(), stop.clone());
        std::thread::Builder::new().name("manager".into()).spawn(move || {
            repo.wait_ready(&stop);
            mgr.run_forever();
        })?
    };
    let cls_thread = {
        let (stop, repo) = (stop.clone(), cls_repo.clone());
        std::thread::Builder::new().name("classifier".into()).spawn(move || {
            repo.wait_ready(&stop);
            classifier.run_forever(stop);
        })?
    };

    info!("worker {} up, model {}, classifier {}", settings.worker_id, settings.model, settings.classifier.label().unwrap_or_else(|| "off".into()));
    let served = api::serve(settings.port, manager.clone(), status, stop.clone());
    if let Err(e) = &served {
        error!("api stopped: {e:#}");
    }
    stop.store(true, Ordering::SeqCst);
    manager.shutdown();
    let _ = mgr_thread.join();
    let _ = cls_thread.join();
    let _ = repo.list_sources(); // keep the trait import used in release builds
    served
}
