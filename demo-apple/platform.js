"use strict";

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

const staticCopy = {
  workspace: ["工作区", "Workspace"], workspaceShort: ["工作区", "Workspace"], overview: ["总览", "Overview"], repositories: ["仓库与制品", "Repositories & artifacts"],
  automation: ["自动扫描", "Automated discovery"],
  agents: ["Agent 管理", "Agent management"], security: ["安全与撤销", "Security & revocation"], operations: ["运营", "Operations"],
  settlement: ["结算与对账", "Settlement & reconciliation"], audit: ["审计", "Audit"], privateBeta: ["Private Beta", "Private Beta"],
  payoutOff: ["生产付款已关闭", "Production payout is off"], openStudio: ["打开 Skill Studio", "Open Skill Studio"],
  organization: ["组织", "Organization"], allSystems: ["控制面正常", "Control plane healthy"], loading: ["正在验证事实链…", "Verifying the evidence chain…"],
  signIn: ["连接到控制面", "Connect to the control plane"], signInHint: ["访问令牌只保存在当前浏览器会话中，并通过 Authorization 请求头发送。", "The access token stays in this browser session and is sent only in the Authorization header."],
  accessToken: ["访问令牌", "Access token"], connect: ["安全连接", "Connect securely"],
};

const PLATFORM_SECTIONS = new Set(["overview", "repositories", "automation", "agents", "security", "settlement", "audit"]);

function sectionFromLocation() {
  if (typeof window === "undefined" || !window.location) return "overview";
  const rawHash = String(window.location.hash || "").replace(/^#/, "").split(/[?&]/, 1)[0].trim();
  let section = rawHash;
  try {
    section = decodeURIComponent(rawHash);
  } catch {
    section = rawHash;
  }
  return PLATFORM_SECTIONS.has(section) ? section : "overview";
}

const state = {
  language: localStorage.getItem("skillsentra-platform-language") || "zh-CN",
  section: sectionFromLocation(),
  token: sessionStorage.getItem("skillsentra-access-token") || "",
  session: null,
  bootstrap: null,
  discoveryResults: [],
  discoveryMeta: null,
  modalReturnFocus: null,
};

const tr = (zh, en) => state.language === "en" ? en : zh;
const formatTime = value => value ? new Intl.DateTimeFormat(state.language === "en" ? "en" : "zh-CN", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}).format(new Date(value)) : "—";
const shortDigest = value => value ? `${value.slice(0, 15)}…${value.slice(-8)}` : "—";
const money = (minor, currency = "USD") => new Intl.NumberFormat(state.language === "en" ? "en-US" : "zh-CN", {
  style: "currency", currency,
}).format(Number(minor || 0) / 100);

function discoveryScreening(item) {
  const text = `${item?.name || ""} ${item?.description || ""}`.toLowerCase();
  const relevance = Math.min(10, 3 + (/(patent|专利)/.test(text) ? 3 : 0) + (/(application|draft|disclos|claim|filing|申请|撰写|交底|权利要求)/.test(text) ? 3 : 0) + (/(skill|agent|codex|claude)/.test(text) ? 1 : 0));
  const stars = Number(item?.github_stars);
  const adoption = Number.isFinite(stars) && stars >= 0 ? Math.min(10, Math.round(Math.log10(stars + 1) * 25) / 10) : 0;
  const updated = item?.repository_updated_at ? new Date(item.repository_updated_at) : null;
  const ageDays = updated && !Number.isNaN(updated.getTime()) ? Math.max(0, (Date.now() - updated.getTime()) / 86400000) : Infinity;
  const recency = ageDays <= 30 ? 10 : ageDays <= 90 ? 8 : ageDays <= 180 ? 6 : ageDays < Infinity ? 4 : 0;
  const completeness = [item?.name, item?.description, item?.repository, item?.ref].filter(Boolean).length / 4 * 10;
  const score = Math.round((relevance * 0.45 + adoption * 0.2 + recency * 0.2 + completeness * 0.15) * 10) / 10;
  return { score, stars: Number.isFinite(stars) && stars >= 0 ? stars : null, updated: updated && !Number.isNaN(updated.getTime()) ? updated : null };
}

function discoveryScoreBadge(item) {
  const result = discoveryScreening(item);
  const score = result.score.toFixed(1);
  return `<span class="score-badge" title="${escapeHTML(tr("公开元数据筛选分，不是质量评价", "Public-metadata screening score, not a quality rating"))}">${score}<small>/10</small></span>`;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const csrf = document.cookie.split(";").map(item => item.trim()).find(item => item.startsWith("skillsentra_csrf="))?.slice("skillsentra_csrf=".length) || "";
  if (["POST", "PUT"].includes(options.method || "GET") && csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 12000);
  let response;
  try {
    response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } catch (cause) {
    const error = new Error(cause?.name === "AbortError" ? tr("请求等待过久，请稍后重试", "The request timed out. Try again shortly") : tr("控制面连接失败，请检查服务状态", "The control plane is unreachable. Check the service status"));
    error.code = cause?.name === "AbortError" ? "request_timeout" : "network_error";
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `${response.status}`);
    error.code = payload.error?.code || "request_failed";
    error.status = response.status;
    error.details = payload.error?.details || {};
    throw error;
  }
  return payload.data;
}

