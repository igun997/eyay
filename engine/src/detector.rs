//! YOLOX ONNX detector (Apache-2.0 weights, same model as the Python worker).
//!
//! The exported graph returns `[1, N, 85]`: `[cx_offset, cy_offset, log_w, log_h, obj, cls x 80]`. Objectness and class
//! scores are already sigmoided, box offsets are not, so grid decoding (offset + cell, stride, exp for size) and NMS happen
//! here, mirroring the Megvii reference. Preprocessing is the YOLOX letterbox: top-left on a 114-grey canvas, BGR, 0..255.
use std::path::Path;

use anyhow::{Context, Result, anyhow, bail};
use ndarray::Array4;
use ort::session::Session;
use ort::session::builder::GraphOptimizationLevel;
use ort::value::TensorRef;

use crate::coco::COCO_CLASSES;
use crate::decode::Frame;

const PAD: f32 = 114.0;
const STRIDES: [usize; 3] = [8, 16, 32];
const OUTPUTS: usize = 5 + COCO_CLASSES.len();

#[derive(Debug, Clone)]
pub struct Detection {
    pub label: &'static str,
    pub class_id: usize,
    pub score: f32,
    /// x1,y1,x2,y2 normalised 0..1 of the source frame
    pub bbox: [f32; 4],
}

/// Anchor grid: (cell x, cell y, stride) for every output row, in YOLOX order.
pub fn make_grids(size: usize) -> Vec<[f32; 3]> {
    let mut out = Vec::new();
    for s in STRIDES {
        let n = size / s;
        for y in 0..n {
            for x in 0..n {
                out.push([x as f32, y as f32, s as f32]);
            }
        }
    }
    out
}

/// Fill `dst` (1x3xSxS, channel 0 = B) with the letterboxed frame. Returns the scale applied to the frame.
pub fn letterbox_into(frame: &Frame, size: usize, dst: &mut Array4<f32>) -> f32 {
    let r = (size as f32 / frame.height as f32).min(size as f32 / frame.width as f32);
    let (nw, nh) = (((frame.width as f32 * r) as usize).max(1), ((frame.height as f32 * r) as usize).max(1));
    dst.fill(PAD);
    let (sx, sy) = (frame.width as f32 / nw as f32, frame.height as f32 / nh as f32);
    let (fw, fh) = (frame.width, frame.height);
    for y in 0..nh.min(size) {
        let fy = ((y as f32 + 0.5) * sy - 0.5).max(0.0);
        let (y0, wy) = (fy as usize, fy.fract());
        let y1 = (y0 + 1).min(fh - 1);
        for x in 0..nw.min(size) {
            let fx = ((x as f32 + 0.5) * sx - 0.5).max(0.0);
            let (x0, wx) = (fx as usize, fx.fract());
            let x1 = (x0 + 1).min(fw - 1);
            for c in 0..3 {
                let at = |xx: usize, yy: usize| f32::from(frame.rgb[(yy * fw + xx) * 3 + c]);
                let top = at(x0, y0) * (1.0 - wx) + at(x1, y0) * wx;
                let bot = at(x0, y1) * (1.0 - wx) + at(x1, y1) * wx;
                // BGR: RGB channel c lands in tensor channel 2 - c
                dst[[0, 2 - c, y, x]] = top * (1.0 - wy) + bot * wy;
            }
        }
    }
    r
}

#[derive(Debug, Clone, Copy)]
struct Cand {
    class_id: usize,
    score: f32,
    b: [f32; 4],
}

fn iou(a: &[f32; 4], b: &[f32; 4]) -> f32 {
    crate::tracker::iou(a, b)
}

/// Greedy per-class NMS, highest score first.
fn nms(mut c: Vec<Cand>, thr: f32) -> Vec<Cand> {
    c.sort_by(|a, b| b.score.total_cmp(&a.score));
    let mut keep: Vec<Cand> = Vec::new();
    for cand in c {
        if !keep.iter().any(|k| k.class_id == cand.class_id && iou(&k.b, &cand.b) > thr) {
            keep.push(cand);
        }
    }
    keep
}

