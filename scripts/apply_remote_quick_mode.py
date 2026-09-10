from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"patch marker not found: {label}")
    return text.replace(old, new, 1)


agents_path = Path("apps/desktop/src/components/sections/AgentsScreen.tsx")
css_path = Path("apps/desktop/src/components/sections/agents.css")

text = agents_path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''function remoteApiBase(appState: AppState): string {
  let url = (appState.settings.remote_endpoint_url ?? "").trim().replace(/\\/+$/, "");
  for (const suffix of ["/v0/management", "/v0", "/v1"]) {
    if (url.toLowerCase().endsWith(suffix)) {
      url = url.slice(0, -suffix.length).replace(/\\/+$/, "");
      break;
    }
  }
  return url;
}

function newProfileId(): string {''',
    '''function remoteApiBase(appState: AppState): string {
  let url = (appState.settings.remote_endpoint_url ?? "").trim().replace(/\\/+$/, "");
  for (const suffix of ["/v0/management", "/v0", "/v1"]) {
    if (url.toLowerCase().endsWith(suffix)) {
      url = url.slice(0, -suffix.length).replace(/\\/+$/, "");
      break;
    }
  }
  return url;
}

const QUICK_REMOTE_PROFILE_ID = "__quotio_remote_quick__";

function newProfileId(): string {''',
    "quick profile id",
)

text = replace_once(
    text,
    '''  const codexProfiles = useMemo<CodexLaunchProfile[]>(
    () => appState.settings.codex_profiles ?? [],
    [appState.settings.codex_profiles],
  );
  const isWindows = appState.platform.os === "windows";''',
    '''  const codexProfiles = useMemo<CodexLaunchProfile[]>(
    () => appState.settings.codex_profiles ?? [],
    [appState.settings.codex_profiles],
  );
  const remoteMode = appState.settings.connection_mode === "remote";
  const advancedCodexProfiles = useMemo(
    () => codexProfiles.filter((profile) => profile.id !== QUICK_REMOTE_PROFILE_ID),
    [codexProfiles],
  );
  const isWindows = appState.platform.os === "windows";''',
    "remote mode + advanced profiles",
)

text = replace_once(
    text,
    '''  const [codexAccounts, setCodexAccounts] = useState<CodexAccountRef[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<string | null>(null);
  const [proxyModels, setProxyModels] = useState<string[]>([]);
  const [launchBusy, setLaunchBusy] = useState(false); // 停止 / 全局忙''',
    '''  const [codexAccounts, setCodexAccounts] = useState<CodexAccountRef[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<string | null>(null);
  const [proxyModels, setProxyModels] = useState<string[]>([]);
  const [quickApiKey, setQuickApiKey] = useState(appState.settings.codex_api_key || "");
  const [quickModel, setQuickModel] = useState(appState.settings.codex_model || "");
  const [quickReasoning, setQuickReasoning] = useState(appState.settings.codex_reasoning || "high");
  const [quickLaunchMode, setQuickLaunchMode] = useState(appState.settings.codex_launch_mode || "app");
  const [quickReasoningLevels, setQuickReasoningLevels] = useState<string[]>([]);
  const [launchBusy, setLaunchBusy] = useState(false); // 停止 / 全局忙''',
    "quick mode state",
)

text = replace_once(
    text,
    '''  const codexModelList = proxyModels.length > 0 ? proxyModels : CODEX_MODELS;''',
    '''  useEffect(() => {
    if (!remoteMode || quickModel.trim() || proxyModels.length === 0) return;
    const preferred =
      proxyModels.find((model) => model === "gpt-5.6-sol") ??
      proxyModels.find((model) => /gpt-5|codex/i.test(model)) ??
      proxyModels[0];
    if (preferred) setQuickModel(preferred);
  }, [remoteMode, proxyModels, quickModel]);

  useEffect(() => {
    if (!remoteMode) {
      setQuickReasoningLevels([]);
      return;
    }
    const model = quickModel.trim();
    if (!model) {
      setQuickReasoningLevels([]);
      return;
    }
    let stale = false;
    invoke<unknown>("fetch_codex_reasoning_levels", { model })
      .then((payload) => {
        if (stale) return;
        const levels = normalizeCodexReasoningLevels(payload);
        setQuickReasoningLevels(levels ?? []);
      })
      .catch((err) => {
        if (!stale) {
          console.warn("[AgentsScreen] quick fetch_codex_reasoning_levels:", err);
          setQuickReasoningLevels([]);
        }
      });
    return () => {
      stale = true;
    };
  }, [remoteMode, quickModel]);

  const codexModelList = proxyModels.length > 0 ? proxyModels : CODEX_MODELS;''',
    "quick defaults and reasoning fetch",
)

text = replace_once(
    text,
    '''  const reasoningLabel = (effort: string) =>
    REASONING_I18N[effort] ? t(REASONING_I18N[effort]) : effort;

  // 当前方案所选模型支持的档位。''',
    '''  const reasoningLabel = (effort: string) =>
    REASONING_I18N[effort] ? t(REASONING_I18N[effort]) : effort;
  const quickReasoningOptions = useMemo(() => {
    const levels = quickReasoningLevels.length > 0 ? quickReasoningLevels : CODEX_REASONING_FALLBACK;
    const all = quickReasoning.trim() && !levels.includes(quickReasoning.trim())
      ? [...levels, quickReasoning.trim()]
      : levels;
    return all.map((effort) => ({
      value: effort,
      label: REASONING_I18N[effort] ? t(REASONING_I18N[effort]) : effort,
    }));
  }, [quickReasoningLevels, quickReasoning, t]);

  // 当前方案所选模型支持的档位。''',
    "quick reasoning options",
)

text = replace_once(
    text,
    '''  async function submitConfiguration(status: AgentStatus) {''',
    '''  async function startQuickRemote() {
    if (!remoteMode) return;
    const base = remoteApiBase(appState);
    if (!base) {
      setLaunchMsg({ ok: false, text: "请先在设置中填写远程 CPA 地址并保存连接" });
      return;
    }

    const quickProfile: CodexLaunchProfile = {
      id: QUICK_REMOTE_PROFILE_ID,
      name: "远程 CPA 快捷启动",
      launch_mode: quickLaunchMode || "app",
      dream_skin_enabled: false,
      dream_skin_theme_id: "dream",
      bound_account: "",
      proxy_url: base,
      model: quickModel.trim(),
      reasoning: quickReasoning.trim() || "high",
      api_key: quickApiKey.trim(),
    };

    setLaunchBusy(true);
    setStartingId(QUICK_REMOTE_PROFILE_ID);
    setLaunchMsg(null);
    try {
      // 同一快捷方案正在运行时也必须先停掉，否则后端会判断“同方案已运行”而
      // 跳过重新写 config.toml，导致刚切换的 Key / 模型 / reasoning 不生效。
      if (activeProfileId) {
        await invoke<string>("codex_stop");
        setActiveProfileId(null);
      }

      // 取后端最新 settings，避免用当前 React props 的旧快照覆盖刚刚在其它页面
      // 修改的设置。快捷方案用固定 id 隐藏保存，既能跨重启记住选择，又不污染高级方案列表。
      const live = await invoke<AppState>("get_app_state");
      const nextProfiles = (live.settings.codex_profiles ?? []).filter(
        (profile) => profile.id !== QUICK_REMOTE_PROFILE_ID,
      );
      const nextSettings: AppSettings = {
        ...live.settings,
        codex_profiles: [...nextProfiles, quickProfile],
        codex_api_key: quickApiKey.trim(),
        codex_model: quickModel.trim(),
        codex_reasoning: quickReasoning.trim() || "high",
        codex_launch_mode: quickLaunchMode || "app",
        // null = 不覆盖安全存储中的远程 Management Key；与页面其它保存入口保持一致。
        remote_management_key: null,
      };
      const saved = await invoke<AppState>("save_settings", {
        settings: nextSettings,
        allowClearCodexProfiles: false,
      });
      if ("__TAURI_INTERNALS__" in window) {
        const { emit } = await import("@tauri-apps/api/event");
        await emit("settings-changed", saved.settings);
      }

      const message = await invoke<string>("codex_start", { profileId: QUICK_REMOTE_PROFILE_ID });
      setActiveProfileId(QUICK_REMOTE_PROFILE_ID);
      setLaunchMsg({ ok: true, text: message });
    } catch (error) {
      setLaunchMsg({ ok: false, text: String(error) });
    } finally {
      setStartingId(null);
      setLaunchBusy(false);
    }
  }

  async function submitConfiguration(status: AgentStatus) {''',
    "quick start function",
)

text = replace_once(
    text,
    '''  function codexLaunchPanel() {
    const running = (id: string) => id === activeProfileId;
    return (
      <section className="panel scheme-panel">
        <div className="scheme-head">''',
    '''  function codexLaunchPanel() {
    const running = (id: string) => id === activeProfileId;
    const quickRunning = activeProfileId === QUICK_REMOTE_PROFILE_ID;
    const remoteKeys = appState.management.api_keys ?? [];
    const quickKeyOptions = [
      {
        value: "",
        label:
          remoteKeys.length > 0
            ? `自动（当前 ${maskKey(remoteKeys[0])}）`
            : "自动（远程 CPA 第一个可用 Key）",
      },
      ...(quickApiKey.trim() && !remoteKeys.includes(quickApiKey.trim())
        ? [{ value: quickApiKey.trim(), label: `${maskKey(quickApiKey.trim())}（当前）` }]
        : []),
      ...remoteKeys.map((key) => ({ value: key, label: maskKey(key) })),
    ];
    const quickModelOptions = [
      { value: "", label: t("agents.unspecified", "默认 / 未指定") },
      ...(quickModel.trim() && !codexModelList.includes(quickModel.trim())
        ? [{ value: quickModel.trim(), label: `${quickModel.trim()}（当前）` }]
        : []),
      ...codexModelList.map((model) => ({ value: model, label: model })),
    ];
    const remoteBase = remoteApiBase(appState);
    return (
      <section className="panel scheme-panel">
        {remoteMode ? (
          <div className={`remote-quick-card${quickRunning ? " remote-quick-card--running" : ""}`}>
            <div className="remote-quick-head">
              <div>
                <span className="remote-quick-kicker">远程 CPA 快捷模式</span>
                <h3>一键应用到 Codex</h3>
                <p>{remoteBase || "尚未配置远程 CPA 地址"}</p>
              </div>
              {quickRunning ? (
                <span className="badge running"><i className="tiny-dot" />运行中</span>
              ) : (
                <span className="badge">无需创建方案</span>
              )}
            </div>

            <div className="settings-form-grid remote-quick-grid">
              <label>
                {t("agents.apiKey", "API Key")}
                <Select
                  value={quickApiKey}
                  options={quickKeyOptions}
                  disabled={launchBusy}
                  onChange={setQuickApiKey}
                />
              </label>
              <label>
                {t("agents.codexModel", "模型")}
                <Select
                  value={quickModel}
                  options={quickModelOptions}
                  disabled={launchBusy}
                  onChange={setQuickModel}
                />
              </label>
              <label>
                {t("agents.codexReasoning", "思考程度")}
                <Select
                  value={quickReasoning}
                  options={quickReasoningOptions}
                  disabled={launchBusy}
                  onChange={setQuickReasoning}
                />
              </label>
              <label>
                {t("agents.launch.mode", "启动方式")}
                <Select
                  value={quickLaunchMode}
                  options={[
                    { value: "app", label: t("agents.launch.modeApp", "应用") },
                    { value: "cli", label: t("agents.launch.modeCli", "终端") },
                  ]}
                  disabled={launchBusy}
                  onChange={setQuickLaunchMode}
                />
              </label>
            </div>

            <div className="remote-quick-actions">
              <button
                className="btn primary"
                type="button"
                onClick={() => void startQuickRemote()}
                disabled={launchBusy || !remoteBase}
              >
                <Icon name="play" />
                {launchBusy && startingId === QUICK_REMOTE_PROFILE_ID
                  ? "处理中…"
                  : quickRunning
                    ? "重新应用并重启 Codex"
                    : activeProfileId
                      ? "切换到快捷模式并启动"
                      : "应用并启动 Codex"}
              </button>
              <button
                className="btn"
                type="button"
                onClick={() => void stopActiveProfile()}
                disabled={launchBusy || !quickRunning}
              >
                <Icon name="route" />
                恢复原 Codex 配置
              </button>
              <span className="remote-quick-hint">
                不启动本地 CPA；Key、模型和推理强度可随时修改，运行中重新应用会自动重启 Codex。
              </span>
            </div>
          </div>
        ) : null}

        <div className="scheme-head">''',
    "quick panel jsx",
)

text = replace_once(
    text,
    '''            <h2 className="panel-title">{t("agents.launch.currentScheme", "当前启动方案")}</h2>''',
    '''            <h2 className="panel-title">
              {remoteMode ? "高级启动方案" : t("agents.launch.currentScheme", "当前启动方案")}
            </h2>''',
    "advanced title",
)

text = replace_once(
    text,
    '''            {t("agents.launch.newProfile", "新建方案")}''',
    '''            {remoteMode ? "新建高级方案" : t("agents.launch.newProfile", "新建方案")}''',
    "advanced new button",
)

text = replace_once(
    text,
    '''        {codexProfiles.length === 0 && !profileDraft ? (''',
    '''        {advancedCodexProfiles.length === 0 && !profileDraft ? (''',
    "advanced empty condition",
)

text = replace_once(
    text,
    '''            {t("agents.launch.emptyProfiles", "还没有启动方案。点「新建方案」，选好账号 / 模型 / 思考程度，就能一键拉起 Codex。")}''',
    '''            {remoteMode
              ? "快捷模式可直接使用；这里可选建多套高级方案，用于保存不同 Key / 模型 / 推理强度组合。"
              : t("agents.launch.emptyProfiles", "还没有启动方案。点「新建方案」，选好账号 / 模型 / 思考程度，就能一键拉起 Codex。") }''',
    "advanced empty copy",
)

text = replace_once(
    text,
    '''        {codexProfiles.length > 0 ? (''',
    '''        {advancedCodexProfiles.length > 0 ? (''',
    "advanced list condition",
)

text = replace_once(
    text,
    '''            {codexProfiles.map((profile) => {''',
    '''            {advancedCodexProfiles.map((profile) => {''',
    "advanced list map",
)

text = replace_once(
    text,
    '''                    <div className="field-label"><Icon name="globe" />{t("agents.launch.localEndpoint", "本地端点")}</div>''',
    '''                    <div className="field-label">
                      <Icon name="globe" />
                      {remoteMode ? "远程端点" : t("agents.launch.localEndpoint", "本地端点")}
                    </div>''',
    "endpoint label",
)

text = replace_once(
    text,
    '''                        <strong>{t("agents.launch.localProxy", "本地代理")}</strong>''',
    '''                        <strong>{remoteMode ? "远程 CPA" : t("agents.launch.localProxy", "本地代理")}</strong>''',
    "flow remote label",
)

agents_path.write_text(text, encoding="utf-8")

css = css_path.read_text(encoding="utf-8")
if ".remote-quick-card" not in css:
    css += r'''

/* ---------- Remote CPA 快捷启动 ---------- */
.agents-redesign .remote-quick-card {
  margin: 22px 22px 18px;
  padding: 18px;
  background: linear-gradient(145deg, rgba(239, 246, 255, 0.94), rgba(255, 255, 255, 0.98));
  border: 1px solid rgba(46, 123, 255, 0.22);
  border-radius: 16px;
  box-shadow: 0 14px 32px rgba(46, 123, 255, 0.08);
}
.agents-redesign .remote-quick-card--running {
  border-color: rgba(32, 184, 110, 0.34);
  box-shadow: 0 16px 36px rgba(32, 184, 110, 0.11);
}
.agents-redesign .remote-quick-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}
.agents-redesign .remote-quick-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--primary);
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.04em;
}
.agents-redesign .remote-quick-head h3 {
  margin: 0;
  color: var(--text);
  font-size: 20px;
  line-height: 28px;
  font-weight: 850;
}
.agents-redesign .remote-quick-head p {
  margin: 4px 0 0;
  max-width: 680px;
  overflow: hidden;
  color: var(--muted);
  font-family: var(--mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agents-redesign .remote-quick-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.agents-redesign .remote-quick-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
.agents-redesign .remote-quick-hint {
  flex: 1 1 320px;
  color: var(--muted);
  font-size: 11.5px;
  line-height: 18px;
  font-weight: 650;
}

@media (max-width: 1180px) {
  .agents-redesign .remote-quick-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .agents-redesign .remote-quick-grid {
    grid-template-columns: 1fr;
  }
  .agents-redesign .remote-quick-head {
    flex-direction: column;
  }
  .agents-redesign .remote-quick-actions .btn {
    width: 100%;
  }
}
'''
    css_path.write_text(css, encoding="utf-8")

print("Remote CPA quick mode patch applied")
