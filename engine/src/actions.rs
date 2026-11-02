//! Where alerts go: a web service (webhook), an MQTT broker, or a PLC over Modbus TCP. Everything here is plain std networking
//! plus `ureq`, so the worker has no broker or PLC library to trust. A simulated action renders exactly what would be sent and touches
//! nothing. See docs/AUTOMATIONS.md for the safety limits (coil writes always pulse, no latching).
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

/// Values a template can use, plus the default JSON body.
pub struct Ctx {
    pub vars: HashMap<String, String>,
    pub default_body: String,
}

#[derive(Debug, Clone)]
pub struct ActionResult {
    pub ok: bool,
    pub message: String,
    /// the rendered body, topic or frame description (for both simulation and live, so the log shows what went out)
    pub would_send: String,
    pub ms: u64,
}

impl ActionResult {
    pub fn to_json(&self, kind: &str, target: &str) -> Value {
        json!({"type": kind, "target": target, "ok": self.ok, "message": self.message, "would_send": self.would_send, "ms": self.ms})
    }

    fn fail(msg: impl Into<String>, would: impl Into<String>, t0: Instant) -> Self {
        Self { ok: false, message: msg.into(), would_send: would.into(), ms: t0.elapsed().as_millis() as u64 }
    }

    fn done(msg: impl Into<String>, would: impl Into<String>, t0: Instant) -> Self {
        Self { ok: true, message: msg.into(), would_send: would.into(), ms: t0.elapsed().as_millis() as u64 }
    }
}

/// Replace `{{name}}` with the value of `name`; unknown names become an empty string. No code, no nesting.
pub fn render(template: &str, vars: &HashMap<String, String>) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(start) = rest.find("{{") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        match after.find("}}") {
            Some(end) => {
                out.push_str(vars.get(after[..end].trim()).map_or("", String::as_str));
                rest = &after[end + 2..];
            }
            None => {
                out.push_str(&rest[start..]);
                return out;
            }
        }
    }
    out.push_str(rest);
    out
}

fn timeout_of(conn: &Value) -> Duration {
    Duration::from_secs_f64(conn["timeout_s"].as_f64().unwrap_or(5.0).clamp(1.0, 15.0))
}

fn connect(host: &str, port: u16, timeout: Duration) -> std::io::Result<TcpStream> {
    let addr = (host, port).to_socket_addrs()?.next().ok_or_else(|| std::io::Error::other("host did not resolve"))?;
    let s = TcpStream::connect_timeout(&addr, timeout)?;
    s.set_read_timeout(Some(timeout))?;
    s.set_write_timeout(Some(timeout))?;
    s.set_nodelay(true)?;
    Ok(s)
}

fn host_port(conn: &Value, default_port: u16) -> Result<(String, u16), String> {
    let host = conn["host"].as_str().filter(|h| !h.trim().is_empty()).ok_or("the connection has no host")?;
    let port = conn["port"].as_u64().map_or(default_port, |p| p as u16);
    Ok((host.trim().to_string(), port))
}

// ---- web service ----------------------------------------------------------------------------------------------------
pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// HMAC-SHA256 (RFC 2104) with the sha2 hash; the signature of a webhook body.
pub fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    const BLOCK: usize = 64;
    let mut k = if key.len() > BLOCK { Sha256::digest(key).to_vec() } else { key.to_vec() };
    k.resize(BLOCK, 0);
    let inner: Vec<u8> = k.iter().map(|b| b ^ 0x36).chain(data.iter().copied()).collect();
    let outer: Vec<u8> = k.iter().map(|b| b ^ 0x5c).chain(Sha256::digest(&inner).iter().copied()).collect();
    Sha256::digest(&outer).to_vec()
}

