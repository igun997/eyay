//! Language of the text the worker writes into the database (event titles, timeline entries, activity-pattern sentences, warning-sign
//! details). Default Indonesian; the dashboard's "automatic text language" (`engine_settings 'defaults'` -> `language`) switches it live.
//! Technical log lines and error messages stay English. Names the admin edits (categories, rules, cameras) are not translated here.
use std::sync::atomic::{AtomicU8, Ordering};

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    Id,
    En,
}

static CURRENT: AtomicU8 = AtomicU8::new(0);

pub fn lang() -> Lang {
    if CURRENT.load(Ordering::Relaxed) == 1 { Lang::En } else { Lang::Id }
}

/// Read `language` from the global defaults; anything but "en" means Indonesian.
pub fn set_from_defaults(defaults: &Value) {
    CURRENT.store(u8::from(defaults["language"].as_str() == Some("en")), Ordering::Relaxed);
}

fn pick(l: Lang, id: String, en: String) -> String {
    if l == Lang::Id { id } else { en }
}

pub fn label_name(l: Lang, label: &str) -> String {
    if l == Lang::En {
        return label.to_string();
    }
    match label {
        "person" => "orang", "car" => "mobil", "truck" => "truk", "bus" => "bus", "motorcycle" => "sepeda motor", "bicycle" => "sepeda",
        "knife" => "pisau", "scissors" => "gunting", "baseball bat" => "tongkat bisbol", "bottle" => "botol", "backpack" => "ransel",
        "handbag" => "tas tangan", "suitcase" => "koper", "cell phone" => "ponsel", "umbrella" => "payung",
        other => other,
    }
    .to_string()
}

pub fn detected_at(l: Lang, category: &str, camera: &str) -> String {
    pick(l, format!("{category} terdeteksi di {camera}"), format!("{category} detected at {camera}"))
}

pub fn suspicious_incident(l: Lang, title: &str, camera: &str) -> String {
    pick(l, format!("Mencurigakan: {title} di {camera}"), format!("Suspicious: {title} at {camera}"))
}

pub fn warning_sign_timeline(l: Lang, title: &str) -> String {
    pick(l, format!("Tanda peringatan: {title}"), format!("Behaviour indicator: {title}"))
}

pub fn pattern_timeline(l: Lang, sentence: &str) -> String {
    pick(l, format!("Pola aktivitas: {sentence}"), format!("Indication: {sentence}"))
}

pub fn photos_detail(l: Lang, n: usize, secs: f64) -> String {
    pick(l, format!("{n} foto dalam {secs:.1} dtk"), format!("{n} snapshot{} over {secs:.1} s", if n != 1 { "s" } else { "" }))
}

pub fn moving(l: Lang, title: &str, secs: f64) -> String {
    pick(l, format!("{title} bergerak melintasi layar selama {secs:.1} dtk"), format!("{title} moving across the frame for {secs:.1} s"))
}

pub fn lingering(l: Lang, title: &str, secs: f64) -> String {
    pick(l, format!("{title} berdiam di area pandang selama {secs:.1} dtk"), format!("{title} lingering in view for {secs:.1} s"))
}

pub fn crowd(l: Lang, n: usize, label: &str) -> String {
    let plural = match label {
        "person" => "people".to_string(),
        "bus" => "buses".to_string(),
        other => format!("{other}s"),
    };
    pick(l, format!("{n} {} terlihat", label_name(l, label)), format!("{n} {plural} in view"))
}

pub fn appeared(l: Lang, title: &str, n: usize) -> String {
    pick(l, format!("{title} muncul (terlihat di {n} foto)"), format!("{title} appeared (seen in {n} snapshot{})", if n != 1 { "s" } else { "" }))
}

pub fn speeding_title(l: Lang) -> String {
    pick(l, "Melebihi batas kecepatan".into(), "Over speed limit".into())
}

pub fn speeding_detail(l: Lang, kmh: f32, limit: f32) -> String {
    pick(
        l,
        format!("Perkiraan {kmh:.0} km/jam, batas {limit:.0} km/jam - perkiraan dari pengaturan kecepatan, perlu ditinjau manusia"),
        format!("{kmh:.0} km/h estimated, limit {limit:.0} km/h - estimate from the calibrated geometry, needs human review"),
    )
}

pub fn object_detail(l: Lang, object: &str, certainty: f32) -> String {
    pick(
        l,
        format!("{} terlihat di dekat orang (tingkat keyakinan {certainty:.2}) - dibaca dari gambar kamera, benda kecil atau tersembunyi sering terlewat, perlu ditinjau manusia", label_name(l, object)),
        format!("{object} seen next to the person (certainty {certainty:.2}) - read from the camera image, small or hidden objects are often missed, needs human review"),
    )
}

pub fn pattern_rule_detail(l: Lang, sentence: &str, min_secs: f64) -> String {
    pick(
        l,
        format!("{sentence} (pola ini butuh {min_secs:.1} dtk atau lebih) - teramati di foto lanjutan, perlu ditinjau manusia"),
        format!("{sentence} (indicator needs {min_secs:.1} s or more) - observed in snapshots, needs human review"),
    )
}

pub fn question_detail(l: Lang, p: f32, threshold: f32) -> String {
    pick(
        l,
        format!("Peluang ya {p:.2} >= {threshold:.2} - perkiraan model, perlu ditinjau manusia"),
        format!("P(yes) {p:.2} >= {threshold:.2} - model estimate, needs human review"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn indonesian_is_the_default_and_only_en_switches_it() {
        set_from_defaults(&json!({}));
        assert_eq!(lang(), Lang::Id);
        set_from_defaults(&json!({"language": "fr"}));
        assert_eq!(lang(), Lang::Id);
        set_from_defaults(&json!({"language": "en"}));
        assert_eq!(lang(), Lang::En);
        set_from_defaults(&json!({"language": "id"}));
        assert_eq!(lang(), Lang::Id);
    }

    #[test]
    fn both_languages_say_the_same_thing() {
        assert_eq!(detected_at(Lang::Id, "Orang", "Gudang"), "Orang terdeteksi di Gudang");
        assert_eq!(detected_at(Lang::En, "Person", "Dock"), "Person detected at Dock");
        assert_eq!(crowd(Lang::Id, 3, "person"), "3 orang terlihat");
        assert_eq!(crowd(Lang::En, 3, "person"), "3 people in view");
        assert!(speeding_detail(Lang::Id, 72.0, 50.0).starts_with("Perkiraan 72 km/jam"));
        assert_eq!(photos_detail(Lang::En, 1, 0.0), "1 snapshot over 0.0 s");
    }
}
