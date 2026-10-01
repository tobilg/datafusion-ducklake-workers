//! Public HTTPS egress. The resolver returns only checked addresses to the connector,
//! avoiding a separate validation lookup followed by an unchecked DNS resolution.
pub use crate::network_policy::{public_ip, validate_url};
use reqwest::dns::{Addrs, Name, Resolve, Resolving};
use std::{sync::Arc, time::Duration};

#[derive(Debug)]
struct PublicResolver {
    local: bool,
}
impl Resolve for PublicResolver {
    fn resolve(&self, name: Name) -> Resolving {
        let allow_loopback =
            self.local && matches!(name.as_str(), "localhost" | "127.0.0.1" | "::1");
        Box::pin(async move {
            let addresses: Vec<_> = tokio::net::lookup_host((name.as_str(), 0)).await?.collect();
            if addresses.is_empty()
                || addresses
                    .iter()
                    .any(|a| !public_ip(a.ip()) && !(allow_loopback && a.ip().is_loopback()))
            {
                return Err("Non-public DNS destination".into());
            }
            Ok(Box::new(addresses.into_iter()) as Addrs)
        })
    }
}
pub fn client(local: bool) -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        .pool_max_idle_per_host(0)
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .dns_resolver(Arc::new(PublicResolver { local }))
        .timeout(Duration::from_secs(5))
        .connect_timeout(Duration::from_secs(3))
        .no_gzip()
        .no_brotli()
        .no_deflate()
        .no_zstd()
        .build()
}
