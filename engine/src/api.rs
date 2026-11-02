//! HTTP side of the worker: /health, /snapshot/<id>.jpg and the annotated /live/<id>.mjpg stream (what the web app proxies).
use std::io::{Cursor, Read};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow};
use serde_json::{Value, json};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};
use tracing::{debug, info};

use crate::manager::WorkerManager;
use crate::render::annotate;
use crate::source::SourceRunner;

const BOUNDARY: &str = "frame";
const MAX_LIVE_FPS: f32 = 8.0;
const MAX_WIDTH: u32 = 960;

pub type StatusFn = Arc<dyn Fn() -> Value + Send + Sync>;

fn header(k: &str, v: &str) -> Header {
    Header::from_bytes(k.as_bytes(), v.as_bytes()).expect("static header")
}

fn json_response(status: u16, body: &Value) -> Response<Cursor<Vec<u8>>> {
    Response::from_string(body.to_string()).with_status_code(status).with_header(header("Content-Type", "application/json"))
}

fn detail(status: u16, msg: &str) -> Response<Cursor<Vec<u8>>> {
    json_response(status, &json!({ "detail": msg }))
}

pub fn serve(port: u16, mgr: Arc<WorkerManager>, classifier: StatusFn, stop: Arc<AtomicBool>) -> Result<()> {
    let server = Server::http(("0.0.0.0", port)).map_err(|e| anyhow!("binding port {port}: {e}"))?;
    info!("listening on :{port}");
    while !stop.load(Ordering::Relaxed) {
        match server.recv_timeout(Duration::from_millis(300)) {
            Ok(Some(req)) => {
                let (mgr, classifier) = (mgr.clone(), classifier.clone());
                thread::spawn(move || handle(req, &mgr, &classifier));
            }
            Ok(None) => {}
            Err(e) => return Err(anyhow!("http server: {e}")),
        }
    }
    Ok(())
}

fn is_uuid(s: &str) -> bool {
    s.len() == 36 && s.chars().all(|c| c.is_ascii_hexdigit() || c == '-')
}

fn handle(req: Request, mgr: &WorkerManager, classifier: &StatusFn) {
    let path = req.url().split('?').next().unwrap_or("").to_string();
    if *req.method() != Method::Get {
        let _ = req.respond(detail(405, "method not allowed"));
        return;
    }
    let runner = |id: &str| -> Result<Arc<SourceRunner>, Response<Cursor<Vec<u8>>>> {
        if !is_uuid(id) {
            return Err(detail(404, "unknown source"));
        }
        mgr.runner(id).ok_or_else(|| detail(404, "source is not running"))
    };
    let res = if path == "/health" {
        let body = json!({"ok": true, "worker_id": mgr.worker_id(), "model": mgr.model(), "sources": mgr.running(), "classifier": classifier()});
        req.respond(json_response(200, &body))
    } else if let Some(id) = path.strip_prefix("/snapshot/").and_then(|p| p.strip_suffix(".jpg")) {
        match runner(id) {
            Err(r) => req.respond(r),
            Ok(r) => match render(&r) {
                Some((_, jpeg)) => req.respond(
                    Response::from_data(jpeg).with_header(header("Content-Type", "image/jpeg")).with_header(header("Cache-Control", "no-store")),
                ),
                None => req.respond(detail(503, "no frame yet")),
            },
        }
    } else if let Some(id) = path.strip_prefix("/live/").and_then(|p| p.strip_suffix(".mjpg")) {
        match runner(id) {
            Err(r) => req.respond(r),
            Ok(r) => {
                let deadline = Instant::now() + Duration::from_secs(6);
                while r.live().1.is_none() && Instant::now() < deadline {
                    thread::sleep(Duration::from_millis(100)); // wait for the first frame before committing to a 200
                }
                if r.live().1.is_none() {
                    req.respond(detail(503, "no frame yet"))
                } else {
                    let stream = Mjpeg { mgr_id: id.to_string(), runner: r, buf: Cursor::new(Vec::new()), last_key: None };
                    let headers = vec![
                        header("Content-Type", &format!("multipart/x-mixed-replace; boundary={BOUNDARY}")),
                        header("Cache-Control", "no-store"),
                        header("X-Accel-Buffering", "no"),
                    ];
                    req.respond(Response::new(StatusCode(200), headers, stream, None, None))
                }
            }
        }
    } else {
        req.respond(detail(404, "not found"))
    };
    if let Err(e) = res {
        debug!("response for {path} ended: {e}"); // client went away
    }
}

/// (sequence, annotated JPEG) of the latest frame, only the boxes of watched labels.
fn render(r: &SourceRunner) -> Option<(u64, Vec<u8>)> {
    let (seq, frame, dets, geometry) = r.live();
    let frame = frame?;
    let watched = r.source().labels;
    let dets: Vec<_> = dets.into_iter().filter(|d| watched.contains(&d.label)).collect();
    annotate(&frame, &dets, geometry.as_ref(), MAX_WIDTH).ok().map(|j| (seq, j))
}

/// `multipart/x-mixed-replace` body: blocks until the next distinct frame, ends when the source stops.
struct Mjpeg {
    mgr_id: String,
    runner: Arc<SourceRunner>,
    buf: Cursor<Vec<u8>>,
    last_key: Option<(u64, Vec<(String, i32)>)>,
}

impl Read for Mjpeg {
    fn read(&mut self, out: &mut [u8]) -> std::io::Result<usize> {
        loop {
            let n = self.buf.read(out)?;
            if n > 0 {
                return Ok(n);
            }
            let period = Duration::from_secs_f32(1.0 / MAX_LIVE_FPS);
            let t0 = Instant::now();
            if !self.runner.alive() {
                return Ok(0);
            }
            let (seq, _, dets, _) = self.runner.live();
            let key = (seq, dets.iter().map(|d| (d.label.clone(), (d.score * 100.0).round() as i32 + d.speed_kmh.map_or(0, |k| k.round() as i32) * 1000)).collect::<Vec<_>>());
            if self.last_key.as_ref() != Some(&key) {
                if let Some((_, jpeg)) = render(&self.runner) {
                    self.last_key = Some(key);
                    let mut chunk = format!("--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n", jpeg.len()).into_bytes();
                    chunk.extend_from_slice(&jpeg);
                    chunk.extend_from_slice(b"\r\n");
                    self.buf = Cursor::new(chunk);
                    continue;
                }
            }
            thread::sleep(period.saturating_sub(t0.elapsed()));
            let _ = &self.mgr_id;
        }
    }
}
