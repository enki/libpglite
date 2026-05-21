//! `tokio-postgres` transport over the in-process PGlite protocol boundary.
//!
//! This module deliberately adapts an existing PostgreSQL client stack instead
//! of decoding rows, parameters, or prepared statement state in `libpglite`.

use std::collections::VecDeque;
use std::io;
use std::pin::Pin;
use std::task::{Context, Poll};

use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio_postgres::tls::NoTlsStream;
use tokio_postgres::{Client, Config, Connection, NoTls};

use crate::PgliteRuntime;

pub type TokioPostgresConnection<R> = Connection<PgliteProtocolStream<R>, NoTlsStream>;

pub async fn connect<R>(
    runtime: R,
    config: &Config,
) -> Result<(Client, TokioPostgresConnection<R>), tokio_postgres::Error>
where
    R: PgliteRuntime + Unpin,
{
    config
        .connect_raw(PgliteProtocolStream::new(runtime), NoTls)
        .await
}

#[derive(Debug)]
pub struct PgliteProtocolStream<R: PgliteRuntime + Unpin> {
    runtime: R,
    read_buffer: VecDeque<u8>,
    write_buffer: Vec<u8>,
    shutdown: bool,
}

impl<R> PgliteProtocolStream<R>
where
    R: PgliteRuntime + Unpin,
{
    pub fn new(runtime: R) -> Self {
        Self {
            runtime,
            read_buffer: VecDeque::new(),
            write_buffer: Vec::new(),
            shutdown: false,
        }
    }
}

impl<R> AsyncRead for PgliteProtocolStream<R>
where
    R: PgliteRuntime + Unpin,
{
    fn poll_read(
        mut self: Pin<&mut Self>,
        _cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let filled_before = buf.filled().len();
        while buf.remaining() > 0 {
            let Some(byte) = self.read_buffer.pop_front() else {
                break;
            };
            buf.put_slice(&[byte]);
        }

        if buf.filled().len() == filled_before {
            return Poll::Pending;
        }

        Poll::Ready(Ok(()))
    }
}

impl<R> AsyncWrite for PgliteProtocolStream<R>
where
    R: PgliteRuntime + Unpin,
{
    fn poll_write(
        mut self: Pin<&mut Self>,
        _cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        if self.shutdown {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "PGlite protocol stream is shut down",
            )));
        }
        self.write_buffer.extend_from_slice(buf);
        Poll::Ready(Ok(buf.len()))
    }

    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        if self.shutdown || self.write_buffer.is_empty() {
            return Poll::Ready(Ok(()));
        }

        let request = std::mem::take(&mut self.write_buffer);
        let response = self
            .runtime
            .exec_protocol_raw(&request)
            .map_err(io::Error::other)?;
        self.read_buffer.extend(response);
        cx.waker().wake_by_ref();
        Poll::Ready(Ok(()))
    }

    fn poll_shutdown(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.shutdown = true;
        let result = self.runtime.shutdown().map_err(io::Error::other);
        Poll::Ready(result)
    }
}

impl<R> Drop for PgliteProtocolStream<R>
where
    R: PgliteRuntime + Unpin,
{
    fn drop(&mut self) {
        if !self.shutdown {
            let _ = self.runtime.shutdown();
            self.shutdown = true;
        }
    }
}