function localizeStatic() {
  document.documentElement.lang = state.language;
  document.title = state.language === "en" ? "SkillSentra · Control Plane" : "SkillSentra · 证据控制台";
  $$('[data-i18n]').forEach(element => {
    const values = staticCopy[element.dataset.i18n];
    if (values) element.textContent = values[state.language === "en" ? 1 : 0];
  });
  $$('[data-language]').forEach(button => {
    const selected = button.dataset.language === state.language;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  updateSystemState(state.bootstrap?.control_plane);
}

function updateSystemState(overview, unavailable = false) {
  const container = $(".system-state");
  if (!container) return;
  const readiness = overview?.readiness || {};
  const statusName = unavailable ? "unavailable" : readiness.status === "ready" ? "ready" : readiness.status === "blocked" ? "blocked" : "review";
  const labels = {
    ready: ["本地证据链就绪", "Local evidence chain ready"],
    blocked: ["存在证据硬阻断", "Evidence blockers present"],
    review: ["证据链尚未闭合", "Evidence chain incomplete"],
    unavailable: ["控制面连接异常", "Control plane unavailable"],
  };
  container.dataset.status = statusName;
  container.querySelector("span").textContent = tr(labels[statusName][0], labels[statusName][1]);
}

async function initialize() {
  // A shell/automation may open the control plane directly at #automation (or another
  // section). Re-read the hash at boot so the first render matches the URL.
  state.section = sectionFromLocation();
  localizeStatic();
  try {
    state.session = await api("/api/v1/session");
    state.bootstrap = await api("/api/v1/bootstrap");
    updateSystemState(state.bootstrap.control_plane);
    $("#auth-gate").hidden = true;
    await renderSection();
  } catch (error) {
    if (error.status === 401) {
      $("#loading-state").hidden = true;
      $("#auth-gate").hidden = false;
      setTimeout(() => $("#access-token").focus(), 40);
      return;
    }
    updateSystemState(null, true);
    showFatal(error);
  }
}

let renderSequence = 0;

async function renderSection() {
  const sequence = ++renderSequence;
  $("#loading-state").hidden = false;
  $("#platform-content").hidden = true;
  $$('.nav-item').forEach(button => button.classList.toggle("is-active", button.dataset.section === state.section));
  try {
    const renderer = {
      overview: renderOverview,
      repositories: renderRepositories,
      automation: renderAutomation,
      agents: renderAgents,
      security: renderSecurity,
      settlement: renderSettlement,
      audit: renderAudit,
    }[state.section];
    const content = await renderer();
    if (sequence !== renderSequence) return;
    $("#platform-content").innerHTML = content;
    const overview = await api("/api/v1/control/overview");
    if (sequence !== renderSequence) return;
    updateSystemState(overview);
    const tenantName = overview.tenant.name;
    $("#tenant-name").textContent = tenantName;
    $("#tenant-name").title = tenantName;
    $("#loading-state").hidden = true;
    $("#platform-content").hidden = false;
    $("#platform-main").scrollTop = 0;
    $("#platform-main").focus({ preventScroll: true });
  } catch (error) {
    if (sequence !== renderSequence) return;
    $("#loading-state").hidden = true;
    $("#platform-content").hidden = false;
    $("#platform-content").innerHTML = errorState(error);
  }
}

function pageHead(kicker, title, description, actions = "") {
  return `<header class="page-head"><div><span class="eyebrow">${escapeHTML(kicker)}</span><h1>${escapeHTML(title)}</h1><p>${escapeHTML(description)}</p></div><div class="head-actions">${actions}</div></header>`;
}

function metricCard(value, label, icon, tone = "") {
  return `<article class="metric-card"><div class="metric-top"><span class="metric-icon ${tone}">${icon}</span></div><strong class="metric-value">${escapeHTML(value)}</strong><span class="metric-label">${escapeHTML(label)}</span></article>`;
}

function status(value) {
  const normalized = String(value || "unknown").toLowerCase();
  const labels = {
    scanned: ["已扫描", "Scanned"], review_required: ["待复核", "Review"], review: ["待闭合", "Review"], blocked: ["已阻断", "Blocked"],
    pass: ["通过", "Pass"], warning: ["提醒", "Warning"], fail: ["失败", "Fail"], unknown: ["未知", "Unknown"], unverified_candidate: ["未验证候选", "Unverified candidate"],
    online: ["在线", "Online"], observed: ["已观测", "Observed"], drifted: ["漂移", "Drifted"], revoked: ["已撤销", "Revoked"],
    applied: ["已应用", "Applied"], qualified: ["合格", "Qualified"], excluded: ["已排除", "Excluded"],
    issued: ["已签发", "Issued"], closed: ["已关闭", "Closed"], paid: ["已支付", "Paid"], active: ["有效", "Active"],
    ready: ["就绪", "Ready"], paused: ["已暂停", "Paused"], running: ["运行中", "Running"], succeeded: ["已完成", "Succeeded"], partial: ["部分完成", "Partial"], error: ["异常", "Error"], failed: ["失败", "Failed"],
    critical: ["严重", "Critical"], high: ["高", "High"], medium: ["中", "Medium"], low: ["低", "Low"], info: ["信息", "Info"],
  };
  const label = labels[normalized] || [value, value];
  return `<span class="status-badge ${escapeHTML(normalized)}">${escapeHTML(state.language === "en" ? label[1] : label[0])}</span>`;
}

function readinessCopy(readiness) {
  const statusCopy = {
    ready: ["当前本地链路就绪", "Local evidence chain ready", "四项确定性检查均已闭合；这不代表 GA、生产容量或独立安全已获批准。", "All four deterministic checks are closed. This does not approve GA, production capacity, or independent security."],
    blocked: ["存在硬阻断，停止继续授权", "Hard blockers stop authorization", "撤销、漂移、失败证据或账本异常必须先独立解决，不能被就绪分数抵消。", "Revocation, drift, failed evidence, or ledger mismatch must be resolved independently and cannot be offset by the score."],
    review: ["证据链尚未闭合", "Evidence chain is incomplete", "缺失或 Unknown 证据保持可见；完成下一项确定性动作后再重新判断。", "Missing or Unknown evidence stays visible. Complete the next deterministic action before reassessment."],
  };
  const nextStepCopy = {
    freeze_artifact: ["下一步：固定制品", "Next: freeze an artifact"],
    review_evidence: ["下一步：复核证据", "Next: review evidence"],
    observe_agent: ["下一步：确认 Agent 实际状态", "Next: observe Agent state"],
    qualify_usage: ["下一步：核验用量与账本", "Next: qualify usage and ledger"],
    resolve_security_blockers: ["处理安全阻断", "Resolve security blockers"],
    resolve_agent_drift: ["处理 Agent 漂移", "Resolve Agent drift"],
    reconcile_ledger: ["处理账本异常", "Reconcile ledger"],
    ready: ["重新验证", "Verify again"],
  };
  const statusValues = statusCopy[readiness.status] || statusCopy.review;
  const nextValues = nextStepCopy[readiness.next_step?.code] || nextStepCopy.review_evidence;
  return {
    title: tr(statusValues[0], statusValues[1]),
    description: tr(statusValues[2], statusValues[3]),
    nextLabel: tr(nextValues[0], nextValues[1]),
  };
}

function renderReadiness(overview) {
  const readiness = overview.readiness || {};
  const score = Math.max(0, Math.min(100, Number(readiness.score) || 0));
  const copy = readinessCopy(readiness);
  const checks = [
    ["artifact_frozen", tr("制品已固定", "Artifact frozen")],
    ["evidence_clear", tr("证据无阻断", "Evidence clear")],
    ["runtime_observed", tr("运行态已观测", "Runtime observed")],
    ["usage_reconciled", tr("用量与账本可对账", "Usage reconciled")],
  ];
  const nextSection = readiness.next_step?.section || "overview";
  return `<section class="readiness-panel ${escapeHTML(readiness.status || "review")}" aria-labelledby="readiness-title">
    <div class="readiness-summary">
      <div><span class="eyebrow">${tr("本地证据就绪度", "LOCAL EVIDENCE READINESS")}</span><h2 id="readiness-title">${escapeHTML(copy.title)}</h2><p>${escapeHTML(copy.description)}</p></div>
      <div class="readiness-score"><strong>${score}%</strong><span>${escapeHTML(`${Number(readiness.passed_checks) || 0} / ${Number(readiness.total_checks) || 4}`)}</span></div>
    </div>
    <div class="readiness-track score-${score}" role="progressbar" aria-label="${tr("本地证据就绪度", "Local evidence readiness")}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${score}"><span></span></div>
    <ul class="readiness-checks">${checks.map(([key, label]) => `<li class="${readiness.checks?.[key] ? "passed" : "pending"}"><i aria-hidden="true"></i><span>${escapeHTML(label)}</span></li>`).join("")}</ul>
    <div class="readiness-footer"><p>${tr(`硬阻断 ${Number(readiness.hard_blockers) || 0} · 待复核 ${Number(readiness.review_items) || 0} · Unknown 不计为通过`, `Hard blockers ${Number(readiness.hard_blockers) || 0} · Review ${Number(readiness.review_items) || 0} · Unknown never counts as passed`)}</p><button class="secondary-button" type="button" data-section="${escapeHTML(nextSection)}">${escapeHTML(copy.nextLabel)}</button></div>
  </section>`;
}

async function loadProductionReadiness() {
  const roles = state.session?.roles || [];
  if (!roles.some(role => role === "admin" || role === "operator")) return null;
  try {
    return await api("/api/v1/control/production-readiness");
  } catch (error) {
    return { status: "unavailable", error_code: error.code || "unavailable" };
  }
}

function productionCheckStatus(value) {
  const normalized = String(value || "unknown").toLowerCase();
  if (normalized === "pass") return { className: "pass", label: tr("通过", "Pass") };
  if (normalized === "block") return { className: "blocked", label: tr("硬阻断", "Blocked") };
  if (normalized === "review") return { className: "review", label: tr("待复核", "Review") };
  return { className: "unknown", label: tr("未知", "Unknown") };
}

function renderProductionGate(readiness) {
  if (!readiness) return "";
  if (readiness.status === "unavailable") {
    return "<section class='production-gate panel unavailable' aria-labelledby='production-gate-title'>" +
      "<header class='panel-head'><div><span class='eyebrow'>" + tr("发布边界", "RELEASE BOUNDARY") +
      "</span><h2 id='production-gate-title'>" + tr("生产门禁暂不可用", "Production gate unavailable") +
      "</h2><p>" + tr("无法读取管理员只读快照；这不构成通过。", "The admin-only read-only snapshot could not be loaded; this is not a pass.") +
      "</p></div>" + status("unknown") + "</header></section>";
  }
  const checks = Array.isArray(readiness.checks) ? readiness.checks : [];
  const statusClass = readiness.status === "go" ? "ready" : "blocked";
  const title = readiness.status === "go" ? tr("生产门禁已满足检查", "Production checks are satisfied") : tr("生产发布仍被阻断", "Production release remains blocked");
  const description = readiness.status === "go"
    ? tr("所有必需检查和待复核项均已闭合；仍需独立人工发布授权。", "All required checks and review items are closed; independent human release authorization is still required.")
    : tr("硬阻断和待复核项逐项列出；本快照不会发布、部署或授权。", "Blockers and review items are listed individually; this snapshot never publishes, deploys, or authorizes.");
  const rows = checks.map(check => {
    const item = productionCheckStatus(check.status);
    const statusName = item.className === "blocked" ? "blocked" : item.className;
    return "<tr><td><strong>" + escapeHTML(check.label || check.id || tr("未命名检查", "Unnamed check")) +
      "</strong><small>" + escapeHTML(check.detail || "") + "</small></td><td>" + status(statusName) + "</td></tr>";
  }).join("");
  return "<section class='production-gate panel " + statusClass + "' aria-labelledby='production-gate-title'>" +
    "<header class='panel-head'><div><span class='eyebrow'>" + tr("发布边界", "RELEASE BOUNDARY") +
    "</span><h2 id='production-gate-title'>" + escapeHTML(title) + "</h2><p>" + escapeHTML(description) +
    "</p></div>" + status(statusClass === "ready" ? "ready" : "blocked") + "</header>" +
    "<div class='production-gate-summary'><div><strong>" + (Number(readiness.blocking_checks?.length) || 0) +
    "</strong><span>" + tr("硬阻断", "Hard blockers") + "</span></div><div><strong>" +
    (Number(readiness.review_checks?.length) || 0) + "</strong><span>" + tr("待复核", "Review") +
    "</span></div><div><strong>" + checks.length + "</strong><span>" + tr("总检查", "Total checks") +
    "</span></div></div><div class='table-shell production-checks'><table class='data-table'><thead><tr><th>" +
    tr("检查", "Check") + "</th><th>" + tr("状态", "Status") + "</th></tr></thead><tbody>" +
    (rows || emptyRow(2, tr("没有返回检查项。", "No checks returned."))) +
    "</tbody></table></div><p class='production-gate-note'>" +
    tr("发布授权始终由外部证据与人工门禁决定；自动化分数、测试通过或此页面都不能替代授权。",
      "Release authorization always depends on external evidence and human gates; automated scores, passing tests, and this page never substitute for authorization.") +
    "</p></section>";
}

async function renderOverview() {
  const [overview, snapshots, deployments, receipts, productionReadiness] = await Promise.all([
    api("/api/v1/control/overview"), api("/api/v1/control/snapshots"), api("/api/v1/control/deployments"), api("/api/v1/control/receipts"), loadProductionReadiness(),
  ]);
  const counts = overview.counts;
  const attention = [
    ...deployments.filter(item => item.status === "drifted").map(item => ({ tone: "critical", title: tr("检测到 Agent 漂移", "Agent drift detected"), note: item.agent_name })),
    ...(overview.counts.active_revocations ? [{ tone: "critical", title: tr("存在生效中的撤销", "Active revocations require attention"), note: `${overview.counts.active_revocations}` }] : []),
    ...snapshots.filter(item => item.status === "review_required").slice(0, 3).map(item => ({ tone: "warning", title: tr("制品需要人工复核", "Artifact needs review"), note: item.repository_name })),
  ];
  if (!attention.length) attention.push({ tone: "", title: tr("当前没有阻断项", "No blocking items"), note: tr("事实链保持完整", "Evidence chain is intact") });
  const latest = snapshots[0];
  const latestDeployment = deployments[0];
  const latestReceipt = receipts[0];
  return `${pageHead(
    tr("证据化运行", "EVIDENCE-BACKED OPERATIONS"),
    tr("每一步，都能对账。", "Every step reconciles."),
    tr("从固定 commit、不可变摘要、安全证据和 Agent 实际状态，一直追溯到合格用量与结算分录。", "Trace every fixed commit, immutable digest, security decision, Agent observation, qualified receipt, and journal line."),
    `<button class="secondary-button" type="button" data-action="refresh">${tr("刷新", "Refresh")}</button><button class="primary-button" type="button" data-action="run-demo">${tr("运行黄金路径", "Run golden path")}</button>`,
  )}
  <section class="metric-grid">
    ${metricCard(counts.snapshots, tr("不可变快照", "Immutable snapshots"), "⌘")}
    ${metricCard(counts.agents, tr("受管 Agent", "Managed Agents"), "A", "green")}
    ${metricCard(counts.qualified_receipts, tr("合格用量", "Qualified receipts"), "✓", "orange")}
    ${metricCard(overview.ledger_balance_minor === 0 ? tr("平衡", "Balanced") : tr("异常", "Mismatch"), tr("账本状态", "Ledger state"), "≡", "ink")}
  </section>
  ${renderReadiness(overview)}
  ${renderProductionGate(productionReadiness)}
  <section class="dashboard-grid">
    <article class="panel"><header class="panel-head"><div><h2>${tr("事实主线", "Evidence spine")}</h2><p>${tr("所有授权与结算都引用同一 Artifact Identity", "Every authorization and settlement action uses one Artifact Identity")}</p></div>${status(latestReceipt?.status || "unknown")}</header>
      <div class="evidence-chain">
        ${chainNode(tr("仓库", "Repository"), latest ? `${latest.owner}/${latest.repository_name}` : tr("等待导入", "Awaiting import"), Boolean(latest))}
        ${chainNode(tr("Commit", "Commit"), latest?.resolved_commit?.slice(0, 12) || "—", Boolean(latest))}
        ${chainNode(tr("Digest", "Digest"), shortDigest(latest?.artifact_digest), Boolean(latest))}
        ${chainNode(tr("门禁", "Gate"), latest ? statusText(latest.status) : "—", latest?.status === "scanned")}
        ${chainNode(tr("Agent", "Agent"), latestDeployment?.agent_name || "—", latestDeployment?.status === "observed")}
        ${chainNode(tr("结算", "Settlement"), latestReceipt ? money(latestReceipt.publisher_amount_minor) : "—", latestReceipt?.status === "qualified")}
      </div>
    </article>
    <article class="panel"><header class="panel-head"><div><h2>${tr("需要关注", "Needs attention")}</h2><p>${tr("硬门优先，不被平均分掩盖", "Hard gates cannot be hidden by averages")}</p></div><span>${attention.length}</span></header><div class="panel-body"><div class="action-stack">${attention.map(item => `<div class="action-card ${item.tone}"><i></i><span><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.note)}</small></span></div>`).join("")}</div></div></article>
  </section>`;
}

