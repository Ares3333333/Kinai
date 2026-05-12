import { expect, test } from "@playwright/test";

const ROUTES = ["/play", "/play?investor_mode=1"];

for (const route of ROUTES) {
  test(`player layout has no horizontal overflow or card overlap: ${route}`, async ({ page }) => {
    await page.goto(route);

    await expect(page.locator("#startProductDemo")).toBeVisible();
    await expect(page.locator(".camera-stage")).toBeVisible();

    const report = await page.evaluate(() => {
      const doc = document.documentElement;
      const viewportWidth = window.innerWidth;
      const rect = (selector: string) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const box = node.getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height };
      };
      const camera = rect(".camera-product");
      const metrics = rect(".metrics-product");
      const stage = rect(".camera-stage");
      const video = rect("#cameraPreview");
      const overlap = camera && metrics
        ? !(camera.right <= metrics.left || metrics.right <= camera.left || camera.bottom <= metrics.top || metrics.bottom <= camera.top)
        : false;
      const videoInsideStage = stage && video
        ? video.left >= stage.left - 1 && video.right <= stage.right + 1 && video.top >= stage.top - 1 && video.bottom <= stage.bottom + 1
        : false;
      return {
        viewportWidth,
        scrollWidth: doc.scrollWidth,
        camera,
        metrics,
        stage,
        video,
        overlap,
        videoInsideStage,
      };
    });

    expect(report.scrollWidth, `${route} should not horizontally overflow`).toBeLessThanOrEqual(report.viewportWidth + 1);
    expect(report.stage?.width || 0).toBeGreaterThan(200);
    expect(report.stage?.height || 0).toBeGreaterThan(120);
    expect(report.overlap, `${route} camera and metrics cards overlap`).toBeFalsy();
    expect(report.videoInsideStage, `${route} video exceeds camera stage bounds`).toBeTruthy();
  });
}
