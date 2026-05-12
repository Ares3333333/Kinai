import { expect, test } from "@playwright/test";

test("evidence stays honest and usable when public APIs fail", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => consoleErrors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error" && !msg.text().startsWith("Failed to load resource:")) {
      consoleErrors.push(msg.text());
    }
  });
  await page.route("**/api/system-health", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ ok: false, error: "test_backend_down" }),
  }));
  await page.route("**/api/evidence", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ ok: false, error: "test_backend_down" }),
  }));
  await page.goto("/evidence");

  await expect(page.locator("#runtimeMode")).toContainText(/PUBLIC DEMO|OFFLINE|DEMO/);
  await expect(page.locator("#proofHeadline")).toContainText(/Demo proof|proof/i);
  await expect(page.locator("#exportPitchPackage")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("LIVE: evidence is based on fresh live signals.");
  expect(consoleErrors).toEqual([]);
});

test("landing exposes social metadata for investor sharing", async ({ page }) => {
  await page.goto("/");
  const meta = await page.evaluate(() => ({
    title: document.title,
    description: document.querySelector('meta[name="description"]')?.getAttribute("content") || "",
    ogTitle: document.querySelector('meta[property="og:title"]')?.getAttribute("content") || "",
    ogImage: document.querySelector('meta[property="og:image"]')?.getAttribute("content") || "",
    twitter: document.querySelector('meta[name="twitter:card"]')?.getAttribute("content") || "",
  }));

  expect(meta.title).toContain("Kinaesthetic AI");
  expect(meta.description).toContain("anti-tilt");
  expect(meta.ogTitle).toContain("Kinaesthetic AI");
  expect(meta.ogImage).toContain("kai-ai-scan-player");
  expect(meta.twitter).toBe("summary_large_image");
});
