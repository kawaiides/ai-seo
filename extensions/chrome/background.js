// AEGIS service worker.
//
// All cross-origin fetches happen here, not in the popup, so we don't
// blow the popup's CSP and so the Bearer key never appears in any
// content-script context. The popup talks to us via runtime messages.

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "AEGIS_AUDIT") return false;

  (async () => {
    try {
      const resp = await fetch(`${msg.apiBase}/api/v1/audit`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${msg.apiKey}`,
          "User-Agent": "AEGIS-Chrome/0.1.0",
        },
        body: JSON.stringify({ input_type: "url", input_value: msg.url }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail =
          (data && data.detail && (data.detail.message || data.detail.error)) ||
          data.message ||
          `HTTP ${resp.status}`;
        sendResponse({ ok: false, error: detail });
        return;
      }
      sendResponse({ ok: true, data });
    } catch (e) {
      sendResponse({ ok: false, error: e.message });
    }
  })();

  // Keep the message channel open for the async sendResponse.
  return true;
});
