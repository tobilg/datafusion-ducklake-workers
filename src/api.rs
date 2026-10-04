use crate::{config::Config, policy};
use serde::Deserialize;
use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use worker::{
    Env, Request, Response, js_sys,
    wasm_bindgen::{self, prelude::*},
    wasm_bindgen_futures::JsFuture,
};

#[derive(Clone, Copy, Debug)]
pub struct ApiError {
    pub status: u16,
    pub code: &'static str,
    pub message: &'static str,
}
impl ApiError {
    pub fn input(code: &'static str, message: &'static str) -> Self {
        Self {
            status: 400,
            code,
            message,
        }
    }
    pub fn forbidden() -> Self {
        Self {
            status: 403,
            code: "QUERY_FORBIDDEN",
            message: "Only supported read-only queries and sources are allowed",
        }
    }
    pub fn resource() -> Self {
        Self {
            status: 422,
            code: "RESOURCE_LIMIT",
            message: "Query or result exceeded a resource budget",
        }
    }
    pub fn unsupported() -> Self {
        Self {
            status: 422,
            code: "UNSUPPORTED_OUTPUT",
            message: "Query output type or value is unsupported",
        }
    }
    pub fn config() -> Self {
        Self {
            status: 503,
            code: "SERVICE_UNAVAILABLE",
            message: "Catalog or storage configuration is unavailable",
        }
    }
    fn auth_config(request_id: &str, reason: &str) -> Self {
        worker::console_error!(
            "{}",
            serde_json::json!({"request_id":request_id,"category":"AUTH_CONFIGURATION_INVALID","configuration_key":"API_KEY","reason":reason})
        );
        Self {
            status: 503,
            code: "AUTH_CONFIGURATION_INVALID",
            message: "Configure API_KEY as a secret containing 32 to 4096 UTF-8 bytes",
        }
    }
    pub fn timeout() -> Self {
        Self {
            status: 504,
            code: "QUERY_TIMEOUT",
            message: "Query exceeded its time budget",
        }
    }
}
impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.code)
    }
}
impl std::error::Error for ApiError {}

static ACTIVE: AtomicBool = AtomicBool::new(false);
struct Permit;
impl Permit {
    fn acquire() -> Result<Self, ApiError> {
        ACTIVE
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| Self)
            .map_err(|_| ApiError {
                status: 429,
                code: "ISOLATE_BUSY",
                message: "This isolate is already executing a query",
            })
    }
}
impl Drop for Permit {
    fn drop(&mut self) {
        ACTIVE.store(false, Ordering::Release);
    }
}

