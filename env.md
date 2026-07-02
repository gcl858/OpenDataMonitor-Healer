# OH MY Pi Environment Audit Report

**Generated:** 2026-07-02 12:23 UTC
**Audit scope:** read-only inspection, no files modified
**Session host:** Linux 6.17 (Azure-hosted runner), user `runner`
**OMP runtime version:** `omp v16.3.0` (bundled native: `pi_natives.linux-x64-{baseline,modern}.node`)

---

## 1. Installed Agents

**Status: NO agents currently unpacked into the runtime.**

OMP bundles a set of task agents inside the `omp` binary that must be materialised via `omp agents unpack`. That command has **not** been run in this environment, so `~/.omp/agent/agents/` and `./.omp/agents/` do not exist.

| Bundled agent (template path) | Role | Source |
|---|---|---|
| `explore` | READ-ONLY codebase scout returning compressed context for handoff | `packages/coding-agent/src/prompts/agents/explore.md` |
| `plan` | Software architect for complex multi-file architectural decisions | `prompts/agents/plan.md` |
| `designer` | UI/UX specialist for design implementation, review, visual refinement | `prompts/agents/designer.md` |
| `reviewer` | Code review specialist for quality/security analysis | `prompts/agents/reviewer.md` |
| `tester` | Authoritative test writer — NEVER skip delegating tests to it | `prompts/agents/tester.md` |
| `librarian` | Researches external libraries and APIs by reading source code | `prompts/agents/librarian.md` |
| `task` | General-purpose subagent with full capabilities for delegated multi-step tasks | `prompts/agents/task.md` |
| `sonic` | Low-reasoning agent for strictly mechanical updates or data collection only | `prompts/agents/sonic.md` |
| `init` | "Generate AGENTS.md for current codebase" | `prompts/agents/init.md` |

**Configuration / unpack locations:**

| `.env` present in working tree | **No** |
| `HEALER_TOKEN` env var | **Set but empty** (`HEALER_TOKEN=`) — pipeline would fail loudly if invoked |
| `share.redactSecrets` | **true** — secrets redacted on `omp share`/session export |
| `secrets.enabled` | **false** — secret-store integration disabled |
| World-readable catalog DB | `~/.omp/agent/models.db` is `644`. Acceptable: contains only public model metadata. |
| Owner-only settings DB | `~/.omp/agent/agent.db` is `600` (runner-only). Good. |
| Shell snapshots | `/tmp/omp-shell-snapshots/*.sh` are `600` (owner-only). Good. |

### 9.2 Unsafe permissions

| Finding | Severity | Detail |
|---|---|---|
| **`tools.approvalMode = yolo`** | **HIGH** | Every tool call is auto-approved with no user confirmation. Acceptable for unattended CI; risky if used interactively. The dispatch pipeline (healer workflow) intentionally relies on this. |
| `bash.enabled = true` + PTY capable | Medium | The bash tool can run arbitrary commands. Currently gated only by approval mode. |
| `--no-pty` not set | Low | Interactive PTY bash is available — full-color `tty` mode for prompts. |
| `lsp.formatOnWrite = false`, `lsp.diagnosticsOnWrite = true` | OK | Writes are checked but not auto-formatted. |
| `lsp.diagnosticsOnEdit = false` | OK | Edits are not gated on LSP diagnostics. |
| `bash.autoBackground.enabled = false` | OK | Bash commands stay foreground unless threshold (60 s) is hit. |
| `retry.maxRetries = 10` with `maxDelayMs = 300000` | Low | A failed call can be retried for up to ~50 minutes wall-clock before falling through. |

### 9.3 Suspicious configuration

| Finding | Detail |
|---|---|
| **`git diff omp_batch.sh` is mode-only (`644 → 755`)** | Unstaged change that makes the batch wrapper executable. Not suspicious per se but **uncommitted** — `git status` shows it as a modification. |
| `provider.appendOnlyContext = auto` | Provider may append context; with `yolo` approval, nothing prompts for confirmation. |
| `compaction.handoffSaveToDisk = false` | No on-disk handoff state — sessions die in-process if interrupted. |
| `memories.enabled = false`, `memory.backend = off` | No long-term memory is being recorded. |
| `mcp.discoveryMode = false` | No external MCP servers can be auto-loaded — good defence in depth. |
| Unreachable local model endpoints | Logs show repeated warnings: `ollama http://127.0.0.1:11434`, `lm-studio http://127.0.0.1:1234/v1`, `llama.cpp http://127.0.0.1:8080` all fail to connect. These are benign (no local LLM is expected), but generate noise. |
| `auth-broker` / `auth-gateway` infrastructure | Present in the binary but **not configured** (`auth.broker.url = (not set)`). No credentials are being brokered. |
| `collab.relayUrl = wss://my.omp.sh` | Collab relay URL is configured but `collab.webUrl` is empty — collab features are inert. |
| `share.store = blob`, `share.serverUrl = https://my.omp.sh/s` | Outbound share endpoint. `share.redactSecrets = true` mitigates data exposure. |
| `dev.autoqa.consent = unset`, `dev.autoqaPush.endpoint = https://qa.omp.sh/v1/grievances` | Telemetry endpoint is defined but consent is unset → no `autoqa` push happens. |
| `HCA_CLOUD_PROVIDER = azure` (env) | Indicates hosted-compute-agent running on Azure; expected given Azure kernel. |
| `MEMORY_PRESSURE_WATCH = /sys/fs/cgroup/.../memory.pressure` | OMP is wired into cgroup memory-pressure monitoring. |
| `CLOUDSDK_CORE_DISABLE_PROMPTS=1`, `GH_PROMPT_DISABLED=1`, `COMPOSER_NO_INTERACTION=1`, `GIT_TERMINAL_PROMPT=0` | All interactive prompts disabled — safe for unattended runs. |
| Compiler cache evidence in `strings` | The `~/.local/bin/omp` binary still contains debug strings referencing `/root/.cargo/registry/src/...` and `/root/.rustup/toolchains/nightly-2025-12-10-...` — build artefacts leaked into the shipped binary. Not a security issue, but indicates the binary was built on a developer machine rather than a sanitised CI runner. |

### 9.4 Bottom line

The OMP runtime in this environment is configured for **fire-and-forget unattended CI**: one provider (`minimax`), zero plugins, zero skills, zero MCP, zero local LLMs, secrets kept out of disk. The principal residual risk is `tools.approvalMode = yolo` combined with full bash + PTY + Python eval — every tool call is auto-approved. This is the expected operating mode for the healer pipeline but should NOT be reused on an interactive workstation without changing `approvalMode` to `always-ask` or `write`.

---

## Appendix A — One-line re-collect

```
omp v16.3.0 · minimax/MiniMax-M3 (high thinking, yolo approval) ·
0 agents unpacked · 0 skills · 0 extensions · 0 MCP · 0 plugins · 1 provider configured
```
Exported to: odm-20260702_121453.html
Session: odm-20260702_121453
Session file: /home/runner/.omp/agent/sessions/custom/odm-20260702_121453.jsonl
Report: odm-20260702_121453.html