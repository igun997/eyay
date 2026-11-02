//! Frame drawing and JPEG helpers: live overlay in the design-system style (accent box, filled label tab).
use anyhow::{Context, Result};
use image::codecs::jpeg::JpegEncoder;
use image::imageops::{self, FilterType};
use image::{ExtendedColorType, RgbImage};

use crate::config::Axis;
use crate::decode::Frame;
use crate::model::{BBox, Snapshot};

const ACCENT: [u8; 3] = [0x4A, 0x9F, 0xD8];
const INK: [u8; 3] = [0x06, 0x12, 0x1C];
const OVER_LIMIT: [u8; 3] = [0xFF, 0x6B, 0x6B]; // sev-critical
const GEOMETRY: [u8; 3] = [0xFF, 0xD6, 0x0A];

pub fn encode_jpeg(img: &RgbImage, quality: u8) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(64 * 1024);
    JpegEncoder::new_with_quality(&mut out, quality)
        .encode(img.as_raw(), img.width(), img.height(), ExtendedColorType::Rgb8)
        .context("jpeg encode")?;
    Ok(out)
}

pub fn decode_jpeg(jpeg: &[u8]) -> Result<RgbImage> {
    Ok(image::load_from_memory_with_format(jpeg, image::ImageFormat::Jpeg)
        .context("snapshot is not a decodable image")?
        .to_rgb8())
}

fn frame_image(frame: &Frame, max_width: u32) -> RgbImage {
    let img = RgbImage::from_raw(frame.width as u32, frame.height as u32, frame.rgb.clone())
        .expect("frame buffer matches its dimensions");
    resize_max_width(img, max_width)
}

pub fn resize_max_width(img: RgbImage, max_width: u32) -> RgbImage {
    if img.width() <= max_width {
        return img;
    }
    let h = ((f64::from(img.height()) * f64::from(max_width) / f64::from(img.width())).round() as u32).max(1);
    imageops::resize(&img, max_width, h, FilterType::Triangle)
}

/// JPEG stored with an event: raw frame (the UI overlays the box), at most 1280 px wide.
pub fn snapshot_jpeg(frame: &Frame, max_width: u32, quality: u8) -> Result<Snapshot> {
    let img = frame_image(frame, max_width);
    Ok(Snapshot { jpeg: encode_jpeg(&img, quality)?, width: img.width() as i32, height: img.height() as i32 })
}

/// One live detection with its estimated speed, when the camera is calibrated.
#[derive(Debug, Clone, PartialEq)]
pub struct LiveDet {
    pub label: String,
    pub score: f32,
    /// x1,y1,x2,y2 normalised 0..1
    pub bbox: [f32; 4],
    pub speed_kmh: Option<f32>,
    pub over_limit: bool,
}

/// Calibration geometry drawn over the live view (fractions of the frame).
#[derive(Debug, Clone, PartialEq)]
pub enum Geometry {
    Lines { axis: Axis, line1: f32, line2: f32 },
    Quad([[f32; 2]; 4]),
}

