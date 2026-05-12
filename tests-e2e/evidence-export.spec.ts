import { expect, test } from "@playwright/test";

test("evidence export downloads client-side proof package when backend export fails", async ({ page }) => {
  await page.route("**/api/export-founder-deck", (route) => route.abort());
  await page.goto("/evidence");

  const downloadPromise = page.waitForEvent("download");
  await page.locator("#exportPitchPackage").click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  expect(stream).toBeTruthy();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));

  expect(payload.package_type).toBe("investor_evidence_package");
  expect(payload.backend_export.mode).toBe("client_only");
  expect(payload.privacy.raw_video).toBe("not stored");
  expect(payload.privacy.raw_audio).toBe("not stored");
  expect(payload.privacy.camera_frames).toBe("not stored");
  await expect(page.locator("#exportSuccess")).toBeVisible();
  await expect(page.locator("#exportStatus")).toContainText(/Backend not required|public demo mode|raw media/i);
});

test("evidence export marks backend+browser package when backend export is available", async ({ page }) => {
  await page.route("**/api/export-founder-deck", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ ok: true, export_dir: "test-export", raw_media_included: false }),
  }));
  await page.goto("/evidence");

  const downloadPromise = page.waitForEvent("download");
  await page.locator("#exportPitchPackage").click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  expect(stream).toBeTruthy();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));

  expect(payload.source).toBe("backend+browser");
  expect(payload.backend_export.export_dir).toBe("test-export");
  expect(payload.privacy.raw_video).toBe("not stored");
  await expect(page.locator("#exportSuccess")).toBeVisible();
});
