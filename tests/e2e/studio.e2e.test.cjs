const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { chromium } = require("playwright");

const projectRoot = path.resolve(__dirname, "..", "..");
const port = 18766;
const baseURL = `http://127.0.0.1:${port}`;
let server;
let browser;
let tempDir;

function resolvePythonCommand() {
  const candidates = [
    process.env.SKILLSENTRA_PYTHON,
    process.env.PYTHON,
    path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe"),
    "python",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (!path.isAbsolute(candidate) || fs.existsSync(candidate)) return candidate;
  }
  return "python";
}

async function assertFocusRing(page, locator) {
  await page.evaluate(() => document.activeElement?.blur());
  let reached = false;
  for (let index = 0; index < 40; index += 1) {
    await page.keyboard.press("Tab");
    reached = await locator.evaluate(element => element === document.activeElement);
    if (reached) break;
  }
  assert.equal(reached, true, "keyboard navigation must reach the target");
  const focus = await locator.evaluate(element => {
    const style = getComputedStyle(element);
    return { width: parseFloat(style.outlineWidth), style: style.outlineStyle, color: style.outlineColor };
  });
  assert.ok(focus.width >= 3 && focus.style !== "none", `expected a visible 3px focus ring, got ${JSON.stringify(focus)}`);
}

async function ensureMarketplaceRanking(page) {
  await page.waitForFunction(() => document.querySelector(".podium-card") || document.querySelector("#ranking-body")?.textContent.includes("近 7 天暂无足够真实信号"));
  if (await page.locator(".podium-card").count()) return;

  await page.getByRole("button", { name: "免费开始" }).click();
  await page.getByLabel("显示名称").fill("Mobile Ranking Fixture");
  await page.getByLabel("邮箱").fill("mobile-ranking@example.com");
  await page.getByLabel("密码", { exact: true }).fill("BrowserPass123");
  await page.getByLabel("确认密码", { exact: true }).fill("BrowserPass123");
  await page.getByLabel(/我了解当前为受控 Private Beta/).check();
  await page.getByRole("button", { name: "创建账户" }).click();
  await page.getByText("个人工作区已创建").waitFor();

  const curatedCard = page.locator(".skill-card").filter({ hasText: "professional-report-writing" });
  await curatedCard.getByRole("button", { name: /查看/ }).click();
  await page.getByRole("button", { name: "查看并免费获取" }).click();
  await page.locator("#purchase-confirm-form input[type=checkbox]").check();
  await page.getByRole("button", { name: "确认获取" }).click();
  await page.getByText("已加入个人资料库").waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "当前账户" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByText("已退出登录").waitFor();
  await page.reload();
  await page.locator(".podium-card").filter({ hasText: "professional-report-writing" }).waitFor();
}

