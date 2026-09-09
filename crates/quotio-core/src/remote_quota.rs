//! Remote CPA quota inspection.
//!
//! Unlike the local quota fetchers, this module never reads OAuth credentials
//! from the Windows machine. It asks the remote CLIProxyAPI Management endpoint
//! to execute the upstream request with a selected `auth_index`, so access and
//! refresh tokens remain on the server.

use std::collections::BTreeMap;

use quotio_types::{APICallRequest, AccountQuota, QuotaModelUsage};
use serde::Deserialize;

use crate::management::{ManagementApiClient, ManagementApiError};

const CODEX_USAGE_URL: &str = "https://chatgpt.com/backend-api/wham/usage";
const CODEX_USER_AGENT: &str = "codex_cli_rs/0.76.0 (Windows; x86_64) Quotio-Remote";

#[derive(Debug, Default, Deserialize)]
struct CodexUsageResponse {
    #[serde(default)]
    plan_type: Option<String>,
    #[serde(default)]
    rate_limit: Option<RateLimitInfo>,
}

#[derive(Debug, Default, Deserialize)]
struct RateLimitInfo {
    #[serde(default)]
    allowed: Option<bool>,
    #[serde(default)]
    limit_reached: Option<bool>,
    #[serde(default)]
    primary_window: Option<WindowInfo>,
    #[serde(default)]
    secondary_window: Option<WindowInfo>,
}

#[derive(Debug, Default, Deserialize)]
struct WindowInfo {
    #[serde(default)]
    used_percent: Option<i64>,
    #[serde(default)]
    reset_at: Option<i64>,
}

pub async fn fetch_remote_codex_quotas(
    client: &ManagementApiClient,
) -> Result<Vec<AccountQuota>, ManagementApiError> {
    let files = client.fetch_auth_files_raw().await?;
    let mut quotas = Vec::new();

    for file in files {
        if !is_codex(&file) {
            continue;
        }
        quotas.push(fetch_one_codex(client, &file).await);
    }

    quotas.sort_by(|left, right| left.account_label.cmp(&right.account_label));
    Ok(quotas)
}

async fn fetch_one_codex(client: &ManagementApiClient, file: &serde_json::Value) -> AccountQuota {
    let name = first_string(file, &["name", "id"]).unwrap_or_else(|| "remote-codex".to_string());
    let key = name.trim_end_matches(".json").to_string();
    let label = first_string(file, &["email", "label"])
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| key.clone());
    let plan = first_string(
        file,
        &["plan_type", "planType", "chatgpt_plan_type", "account_type"],
    );
    let disabled = bool_field(file, "disabled") || bool_field(file, "unavailable");

    if disabled {
        return blank_quota(
            label,
            key,
            true,
            plan_status(plan.as_deref(), Some("disabled")),
        );
    }

    let Some(auth_index) = first_string(file, &["auth_index", "authIndex"]) else {
        return blank_quota(
            label,
            key,
            false,
            plan_status(plan.as_deref(), Some("missing auth_index")),
        );
    };

    let account_id = first_string(
        file,
        &[
            "chatgpt_account_id",
            "chatgptAccountId",
            "account_id",
            "accountId",
            "account",
        ],
    )
    .or_else(|| nested_string(file, &["metadata", "chatgpt_account_id"]))
    .or_else(|| nested_string(file, &["metadata", "account_id"]));

    let Some(account_id) = account_id.filter(|value| !value.trim().is_empty()) else {
        return blank_quota(
            label,
            key,
            false,
            plan_status(plan.as_deref(), Some("missing chatgpt_account_id")),
        );
    };

    let mut headers = BTreeMap::new();
    headers.insert("Authorization".to_string(), "Bearer $TOKEN$".to_string());
    headers.insert("Accept".to_string(), "application/json".to_string());
    headers.insert("User-Agent".to_string(), CODEX_USER_AGENT.to_string());
    headers.insert("Chatgpt-Account-Id".to_string(), account_id);

    let request = APICallRequest {
        auth_index: Some(auth_index),
        method: "GET".to_string(),
        url: CODEX_USAGE_URL.to_string(),
        header: Some(headers),
        data: None,
    };

    let response = match client.api_call(&request).await {
        Ok(response) => response,
        Err(error) => {
            return blank_quota(
                label,
                key,
                false,
                plan_status(plan.as_deref(), Some(&format!("management: {error}"))),
            )
        }
    };

    if !(200..300).contains(&response.status_code) {
        let auth_failed = matches!(response.status_code, 401 | 403);
        let forbidden = auth_failed || response.status_code == 429;
        let status = if auth_failed {
            Some("auth_failed".to_string())
        } else {
            plan_status(
                plan.as_deref(),
                Some(&format!("HTTP {}", response.status_code)),
            )
        };
        return blank_quota(label, key, forbidden, status);
    }

    let Some(body) = response.body.as_deref() else {
        return blank_quota(
            label,
            key,
            false,
            plan_status(plan.as_deref(), Some("empty quota response")),
        );
    };
    let usage: CodexUsageResponse = match serde_json::from_str(body) {
        Ok(usage) => usage,
        Err(_) => {
            return blank_quota(
                label,
                key,
                false,
                plan_status(plan.as_deref(), Some("invalid quota response")),
            )
        }
    };

    let resolved_plan = usage.plan_type.as_deref().or(plan.as_deref());
    let rate = usage.rate_limit.unwrap_or_default();
    let session_used = rate
        .primary_window
        .as_ref()
        .and_then(|window| window.used_percent)
        .unwrap_or(0)
        .clamp(0, 100);
    let weekly_used = rate
        .secondary_window
        .as_ref()
        .and_then(|window| window.used_percent)
        .unwrap_or(0)
        .clamp(0, 100);
    let session_reset = rate
        .primary_window
        .as_ref()
        .and_then(|window| window.reset_at);
    let weekly_reset = rate
        .secondary_window
        .as_ref()
        .and_then(|window| window.reset_at);
    let blocked = rate.allowed == Some(false)
        || rate.limit_reached == Some(true)
        || session_used >= 100
        || weekly_used >= 100;

    AccountQuota {
        provider_id: "codex".to_string(),
        account_label: label,
        account_key: key,
        is_forbidden: blocked,
        status_message: plan_status(resolved_plan, None),
        models: vec![
            model_usage("Session", session_used, session_reset),
            model_usage("Weekly", weekly_used, weekly_reset),
        ],
    }
}