#[wasm_bindgen(inline_js = r#"
export async function queryReadBounded(request, cap, timeout) {
  const reader=request.body?.getReader();
  if (!reader) return new Uint8Array();
  let chunks=[], size=0, expired=false;
  const timer=setTimeout(()=>{expired=true;reader.cancel().catch(()=>{});},timeout);
  try {
    for (;;) {
      const {value,done}=await reader.read();
      if(expired)return undefined;
      if(done)break;
      if(value.byteLength>cap-size){await reader.cancel();return null;}
      size+=value.byteLength;chunks.push(value);
    }
    const result=new Uint8Array(size);let at=0;
    for(const c of chunks){result.set(c,at);at+=c.byteLength;}
    return result;
  } finally {clearTimeout(timer);reader.releaseLock();}
}
"#)]
extern "C" {
    #[wasm_bindgen(js_name=queryReadBounded)]
    fn read_bounded(request: &worker::web_sys::Request, cap: u32, timeout: u32) -> js_sys::Promise;
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueryRequest {
    pub sql: String,
    pub max_rows: Option<usize>,
    pub timeout_ms: Option<u64>,
}
pub async fn handle(req: Request, env: Env, ready: bool) -> worker::Result<Response> {
    let id = uuid::Uuid::new_v4().to_string();
    let start = Instant::now();
    let result = async {
        let token = env
            .secret("API_KEY")
            .map_err(|_| ApiError::auth_config(&id, "missing_secret"))?
            .to_string();
        if token.len() < 32 || token.len() > 4096 {
            return Err(ApiError::auth_config(&id, "invalid_length"));
        }
        let supplied = req
            .headers()
            .get("Authorization")
            .ok()
            .flatten()
            .unwrap_or_default();
        let supplied = supplied.strip_prefix("Bearer ").unwrap_or("");
        if !bool::from(token.as_bytes().ct_eq(supplied.as_bytes())) {
            return Err(ApiError {
                status: 401,
                code: "UNAUTHORIZED",
                message: "A valid caller bearer token is required",
            });
        }
        let _permit = Permit::acquire()?;
        let config = Config::load(&env)?;
        let operation = async {
            if ready {
                return crate::query::run(&env, &config, None, &id, start).await;
            }
            let content_type = req
                .headers()
                .get("Content-Type")
                .ok()
                .flatten()
                .unwrap_or_default();
            if content_type.split(';').next().map(str::trim) != Some("application/json") {
                return Err(ApiError::input(
                    "INVALID_CONTENT_TYPE",
                    "Expected application/json",
                ));
            }
            let bytes = tokio::time::timeout(
                Duration::from_millis(config.timeout_ms),
                JsFuture::from(read_bounded(req.inner(), 32768, config.timeout_ms as u32)),
            )
            .await
            .map_err(|_| ApiError::timeout())?
            .map_err(|_| ApiError::input("INVALID_BODY", "Could not read request body"))?;
            if bytes.is_undefined() {
                return Err(ApiError::timeout());
            }
            if bytes.is_null() {
                return Err(ApiError {
                    status: 413,
                    code: "REQUEST_TOO_LARGE",
                    message: "Request body exceeds 32 KiB",
                });
            }
            let input: QueryRequest =
                serde_json::from_slice(&js_sys::Uint8Array::new(&bytes).to_vec()).map_err(
                    |_| ApiError::input("INVALID_BODY", "Invalid JSON or unknown request field"),
                )?;
            if input.sql.is_empty() || input.sql.len() > 16384 {
                return Err(ApiError::input(
                    "INVALID_SQL",
                    "SQL must contain 1 to 16384 UTF-8 bytes",
                ));
            }
            if input.max_rows.is_some_and(|n| n == 0 || n > config.rows)
                || input
                    .timeout_ms
                    .is_some_and(|n| n == 0 || n > config.timeout_ms)
            {
                return Err(ApiError::input(
                    "INVALID_LIMIT",
                    "Clients may reduce, but not raise, configured limits",
                ));
            }
            policy::parse(&input.sql, config.mode)?;
            crate::query::run(&env, &config, Some(input), &id, start).await
        };
        operation.await
    }
    .await;
    let (status, code, body) = match result {
        Ok(bytes) => (200, "OK", bytes),
        Err(e) => (
            e.status,
            e.code,
            serde_json::to_vec(
                &serde_json::json!({"request_id":id,"error":{"code":e.code,"message":e.message}}),
            )?,
        ),
    };
    worker::console_log!(
        "{}",
        serde_json::json!({"request_id":id,"operation":if ready{"ready"}else{"query"},"status":status,"category":code,"elapsed_ms":start.elapsed().as_millis(),"response_bytes":body.len()})
    );
    let mut response = Response::from_bytes(body)?.with_status(status);
    response
        .headers_mut()
        .set("Content-Type", "application/json; charset=utf-8")?;
    response.headers_mut().set("Cache-Control", "no-store")?;
    response.headers_mut().set("X-Request-ID", &id)?;
    if status == 401 {
        response.headers_mut().set("WWW-Authenticate", "Bearer")?;
    }
    if status == 429 {
        response.headers_mut().set("Retry-After", "1")?;
    }
    Ok(response)
}