function chainNode(title, note, ready) {
  return `<div class="chain-node ${ready ? "" : "warning"}"><i></i><strong>${escapeHTML(title)}</strong><small title="${escapeHTML(note)}">${escapeHTML(note)}</small></div>`;
}

function statusText(value) {
  const labels = { scanned: tr("已通过", "Passed"), review_required: tr("待复核", "Review"), blocked: tr("阻断", "Blocked") };
  return labels[value] || String(value || "—");
}

async function renderRepositories() {
  const [repositories, snapshots] = await Promise.all([api("/api/v1/control/repositories"), api("/api/v1/control/snapshots")]);
  const sources = state.bootstrap?.skill_sources || [];
  const sourceOptions = sources.map(item => `<option value="${escapeHTML(item.id)}">${escapeHTML(item.name)} · ${escapeHTML(shortDigest(item.artifact_digest))}</option>`).join("");
  const rows = repositories.map(item => `<tr><td><div class="entity-cell"><span class="entity-icon">${item.provider === "github" ? "GH" : "L"}</span><span><strong>${escapeHTML(item.owner)}/${escapeHTML(item.name)}</strong><small>${escapeHTML(item.visibility)} · ${escapeHTML(item.provider)}</small></span></div></td><td>${status(item.status)}</td><td><span class="mono" title="${escapeHTML(item.latest_digest || "")}">${escapeHTML(shortDigest(item.latest_digest))}</span></td><td>${item.snapshot_count}</td><td><button class="row-action" type="button" data-action="scan-repository" data-id="${escapeHTML(item.id)}">${tr("扫描", "Scan")}</button></td></tr>`).join("");
  const snapshotRows = snapshots.map(item => `<tr><td>${escapeHTML(item.owner)}/${escapeHTML(item.repository_name)}</td><td><span class="mono">${escapeHTML(item.resolved_commit.slice(0, 16))}</span></td><td><span class="mono" title="${escapeHTML(item.artifact_digest)}">${escapeHTML(shortDigest(item.artifact_digest))}</span></td><td>${status(item.status)}</td><td>${item.file_count}</td><td>${formatTime(item.created_at)}</td></tr>`).join("");
  return `${pageHead(
    tr("来源与制品", "SOURCE & ARTIFACT"), tr("固定来源，再谈安全。", "Fix the source before judging safety."),
    tr("GitHub ref 会先解析为 commit；归档在隔离区静态读取，不执行仓库代码。", "A GitHub ref resolves to a commit first. The archive is read statically in quarantine and repository code is never executed."),
  )}
  <form class="filter-bar" data-form="repository">
    <div class="field wide"><label for="github-url">GitHub URL</label><input id="github-url" name="url" type="url" placeholder="https://github.com/owner/repository"></div>
    <div class="field"><label for="visibility">${tr("可见性", "Visibility")}</label><select id="visibility" name="visibility"><option value="public">Public</option><option value="private">Private</option><option value="internal">Internal</option></select></div>
    <button class="primary-button" type="submit">${tr("添加仓库", "Add repository")}</button>
  </form>
  ${sources.length ? `<form class="filter-bar" data-form="local-source"><div class="field wide"><label for="local-source">${tr("授权本地 Skill（测试/演示）", "Authorized local Skill (test/demo)")}</label><select id="local-source" name="source_id">${sourceOptions}</select></div><button class="secondary-button" type="submit">${tr("加入并固定", "Add and freeze")}</button></form>` : ""}
  <section class="table-shell"><table class="data-table"><thead><tr><th>${tr("仓库", "Repository")}</th><th>${tr("状态", "Status")}</th><th>Digest</th><th>${tr("快照", "Snapshots")}</th><th>${tr("操作", "Action")}</th></tr></thead><tbody>${rows || emptyRow(5, tr("还没有仓库。", "No repositories yet."))}</tbody></table></section>
  <div class="section-gap" aria-hidden="true"></div>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("不可变快照", "Immutable snapshots")}</h2><p>Repository → Commit → Artifact Digest</p></div><span>${snapshots.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("来源", "Source")}</th><th>Commit</th><th>Digest</th><th>${tr("门禁", "Gate")}</th><th>${tr("文件", "Files")}</th><th>${tr("时间", "Time")}</th></tr></thead><tbody>${snapshotRows || emptyRow(6, tr("扫描后将在这里形成快照。", "Snapshots appear here after scanning."))}</tbody></table></div></div></section>`;
}

