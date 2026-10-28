//! Shared client configuration. Providers remain owned and refreshed by the SDK.

use aws_config::{BehaviorVersion, SdkConfig};
use aws_credential_types::Credentials;
use pyo3::prelude::*;
use std::time::Duration;

use crate::raise_core_error;

#[derive(Clone, Default, FromPyObject)]
pub struct RequestConfig {
    pub access_key_id: Option<String>,
    pub secret_access_key: Option<String>,
    pub session_token: Option<String>,
    pub role_arn: Option<String>,
    pub role_session_name: Option<String>,
    pub external_id: Option<String>,
    pub connect_timeout: Option<f64>,
    pub read_timeout: Option<f64>,
    pub operation_timeout: Option<f64>,
    pub attempt_timeout: Option<f64>,
    pub max_attempts: Option<u32>,
    pub retry_mode: Option<String>,
}

impl RequestConfig {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.access_key_id.is_some() != self.secret_access_key.is_some() {
            return Err("access_key_id and secret_access_key must be supplied together");
        }
        if self.session_token.is_some() && self.access_key_id.is_none() {
            return Err("session_token requires explicit credentials");
        }
        if self.role_arn.is_none()
            && (self.role_session_name.is_some() || self.external_id.is_some())
        {
            return Err("role_session_name and external_id require role_arn");
        }
        for value in [
            &self.access_key_id,
            &self.secret_access_key,
            &self.session_token,
            &self.role_arn,
            &self.role_session_name,
            &self.external_id,
        ]
        .into_iter()
        .flatten()
        {
            if value.trim().is_empty() {
                return Err("credential and role settings must be nonempty strings");
            }
        }
        for value in [
            self.connect_timeout,
            self.read_timeout,
            self.operation_timeout,
            self.attempt_timeout,
        ]
        .into_iter()
        .flatten()
        {
            if !value.is_finite() || value <= 0.0 || value > u32::MAX as f64 {
                return Err("timeouts must be finite and positive (at most 2**32 - 1)");
            }
        }
        if self.max_attempts == Some(0) {
            return Err("max_attempts must include at least the initial request");
        }
        if !matches!(
            self.retry_mode.as_deref(),
            None | Some("standard" | "adaptive")
        ) {
            return Err("retry_mode must be 'standard' or 'adaptive'");
        }
        Ok(())
    }

    pub async fn load(&self, region: Option<&str>, profile: Option<&str>) -> PyResult<SdkConfig> {
        let mut loader = aws_config::defaults(BehaviorVersion::latest());
        if let Some(region) = region {
            loader = loader.region(aws_config::Region::new(region.to_owned()));
        }
        if let Some(profile) = profile {
            loader = loader.profile_name(profile);
        }
        if let (Some(key), Some(secret)) = (&self.access_key_id, &self.secret_access_key) {
            loader = loader.credentials_provider(Credentials::new(
                key,
                secret,
                self.session_token.clone(),
                None,
                "rboto-explicit",
            ));
        }
        // Loading the base first preserves profile/environment settings when an
        // application overrides only one timeout or retry field.
        let base = loader.load().await;
        let mut timeouts = base
            .timeout_config()
            .cloned()
            .unwrap_or_else(|| aws_config::timeout::TimeoutConfig::builder().build())
            .to_builder();
        if let Some(value) = self.connect_timeout {
            timeouts.set_connect_timeout(Some(Duration::from_secs_f64(value)));
        }
        if let Some(value) = self.read_timeout {
            timeouts.set_read_timeout(Some(Duration::from_secs_f64(value)));
        }
        if let Some(value) = self.operation_timeout {
            timeouts.set_operation_timeout(Some(Duration::from_secs_f64(value)));
        }
        if let Some(value) = self.attempt_timeout {
            timeouts.set_operation_attempt_timeout(Some(Duration::from_secs_f64(value)));
        }
        let mut retries = base
            .retry_config()
            .cloned()
            .unwrap_or_else(aws_config::retry::RetryConfig::standard);
        if let Some(value) = self.max_attempts {
            retries = retries.with_max_attempts(value);
        }
        if let Some(mode) = &self.retry_mode {
            retries = retries.with_retry_mode(if mode == "adaptive" {
                aws_config::retry::RetryMode::Adaptive
            } else {
                aws_config::retry::RetryMode::Standard
            });
        }
        let base = base
            .into_builder()
            .timeout_config(timeouts.build())
            .retry_config(retries)
            .build();
        if base.region().is_none() {
            return Err(Python::attach(|py| {
                raise_core_error(
                    py,
                    "ConfigurationError",
                    "No region configured; pass region or configure a profile/environment region",
                )
            }));
        }
        if let Some(role) = &self.role_arn {
            // The requested profile/static credentials, region, transport and
            // request limits are also used for the STS call.
            let mut provider = aws_config::sts::AssumeRoleProvider::builder(role)
                .configure(&base)
                .session_name(self.role_session_name.as_deref().unwrap_or("rboto"));
            if let Some(external_id) = &self.external_id {
                provider = provider.external_id(external_id);
            }
            let provider = provider.build().await;
            return Ok(base
                .into_builder()
                .credentials_provider(
                    aws_credential_types::provider::SharedCredentialsProvider::new(provider),
                )
                .build());
        }
        Ok(base)
    }
}
