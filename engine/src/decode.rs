//! ffmpeg decoding of one source into tightly packed RGB24 frames.
//!
//! Adapted from the decoder of jenggo/poc-yolo-coco; reconnecting is the source runner's job here, and network
//! timeouts are set so a dead camera surfaces as an error instead of a hang.
use anyhow::{Context, Result, anyhow, bail};
use ffmpeg::format::Pixel;
use ffmpeg::media::Type;
use ffmpeg::software::scaling::{Context as ScalingContext, flag::Flags};
use ffmpeg::util::frame::video::Video;
use ffmpeg_next as ffmpeg;

/// One decoded video frame in tightly packed RGB24.
#[derive(Debug, Clone)]
pub struct Frame {
    pub width: usize,
    pub height: usize,
    pub rgb: Vec<u8>,
}

/// Microseconds before a stalled open/read on a network source fails.
const NETWORK_TIMEOUT_US: &str = "8000000";

pub struct Decoder {
    input: ffmpeg::format::context::Input,
    decoder: ffmpeg::decoder::Video,
    scaler: ScalingContext,
    stream_index: usize,
    fps: f32,
    eof_sent: bool,
}

// SAFETY: the ffmpeg contexts inside a Decoder are only ever used from one thread at a time (it is moved into the reader
// thread and never shared); the raw pointers merely stop the compiler from proving that.
unsafe impl Send for Decoder {}

pub fn init() -> Result<()> {
    ffmpeg::init().context("initialising ffmpeg")?;
    ffmpeg::util::log::set_level(ffmpeg::util::log::Level::Error);
    Ok(())
}

impl Decoder {
    pub fn open(source: &str) -> Result<Self> {
        let mut opts = ffmpeg::Dictionary::new();
        // rtsp over TCP is far more robust than UDP across NAT/docker; ignored for other protocols.
        opts.set("rtsp_transport", "tcp");
        opts.set("timeout", NETWORK_TIMEOUT_US);
        opts.set("rw_timeout", NETWORK_TIMEOUT_US);
        let input = ffmpeg::format::input_with_dictionary(&source, opts)
            .with_context(|| format!("opening the video source {}", redact(source)))?;

        let stream = input
            .streams()
            .best(Type::Video)
            .ok_or_else(|| anyhow!("no video stream found"))?;
        let stream_index = stream.index();
        let fps = resolve_fps(stream.avg_frame_rate(), stream.rate());

        let decoder = ffmpeg::codec::context::Context::from_parameters(stream.parameters())
            .context("creating a decoder")?
            .decoder()
            .video()
            .context("opening the video decoder")?;

        let scaler = ScalingContext::get(
            decoder.format(),
            decoder.width(),
            decoder.height(),
            Pixel::RGB24,
            decoder.width(),
            decoder.height(),
            Flags::BILINEAR,
        )
        .context("creating the RGB scaler")?;

        Ok(Self { input, decoder, scaler, stream_index, fps, eof_sent: false })
    }

    pub fn fps(&self) -> f32 {
        self.fps
    }

    /// Next decoded frame, or `None` at end of stream.
    pub fn next_frame(&mut self) -> Result<Option<Frame>> {
        loop {
            let mut decoded = Video::empty();
            match self.decoder.receive_frame(&mut decoded) {
                Ok(()) => {
                    let mut rgb = Video::empty();
                    self.scaler.run(&decoded, &mut rgb).context("converting a frame to RGB24")?;
                    return Ok(Some(pack(&rgb)?));
                }
                Err(ffmpeg::Error::Other { errno }) if errno == ffmpeg::util::error::EAGAIN => {}
                Err(ffmpeg::Error::Eof) => return Ok(None),
                Err(err) => bail!("decoding a frame: {err}"),
            }
            if self.eof_sent {
                return Ok(None);
            }
            match self.next_video_packet() {
                Some(packet) => self.decoder.send_packet(&packet).context("feeding a packet")?,
                None => {
                    self.decoder.send_eof().context("flushing the decoder")?;
                    self.eof_sent = true;
                }
            }
        }
    }

    fn next_video_packet(&mut self) -> Option<ffmpeg::Packet> {
        let index = self.stream_index;
        self.input.packets().find(|(s, _)| s.index() == index).map(|(_, p)| p)
    }
}

/// Copy the scaler output row by row so stride padding is not leaked into the packed buffer.
fn pack(rgb: &Video) -> Result<Frame> {
    let (width, height) = (rgb.width() as usize, rgb.height() as usize);
    let (stride, row_len) = (rgb.stride(0), width * 3);
    if stride < row_len {
        bail!("decoded frame stride {stride} is smaller than the row length {row_len}");
    }
    let data = rgb.data(0);
    let mut out = Vec::with_capacity(row_len * height);
    for row in 0..height {
        out.extend_from_slice(&data[row * stride..row * stride + row_len]);
    }
    Ok(Frame { width, height, rgb: out })
}

/// `avg_frame_rate`, then `r_frame_rate`, then 25 fps.
fn resolve_fps(avg: ffmpeg::Rational, base: ffmpeg::Rational) -> f32 {
    for c in [avg, base] {
        if c.numerator() > 0 && c.denominator() > 0 {
            let fps = f64::from(c) as f32;
            if fps.is_finite() && fps > 0.0 {
                return fps;
            }
        }
    }
    25.0
}

/// Hide credentials in rtsp://user:pass@host/...
pub fn redact(url: &str) -> String {
    match (url.split_once("://"), url.rsplit_once('@')) {
        (Some((scheme, _)), Some((_, host))) => format!("{scheme}://***@{host}"),
        _ => url.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn average_rate_wins_then_base_then_25() {
        assert!((resolve_fps(ffmpeg::Rational::new(30, 1), ffmpeg::Rational::new(60, 1)) - 30.0).abs() < 1e-3);
        assert!((resolve_fps(ffmpeg::Rational::new(0, 0), ffmpeg::Rational::new(24, 1)) - 24.0).abs() < 1e-3);
        assert_eq!(resolve_fps(ffmpeg::Rational::new(0, 0), ffmpeg::Rational::new(0, 0)), 25.0);
    }

    #[test]
    fn credentials_are_redacted() {
        assert_eq!(redact("rtsp://user:pass@10.0.0.5:554/s"), "rtsp://***@10.0.0.5:554/s");
        assert_eq!(redact("http://host/live.m3u8"), "http://host/live.m3u8");
    }
}