async function renderAutomation() {
  const [targets, runs] = await Promise.all([
    api("/api/v1/admin/scan-targets"), api("/api/v1/admin/scan-runs"),
  ]);
  const targetRows = targets.map(item => `<tr><td><div class="entity-cell"><span class="entity-icon">${item.target_type === "github" ? "GH" : "WEB"}</span><span><strong>${escapeHTML(item.name)}</strong><small title="${escapeHTML(item.url)}">${escapeHTML(item.url)}</small></span></div></td><td>${status(item.status)}</td><td>${escapeHTML(item.requested_ref)}</td><td>${escapeHTML(`${item.interval_minutes} min`)}</td><td>${formatTime(item.next_run_at)}</td><td><div class="row-actions"><button class="row-action" type="button" data-action="run-scan-target" data-id="${escapeHTML(item.id)}">${tr("立即扫描", "Run now")}</button><button class="row-action" type="button" data-action="toggle-scan-target" data-id="${escapeHTML(item.id)}" data-enabled="${item.auto_enabled}">${item.auto_enabled ? tr("暂停", "Pause") : tr("启用", "Enable")}</button></div></td></tr>`).join("");
  const runRows = runs.map(item => `<tr><td>${formatTime(item.started_at)}</td><td>${escapeHTML(item.target_name)}</td><td>${status(item.status)}</td><td>${item.discovered_count}</td><td>${item.scanned_count}</td><td>${item.blocked_count}</td><td>${escapeHTML(item.error_message || "—")}</td></tr>`).join("");
  const discoveryRows = state.discoveryResults.map(item => {
    const score = discoveryScreening(item);
    const stars = score.stars === null ? "—" : new Intl.NumberFormat(state.language === "en" ? "en-US" : "zh-CN").format(score.stars);
    return `<tr><td><div class="entity-cell"><span class="entity-icon">${item.source === "github" ? "GH" : "WEB"}</span><span><strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.description || tr("未提供说明", "No description"))}</small></span></div></td><td><a href="${escapeHTML(item.repository)}" target="_blank" rel="noopener noreferrer">${escapeHTML(item.repository)}</a></td><td>${escapeHTML(item.ref || "—")}</td><td>${escapeHTML(stars)}</td><td>${score.updated ? escapeHTML(formatTime(score.updated.toISOString())) : "—"}</td><td>${discoveryScoreBadge(item)}</td><td>${status(item.verification_status || (item.unverified_candidate ? "unverified_candidate" : "unknown"))}</td><td>${tr("必须先固定 commit 并静态扫描", "Fix a commit and run a static scan first")}</td></tr>`;
  }).join("");
  const discoveryMeta = state.discoveryMeta;
  const discoveryFeedback = discoveryMeta?.elapsedMs !== undefined
    ? discoveryMeta.error
      ? tr(`搜索失败 ${discoveryMeta.elapsedMs} ms · 请重试`, `Search failed ${discoveryMeta.elapsedMs} ms · retry when ready`)
      : tr(`最近一次搜索 ${discoveryMeta.elapsedMs} ms · ${discoveryMeta.count} 个候选`, `Last search ${discoveryMeta.elapsedMs} ms · ${discoveryMeta.count} candidates`)
    : tr("等待搜索 · 结果将保留为未验证候选", "Ready · results remain unverified candidates");
  return `${pageHead(
    tr("自动发现", "AUTOMATED DISCOVERY"), tr("持续找到新 Skill。", "Continuously discover new Skills."),
    tr("按周期扫描 GitHub 仓库或指定 HTTPS 网站；固定 commit 后仅做静态检查，不执行外部代码。", "Scan GitHub repositories or approved HTTPS sites on a schedule. Commits are fixed before static inspection and external code is never executed."),
    `<button class="secondary-button" type="button" data-action="run-due-targets">${tr("运行到期任务", "Run due targets")}</button>`,
  )}
  <form class="filter-bar" data-form="discovery-search">
    <div class="field"><label for="discovery-provider">${tr("搜索来源", "Search source")}</label><select id="discovery-provider" name="provider"><option value="github">GitHub</option><option value="catalog">HTTPS Catalog</option></select></div>
    <div class="field wide"><label for="discovery-query">${tr("关键词", "Keywords")}</label><input id="discovery-query" name="query" minlength="2" maxlength="80" required placeholder="${tr("例如：专利评价", "e.g. patent evaluation")}"></div>
    <div class="field wide"><label for="discovery-catalog">${tr("目录 URL（仅 Catalog 需要）", "Catalog URL (Catalog only)")}</label><input id="discovery-catalog" name="catalog_url" type="url" placeholder="https://example.com/skills.json"></div>
    <div class="field"><label for="discovery-limit">${tr("候选数量", "Candidate limit")}</label><input id="discovery-limit" name="limit" type="number" min="1" max="20" value="10"></div>
    <button class="primary-button" type="submit">${tr("搜索候选", "Search candidates")}</button>
  </form>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("外部候选", "External candidates")}</h2><p>${tr("搜索结果未经验证，不会自动下载、安装或执行", "Search results are unverified and are never downloaded, installed, or executed automatically")}</p></div><div class="discovery-meta" role="status" aria-live="polite"><span class="live-dot"></span><span>${escapeHTML(discoveryFeedback)}</span><strong>${state.discoveryResults.length}</strong></div></header><div class="panel-body"><p class="discovery-score-note">${tr("筛选分 = 相关性 45% + 星标采用 20% + 更新时间 20% + 元数据完整度 15%；正式质量评分需固定 commit 并完成静态扫描", "Screening score = relevance 45% + stars 20% + recency 20% + metadata completeness 15%; formal quality evaluation requires a fixed commit and static scan")}</p><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("候选", "Candidate")}</th><th>${tr("仓库", "Repository")}</th><th>Ref</th><th>${tr("星标", "Stars")}</th><th>${tr("更新时间", "Updated")}</th><th>${tr("筛选分", "Screening")}</th><th>${tr("状态", "Status")}</th><th>${tr("下一步", "Next step")}</th></tr></thead><tbody>${discoveryRows || emptyRow(8, tr("输入关键词后搜索 GitHub 或受控 HTTPS 目录。", "Search GitHub or a controlled HTTPS catalog by keyword."))}</tbody></table></div></div></section>
  <div class="section-gap" aria-hidden="true"></div>
  <form class="filter-bar" data-form="scan-target">
    <div class="field"><label for="scan-target-name">${tr("目标名称", "Target name")}</label><input id="scan-target-name" name="name" required maxlength="100" placeholder="${tr("官方 Skill 目录", "Official Skill catalog")}"></div>
    <div class="field"><label for="scan-target-type">${tr("来源类型", "Source type")}</label><select id="scan-target-type" name="target_type"><option value="github">GitHub</option><option value="website">HTTPS Website</option></select></div>
    <div class="field wide"><label for="scan-target-url">${tr("来源地址", "Source URL")}</label><input id="scan-target-url" name="url" type="url" required placeholder="https://github.com/owner/repository"></div>
    <div class="field"><label for="scan-target-ref">${tr("分支 / Ref", "Branch / Ref")}</label><input id="scan-target-ref" name="requested_ref" value="main" maxlength="200"></div>
    <div class="field"><label for="scan-target-interval">${tr("周期（分钟）", "Interval (minutes)")}</label><input id="scan-target-interval" name="interval_minutes" type="number" min="15" max="43200" value="1440" required></div>
    <label class="check-field"><input name="auto_enabled" type="checkbox" checked><span>${tr("自动运行", "Run automatically")}</span></label>
    <button class="primary-button" type="submit">${tr("添加扫描目标", "Add target")}</button>
  </form>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("扫描目标", "Scan targets")}</h2><p>Source → Fixed Commit → Static Evidence</p></div><span>${targets.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("目标", "Target")}</th><th>${tr("状态", "Status")}</th><th>Ref</th><th>${tr("周期", "Interval")}</th><th>${tr("下次运行", "Next run")}</th><th>${tr("操作", "Action")}</th></tr></thead><tbody>${targetRows || emptyRow(6, tr("还没有自动扫描目标。", "No automated scan targets yet."))}</tbody></table></div></div></section>
  <div class="section-gap" aria-hidden="true"></div>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("运行记录", "Run history")}</h2><p>${tr("发现、扫描、阻断和失败全部留痕", "Discovery, scans, blocks, and failures remain traceable")}</p></div><span>${runs.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("时间", "Time")}</th><th>${tr("目标", "Target")}</th><th>${tr("状态", "Status")}</th><th>${tr("发现", "Found")}</th><th>${tr("扫描", "Scanned")}</th><th>${tr("阻断", "Blocked")}</th><th>${tr("错误", "Error")}</th></tr></thead><tbody>${runRows || emptyRow(7, tr("扫描运行后会在这里留下记录。", "Run records appear here after scanning."))}</tbody></table></div></div></section>`;
}

