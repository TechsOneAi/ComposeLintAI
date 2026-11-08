# Security Policy & Operational Integrity

The ComposeLintAI team takes the security, reliability, and safety of developer environments and source code seriously.

---

## 🛡️ Supported Versions

We release security patches, stability updates, and governance improvements for the following versions:

| Version | Supported          | Status                                             |
| ------- | ------------------ | -------------------------------------------------- |
| 1.1.x   | :white_check_mark: | Active (Current stable release branch: v1.1.9)    |
| 1.0.x   | :x:                | Deprecated                                         |
| < 1.0   | :x:                | Unsupported                                        |

---

## 🔒 Security Architecture & Guarantees

1. **Local-First Default & Optional Remote Funnel:**
   - By default, the Bridge Server (`server.py`) and Agent WebSocket connect exclusively over local loopback interfaces (`127.0.0.1` / ADB port forwarding `tcp:8711`).
   - Remote WAN tunnel previewing via Tailscale Funnel is strictly **opt-in**, requiring manual activation via the "Share Remote" toolbar action and token authentication.
   - The server does not expose open public relay endpoints or transmit your source code to external cloud services.
2. **Dynamic Auth Token Protection:**
   - Every session generates a cryptographically random authentication token stored locally in `.composebridge-state/auth.json`.
   - Incoming WebSocket connections must supply a valid authentication header or token to execute file modifications.
3. **Byte-Level Journaling & Safe Rollbacks:**
   - Any modification made by the AST Splicer generates a `.bridge.bak` byte-level backup before applying changes.
   - Pending changes are logged to `PatternJournal` and can be reverted cleanly at any time (`Reject All`).
4. **Whitelisted Gradle Execution:**
   - The MCP build tools only permit explicitly whitelisted Gradle tasks (e.g. `assembleDebug`, `check`, `compileKotlin`). Arbitrary shell command execution is prohibited.
5. **Zero Internal APIs Guarantee:**
   - The IntelliJ plugin adheres strictly to official JetBrains Platform public SDKs, eliminating `com.intellij.*.impl.*` imports to prevent binary breaks or untracked memory leaks.
6. **Agent Debt Hard-Gate Governance:**
   - AI agent prompts are structurally hard-gated with machine-readable file coordinates and reverse-order execution to guarantee idempotent, non-destructive source changes.

---

## ⚠️ Operational Security & Code Integrity Notices

### 1. Mobile Live Editing Operational Boundaries
- **AST Mutation Scope:** The **Live Editing** feature (modifying colors, dimensions, and text directly from a connected mobile device) performs real-time AST transformations directly into source files.
- **Project Structure Sensitivity:** Non-standard project architectures (such as proprietary design token wrappers, complex custom builders, or unconventional multi-module layouts) may occasionally trigger anomalous code formatting or syntax placement during live edits.
- **Mandatory Human-in-the-Loop Review:** Developers **must review all incoming modifications** in the Android Studio `Changes` tab (`File Diffs`) before accepting them. The atomic `Approve` / `Reject` controls exist specifically as an architectural safeguard to maintain 100% code integrity.

### 2. General Tool Reliability
- All core analytical features—including **IDE Architecture Diagnostics**, **Agent Debt Governance**, **Token Duplication Scanners**, **Token Splitting**, and **Gradle Remote Verification**—operate non-destructively and are fully reliable across all standard Kotlin and Kotlin Multiplatform (KMP) codebases.

### 3. Reporting Live Editing Anomalies vs. Security Vulnerabilities
- If you encounter anomalous syntax errors or unexpected behavior while editing colors, dimensions, or text from your phone, please open a public bug report with project context on our [GitHub Issues Tracker](https://github.com/TechsOneAi/ComposeLintAI/issues).
- Security vulnerabilities (unauthorized access, privilege escalation, token leakage) must **not** be posted publicly and should follow the private disclosure process below.

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability in ComposeLintAI, please **do not** open a public issue.

Instead, please report it via:
1. **GitHub Private Security Advisory:** Open an advisory under the Security tab of the [repository](https://github.com/TechsOneAi/ComposeLintAI/security/advisories).
2. **Direct Contact:** Send an email to the project maintainers via the GitHub profile contact.

Please include:
- A description of the issue and potential impact.
- Clear reproduction steps or proof-of-concept (PoC).
- Your environment details (OS, ComposeLintAI version, Kotlin version, Python version).

We will acknowledge receipt within **48 hours** and provide regular progress updates until a patch is released.
