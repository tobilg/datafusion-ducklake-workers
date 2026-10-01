//! Request-owned Parquet sources. SQL URLs select data; configuration supplies capabilities.
use crate::{
    api::{ApiError, QueryRequest},
    bounded_store::BoundedStore,
    config::{Config, QueryMode},
    path_policy::PathPolicy,
    r2_store::{R2Store, ReadMetrics},
    registry::FileRegistry,
};
use datafusion::{
    catalog::{DynamicFileCatalog, TableProvider, UrlTableFactory},
    datasource::{
        file_format::parquet::ParquetFormat,
        listing::{ListingOptions, ListingTable, ListingTableConfig, ListingTableUrl},
    },
};
use object_store::{
    ClientConfigKey, ClientOptions, ObjectStore, RetryConfig, aws::AmazonS3Builder,
};
use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::Semaphore;
use url::Url;
use worker::Env;

fn variable(env: &Env, name: &str) -> Option<String> {
    env.var(name).ok().map(|v| v.to_string())
}
pub fn local(env: &Env) -> bool {
    variable(env, "ALLOW_LOCAL_HTTP").as_deref() == Some("true")
}
fn bindings(env: &Env) -> Result<HashMap<String, String>, ApiError> {
    let raw = variable(env, "R2_BINDINGS").unwrap_or_else(|| "{}".into());
    if raw.len() > 8192 {
        return Err(ApiError::config());
    }
    let map: HashMap<String, String> =
        serde_json::from_str(&raw).map_err(|_| ApiError::config())?;
    if map.len() > 16 {
        return Err(ApiError::config());
    }
    for (bucket, binding) in &map {
        PathPolicy::files(bucket).map_err(|_| ApiError::config())?;
        if binding.is_empty() || binding.len() > 128 {
            return Err(ApiError::config());
        }
        env.bucket(binding).map_err(|_| ApiError::config())?;
    }
    Ok(map)
}
struct S3Config {
    endpoint: Option<String>,
    region: String,
    virtual_host: bool,
    credentials: Option<(String, String)>,
    token: Option<String>,
    local: bool,
}
impl S3Config {
    fn load(env: &Env) -> Result<Self, ApiError> {
        let endpoint = variable(env, "S3_ENDPOINT");
        if let Some(value) = &endpoint {
            let url = Url::parse(value).map_err(|_| ApiError::config())?;
            crate::network::validate_url(&url, local(env)).map_err(|_| ApiError::config())?;
            if url.path() != "/" || url.query().is_some() {
                return Err(ApiError::config());
            }
        }
        let access = env.secret("S3_ACCESS_KEY_ID").ok().map(|v| v.to_string());
        let secret = env
            .secret("S3_SECRET_ACCESS_KEY")
            .ok()
            .map(|v| v.to_string());
        let token = env.secret("S3_SESSION_TOKEN").ok().map(|v| v.to_string());
        let credentials = match (access, secret) {
            (None, None) if token.is_none() => None,
            (Some(a), Some(s))
                if !a.is_empty() && !s.is_empty() && a.len() <= 4096 && s.len() <= 4096 =>
            {
                Some((a, s))
            }
            _ => return Err(ApiError::config()),
        };
        if token
            .as_ref()
            .is_some_and(|t| t.is_empty() || t.len() > 16384)
        {
            return Err(ApiError::config());
        }
        let region = variable(env, "S3_REGION").unwrap_or_else(|| "us-east-1".into());
        if region.is_empty()
            || region.len() > 64
            || !region
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
        {
            return Err(ApiError::config());
        }
        let virtual_host = match variable(env, "S3_ADDRESSING_STYLE")
            .as_deref()
            .unwrap_or("path")
        {
            "path" => false,
            "virtual" => true,
            _ => return Err(ApiError::config()),
        };
        Ok(Self {
            endpoint,
            region,
            virtual_host,
            credentials,
            token,
            local: local(env),
        })
    }
    fn store(&self, bucket: &str) -> Result<Arc<dyn ObjectStore>, ApiError> {
        let options = ClientOptions::new()
            .with_pool_max_idle_per_host(0)
            .with_config(ClientConfigKey::RandomizeAddresses, "false")
            .with_timeout(Duration::from_secs(5))
            .with_connect_timeout(Duration::from_secs(3))
            .with_allow_http(self.local);
        let mut builder = AmazonS3Builder::new()
            .with_bucket_name(bucket)
            .with_region(&self.region)
            .with_virtual_hosted_style_request(self.virtual_host)
            .with_client_options(options)
            .with_retry(RetryConfig {
                max_retries: 1,
                retry_timeout: Duration::from_secs(5),
                ..Default::default()
            });
        if let Some(endpoint) = &self.endpoint {
            builder = builder.with_endpoint(endpoint);
        }
        builder = match &self.credentials {
            Some((access, secret)) => builder
                .with_access_key_id(access)
                .with_secret_access_key(secret),
            None => builder.with_skip_signature(true),
        };
        if let Some(token) = &self.token {
            builder = builder.with_token(token);
        }
        Ok(Arc::new(builder.build().map_err(|_| ApiError::config())?))
    }
}
pub fn validate_config(env: &Env) -> Result<(), ApiError> {
    bindings(env)?;
    S3Config::load(env)?;
    Ok(())
}