async function renderAgents() {
  const [agents, deployments, snapshots] = await Promise.all([api("/api/v1/control/agents"), api("/api/v1/control/deployments"), api("/api/v1/control/snapshots")]);
  const agentOptions = agents.map(item => `<option value="${escapeHTML(item.id)}">${escapeHTML(item.name)} · ${escapeHTML(item.environment)}</option>`).join("");
  const snapshotOptions = snapshots.filter(item => item.status !== "blocked").map(item => `<option value="${escapeHTML(item.id)}">${escapeHTML(item.repository_name)} · ${escapeHTML(shortDigest(item.artifact_digest))}</option>`).join("");
  const agentRows = agents.map(item => `<tr><td><div class="entity-cell"><span class="entity-icon">A</span><span><strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.host_name)} · ${escapeHTML(item.adapter_version)}</small></span></div></td><td>${escapeHTML(item.environment)}</td><td>${status(item.status)}</td><td>${escapeHTML(item.capabilities.revoke || "unsupported")}</td><td>${formatTime(item.last_heartbeat_at)}</td></tr>`).join("");
  const deploymentRows = deployments.map(item => `<tr><td>${escapeHTML(item.agent_name)}</td><td><span class="mono" title="${escapeHTML(item.desired_digest)}">${escapeHTML(shortDigest(item.desired_digest))}</span></td><td><span class="mono" title="${escapeHTML(item.observed_digest)}">${escapeHTML(shortDigest(item.observed_digest))}</span></td><td>${escapeHTML(item.control_level)}</td><td>${status(item.status)}</td><td>${item.observed_digest ? "—" : `<button class="row-action" type="button" data-action="observe-deployment" data-id="${escapeHTML(item.id)}" data-digest="${escapeHTML(item.desired_digest)}">${tr("确认观测", "Observe")}</button>`}</td></tr>`).join("");
  return `${pageHead(tr("运行控制", "RUNTIME CONTROL"), tr("目标状态与实际状态，分开看。", "Keep desired and observed state separate."), tr("每个 Adapter 按能力声明 Enforced、Observed、Declared 或 Unsupported；未知状态不会被渲染成成功。", "Every Adapter reports each capability as Enforced, Observed, Declared, or Unsupported. Unknown never renders as success."))}
  <form class="filter-bar" data-form="agent"><div class="field wide"><label>${tr("Agent 名称", "Agent name")}</label><input name="name" required maxlength="100" placeholder="Research Agent"></div><div class="field"><label>${tr("环境", "Environment")}</label><select name="environment"><option value="development">Development</option><option value="staging">Staging</option><option value="production">Production</option></select></div><div class="field"><label>${tr("主机", "Host")}</label><input name="host_name" placeholder="reference-host"></div><button class="primary-button" type="submit">${tr("登记 Agent", "Register Agent")}</button></form>
  ${agents.length && snapshots.length ? `<form class="filter-bar" data-form="deployment"><div class="field"><label>Agent</label><select name="agent_id">${agentOptions}</select></div><div class="field wide"><label>${tr("批准制品", "Approved artifact")}</label><select name="snapshot_id">${snapshotOptions}</select></div><div class="field"><label>${tr("控制等级", "Control level")}</label><select name="control_level"><option value="enforced">Enforced</option><option value="observed">Observed</option><option value="declared">Declared</option><option value="unsupported">Unsupported</option></select></div><button class="secondary-button" type="submit">${tr("绑定制品", "Bind artifact")}</button></form>` : ""}
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("Agent 清单", "Agent inventory")}</h2><p>Agent → Host → Adapter → Capability</p></div><span>${agents.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>Agent</th><th>${tr("环境", "Environment")}</th><th>${tr("健康", "Health")}</th><th>Revoke</th><th>${tr("心跳", "Heartbeat")}</th></tr></thead><tbody>${agentRows || emptyRow(5, tr("尚未登记 Agent。", "No Agents registered."))}</tbody></table></div></div></section>
  <div class="section-gap" aria-hidden="true"></div>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("部署与漂移", "Deployments & drift")}</h2><p>Desired Digest ↔ Observed Digest</p></div><span>${deployments.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>Agent</th><th>Desired</th><th>Observed</th><th>${tr("能力", "Level")}</th><th>${tr("状态", "Status")}</th><th>${tr("操作", "Action")}</th></tr></thead><tbody>${deploymentRows || emptyRow(6, tr("绑定制品后显示部署。", "Deployments appear after binding an artifact."))}</tbody></table></div></div></section>`;
}

async function renderSecurity() {
  const [evidence, revocations, snapshots] = await Promise.all([api("/api/v1/control/evidence"), api("/api/v1/control/revocations"), api("/api/v1/control/snapshots")]);
  const counts = { critical: 0, warning: 0, pass: 0 };
  evidence.forEach(item => { if (item.severity === "critical" || item.status === "fail") counts.critical += 1; else if (item.status === "warning" || item.status === "unknown") counts.warning += 1; else counts.pass += 1; });
  const evidenceRows = evidence.map(item => `<tr><td>${escapeHTML(item.repository_name)}</td><td>${escapeHTML(item.evidence_type)}</td><td>${status(item.status)}</td><td>${status(item.severity)}</td><td>${escapeHTML(item.engine)} · ${escapeHTML(item.ruleset_version)}</td><td><span class="mono">${escapeHTML(shortDigest(item.subject_digest))}</span></td></tr>`).join("");
  const revocationRows = revocations.map(item => `<tr><td><span class="mono" title="${escapeHTML(item.artifact_digest)}">${escapeHTML(shortDigest(item.artifact_digest))}</span></td><td>${status(item.severity)}</td><td>${escapeHTML(item.reason)}</td><td>${status(item.status)}</td><td>${formatTime(item.created_at)}</td></tr>`).join("");
  const candidates = snapshots.filter(item => !revocations.some(revocation => revocation.artifact_digest === item.artifact_digest && revocation.status === "active"));
  const options = candidates.map(item => `<option value="${escapeHTML(item.artifact_digest)}">${escapeHTML(item.repository_name)} · ${escapeHTML(shortDigest(item.artifact_digest))}</option>`).join("");
  return `${pageHead(tr("确定性安全", "DETERMINISTIC SECURITY"), tr("安全结论必须带证据边界。", "Security decisions need evidence boundaries."), tr("静态规则、来源、依赖、能力推断与动态沙箱分别展示；模型建议不构成授权边界。", "Static rules, provenance, dependencies, capability inference, and sandbox evidence remain separate. Model suggestions never become authorization."))}
  <section class="risk-grid">
    <article class="risk-card critical"><span class="risk-count">${counts.critical}</span><h3>${tr("硬门失败", "Hard-gate failures")}</h3><p>${tr("Critical、来源漂移或已撤销制品不能进入生产授权。", "Critical, drifting, or revoked artifacts cannot receive production authorization.")}</p></article>
    <article class="risk-card warning"><span class="risk-count">${counts.warning}</span><h3>${tr("未知或待复核", "Unknown or review")}</h3><p>${tr("未知不会被显示为通过；需要人工决定或补充证据。", "Unknown never appears as passed; a reviewer or more evidence is required.")}</p></article>
    <article class="risk-card good"><span class="risk-count">${counts.pass}</span><h3>${tr("有效证据", "Valid evidence")}</h3><p>${tr("每项结论绑定引擎、规则版本、时间和 Artifact Digest。", "Each conclusion binds its engine, ruleset, time, and Artifact Digest.")}</p></article>
  </section>
  ${options ? `<form class="filter-bar" data-form="revocation"><div class="field wide"><label>${tr("撤销制品", "Revoke artifact")}</label><select name="artifact_digest">${options}</select></div><div class="field wide"><label>${tr("原因", "Reason")}</label><input name="reason" required placeholder="${tr("说明证据与影响", "Describe evidence and impact")}"></div><div class="field"><label>${tr("严重度", "Severity")}</label><select name="severity"><option value="high">High</option><option value="critical">Critical</option><option value="medium">Medium</option></select></div><button class="danger-button" type="submit">${tr("立即撤销", "Revoke now")}</button></form>` : ""}
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("证据证明", "Evidence attestations")}</h2><p>${tr("Observed 不等于 Enforced", "Observed is not Enforced")}</p></div><span>${evidence.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("仓库", "Repository")}</th><th>${tr("证据类型", "Evidence type")}</th><th>${tr("结论", "Status")}</th><th>${tr("严重度", "Severity")}</th><th>${tr("签发者", "Issuer")}</th><th>Digest</th></tr></thead><tbody>${evidenceRows || emptyRow(6, tr("扫描制品后形成证据。", "Scan an artifact to create evidence."))}</tbody></table></div></div></section>
  <div class="section-gap" aria-hidden="true"></div>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("撤销清单", "Revocation list")}</h2><p>${tr("撤销优先于批准缓存、部署和授权", "Revocation outranks approval caches, deployments, and entitlements")}</p></div><span>${revocations.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>Digest</th><th>${tr("严重度", "Severity")}</th><th>${tr("原因", "Reason")}</th><th>${tr("状态", "Status")}</th><th>${tr("时间", "Time")}</th></tr></thead><tbody>${revocationRows || emptyRow(5, tr("当前没有撤销。", "No revocations."))}</tbody></table></div></div></section>`;
}

