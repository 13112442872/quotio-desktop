from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected pattern not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected pattern not found in {path}: {old!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


# 1) “System” language must resolve from the OS/browser locale instead of treating
# the literal string "system" as a non-Chinese language (which always fell back to English).
replace_once(
    "apps/desktop/src/i18n.tsx",
    '''export function resolveLocale(language: string | undefined | null): Locale {\n  if (!language) return "en";\n  return language.toLowerCase().startsWith("zh") ? "zh" : "en";\n}''',
    '''export function resolveLocale(language: string | undefined | null): Locale {\n  const configured = (language ?? "").trim().toLowerCase();\n  let candidate = configured;\n\n  if (!candidate || candidate === "system") {\n    if (typeof navigator !== "undefined") {\n      const preferred = Array.isArray(navigator.languages)\n        ? navigator.languages.find((value) => typeof value === "string" && value.trim().length > 0)\n        : undefined;\n      candidate = (preferred ?? navigator.language ?? "").trim().toLowerCase();\n    } else {\n      candidate = "";\n    }\n  }\n\n  return candidate.startsWith("zh") ? "zh" : "en";\n}''',
)

# 2) The secure remote key survives reinstall/upgrade by design. In Local mode do
# not render its masked value as if it were a pre-filled/default password. In
# Remote mode, make it explicit that the mask is an already-saved credential.
replace_once(
    "apps/desktop/src/components/sections/SettingsScreen.tsx",
    'placeholder={credentialStatus.remote_management_key_masked ?? "保存后迁入安全存储"}',
    '''placeholder={\n                          settings.connection_mode === "remote"\n                            ? credentialStatus.remote_management_key_masked\n                              ? `已保存远程密钥：${credentialStatus.remote_management_key_masked}`\n                              : "输入远程 CPA 管理密钥"\n                            : "仅远程代理模式使用"\n                        }''',
)

# 3) This fork must never query upstream xiaocoss releases for application updates.
replace_all(
    "apps/desktop/src-tauri/tauri.conf.json",
    "github.com/xiaocoss/quotio-desktop/releases/latest/download/latest.json",
    "github.com/13112442872/quotio-desktop/releases/latest/download/latest.json",
)

# Keep About/Home/Help links inside this fork as well, so users aren't sent back
# to upstream when they are running the customized build.
replace_all(
    "apps/desktop/src/components/AppShell.tsx",
    "https://github.com/xiaocoss/quotio-desktop",
    "https://github.com/13112442872/quotio-desktop",
)

# The two feature workflows + patchers were only scaffolding used to land Remote
# CPA V1/Quick Mode. main now contains the real source, so remove the scaffolding.
for rel in [
    ".github/workflows/remote-cpa-v1.yml",
    ".github/workflows/remote-cpa-quick.yml",
    "scripts/apply_remote_cpa_v1.py",
    "scripts/apply_remote_quick_mode.py",
]:
    path = ROOT / rel
    if path.exists():
        path.unlink()

print("main polish patch applied")
