use crate::{bounded_store::BoundedStore, path_policy::PathPolicy};
use object_store::{ClientConfigKey, ClientOptions, RetryConfig, aws::AmazonS3Builder};
use std::{sync::Arc, time::Duration};
use worker::Env;

pub fn s3(env: &Env, policy: PathPolicy) -> Result<Arc<BoundedStore>, Box<dyn std::error::Error>> {
    let endpoint = env.var("S3_ENDPOINT")?.to_string();
    let url = url::Url::parse(&endpoint)?;
    let local = env
        .var("ALLOW_LOCAL_HTTP")
        .is_ok_and(|v| v.to_string() == "true");
    let allow_http = local
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1" | "localhost"));
    if url.scheme() != "https" && !allow_http {
        return Err("S3 endpoint must use HTTPS (or explicit loopback test mode)".into());
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        return Err("Invalid S3 endpoint".into());
    }
    let bucket = env.var("S3_BUCKET")?.to_string();
    // A different endpoint must contain the exact referenced bucket and keys.
    if bucket != policy.bucket {
        return Err("S3 physical bucket must match canonical R2 bucket".into());
    }
    let virtual_host = match env.var("S3_ADDRESSING_STYLE")?.to_string().as_str() {
        "path" => false,
        "virtual" => true,
        _ => return Err("Invalid S3 addressing style".into()),
    };
    let client = ClientOptions::new()
        .with_config(ClientConfigKey::RandomizeAddresses, "false")
        .with_timeout(Duration::from_secs(5))
        .with_connect_timeout(Duration::from_secs(3))
        .with_allow_http(allow_http);
    let mut builder = AmazonS3Builder::new()
        .with_endpoint(endpoint)
        .with_bucket_name(bucket)
        .with_region(env.var("S3_REGION")?.to_string())
        .with_virtual_hosted_style_request(virtual_host)
        .with_access_key_id(env.secret("S3_ACCESS_KEY_ID")?.to_string())
        .with_secret_access_key(env.secret("S3_SECRET_ACCESS_KEY")?.to_string())
        .with_client_options(client)
        .with_retry(RetryConfig {
            max_retries: 1,
            retry_timeout: Duration::from_secs(5),
            ..Default::default()
        });
    if let Ok(token) = env.secret("S3_SESSION_TOKEN") {
        builder = builder.with_token(token.to_string());
    }
    Ok(Arc::new(BoundedStore::new(
        Arc::new(builder.build()?),
        policy,
    )))
}
