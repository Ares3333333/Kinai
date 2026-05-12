import { test, expect } from "@playwright/test";

// Smoke checks for every public surface. The goal is to fail fast on
// regressions: page renders, key element appears, no console error.
//
// We don't need a camera; /play renders the engine-offline banner and
// the layout shell when the cockpit is not live, which is exactly what
// most testers see on first open.

const PAGES: Array<{ path: string; expectSelector: string }> = [
  { path: "/", expectSelector: ".cta-primary, a[href='/play?autostart=1']" },
  { path: "/play", expectSelector: "#tiltValue, #startProductDemo" },
  { path: "/camera-check", expectSelector: "#runCameraCheck, #checkResult" },
  { path: "/demo", expectSelector: "#startInvestorDemo, .investor-demo-grid" },
  { path: "/evidence", expectSelector: "#exportPitchPackage, .evidence-onepager" },
  { path: "/pitch", expectSelector: "h1, .pitch-hero, .content-hero" },
  { path: "/metrics", expectSelector: "h1, .metrics-grid, body" },
  { path: "/privacy", expectSelector: "h1" },
  { path: "/admin", expectSelector: "#adminStatusPill" },
];

for (const { path, expectSelector } of PAGES) {
  test(`renders ${path}`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (err) => consoleErrors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    const response = await page.goto(path);
    expect(response?.ok(), `${path} should return 2xx`).toBeTruthy();
    await expect(page.locator(expectSelector).first()).toBeVisible();

    // Console-error budget: we allow zero hard errors. Network 4xx noise
    // (e.g. /api/cohort-summary on a fresh data dir) does not produce
    // pageerror events, so this assertion stays meaningful.
    expect(consoleErrors, `${path} produced JS errors: ${consoleErrors.join("\n")}`).toEqual([]);
  });
}

test("api health endpoints respond", async ({ request }) => {
  const healthz = await request.get("/healthz");
  expect(healthz.ok()).toBeTruthy();
  const engine = await request.get("/api/engine-health");
  expect(engine.ok()).toBeTruthy();
  const admin = await request.get("/api/admin-summary");
  expect(admin.ok()).toBeTruthy();
  const adminBody = await admin.json();
  expect(adminBody.security).toBeTruthy();
  expect(adminBody.security.consent_version).toBe("v1");
});

test("write endpoints reject anonymous POST when auth is on (best-effort)", async ({ request }) => {
  // We don't know if auth is enabled in this environment — but we can
  // still verify that the backend either accepts the request (auth off)
  // or rejects with the documented error codes (401 / 403). Anything
  // else is a regression.
  const response = await request.post("/api/feedback", {
    headers: { "Content-Type": "application/json" },
    data: { source: "playwright_smoke", helped: true },
  });
  expect([200, 401, 403, 429]).toContain(response.status());
});

test("SSE stream produces at least one state event", async ({ page }) => {
  // We can't easily consume EventSource without a browser context, so
  // run a tiny inline page that opens the stream and reports the first
  // event back via window.__sseFirstEvent.
  await page.goto("/");
  await page.setContent(
    `<!doctype html><html><body><pre id="out">waiting</pre>
     <script>
       const out = document.getElementById('out');
       const es = new EventSource('/api/session-stream');
       window.__sseFirstEvent = null;
       es.addEventListener('state', (event) => {
         window.__sseFirstEvent = event.data;
         out.textContent = event.data;
         es.close();
       });
       es.addEventListener('error', (err) => { out.textContent = 'error'; });
     </script></body></html>`
  );
  await page.waitForFunction(() => Boolean((window as any).__sseFirstEvent), null, { timeout: 8_000 });
  const data = await page.evaluate(() => (window as any).__sseFirstEvent);
  const parsed = JSON.parse(data);
  expect(parsed).toHaveProperty("mode");
});

test("demo lock flow marks proof export", async ({ page }) => {
  await page.goto("/play?demo_lock_test=1");
  await page.waitForSelector("#demoLockBtn");
  await page.evaluate(() => {
    const w = window as any;
    if (typeof w.__runDemoLockTestMode === "function") {
      w.__runDemoLockTestMode();
    } else {
      (document.querySelector("#demoLockBtn") as HTMLButtonElement | null)?.click();
    }
  });
  await expect(page.locator("#productStatus")).toContainText("Demo lock завершён", { timeout: 6000 });
  await expect(page.locator("#saveStatus")).toContainText("proof экспортирован", { timeout: 6000 });
  const marker = await page.evaluate(() => localStorage.getItem("kinaesthetic_last_proof_export_at"));
  expect(marker).toBeTruthy();
});