async function waitForServer() {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseURL}/api/health`);
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error("SkillSentra test server did not become ready.");
}

test.before(async () => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "skillsentra-e2e-"));
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    SKILLSENTRA_DEMO_SEED: "true",
    SKILLSENTRA_ARTIFACT_DIR: path.join(tempDir, "artifacts"),
    SKILLSENTRA_ALLOWED_SKILL_ROOTS: path.join(projectRoot, "sample-skills"),
  };
  server = spawn(resolvePythonCommand(), [
    "-m",
    "app.server",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--database",
    path.join(tempDir, "e2e.db"),
  ], { cwd: projectRoot, env, stdio: ["ignore", "pipe", "pipe"] });
  server.once("exit", code => {
    if (code && code !== 0) process.stderr.write(`SkillSentra test server exited with ${code}.\n`);
  });
  await waitForServer();
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,
  });
});

test.after(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    server.kill();
    await Promise.race([once(server, "exit"), new Promise(resolve => setTimeout(resolve, 3000))]);
  }
  if (tempDir) fs.rmSync(tempDir, { recursive: true, force: true });
});

test("template route saves the first stage and opens the project ledger", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  const response = await page.goto(`${baseURL}/studio.html`);

  assert.equal(response.status(), 200);
  assert.match(response.headers()["content-security-policy"], /frame-ancestors 'none'/);
  await page.getByRole("button", { name: "基于模板创建" }).click();
 assert.equal(await page.locator("#step-list .stage-group").count(), 5);
  assert.deepEqual(await page.locator("#step-list .stage-label").allTextContents(), ["阶段 01 / 05", "阶段 02 / 05", "阶段 03 / 05", "阶段 04 / 05", "阶段 05 / 05"]);
  assert.deepEqual(await page.locator("#step-list .step-index").allTextContents(), ["1.1", "1.2", "2.1", "2.2", "2.3", "3.1", "3.2", "4.1", "5.1"]);
  assert.equal(await page.locator("#step-list .step-link").first().getAttribute("aria-label"), "阶段 1，步骤 1，明确目标，当前");
 await page.getByText("定义", { exact: true }).first().waitFor();
  await page.getByRole("button", { name: "继续", exact: true }).click();
  const requiredName = page.getByLabel("Skill 名称");
  assert.equal(await requiredName.getAttribute("aria-invalid"), "true");
  assert.equal(await requiredName.evaluate(element => element === document.activeElement), true);
  await page.getByLabel("Skill 名称").fill("browser-test-skill");
  await page.getByLabel("主要使用者").fill("测试人员");
  await page.getByLabel("要解决的问题").fill("把输入整理成可验证的 Skill 候选。");
  await page.getByLabel("成功输出").fill("生成结构清晰且可复核的候选版本。");
  await page.getByRole("button", { name: "继续", exact: true }).click();
  await page.getByRole("heading", { name: "借鉴比较" }).waitFor();

  await page.locator('summary[aria-label="更多操作"]').click();
  await page.getByRole("button", { name: "打开项目与审计中心" }).click();
  await page.getByRole("heading", { name: "项目中心" }).waitFor();
  await page.getByText("browser-test-skill").first().waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("heading", { name: "借鉴比较" }).waitFor();

  assert.deepEqual(consoleErrors, []);
  await context.close();
});

test("product evidence card separates proposal, human decision and verification", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${baseURL}/studio.html`);
  await page.getByRole("button", { name: "基于模板创建" }).click();
  await page.evaluate(() => {
    state.current.template = 5;
    renderStep();
  });

  await page.getByRole("heading", { name: "协作与验证证据" }).waitFor();
  assert.equal(await page.getByText("先生成候选版本，再绑定验证证据").isVisible(), true);
  assert.equal(await page.getByText("内部模拟专家矩阵", { exact: true }).count(), 0);
  assert.equal(await page.getByText("本步尚无真实协作结果", { exact: true }).isVisible(), true);
  assert.equal(await page.getByText("待人工审阅", { exact: true }).isVisible(), true);
  assert.equal(await page.getByText("未由本卡片授权", { exact: true }).isVisible(), true);
  assert.equal(await page.getByText("10.0000", { exact: true }).count(), 0, "missing evidence must not render a placeholder score");
  await context.close();
});

