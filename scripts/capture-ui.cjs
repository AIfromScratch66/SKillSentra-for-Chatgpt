"use strict";

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

function watchConsole(page, errors) {
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", error => errors.push(error.message));
}

async function auditViewport(page, label, { mobile = false } = {}) {
  const audit = await page.evaluate(({ mobile }) => {
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
    };
    const nameOf = element => {
      const labelledBy = element.getAttribute("aria-labelledby");
      const referenced = labelledBy ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" ") : "";
      return [
        element.getAttribute("aria-label"),
        referenced,
        [...(element.labels || [])].map(label => label.textContent).join(" "),
        element.textContent,
        element.getAttribute("alt"),
        element.getAttribute("title"),
      ].find(value => value && value.trim())?.trim() || "";
    };
    const interactive = [...document.querySelectorAll("button, a[href], input, select, textarea, summary")].filter(visible);
    const unnamed = interactive.filter(element => !nameOf(element)).map(element => element.outerHTML.slice(0, 120));
    const undersized = mobile ? interactive
      .filter(element => element.matches("button, a[href], input, select, textarea, summary, [role='button']"))
      .map(element => {
        const rect = element.getBoundingClientRect();
        return { name: nameOf(element), width: Math.round(rect.width), height: Math.round(rect.height) };
      })
      .filter(item => item.width < 44 || item.height < 44) : [];
    const smallInputs = mobile ? [...document.querySelectorAll("input, select, textarea")]
      .filter(visible)
      .map(element => ({ name: nameOf(element), fontSize: parseFloat(getComputedStyle(element).fontSize) }))
      .filter(item => item.fontSize < 16) : [];
    return {
      rootOverflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth,
      unnamed,
      undersized,
      smallInputs,
    };
  }, { mobile });
  assert.ok(audit.rootOverflow <= 1, `${label}: root overflows by ${audit.rootOverflow}px`);
  assert.deepEqual(audit.unnamed, [], `${label}: visible interactive controls need accessible names`);
  assert.deepEqual(audit.undersized, [], `${label}: mobile controls must meet the 44px target`);
  assert.deepEqual(audit.smallInputs, [], `${label}: mobile form text must remain at least 16px`);
}

async function auditPodiumLayout(page, label) {
  const cards = await page.locator(".podium-card").evaluateAll(elements => elements.map(element => {
    const publisher = element.querySelector("p")?.getBoundingClientRect();
    const meta = element.querySelector(".rank-meta")?.getBoundingClientRect();
    const metaText = element.querySelector(".rank-meta span")?.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    return {
      name: element.querySelector("h3")?.textContent || "",
      gap: publisher && metaText ? metaText.top - publisher.bottom : 0,
      contained: meta ? meta.bottom <= rect.bottom + 1 : false,
      clipped: element.scrollHeight > element.clientHeight + 1,
    };
  }));
  const failures = cards.filter(card => card.gap < 8 || !card.contained || card.clipped);
  assert.deepEqual(failures, [], `${label}: ranking cards must not collide or clip`);
}

async function auditTextContrast(page, label, selectors, minimum = 4.5) {
  const samples = await page.evaluate(selectors => {
    const channel = value => { const scaled = value / 255; return scaled <= .04045 ? scaled / 12.92 : ((scaled + .055) / 1.055) ** 2.4; };
    const rgb = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    const luminance = value => { const [r, g, b] = rgb(value); return .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b); };
    const ratio = (foreground, background) => { const a = luminance(foreground); const b = luminance(background); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05); };
    return selectors.flatMap(selector => [...document.querySelectorAll(selector)].map(element => ({ selector, element })))
      .filter(({ element }) => { const rect = element.getBoundingClientRect(); const style = getComputedStyle(element); return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden"; })
      .map(({ selector, element }) => {
        const style = getComputedStyle(element);
        return { selector, text: element.textContent.trim().slice(0, 42), foreground: style.color, background: style.backgroundColor, ratio: ratio(style.color, style.backgroundColor) };
      });
  }, selectors);
  const failures = samples.filter(sample => sample.ratio < minimum || !Number.isFinite(sample.ratio));
  assert.deepEqual(failures, [], `${label}: text contrast must be at least ${minimum}:1`);
}