pub fn annotate(frame: &Frame, dets: &[LiveDet], geometry: Option<&Geometry>, max_width: u32) -> Result<Vec<u8>> {
    let mut img = frame_image(frame, max_width);
    let (w, h) = (img.width() as f32, img.height() as f32);
    let thick = ((w / 480.0).round() as i32).max(2);
    let scale = if w >= 900.0 { 2 } else { 1 };
    match geometry {
        Some(Geometry::Lines { axis, line1, line2 }) => {
            for l in [line1, line2] {
                match axis {
                    Axis::X => line(&mut img, (l * w) as i32, 0, (l * w) as i32, h as i32, 1, GEOMETRY),
                    Axis::Y => line(&mut img, 0, (l * h) as i32, w as i32, (l * h) as i32, 1, GEOMETRY),
                }
            }
        }
        Some(Geometry::Quad(p)) => {
            for i in 0..4 {
                let (a, b) = (p[i], p[(i + 1) % 4]);
                line(&mut img, (a[0] * w) as i32, (a[1] * h) as i32, (b[0] * w) as i32, (b[1] * h) as i32, 1, GEOMETRY);
            }
        }
        None => {}
    }
    for d in dets {
        let colour = if d.over_limit { OVER_LIMIT } else { ACCENT };
        let (x1, y1) = ((d.bbox[0] * w) as i32, (d.bbox[1] * h) as i32);
        let (x2, y2) = ((d.bbox[2] * w) as i32, (d.bbox[3] * h) as i32);
        rect(&mut img, x1, y1, x2, y2, thick, colour);
        let mut text = format!("{} {:.2}", d.label.to_uppercase(), d.score);
        if let Some(k) = d.speed_kmh {
            text.push_str(&format!(" {k:.0} KMH"));
        }
        let tw = text_width(&text, scale) + 8;
        let th = 7 * scale + 6;
        let top = (y1 - th).max(0);
        fill(&mut img, x1, top, x1 + tw, top + th, colour);
        draw_text(&mut img, x1 + 4, top + 3, &text, scale, INK);
    }
    encode_jpeg(&img, 80)
}

fn line(img: &mut RgbImage, x0: i32, y0: i32, x1: i32, y1: i32, t: i32, c: [u8; 3]) {
    let (dx, dy) = ((x1 - x0).abs(), -(y1 - y0).abs());
    let (sx, sy) = (if x0 < x1 { 1 } else { -1 }, if y0 < y1 { 1 } else { -1 });
    let (mut x, mut y, mut err) = (x0, y0, dx + dy);
    loop {
        fill(img, x, y, x + t, y + t, c);
        if x == x1 && y == y1 {
            break;
        }
        let e2 = 2 * err;
        if e2 >= dy {
            err += dy;
            x += sx;
        }
        if e2 <= dx {
            err += dx;
            y += sy;
        }
    }
}

/// Crop around a normalised box (+`margin` of its larger side on every side, clamped); the full frame when the crop would
/// be tinier than `min_px` on either side.
pub fn crop_jpeg(jpeg: &[u8], bbox: Option<&BBox>, margin: f64, min_px: i64) -> Result<Vec<u8>> {
    let img = decode_jpeg(jpeg)?;
    let (iw, ih) = (i64::from(img.width()), i64::from(img.height()));
    let mut out = img;
    if let Some(b) = bbox {
        let (x1, y1) = (b.x * iw as f64, b.y * ih as f64);
        let (x2, y2) = (x1 + b.w * iw as f64, y1 + b.h * ih as f64);
        let m = margin * (x2 - x1).max(y2 - y1);
        let cx1 = ((x1 - m) as i64).max(0);
        let cy1 = ((y1 - m) as i64).max(0);
        let cx2 = ((x2 + m).round() as i64).min(iw);
        let cy2 = ((y2 + m).round() as i64).min(ih);
        if cx2 - cx1 >= min_px && cy2 - cy1 >= min_px {
            out = imageops::crop_imm(&out, cx1 as u32, cy1 as u32, (cx2 - cx1) as u32, (cy2 - cy1) as u32).to_image();
        }
    }
    encode_jpeg(&out, 90)
}

fn put(img: &mut RgbImage, x: i32, y: i32, c: [u8; 3]) {
    if x >= 0 && y >= 0 && (x as u32) < img.width() && (y as u32) < img.height() {
        img.put_pixel(x as u32, y as u32, image::Rgb(c));
    }
}

fn fill(img: &mut RgbImage, x1: i32, y1: i32, x2: i32, y2: i32, c: [u8; 3]) {
    for y in y1..y2 {
        for x in x1..x2 {
            put(img, x, y, c);
        }
    }
}