test("product evidence is bound to the active version and does not expose internal scores", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${baseURL}/studio.html`);
  await page.getByRole("button", { name: "基于模板创建" }).click();
  await page.evaluate(() => {
    state.current.template = 5;
    state.backend.available = true;
    state.backend.projectId = "project-review-scope";
    state.backend.activeVersionId = "version-current";
    state.backend.versions = [
      { id: "version-current", version: "0.1.0-candidate.2", artifact_digest: "sha256:" + "a".repeat(64) },
      { id: "version-old", version: "0.1.0-candidate.1", artifact_digest: "sha256:" + "b".repeat(64) }
    ];
    state.backend.expertReviews = [{ artifact_version_id: "version-old", artifact_digest: "sha256:" + "b".repeat(64), raw_score: 9.8, capped_score: 7.9, decision: "NO_GO", evidence_ceiling: "E2", gates: {} }];
    renderStep();
  });
  await page.getByRole("heading", { name: "协作与验证证据" }).waitFor();
  assert.equal(await page.getByText("当前版本暂无验证记录").isVisible(), true);
  assert.equal(await page.getByText("9.8000", { exact: true }).count(), 0, "an old-version review must not be shown for the current version");
  assert.equal(await page.getByRole("button", { name: "刷新验证状态" }).isVisible(), true);
  await context.close();
});

test("Chinese and English system pages switch and persist", async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${baseURL}/studio.html`);

  await page.getByRole("button", { name: "Switch to English" }).click();
  assert.equal(await page.locator("html").getAttribute("lang"), "en");
  const productSwitcher = page.locator('summary[aria-label="Switch SkillSentra products"]');
  await assertFocusRing(page, productSwitcher);
  await productSwitcher.click();
  const marketplaceLink = page.getByRole("link", { name: "Open Skill marketplace" });
  await marketplaceLink.waitFor();
  await assertFocusRing(page, marketplaceLink);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Create from template" }).waitFor();
  await page.reload();
  await page.getByRole("button", { name: "Create from template" }).waitFor();
  assert.equal(await page.locator("html").getAttribute("lang"), "en");

  await page.getByRole("button", { name: "Switch to Chinese" }).click();
  await page.getByRole("button", { name: "基于模板创建" }).waitFor();
  assert.equal(await page.locator("html").getAttribute("lang"), "zh-CN");
  await context.close();
});

test("mobile controls meet the 44 point target and the dialog traps focus", async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const page = await context.newPage();
  await page.goto(`${baseURL}/studio.html`);

  const undersized = await page.locator("button:visible").evaluateAll(elements => elements
    .map(element => {
      const rect = element.getBoundingClientRect();
      return { text: element.getAttribute("aria-label") || element.textContent.trim(), width: rect.width, height: rect.height };
    })
    .filter(item => item.width < 44 || item.height < 44));
  assert.deepEqual(undersized, []);

  const sidebarToggle = page.getByRole("button", { name: "显示或隐藏步骤列表" });
  assert.equal(await sidebarToggle.getAttribute("aria-expanded"), "false");
  await sidebarToggle.click();
  assert.equal(await page.locator("#sidebar-scrim").isVisible(), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#sidebar-scrim").isVisible(), false);
  assert.equal(await sidebarToggle.evaluate(element => element === document.activeElement), true);

  const moreActions = page.locator('summary[aria-label="更多操作"]');
  await assertFocusRing(page, moreActions);
  await moreActions.click();
  assert.equal(await page.getByRole("button", { name: "打开项目与审计中心" }).isVisible(), true);
  assert.equal(await page.getByRole("link", { name: "打开 Skill 市场" }).isVisible(), true);
  assert.equal(await page.getByRole("link", { name: "打开证据控制台" }).isVisible(), true);
  await page.keyboard.press("Escape");
  assert.equal(await moreActions.evaluate(element => element === document.activeElement), true);

  await page.getByRole("button", { name: "基于模板创建" }).click();
  const successfulOutput = page.getByLabel("成功输出");
  await successfulOutput.scrollIntoViewIfNeeded();
  await successfulOutput.focus();
  const clearance = await page.evaluate(() => {
    const field = document.querySelector('[data-bind="success"]').getBoundingClientRect();
    const actions = document.querySelector("#mobile-actions").getBoundingClientRect();
    return { fieldBottom: field.bottom, actionsTop: actions.top };
  });
  assert.ok(clearance.fieldBottom <= clearance.actionsTop, `focused field must remain above mobile actions: ${JSON.stringify(clearance)}`);

  const immediateDialogFocus = await page.getByRole("button", { name: "打开 AI 控制中心" }).evaluate(element => {
    element.click();
    return document.activeElement?.id;
  });
  assert.equal(immediateDialogFocus, "sheet-close", "dialog must establish initial focus synchronously");
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  assert.equal(await dialog.getAttribute("aria-modal"), "true");
  await page.waitForFunction(() => document.activeElement?.id === "sheet-close");
  await page.evaluate(() => document.activeElement?.blur());
  assert.equal(await dialog.locator(":focus").count(), 0, "fixture must exercise focus recovery from outside the dialog");
  await page.keyboard.press("Shift+Tab");
  assert.equal(await dialog.locator(":focus").count(), 1);
  assert.equal(await dialog.locator("[data-close-sheet]").evaluate(element => element === document.activeElement), true);
  await page.keyboard.press("Escape");
  assert.equal(await dialog.isVisible(), false);
  await context.close();
});