/// Decode raw `[N, 85]` rows into detections in letterbox pixels, then NMS.
fn postprocess(raw: &[f32], grids: &[[f32; 3]], conf: f32, nms_thr: f32, classes: &[usize]) -> Vec<Cand> {
    let mut cands = Vec::new();
    for (i, g) in grids.iter().enumerate() {
        let row = &raw[i * OUTPUTS..(i + 1) * OUTPUTS];
        let obj = row[4];
        if obj <= conf {
            continue; // class scores are <= 1, so obj*cls can never pass
        }
        let (cx, cy) = ((row[0] + g[0]) * g[2], (row[1] + g[1]) * g[2]);
        let (hw, hh) = (row[2].exp() * g[2] * 0.5, row[3].exp() * g[2] * 0.5);
        for &c in classes {
            let score = obj * row[5 + c];
            if score > conf {
                cands.push(Cand { class_id: c, score, b: [cx - hw, cy - hh, cx + hw, cy + hh] });
            }
        }
    }
    nms(cands, nms_thr)
}

/// ONNX Runtime allows exactly one environment per process; every source's session shares this one.
fn environment() -> Result<&'static ort::environment::Environment> {
    static ENV: std::sync::OnceLock<ort::environment::Environment> = std::sync::OnceLock::new();
    static INIT: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = INIT.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(e) = ENV.get() {
        return Ok(e);
    }
    let env = ort::init().with_name("eyeai").build()?;
    env.set_log_level(ort::logging::LogLevel::Warning);
    Ok(ENV.get_or_init(|| env))
}

pub struct Yolox {
    session: Session,
    input_name: String,
    size: usize,
    grids: Vec<[f32; 3]>,
    classes: Vec<usize>,
    conf: f32,
    nms: f32,
    input: Array4<f32>,
}

impl Yolox {
    /// `classes` are COCO names; the model is told to look at those only.
    pub fn load(model: &Path, conf: f32, nms: f32, classes: &[String], threads: Option<usize>) -> Result<Self> {
        if !model.is_file() {
            bail!("YOLOX model not found: {}", model.display());
        }
        let mut ids = Vec::new();
        for name in classes {
            ids.push(COCO_CLASSES.iter().position(|c| c == name).ok_or_else(|| anyhow!("class {name:?} is not in COCO-80"))?);
        }
        if ids.is_empty() {
            bail!("source watches no labels");
        }
        ids.sort_unstable();
        ids.dedup();

        let mut builder = Session::builder(environment()?)?;
        if let Some(t) = threads.filter(|t| *t > 0) {
            builder = builder.with_intra_threads(t).map_err(|e| anyhow!("setting intra threads: {e}"))?;
        }
        let session = builder
            .with_optimization_level(GraphOptimizationLevel::Level3)
            .map_err(|e| anyhow!("setting the graph optimization level: {e}"))?
            .commit_from_file(model)
            .with_context(|| format!("loading the ONNX model {}", model.display()))?;
        let input = session.inputs().first().ok_or_else(|| anyhow!("model declares no inputs"))?;
        let input_name = input.name().to_string();
        let ort::value::ValueType::Tensor { shape, .. } = input.dtype() else { bail!("model input is not a tensor") };
        let dims: Vec<i64> = shape.iter().copied().collect();
        let size = match dims.as_slice() {
            [_, 3, h, w] if h == w && *h > 0 && (*h as usize) % 32 == 0 => *h as usize,
            _ => bail!("unsupported YOLOX input shape {dims:?}; need 1x3xSxS with S a multiple of 32"),
        };
        let grids = make_grids(size);
        Ok(Self {
            session, input_name, size, grids, classes: ids, conf, nms,
            input: Array4::zeros((1, 3, size, size)),
        })
    }

    /// Apply confidence, NMS and the watched labels without rebuilding the session.
    pub fn reconfigure(&mut self, conf: f32, nms: f32, classes: &[String]) -> Result<()> {
        let mut ids = Vec::new();
        for name in classes {
            ids.push(COCO_CLASSES.iter().position(|c| c == name).ok_or_else(|| anyhow!("class {name:?} is not in COCO-80"))?);
        }
        if ids.is_empty() {
            bail!("source watches no labels");
        }
        ids.sort_unstable();
        ids.dedup();
        (self.conf, self.nms, self.classes) = (conf, nms, ids);
        Ok(())
    }