fn rect(img: &mut RgbImage, x1: i32, y1: i32, x2: i32, y2: i32, t: i32, c: [u8; 3]) {
    fill(img, x1, y1, x2, y1 + t, c);
    fill(img, x1, y2 - t, x2, y2, c);
    fill(img, x1, y1, x1 + t, y2, c);
    fill(img, x2 - t, y1, x2, y2, c);
}

fn text_width(text: &str, scale: i32) -> i32 {
    text.chars().count() as i32 * 6 * scale
}

fn draw_text(img: &mut RgbImage, x: i32, y: i32, text: &str, scale: i32, c: [u8; 3]) {
    for (i, ch) in text.chars().enumerate() {
        let Some(rows) = glyph(ch) else { continue };
        for (ry, row) in rows.iter().enumerate() {
            for rx in 0..5 {
                if row & (1 << (4 - rx)) != 0 {
                    let (px, py) = (x + (i as i32 * 6 + rx) * scale, y + ry as i32 * scale);
                    fill(img, px, py, px + scale, py + scale, c);
                }
            }
        }
    }
}

/// 5x7 glyphs, one byte per row, five low bits per row (bit 4 is the left column).
fn glyph(c: char) -> Option<[u8; 7]> {
    let rows = match c {
        ' ' => [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000],
        '0' => [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
        '1' => [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
        '2' => [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
        '3' => [0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110],
        '4' => [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
        '5' => [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
        '6' => [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
        '7' => [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
        '8' => [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
        '9' => [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
        'A' => [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
        'B' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
        'C' => [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
        'D' => [0b11100, 0b10010, 0b10001, 0b10001, 0b10001, 0b10010, 0b11100],
        'E' => [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
        'F' => [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
        'G' => [0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01111],
        'H' => [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
        'I' => [0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
        'J' => [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
        'K' => [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
        'L' => [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
        'M' => [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
        'N' => [0b10001, 0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001],
        'O' => [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
        'P' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
        'Q' => [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
        'R' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
        'S' => [0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110],
        'T' => [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
        'U' => [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
        'V' => [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100],
        'W' => [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
        'X' => [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
        'Y' => [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
        'Z' => [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],
        'a' => [0b00000, 0b00000, 0b01110, 0b00001, 0b01111, 0b10001, 0b01111],
        'b' => [0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b11110],
        'c' => [0b00000, 0b00000, 0b01110, 0b10000, 0b10000, 0b10001, 0b01110],
        'd' => [0b00001, 0b00001, 0b01111, 0b10001, 0b10001, 0b10001, 0b01111],
        'e' => [0b00000, 0b00000, 0b01110, 0b10001, 0b11111, 0b10000, 0b01110],
        'f' => [0b00110, 0b01001, 0b01000, 0b11100, 0b01000, 0b01000, 0b01000],
        'g' => [0b00000, 0b01111, 0b10001, 0b10001, 0b01111, 0b00001, 0b01110],
        'h' => [0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b10001],
        'i' => [0b00100, 0b00000, 0b01100, 0b00100, 0b00100, 0b00100, 0b01110],
        'j' => [0b00010, 0b00000, 0b00110, 0b00010, 0b00010, 0b10010, 0b01100],
        'k' => [0b10000, 0b10000, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010],
        'l' => [0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
        'm' => [0b00000, 0b00000, 0b11010, 0b10101, 0b10101, 0b10001, 0b10001],
        'n' => [0b00000, 0b00000, 0b11110, 0b10001, 0b10001, 0b10001, 0b10001],
        'o' => [0b00000, 0b00000, 0b01110, 0b10001, 0b10001, 0b10001, 0b01110],
        'p' => [0b00000, 0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000],
        'q' => [0b00000, 0b01111, 0b10001, 0b10001, 0b01111, 0b00001, 0b00001],
        'r' => [0b00000, 0b00000, 0b10110, 0b11001, 0b10000, 0b10000, 0b10000],
        's' => [0b00000, 0b00000, 0b01111, 0b10000, 0b01110, 0b00001, 0b11110],
        't' => [0b01000, 0b01000, 0b11100, 0b01000, 0b01000, 0b01001, 0b00110],
        'u' => [0b00000, 0b00000, 0b10001, 0b10001, 0b10001, 0b10011, 0b01101],
        'v' => [0b00000, 0b00000, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100],
        'w' => [0b00000, 0b00000, 0b10001, 0b10001, 0b10101, 0b10101, 0b01010],
        'x' => [0b00000, 0b00000, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001],
        'y' => [0b00000, 0b10001, 0b10001, 0b10001, 0b01111, 0b00001, 0b01110],
        'z' => [0b00000, 0b00000, 0b11111, 0b00010, 0b00100, 0b01000, 0b11111],
        '.' => [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100],
        ',' => [0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b00100, 0b01000],
        '#' => [0b01010, 0b01010, 0b11111, 0b01010, 0b11111, 0b01010, 0b01010],
        ':' => [0b00000, 0b01100, 0b01100, 0b00000, 0b01100, 0b01100, 0b00000],
        '/' => [0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000],
        '-' => [0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000],
        '%' => [0b11001, 0b11010, 0b00010, 0b00100, 0b01000, 0b01011, 0b10011],
        '(' => [0b00010, 0b00100, 0b01000, 0b01000, 0b01000, 0b00100, 0b00010],
        ')' => [0b01000, 0b00100, 0b00010, 0b00010, 0b00010, 0b00100, 0b01000],
        _ => return None,
    };
    Some(rows)
}


#[cfg(test)]
mod tests {
    use super::*;

    fn jpeg(w: u32, h: u32) -> Vec<u8> {
        let mut img = RgbImage::from_pixel(w, h, image::Rgb([40, 80, 120]));
        fill(&mut img, (w / 2) as i32, (h / 2) as i32, (w / 2 + 20) as i32, (h / 2 + 20) as i32, [255, 255, 255]);
        encode_jpeg(&img, 85).unwrap()
    }

    fn size(b: &[u8]) -> (u32, u32) {
        let i = decode_jpeg(b).unwrap();
        (i.width(), i.height())
    }

    #[test]
    fn crop_adds_margin_of_the_larger_side() {
        // 100x200 px box in a 640x360 frame, 15 % margin of 200 = 30 px per side -> 160x260
        let b = BBox { x: 200.0 / 640.0, y: 50.0 / 360.0, w: 100.0 / 640.0, h: 200.0 / 360.0 };
        let (w, h) = size(&crop_jpeg(&jpeg(640, 360), Some(&b), 0.15, 40).unwrap());
        assert!(w.abs_diff(160) <= 2 && h.abs_diff(260) <= 2, "{w}x{h}");
    }

    #[test]
    fn crop_clamps_to_frame_edges() {
        let b = BBox { x: 0.0, y: 0.0, w: 100.0 / 640.0, h: 100.0 / 360.0 };
        assert_eq!(size(&crop_jpeg(&jpeg(640, 360), Some(&b), 0.15, 40).unwrap()), (115, 115));
    }

    #[test]
    fn tiny_crop_falls_back_to_the_full_frame() {
        let b = BBox { x: 0.5, y: 0.5, w: 10.0 / 640.0, h: 10.0 / 360.0 };
        assert_eq!(size(&crop_jpeg(&jpeg(640, 360), Some(&b), 0.15, 40).unwrap()), (640, 360));
        assert_eq!(size(&crop_jpeg(&jpeg(640, 360), None, 0.15, 40).unwrap()), (640, 360));
    }

    #[test]
    fn crop_rejects_non_image() {
        assert!(crop_jpeg(b"not a jpeg", None, 0.15, 40).is_err());
    }

    #[test]
    fn snapshot_is_scaled_down_to_max_width() {
        let f = Frame { width: 1920, height: 1080, rgb: vec![10; 1920 * 1080 * 3] };
        let s = snapshot_jpeg(&f, 1280, 85).unwrap();
        assert_eq!((s.width, s.height), (1280, 720));
    }
}
