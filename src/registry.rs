use datafusion::{
    error::{DataFusionError, Result},
    execution::object_store::ObjectStoreRegistry,
};
use object_store::ObjectStore;
use std::sync::Arc;
use url::Url;

/// A sealed request-owned registry: no filesystem default, discovery or replacement.
#[derive(Debug)]
pub struct CatalogRegistry {
    pub bucket: String,
    pub store: Arc<dyn ObjectStore>,
}
impl ObjectStoreRegistry for CatalogRegistry {
    fn register_store(&self, _: &Url, _: Arc<dyn ObjectStore>) -> Option<Arc<dyn ObjectStore>> {
        // This API cannot return an error. Retain the configured store unconditionally.
        Some(self.store.clone())
    }
    fn get_store(&self, url: &Url) -> Result<Arc<dyn ObjectStore>> {
        if url.scheme() != "r2"
            || url.host_str() != Some(&self.bucket)
            || url.port().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(DataFusionError::Plan("Unapproved object-store root".into()));
        }
        Ok(self.store.clone())
    }
}

/// Built once from this request's validated sources. No implicit filesystem/network discovery.
#[derive(Debug, Default)]
pub struct FileRegistry(pub std::collections::HashMap<String, Arc<dyn ObjectStore>>);
impl ObjectStoreRegistry for FileRegistry {
    fn register_store(&self, url: &Url, _: Arc<dyn ObjectStore>) -> Option<Arc<dyn ObjectStore>> {
        self.0.get(&file_root(url)).cloned()
    }
    fn get_store(&self, url: &Url) -> Result<Arc<dyn ObjectStore>> {
        self.0
            .get(&file_root(url))
            .cloned()
            .ok_or_else(|| DataFusionError::Plan("Unapproved source".into()))
    }
}

fn file_root(url: &Url) -> String {
    // DataFusion's inference path passes ListingTableUrl, while execution passes ObjectStoreUrl.
    // Both select the same transport. Keys are still independently validated by the adapter.
    if !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return String::new();
    }
    format!("{}://{}/", url.scheme(), url.host_str().unwrap_or(""))
}
