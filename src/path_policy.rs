//! Keys are already decoded by object_store. Never URL-decode them again.
#[derive(Debug, Clone)]
pub struct PathPolicy {
    pub bucket: String,
    pub prefix: String,
}
impl PathPolicy {
    /// File mode authorizes the named bucket through credentials/bindings, not a catalog prefix.
    pub fn files(bucket: &str) -> Result<Self, &'static str> {
        if !(3..=63).contains(&bucket.len())
            || bucket
                .split('.')
                .any(|label| label.is_empty() || label.starts_with('-') || label.ends_with('-'))
            || !bucket
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'.')
        {
            return Err("Invalid bucket name");
        }
        Ok(Self {
            bucket: bucket.into(),
            prefix: String::new(),
        })
    }

    pub fn new(bucket: &str, catalog: &str, data_path: &str) -> Result<Self, &'static str> {
        if !(3..=63).contains(&bucket.len())
            || !bucket
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || bucket.starts_with('-')
            || bucket.ends_with('-')
        {
            return Err("Invalid bucket name");
        }
        if catalog.is_empty()
            || catalog.len() > 128
            || !catalog
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err("Invalid catalog ID");
        }
        let prefix = format!("catalogs/{catalog}/");
        if data_path != format!("r2://{bucket}/{prefix}") {
            return Err("Canonical catalog path mismatch");
        }
        Ok(Self {
            bucket: bucket.into(),
            prefix,
        })
    }
    pub fn key(&self, key: &str) -> Result<(), &'static str> {
        if !key.starts_with(&self.prefix)
            || key.len() <= self.prefix.len()
            || key.len() > 1024
            || key.chars().any(|c| c.is_control() || c == '\\')
            || key
                .split('/')
                .any(|s| s.is_empty() || s == "." || s == "..")
        {
            return Err("Object key outside catalog or invalid");
        }
        Ok(())
    }
    pub fn list_prefix(&self, prefix: Option<&str>) -> Result<String, &'static str> {
        match prefix {
            None | Some("") => Ok(self.prefix.clone()),
            Some(p) if p.trim_end_matches('/') == self.prefix.trim_end_matches('/') => {
                Ok(self.prefix.clone())
            }
            Some(p) => {
                self.key(p.trim_end_matches('/'))?;
                Ok(format!("{}/", p.trim_end_matches('/')))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_path_and_keys() {
        let p = PathPolicy::new("bucket-a", "a", "r2://bucket-a/catalogs/a/").unwrap();
        for key in [
            "catalogs/a/file space.parquet",
            "catalogs/a/Grüße%2F.parquet",
            "catalogs/a/100%.parquet",
        ] {
            assert!(p.key(key).is_ok());
        }
        for key in [
            "catalogs/ab/x",
            "catalogs/a/../b/x",
            "catalogs/a//x",
            "/catalogs/a/x",
            "file:///x",
            "catalogs/a/./x",
            "catalogs/a/x\\y",
        ] {
            assert!(p.key(key).is_err(), "{key}");
        }
        assert_eq!(p.list_prefix(None).unwrap(), "catalogs/a/");
        assert!(PathPolicy::new("bucket-a", "a", "s3://bucket-a/catalogs/a/").is_err());
        assert!(PathPolicy::new("bucket-b", "a", "r2://bucket-a/catalogs/a/").is_err());
    }
}