    pub fn detect(&mut self, frame: &Frame) -> Result<Vec<Detection>> {
        let r = letterbox_into(frame, self.size, &mut self.input);
        let tensor = TensorRef::from_array_view(self.input.view())?;
        let outputs = self.session.run(ort::inputs![self.input_name.as_str() => tensor])?;
        let (shape, data) = outputs[0].try_extract_tensor::<f32>()?;
        let dims: Vec<i64> = shape.iter().copied().collect();
        if dims.as_slice() != [1, self.grids.len() as i64, OUTPUTS as i64] {
            bail!("unexpected YOLOX output shape {dims:?} for input {}", self.size);
        }
        let (w, h) = (frame.width as f32, frame.height as f32);
        let clamp = |v: f32| v.clamp(0.0, 1.0);
        Ok(postprocess(data, &self.grids, self.conf, self.nms, &self.classes)
            .into_iter()
            .map(|c| Detection {
                label: COCO_CLASSES[c.class_id],
                class_id: c.class_id,
                score: c.score,
                bbox: [clamp(c.b[0] / r / w), clamp(c.b[1] / r / h), clamp(c.b[2] / r / w), clamp(c.b[3] / r / h)],
            })
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grids_cover_all_three_strides() {
        let g = make_grids(640);
        assert_eq!(g.len(), 80 * 80 + 40 * 40 + 20 * 20);
        assert_eq!(g[0], [0.0, 0.0, 8.0]);
        assert_eq!(g[80 * 80], [0.0, 0.0, 16.0]);
        assert_eq!(*g.last().unwrap(), [19.0, 19.0, 32.0]);
    }

    #[test]
    fn letterbox_places_frame_top_left_on_grey_in_bgr() {
        // 100x50 red frame -> scale 6.4, content 640x320; below that stays grey
        let f = Frame { width: 100, height: 50, rgb: [255u8, 0, 0].repeat(100 * 50) };
        let mut t = Array4::zeros((1, 3, 640, 640));
        let r = letterbox_into(&f, 640, &mut t);
        assert!((r - 6.4).abs() < 1e-4);
        assert_eq!(t[[0, 2, 10, 10]], 255.0, "red lands in channel 2 (BGR)");
        assert_eq!(t[[0, 0, 10, 10]], 0.0);
        assert_eq!(t[[0, 0, 500, 10]], PAD);
    }

    fn row(cx: f32, cy: f32, obj: f32, class: usize, cls: f32) -> Vec<f32> {
        let mut r = vec![0.0; OUTPUTS];
        r[0] = cx;
        r[1] = cy;
        r[2] = 1.0_f32.ln() + 2.0_f32.ln(); // exp -> 2 cells wide
        r[3] = 2.0_f32.ln();
        r[4] = obj;
        r[5 + class] = cls;
        r
    }

    #[test]
    fn postprocess_decodes_grid_offsets_and_filters_by_class_and_confidence() {
        let grids = make_grids(640);
        let mut raw = vec![0.0f32; grids.len() * OUTPUTS];
        // anchor 81: stride 8, cell (1,1); a person (0) with 0.9*0.9 and a car (2) the same place
        let a = 81;
        raw[a * OUTPUTS..(a + 1) * OUTPUTS].copy_from_slice(&row(0.5, 0.5, 0.9, 0, 0.9));
        raw[a * OUTPUTS + 5 + 2] = 0.8;
        let only_person = postprocess(&raw, &grids, 0.4, 0.45, &[0]);
        assert_eq!(only_person.len(), 1);
        let p = only_person[0];
        // centre = (0.5+1)*8 = 12, width = 2*8 = 16
        assert!((p.b[0] - 4.0).abs() < 1e-3 && (p.b[2] - 20.0).abs() < 1e-3, "{:?}", p.b);
        assert!((p.score - 0.81).abs() < 1e-4);
        assert_eq!(postprocess(&raw, &grids, 0.4, 0.45, &[0, 2]).len(), 2, "same box, different classes both survive NMS");
        assert!(postprocess(&raw, &grids, 0.95, 0.45, &[0, 2]).is_empty());
    }

    #[test]
    fn nms_collapses_same_class_overlaps_only() {
        let c = |class_id, score, b| Cand { class_id, score, b };
        let out = nms(vec![
            c(0, 0.9, [0.0, 0.0, 10.0, 10.0]),
            c(0, 0.8, [1.0, 0.0, 11.0, 10.0]),
            c(2, 0.7, [0.0, 0.0, 10.0, 10.0]),
        ], 0.45);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].score, 0.9);
    }
}