/// URL validation is completed for every SQL source before constructing any tables or doing I/O.
fn source(raw: &str, local: bool) -> Result<Url, ApiError> {
    let url = Url::parse(raw).map_err(|_| ApiError::forbidden())?;
    if raw.chars().any(char::is_control)
        || raw.contains('\\')
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err(ApiError::forbidden());
    }
    match url.scheme() {
        "s3" | "r2" => {
            if url.query().is_some() || url.port().is_some() {
                return Err(ApiError::forbidden());
            }
            let policy = PathPolicy::files(url.host_str().ok_or_else(ApiError::forbidden)?)
                .map_err(|_| ApiError::forbidden())?;
            let (_, key) = object_store::parse_url(&url)
                .or_else(|_| {
                    // object_store does not recognize the custom r2 scheme; use identical path parsing.
                    let mut u = url.clone();
                    u.set_scheme("s3")
                        .map_err(|_| object_store::Error::NotSupported {
                            source: "URL".into(),
                        })?;
                    object_store::parse_url(&u)
                })
                .map_err(|_| ApiError::forbidden())?;
            if !key.as_ref().is_empty() {
                policy
                    .key(key.as_ref().trim_end_matches('/'))
                    .map_err(|_| ApiError::forbidden())?;
            }
            if !url.path().ends_with('/') && !url.path().ends_with(".parquet") {
                return Err(ApiError::forbidden());
            }
        }
        "https" | "http" => {
            crate::network::validate_url(&url, local).map_err(|_| ApiError::forbidden())?;
            if !url.path().ends_with(".parquet") {
                return Err(ApiError::forbidden());
            }
        }
        _ => return Err(ApiError::forbidden()),
    }
    // Reject traversal before Url's dot-segment normalization can hide it. Decode each
    // object key exactly once through object_store, retaining literal percent characters.
    let raw_path = raw
        .split_once("://")
        .and_then(|(_, s)| s.find('/').map(|i| &s[i..]))
        .unwrap_or("/");
    let path = raw_path.split('?').next().unwrap_or(raw_path);
    if path.split('/').any(|segment| {
        matches!(
            segment.to_ascii_lowercase().as_str(),
            "." | ".." | "%2e" | "%2e%2e" | ".%2e" | "%2e."
        )
    }) {
        return Err(ApiError::forbidden());
    }
    Ok(url)
}

struct PreparedTables(HashMap<String, Arc<dyn TableProvider>>);
impl std::fmt::Debug for PreparedTables {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("PreparedParquetSources(<redacted>)")
    }
}
#[async_trait::async_trait]
impl UrlTableFactory for PreparedTables {
    async fn try_new(
        &self,
        url: &str,
    ) -> datafusion::error::Result<Option<Arc<dyn TableProvider>>> {
        Ok(self.0.get(url).cloned())
    }
}