test("control plane completes the evidence-to-settlement golden path", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.goto(`${baseURL}/platform.html`);

  await page.locator(".system-state").getByText("证据链尚未闭合", { exact: true }).waitFor();
  await page.getByRole("heading", { name: "每一步，都能对账。" }).waitFor();
  await page.getByRole("heading", { name: "证据链尚未闭合" }).waitFor();
  await page.getByRole("progressbar", { name: "本地证据就绪度" }).waitFor();
  assert.equal(await page.getByRole("progressbar", { name: "本地证据就绪度" }).getAttribute("aria-valuenow"), "0");
  await page.getByRole("button", { name: "下一步：固定制品" }).waitFor();
  await page.getByRole("button", { name: "运行黄金路径" }).click();
  await page.getByText("黄金路径已完成并留下完整证据。").waitFor();
  await page.getByRole("heading", { name: "证据链尚未闭合" }).waitFor();
  assert.equal(await page.getByRole("progressbar", { name: "本地证据就绪度" }).getAttribute("aria-valuenow"), "75");
  await page.getByRole("button", { name: "下一步：复核证据" }).waitFor();
  await page.getByRole("button", { name: "结算与对账" }).click();
  await page.getByRole("heading", { name: "先证明用量，再形成金额。" }).waitFor();
  await page.getByText("合格", { exact: true }).first().waitFor();
  await page.getByRole("button", { name: "生成结算单" }).click();
  await page.getByRole("heading", { name: "确认生成结算单" }).waitFor();
  await page.locator("#impact-confirm-form input[type=checkbox]").check();
  await page.getByRole("button", { name: "确认生成" }).click();
  await page.getByText("结算单已签发。").waitFor();
  await page.getByRole("button", { name: "支付沙箱" }).click();
  await page.getByRole("heading", { name: "确认沙箱支付" }).waitFor();
  await page.locator("#impact-confirm-form input[type=checkbox]").check();
  await page.getByRole("button", { name: "确认沙箱支付" }).click();
  await page.getByText("支付沙箱已完成；未发生真实资金划转。").waitFor();
  await page.getByRole("button", { name: "审计" }).click();
  await page.getByText("审计链完整").waitFor();

  assert.deepEqual(consoleErrors, []);
  await context.close();
});

test("control plane remains keyboard-sized on mobile", async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const page = await context.newPage();
  await page.goto(`${baseURL}/platform.html`);
  await page.getByRole("heading", { name: "每一步，都能对账。" }).waitFor();
  const undersized = await page.locator("button:visible").evaluateAll(elements => elements
    .map(element => {
      const rect = element.getBoundingClientRect();
      return { text: element.getAttribute("aria-label") || element.textContent.trim(), width: rect.width, height: rect.height };
    })
    .filter(item => item.width < 44 || item.height < 44));
  assert.deepEqual(undersized, []);
  const menu = page.getByRole("button", { name: "打开导航" });
  await menu.click();
  assert.equal(await page.locator("#nav-scrim").isVisible(), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#nav-scrim").isVisible(), false);
  assert.equal(await menu.evaluate(element => element === document.activeElement), true);
  await context.close();
});