async function renderSettlement() {
  const [receipts, statements, payouts, ledger] = await Promise.all([api("/api/v1/control/receipts"), api("/api/v1/control/statements"), api("/api/v1/control/payouts"), api("/api/v1/control/ledger")]);
  const receiptRows = receipts.map(item => `<tr><td>${escapeHTML(item.external_receipt_id)}</td><td><span class="mono">${escapeHTML(shortDigest(item.artifact_digest))}</span></td><td>${escapeHTML(item.evidence_level)}</td><td>${escapeHTML(`${item.quantity} ${item.unit}`)}</td><td>${status(item.status)}</td><td>${item.status === "qualified" ? money(item.gross_amount_minor) : escapeHTML(item.exclusion_reason)}</td></tr>`).join("");
  const statementRows = statements.map(item => `<tr><td>${escapeHTML(item.id)}</td><td>${escapeHTML(item.publisher_id)}</td><td>${escapeHTML(`${formatTime(item.cycle_start)} – ${formatTime(item.cycle_end)}`)}</td><td>${money(item.total_gross_minor, item.currency)}</td><td>${money(item.total_payable_minor, item.currency)}</td><td>${status(item.status)}</td><td>${item.status === "issued" ? `<button class="row-action" type="button" data-action="payout" data-id="${escapeHTML(item.id)}">${tr("支付沙箱", "Sandbox payout")}</button>` : "—"}</td></tr>`).join("");
  const payoutRows = payouts.map(item => `<tr><td>${escapeHTML(item.provider_reference)}</td><td>${money(item.amount_minor, item.currency)}</td><td>${status(item.status)}</td><td>${escapeHTML(item.mode)}</td><td>${formatTime(item.updated_at)}</td></tr>`).join("");
  const qualified = receipts.filter(item => item.status === "qualified").reduce((sum, item) => sum + item.gross_amount_minor, 0);
  const payable = receipts.filter(item => item.status === "qualified").reduce((sum, item) => sum + item.publisher_amount_minor, 0);
  return `${pageHead(
    tr("可审计结算", "AUDITABLE SETTLEMENT"), tr("先证明用量，再形成金额。", "Prove usage before creating money."),
    tr("只有 E2/E3、有效授权、精确 Digest、未撤销且未重复的收据才能入账；生产付款保持硬关闭。", "Only E2/E3 receipts with a valid entitlement, exact Digest, no revocation, and no duplicate can enter the ledger. Production payout stays hard-off."),
    receipts.some(item => item.status === "qualified") ? `<button class="primary-button" type="button" data-action="create-statement">${tr("生成结算单", "Create statement")}</button>` : "",
  )}
  <section class="metric-grid">
    ${metricCard(receipts.length, tr("收据", "Receipts"), "R")}
    ${metricCard(money(qualified), tr("合格总额", "Qualified gross"), "$", "green")}
    ${metricCard(money(payable), tr("开发者应付", "Publisher payable"), "↗", "orange")}
    ${metricCard(ledger.balanced ? tr("0 差异", "Zero variance") : `${ledger.imbalances.length}`, tr("账本对账", "Ledger reconciliation"), "≡", "ink")}
  </section>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("用量资格", "Usage qualification")}</h2><p>${tr("收据 → 资格 → 价格表 → 分成规则", "Receipt → Qualification → Pricebook → Share Rule")}</p></div><span>${receipts.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("收据", "Receipt")}</th><th>Digest</th><th>${tr("证据", "Evidence")}</th><th>${tr("用量", "Usage")}</th><th>${tr("资格", "Qualification")}</th><th>${tr("金额/原因", "Amount/reason")}</th></tr></thead><tbody>${receiptRows || emptyRow(6, tr("运行黄金路径后生成合格收据。", "Run the golden path to create a qualified receipt."))}</tbody></table></div></div></section>
  <div class="section-gap" aria-hidden="true"></div>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("结算单", "Statements")}</h2><p>${tr("冻结后不改写，只追加调整", "Frozen history is never rewritten; adjustments are appended")}</p></div><span>${statements.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>ID</th><th>${tr("发布者", "Publisher")}</th><th>${tr("周期", "Cycle")}</th><th>${tr("总额", "Gross")}</th><th>${tr("应付", "Payable")}</th><th>${tr("状态", "Status")}</th><th>${tr("操作", "Action")}</th></tr></thead><tbody>${statementRows || emptyRow(7, tr("还没有结算单。", "No statements yet."))}</tbody></table></div></div></section>
  <div class="section-gap" aria-hidden="true"></div>
  <section class="panel flush"><header class="panel-head"><div><h2>${tr("支付沙箱", "Payout sandbox")}</h2><p>${tr("真实付款开关在 API 与配置层硬关闭", "Production payout is hard-disabled in the API and configuration")}</p></div><span>${payouts.length}</span></header><div class="panel-body"><div class="table-shell"><table class="data-table"><thead><tr><th>${tr("服务商引用", "Provider ref")}</th><th>${tr("金额", "Amount")}</th><th>${tr("状态", "Status")}</th><th>${tr("模式", "Mode")}</th><th>${tr("更新时间", "Updated")}</th></tr></thead><tbody>${payoutRows || emptyRow(5, tr("沙箱支付会在这里显示。", "Sandbox payouts appear here."))}</tbody></table></div></div></section>`;
}

async function renderAudit() {
  const audit = await api("/api/v1/control/audit");
  const rows = audit.events.map(item => `<tr><td>${formatTime(item.created_at)}</td><td>${escapeHTML(item.actor_id)}</td><td>${escapeHTML(item.action)}</td><td>${escapeHTML(item.entity_type)}</td><td><span class="mono">${escapeHTML(item.entity_id)}</span></td><td><span class="mono" title="${escapeHTML(item.event_hash)}">${escapeHTML(shortDigest(item.event_hash))}</span></td></tr>`).join("");
  return `${pageHead(tr("透明审计", "TRANSPARENT AUDIT"), tr("每个动作，都留下可验证痕迹。", "Every action leaves a verifiable trace."), tr("事件按租户形成 SHA-256 哈希链；删除、插入或修改中间事件都会破坏完整性校验。", "Events form a tenant-scoped SHA-256 hash chain. Deleting, inserting, or changing an intermediate event breaks verification."), `<button class="secondary-button" type="button" data-action="refresh">${tr("重新验证", "Verify again")}</button>`)}
  <div class="integrity-banner"><i></i><span><strong>${audit.integrity.valid ? tr("审计链完整", "Audit chain intact") : tr("审计链已损坏", "Audit chain broken")}</strong><small>${tr(`已验证 ${audit.integrity.event_count} 个事件`, `${audit.integrity.event_count} events verified`)}</small></span></div>
  <section class="table-shell"><table class="data-table"><thead><tr><th>${tr("时间", "Time")}</th><th>${tr("操作者", "Actor")}</th><th>${tr("动作", "Action")}</th><th>${tr("对象", "Entity")}</th><th>ID</th><th>Hash</th></tr></thead><tbody>${rows || emptyRow(6, tr("动作发生后会进入审计链。", "Actions appear in the audit chain."))}</tbody></table></section>`;
}

