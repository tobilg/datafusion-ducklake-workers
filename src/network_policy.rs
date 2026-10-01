//! Pure URL and IP policy, shared with native regression tests.
use std::net::IpAddr;
use url::Url;

pub fn public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => {
            let [a, b, _, _] = ip.octets();
            !(ip.is_private()
                || ip.is_loopback()
                || ip.is_link_local()
                || ip.is_broadcast()
                || ip.is_documentation()
                || ip.is_unspecified()
                || ip.is_multicast()
                || a == 0
                || a >= 240
                || (a == 100 && (64..128).contains(&b))
                || (a == 198 && (b == 18 || b == 19))
                || (a == 192 && b == 0))
        }
        IpAddr::V6(ip) => {
            if let Some(v4) = ip.to_ipv4_mapped() {
                return public_ip(IpAddr::V4(v4));
            }
            let s = ip.segments();
            s[0] & 0xe000 == 0x2000
                && !(s[0] == 0x2001 && (s[1] < 0x200 || s[1] == 0xdb8))
                && s[0] != 0x2002
        }
    }
}
pub fn validate_url(url: &Url, local: bool) -> Result<(), &'static str> {
    let host = url.host_str().ok_or("Missing host")?;
    let loopback = local && matches!(host, "127.0.0.1" | "localhost" | "[::1]");
    if url.scheme() != "https" && !(loopback && url.scheme() == "http") {
        return Err("HTTPS required");
    }
    if !url.username().is_empty() || url.password().is_some() || url.fragment().is_some() {
        return Err("Invalid URL authority or fragment");
    }
    if !loopback {
        match url.host() {
            Some(url::Host::Ipv4(ip)) if !public_ip(ip.into()) => return Err("Non-public address"),
            Some(url::Host::Ipv6(ip)) if !public_ip(ip.into()) => return Err("Non-public address"),
            Some(url::Host::Domain(h))
                if !h.contains('.') || h.ends_with(".localhost") || h.ends_with(".local") =>
            {
                return Err("Non-public host");
            }
            _ => {}
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn public_destinations() {
        for value in [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "::1",
            "::ffff:127.0.0.1",
            "2001:db8::1",
        ] {
            assert!(!public_ip(value.parse().unwrap()), "{value}");
        }
        assert!(public_ip(std::net::Ipv4Addr::new(1, 1, 1, 1).into()));
        assert!(validate_url(&Url::parse("http://127.0.0.1:8900/a").unwrap(), true).is_ok());
        assert!(validate_url(&Url::parse("http://127.0.0.1:8900/a").unwrap(), false).is_err());
        assert!(
            validate_url(
                &Url::parse("https://user:secret@example.com/a").unwrap(),
                false
            )
            .is_err()
        );
    }
}
