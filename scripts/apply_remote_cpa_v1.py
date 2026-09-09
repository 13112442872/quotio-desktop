from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {rel}")


def replace_once(rel: str, old: str, new: str, marker: str | None = None) -> None:
    text = read(rel)
    if marker and marker in text:
        print(f"skip {rel}: marker already present: {marker}")
        return
    if old not in text:
        raise RuntimeError(f"anchor not found in {rel}: {old[:120]!r}")
    write(rel, text.replace(old, new, 1))


def regex_once(rel: str, pattern: str, replacement: str, marker: str | None = None) -> None:
    text = read(rel)
    if marker and marker in text:
        print(f"skip {rel}: marker already present: {marker}")
        return
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"regex anchor matched {count} times in {rel}: {pattern[:120]!r}")
    write(rel, new_text)


# ---------------------------------------------------------------------------
# 1) Core modules + remote management raw auth-file access
# ---------------------------------------------------------------------------
replace_once(
    "crates/quotio-core/src/lib.rs",
    "pub mod quota;\n",
    "pub mod quota;\npub mod remote_codex;\npub mod remote_quota;\n",
    marker="pub mod remote_quota;",
)

replace_once(
    "crates/quotio-core/src/management.rs",
    '''    pub async fn fetch_auth_files(&self) -> Result<Vec<AuthFile>, ManagementApiError> {\n        let body = self.request("GET", "/auth-files", None)?;\n        decode_auth_files_response(&body)\n    }\n''',
    '''    pub async fn fetch_auth_files(&self) -> Result<Vec<AuthFile>, ManagementApiError> {\n        let body = self.request("GET", "/auth-files", None)?;\n        decode_auth_files_response(&body)\n    }\n\n    /// Fetch the raw `/auth-files` entries without discarding provider-specific\n    /// metadata. Remote Codex quota inspection needs `chatgpt_account_id` and\n    /// `plan_type`, which are intentionally not part of the generic `AuthFile`.\n    pub async fn fetch_auth_files_raw(\n        &self,\n    ) -> Result<Vec<serde_json::Value>, ManagementApiError> {\n        let body = self.request("GET", "/auth-files", None)?;\n        let value: serde_json::Value = serde_json::from_str(&body)\n            .map_err(|error| ManagementApiError::Json(error.to_string()))?;\n        match value {\n            serde_json::Value::Array(items) => Ok(items),\n            serde_json::Value::Object(mut object) => match object.remove("files") {\n                Some(serde_json::Value::Array(items)) => Ok(items),\n                Some(_) => Err(ManagementApiError::Json(\n                    "auth-files.files 不是数组".to_string(),\n                )),\n                None => Ok(Vec::new()),\n            },\n            _ => Err(ManagementApiError::Json(\n                "auth-files 响应格式无效".to_string(),\n            )),\n        }\n    }\n''',
    marker="fetch_auth_files_raw",
)

# ---------------------------------------------------------------------------
# 2) Remote Codex launch: in Remote connection mode bypass the local CPA/account
# ---------------------------------------------------------------------------
replace_once(
    "crates/quotio-core/src/lib.rs",
    '''            .ok_or_else(|| {\n                ManagementCoreError::Unavailable("找不到该启动方案，请刷新后重试".to_string())\n            })?;\n\n        // 已经在跑：同一套幂等返回；不同套（或只有旧备份）先完整清理再起新的。\n''',
    '''            .ok_or_else(|| {\n                ManagementCoreError::Unavailable("找不到该启动方案，请刷新后重试".to_string())\n            })?;\n\n        // Remote Proxy 模式不启动本地 CLIProxyAPI，也不要求/注入本地 CPA 账号。\n        // 只临时改写 Codex config.toml 指向远程 CPA，然后沿用现有启动/停止恢复事务。\n        if self.is_remote_connection() {\n            return self.codex_start_remote_unlocked(profile);\n        }\n\n        // 已经在跑：同一套幂等返回；不同套（或只有旧备份）先完整清理再起新的。\n''',
    marker="codex_start_remote_unlocked(profile)",
)

