use std::pin::Pin;
use std::task::{Context, Poll};

use bytes::Bytes;
use futures_core::Stream;

pub const FRAME_BUFFER: usize = 32;

#[derive(Debug, Clone)]
pub struct FrameSink {
    inner: tokio::sync::mpsc::Sender<Result<Bytes, std::io::Error>>,
}

impl FrameSink {
    pub async fn send(&self, frame: impl Into<Bytes>) -> bool {
        self.inner.send(Ok(frame.into())).await.is_ok()
    }

    pub async fn done(&self) -> bool {
        self.send(crate::sse::DONE).await
    }
}

#[derive(Debug)]
pub struct FrameStream {
    inner: tokio::sync::mpsc::Receiver<Result<Bytes, std::io::Error>>,
}

impl Stream for FrameStream {
    type Item = Result<Bytes, std::io::Error>;

    fn poll_next(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.poll_recv(context)
    }
}

pub fn frame_channel() -> (FrameSink, FrameStream) {
    let (sender, receiver) = tokio::sync::mpsc::channel(FRAME_BUFFER);
    (FrameSink { inner: sender }, FrameStream { inner: receiver })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn frames_arrive_in_order_and_the_stream_ends_with_the_sender() {
        let (sink, stream) = frame_channel();
        let writer = tokio::spawn(async move {
            assert!(sink.send("data: a\n\n").await);
            assert!(sink.done().await);
        });
        let body = axum::body::Body::from_stream(stream);
        let bytes = axum::body::to_bytes(body, 4096).await.unwrap();
        writer.await.unwrap();
        assert_eq!(
            String::from_utf8(bytes.to_vec()).unwrap(),
            "data: a\n\ndata: [DONE]\n\n"
        );
    }

    #[tokio::test]
    async fn a_dropped_reader_tells_the_producer_to_stop() {
        let (sink, stream) = frame_channel();
        drop(stream);
        assert!(!sink.send("data: x\n\n").await);
        assert!(!sink.done().await);
        assert!(format!("{sink:?}").contains("FrameSink"));
    }
}
