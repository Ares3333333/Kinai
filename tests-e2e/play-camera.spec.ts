import { expect, test } from "@playwright/test";

async function seedConsent(page) {
  await page.addInitScript(() => {
    localStorage.setItem("kinaesthetic_consent_v1", JSON.stringify({
      accepted_at: new Date().toISOString(),
      raw_video_local: true,
      derived_signals_allowed: true,
      no_medical_claims: true,
    }));
  });
}

test.describe("player camera UX", () => {
  test("starts fake camera and hides placeholder", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "chromium-fake-camera", "requires fake media project");
    await seedConsent(page);
    await page.goto("/play?tester=e2e&game=qa");
    await page.locator("#startProductDemo").click();

    await expect(page.locator("#cameraPreview")).toBeVisible();
    await expect(page.locator("#cameraPlaceholder")).toBeHidden({ timeout: 12_000 });
    await expect(page.locator("#cameraState")).toHaveText("active", { timeout: 12_000 });
    await expect(page.locator("#runtimeMode")).not.toHaveText("OFFLINE", { timeout: 12_000 });

    const hasStream = await page.evaluate(() => Boolean((document.querySelector("#cameraPreview") as HTMLVideoElement | null)?.srcObject));
    expect(hasStream).toBeTruthy();
  });

  test("shows clear recovery UX when camera permission is blocked", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "chromium-fake-camera", "fake media project grants camera");
    await seedConsent(page);
    await page.addInitScript(() => {
      const error = Object.assign(new Error("blocked by test"), { name: "NotAllowedError" });
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia: () => Promise.reject(error) },
      });
    });
    await page.goto("/play?tester=e2e&game=qa");
    await page.locator("#startProductDemo").click();

    await expect(page.locator("#cameraState")).toHaveText("no access");
    await expect(page.locator("#cameraPlaceholder")).toBeVisible();
    await expect(page.locator("#cameraHelpPanel")).toBeVisible();
    await expect(page.locator("#cameraHelpPanel")).toContainText("Camera blocked");
    await expect(page.locator("#runtimeMode")).toHaveText("OFFLINE");
    await expect(page.locator("#productStatus")).not.toContainText("LIVE");
  });
});
