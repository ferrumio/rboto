//! Per-client transport. No process environment is modified.

use aws_smithy_http_client::{
    Builder, Connector,
    proxy::ProxyConfig,
    tls::{self, TlsContext, TrustStore},
};
use aws_smithy_runtime_api::client::http::SharedHttpClient;
use rustls::pki_types::{CertificateDer, pem::PemObject};

use crate::RequestConfig;

const INVALID_PROXY: &str = "proxy_url must be an HTTP(S) URL with a host";

pub(crate) fn proxy(config: &RequestConfig) -> Result<ProxyConfig, &'static str> {
    let mut proxy = match &config.proxy_url {
        Some(url) => ProxyConfig::all(url).map_err(|_| INVALID_PROXY)?,
        None => ProxyConfig::disabled(),
    };
    if let Some(rules) = &config.no_proxy {
        if config.proxy_url.is_none() {
            return Err("no_proxy requires proxy_url");
        }
        proxy = proxy.no_proxy(rules);
    }
    Ok(proxy)
}

pub(crate) fn build(config: &RequestConfig) -> Result<SharedHttpClient, &'static str> {
    let proxy = proxy(config)?;
    let mut trust = TrustStore::default();
    if let Some(path) = &config.ca_bundle {
        let pem = std::fs::read(path).map_err(|_| "Could not read ca_bundle")?;
        validate_certificates(&pem)?;
        trust = trust.with_pem_certificate(pem);
    }
    let context = TlsContext::builder()
        .with_trust_store(trust)
        .build()
        .map_err(|_| "Could not configure TLS trust")?;
    Ok(
        Builder::new().build_with_connector_fn(move |settings, components| {
            let mut builder = Connector::builder()
                .tls_provider(tls::Provider::Rustls(
                    tls::rustls_provider::CryptoMode::AwsLc,
                ))
                .tls_context(context.clone())
                .proxy_config(proxy.clone());
            // Preserve connector deadlines and the SDK's asynchronous timer.
            builder.set_connector_settings(settings.cloned());
            builder.set_sleep_impl(components.and_then(|components| components.sleep_impl()));
            builder.build()
        }),
    )
}

fn validate_certificates(pem: &[u8]) -> Result<(), &'static str> {
    // The connector assumes valid PEM/DER and panics otherwise. Validate with
    // the same TLS parser before giving it application-supplied certificates.
    let mut roots = rustls::RootCertStore::empty();
    for cert in CertificateDer::pem_slice_iter(pem) {
        roots
            .add(cert.map_err(|_| "ca_bundle must contain valid PEM certificates")?)
            .map_err(|_| "ca_bundle must contain valid PEM certificates")?;
    }
    if roots.is_empty() {
        return Err("ca_bundle must contain at least one PEM certificate");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_trust_is_rejected_before_connector_construction() {
        for pem in [
            &b""[..],
            &b"not a certificate"[..],
            &b"-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----"[..],
            &b"-----BEGIN CERTIFICATE-----\nYWJjZA==\n-----END CERTIFICATE-----"[..],
        ] {
            assert!(validate_certificates(pem).is_err());
        }
    }

    #[test]
    fn proxy_errors_do_not_include_secrets() {
        let config = RequestConfig {
            proxy_url: Some("socks5://user:secret@localhost".into()),
            ..Default::default()
        };
        assert_eq!(proxy(&config).unwrap_err(), INVALID_PROXY);
    }
}