test("marketplace supports registration and opens a personal creator center", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.goto(baseURL);

  await page.locator("#ranking").waitFor();
  await page.getByText("近 7 天暂无足够真实信号").waitFor();
  assert.equal(await page.locator(".podium-card").count(), 0, "zero weekly signals must not create a ranking");
  await page.getByRole("button", { name: "免费开始" }).click();
  await page.getByLabel("显示名称").fill("Browser Creator");
  await page.getByLabel("邮箱").fill("browser-creator@example.com");
  await page.getByLabel("密码", { exact: true }).fill("BrowserPass123");
  await page.getByLabel("确认密码", { exact: true }).fill("BrowserPass123");
  await page.getByLabel(/我了解当前为受控 Private Beta/).check();
  await page.getByRole("button", { name: "创建账户" }).click();
  await page.getByText("个人工作区已创建").waitFor();
  await page.getByRole("button", { name: "创作者中心", exact: true }).click();
  await page.getByRole("heading", { name: "发布与收入中心" }).waitFor();
  await page.getByText("请先在 Studio 完成创建、验证和交付。").waitFor();
  await page.keyboard.press("Escape");

  const curatedCard = page.locator(".skill-card").filter({ hasText: "professional-report-writing" });
  await curatedCard.getByRole("button", { name: /查看/ }).click();
  await page.getByRole("heading", { name: "可信护照" }).waitFor();
  await page.getByText("静态已验证", { exact: true }).waitFor();
  await page.getByText("综合状态：部分验证", { exact: true }).waitFor();
  await page.getByText("静态门禁通过", { exact: true }).waitFor();
  await page.getByRole("button", { name: "查看并免费获取" }).click();
  await page.getByRole("heading", { name: "确认免费获取" }).waitFor();
  await page.locator("#purchase-confirm-form input[type=checkbox]").check();
  await page.getByRole("button", { name: "确认获取" }).click();
  await page.getByText("已加入个人资料库").waitFor();
  await page.reload();
  await page.locator(".podium-card").filter({ hasText: "professional-report-writing" }).waitFor();

  assert.deepEqual(consoleErrors, []);
  await context.close();
});

test("marketplace mobile controls meet the 44 point target and trap modal focus", async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const page = await context.newPage();
  await page.goto(baseURL);
  await page.locator("#ranking").waitFor();
  await ensureMarketplaceRanking(page);

  const undersized = await page.locator("button:visible").evaluateAll(elements => elements
    .map(element => {
      const rect = element.getBoundingClientRect();
      return { text: element.getAttribute("aria-label") || element.textContent.trim(), width: rect.width, height: rect.height };
    })
    .filter(item => item.width < 44 || item.height < 44));
  assert.deepEqual(undersized, []);

  const mobileMenu = page.locator('summary[aria-label="打开主导航"]');
  await mobileMenu.click();
  await page.locator('summary[aria-label="关闭主导航"]').waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.locator('summary[aria-label="打开主导航"]').evaluate(element => element === document.activeElement), true);

  const rankedSkill = page.locator(".podium-card").first();
  assert.equal(await rankedSkill.evaluate(element => element.tagName), "BUTTON");
  await rankedSkill.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "可信护照" }).waitFor();
  await page.waitForFunction(() => document.activeElement?.classList.contains("modal-close"));
  assert.equal(await page.locator(".modal").evaluate(element => element.scrollTop), 0);
  assert.equal(await page.locator(".modal-close").evaluate(element => element === document.activeElement), true);
  assert.equal(await page.getByText("综合状态：部分验证", { exact: true }).isVisible(), true);
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "免费开始" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  await page.getByLabel("显示名称").fill("Mobile Creator");
  await page.getByLabel("邮箱").fill("mobile-creator@example.com");
  await page.getByLabel("密码", { exact: true }).fill("BrowserPass123");
  const confirmPassword = page.getByLabel("确认密码", { exact: true });
  await confirmPassword.fill("DifferentPass123");
  await page.getByLabel(/我了解当前为受控 Private Beta/).check();
  await page.getByRole("button", { name: "创建账户" }).click();
  assert.equal(await confirmPassword.getAttribute("aria-invalid"), "true");
  assert.equal(await confirmPassword.evaluate(element => element === document.activeElement), true);
  await confirmPassword.fill("BrowserPass123");
  assert.equal(await confirmPassword.getAttribute("aria-invalid"), null);
  await dialog.locator(".modal-close").focus();
  await page.keyboard.press("Shift+Tab");
  assert.equal(await dialog.locator(":focus").count(), 1);
  await page.keyboard.press("Escape");
  assert.equal(await dialog.isVisible(), false);
  await context.close();
});