regex_once(
    "crates/quotio-core/src/lib.rs",
    r'''    pub fn codex_model_fetch_params\(&self\) -> \(String, String\) \{.*?\n    \}\n\n    pub fn list_agent_backups''',
    '''    pub fn codex_model_fetch_params(&self) -> (String, String) {\n        let endpoint = if self.is_remote_connection() {\n            self.remote_api_base_url()\n                .unwrap_or_else(|| self.proxy.state.endpoint.clone())\n        } else {\n            self.proxy.state.endpoint.clone()\n        };\n        let api_key = self\n            .management_snapshot\n            .api_keys\n            .first()\n            .cloned()\n            .unwrap_or_default();\n        (endpoint, api_key)\n    }\n\n    pub fn list_agent_backups''',
    marker="self.remote_api_base_url()",
)

# ---------------------------------------------------------------------------
# 3) Tauri quota refresh: Remote mode uses CPA Management /api-call
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src-tauri/src/lib.rs",
    '''async fn refresh_quotas(\n    app: AppHandle,\n    state: State<'_, DesktopState>,\n) -> Result<AppState, String> {\n''',
    '''async fn refresh_quotas(\n    app: AppHandle,\n    state: State<'_, DesktopState>,\n) -> Result<AppState, String> {\n    // Remote Proxy: query the accounts that live on the remote CPA via its\n    // Management API. No local ~/.cli-proxy-api files or local CPA process are\n    // involved, and OAuth tokens stay on the remote server.\n    let remote_client = {\n        let mut core = lock_core(&state.core);\n        if core.is_remote_connection() {\n            Some(core.management_client().map_err(|error| error.to_string())?)\n        } else {\n            None\n        }\n    };\n    if let Some(client) = remote_client {\n        let quotas = quotio_core::remote_quota::fetch_remote_codex_quotas(&client)\n            .await\n            .map_err(|error| format!("远程额度刷新失败：{error}"))?;\n        for account in &quotas {\n            let _ = app.emit("quota-account", account);\n        }\n        let mut core = lock_core(&state.core);\n        return Ok(core.set_quotas(quotas));\n    }\n\n''',
    marker="Remote Proxy: query the accounts that live on the remote CPA",
)

# The local smart-scheduler background refresh must never overwrite the remote
# quota cards with an empty local scan while Remote mode is selected.
replace_once(
    "apps/desktop/src-tauri/src/lib.rs",
    '''fn refresh_quotas_and_reschedule(app: &AppHandle) {\n    let proxy_url = app.try_state::<DesktopState>().and_then(|state| {\n''',
    '''fn refresh_quotas_and_reschedule(app: &AppHandle) {\n    let remote = app\n        .try_state::<DesktopState>()\n        .and_then(|state| state.core.lock().ok().map(|core| core.is_remote_connection()))\n        .unwrap_or(false);\n    if remote {\n        return;\n    }\n    let proxy_url = app.try_state::<DesktopState>().and_then(|state| {\n''',
    marker="let remote = app\n        .try_state::<DesktopState>()",
)

# ---------------------------------------------------------------------------
# 4) Agents UI: in Remote mode a new Codex scheme automatically points at the
#    remote CPA, pre-fills its first API key, and no longer requires a local
#    bound Codex account.
# ---------------------------------------------------------------------------
replace_once(
    "apps/desktop/src/components/sections/AgentsScreen.tsx",
    '''function newProfileId(): string {\n''',
    '''function remoteApiBase(appState: AppState): string {\n  let url = (appState.settings.remote_endpoint_url ?? "").trim().replace(/\\/+$/, "");\n  for (const suffix of ["/v0/management", "/v0", "/v1"]) {\n    if (url.toLowerCase().endsWith(suffix)) {\n      url = url.slice(0, -suffix.length).replace(/\\/+$/, "");\n      break;\n    }\n  }\n  return url;\n}\n\nfunction newProfileId(): string {\n''',
    marker="function remoteApiBase(appState: AppState)",
)