async function main() {
  const baseURL = process.env.SKILLSENTRA_CAPTURE_URL || "http://127.0.0.1:8766";
  const output = path.resolve(process.argv[2] || "evidence/visual");
  fs.mkdirSync(output, { recursive: true });
  await fetch(`${baseURL}/api/v1/control/demo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,
  });
  try {
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const page = await desktop.newPage();
    const consoleErrors = [];
    watchConsole(page, consoleErrors);
    await page.goto(baseURL);
    await page.locator("#ranking").waitFor();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, "marketplace-home.png"), fullPage: true });
    await auditViewport(page, "marketplace desktop");
    await auditPodiumLayout(page, "marketplace desktop");
    const curatedCard = page.locator(".skill-card").filter({ hasText: "professional-report-writing" });
    assert.equal(await curatedCard.count(), 1, "visual acceptance requires the curated marketplace fixture; start the server with SKILLSENTRA_DEMO_SEED=true");
    await curatedCard.getByRole("button", { name: /查看|View/ }).click();
    await page.getByRole("heading", { name: /可信护照|Trust passport/ }).waitFor();
    await page.screenshot({ path: path.join(output, "marketplace-trust-passport.png"), fullPage: true });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "免费开始" }).click();
    await page.getByRole("dialog").waitFor();
    await page.screenshot({ path: path.join(output, "marketplace-register.png"), fullPage: true });
    await page.goto(`${baseURL}/studio.html`);
    await page.getByRole("button", { name: "基于模板创建" }).click();
    await page.getByText("定义", { exact: true }).first().waitFor();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, "studio-five-stage.png"), fullPage: true });
    await auditViewport(page, "studio desktop");
    await page.evaluate(() => {
      state.current.template = 5;
      renderStep();
    });
    await page.getByRole("heading", { name: "内部模拟专家矩阵" }).waitFor();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, "studio-expert-matrix.png"), fullPage: true });
    await auditViewport(page, "studio expert matrix desktop");
    await page.goto(`${baseURL}/platform.html`);
    await page.getByRole("heading", { name: "每一步，都能对账。" }).waitFor();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, "platform-overview.png"), fullPage: true });
    await auditViewport(page, "platform desktop");
    await page.getByRole("button", { name: "结算与对账" }).click();
    await page.getByRole("heading", { name: "先证明用量，再形成金额。" }).waitFor();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, "platform-settlement.png"), fullPage: true });
    await page.getByRole("button", { name: "自动扫描" }).click();
    await page.getByRole("heading", { name: "持续找到新 Skill。" }).waitFor();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, "platform-automation.png"), fullPage: true });
    assert.deepEqual(consoleErrors, [], "desktop pages must not emit console errors");
    await desktop.close();

    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 1 });
    const mobilePage = await mobile.newPage();
    const mobileErrors = [];
    watchConsole(mobilePage, mobileErrors);
    await mobilePage.goto(baseURL);
    await mobilePage.locator("#ranking").waitFor();
    await mobilePage.waitForTimeout(220);
    await mobilePage.screenshot({ path: path.join(output, "marketplace-mobile.png"), fullPage: true });
    await auditViewport(mobilePage, "marketplace mobile", { mobile: true });
    await auditPodiumLayout(mobilePage, "marketplace mobile");
    const marketplaceMenu = mobilePage.locator('summary[aria-label="打开主导航"]');
    await marketplaceMenu.click();
    await mobilePage.locator('summary[aria-label="关闭主导航"]').waitFor();
    await mobilePage.keyboard.press("Escape");
    assert.equal(await mobilePage.locator('summary[aria-label="打开主导航"]').evaluate(element => element === document.activeElement), true, "marketplace mobile menu must restore focus after Escape");
    const mobileCurated = mobilePage.locator(".skill-card").filter({ hasText: "professional-report-writing" });
    await mobileCurated.getByRole("button", { name: /查看|View/ }).click();
    await mobilePage.getByRole("heading", { name: /可信护照|Trust passport/ }).waitFor();
    await mobilePage.waitForFunction(() => document.activeElement?.classList.contains("modal-close"));
    assert.equal(await mobilePage.locator(".modal").evaluate(element => element.scrollTop), 0, "mobile trust passport must open at the top");
    assert.equal(await mobilePage.locator(".modal-close").evaluate(element => element === document.activeElement), true, "mobile modal must initially focus the close control");
    assert.equal(await mobilePage.getByText(/综合状态：部分验证|Overall status: Partially verified/).isVisible(), true, "mobile trust status must be visible on open");
    await mobilePage.screenshot({ path: path.join(output, "marketplace-mobile-trust-passport.png"), fullPage: true });
    await mobilePage.keyboard.press("Escape");
    await mobilePage.getByRole("button", { name: "免费开始" }).click();
    await mobilePage.getByRole("dialog").waitFor();
    await mobilePage.screenshot({ path: path.join(output, "marketplace-mobile-register.png"), fullPage: true });
    await mobilePage.keyboard.press("Escape");
    await mobilePage.goto(`${baseURL}/studio.html`);
    await mobilePage.getByRole("button", { name: "基于模板创建" }).click();
    await mobilePage.waitForTimeout(220);
    const studioMore = mobilePage.locator('summary[aria-label="更多操作"]');
    await studioMore.click();
    assert.equal(await mobilePage.getByRole("button", { name: "打开项目与审计中心" }).isVisible(), true, "Studio mobile must retain the project center");
    assert.equal(await mobilePage.getByRole("link", { name: "打开 Skill 市场" }).isVisible(), true, "Studio mobile must retain the marketplace");
    assert.equal(await mobilePage.getByRole("link", { name: "打开证据控制台" }).isVisible(), true, "Studio mobile must retain the control plane");
    await mobilePage.keyboard.press("Escape");
    assert.equal(await studioMore.evaluate(element => element === document.activeElement), true, "Studio mobile menu must restore focus after Escape");
    await auditViewport(mobilePage, "studio mobile", { mobile: true });
    await mobilePage.screenshot({ path: path.join(output, "studio-mobile.png"), fullPage: true });
    await mobilePage.goto(`${baseURL}/platform.html`);
    await mobilePage.getByRole("heading", { name: "每一步，都能对账。" }).waitFor();
    await mobilePage.waitForTimeout(220);
    await mobilePage.screenshot({ path: path.join(output, "platform-mobile.png"), fullPage: true });
    await auditViewport(mobilePage, "platform mobile", { mobile: true });
    assert.deepEqual(mobileErrors, [], "mobile pages must not emit console errors");
    await mobile.close();

    const narrow = await browser.newContext({ viewport: { width: 320, height: 800 }, isMobile: true, deviceScaleFactor: 1 });
    const narrowPage = await narrow.newPage();
    const narrowErrors = [];
    watchConsole(narrowPage, narrowErrors);
    await narrowPage.goto(baseURL);
    await narrowPage.locator("#ranking").waitFor();
    await auditViewport(narrowPage, "marketplace 320px", { mobile: true });
    await auditPodiumLayout(narrowPage, "marketplace 320px");
    await narrowPage.screenshot({ path: path.join(output, "marketplace-320.png"), fullPage: true });
    await narrowPage.goto(`${baseURL}/studio.html`);
    await narrowPage.getByRole("button", { name: "基于模板创建" }).click();
    await auditViewport(narrowPage, "studio 320px", { mobile: true });
    await narrowPage.screenshot({ path: path.join(output, "studio-320.png"), fullPage: true });
    await narrowPage.goto(`${baseURL}/platform.html`);
    await narrowPage.getByRole("heading", { name: "每一步，都能对账。" }).waitFor();
    await narrowPage.waitForTimeout(220);
    await auditViewport(narrowPage, "platform 320px", { mobile: true });
    await narrowPage.screenshot({ path: path.join(output, "platform-320.png"), fullPage: true });
    assert.deepEqual(narrowErrors, [], "320px pages must not emit console errors");
    await narrow.close();

    const dense = await browser.newContext({ viewport: { width: 720, height: 500 }, deviceScaleFactor: 2 });
    const densePage = await dense.newPage();
    const denseErrors = [];
    watchConsole(densePage, denseErrors);
    for (const route of ["", "/studio.html", "/platform.html"]) {
      await densePage.goto(`${baseURL}${route}`);
      await densePage.waitForTimeout(220);
      await auditViewport(densePage, `${route || "/"} 720px reflow at 2x pixel density`, { mobile: true });
    }
    assert.deepEqual(denseErrors, [], "2x pixel-density reflow must not emit console errors");
    await dense.close();

    const dark = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark", reducedMotion: "reduce" });
    const darkPage = await dark.newPage();
    const darkErrors = [];
    watchConsole(darkPage, darkErrors);
    await darkPage.goto(baseURL);
    await darkPage.locator("#ranking").waitFor();
    await auditViewport(darkPage, "marketplace dark");
    await auditTextContrast(darkPage, "marketplace dark", [".hero .primary", ".category-row button:not(.is-active)"]);
    await darkPage.screenshot({ path: path.join(output, "marketplace-dark.png"), fullPage: true });
    await darkPage.goto(`${baseURL}/studio.html`);
    await darkPage.getByRole("button", { name: "基于模板创建" }).click();
    await auditViewport(darkPage, "studio dark");
    await auditTextContrast(darkPage, "studio dark", [".ai-run-button", ".primary-button"]);
    await darkPage.screenshot({ path: path.join(output, "studio-dark.png"), fullPage: true });
    await darkPage.goto(`${baseURL}/platform.html`);
    await darkPage.getByRole("heading", { name: "每一步，都能对账。" }).waitFor();
    await auditViewport(darkPage, "platform dark");
    await auditTextContrast(darkPage, "platform dark", [".primary-button"]);
    await darkPage.screenshot({ path: path.join(output, "platform-dark.png"), fullPage: true });
    assert.deepEqual(darkErrors, [], "dark pages must not emit console errors");
    await dark.close();
  } finally {
    await browser.close();
  }
  process.stdout.write(`${output}\n`);
}

main().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