fn blank_quota(
    label: String,
    key: String,
    forbidden: bool,
    status: Option<String>,
) -> AccountQuota {
    AccountQuota {
        provider_id: "codex".to_string(),
        account_label: label,
        account_key: key,
        is_forbidden: forbidden,
        status_message: status,
        models: Vec::new(),
    }
}

fn model_usage(name: &str, used_percent: i64, reset_at_unix: Option<i64>) -> QuotaModelUsage {
    let used = used_percent.clamp(0, 100) as f64;
    QuotaModelUsage {
        model: name.to_string(),
        used_percent: used,
        remaining_percent: 100.0 - used,
        reset_at: reset_at_unix.and_then(format_reset_unix),
        reset_at_unix: reset_at_unix.filter(|value| *value > 0),
    }
}

fn format_reset_unix(reset_at: i64) -> Option<String> {
    if reset_at <= 0 {
        return None;
    }
    let now = quotio_platform::current_unix_seconds() as i64;
    let secs = reset_at - now;
    if secs <= 0 {
        return Some("即将刷新".to_string());
    }
    let days = secs / 86_400;
    let hours = (secs % 86_400) / 3_600;
    let minutes = (secs % 3_600) / 60;
    Some(if days > 0 {
        format!("{days}d {hours}h")
    } else if hours > 0 {
        format!("{hours}h {minutes}m")
    } else {
        format!("{}m", minutes.max(1))
    })
}

fn plan_status(plan: Option<&str>, detail: Option<&str>) -> Option<String> {
    let mut parts = Vec::new();
    if let Some(plan) = plan.map(str::trim).filter(|value| !value.is_empty()) {
        parts.push(format!("plan: {plan}"));
    }
    if let Some(detail) = detail.map(str::trim).filter(|value| !value.is_empty()) {
        parts.push(detail.to_string());
    }
    (!parts.is_empty()).then(|| parts.join(" | "))
}

fn is_codex(value: &serde_json::Value) -> bool {
    let provider = first_string(value, &["provider", "type"])
        .unwrap_or_default()
        .to_ascii_lowercase();
    let name = first_string(value, &["name", "id"])
        .unwrap_or_default()
        .to_ascii_lowercase();
    provider == "codex" || name.contains("codex")
}

fn bool_field(value: &serde_json::Value, key: &str) -> bool {
    value
        .get(key)
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
}

fn first_string(value: &serde_json::Value, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| {
        value
            .get(*key)
            .and_then(value_to_string)
            .filter(|text| !text.trim().is_empty())
    })
}

fn nested_string(value: &serde_json::Value, path: &[&str]) -> Option<String> {
    let mut cursor = value;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    value_to_string(cursor).filter(|text| !text.trim().is_empty())
}

fn value_to_string(value: &serde_json::Value) -> Option<String> {
    match value {
        serde_json::Value::String(text) => Some(text.trim().to_string()),
        serde_json::Value::Number(number) => Some(number.to_string()),
        _ => None,
    }
}
