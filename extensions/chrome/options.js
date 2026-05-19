const $ = (id) => document.getElementById(id);

chrome.storage.sync.get(["apiBase", "apiKey"], (data) => {
  $("apiBase").value = data.apiBase || "https://aegis-autopilot.com";
  $("apiKey").value = data.apiKey || "";
});

$("save").addEventListener("click", () => {
  const apiBase = ($("apiBase").value || "").trim().replace(/\/$/, "");
  const apiKey = ($("apiKey").value || "").trim();
  chrome.storage.sync.set({ apiBase, apiKey }, () => {
    const ok = $("ok");
    ok.style.display = "block";
    setTimeout(() => (ok.style.display = "none"), 2000);
  });
});
