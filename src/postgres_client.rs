//! `tokio-postgres` transport over the in-process PGlite protocol boundary.
//!
//! This module deliberately adapts an existing PostgreSQL client stack instead
//! of decoding rows, parameters, or prepared statement state in `libpglite`.

use std::collections::VecDeque;
use std::future::Future;
use std::io;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};

use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio_postgres::tls::NoTlsStream;
use tokio_postgres::{Client, Config, Connection, NoTls};

use crate::{PgliteBackendOutputLedger, PgliteRuntime};

pub type TokioPostgresConnection<R> = Connection<PgliteProtocolStream<R>, NoTlsStream>;

#[derive(Debug, thiserror::Error)]
pub enum PgliteTokioPostgresSessionError<DriverError>
where
    DriverError: std::error::Error + Send + Sync + 'static,
{
    #[error("libpglite tokio-postgres client connection failed: {0}")]
    Connect(#[source] tokio_postgres::Error),
    #[error("libpglite tokio-postgres connection-driver spawn failed: {0}")]
    Driver(#[source] DriverError),
}

#[derive(Debug)]
pub struct PgliteTokioPostgresSession<DriverGuard> {
    client: Client,
    driver_guard: DriverGuard,
    backend_output: PgliteSessionBackendOutputDrain,
}

impl<DriverGuard> PgliteTokioPostgresSession<DriverGuard> {
    pub fn client(&self) -> &Client {
        &self.client
    }

    pub fn client_mut(&mut self) -> &mut Client {
        &mut self.client
    }

    pub fn driver_guard(&self) -> &DriverGuard {
        &self.driver_guard
    }

    pub fn backend_output_mut(&mut self) -> &mut PgliteSessionBackendOutputDrain {
        &mut self.backend_output
    }

    pub fn into_parts(self) -> (Client, DriverGuard, PgliteSessionBackendOutputDrain) {
        (self.client, self.driver_guard, self.backend_output)
    }
}

#[derive(Debug)]
pub struct PgliteSessionBackendOutputDrain {
    ledger: Arc<Mutex<PgliteBackendOutputLedger>>,
}

impl PgliteSessionBackendOutputDrain {
    fn new() -> (Self, PgliteSessionBackendOutputWriter) {
        let ledger = Arc::new(Mutex::new(PgliteBackendOutputLedger::empty()));
        (
            Self {
                ledger: Arc::clone(&ledger),
            },
            PgliteSessionBackendOutputWriter { ledger },
        )
    }

    pub fn take_backend_output(&mut self) -> PgliteBackendOutputLedger {
        let mut ledger = self
            .ledger
            .lock()
            .expect("libpglite session backend output drain mutex poisoned");
        std::mem::take(&mut *ledger)
    }
}

#[derive(Debug, Clone)]
struct PgliteSessionBackendOutputWriter {
    ledger: Arc<Mutex<PgliteBackendOutputLedger>>,
}

impl PgliteSessionBackendOutputWriter {
    fn extend(&self, output: PgliteBackendOutputLedger) {
        if output.is_empty() {
            return;
        }
        let mut ledger = self
            .ledger
            .lock()
            .expect("libpglite session backend output writer mutex poisoned");
        ledger.extend(output.records);
    }
}

pub async fn connect<R>(
    runtime: R,
    config: &Config,
) -> Result<
    (
        Client,
        TokioPostgresConnection<R>,
        PgliteSessionBackendOutputDrain,
    ),
    tokio_postgres::Error,
>
where
    R: PgliteRuntime + Unpin,
{
    let (backend_output, writer) = PgliteSessionBackendOutputDrain::new();
    let (client, connection) = config
        .connect_raw(PgliteProtocolStream::new(runtime, writer), NoTls)
        .await?;
    Ok((client, connection, backend_output))
}

pub async fn connect_with_driver<R, SpawnDriver, SpawnFuture, DriverGuard, DriverError>(
    runtime: R,
    config: &Config,
    spawn_driver: SpawnDriver,
) -> Result<PgliteTokioPostgresSession<DriverGuard>, PgliteTokioPostgresSessionError<DriverError>>
where
    R: PgliteRuntime + Unpin,
    SpawnDriver: FnOnce(TokioPostgresConnection<R>) -> SpawnFuture,
    SpawnFuture: Future<Output = Result<DriverGuard, DriverError>>,
    DriverError: std::error::Error + Send + Sync + 'static,
{
    let (client, connection, backend_output) = connect(runtime, config)
        .await
        .map_err(PgliteTokioPostgresSessionError::Connect)?;
    let driver_guard = spawn_driver(connection)
        .await
        .map_err(PgliteTokioPostgresSessionError::Driver)?;
    Ok(PgliteTokioPostgresSession {
        client,
        driver_guard,
        backend_output,
    })
}

#[derive(Debug)]
pub struct PgliteProtocolStream<R: PgliteRuntime + Unpin> {
    runtime: R,
    backend_output: PgliteSessionBackendOutputWriter,
    read_buffer: VecDeque<u8>,
    write_buffer: Vec<u8>,
    shutdown: bool,
}

impl<R> PgliteProtocolStream<R>
where
    R: PgliteRuntime + Unpin,
{
    fn new(mut runtime: R, backend_output: PgliteSessionBackendOutputWriter) -> Self {
        backend_output.extend(runtime.take_backend_output());
        Self {
            runtime,
            backend_output,
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
        let response = match self.runtime.exec_protocol_raw(&request) {
            Ok(response) => response,
            Err(error) => {
                let backend_output = self.runtime.take_backend_output();
                self.backend_output.extend(backend_output);
                return Poll::Ready(Err(io::Error::other(error)));
            }
        };
        let backend_output = self.runtime.take_backend_output();
        self.backend_output.extend(backend_output);
        self.read_buffer.extend(response);
        cx.waker().wake_by_ref();
        Poll::Ready(Ok(()))
    }

    fn poll_shutdown(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.shutdown = true;
        let result = self.runtime.shutdown().map_err(io::Error::other);
        let backend_output = self.runtime.take_backend_output();
        self.backend_output.extend(backend_output);
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
            let backend_output = self.runtime.take_backend_output();
            self.backend_output.extend(backend_output);
            self.shutdown = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{PgliteBackendOutputRecord, PgliteBackendOutputStream, PgliteConfig, PgliteResult};
    use std::collections::VecDeque;
    use tokio::io::AsyncWriteExt;

    #[derive(Debug)]
    struct FixtureRuntime {
        ledgers: VecDeque<PgliteBackendOutputLedger>,
    }

    impl FixtureRuntime {
        fn new(phases: impl IntoIterator<Item = &'static str>) -> Self {
            Self {
                ledgers: phases
                    .into_iter()
                    .map(|phase| {
                        let mut ledger = PgliteBackendOutputLedger::empty();
                        ledger.push(PgliteBackendOutputRecord::new(
                            PgliteBackendOutputStream::Stdout,
                            phase,
                            format!("{phase} output"),
                        ));
                        ledger
                    })
                    .collect(),
            }
        }
    }

    impl PgliteRuntime for FixtureRuntime {
        fn open(_config: PgliteConfig) -> PgliteResult<Self> {
            Ok(Self::new([]))
        }

        fn exec_protocol_raw(&mut self, _message: &[u8]) -> PgliteResult<Vec<u8>> {
            Ok(Vec::new())
        }

        fn shutdown(&mut self) -> PgliteResult<()> {
            Ok(())
        }

        fn take_backend_output(&mut self) -> PgliteBackendOutputLedger {
            self.ledgers.pop_front().unwrap_or_default()
        }
    }

    #[tokio::test]
    async fn protocol_stream_moves_startup_protocol_and_shutdown_output_into_session_drain() {
        let (mut drain, writer) = PgliteSessionBackendOutputDrain::new();
        let runtime =
            FixtureRuntime::new(["fixture_startup", "fixture_protocol", "fixture_shutdown"]);
        let mut stream = PgliteProtocolStream::new(runtime, writer);

        assert_phase(
            drain.take_backend_output(),
            "fixture_startup",
            "startup output must be drained when protocol stream is born",
        );

        stream
            .write_all(b"fixture protocol request")
            .await
            .expect("fixture protocol write succeeds");
        stream
            .flush()
            .await
            .expect("fixture protocol flush succeeds");
        assert_phase(
            drain.take_backend_output(),
            "fixture_protocol",
            "protocol output must be drained after protocol flush",
        );

        stream
            .shutdown()
            .await
            .expect("fixture protocol shutdown succeeds");
        assert_phase(
            drain.take_backend_output(),
            "fixture_shutdown",
            "shutdown output must be drained after protocol shutdown",
        );
        assert!(drain.take_backend_output().is_empty());
    }

    fn assert_phase(ledger: PgliteBackendOutputLedger, phase: &str, context: &str) {
        assert!(
            ledger.records.iter().any(|record| record.phase == phase),
            "{context}: expected phase {phase}, got {:?}",
            ledger.records
        );
    }
}