pub fn webhook(conn: &Value, body_template: Option<&str>, ctx: &Ctx, simulate: bool) -> ActionResult {
    let t0 = Instant::now();
    let url = conn["url"].as_str().unwrap_or("").trim().to_string();
    let method = conn["method"].as_str().map_or("POST", |m| if m.eq_ignore_ascii_case("PUT") { "PUT" } else { "POST" });
    let body = match body_template.filter(|t| !t.trim().is_empty()) {
        Some(t) => render(t, &ctx.vars),
        None => ctx.default_body.clone(),
    };
    let would = format!("{method} {url}\n{body}");
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return ActionResult::fail("the web service address must start with http:// or https://", would, t0);
    }
    if simulate {
        return ActionResult::done("simulated: nothing was sent", would, t0);
    }
    let agent = ureq::AgentBuilder::new().timeout(timeout_of(conn)).user_agent(crate::classifier::USER_AGENT).build();
    let mut req = agent.request(method, &url).set("Content-Type", "application/json");
    if let Some(h) = conn["headers"].as_object() {
        for (k, v) in h.iter().take(20) {
            if let Some(v) = v.as_str() {
                req = req.set(k, v);
            }
        }
    }
    if let Some(secret) = conn["secret"].as_str().filter(|s| !s.is_empty()) {
        req = req.set("X-EyeAI-Signature", &format!("sha256={}", hex(&hmac_sha256(secret.as_bytes(), body.as_bytes()))));
    }
    match req.send_string(&body) {
        Ok(r) => ActionResult::done(format!("HTTP {}", r.status()), would, t0),
        Err(ureq::Error::Status(code, _)) => ActionResult::fail(format!("the web service answered HTTP {code}"), would, t0),
        Err(ureq::Error::Transport(t)) => ActionResult::fail(format!("could not reach the web service: {t}"), would, t0),
    }
}

// ---- MQTT 3.1.1, QoS 0 ----------------------------------------------------------------------------------------------
fn mqtt_len(mut n: usize, out: &mut Vec<u8>) {
    loop {
        let mut b = (n % 128) as u8;
        n /= 128;
        if n > 0 {
            b |= 0x80;
        }
        out.push(b);
        if n == 0 {
            break;
        }
    }
}

fn mqtt_str(s: &str, out: &mut Vec<u8>) {
    out.extend_from_slice(&(s.len() as u16).to_be_bytes());
    out.extend_from_slice(s.as_bytes());
}

pub fn mqtt_connect_packet(client_id: &str, username: Option<&str>, password: Option<&str>) -> Vec<u8> {
    let mut body = Vec::new();
    mqtt_str("MQTT", &mut body);
    body.push(4); // protocol level 3.1.1
    let mut flags = 0x02u8; // clean session
    if username.is_some() {
        flags |= 0x80;
        if password.is_some() {
            flags |= 0x40;
        }
    }
    body.push(flags);
    body.extend_from_slice(&30u16.to_be_bytes()); // keep alive
    mqtt_str(client_id, &mut body);
    if let Some(u) = username {
        mqtt_str(u, &mut body);
        if let Some(p) = password {
            mqtt_str(p, &mut body);
        }
    }
    let mut out = vec![0x10];
    mqtt_len(body.len(), &mut out);
    out.extend(body);
    out
}

pub fn mqtt_publish_packet(topic: &str, payload: &[u8], retain: bool) -> Vec<u8> {
    let mut body = Vec::new();
    mqtt_str(topic, &mut body);
    body.extend_from_slice(payload);
    let mut out = vec![0x30 | u8::from(retain)];
    mqtt_len(body.len(), &mut out);
    out.extend(body);
    out
}

fn mqtt_open(conn: &Value) -> Result<TcpStream, String> {
    let (host, port) = host_port(conn, 1883)?;
    let mut s = connect(&host, port, timeout_of(conn)).map_err(|e| format!("could not reach the message broker: {e}"))?;
    let client_id = conn["client_id"].as_str().filter(|c| !c.is_empty()).unwrap_or("eyeai");
    let user = conn["username"].as_str().filter(|u| !u.is_empty());
    let pass = conn["password"].as_str().filter(|p| !p.is_empty());
    s.write_all(&mqtt_connect_packet(client_id, user, pass)).map_err(|e| format!("sending to the broker failed: {e}"))?;
    let mut ack = [0u8; 4];
    s.read_exact(&mut ack).map_err(|e| format!("the broker did not answer: {e}"))?;
    if ack[0] != 0x20 || ack[3] != 0 {
        return Err(format!("the broker refused the connection (code {})", ack[3]));
    }
    Ok(s)
}

