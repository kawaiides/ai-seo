// AEGIS Chrome popup — audit the current tab.
//
// Flow:
//   1. On open, read API base URL + key from chrome.storage.sync.
//   2. If unconfigured, show the settings CTA.
//   3. On "Audit": send a runtime message to background.js, which calls
//      `${apiBase}/api/v1/audit` with the tab URL.
//   4. Render score, band, per-check pass/fail rows.

const $ = (id) => document.getElementById(id);

const BAND_LABELS = [
  [85, "AEO Optimized"],
  [65, "Needs Improvement"],
  [40, "Significant Gaps"],
  [0, "Not AEO Ready"],
];

function bandLabel(score) {
  for (const [threshold, label] of BAND_LABELS) {
    if (score >= threshold) return label;
  }
  return "Not AEO Ready";
}

function bandColor(score) {
  if (score >= 85) return "#4ade80";
  if (score >= 65) return "#a3e635";
  if (score >= 40) return "#facc15";
  return "#f87171";
}

async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(["apiBase", "apiKey"], (data) => {
      resolve({
        apiBase: (data.apiBase || "https://aegis-autopilot.com").replace(
          /\/$/,
          ""
        ),
        apiKey: data.apiKey || "",
      });
    });
  });
}

async function currentTabUrl() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      resolve(tabs && tabs[0] ? tabs[0].url : null);
    });
  });
}

function render(data) {
  $("score").textContent = data.aeo_score;
  $("score").style.color = bandColor(data.aeo_score);
  $("band").textContent = data.band || bandLabel(data.aeo_score);
  $("plan").textContent =
    data.plan === "pro" ? "Pro plan · all 7 checks" : "Free plan · 3 checks";
  const ul = $("checks");
  ul.innerHTML = "";
  (data.checks || []).forEach((c) => {
    const li = document.createElement("li");
    li.className = c.passed ? "pass" : "fail";
    li.textContent = `${c.passed ? "✓" : "✗"} ${c.name} (${c.score}/${c.max_score})`;
    ul.appendChild(li);
  });
  if (Array.isArray(data.locked_checks) && data.locked_checks.length) {
    const el = $("locked");
    el.innerHTML =
      `<strong>Pro checks:</strong> ` +
      data.locked_checks.map((l) => l.name).join(", ") +
      ` · <a href="https://aegis-autopilot.com/pricing" target="_blank" style="color:#93c5fd;">Upgrade</a>`;
    el.hidden = false;
  } else {
    $("locked").hidden = true;
  }
  $("result").hidden = false;
}

async function runAudit() {
  $("err").hidden = true;
  $("cta").hidden = true;
  const { apiBase, apiKey } = await getConfig();
  if (!apiKey) {
    $("cta").hidden = false;
    return;
  }
  const url = await currentTabUrl();
  if (!url || !/^https?:/.test(url)) {
    $("err").textContent = "Not an http(s) page.";
    $("err").hidden = false;
    return;
  }
  $("run").disabled = true;
  $("run").textContent = "Auditing…";
  try {
    const resp = await chrome.runtime.sendMessage({
      type: "AEGIS_AUDIT",
      url,
      apiBase,
      apiKey,
    });
    if (!resp || !resp.ok) {
      throw new Error(resp && resp.error ? resp.error : "Audit failed");
    }
    render(resp.data);
  } catch (e) {
    $("err").textContent = e.message;
    $("err").hidden = false;
  } finally {
    $("run").disabled = false;
    $("run").textContent = "Audit this page";
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const url = await currentTabUrl();
  $("url").textContent = url || "—";
  const { apiKey } = await getConfig();
  if (!apiKey) {
    $("cta").hidden = false;
  }
});

$("run").addEventListener("click", runAudit);
$("open-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("open-options-link").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
