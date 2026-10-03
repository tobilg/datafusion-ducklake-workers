use crate::api::ApiError;
#[cfg(feature = "ducklake")]
use crate::path_policy::PathPolicy;
use worker::Env;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum QueryMode {
    Files,
    DuckLake,
}
pub struct Config {
    pub mode: QueryMode,
    #[cfg(feature = "ducklake")]
    pub catalog: Option<CatalogConfig>,
    pub rows: usize,
    pub bytes: usize,
    pub timeout_ms: u64,
    pub memory_bytes: usize,
}
#[cfg(feature = "ducklake")]
pub struct CatalogConfig {
    pub uri: String,
    pub ssl: bool,
    pub path: String,
    pub policy: PathPolicy,
    pub backend: String,
}
#[cfg(feature = "ducklake")]
impl CatalogConfig {
    fn load(env: &Env) -> Result<Self, ApiError> {
        let var = |key| {
            env.var(key)
                .map(|v| v.to_string())
                .map_err(|_| ApiError::config())
        };
        let uri = var("QUACK_URI")?;
        let authority = uri.strip_prefix("quack:").ok_or_else(ApiError::config)?;
        let parsed =
            url::Url::parse(&format!("https://{authority}")).map_err(|_| ApiError::config())?;
        if !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.path() != "/"
            || parsed.query().is_some()
            || parsed.fragment().is_some()
            || authority.contains('/')
        {
            return Err(ApiError::config());
        }
        let local = env
            .var("ALLOW_LOCAL_HTTP")
            .is_ok_and(|v| v.to_string() == "true")
            && parsed.host_str() == Some("127.0.0.1");
        if !local && !authority.ends_with(":443") {
            return Err(ApiError::config());
        }
        let path = var("CATALOG_DATA_PATH")?;
        let policy = PathPolicy::new(&var("CATALOG_BUCKET")?, &var("CATALOG_ID")?, &path)
            .map_err(|_| ApiError::config())?;
        let backend = var("STORAGE_BACKEND")?;
        if !matches!(backend.as_str(), "r2_binding" | "s3") {
            return Err(ApiError::config());
        }
        Ok(Self {
            uri,
            ssl: !local,
            path,
            policy,
            backend,
        })
    }
}
impl Config {
    pub fn load(env: &Env) -> Result<Self, ApiError> {
        let default = if cfg!(feature = "ducklake") {
            "ducklake"
        } else {
            "files"
        };
        let mode = match env
            .var("QUERY_MODE")
            .map(|v| v.to_string())
            .unwrap_or(default.into())
            .as_str()
        {
            "files" => QueryMode::Files,
            "ducklake" if cfg!(feature = "ducklake") => QueryMode::DuckLake,
            _ => return Err(ApiError::config()),
        };
        #[cfg(feature = "ducklake")]
        let catalog = if mode == QueryMode::DuckLake {
            Some(CatalogConfig::load(env)?)
        } else {
            None
        };
        if mode == QueryMode::Files {
            crate::files::validate_config(env)?;
        }
        fn limit(env: &Env, name: &str, max: usize) -> Result<usize, ApiError> {
            match env.var(name) {
                Err(_) => Ok(max),
                Ok(v) => v
                    .to_string()
                    .parse::<usize>()
                    .ok()
                    .filter(|n| *n > 0 && *n <= max)
                    .ok_or_else(ApiError::config),
            }
        }
        Ok(Self {
            mode,
            #[cfg(feature = "ducklake")]
            catalog,
            rows: limit(env, "MAX_RESULT_ROWS", 1000)?,
            bytes: limit(env, "MAX_RESPONSE_BYTES", 1048576)?,
            timeout_ms: limit(env, "QUERY_TIMEOUT_MS", 10000)? as u64,
            memory_bytes: limit(env, "DATAFUSION_MEMORY_BYTES", 50331648)?,
        })
    }
}
