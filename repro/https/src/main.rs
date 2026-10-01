//! Isolate DNS, ring getentropy and Rustls from the DataFusion dependency graph.
use ring::rand::{SecureRandom, SystemRandom};
use std::time::Duration;
use worker::*;

fn main() {}

#[event(fetch)]
async fn fetch(req: Request, _env: Env, _ctx: Context) -> Result<Response> {
    if req.path() == "/healthz" {
        return Response::ok("ok");
    }
    let url = req.url()?;
    let no_pool = url
        .query_pairs()
        .any(|(key, value)| key == "pool" && value == "off");
    let loopback = req.path() == "/loopback";
    let mut bytes = [0; 32];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| Error::RustError("CSPRNG failed".into()))?;
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5));
    if no_pool {
        builder = builder.pool_max_idle_per_host(0);
    }
    let client = builder
        .build()
        .map_err(|e| Error::RustError(e.to_string()))?;
    let response = client
        .get(if loopback {
            "http://127.0.0.1:8787/"
        } else {
            "https://example.com/"
        })
        .send()
        .await
        .map_err(|e| Error::RustError(e.to_string()))?;
    let status = response.status().as_u16();
    let received = response
        .bytes()
        .await
        .map_err(|e| Error::RustError(e.to_string()))?
        .len();
    Response::ok(format!(
        "https_status={status} random_bytes={} received_bytes={received}",
        bytes.len()
    ))
}