replace_once(
    "apps/desktop/src/components/sections/AgentsScreen.tsx",
    '''      bound_account: "",\n      proxy_url: appState.proxy.endpoint || "",\n      model: "",\n      reasoning: "high",\n      api_key: "",\n''',
    '''      bound_account: "",\n      proxy_url:\n        appState.settings.connection_mode === "remote"\n          ? remoteApiBase(appState)\n          : appState.proxy.endpoint || "",\n      model: "",\n      reasoning: "high",\n      api_key:\n        appState.settings.connection_mode === "remote"\n          ? (appState.management.api_keys?.[0] ?? "")\n          : "",\n''',
    marker='appState.settings.connection_mode === "remote"\n          ? remoteApiBase(appState)',
)

replace_once(
    "apps/desktop/src/components/sections/AgentsScreen.tsx",
    '''    if (!profileDraft.bound_account.trim()) {\n      setLaunchMsg({ ok: false, text: t("agents.launch.needAccount", "请选择要绑定的 Codex 账号") });\n      return;\n    }\n''',
    '''    if (appState.settings.connection_mode !== "remote" && !profileDraft.bound_account.trim()) {\n      setLaunchMsg({ ok: false, text: t("agents.launch.needAccount", "请选择要绑定的 Codex 账号") });\n      return;\n    }\n''',
    marker='appState.settings.connection_mode !== "remote" && !profileDraft.bound_account.trim()',
)

replace_once(
    "apps/desktop/src/components/sections/AgentsScreen.tsx",
    '''  const codexKeyIssue = (profile: CodexLaunchProfile): string | null => {\n    const key = profile.api_key.trim();\n''',
    '''  const codexKeyIssue = (profile: CodexLaunchProfile): string | null => {\n    if (appState.settings.connection_mode === "remote") return null;\n    const key = profile.api_key.trim();\n''',
    marker='if (appState.settings.connection_mode === "remote") return null;',
)

replace_once(
    "apps/desktop/src/components/sections/AgentsScreen.tsx",
    '''                    <div className="field-value">{accountEmail(profile.bound_account)}</div>\n''',
    '''                    <div className="field-value">\n                      {appState.settings.connection_mode === "remote"\n                        ? t("settings.remoteProxy", "远程 CPA")\n                        : accountEmail(profile.bound_account)}\n                    </div>\n''',
    marker='? t("settings.remoteProxy", "远程 CPA")',
)

# Give the account picker a clear Remote-mode hint. It remains visible for layout
# compatibility, but it is no longer required by validation/backend.
replace_once(
    "apps/desktop/src/components/sections/AgentsScreen.tsx",
    '''                    { value: "", label: t("agents.launch.accountPick", "选择一个 Codex 账号") },\n''',
    '''                    {\n                      value: "",\n                      label:\n                        appState.settings.connection_mode === "remote"\n                          ? t("agents.launch.remoteNoAccount", "远程 CPA（无需绑定本地账号）")\n                          : t("agents.launch.accountPick", "选择一个 Codex 账号"),\n                    },\n''',
    marker='t("agents.launch.remoteNoAccount", "远程 CPA（无需绑定本地账号）")',
)

# ---------------------------------------------------------------------------
# 5) New Rust modules
# ---------------------------------------------------------------------------
REMOTE_QUOTA = r'''//! Remote CPA quota inspection.
//!
//! Unlike the local quota fetchers, this module never reads OAuth credentials
//! from the Windows machine. It asks the remote CLIProxyAPI Management endpoint
//! to execute the upstream request with a selected `auth_index`, so access and
//! refresh tokens remain on the server.

use std::collections::BTreeMap;

use quotio_types::{AccountQuota, APICallRequest, QuotaModelUsage};
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
    let plan = first_string(file, &["plan_type", "planType", "chatgpt_plan_type", "account_type"]);
    let disabled = bool_field(file, "disabled") || bool_field(file, "unavailable");

    if disabled {
        return blank_quota(label, key, true, plan_status(plan.as_deref(), Some("disabled")));
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
    let session_reset = rate.primary_window.as_ref().and_then(|window| window.reset_at);
    let weekly_reset = rate.secondary_window.as_ref().and_then(|window| window.reset_at);
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
'''