pub fn mqtt_publish(conn: &Value, topic: &str, payload_template: Option<&str>, retain: bool, ctx: &Ctx, simulate: bool) -> ActionResult {
    let t0 = Instant::now();
    let payload = match payload_template.filter(|t| !t.trim().is_empty()) {
        Some(t) => render(t, &ctx.vars),
        None => ctx.default_body.clone(),
    };
    let topic = render(topic, &ctx.vars);
    let would = format!("MQTT publish{} {topic}\n{payload}", if retain { " (retained)" } else { "" });
    if topic.trim().is_empty() || topic.contains(['#', '+']) {
        return ActionResult::fail("the topic is empty or contains a wildcard", would, t0);
    }
    if simulate {
        return ActionResult::done("simulated: nothing was sent", would, t0);
    }
    match mqtt_open(conn).and_then(|mut s| {
        s.write_all(&mqtt_publish_packet(&topic, payload.as_bytes(), retain)).map_err(|e| format!("sending to the broker failed: {e}"))?;
        let _ = s.write_all(&[0xE0, 0x00]); // DISCONNECT
        Ok(())
    }) {
        Ok(()) => ActionResult::done("published", would, t0),
        Err(e) => ActionResult::fail(e, would, t0),
    }
}

pub fn mqtt_check(conn: &Value) -> ActionResult {
    let t0 = Instant::now();
    match mqtt_open(conn) {
        Ok(mut s) => {
            let _ = s.write_all(&[0xE0, 0x00]);
            ActionResult::done("the broker accepted the connection", "MQTT connect only, nothing published", t0)
        }
        Err(e) => ActionResult::fail(e, "MQTT connect only, nothing published", t0),
    }
}

// ---- Modbus TCP -----------------------------------------------------------------------------------------------------
/// MBAP header + PDU of a "write single coil" (5) or "write single register" (6) request.
pub fn modbus_frame(transaction: u16, unit: u8, function: u8, address: u16, value: u16) -> Vec<u8> {
    let mut f = Vec::with_capacity(12);
    f.extend_from_slice(&transaction.to_be_bytes());
    f.extend_from_slice(&0u16.to_be_bytes()); // protocol id
    f.extend_from_slice(&6u16.to_be_bytes()); // bytes that follow: unit + function + address + value
    f.push(unit);
    f.push(function);
    f.extend_from_slice(&address.to_be_bytes());
    f.extend_from_slice(&value.to_be_bytes());
    f
}

fn modbus_exchange(conn: &Value, function: u8, address: u16, value: u16) -> Result<(), String> {
    let (host, port) = host_port(conn, 502)?;
    let unit = conn["unit_id"].as_u64().unwrap_or(1) as u8;
    let mut s = connect(&host, port, timeout_of(conn)).map_err(|e| format!("could not reach the PLC: {e}"))?;
    let frame = modbus_frame(1, unit, function, address, value);
    s.write_all(&frame).map_err(|e| format!("sending to the PLC failed: {e}"))?;
    let mut resp = [0u8; 12];
    s.read_exact(&mut resp).map_err(|e| format!("the PLC did not answer: {e}"))?;
    if resp[7] == function | 0x80 {
        return Err(format!("the PLC refused the write (Modbus exception {})", resp[8]));
    }
    if resp[7] != function || resp[8..12] != frame[8..12] {
        return Err("the PLC answered with something unexpected".into());
    }
    Ok(())
}

/// Write a single coil or register. `value` is 0/1 for a coil (on = 0xFF00 on the wire).
pub fn modbus_write(conn: &Value, function: &str, address: u16, value: u16, note: &str, simulate: bool) -> ActionResult {
    let t0 = Instant::now();
    let (func, wire, what) = match function {
        "write_coil" => (5u8, if value != 0 { 0xFF00 } else { 0 }, format!("coil {address} {}", if value != 0 { "ON" } else { "OFF" })),
        _ => (6u8, value, format!("register {address} = {value}")),
    };
    let unit = conn["unit_id"].as_u64().unwrap_or(1);
    let would = format!("Modbus TCP unit {unit}: write {what}{note}\nframe: {}", hex(&modbus_frame(1, unit as u8, func, address, wire)));
    if simulate {
        return ActionResult::done("simulated: nothing was sent", would, t0);
    }
    match modbus_exchange(conn, func, address, wire) {
        Ok(()) => ActionResult::done("the PLC confirmed the write", would, t0),
        Err(e) => ActionResult::fail(e, would, t0),
    }
}

pub fn modbus_check(conn: &Value) -> ActionResult {
    let t0 = Instant::now();
    match host_port(conn, 502).and_then(|(h, p)| connect(&h, p, timeout_of(conn)).map(|_| ()).map_err(|e| format!("could not reach the PLC: {e}"))) {
        Ok(()) => ActionResult::done("the PLC accepted the connection (nothing written)", "TCP connect only, nothing written", t0),
        Err(e) => ActionResult::fail(e, "TCP connect only, nothing written", t0),
    }
}