test("marketplace keeps first-party catalog visible when GitHub candidates fail", async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await page.route("**/api/v1/discovery/github/top-skills", route => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "github_unavailable", message: "GitHub candidates are unavailable" } }),
  }));

  await page.goto(baseURL);
  await page.locator(".skill-card").first().waitFor();
  assert.ok(await page.locator(".skill-card").count() > 0, "first-party catalog should survive a GitHub-source failure");
  await page.getByText(/部分内容暂时无法刷新/).waitFor();
  await context.close();
});

test("marketplace keeps a recoverable catalog error until keyboard retry succeeds", async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  let publicationAttempts = 0;
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.route("**/api/v1/marketplace/publications", async route => {
    publicationAttempts += 1;
    if (publicationAttempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "catalog_unavailable", message: "Catalog fixture unavailable" } }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto(baseURL);
  const discover = page.locator("#discover");
  const status = page.locator("#catalog-status");
  await page.getByRole("heading", { name: "Skill 目录暂时无法加载" }).waitFor();
  assert.equal(await discover.isVisible(), true);
  assert.equal(await discover.getAttribute("aria-busy"), "false");
  assert.equal(await status.getAttribute("role"), "alert");
  assert.equal(await status.isVisible(), true);

  const retry = page.getByRole("button", { name: "重试加载 Skill" });
  await assertFocusRing(page, retry);
  await page.keyboard.press("Enter");
  await page.locator(".skill-card").first().waitFor();
  assert.equal(publicationAttempts, 2);
  assert.equal(await discover.getAttribute("aria-busy"), "false");
  assert.equal(await status.isHidden(), true);
  assert.deepEqual(consoleErrors.filter(message => !message.includes("status of 503")), []);
  assert.deepEqual(pageErrors, []);
  await context.close();
});

test("administrators can configure automated GitHub discovery without starting external code", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  let markInitialOverviewStarted;
  let releaseInitialOverview;
  const initialOverviewStarted = new Promise(resolve => { markInitialOverviewStarted = resolve; });
  const initialOverviewBlocked = new Promise(resolve => { releaseInitialOverview = resolve; });
  let overviewRequests = 0;
  await page.route("**/api/v1/control/overview", async route => {
    overviewRequests += 1;
    if (overviewRequests === 1) {
      markInitialOverviewStarted();
      await initialOverviewBlocked;
    }
    await route.continue();
  });
  await page.goto(`${baseURL}/platform.html`);
  await initialOverviewStarted;
  await page.getByRole("button", { name: "自动扫描" }).click();
  await page.getByRole("heading", { name: "持续找到新 Skill。" }).waitFor();
  const staleOverviewResponse = page.waitForResponse(response => response.url().endsWith("/api/v1/control/overview"));
  releaseInitialOverview();
  const response = await staleOverviewResponse;
  await response.finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.getByRole("heading", { name: "持续找到新 Skill。" }).waitFor();
  await page.getByLabel("目标名称").fill("Browser catalog");
  await page.getByLabel("来源地址").fill("https://github.com/example/skills");
  await page.getByRole("button", { name: "添加扫描目标" }).click();
  await page.getByText("自动扫描目标已添加。").waitFor();
  await page.getByText("Browser catalog").waitFor();
  await page.getByText("https://github.com/example/skills").waitFor();
  await context.close();
});