REMOTE_CODEX = r'''//! Remote-CPA Codex launch support.
//!
//! This path intentionally does not start a local CLIProxyAPI and does not read
//! or inject any account from `~/.cli-proxy-api`. The user's existing Codex
//! login (`~/.codex/auth.json`) is left untouched during startup; only the
//! provider block in `config.toml` is temporarily pointed at the remote CPA.

use std::{collections::BTreeMap, thread, time::Duration};

use quotio_types::{
    AgentConfigMode, AgentConfigStorageOption, AgentConfigurationRequest, AgentSetupMode,
    CodexLaunchProfile, ConnectionMode, ModelSlot,
};

use crate::{
    agent_config, codex_launch, codex_session_visibility, dream_skin, AppCore,
    ManagementCoreError,
};

pub(crate) fn normalize_remote_api_base(value: &str) -> String {
    let mut url = value.trim().trim_end_matches('/').to_string();
    for suffix in ["/v0/management", "/v0", "/v1"] {
        if url.to_ascii_lowercase().ends_with(suffix) {
            let new_len = url.len().saturating_sub(suffix.len());
            url.truncate(new_len);
            url = url.trim_end_matches('/').to_string();
            break;
        }
    }
    url
}

impl AppCore {
    pub fn is_remote_connection(&self) -> bool {
        matches!(self.settings.connection_mode, ConnectionMode::Remote)
    }

    pub fn remote_api_base_url(&self) -> Option<String> {
        self.settings
            .remote_endpoint_url
            .as_deref()
            .map(normalize_remote_api_base)
            .filter(|url| !url.is_empty())
    }

    pub(crate) fn codex_start_remote_unlocked(
        &mut self,
        profile: CodexLaunchProfile,
    ) -> Result<String, ManagementCoreError> {
        let profile_id = profile.id.trim().to_string();
        if profile_id.is_empty() {
            return Err(ManagementCoreError::Unavailable(
                "远程 Codex 方案 id 为空，请重新保存方案".to_string(),
            ));
        }

        if self.codex_active() {
            if crate::codex_runtime_satisfies_start(
                self.codex_session.as_ref(),
                self.codex_cleanup_pending,
                self.codex_active_profile_id.as_deref(),
                &profile_id,
            ) {
                return Ok("该远程方案已在运行".to_string());
            }
            self.codex_stop_unlocked()?;
        }

        let mode = if profile.launch_mode.trim().is_empty() {
            "app".to_string()
        } else {
            profile.launch_mode.trim().to_string()
        };
        let proxy_url = if profile.proxy_url.trim().is_empty() {
            self.remote_api_base_url().ok_or_else(|| {
                ManagementCoreError::Unavailable("远程 CPA 地址未配置".to_string())
            })?
        } else {
            normalize_remote_api_base(&profile.proxy_url)
        };
        if proxy_url.is_empty() {
            return Err(ManagementCoreError::Unavailable(
                "远程 CPA 地址无效".to_string(),
            ));
        }

        let api_key = if !profile.api_key.trim().is_empty() {
            profile.api_key.trim().to_string()
        } else {
            self.management_snapshot
                .api_keys
                .first()
                .cloned()
                .unwrap_or_default()
        };
        if api_key.is_empty() {
            return Err(ManagementCoreError::Unavailable(
                "未获取到远程 CPA API Key：请先刷新远程管理状态，或在方案中填写 API Key"
                    .to_string(),
            ));
        }

        let launch_outcome = (|| -> Result<String, ManagementCoreError> {
            if codex_launch::codex_app_process_running() {
                codex_launch::close_codex_app();
                thread::sleep(Duration::from_millis(500));
            }
            dream_skin::cleanup_if_present().map_err(|error| {
                ManagementCoreError::Unavailable(format!("清理旧 Dream Skin 会话失败：{error}"))
            })?;

            // Reuse the existing Quotio launch transaction so Stop / app-exit can
            // restore the user's original config.toml. account_key is deliberately
            // empty because Remote mode does not own a local CPA account.
            codex_launch::write_launch_backup_for_session_unlocked(
                codex_launch::CodexLaunchSessionState {
                    profile_id: profile_id.clone(),
                    account_key: String::new(),
                    launch_mode: mode.clone(),
                    pid: None,
                    phase: codex_launch::CodexLaunchPhase::Prepared,
                },
            )
            .map_err(ManagementCoreError::Unavailable)?;

            let mut model_slots = BTreeMap::new();
            if !profile.model.trim().is_empty() {
                model_slots.insert(ModelSlot::Sonnet, profile.model.trim().to_string());
            }
            let request = AgentConfigurationRequest {
                agent_id: "codex".to_string(),
                mode: AgentConfigMode::Automatic,
                setup_mode: AgentSetupMode::Proxy,
                storage_option: AgentConfigStorageOption::Json,
                proxy_url: proxy_url.clone(),
                api_key: api_key.clone(),
                model_slots,
                use_oauth: false,
                available_models: Vec::new(),
                reasoning_effort: profile.reasoning.clone(),
            };
            agent_config::write_codex_proxy_config_no_backup_unlocked(&request)?;

            // Keep the current auth.json exactly as-is. Existing local Codex login
            // is only used to let the desktop app open; inference goes to the
            // remote CPA provider written above.
            let _ = codex_session_visibility::repair_session_visibility_in_default_dir_no_backup_unlocked();

            let use_dream_skin =
                mode == "app" && profile.dream_skin_enabled && cfg!(target_os = "windows");
            let pid = if mode == "cli" {
                codex_launch::launch_codex_cli().map_err(ManagementCoreError::Unavailable)?
            } else {
                let exe = self.resolve_codex_app_path()?;
                if use_dream_skin {
                    dream_skin::validate_codex_target(&exe)
                        .map_err(ManagementCoreError::Unavailable)?;
                    if let Err(error) = dream_skin::start(&profile.dream_skin_theme_id) {
                        codex_launch::close_codex_app();
                        thread::sleep(Duration::from_millis(400));
                        let _ = dream_skin::cleanup_if_present();
                        return Err(ManagementCoreError::Unavailable(error));
                    }
                    None
                } else {
                    codex_launch::launch_codex_app(&exe)
                        .map_err(ManagementCoreError::Unavailable)?
                }
            };

            if let Err(error) = codex_launch::mark_launch_session_running_unlocked(pid) {
                if let Some(pid) = pid {
                    codex_launch::kill_process(pid);
                }
                codex_launch::close_codex_app();
                thread::sleep(Duration::from_millis(400));
                return Err(ManagementCoreError::Unavailable(format!(
                    "记录 Codex 启动进程失败：{error}"
                )));
            }

            self.codex_session = Some(codex_launch::CodexSession::new(pid, &mode));
            self.codex_session_generation = self.codex_session_generation.wrapping_add(1);
            self.codex_active_profile_id = Some(profile_id.clone());
            self.codex_active_account_key = None;
            self.codex_cleanup_pending = false;

            Ok(if mode == "cli" {
                format!("已在终端启动 Codex → 远程 CPA {proxy_url}")
            } else if use_dream_skin {
                format!("已启动 Codex（Dream Skin）→ 远程 CPA {proxy_url}")
            } else {
                format!("已启动 Codex → 远程 CPA {proxy_url}")
            })
        })();

        match launch_outcome {
            Ok(message) => Ok(message),
            Err(error) => {
                let cleanup = crate::cleanup_managed_codex_runtime_unlocked()
                    .map_err(ManagementCoreError::Unavailable);
                self.codex_session = None;
                self.codex_session_generation = self.codex_session_generation.wrapping_add(1);
                match cleanup {
                    Ok(_) => {
                        self.codex_active_profile_id = None;
                        self.codex_active_account_key = None;
                        self.codex_cleanup_pending = false;
                        Err(error)
                    }
                    Err(cleanup_error) => {
                        self.codex_active_profile_id = Some(profile_id);
                        self.codex_active_account_key = None;
                        self.codex_cleanup_pending = true;
                        Err(ManagementCoreError::Unavailable(format!(
                            "{error}\n启动失败后的 Codex 状态清理也失败：{cleanup_error}"
                        )))
                    }
                }
            }
        }
    }
}
'''

write("crates/quotio-core/src/remote_quota.rs", REMOTE_QUOTA)
write("crates/quotio-core/src/remote_codex.rs", REMOTE_CODEX)

print("Remote CPA V1 patch applied successfully.")