pub fn webhook_check(conn: &Value) -> ActionResult {
    let ctx = Ctx { vars: HashMap::new(), default_body: json!({"eyeai": 1, "test": true, "message": "connection check from EyeAI"}).to_string() };
    webhook(conn, None, &ctx, false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::{Arc, Mutex};

    fn vars(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    #[test]
    fn templates_replace_known_names_and_blank_the_rest() {
        let v = vars(&[("event.label", "person"), ("camera.name", "Dock 2")]);
        assert_eq!(render("{{ event.label }} at {{camera.name}} {{nope}}!", &v), "person at Dock 2 !");
        assert_eq!(render("no braces", &v), "no braces");
        assert_eq!(render("open {{ never closed", &v), "open {{ never closed");
    }

    #[test]
    fn hmac_sha256_matches_the_rfc_4231_vector() {
        assert_eq!(hex(&hmac_sha256(b"Jefe", b"what do ya want for nothing?")), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
        let long_key = vec![0xaa; 131]; // longer than one block: hashed first (RFC 4231 case 6)
        assert_eq!(
            hex(&hmac_sha256(&long_key, b"Test Using Larger Than Block-Size Key - Hash Key First")),
            "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"
        );
    }

    #[test]
    fn mqtt_packets_follow_the_3_1_1_layout() {
        let c = mqtt_connect_packet("eyeai", Some("u"), Some("p"));
        assert_eq!(&c[..2], &[0x10, c.len() as u8 - 2]);
        assert_eq!(&c[2..10], &[0, 4, b'M', b'Q', b'T', b'T', 4, 0xC2]);
        let p = mqtt_publish_packet("a/b", b"hi", true);
        assert_eq!(p, vec![0x31, 7, 0, 3, b'a', b'/', b'b', b'h', b'i']);
        let mut len = Vec::new();
        mqtt_len(321, &mut len);
        assert_eq!(len, vec![0xC1, 0x02], "remaining length uses the continuation bit");
    }

    #[test]
    fn modbus_frames_carry_unit_function_address_and_value() {
        assert_eq!(modbus_frame(1, 7, 5, 0x0010, 0xFF00), vec![0, 1, 0, 0, 0, 6, 7, 5, 0, 0x10, 0xFF, 0]);
    }

    #[test]
    fn simulation_renders_what_would_be_sent_and_sends_nothing() {
        let ctx = Ctx { vars: vars(&[("event.label", "person")]), default_body: "{\"x\":1}".into() };
        // unroutable addresses: if anything were sent these would fail
        let w = webhook(&json!({"url": "http://10.255.255.1/hook"}), Some("label={{event.label}}"), &ctx, true);
        assert!(w.ok && w.would_send.contains("POST http://10.255.255.1/hook") && w.would_send.contains("label=person"));
        let m = mqtt_publish(&json!({"host": "10.255.255.1"}), "plant/{{event.label}}", None, false, &ctx, true);
        assert!(m.ok && m.would_send.contains("plant/person") && m.would_send.contains("{\"x\":1}"));
        let d = modbus_write(&json!({"host": "10.255.255.1", "unit_id": 3}), "write_coil", 4, 1, " for 5 s", true);
        assert!(d.ok && d.would_send.contains("coil 4 ON") && d.would_send.contains("for 5 s") && d.would_send.contains("frame: 00010000000603050004ff00"));
    }

    #[test]
    fn bad_targets_are_refused_before_anything_is_sent() {
        let ctx = Ctx { vars: HashMap::new(), default_body: "{}".into() };
        assert!(!webhook(&json!({"url": "ftp://x"}), None, &ctx, false).ok);
        assert!(!mqtt_publish(&json!({"host": "h"}), "a/#", None, false, &ctx, false).ok);
        assert!(!mqtt_publish(&json!({"host": "h"}), "", None, false, &ctx, false).ok);
    }

    #[test]
    fn webhook_posts_the_body_with_headers_and_a_signature() {
        let server = Arc::new(tiny_http::Server::http("127.0.0.1:0").unwrap());
        let url = format!("http://{}/hook", server.server_addr().to_ip().unwrap());
        let seen: Arc<Mutex<Vec<(String, String, Option<String>, Option<String>)>>> = Arc::default();
        let (s2, seen2) = (server.clone(), seen.clone());
        std::thread::spawn(move || {
            if let Some(mut req) = s2.incoming_requests().next() {
                let mut body = String::new();
                let _ = req.as_reader().read_to_string(&mut body);
                let h = |n: &str| req.headers().iter().find(|h| h.field.to_string().eq_ignore_ascii_case(n)).map(|h| h.value.to_string());
                seen2.lock().unwrap().push((req.method().to_string(), body, h("X-EyeAI-Signature"), h("X-Api-Key")));
                let _ = req.respond(tiny_http::Response::from_string("ok"));
            }
        });
        let ctx = Ctx { vars: vars(&[("event.label", "person")]), default_body: "{}".into() };
        let r = webhook(&json!({"url": url, "secret": "s3cret", "headers": {"X-Api-Key": "k"}}), Some("{\"l\":\"{{event.label}}\"}"), &ctx, false);
        assert!(r.ok, "{}", r.message);
        let seen = seen.lock().unwrap();
        assert_eq!(seen[0].0, "POST");
        assert_eq!(seen[0].1, "{\"l\":\"person\"}");
        assert_eq!(seen[0].2.as_deref(), Some(format!("sha256={}", hex(&hmac_sha256(b"s3cret", seen[0].1.as_bytes()))).as_str()));
        assert_eq!(seen[0].3.as_deref(), Some("k"));
        server.unblock();
    }

    #[test]
    fn mqtt_publish_talks_to_a_broker_and_reports_a_refusal() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let got: Arc<Mutex<Vec<u8>>> = Arc::default();
        let g2 = got.clone();
        let t = std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            let mut buf = [0u8; 256];
            let n = c.read(&mut buf).unwrap(); // CONNECT
            g2.lock().unwrap().extend_from_slice(&buf[..n]);
            c.write_all(&[0x20, 0x02, 0, 0]).unwrap();
            let n = c.read(&mut buf).unwrap(); // PUBLISH (+ DISCONNECT)
            g2.lock().unwrap().extend_from_slice(&buf[..n]);
        });
        let ctx = Ctx { vars: HashMap::new(), default_body: "{\"a\":1}".into() };
        let r = mqtt_publish(&json!({"host": "127.0.0.1", "port": port}), "plant/alarm", None, false, &ctx, false);
        t.join().unwrap();
        assert!(r.ok, "{}", r.message);
        let bytes = got.lock().unwrap().clone();
        assert!(bytes.windows(11).any(|w| w == b"plant/alarm"), "topic on the wire");
        assert!(bytes.windows(7).any(|w| w == b"{\"a\":1}"), "payload on the wire");

        let l2 = TcpListener::bind("127.0.0.1:0").unwrap();
        let p2 = l2.local_addr().unwrap().port();
        let t2 = std::thread::spawn(move || {
            let (mut c, _) = l2.accept().unwrap();
            let mut b = [0u8; 128];
            let _ = c.read(&mut b);
            c.write_all(&[0x20, 0x02, 0, 5]).unwrap(); // not authorised
        });
        let r2 = mqtt_check(&json!({"host": "127.0.0.1", "port": p2}));
        t2.join().unwrap();
        assert!(!r2.ok && r2.message.contains("refused"), "{}", r2.message);
    }

    #[test]
    fn modbus_write_is_confirmed_by_the_echo_and_exceptions_are_reported() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let t = std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            let mut req = [0u8; 12];
            c.read_exact(&mut req).unwrap();
            c.write_all(&req).unwrap(); // a PLC echoes a successful single write
            req
        });
        let r = modbus_write(&json!({"host": "127.0.0.1", "port": port, "unit_id": 2}), "write_coil", 9, 1, "", false);
        let req = t.join().unwrap();
        assert!(r.ok, "{}", r.message);
        assert_eq!(&req[6..], &[2, 5, 0, 9, 0xFF, 0]);

        let l2 = TcpListener::bind("127.0.0.1:0").unwrap();
        let p2 = l2.local_addr().unwrap().port();
        let t2 = std::thread::spawn(move || {
            let (mut c, _) = l2.accept().unwrap();
            let mut req = [0u8; 12];
            c.read_exact(&mut req).unwrap();
            c.write_all(&[0, 1, 0, 0, 0, 3, 1, 0x85, 2, 0, 0, 0]).unwrap(); // exception 2: illegal data address
        });
        let r2 = modbus_write(&json!({"host": "127.0.0.1", "port": p2}), "write_coil", 9, 1, "", false);
        t2.join().unwrap();
        assert!(!r2.ok && r2.message.contains("exception 2"), "{}", r2.message);
    }
}
