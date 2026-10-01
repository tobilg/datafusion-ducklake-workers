//! Request-scoped, read-only DataFusion queries in an Emscripten Worker.
mod api;
mod bounded_store;
#[cfg(all(feature = "protocol-probe", feature = "ducklake"))]
mod catalog_probe;
mod config;
mod files;
mod http_store;
mod network;
mod network_policy;
mod output;
mod path_policy;
mod policy;
mod query;
#[cfg(all(target_os = "emscripten", feature = "protocol-probe"))]
mod r2_contract;
#[cfg(target_os = "emscripten")]
mod r2_store;
mod registry;
mod storage;
#[cfg(feature = "protocol-probe")]
use datafusion::{
    arrow::array::Int64Array,
    execution::{
        disk_manager::{DiskManagerBuilder, DiskManagerMode},
        memory_pool::GreedyMemoryPool,
        runtime_env::RuntimeEnvBuilder,
    },
    prelude::{SessionConfig, SessionContext},
};
#[cfg(feature = "protocol-probe")]
use futures_util::TryStreamExt;
#[cfg(feature = "protocol-probe")]
use std::{sync::Arc, time::Duration};
use worker::*;

fn main() {}

#[cfg(feature = "protocol-probe")]
fn session() -> std::result::Result<SessionContext, Box<dyn std::error::Error>> {
    session_with_store(None)
}
#[cfg(feature = "protocol-probe")]
fn session_with_store(
    registry: Option<Arc<registry::CatalogRegistry>>,
) -> std::result::Result<SessionContext, Box<dyn std::error::Error>> {
    let mut builder = RuntimeEnvBuilder::new()
        .with_memory_pool(Arc::new(GreedyMemoryPool::new(48 * 1024 * 1024)))
        .with_disk_manager_builder(
            DiskManagerBuilder::default().with_mode(DiskManagerMode::Disabled),
        );
    if let Some(registry) = registry {
        builder = builder.with_object_store_registry(registry);
    }
    let runtime = builder.build_arc()?;
    let config = SessionConfig::new()
        .with_target_partitions(1)
        .with_batch_size(1024);
    #[cfg(feature = "ducklake")]
    let config = {
        let mut options = datafusion_ducklake_provider::DuckLakeConfig::default();
        options.contain_paths = true;
        config.with_option_extension(options)
    };
    Ok(SessionContext::new_with_config_rt(config, runtime))
}

#[cfg(feature = "protocol-probe")]
async fn select_one() -> std::result::Result<i64, Box<dyn std::error::Error>> {
    let context = session()?;
    let mut stream = context
        .sql("SELECT 1 AS value")
        .await?
        .execute_stream()
        .await?;
    let batch = stream.try_next().await?.ok_or("no batch")?;
    let values = batch
        .column(0)
        .as_any()
        .downcast_ref::<Int64Array>()
        .ok_or("unexpected type")?;
    if values.len() != 1 || stream.try_next().await?.is_some() {
        return Err("unexpected result size".into());
    }
    Ok(values.value(0))
}

#[event(fetch)]
async fn fetch(req: Request, env: Env, _ctx: Context) -> Result<Response> {
    match (req.method(), req.path().as_str()) {
        (Method::Get, "/healthz") => Response::ok("ok"),
        (Method::Get, "/readyz") => api::handle(req, env, true).await,
        (Method::Post, "/query") => api::handle(req, env, false).await,
        #[cfg(feature = "protocol-probe")]
        (Method::Get, "/__spike/select-one") => {
            match tokio::time::timeout(Duration::from_secs(10), select_one()).await {
                Ok(Ok(value)) => {
                    Response::from_json(&serde_json::json!({"value": value.to_string()}))
                }
                Ok(Err(_)) => Response::error("Engine probe failed", 503),
                Err(_) => Response::error("Engine probe timed out", 504),
            }
        }
        #[cfg(all(feature = "protocol-probe", feature = "ducklake"))]
        (Method::Get, "/__spike/catalog") => catalog_probe::run(&env, false).await,
        #[cfg(feature = "protocol-probe")]
        (Method::Get, "/__spike/r2-contract") => match r2_contract::run(&env).await {
            Ok(result) => Response::from_json(&result),
            Err(error) => {
                console_error!("R2 contract failed: {}", error);
                Response::error("R2 contract failed", 500)
            }
        },
        #[cfg(all(feature = "protocol-probe", feature = "ducklake"))]
        (Method::Get, "/__spike/attach") => catalog_probe::run(&env, true).await,
        _ => Response::error("Not found", 404),
    }
}