function emptyRow(columns, message) {
  return `<tr><td colspan="${columns}"><div class="empty-state"><span class="empty-mark">○</span><h3>${escapeHTML(message)}</h3><p>${tr("可以从页面上方的主要操作开始。", "Start with the primary action above.")}</p></div></td></tr>`;
}

function errorState(error) {
  return `${pageHead(tr("请求未完成", "REQUEST NOT COMPLETED"), tr("控制面暂时无法显示。", "The control plane cannot be displayed yet."), error.message || String(error), `<button class="primary-button" type="button" data-action="refresh">${tr("重试", "Try again")}</button>`)}`;
}

function showFatal(error) {
  $("#loading-state").hidden = true;
  $("#platform-content").hidden = false;
  $("#platform-content").innerHTML = errorState(error);
}

let toastTimer;
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").classList.add("is-visible");
  toastTimer = setTimeout(() => $("#toast").classList.remove("is-visible"), 2800);
}

function openModal(content) {
  state.modalReturnFocus = document.activeElement;
  $("#modal-content").innerHTML = content;
  $("#modal-backdrop").hidden = false;
  setTimeout(() => $("#modal-backdrop").querySelector("input, select, button")?.focus(), 20);
}

function closeModal() {
  $("#modal-backdrop").hidden = true;
  $("#modal-content").innerHTML = "";
  state.modalReturnFocus?.focus?.();
}

function requestConfirmation({ title, description, facts, acknowledgement, confirmLabel, danger = false }, operation) {
  const rows = facts.map(([label, value]) => `<div class="detail-row"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`).join("");
  openModal(`<span class="eyebrow">IMPACT PREVIEW</span><h2 id="modal-title">${escapeHTML(title)}</h2><p>${escapeHTML(description)}</p><div class="modal-grid confirmation-grid">${rows}</div><form id="impact-confirm-form" class="confirmation-form"><label class="check-field confirmation-check"><input name="ack" type="checkbox" required><span>${escapeHTML(acknowledgement)}</span></label><p class="form-error" role="alert"></p><div class="modal-actions"><button class="quiet-button" type="button" data-close-modal>${tr("取消", "Cancel")}</button><button class="${danger ? "danger-button" : "primary-button"}" type="submit">${escapeHTML(confirmLabel)}</button></div></form>`);
  $("#impact-confirm-form").addEventListener("submit", async event => {
    event.preventDefault();
    const submit = event.currentTarget.querySelector('[type="submit"]');
    const error = event.currentTarget.querySelector(".form-error");
    submit.disabled = true;
    try {
      await operation();
      closeModal();
      await renderSection();
    } catch (failure) {
      error.textContent = failure.message || tr("操作失败", "Action failed");
      submit.disabled = false;
    }
  });
}

async function handleAction(button) {
  const action = button.dataset.action;
  try {
    button.disabled = true;
    if (action === "refresh") {
      await renderSection();
    } else if (action === "run-demo") {
      await api("/api/v1/control/demo", { method: "POST", body: {} });
      toast(tr("黄金路径已完成并留下完整证据。", "Golden path completed with full evidence."));
      await renderSection();
    } else if (action === "scan-repository") {
      await api(`/api/v1/control/repositories/${encodeURIComponent(button.dataset.id)}/scans`, { method: "POST", body: { ref: "main" } });
      toast(tr("静态扫描已完成，未执行仓库代码。", "Static scan complete; repository code was not executed."));
      await renderSection();
    } else if (action === "run-scan-target") {
      await api(`/api/v1/admin/scan-targets/${encodeURIComponent(button.dataset.id)}/run`, { method: "POST", body: {} });
      toast(tr("扫描已完成，证据和异常已留存。", "Scan completed; evidence and exceptions were recorded."));
      await renderSection();
    } else if (action === "toggle-scan-target") {
      const autoEnabled = button.dataset.enabled !== "true";
      await api(`/api/v1/admin/scan-targets/${encodeURIComponent(button.dataset.id)}`, { method: "PUT", body: { auto_enabled: autoEnabled } });
      toast(autoEnabled ? tr("自动扫描已启用。", "Automated scanning enabled.") : tr("自动扫描已暂停。", "Automated scanning paused."));
      await renderSection();
    } else if (action === "run-due-targets") {
      const result = await api("/api/v1/admin/scan-runs/run-due", { method: "POST", body: {} });
      toast(tr(`已运行 ${result.length} 个到期目标。`, `${result.length} due targets completed.`));
      await renderSection();
    } else if (action === "observe-deployment") {
      await api(`/api/v1/control/deployments/${encodeURIComponent(button.dataset.id)}/observe`, { method: "POST", body: { observed_digest: button.dataset.digest } });
      toast(tr("已记录 Agent 实际 Digest。", "Observed Agent Digest recorded."));
      await renderSection();
    } else if (action === "create-statement") {
      requestConfirmation({
        title: tr("确认生成结算单", "Confirm statement creation"),
        description: tr("符合条件的收据将被冻结到本周期的不可变结算血缘。", "Eligible receipts will be frozen into immutable settlement lineage for this cycle."),
        facts: [[tr("发布者", "Publisher"), "publisher-demo"], [tr("周期", "Cycle"), "2000-01-01 — 2100-01-01"], [tr("资金影响", "Financial impact"), tr("仅账本和沙箱状态", "Ledger and sandbox state only")]],
        acknowledgement: tr("我已核对周期，了解历史血缘不会被覆盖，生产付款保持关闭。", "I verified the cycle and understand history is not overwritten and production payout remains off."),
        confirmLabel: tr("确认生成", "Create statement"),
      }, async () => {
        await api("/api/v1/control/statements", { method: "POST", body: { publisher_id: "publisher-demo", cycle_start: "2000-01-01T00:00:00+00:00", cycle_end: "2100-01-01T00:00:00+00:00" } });
        toast(tr("结算单已签发。", "Statement issued."));
      });
    } else if (action === "payout") {
      const statementId = button.dataset.id;
      requestConfirmation({
        title: tr("确认沙箱支付", "Confirm sandbox payout"),
        description: tr("此动作只写入幂等沙箱支付与平衡账本，不连接真实付款通道。", "This writes only an idempotent sandbox payout and balanced ledger; no live payment rail is connected."),
        facts: [[tr("结算单", "Statement"), statementId], [tr("模式", "Mode"), "sandbox"], [tr("真实资金", "Real funds"), tr("不发生划转", "No movement")]],
        acknowledgement: tr("我已核对结算单，并了解此次操作不会划转真实资金。", "I verified the Statement and understand this operation moves no real funds."),
        confirmLabel: tr("确认沙箱支付", "Confirm sandbox payout"),
      }, async () => {
        await api(`/api/v1/control/statements/${encodeURIComponent(statementId)}/payouts`, { method: "POST", headers: { "Idempotency-Key": `ui-${statementId}-v1` }, body: {} });
        toast(tr("支付沙箱已完成；未发生真实资金划转。", "Sandbox payout completed; no real funds moved."));
      });
    }
  } catch (error) {
    toast(error.message || tr("操作失败", "Action failed"));
  } finally {
    button.disabled = false;
  }
}

