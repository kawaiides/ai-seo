// AEGIS — Webflow Designer Extension.
//
// Webflow injects a `window.webflow` object that exposes:
//   - getCurrentPage()      → returns { id, name, slug, ... }
//   - getSiteInfo()         → returns { siteId, shortName, ... }
//   - getAllElements()      → live element tree
//
// We use the live element tree to assemble the page's HTML so we can
// audit the *draft*, not the last-published version. Falls back to the
// publish URL when DOM access is restricted.

const $ = (id) => document.getElementById(id);
const STORE = "aegis.webflow.config";

function loadConfig() {
  try {
    return JSON.parse(localStorage.getItem(STORE) || "{}");
  } catch {
    return {};
  }
}

function saveConfig(c) {
  localStorage.setItem(STORE, JSON.stringify(c));
}

function bandColor(s) {
  if (s >= 85) return "#4ade80";
  if (s >= 65) return "#a3e635";
  if (s >= 40) return "#facc15";
  return "#f87171";
}

async function collectPageHtml() {
  // Best-effort. Webflow's Designer API exposes a node tree; we
  // serialise visible text-bearing nodes as a flat HTML approximation,
  // good enough for AEO checks that look at h-tags + first paragraph
  // + body text.
  if (!window.webflow || !window.webflow.getAllElements) {
    return null;
  }
  const els = await window.webflow.getAllElements();
  const parts = [];
  for (const el of els) {
    try {
      const tag = (await el.getTag?.()) || "div";
      const text = (await el.getText?.()) || "";
      if (!text.trim()) continue;
      parts.push(`<${tag}>${escapeHtml(text)}</${tag}>`);
    } catch {
      /* skip */
    }
  }
  return parts.join("\n");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function describePage() {
  if (!window.webflow || !window.webflow.getCurrentPage) {
    return "Designer API unavailable — open inside Webflow Designer.";
  }
  try {
    const p = await window.webflow.getCurrentPage();
    return `${p?.name || "Untitled"} · /${p?.slug || ""}`;
  } catch {
    return "—";
  }
}

async function runAudit() {
  $("err").hidden = true;
  const cfg = loadConfig();
  const apiBase = ($("apiBase").value || cfg.apiBase || "https://aegis-autopilot.com").replace(
    /\/+$/,
    ""
  );
  const apiKey = ($("apiKey").value || cfg.apiKey || "").trim();
  saveConfig({ apiBase, apiKey });
  if (!apiKey) {
    $("err").textContent = "Paste your AEGIS API key first.";
    $("err").hidden = false;
    return;
  }

  $("run").disabled = true;
  $("run").textContent = "Auditing…";
  try {
    const html = await collectPageHtml();
    if (!html) {
      $("err").textContent =
        "Could not read the page tree. Make sure the extension is opened inside Webflow Designer.";
      $("err").hidden = false;
      return;
    }
    const r = await fetch(`${apiBase}/api/v1/audit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
        "User-Agent": "AEGIS-Webflow/0.1.0",
      },
      body: JSON.stringify({ input_type: "text", input_value: html }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !("aeo_score" in data)) {
      const msg =
        data?.detail?.message || data?.detail?.error || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    render(data);
  } catch (e) {
    $("err").textContent = e.message;
    $("err").hidden = false;
  } finally {
    $("run").disabled = false;
    $("run").textContent = "Audit this page";
  }
}

function render(data) {
  $("score").textContent = data.aeo_score;
  $("score").style.color = bandColor(data.aeo_score);
  $("band").textContent = `${data.band} · ${data.plan || "free"} plan`;
  const ul = $("checks");
  ul.innerHTML = "";
  (data.checks || []).forEach((c) => {
    const li = document.createElement("li");
    li.className = c.passed ? "pass" : "fail";
    li.textContent = `${c.passed ? "✓" : "✗"} ${c.name} (${c.score}/${c.max_score})`;
    ul.appendChild(li);
  });
  $("panel").hidden = false;
}

(async () => {
  const cfg = loadConfig();
  $("apiBase").value = cfg.apiBase || "https://aegis-autopilot.com";
  $("apiKey").value = cfg.apiKey || "";
  $("pageLabel").textContent = await describePage();
})();

$("run").addEventListener("click", runAudit);
