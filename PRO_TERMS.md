# Terms of Service & Privacy Policy for ComposeLintAI Pro

**Last Updated:** November 17, 2026

## Overview
**ComposeLintAI Pro** (`com.composelintai.pro.plugin`) is a commercial JetBrains IDE plugin and enterprise AI governance platform for Jetpack Compose and Kotlin Multiplatform (KMP) development, built 100% natively in Kotlin and JVM.

Your code privacy and intellectual property are strictly protected. ComposeLintAI Pro is engineered with a **100% local-processing, zero-cloud-code-collection architecture**.

## 100% Local Processing & Zero Code Collection
- **Zero Source Code Collection:** All Kotlin PSI / AST analysis, rule enforcement (`.composelintai.json`), Shadow PSI validation, Agent Debt scanning, and MCP server tool executions run **100% locally** inside your JetBrains IDE (`Android Studio` / `IntelliJ IDEA`) and local JVM process.
- **No External Code Storage:** Your source files, design tokens, diffs, AST snapshots, and AER-1 provenance receipts are never uploaded to, processed by, or stored on external servers.
- **No Telemetry / Tracking:** ComposeLintAI Pro embeds zero third-party analytics, behavioral tracking, or advertising SDKs.

## Commercial Licensing & Billing
- **JetBrains Marketplace Governance:** All commercial subscriptions, trial periods, invoicing, payments, and license verification for **ComposeLintAI Pro** (`com.composelintai.pro.plugin`) are handled exclusively and securely through the official [JetBrains Marketplace](https://plugins.jetbrains.com/plugin/36095-composelintai-pro).
- **License Validation:** License checks rely solely on JetBrains' built-in licensing mechanism inside the IDE. We do not store payment cards or billing details outside JetBrains Marketplace.

## Network and Local Filesystem Scope
- **Local Loopback by Default:** Communication between the JetBrains IDE plugin, the local MCP server, and an optional connected Android device runs over local loopback interfaces (`127.0.0.1` / `localhost`) with dynamic authentication tokens.
- **Project-Scoped State:** Governance receipts (`.composebridge-state/receipts/`), pending edit journals, and baseline backups remain strictly inside your designated project workspace.

## Third-Party AI Services
When you connect ComposeLintAI Pro's local MCP server to an external AI coding assistant (such as Antigravity, Cursor, Claude Desktop, or Gemini), your prompts and responses with that LLM provider are governed by that provider's own terms and privacy policy.

## Disclaimer & Limitation of Liability
- **"As Is" Basis:** ComposeLintAI Pro is provided **"AS IS"**, without warranty of any kind, express or implied, including merchantability, fitness for a particular purpose, or non-infringement.
- **Developer Responsibility:** Automated refactoring, Shadow PSI gates, and AI-assisted edits are productivity and governance aids. You—the developer or organization—remain solely responsible for reviewing, verifying, and testing all generated or modified code before merging or deploying to production environments.
- **Limitation of Liability:** To the maximum extent permitted by applicable law, the authors and copyright holders shall not be liable for any indirect, incidental, special, consequential, or business-interruption damages arising out of the use or inability to use the plugin.

## Contact
For licensing inquiries, support, or security disclosures regarding **ComposeLintAI Pro**, please reach out via the official [JetBrains Marketplace plugin page](https://plugins.jetbrains.com/plugin/36095-composelintai-pro) or open an issue on the project repository:
https://github.com/TechsOneAi/ComposeLintAI
