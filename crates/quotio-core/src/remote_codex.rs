//! Remote-CPA Codex launch support.
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
    agent_config, codex_launch, codex_session_visibility, dream_skin, AppCore, ManagementCoreError,
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