test("synthetic API contract covers all 16 steps with real-only requests and exact usage", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const requests = [];
  await page.route("**/api/v1/projects/*/ai-policy", route => route.fulfill({ json: { data: {} } }));
  await page.route("**/api/v1/projects/*/ai-orchestrations", async route => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    const id = `synthetic-${payload.route}-${payload.step_index}`;
    const runs = ["creator", "evaluator", "safety"].map(role => ({ id: `${id}-${role}`, role, status: "completed", connection_provider: "openai", provider_model: "synthetic-test-model", provider_request_id: `${id}-${role}`, output: { summary: `Synthetic contract ${role} / ${payload.route} / ${payload.step_index}` }, input_tokens: 12, output_tokens: 8, total_tokens: 20, usage_status: "reported", cost_status: "estimated_local_pricebook" }));
    await route.fulfill({ json: { data: { id, status: "completed", runs, attempts: runs, total_cost: 0.001, currency: "USD" } } });
  });
  await page.goto(`${baseURL}/studio.html`);
  await page.waitForFunction(() => state.backend.available);
  for (const route of ["template", "existing"]) {
    await page.evaluate(async route => {
      const project = await apiRequest("/api/v1/projects", { method: "POST", body: { name: `synthetic-ui-${route}`, route } });
      state.backend.projectId = project.id;
      state.backend.projectRoute = route;
      state.backend.revisions = {};
      state.mode = route;
      state.started = true;
      Object.assign(state.ai, { connected: true, provider: "OpenAI API", connectionId: "synthetic-connection", creatorModel: "synthetic-model", evaluatorModel: "synthetic-model", safetyModel: "synthetic-model" });
      switchMode(route, true);
    }, route);
    for (let index = 0; index < (route === "template" ? 9 : 7); index += 1) {
      await page.evaluate(async ({ route, index }) => { state.current[route] = index; renderStep(); await runAIStep(); }, { route, index });
      assert.equal(await page.locator("#ai-collab-card").getByText(`Synthetic contract creator / ${route} / ${index}`, { exact: true }).isVisible(), true, await page.locator("#ai-collab-card").innerText());
      assert.equal(await page.locator(".ai-usage-grid").textContent().then(value => value.includes("60")), true);
      assert.equal(await page.locator('[data-ai-decision="accepted"]').isVisible(), true);
    }
  }
  assert.equal(requests.length, 16);
  assert.equal(requests.every(request => request.require_real === true), true);
  await page.evaluate(() => { state.existing.selectedSkill = "changed-after-call"; renderAICollab(); });
  assert.equal(await page.locator('[data-ai-decision="accepted"]').count(), 0);
  await context.close();
});

