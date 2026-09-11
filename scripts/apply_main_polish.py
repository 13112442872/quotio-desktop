from pathlib import Path

path = Path("crates/quotio-core/src/lib.rs")
text = path.read_text(encoding="utf-8")

old = '''        if self.settings.keep_proxy_on_exit {
            return;
        }
        if let Err(error) = self.finish_codex_session_unlocked(true) {
'''

new = '''        // Remote CPA mode is a persistent Codex deployment. Closing Quotio must
        // not kill Codex and must not restore/remove the managed cliproxyapi
        // provider. Users can still restore explicitly from the Agents page.
        if matches!(self.settings.connection_mode, ConnectionMode::Remote) {
            return;
        }
        if self.settings.keep_proxy_on_exit {
            return;
        }
        if let Err(error) = self.finish_codex_session_unlocked(true) {
'''

if old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    print("Applied Remote CPA persistent-exit behavior")
elif "Remote CPA mode is a persistent Codex deployment" in text:
    print("Remote CPA persistent-exit behavior already applied")
else:
    raise SystemExit("Could not locate shutdown_unlocked patch anchor")
