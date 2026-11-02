//! Process-wide settings from the environment (same variables as the Python worker).
use std::env;

use anyhow::{Result, bail};

use crate::classifier::ClassifierConfig;

#[derive(Debug, Clone)]
pub struct Settings {
    pub database_url: String,
    pub worker_id: String,
    pub model: String,
    pub port: u16,
    pub target_fps: f32,
    pub poll_s: f32,
    pub stall_s: f32,
    pub ort_threads: Option<usize>,
    pub classifier: ClassifierConfig,
}

fn var(name: &str) -> Option<String> {
    env::var(name).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

fn parse<T: std::str::FromStr>(name: &str, default: T) -> Result<T> {
    match var(name) {
        None => Ok(default),
        Some(v) => v.parse().map_err(|_| anyhow::anyhow!("{name}={v:?} is not valid")),
    }
}

impl Settings {
    pub fn from_env() -> Result<Self> {
        let Some(database_url) = var("DATABASE_URL") else {
            bail!("DATABASE_URL is required");
        };
        let host = var("HOSTNAME").unwrap_or_else(|| "local".into());
        Ok(Self {
            database_url,
            worker_id: var("WORKER_ID").unwrap_or_else(|| format!("worker-{host}")),
            model: var("EYEAI_MODEL").unwrap_or_else(|| "/app/data/models/yolox_s.onnx".into()),
            port: parse("PORT", 8090)?,
            target_fps: parse("EYEAI_TARGET_FPS", 5.0)?,
            poll_s: parse("EYEAI_POLL_S", 5.0)?,
            stall_s: parse("EYEAI_STALL_S", 10.0)?,
            ort_threads: match var("EYEAI_ORT_THREADS") {
                Some(v) => Some(v.parse()?),
                None => None,
            },
            classifier: ClassifierConfig::from_env(),
        })
    }
}