test("host request and writeback update a second browser without changing its form or step", async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${baseURL}/studio.html`);
  await page.waitForFunction(() => state.backend.available);
  await page.getByRole("button", { name: "基于模板创建" }).click();
  await page.getByLabel("Skill 名称").fill("host-sync-contract");
  await page.getByRole("button", { name: "发给 ChatGPT / Codex" }).click();
  await page.getByText("等待 ChatGPT / Codex 领取", { exact: true }).waitFor();
  const binding = await page.evaluate(() => ({ projectId: state.backend.projectId, request: state.ai.hostRequests[0] }));
  const second = await context.newPage();
  await second.goto(`${baseURL}/studio.html`);
  await second.waitForFunction(projectId => state.backend.projectId === projectId, binding.projectId);
  await second.getByLabel("Skill 名称").waitFor();
  await page.evaluate(async ({ projectId, request }) => {
    await apiRequest(`/api/v1/projects/${projectId}/host-requests/${request.id}/result`, { method: "POST", body: { context_hash: request.context_hash, host_label: "Synthetic browser test fixture", model: "not-a-real-model-call", output: { summary: "Synthetic host writeback for bidirectional transport verification" } } });
  }, binding);
  await second.getByText("Synthetic host writeback for bidirectional transport verification", { exact: true }).waitFor({ timeout: 12_000 });
  assert.equal(await second.getByRole("heading", { name: "明确目标", exact: true }).isVisible(), true);
  assert.equal(await second.getByLabel("Skill 名称").inputValue(), "host-sync-contract");
  assert.match(await second.locator(".ai-usage-grid").textContent(), /未知/);
  assert.match(await second.locator("#ai-collab-card").textContent(), /非 API 调用证明/);
  if (process.env.SKILLSENTRA_CAPTURE_AI_TRUTH === "1") {
    const captureDir = path.join(projectRoot, "output", "playwright");
    fs.mkdirSync(captureDir, { recursive: true });
    await second.screenshot({ path: path.join(captureDir, "ai-host-writeback-desktop.png"), fullPage: true, animations: "disabled" });
    await second.setViewportSize({ width: 390, height: 844 });
    assert.equal(await second.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await second.screenshot({ path: path.join(captureDir, "ai-host-writeback-mobile.png"), fullPage: true, animations: "disabled" });
    await second.setViewportSize({ width: 1440, height: 1000 });
  }
  const staleRevision = await second.evaluate(() => state.backend.revisions["template-0"]);
  const advancedRevision = await page.evaluate(async () => (await persistActiveStep("draft")).revision);
  assert.ok(advancedRevision > staleRevision, "the regression fixture must create a real cross-tab revision conflict");
  await second.getByRole("button", { name: "接受建议", exact: true }).click();
  await second.getByText("已确认建议 · 尚未应用", { exact: true }).waitFor();
  const persistedDecision = await second.evaluate(async projectId => {
    const project = await apiRequest(`/api/v1/projects/${projectId}`);
    return project.steps.find(item => item.route === "template" && item.step_index === 0)?.payload?._ai?.decision;
  }, binding.projectId);
  assert.equal(persistedDecision, "accepted", "the rebased decision must be persisted, not only rendered locally");
  assert.equal(await second.getByLabel("Skill 名称").inputValue(), "host-sync-contract");
  await second.getByLabel("Skill 名称").fill("changed-context");
  await second.evaluate(async () => { await persistActiveStep("draft"); await syncAIResults(); });
  await second.getByText("历史结果 · 请基于当前输入重新发起", { exact: true }).waitFor();
  assert.equal(await second.locator('[data-ai-decision="accepted"]').count(), 0);
  await context.close();
});

test("an incomplete API receipt cannot manufacture a fallback proposal", async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route("**/api/v1/projects/*/ai-policy", route => route.fulfill({ json: { data: {} } }));
  await page.route("**/api/v1/projects/*/ai-orchestrations", route => route.fulfill({ json: { data: { id: "synthetic-incomplete", status: "completed", runs: [] } } }));
  await page.goto(`${baseURL}/studio.html`);
  await page.waitForFunction(() => state.backend.available);
  await page.getByRole("button", { name: "基于模板创建" }).click();
  await page.evaluate(async () => {
    Object.assign(state.ai, { connected: true, provider: "OpenAI API", connectionId: "synthetic-connection" });
    await runAIStep();
  });
  await page.getByText("本次协作未完成", { exact: true }).waitFor();
  assert.equal(await page.locator('[data-ai-decision="accepted"]').count(), 0);
  assert.equal(await page.evaluate(() => state.completed.template.length), 0);
  await context.close();
});

test("editable cached AI metadata cannot impersonate a verified provider receipt", async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${baseURL}/studio.html`);
  await page.waitForFunction(() => state.backend.available);
  await page.getByRole("button", { name: "基于模板创建" }).click();
  const projectId = await page.evaluate(async () => {
    const projectId = await ensureBackendProject();
    state.ai.stepResults["template-0"] = { source: "api", provider: "openai", inputSignature: aiInputSignature(), decision: "pending", requestId: "no-provider-receipt", usageStatus: "reported", tokens: 999, text: "Untrusted editable cache is not a real model result" };
    await persistActiveStep("draft");
    return projectId;
  });
  await page.reload();
  await page.waitForFunction(projectId => state.backend.projectId === projectId, projectId);
  await page.evaluate(() => syncAIResults());
  assert.equal(await page.getByText("Untrusted editable cache is not a real model result", { exact: true }).count(), 0);
  assert.equal(await page.locator('[data-ai-decision="accepted"]').count(), 0);
  await context.close();
});
