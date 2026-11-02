# Hagent Windows readiness — 29 October 2026

Status: repaired and locally tested, but **not ready for public Windows or Microsoft Store release**. The live Holly acceptance exercise did not deliver its artifacts. No package was installed, certificate trusted, or Store submission uploaded.

## Changes

- Shared completion checks prevent manual review approval and CLI completion while runs or children are unfinished, or required verification is missing.
- Approval-gated verification keeps a durable link to its original run, inherits its working directory, applies once, and rejects superseded work.
- Issue-based synchronous delegation now creates a child issue and a distinct worker run, passes through normal verification, preserves delegation limits, and checks parent cancellation. Chat-only delegation retains its existing timeline behavior.
- The CLI tool bridge supports installed MCP SDK 1 and 2 interfaces. Its loopback endpoints require an unpredictable per-run token and refuse browser-origin requests. Execution context crosses the bridge thread so the delegated run retains its parent.
- Claude and Codex error events no longer silently appear as successful responses.
- The integrated Windows PowerShell terminal includes owner-only access, workspace-scoped transcripts, session shutdown, and agent activity links. Cross-origin requests and loopback hostname checks protect local mode. PowerShell no longer writes Hagent commands into the shared ConsoleHost history file.
- Uploaded branding goes beside the database, outside the read-only installed application folder.
- A native Windows tray launcher and offline MSIX build/sign scripts are included. The test app uses a separate `%LOCALAPPDATA%\HagentTest` data directory and an available loopback port. It does not import your existing database or provider credentials.

## Evidence

- Full regression after terminal cleanup fixes: **371 passed, 1 skipped** in 134.92 seconds. An additional restart-protection regression also passed separately. Output: `output/readiness-final.log` and `output/readiness-final.xml`. The skip is the pre-existing Windows named-pipe MCP integration restriction; the bridge subprocess tests added here passed.
- MCP SDK 2 compatibility: `output/mcp-v2-bridge.log`, 2 real subprocess tests passed. SDK 1 is covered in the full suite. Tests exercise initialization, schema discovery, successful calls, and error responses.
- Controlled hierarchy regression: `test_issue_delegation_has_child_run_and_independent_review` verifies parent, worker, and reviewer records. Its model responses are mocked; it is not evidence of successful live Holly delivery.
- Browser terminal: entered `Write-Output ('HAGENT_TERMINAL_' + (6 * 7))`; observed `HAGENT_TERMINAL_42`; stopped the session and checked saved history. Screenshot: `output/terminal-readiness-20261029.jpg`.
- Native packaged launcher passed `--smoke-test`: the bundled Python started a fresh database/server, answered health checks, and shut down. Bundled `winpty`, `hagent.web`, and `mcp` imports passed.
- Packaged backend acceptance with fresh data passed eight routes, a real WebSocket terminal command, saved transcript, and graceful server shutdown. Evidence: `output/packaged-acceptance.json`; repeat using `scripts/verify_windows_payload.py <payload-folder>`.
- Portable ZIP extraction into a folder containing spaces and native launcher smoke test passed. Both archive CRC checks passed, selected packaged source files match the checkout, and no database, PFX, or .env was included. Current sizes and SHA-256 digests are in `output/readiness-artifacts.json`.
- Real PowerShell process-tree cleanup passed: parent PID 26060 and child PIDs 42364/42804 all stopped. Evidence: `output/terminal-process-cleanup.json`. Cleanup and disk failures now release the terminal owner lock, with failures logged; the restart helper refuses to terminate a live server with open terminal sessions.
- Windows SDK MakeAppx completed packaging with manifest validation. Log: `output/msix-pack.log`. This is not a Windows App Certification Kit pass or an installed-MSIX test.
- Live database `PRAGMA quick_check`: `ok`. Six orphaned attachment metadata records were preserved in `orphaned_attachment_archive`; all six were compared to the backup and matched exactly. No attachment files were deleted. Foreign-key violations after repair: zero.
- Database backup: `hagent.db.before-attachment-repair-20261029-215229.bak`.

## Holly live acceptance

Issue: `4f0adcb9-5979-4037-ac33-692c476469e3`, “Holly hierarchy acceptance: expense summary”.

The existing brief asks Holly to coordinate a small Decimal-based expense summarizer through Mika, Jack and Ruth, writing only `expenses.py`, `test_expenses.py` and `README.md` under `output/holly-acceptance-20261029`, with independent test execution before completion.

The signed-in dashboard retry created run `3180e306-a83e-44cd-9265-09cb3521df5a`. It failed: Claude reported `ECONNREFUSED`; the Codex backup reported access denied during startup. The output folder remained empty. This is a failed acceptance test, not completed work. Earlier “completed” model turns were also rejected by verification and did not establish delivery.

Next live step: launch `scripts/restart_hagent.cmd` yourself in your normal Windows session, check provider sign-in/connectivity under Connections, then retry the same issue. Do not weaken sandbox or approval settings. The remaining agent hierarchy must be observed in a successful live run before release.

## Test artifacts and limitations

- `dist/Hagent-Portable-Test-0.2.3-x64.zip`: extract the entire ZIP and double-click `Hagent.exe`. Includes `START_HERE.txt`, all required runtime files, and no installer/certificate trust step. This remains a test build with a separate empty workspace.
- `dist/Hagent-Test-0.2.3-x64-unsigned.msix`: unsigned, test-identity package. **Not a working double-click installer yet.**
- `build/windows-test-20261029-174057/Hagent.exe`: runnable extracted test launcher. Keep its adjacent `runtime` folder. It opens a separate empty workspace; Exit Hagent is available from its tray icon.
- `scripts/build_windows_test.py`: rebuilds from a Windows Python environment with installed project dependencies; records exact bundled dependency versions in `runtime-versions.json`. It copies only the declared dependency graph and selected app resources.
- `scripts/sign_windows_test.py <package.msix>`: generates an ephemeral local test signing certificate and invokes SignTool; it does not trust or install certificates. The signing attempt here failed at SignTool's private-key filter for both unencrypted and encrypted PFX input. The temporary private key was removed; no signing success is claimed. Diagnostics: `output/msix-sign.log`.

The currently available complete build environment is Python 3.10. It is suitable only for this local test artifact: Python 3.10 reached end of support on 1 October 2026. Rebuild and retest with a supported Python version and compatible pywinpty before public release. The available Python 3.12 test runtime lacks pywinpty; the configured package source could not supply additional build packages in this session.

Follow-up environment inspection confirmed `PIP_NO_INDEX` is enabled and HTTP/HTTPS/ALL proxy variables point at `127.0.0.1:9`. These managed-session restrictions explain why dependency downloads and network provider execution cannot be completed here; they were not removed or overridden. Another attempt to install pywinpty for Python 3.12 failed. No additional paid Holly retry was started against the same known restriction.

Store work remains: obtain the reserved package identity and publisher from Partner Center; replace the test manifest identity; complete branding/listing/privacy information; validate restricted `runFullTrust` capability; run clean-machine installation, upgrade, uninstall and Windows App Certification Kit checks. Live provider authentication, terminal child-process cleanup under every CLI, accessibility and long-running recovery need release acceptance beyond the checks recorded here.

## References

- Microsoft: [Package signing](https://learn.microsoft.com/en-us/windows/msix/package/sign-msix-package-guide), [MSIX testing](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-debug), [Package identity](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/package-identity-overview).
- Python: [Supported versions](https://devguide.python.org/versions/).
- Vendored xterm.js license: `hagent/static/vendor/xterm/LICENSE`, from [upstream](https://raw.githubusercontent.com/xtermjs/xterm.js/master/LICENSE).
