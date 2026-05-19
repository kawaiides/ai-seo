// AEGIS — VS Code extension.
//
// Three commands:
//   aegis.auditActiveFile  → audit the entire active editor as `text`
//   aegis.auditSelection   → audit just the selection (context menu)
//   aegis.configure        → prompt for the Bearer API key, store in
//                            SecretStorage so it never lives on disk
//                            in plaintext settings.json.
//
// Results render in a side webview panel: score + band + per-check
// pass/fail rows, with a status-bar badge showing the latest score.

import * as vscode from "vscode";

const SECRET_KEY = "aegis.apiKey";

interface AuditCheck {
  check_id: string;
  name: string;
  passed: boolean;
  score: number;
  max_score: number;
  recommendation?: string | null;
}

interface AuditResponse {
  aeo_score: number;
  band: string;
  checks: AuditCheck[];
  plan?: "free" | "pro";
  locked_checks?: { check_id: string; name: string }[];
}

let statusItem: vscode.StatusBarItem | undefined;
let webview: vscode.WebviewPanel | undefined;

export function activate(context: vscode.ExtensionContext) {
  statusItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    50
  );
  statusItem.command = "aegis.auditActiveFile";
  statusItem.text = "$(beaker) AEGIS";
  statusItem.tooltip = "Audit this file with AEGIS";
  statusItem.show();
  context.subscriptions.push(statusItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("aegis.configure", () =>
      configureApiKey(context)
    ),
    vscode.commands.registerCommand("aegis.auditActiveFile", () =>
      runAudit(context, "active")
    ),
    vscode.commands.registerCommand("aegis.auditSelection", () =>
      runAudit(context, "selection")
    )
  );
}

export function deactivate() {
  statusItem?.dispose();
  webview?.dispose();
}

async function configureApiKey(context: vscode.ExtensionContext) {
  const value = await vscode.window.showInputBox({
    title: "AEGIS API key",
    prompt:
      "Paste your org-scoped Bearer key. Stored in VS Code SecretStorage.",
    password: true,
    ignoreFocusOut: true,
    validateInput: (v) =>
      !v?.trim() || v.length < 16 ? "Key looks too short." : null,
  });
  if (value !== undefined) {
    await context.secrets.store(SECRET_KEY, value.trim());
    vscode.window.showInformationMessage("AEGIS API key saved.");
  }
}

async function runAudit(
  context: vscode.ExtensionContext,
  mode: "active" | "selection"
) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("Open a file to audit first.");
    return;
  }
  const text =
    mode === "selection" && !editor.selection.isEmpty
      ? editor.document.getText(editor.selection)
      : editor.document.getText();
  if (!text.trim()) {
    vscode.window.showWarningMessage("Nothing to audit (empty selection).");
    return;
  }

  const apiKey = await context.secrets.get(SECRET_KEY);
  if (!apiKey) {
    const pick = await vscode.window.showWarningMessage(
      "AEGIS API key not configured.",
      "Configure now"
    );
    if (pick === "Configure now") {
      await configureApiKey(context);
    }
    return;
  }
  const cfg = vscode.workspace.getConfiguration("aegis");
  const apiBase = (cfg.get<string>("apiBase") || "https://aegis-autopilot.com").replace(
    /\/+$/,
    ""
  );
  const threshold = cfg.get<number>("threshold") ?? 65;

  if (statusItem) {
    statusItem.text = "$(sync~spin) AEGIS";
    statusItem.tooltip = "Auditing…";
  }

  try {
    const resp = await fetch(`${apiBase}/api/v1/audit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
        "User-Agent": "AEGIS-VSCode/0.1.0",
      },
      body: JSON.stringify({ input_type: "text", input_value: text }),
    });
    const data = (await resp.json().catch(() => ({}))) as
      | AuditResponse
      | { detail?: { message?: string; error?: string } };
    if (!resp.ok || !("aeo_score" in data)) {
      const detail =
        ("detail" in data && data.detail?.message) ||
        ("detail" in data && data.detail?.error) ||
        `HTTP ${resp.status}`;
      throw new Error(detail as string);
    }
    showResult(data as AuditResponse, threshold);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    vscode.window.showErrorMessage(`AEGIS audit failed: ${msg}`);
    if (statusItem) {
      statusItem.text = "$(error) AEGIS";
      statusItem.tooltip = msg;
    }
  }
}

function showResult(data: AuditResponse, threshold: number) {
  if (statusItem) {
    const colour =
      data.aeo_score >= threshold
        ? new vscode.ThemeColor("statusBarItem.prominentForeground")
        : new vscode.ThemeColor("statusBarItem.errorForeground");
    statusItem.text = `$(beaker) AEGIS ${data.aeo_score}`;
    statusItem.color = colour;
    statusItem.tooltip = `${data.band} · ${data.plan ?? "free"} plan`;
  }

  if (!webview) {
    webview = vscode.window.createWebviewPanel(
      "aegis.report",
      "AEGIS Audit",
      vscode.ViewColumn.Beside,
      { enableScripts: false, retainContextWhenHidden: true }
    );
    webview.onDidDispose(() => (webview = undefined));
  }
  webview.webview.html = renderHtml(data);
  webview.reveal(vscode.ViewColumn.Beside, true);
}

function renderHtml(data: AuditResponse): string {
  const checksHtml = data.checks
    .map((c) => {
      const colour = c.passed ? "#4ade80" : "#f87171";
      const mark = c.passed ? "✓" : "✗";
      return `<li style="color:${colour}"><strong>${mark}</strong> ${escape(
        c.name
      )} <span style="color:#94a3b8">(${c.score}/${c.max_score})</span></li>`;
    })
    .join("");
  const lockedHtml =
    data.locked_checks && data.locked_checks.length
      ? `<div style="margin-top:14px;padding:8px;background:#1e1b2f;border-radius:8px;font-size:12px;color:#cbd5e1;">
           <strong style="color:#f0abfc">Pro checks (locked):</strong>
           ${data.locked_checks.map((l) => escape(l.name)).join(", ")}
         </div>`
      : "";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    body { font-family: var(--vscode-font-family); padding: 20px; color: #e2e8f0; background: #0a0a16; }
    h1 { font-size: 14px; letter-spacing: .1em; text-transform: uppercase; color: #94a3b8; margin: 0 0 12px; }
    .score { font-size: 56px; font-weight: 800; line-height: 1; }
    .band { color: #cbd5e1; margin-top: 6px; }
    ul { list-style: none; padding: 0; margin: 18px 0 0; font-size: 13px; }
    ul li { padding: 6px 0; border-top: 1px solid rgba(255,255,255,.05); }
    ul li:first-child { border-top: 0; }
  </style></head><body>
    <h1>AEGIS · AEO Audit</h1>
    <div class="score">${data.aeo_score}</div>
    <div class="band">${escape(data.band)} · ${data.plan ?? "free"} plan</div>
    <ul>${checksHtml}</ul>
    ${lockedHtml}
  </body></html>`;
}

function escape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