async function handleForm(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const kind = form.dataset.form;
  const submit = form.querySelector('[type="submit"]');
  const originalSubmitLabel = submit?.textContent || "";
  let discoveryStartedAt = null;
  let localError = form.querySelector(".inline-form-error");
  if (localError) localError.remove();
  form.querySelectorAll('[aria-invalid="true"]').forEach(field => { field.removeAttribute("aria-invalid"); field.removeAttribute("aria-describedby"); });
  try {
    submit.disabled = true;
    if (kind === "discovery-search") submit.textContent = tr("搜索中…", "Searching…");
    if (kind === "repository") {
      await api("/api/v1/control/repositories", { method: "POST", body: values });
      toast(tr("GitHub 仓库已加入，等待固定 commit 扫描。", "GitHub repository added; scan it to fix a commit."));
    } else if (kind === "local-source") {
      const repository = await api("/api/v1/control/repositories", { method: "POST", body: values });
      await api(`/api/v1/control/repositories/${encodeURIComponent(repository.id)}/scans`, { method: "POST", body: values });
      toast(tr("本地授权 Skill 已固定为不可变快照。", "Authorized local Skill frozen as an immutable snapshot."));
    } else if (kind === "scan-target") {
      await api("/api/v1/admin/scan-targets", { method: "POST", body: { ...values, auto_enabled: values.auto_enabled === "on", interval_minutes: Number(values.interval_minutes) } });
      toast(tr("自动扫描目标已添加。", "Automated scan target added."));
    } else if (kind === "discovery-search") {
      discoveryStartedAt = performance.now();
      const result = await api("/api/v1/discovery/search", {
        method: "POST",
        timeout: 9000,
        body: { ...values, limit: Number(values.limit || 10) },
      });
      const elapsedMs = Math.round(performance.now() - discoveryStartedAt);
      state.discoveryResults = result.items || [];
      state.discoveryMeta = { elapsedMs, count: result.count || 0, provider: result.provider || values.provider };
      toast(tr(`找到 ${result.count} 个未验证候选 · ${elapsedMs} ms`, `Found ${result.count} unverified candidates · ${elapsedMs} ms`));
    } else if (kind === "agent") {
      await api("/api/v1/control/agents", { method: "POST", body: values });
      toast(tr("Agent 已登记。", "Agent registered."));
    } else if (kind === "deployment") {
      requestConfirmation({
        title: tr("确认应用部署目标", "Confirm deployment target"),
        description: tr("部署会把不可变制品 Digest 绑定到指定 Agent；请先核对对象和策略。", "Deployment binds an immutable artifact Digest to the selected Agent; verify the target and policy first."),
        facts: [["Agent", values.agent_id || "—"], ["Artifact", values.snapshot_id || "—"], [tr("控制级别", "Control level"), values.control_level || "—"], [tr("策略版本", "Policy version"), "policy-ui-v1"]],
        acknowledgement: tr("我已核对 Agent、制品和控制级别，并了解撤销状态始终具有更高优先级。", "I verified the Agent, artifact, and control level and understand revocation always takes precedence."),
        confirmLabel: tr("应用部署", "Apply deployment"),
      }, async () => {
        await api("/api/v1/control/deployments", { method: "POST", body: { ...values, policy_version: "policy-ui-v1" } });
        toast(tr("制品目标状态已应用。", "Artifact desired state applied."));
      });
      return;
    } else if (kind === "revocation") {
      requestConfirmation({
        title: tr("确认紧急撤销", "Confirm emergency revocation"),
        description: tr("撤销会立即优先于部署、授权和结算资格，并保留不可删除的审计记录。", "Revocation immediately outranks deployments, entitlements, and settlement eligibility and leaves an immutable audit record."),
        facts: [["Artifact Digest", values.artifact_digest || "—"], [tr("严重级别", "Severity"), values.severity || "—"], [tr("原因", "Reason"), values.reason || "—"]],
        acknowledgement: tr("我已核对影响范围，确认需要立即阻止该制品继续部署和获得授权。", "I verified the impact scope and confirm this artifact must be blocked from deployment and entitlement immediately."),
        confirmLabel: tr("确认撤销", "Confirm revocation"), danger: true,
      }, async () => {
        await api("/api/v1/control/revocations", { method: "POST", body: values });
        toast(tr("撤销已生效，并优先传播到部署与授权。", "Revocation is active and now outranks deployments and entitlements."));
      });
      return;
    }
    await renderSection();
  } catch (error) {
    const message = error.message || tr("保存失败", "Save failed");
    if (kind === "discovery-search" && discoveryStartedAt !== null) {
      state.discoveryMeta = { elapsedMs: Math.round(performance.now() - discoveryStartedAt), count: 0, error: error.code || "request_failed" };
    }
    localError = document.createElement("p");
    localError.className = "form-error inline-form-error";
    localError.id = `${kind}-form-error`;
    localError.setAttribute("role", "alert");
    localError.textContent = message;
    form.append(localError);
    const field = form.querySelector(":invalid") || form.querySelector("input, select, textarea");
    if (field) {
      field.setAttribute("aria-invalid", "true");
      field.setAttribute("aria-describedby", localError.id);
      field.focus();
    }
  } finally {
    submit.disabled = false;
    if (kind === "discovery-search") submit.textContent = originalSubmitLabel || tr("搜索候选", "Search candidates");
  }
}

document.addEventListener("click", event => {
  const navigation = event.target.closest("[data-section]");
  if (navigation && PLATFORM_SECTIONS.has(navigation.dataset.section)) {
    state.section = navigation.dataset.section;
    if (typeof window !== "undefined" && window.history?.replaceState) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${state.section}`);
    }
    closeMobileNavigation();
    renderSection();
    return;
  }
  const language = event.target.closest("[data-language]");
  if (language) {
    state.language = language.dataset.language;
    localStorage.setItem("skillsentra-platform-language", state.language);
    localizeStatic();
    renderSection();
    return;
  }
  const action = event.target.closest("[data-action]");
  if (action) handleAction(action);
});

document.addEventListener("submit", event => {
  if (event.target.matches("[data-form]")) {
    event.preventDefault();
    handleForm(event.target);
  }
});

if (typeof window !== "undefined") {
  window.addEventListener("hashchange", () => {
    const nextSection = sectionFromLocation();
    if (nextSection === state.section) return;
    state.section = nextSection;
    closeMobileNavigation();
    if (state.session) renderSection();
  });
}

$("#auth-form").addEventListener("submit", async event => {
  event.preventDefault();
  const token = $("#access-token").value.trim();
  state.token = token;
  try {
    state.session = await api("/api/v1/session");
    sessionStorage.setItem("skillsentra-access-token", token);
    state.bootstrap = await api("/api/v1/bootstrap");
    $("#auth-error").textContent = "";
    $("#auth-gate").hidden = true;
    await renderSection();
  } catch (error) {
    state.token = "";
    sessionStorage.removeItem("skillsentra-access-token");
    $("#auth-error").textContent = error.message || tr("访问令牌无效。", "Invalid access token.");
  }
});

function closeMobileNavigation({ restoreFocus = false } = {}) {
  const menuButton = $("#mobile-menu");
  $(".platform-sidebar").classList.remove("is-open");
  $("#nav-scrim").hidden = true;
  menuButton.setAttribute("aria-expanded", "false");
  menuButton.setAttribute("aria-label", tr("打开导航", "Open navigation"));
  if (restoreFocus) menuButton.focus();
}

$("#mobile-menu").addEventListener("click", () => {
  const open = $(".platform-sidebar").classList.toggle("is-open");
  $("#nav-scrim").hidden = !open;
  $("#mobile-menu").setAttribute("aria-expanded", String(open));
  $("#mobile-menu").setAttribute("aria-label", open ? tr("关闭导航", "Close navigation") : tr("打开导航", "Open navigation"));
  if (open) $(".platform-sidebar .nav-item.is-active")?.focus();
});

$("#nav-scrim").addEventListener("click", () => closeMobileNavigation({ restoreFocus: true }));

$("#identity-button").addEventListener("click", () => {
  const session = state.session || {};
  openModal(`<span class="eyebrow">IDENTITY</span><h2 id="modal-title">${escapeHTML(session.actor_id || "—")}</h2><p>${tr("权限与租户边界由服务端执行，隐藏导航不构成授权。", "The server enforces roles and tenant boundaries; hidden navigation is never authorization.")}</p><div class="modal-grid"><div class="detail-row"><span>Tenant</span><strong>${escapeHTML(session.tenant_id || "—")}</strong></div><div class="detail-row"><span>Roles</span><strong>${escapeHTML((session.roles || []).join(", "))}</strong></div><div class="detail-row"><span>Auth</span><strong>${escapeHTML(session.auth_mode || "—")}</strong></div></div><button class="secondary-button" type="button" data-close-modal>${tr("关闭", "Close")}</button>`);
});

$("#modal-close").addEventListener("click", closeModal);
$("#modal-backdrop").addEventListener("click", event => { if (event.target === $("#modal-backdrop")) closeModal(); });
document.addEventListener("click", event => { if (event.target.closest("[data-close-modal]")) closeModal(); });
document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    if (!$("#modal-backdrop").hidden) closeModal();
    if ($(".platform-sidebar").classList.contains("is-open")) closeMobileNavigation({ restoreFocus: true });
  }
  if (event.key === "Tab" && !$("#modal-backdrop").hidden) {
    const focusable = [...$("#modal-backdrop").querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [href]')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

initialize();
