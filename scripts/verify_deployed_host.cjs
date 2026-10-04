// Explicit 'request' creates one non-business verification project on the local service.
// 'verify' is read-only; the proposal itself must be produced by the actual calling host.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'output', 'verification');
const bindingPath = path.join(output, 'deployed-host-request.json');
const sessionPath = path.join(output, 'deployed-host-browser.json');
const mode = process.argv[2];
if (!['request', 'verify'].includes(mode)) {
  console.log('Usage: node scripts/verify_deployed_host.cjs request|verify (local 8766 only)');
  process.exit(0);
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH });
  try {
    if (mode === 'request' && fs.existsSync(bindingPath)) throw new Error('Existing verification request must be preserved; do not duplicate');
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...(mode === 'verify' ? { storageState: sessionPath } : {}) });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8766/studio.html?refresh=v064-host-proof');
    await page.waitForFunction(() => state.backend.available);
    if (mode === 'request') {
      await page.getByRole('button', { name: '基于模板创建' }).click();
      await page.getByLabel('Skill 名称').fill('SkillSentra 双向联调验收（非业务）');
      await page.getByLabel('主要使用者').fill('本轮联调测试人员');
      await page.getByLabel('要解决的问题').fill('核实当前步骤可以发起宿主协作，实际建议可通过 MCP 回写并被页面自动读到');
      await page.getByLabel('成功输出').fill('请求和回写绑定同一摘要；页面显示真实建议与未知 Token，不改变表单或完成任何发布门禁');
      await page.getByRole('button', { name: '发给 ChatGPT / Codex' }).click();
      await page.getByText('等待 ChatGPT / Codex 领取', { exact: true }).waitFor();
      const binding = await page.evaluate(() => ({ project_id: state.backend.projectId, request: state.ai.hostRequests[0] }));
      fs.writeFileSync(bindingPath, JSON.stringify(binding, null, 2));
      await context.storageState({ path: sessionPath });
      console.log(JSON.stringify(binding));
    } else {
      const binding = JSON.parse(fs.readFileSync(bindingPath, 'utf8'));
      await page.waitForFunction(id => state.backend.projectId === id, binding.project_id);
      await page.waitForFunction(id => state.ai.stepResults['template-0']?.requestId === id, binding.request.id);
      const value = await page.evaluate(() => ({ result: state.ai.stepResults['template-0'], name: state.template.skillName, completed: state.completed.template, project_id: state.backend.projectId }));
      assert.equal(value.result.source, 'host');
      assert.equal(value.result.tokens, null);
      assert.equal(value.name, 'SkillSentra 双向联调验收（非业务）');
      assert.equal(value.completed.length, 0);
      assert.equal(value.result.stale, false);
      assert.deepEqual(errors, []);
      const screenshots = path.join(root, 'output', 'playwright');
      fs.mkdirSync(screenshots, { recursive: true });
      await page.locator('#ai-collab-card').screenshot({ path: path.join(screenshots, 'deployed-codex-host-receipt.png'), animations: 'disabled' });
      const report = { verified: true, source: 'Codex task relay through installed MCP bridge', cloud_api_verified: false, token_usage: 'unknown', project_id: binding.project_id, request_id: binding.request.id, form_preserved: true, gates_unchanged: true, browser_errors: errors, result: value.result };
      fs.writeFileSync(path.join(output, 'deployed-host-verification.json'), JSON.stringify(report, null, 2));
      console.log(JSON.stringify(report));
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