pub async fn run(
    env: &Env,
    config: &Config,
    input: Option<QueryRequest>,
    id: &str,
    start: Instant,
) -> Result<Vec<u8>, ApiError> {
    let duration = Duration::from_millis(
        input
            .as_ref()
            .and_then(|q| q.timeout_ms)
            .unwrap_or(config.timeout_ms),
    )
    .checked_sub(start.elapsed())
    .ok_or_else(ApiError::timeout)?;
    tokio::time::timeout(duration, async {
        let sources = match &input {
            Some(q) => crate::policy::parse(&q.sql, QueryMode::Files)?,
            None => vec![],
        };
        let urls = sources
            .iter()
            .map(|s| source(s, local(env)))
            .collect::<Result<Vec<_>, _>>()?;
        let bindings = bindings(env)?;
        let s3 = S3Config::load(env)?;
        let permits = Arc::new(Semaphore::new(2));
        let metrics = Arc::new(ReadMetrics::default());
        let mut registry = FileRegistry::default();
        let mut paths = Vec::new();
        for (index, url) in urls.iter().enumerate() {
            let (root, path, store): (String, String, Arc<dyn ObjectStore>) = match url.scheme() {
                "r2" | "s3" => {
                    let bucket = url.host_str().ok_or_else(ApiError::forbidden)?;
                    let policy = PathPolicy::files(bucket).map_err(|_| ApiError::forbidden())?;
                    let root = format!("{}://{bucket}/", url.scheme());
                    let store = if let Some(store) = registry.0.get(&root) {
                        store.clone()
                    } else if url.scheme() == "r2" {
                        let binding = bindings.get(bucket).ok_or_else(ApiError::config)?;
                        Arc::new(
                            R2Store::new(
                                env.bucket(binding).map_err(|_| ApiError::config())?,
                                policy,
                            )
                            .with_budget(permits.clone(), metrics.clone()),
                        ) as Arc<dyn ObjectStore>
                    } else {
                        Arc::new(
                            BoundedStore::new(s3.store(bucket)?, policy)
                                .with_budget(permits.clone(), metrics.clone()),
                        )
                    };
                    (root, url.to_string(), store)
                }
                _ => {
                    let bucket = format!("source-{index}");
                    let store = crate::http_store::HttpFile::new(url.clone(), local(env))
                        .map_err(|_| ApiError::config())?;
                    let bounded = BoundedStore::new(
                        Arc::new(store),
                        PathPolicy::files(&bucket).map_err(|_| ApiError::config())?,
                    )
                    .with_budget(permits.clone(), metrics.clone());
                    (
                        format!("remote://{bucket}/"),
                        format!("remote://{bucket}/data.parquet"),
                        Arc::new(bounded),
                    )
                }
            };
            registry.0.insert(root, store);
            paths.push(path);
        }
        let context = crate::query::session(config, Arc::new(registry))?;
        let mut tables = HashMap::new();
        for (raw, path) in sources.iter().zip(paths) {
            let options = ListingOptions::new(Arc::new(ParquetFormat::default()))
                .with_file_extension(".parquet");
            let listing = ListingTableConfig::new(
                ListingTableUrl::parse(&path).map_err(crate::query::execution_error)?,
            )
            .with_listing_options(options)
            .infer_schema(&context.state())
            .await
            .map_err(crate::query::execution_error)?;
            let table = ListingTable::try_new(listing).map_err(crate::query::execution_error)?;
            tables.insert(raw.clone(), Arc::new(table) as Arc<dyn TableProvider>);
        }
        let approved = tables.values().cloned().collect::<Vec<_>>();
        let catalog = Arc::new(DynamicFileCatalog::new(
            context.state().catalog_list().clone(),
            Arc::new(PreparedTables(tables)),
        ));
        let context: datafusion::prelude::SessionContext = context
            .into_state_builder()
            .with_catalog_list(catalog)
            .build()
            .into();
        crate::query::execute(
            &context,
            config,
            input,
            id,
            start,
            &approved,
            Some(&metrics),
        )
        .await
    })
    .await
    .map_err(|_| ApiError::timeout())?
}
