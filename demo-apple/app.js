const i18n = window.SkillSentraI18n;
const ui = value => i18n.translate(value);
const localizeUI = (root = document.body) => i18n.localize(root);
const aiTruth = window.SkillSentraAITruth;

const templateSteps = [
  { title: "明确目标", intro: "说明为谁创建、解决什么问题、何时使用以及怎样算成功。", aiTask: "从描述中提取使用者、任务、正负触发与成功输出，并指出还缺什么。" },
  { title: "借鉴比较", intro: "查找相似 Skill，比较差异并决定复用、改进或重新创建。", aiTask: "语义检索同类 Skill，比较触发、流程、资源、许可和维护成本。" },
  { title: "设定标准", intro: "提前确定评价维度、测试样例、合格条件和安全底线。", aiTask: "提出典型、边缘、对抗和不应触发样例，检查评价维度是否覆盖目标。" },
  { title: "设计内容", intro: "设计方法、操作流程、注意事项、工具和参考资料。", aiTask: "把目标转成方法、流程、失败路径、工具边界和渐进加载资源。" },
  { title: "生成检查", intro: "生成 Skill 初稿，检查结构、描述、引用、依赖和安全问题。", aiTask: "生成候选文件并进行 AI 自检，再交给确定性结构与安全规则验证。" },
  { title: "测试对比", intro: "在冻结输入和规则版本下评价候选，并保留参考与未知项。", aiTask: "基于冻结输入、候选摘要和参考摘要提出对比问题，不把未执行的任务效果写成结论。" },
  { title: "优化复测", intro: "根据测试结果进行优化，并重新验证受影响的功能。", aiTask: "定位失败原因，生成最小修改建议，并选择必须重测的受影响样例。" },
  { title: "确认交付", intro: "确认版本、交付位置、权限范围、修改摘要和回滚方式。", aiTask: "起草版本说明、已知限制、迁移说明与回滚步骤，供人工核对。" },
  { title: "持续更新", intro: "跟踪规范、依赖和使用反馈，持续形成改进建议。", aiTask: "分析规范、依赖、上游与反馈的语义变化，生成影响和重测建议。" }
];

const existingSteps = [
  { title: "导入锁定", intro: "安全读取现有 Skill，并锁定来源、版本、许可和依赖信息。", aiTask: "解释目录、入口、依赖与能力，但不执行任何导入脚本。" },
  { title: "现状评估", intro: "评价当前 Skill 的结构、效果、风险、成本和适用范围。", aiTask: "用固定样例分析结构、触发、任务增量、安全、成本与适用边界。" },
  { title: "确定方向", intro: "查看改进建议，逐项决定接受、拒绝或暂缓。", aiTask: "把问题转成收益、风险、改法、验证方式和回滚方向。" },
  { title: "实施改进", intro: "应用已接受的修改，生成结构清晰的候选版本。", aiTask: "根据已接受方向生成结构化补丁和平台可管理的候选版本。" },
  { title: "对比复测", intro: "比较原版与改进版，确认效果提升且没有新增问题。", aiTask: "对原版与候选版进行盲评、反例、回归和授权范围内的安全验证。" },
  { title: "确认升级", intro: "确认新版本、修改内容、迁移方式、交付位置和回滚方案。", aiTask: "生成版本摘要、兼容说明、迁移步骤和回滚文案，供人工核对。" },
  { title: "持续跟踪", intro: "跟踪上游更新和使用反馈，识别冲突并提出后续改进。", aiTask: "比较 Base、Local 与 Upstream，解释语义冲突并建议最小重测范围。" }
];

const journeyStages = {
  template: [
    { title: "定义", note: "目标与参考", steps: [0, 1] },
    { title: "创建", note: "标准、内容与生成", steps: [2, 3, 4] },
    { title: "验证", note: "测试、优化与复测", steps: [5, 6] },
    { title: "发布", note: "确认版本与交付", steps: [7] },
    { title: "迭代", note: "更新监测与改进", steps: [8] },
  ],
  existing: [
    { title: "定义", note: "锁定来源与基线", steps: [0, 1] },
    { title: "创建", note: "确定方向并改进", steps: [2, 3] },
    { title: "验证", note: "对比与回归复测", steps: [4] },
    { title: "发布", note: "确认升级与回滚", steps: [5] },
    { title: "迭代", note: "持续跟踪变化", steps: [6] },
  ],
};

const defaultState = () => ({
  mode: "template",
  started: false,
  backend: {
    available: false,
    projectId: "",
    projectRoute: "",
    defaultConnection: null,
    connections: [],
    lastError: "",
    projects: [],
    sources: [],
    versions: [],
    activeVersionId: "",
    validations: [],
    expertFramework: null,
    expertReviews: [],
    revisions: {},
    pricebook: { version: "local-pricebook-v1", currency: "USD" }
  },
  ai: {
    enabled: true,
    connected: false,
    connecting: false,
    provider: "OpenAI API",
    connectionName: "团队模型连接",
    connectionId: "",
    connectionError: "",
    credentialLast4: "",
    baseUrl: "https://api.openai.com/v1",
    credentialEnv: "SKILLSENTRA_OPENAI_API_KEY",
    mode: "建议模式",
    creatorModel: "",
    evaluatorModel: "",
    safetyModel: "",
    customModel: "",
    models: [],
    fallback: false,
    redact: true,
    externalFiles: false,
    budget: 20,
    runningStep: "",
    stepResults: {},
    hostRequests: [],
    syncError: ""
  },
  current: { template: 0, existing: 0 },
  completed: { template: [], existing: [] },
  template: {
    skillName: "",
    audience: "",
    goal: "",
    trigger: "",
    success: "",
    nonGoal: "",
    method: "",
    freedom: "中",
    capabilities: ["读取工作区"],
    referenceSkill: "professional-report-writing",
    reuseDecision: "扩展",
    differenceGoal: "",
    flows: ["理解输入与边界", "执行核心任务", "检查并交付结果"],
    failure: "遇到缺少来源或授权时停止，并明确指出需要用户补充的内容。",
    mustDo: "",
    mustNot: "",
    checklist: [true, true, false],
    dimensions: [
      { name: "结构有效性", detail: "命名、入口、引用和目录完整", weight: 15, on: true },
      { name: "触发质量", detail: "正例、反例与同类竞争准确", weight: 20, on: true },
      { name: "任务增量", detail: "相对无 Skill 的真实提升", weight: 25, on: true },
      { name: "稳健性", detail: "边界、失败与异常处理", weight: 15, on: true },
      { name: "成本效率", detail: "上下文与步骤不过度膨胀", weight: 10, on: true },
      { name: "安全透明", detail: "最小权限、来源与操作可解释", weight: 15, on: true }
    ],
    hardGate: true,
    staticRunning: false,
    staticPreflight: false,
    regressionRunning: false,
    regressionVerified: false,
    evaluated: false,
    running: false,
    scores: {},
    proposals: [
      { id: "negative", title: "补充两个相邻意图的负触发样例", copy: "降低简单摘要与翻译请求误触发的概率。", decision: "pending" },
      { id: "reference", title: "把详细评价规则移入 references", copy: "保留入口文件精简，并按评价阶段渐进加载。", decision: "pending" },
      { id: "failure", title: "增加来源缺失的停止条件", copy: "不能验证事实时输出 Unknown，而不是补写推测。", decision: "pending" }
    ],
    deliveryConfirmed: false,
    delivered: false,
    updates: { source: true, policy: true, manual: false },
    frequency: "每周",
    updatePolicy: "先生成三方差异，人工确认后再形成候选版本",
    updateResult: null
  },
  existing: {
    selectedSkill: "professional-report-writing",
    selectedSourceId: "",
    skillSource: "本地已安装 · 固定摘要 b84…1f",
    snapshotFrozen: false,
    baselineEvaluated: false,
    running: false,
    verified: false,
    platformized: false,
    scores: {},
    baselineScores: null,
    candidateScores: null,
    proposals: [
      { id: "scope", title: "收紧触发范围", copy: "将“任意文档”调整为“需要形成完整专业报告的任务”。", decision: "pending" },
      { id: "evidence", title: "新增来源—主张台账", copy: "区分外部事实、内部判断和行动建议。", decision: "pending" },
      { id: "router", title: "按阶段加载参考文件", copy: "减少无关上下文，同时保留深度规则。", decision: "pending" }
    ],
    deliveryConfirmed: false,
    delivered: false,
    updates: { upstream: true, security: true, manual: false },
    frequency: "每周",
    diffDetected: false,
    diffDecision: "pending",
    updateResult: null
  }
});

let state = defaultState();
let toastTimer;
let backendSaveTimer;
let backendSaveQueue = Promise.resolve();
let policySaveTimer;
let sheetReturnFocus = null;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") => String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const activeSteps = () => state.mode === "template" ? templateSteps : existingSteps;
const activeStages = () => journeyStages[state.mode];
const activeData = () => state[state.mode];
const currentIndex = () => state.current[state.mode];
const stageForStep = index => activeStages().findIndex(stage => stage.steps.includes(index));

async function apiRequest(path, options = {}) {
  const csrf = document.cookie.split(";").map(item => item.trim()).find(item => item.startsWith("skillsentra_csrf="))?.slice("skillsentra_csrf=".length) || "";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (["POST", "PUT"].includes(options.method || "GET") && csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `${ui("请求失败")}（${response.status}）`);
    error.code = payload.error?.code || "request_failed";
    error.details = payload.error?.details || {};
    throw error;
  }
  return payload.data;
}

function rememberValidation(validation) {
  if (!validation?.id) return;
  state.backend.validations = [validation, ...state.backend.validations.filter(item => item.id !== validation.id)];
}

function rememberVersion(version) {
  if (!version?.id) return;
  state.backend.versions = [version, ...state.backend.versions.filter(item => item.id !== version.id)];
  state.backend.activeVersionId = version.id;
}

function currentProjectVersion() {
  return state.backend.versions.find(item => item.id === state.backend.activeVersionId)
    || state.backend.versions[0]
    || null;
}

function latestExpertReviewFor(version) {
  if (!version?.id) return null;
  return (state.backend.expertReviews || []).find(review =>
    review.artifact_version_id === version.id
      && (!version.artifact_digest || review.artifact_digest === version.artifact_digest)
  ) || null;
}

function latestValidationFor(stage) {
  return state.backend.validations.find(item => item.stage === stage);
}

function hasPassedValidation(stage, versionId) {
  const validation = latestValidationFor(stage);
  return Boolean(validation && validation.status === "passed" && validation.version_id === versionId);
}

function deliveryGatePlan(version) {
  if (!version?.id) return [];
  const gates = [{ stage: "static", versionId: version.id }];
  if (state.mode === "template") {
    gates.push({ stage: "evaluation", versionId: version.id }, { stage: "regression", versionId: version.id });
  } else {
    gates.push({ stage: "baseline", versionId: version.base_version_id }, { stage: "regression", versionId: version.id });
  }
  return gates;
}

const validationStageLabels = { static: "静态", evaluation: "evaluation", baseline: "基线", regression: "回归" };

async function ensureDeliveryEvidence(projectId, version) {
  if (!version?.id) throw new Error("请先生成并验证候选版本");
  if (state.mode === "existing" && !version.base_version_id) throw new Error("当前候选版本缺少基线绑定，请重新结构化候选版本");
  for (const gate of deliveryGatePlan(version)) {
    if (!gate.versionId || hasPassedValidation(gate.stage, gate.versionId)) continue;
    showToast(`正在复核${validationStageLabels[gate.stage] || gate.stage}阶段门…`);
    const validation = await apiRequest(`/api/v1/projects/${projectId}/validations`, {
      method: "POST",
      body: { stage: gate.stage, version_id: gate.versionId }
    });
    rememberValidation(validation);
    if (validation.status !== "passed") {
      throw new Error(`当前版本未通过${validationStageLabels[gate.stage] || gate.stage}阶段门，请查看验证发现`);
    }
  }
}

function providerApiId(provider) {
  return ({ "内置 Mock": "mock", "OpenAI API": "openai", "兼容网关": "openai-compatible" })[provider] || "mock";
}

function providerDisplayName(providerId) {
  return ({ mock: "内置 Mock", openai: "OpenAI API", "openai-compatible": "兼容网关" })[providerId] || providerId;
}

function modeApiValue(mode) {
  return ({ "建议模式": "suggest", "自动协作": "auto", "仅人工": "manual" })[mode] || "suggest";
}

async function initializeBackend() {
  try {
    const bootstrap = await apiRequest("/api/v1/bootstrap");
    const connection = (bootstrap.connections || []).find(item => item.provider !== "mock" && item.status === "healthy")
      || (bootstrap.connections || []).find(item => item.provider !== "mock") || null;
    state.backend.available = true;
    state.backend.defaultConnection = connection;
    state.backend.connections = bootstrap.connections || [];
    state.backend.projects = bootstrap.projects || [];
    state.backend.sources = bootstrap.skill_sources || [];
    state.backend.expertFramework = bootstrap.expert_framework || null;
    state.backend.pricebook = bootstrap.pricebook || state.backend.pricebook;
    state.backend.lastError = "";
    state.ai.connectionId = connection?.id || "";
    state.ai.provider = providerDisplayName(connection?.provider || "openai");
    state.ai.connectionName = connection?.display_name || "团队模型连接";
    state.ai.credentialLast4 = connection?.credential_last4 || "";
    state.ai.connectionError = connection?.last_error || "";
    state.ai.connected = connection?.status === "healthy";
    state.ai.models = connection?.model_catalog || [];
    if (state.ai.models.length) {
      state.ai.creatorModel = state.ai.models[0].id;
      state.ai.evaluatorModel = state.ai.models[1]?.id || state.ai.models[0].id;
      state.ai.safetyModel = state.ai.models[2]?.id || state.ai.models[0].id;
    }
    $("#save-status").textContent = ui("后端与本地数据库已连接");
    const rememberedProject = localStorage.getItem("skillsentra.activeProject");
    if (rememberedProject && state.backend.projects.some(project => project.id === rememberedProject)) {
      await resumeProject(rememberedProject, false);
    }
  } catch (error) {
    state.backend.available = false;
    state.backend.lastError = error.message;
    $("#save-status").textContent = ui("离线演示模式 · 未连接后端");
  }
  renderAIStatus();
  renderInspector();
  localizeUI();
}

let projectCreation = null;
async function ensureBackendProject() {
  if (!state.backend.available || state.backend.projectId) return state.backend.projectId;
  const route = state.mode;
  const backend = state.backend;
  if (projectCreation?.route === route && projectCreation.backend === backend) return projectCreation.promise;
  const name = state.mode === "template" ? (state.template.skillName || "新 Skill") : state.existing.selectedSkill;
  const pending = { route, backend, promise: null };
  pending.promise = apiRequest("/api/v1/projects", { method: "POST", body: { name, route } }).then(project => {
    if (state.backend !== backend || state.mode !== route) throw new Error("工作项目已切换，请在当前项目重新操作");
    backend.projectId = project.id;
    backend.projectRoute = project.route;
    localStorage.setItem("skillsentra.activeProject", project.id);
    const policy = project.ai_policy || {};
    if (!state.ai.connectionId && policy.connection_id && backend.connections.some(item => item.id === policy.connection_id && item.provider !== "mock")) state.ai.connectionId = policy.connection_id;
    return project.id;
  }).finally(() => { if (projectCreation === pending) projectCreation = null; });
  projectCreation = pending;
  return pending.promise;
}

function persistActiveStep(status = "draft", index = currentIndex()) {
  if (!state.backend.available) return Promise.resolve(null);
  const route = state.mode;
  const revisions = state.backend.revisions;
  const projectPromise = ensureBackendProject();
  const stepPayload = JSON.parse(JSON.stringify({
    ...activeData(),
    _ai: state.ai.stepResults[`${route}-${index}`] || null
  }));
  const key = `${route}-${index}`;
  const operation = async () => {
    const projectId = await projectPromise;
    const saved = await apiRequest(`/api/v1/projects/${projectId}/steps/${index}`, {
      method: "PUT",
      body: {
        route,
        status,
        payload: stepPayload,
        expected_revision: revisions[key] ?? 0
      }
    });
    revisions[key] = saved.revision;
    return saved;
  };
  const result = backendSaveQueue.then(operation, operation);
  backendSaveQueue = result.catch(() => null);
  return result;
}

async function resumeProject(projectId, notify = true) {
  if (!state.backend.available) return;
  await backendSaveQueue.catch(() => null);
  const project = await apiRequest(`/api/v1/projects/${projectId}`);
  const backend = state.backend;
  state = defaultState();
  state.backend = { ...backend, revisions: {} };
  state.backend.projectId = project.id;
  state.backend.projectRoute = project.route;
  state.backend.versions = project.versions || [];
  state.backend.activeVersionId = project.active_version_id || state.backend.versions[0]?.id || "";
  state.backend.validations = project.validations || [];
  state.backend.expertReviews = project.expert_reviews || [];
  state.mode = project.route;
  state.started = true;
  state.current[project.route] = Math.min(Number(project.current_step || 0), project.route === "template" ? 8 : 6);
  const target = state[project.route];
  for (const step of project.steps || []) {
    if (step.route !== project.route) continue;
    Object.assign(target, step.payload || {});
    state.backend.revisions[`${step.route}-${step.step_index}`] = step.revision;
    if (step.status === "complete" && !state.completed[step.route].includes(step.step_index)) state.completed[step.route].push(step.step_index);
    // Suggestions are rebuilt from server call/host receipts, never trusted from editable step payloads.
  }
  const policy = project.ai_policy || {};
  const connection = state.backend.connections.find(item => item.id === policy.connection_id) || state.backend.defaultConnection;
  Object.assign(state.ai, {
    enabled: policy.enabled ?? true,
    mode: ({ suggest: "建议模式", auto: "自动协作", manual: "仅人工" })[policy.mode] || "建议模式",
    connectionId: policy.connection_id || state.ai.connectionId,
    creatorModel: policy.creator_model || state.ai.creatorModel,
    evaluatorModel: policy.evaluator_model || state.ai.evaluatorModel,
    safetyModel: policy.safety_model || state.ai.safetyModel,
    fallback: policy.fallback_enabled ?? true,
    redact: policy.redact_enabled ?? true,
    externalFiles: policy.external_files_enabled ?? false,
    budget: policy.daily_budget ?? 20,
    connected: connection?.status === "healthy" && connection?.provider !== "mock",
    provider: providerDisplayName(connection?.provider || "mock"),
    connectionName: connection?.display_name || "团队模型连接",
    models: connection?.model_catalog || [],
    credentialLast4: connection?.credential_last4 || ""
  });
  state.ai.connectionError = connection?.last_error || "";
  if (project.source_id) {
    const source = state.backend.sources.find(item => item.id === project.source_id);
    if (source) {
      state.existing.selectedSourceId = source.id;
      state.existing.selectedSkill = source.name;
      state.existing.skillSource = `${source.origin} · ${source.artifact_digest.slice(0, 18)}…`;
      state.existing.snapshotFrozen = true;
    }
  }
  localStorage.setItem("skillsentra.activeProject", project.id);
  switchMode(project.route, true);
  if (notify) showToast(`已恢复项目：${project.name}`);
}

async function refreshExpertReviews() {
  if (!state.backend.available || !state.backend.projectId) {
    return showToast("请先启动并保存当前项目，再刷新专家评价");
  }
  const button = $("[data-refresh-expert-reviews]");
  if (button) {
    button.disabled = true;
    button.textContent = "正在刷新…";
  }
  try {
    const project = await apiRequest(`/api/v1/projects/${state.backend.projectId}`);
    state.backend.versions = project.versions || state.backend.versions;
    state.backend.activeVersionId = project.active_version_id || state.backend.activeVersionId || state.backend.versions[0]?.id || "";
    state.backend.validations = project.validations || state.backend.validations;
    state.backend.expertReviews = project.expert_reviews || [];
    renderStep();
    const version = currentProjectVersion();
    const validation = version && state.backend.validations.find(item => item.version_id === version.id);
    showToast(validation ? "已刷新当前版本验证记录" : "当前版本暂无验证记录");
  } catch (error) {
    showToast(`刷新评价失败：${error.message}`);
  } finally {
    const refreshedButton = $("[data-refresh-expert-reviews]");
    if (refreshedButton) {
      refreshedButton.disabled = false;
      refreshedButton.textContent = "刷新验证状态";
    }
  }
}

function scheduleBackendSave() {
  clearTimeout(backendSaveTimer);
  if (!state.backend.available || !state.started) return;
  backendSaveTimer = setTimeout(() => {
    persistActiveStep("draft")
      .then(() => { $("#save-status").textContent = ui("已同步至本地数据库"); })
      .catch(error => {
        state.backend.lastError = error.message;
        $("#save-status").textContent = ui(error.code === "step_revision_conflict" ? "检测到版本冲突，请刷新" : "数据库同步失败");
      });
  }, 450);
}

function schedulePolicySave() {
  clearTimeout(policySaveTimer);
  if (!state.backend.available || !state.backend.projectId) return;
  policySaveTimer = setTimeout(() => {
    apiRequest(`/api/v1/projects/${state.backend.projectId}/ai-policy`, {
      method: "PUT",
      body: {
        enabled: state.ai.enabled,
        mode: modeApiValue(state.ai.mode),
        connection_id: state.ai.connectionId || null,
        creator_model: state.ai.creatorModel,
        evaluator_model: state.ai.evaluatorModel,
        safety_model: state.ai.safetyModel,
        fallback_enabled: state.ai.fallback,
        redact_enabled: state.ai.redact,
        external_files_enabled: state.ai.externalFiles,
        daily_budget: state.ai.budget
      }
    }).catch(error => { state.backend.lastError = error.message; });
  }, 250);
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = ui(message);
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2300);
}

function setSaveState(message = "正在保存…") {
  $("#save-status").textContent = ui(message);
  scheduleBackendSave();
  if (!state.backend.available) $("#save-status").textContent = ui("仅保存在当前页面");
}

function aiStepKey() {
  return `${state.mode}-${currentIndex()}`;
}

function aiInputSignature(data = activeData()) {
  const input = { ...data };
  delete input._ai;
  return JSON.stringify(input);
}

function isAIResultStale(result) {
  return Boolean(!["api", "host"].includes(result?.source) || result?.stale || (result?.source === "api" && (!result.inputSignature || result.inputSignature !== aiInputSignature())));
}

function aiModelSummary() {
  const a = state.ai;
  if (!a.enabled) return "AI 已关闭";
  if (!a.connected) return "未连接模型 API";
  return `${a.provider} · ${a.creatorModel}`;
}

function providerLabel(provider) {
  return {
    mock: "Mock 联调",
    openai: "OpenAI API",
    "openai-compatible": "兼容 API",
    chatgpt: "ChatGPT 宿主"
  }[provider] || "来源未知";
}

function contributionStatusLabel(status) {
  return {
    confirmed_chatgpt_host: "已确认 ChatGPT",
    real_model_not_chatgpt_host: "真实模型，非 ChatGPT 宿主",
    not_confirmed: "未确认 ChatGPT"
  }[status] || "未确认";
}

function renderAIStatus() {
  const a = state.ai;
  const label = $("#ai-status-label");
  const dot = $("#ai-connection-dot");
  label.textContent = !a.enabled ? "已关闭" : a.connected ? `${a.provider} · 已连接` : "API 未连接";
  dot.classList.toggle("is-connected", a.enabled && a.connected);
  $("#ai-center-button").classList.toggle("is-connected", a.enabled && a.connected);
  localizeUI($("#ai-center-button"));
}

function renderAICollab() {
  const a = state.ai;
  const step = activeSteps()[currentIndex()];
  const key = aiStepKey();
  const result = a.stepResults[key];
  const stale = result && isAIResultStale(result);
  const running = a.runningStep === key;
  const sendingHost = running && a.hostSending;
  const manualOnly = !a.enabled || a.mode === "仅人工";
  const statusText = !a.enabled ? "仅人工" : result?.source === "host" && !stale ? "宿主结果已回写" : a.connected ? a.mode : "等待 API 连接";
  const hostRequest = a.hostRequests.find(item => item.route === state.mode && Number(item.step_index) === currentIndex());
  const hostWaiting = hostRequest && !hostRequest.is_stale && ["pending", "queued", "claimed"].includes(hostRequest.status);
  const hostCommand = hostWaiting ? `$skillsentra 读取项目 ${state.backend.projectId} 的协作请求 ${hostRequest.id}，基于上下文生成建议并回写` : "";
  const sourceText = result?.source === "host" ? `${result.provider} · 宿主回写（非 API 调用证明）` : providerLabel(result?.provider);
  const usageText = result?.usageStatus === "reported" ? "供应商回执" : result?.usageStatus === "host_reported" ? "宿主申报，非供应商回执" : "用量未知";
  const resultMarkup = result ? `<div class="ai-result ${result.decision === "accepted" ? "is-accepted" : result.decision === "ignored" ? "is-ignored" : ""}">
      <span class="ai-result-label">${stale ? "历史结果 · 请基于当前输入重新发起" : result.decision === "accepted" ? "已确认建议 · 尚未应用" : result.decision === "ignored" ? "已忽略" : "AI 建议 · 待确认"}</span>
      <p data-ai-output>${esc(result.text)}</p>
      <small class="ai-result-meta">${esc(sourceText)} · ${esc(result.model || "模型未知")}</small>
      ${result.evaluator ? `<details class="ai-role-detail"><summary>查看独立评价</summary><p data-ai-output>${esc(result.evaluator)}</p></details>` : ""}
      ${result.safety ? `<details class="ai-role-detail"><summary>查看安全复核</summary><p data-ai-output>${esc(result.safety)}</p></details>` : ""}
      <div class="ai-usage-grid" aria-label="Token 用量"><span>输入 <strong>${aiTruth.tokenLabel(result.inputTokens)}</strong></span><span>输出 <strong>${aiTruth.tokenLabel(result.outputTokens)}</strong></span><span>总计 <strong>${aiTruth.tokenLabel(result.tokens)}</strong></span><small>${esc(usageText)} · ${result.cost == null ? "费用未知" : `估算费用 ${esc(result.currency)} ${Number(result.cost).toFixed(4)}`}</small></div>
      ${result.requestId ? `<small class="ai-result-meta">${esc(result.requestId)}${result.providerRequestId ? ` · ${esc(result.providerRequestId)}` : ""}</small>` : ""}
      ${result.runs?.length ? `<details class="ai-role-detail"><summary>查看逐角色调用与用量</summary>${result.runs.map(run => `<p>${esc(run.role)} · ${esc(run.status)} · ${esc(run.model)}${run.fallbackUsed ? " · 备用模型" : ""}<br>输入 ${aiTruth.tokenLabel(run.inputTokens)} / 输出 ${aiTruth.tokenLabel(run.outputTokens)} / 总计 ${aiTruth.tokenLabel(run.totalTokens)}<br><small>${esc(run.providerRequestId || run.id || "调用标识未知")}</small></p>`).join("")}</details>` : ""}
      ${result.decision === "pending" && !stale ? `<div class="ai-result-actions"><button data-ai-decision="ignored">忽略</button><button class="accept" data-ai-decision="accepted">接受建议</button></div>` : ""}
    </div>` : "";
  $("#ai-collab-card").innerHTML = `<div class="ai-collab-main">
      <span class="ai-orb" aria-hidden="true">✦</span>
      <div class="ai-collab-copy"><div class="ai-collab-title"><strong>AI 协作</strong><span>${esc(statusText)}</span></div><p>${esc(step.aiTask)}</p><small>${esc(aiModelSummary())} · 创建/评价/安全三角色</small></div>
      <div class="ai-collab-actions"><button class="quiet-button" data-ai-scope>查看输入范围</button><button class="quiet-button" data-ai-host ${a.runningStep || manualOnly || hostWaiting ? "disabled" : ""}>${sendingHost ? "发送中" : hostWaiting ? "已生成请求" : "发给 ChatGPT / Codex"}</button><button class="ai-run-button ${running && !sendingHost ? "is-running" : ""}" data-ai-run ${a.runningStep || manualOnly ? "disabled" : ""}>${manualOnly ? "仅人工模式" : running && !sendingHost ? "调用中" : "调用模型 API"}</button></div>
    </div>${hostWaiting ? `<div class="ai-sync-notice" role="status"><strong>${hostRequest.status === "claimed" ? "宿主已领取，等待结果回写" : "等待 ChatGPT / Codex 领取"}</strong><small>请求已创建。请在 ChatGPT / Codex 中调用 SkillSentra 插件领取并回写；创建请求不等于已调用模型<br>${esc(hostRequest.id)}</small><div class="host-command"><code>${esc(hostCommand)}</code><button type="button" class="quiet-button" data-copy-host-command="${esc(hostCommand)}">复制指令</button></div></div>` : ""}${a.lastRunError?.key === key ? `<div class="ai-sync-notice is-error" role="alert"><strong>本次协作未完成</strong><small>${esc(a.lastRunError.message)}</small></div>` : ""}${a.syncError ? `<div class="ai-sync-notice is-error" role="status">${esc(a.syncError)}</div>` : ""}${resultMarkup}`;
  localizeUI($("#ai-collab-card"));
}

function modeLabel(mode = state.mode) {
  return mode === "template" ? "基于模板" : "基于现有 Skill";
}

function switchMode(mode, shouldStart = state.started) {
  if (shouldStart && state.backend.projectId && state.backend.projectRoute && state.backend.projectRoute !== mode) {
    state.backend.projectId = "";
    state.backend.projectRoute = "";
    state.backend.revisions = {};
  }
  state.mode = mode;
  state.started = shouldStart;
  $$("[data-mode]").forEach(button => {
    const selected = button.dataset.mode === mode;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("#mode-step-summary").textContent = `5 个阶段 · ${activeSteps().length} 项检查`;
  $("#mode-description").textContent = mode === "template"
    ? "从目标开始，逐步生成可验证的标准 Skill。"
    : "先建立基线，再形成可验证、可回滚的新版本。";
  renderAIStatus();
  if (shouldStart) {
    $("#welcome-view").hidden = true;
    $("#step-view").hidden = false;
    $("#mobile-actions").hidden = false;
    renderStep();
  } else {
    $("#welcome-view").hidden = false;
    $("#step-view").hidden = true;
    $("#mobile-actions").hidden = true;
    renderSidebar();
    renderInspector();
  }
  localizeUI();
}

async function startMode(mode) {
  if (!state.started || state.mode !== mode) {
    state.backend.projectId = "";
    state.backend.projectRoute = "";
    state.backend.revisions = {};
  }
  switchMode(mode, true);
  $("#main-content").focus({ preventScroll: true });
  try {
    await ensureBackendProject();
    await persistActiveStep("draft");
  } catch (error) {
    state.backend.lastError = error.message;
    showToast(`后端暂不可用：${error.message}`);
  }
}

function renderSidebar() {
  const steps = activeSteps();
  const stages = activeStages();
  const completed = state.completed[state.mode];
  const current = currentIndex();
  $("#project-name").textContent = state.mode === "template"
    ? (state.template.skillName || "新 Skill")
    : state.existing.selectedSkill;
  $("#step-list").innerHTML = stages.map((stage, stageIndex) => {
    const stageDone = stage.steps.every(index => completed.includes(index));
    const stageCurrent = stage.steps.includes(current);
    const links = stage.steps.map(index => {
      const step = steps[index];
      const done = completed.includes(index);
      const reachable = index <= Math.max(current, completed.length);
      const localStepNumber = stage.steps.indexOf(index) + 1;
      const stepCode = `${stageIndex + 1}.${localStepNumber}`;
      const stateLabel = done ? `，${ui("已完成")}` : index === current ? `，${ui("当前")}` : "";
      const accessibleLabel = `${ui("阶段")} ${stageIndex + 1}，${ui("步骤")} ${index + 1}，${ui(step.title)}${stateLabel}`;
      return `<button type="button" class="step-link ${index === current ? "is-active" : ""} ${done ? "is-complete" : ""} ${reachable ? "" : "is-locked"}" data-step="${index}" data-stage="${stageIndex + 1}" data-stage-step="${localStepNumber}" aria-label="${esc(accessibleLabel)}" ${index === current ? "aria-current=\"step\"" : ""} ${reachable ? "" : "aria-disabled=\"true\""}>
        <span class="step-index" aria-hidden="true">${stepCode}</span>
        <span class="step-copy"><strong>${esc(ui(step.title))}</strong><small title="${esc(ui(step.intro))}">${esc(ui(step.intro))}</small></span>
        <span class="step-state" aria-hidden="true">${done ? "✓" : ""}</span>
      </button>`;
    }).join("");
    const stageNumber = String(stageIndex + 1).padStart(2, "0");
    const stageTotal = String(stages.length).padStart(2, "0");
    const stageTitleId = `${state.mode}-stage-${stageIndex + 1}`;
    const stageState = stageDone ? `<span class="stage-status" role="img" aria-label="${esc(ui("已完成"))}">✓</span>` : "";
    return `<section class="stage-group ${stageCurrent ? "is-current" : ""} ${stageDone ? "is-complete" : ""}" aria-labelledby="${stageTitleId}">
      <header class="stage-heading">
        <span class="stage-index" aria-hidden="true"><small>${esc(ui("阶段"))}</small><strong>${stageNumber}</strong></span>
        <span class="stage-heading-copy"><span class="stage-label">${esc(ui("阶段"))} ${stageNumber} / ${stageTotal}</span><strong id="${stageTitleId}">${esc(ui(stage.title))}</strong><small title="${esc(ui(stage.note))}">${esc(ui(stage.note))}</small></span>
        <span class="stage-meta"><span class="stage-count">${stage.steps.length} ${esc(ui("步"))}</span>${stageState}</span>
      </header>
      <div class="stage-steps">${links}</div>
    </section>`;
  }).join("");
  const percent = Math.round((completed.length / steps.length) * 100);
  $("#progress-label").textContent = `${percent}%`;
  $("#progress-fill").value = percent;
  $("#progress-fill").setAttribute("aria-valuenow", String(percent));
  localizeUI($("#step-sidebar"));
}

function renderStep() {
  const steps = activeSteps();
  const index = currentIndex();
  const stageIndex = stageForStep(index);
  const step = steps[index];
  $("#step-kicker").textContent = `阶段 ${stageIndex + 1} / 5 · 步骤 ${index + 1} / ${steps.length} · ${modeLabel()}`;
  $("#step-title").textContent = step.title;
  $("#step-intro").textContent = step.intro;
  renderAIStatus();
  renderAICollab();
  $("#step-content").innerHTML = state.mode === "template" ? renderTemplateStep(index) : renderExistingStep(index);
  $("#gate-message").hidden = true;
  $("#previous-step").disabled = index === 0;
  $("#next-step").textContent = index === steps.length - 1 ? "完成流程" : "继续";
  $("#mobile-previous").disabled = index === 0;
  $("#mobile-next").textContent = index === steps.length - 1 ? "完成" : "继续";
  $("#mobile-step-label").textContent = `步骤 ${index + 1} / ${steps.length}`;
  renderSidebar();
  renderInspector();
  localizeUI();
  $("#main-content").scrollTo({ top: 0, behavior: "smooth" });
}

function card(title, description, body, number = "") {
  return `<section class="form-card">
    <div class="card-heading"><div><h3>${title}</h3>${description ? `<p>${description}</p>` : ""}</div>${number ? `<span class="card-number">${number}</span>` : ""}</div>
    ${body}
  </section>`;
}

function field(label, key, value, options = {}) {
  const { full = false, textarea = false, placeholder = "", help = "", type = "text", required = false } = options;
  return `<label class="field ${full ? "full" : ""}"><span>${label}${required ? ` <b class="required-mark" aria-hidden="true">*</b>` : ""}</span>
    ${textarea
      ? `<textarea data-bind="${key}" ${required ? "required aria-required=\"true\"" : ""} placeholder="${esc(ui(placeholder))}">${esc(ui(value))}</textarea>`
      : `<input type="${type}" data-bind="${key}" ${required ? "required aria-required=\"true\"" : ""} value="${esc(ui(value))}" placeholder="${esc(ui(placeholder))}">`}
    ${help ? `<small>${help}</small>` : ""}
  </label>`;
}

function skillSourceCards(selectedName, selectedId = "", mode = "reference") {
  const sources = state.backend.sources || [];
  if (!sources.length) {
    return `<div class="empty-inline"><strong>尚未发现授权 Skill</strong><small>请在服务端配置授权 Skill 根目录，然后刷新项目中心。</small></div>`;
  }
  return sources.map(source => {
    const selected = selectedId ? source.id === selectedId : source.name === selectedName;
    const description = source.metadata?.description || "已完成静态读取与内容摘要";
    const attribute = mode === "source" ? `data-source-id="${esc(source.id)}" data-skill="${esc(source.name)}"` : `data-reference="${esc(source.name)}"`;
    return `<button type="button" class="skill-option ${selected ? "is-selected" : ""}" ${attribute} aria-pressed="${selected}"><strong>${esc(source.name)}</strong><small>${esc(description)}</small><span class="skill-meta"><span>${esc(source.origin || "local")}</span><span>${esc(String(source.artifact_digest || "").slice(0, 18))}…</span></span></button>`;
  }).join("");
}

function expertReviewCard() {
  const version = currentProjectVersion();
  const refreshControl = state.backend.available && state.backend.projectId
    ? `<button type="button" class="quiet-button expert-refresh-button" data-refresh-expert-reviews>刷新验证状态</button>`
    : "";
  const validation = version && state.backend.validations.find(item => item.version_id === version.id);
  const proposal = state.ai.stepResults[aiStepKey()];
  const verification = validation ? `${validation.stage} · ${validation.status}` : "当前版本暂无验证记录";
  const evidenceKind = validation?.evidence?.evaluation_kind || validation?.evidence?.kind || "";
  return card(
    "协作与验证证据",
    "AI 建议、人工确认、验证记录与发布授权分别记录，不以建议或分数代替验证",
    `<div class="delivery-facts"><div class="fact"><span>AI 建议</span><strong>${proposal ? "已有建议，查看上方结果" : "本步尚无真实协作结果"}</strong></div><div class="fact"><span>人工确认</span><strong>${proposal?.decision === "accepted" ? "已确认建议，尚未应用" : "待人工审阅"}</strong></div><div class="fact"><span>验证证据</span><strong>${esc(verification)}</strong></div><div class="fact"><span>发布授权</span><strong>未由本卡片授权</strong></div></div><div class="ai-sync-notice"><strong>${version ? `绑定版本 ${esc(version.version || version.id)}` : "先生成候选版本，再绑定验证证据"}</strong><small>${esc(evidenceKind)} 本地结构检查不等于真实任务效果，宿主回写不等于发布批准</small>${refreshControl}</div>`,
    ""
  );
}

function renderTemplateStep(index) {
  const d = state.template;
  if (index === 0) {
    return card("目标与边界", "用具体任务语言描述，不需要编写技术格式。", `<div class="field-grid">
      ${field("Skill 名称", "skillName", d.skillName, { required: true, placeholder: "例如 research-brief-builder", help: "建议使用小写字母与连字符" })}
      ${field("主要使用者", "audience", d.audience, { required: true, placeholder: "例如 研究员、咨询顾问" })}
      ${field("要解决的问题", "goal", d.goal, { required: true, full: true, textarea: true, placeholder: "用户现在遇到什么问题？这个 Skill 如何帮助他？" })}
      ${field("何时触发", "trigger", d.trigger, { full: true, placeholder: "例如：需要把多份资料整理成可决策简报时" })}
      ${field("成功输出", "success", d.success, { required: true, full: true, placeholder: "例如：结论先行的简报、来源台账和风险项" })}
      ${field("明确不做", "nonGoal", d.nonGoal, { full: true, placeholder: "例如：不代替法律判断，不补写没有来源的事实" })}
    </div>`, "01");
  }
  if (index === 1) {
    return card("已有 Skill 候选", "先比较触发、流程、资源、许可和固定版本，再决定如何创建。", `<div class="skill-picker">
      ${skillSourceCards(d.referenceSkill)}
    </div>`, "02") + card("复用决策", "借鉴的是可追溯模式，不复制未知许可内容。", `<div class="field-grid"><div class="field full"><span>采用方式</span><div class="segmented" role="group" aria-label="采用方式">${["复用", "扩展", "新建"].map(v => `<button type="button" data-choice="reuseDecision" data-value="${v}" class="${d.reuseDecision === v ? "is-selected" : ""}">${v}</button>`).join("")}</div></div>${field("差异化目标", "differenceGoal", d.differenceGoal, { required: true, full: true, textarea: true, placeholder: "说明保留什么、改变什么，以及为什么不能直接使用已有 Skill。" })}</div><div class="notice warning notice-spaced"><span class="notice-icon">!</span><div><strong>许可与来源阶段门</strong><small>当前只借鉴抽象结构；来源摘要、采用决定和差异化目标将写入决策记录。</small></div></div>`);
  }
  if (index === 2) {
    const total = d.dimensions.filter(x => x.on).reduce((sum, x) => sum + Number(x.weight || 0), 0);
    return card("评价契约", "先固定怎么判断成功，再开始编写候选 Skill。", `<div class="notice"><span class="notice-icon">i</span><div><strong>评价证据分层</strong><small>结构与危险模式由确定性规则给出；触发、任务质量、稳健性与成本根据冻结输入和制品计算；AI 评价单独记录，不能替代硬门。</small></div></div>`, "03") + card("评价维度与权重", `已启用维度的权重合计必须为 100%，当前为 ${total}%。`, `<div class="dimension-list">${d.dimensions.map((dim, i) => `<label class="dimension-row"><input type="checkbox" data-dimension-on="${i}" ${dim.on ? "checked" : ""}><span class="dimension-copy"><strong>${esc(dim.name)}</strong><small>${esc(dim.detail)}</small></span><input class="weight-input" type="number" min="0" max="100" data-dimension-weight="${i}" value="${Number(dim.weight) || 0}" aria-label="${esc(dim.name)}权重"></label>`).join("")}</div>`) + card("发布硬门", "硬门失败不能被平均分补偿。", `<label class="toggle-row"><div><strong>启用结构、安全与来源硬门</strong><small>Critical Finding = 0；来源和许可状态可确认。</small></div><span class="switch"><input type="checkbox" data-bind-check="hardGate" ${d.hardGate ? "checked" : ""}><i></i></span></label>`);
  }
  if (index === 3) {
    const checkItems = ["输出前复核来源与主张是否对应", "检查是否越过权限和任务边界", "对无法验证的内容明确标记 Unknown"];
    return card("核心方法与能力", "说明工作原则、自主空间和允许使用的能力。", `<div class="field-grid">${field("创建方法", "method", d.method, { required: true, full: true, textarea: true, placeholder: "例如：先确认目标与证据范围，再提取事实，分层判断，最后按交付契约检查。" })}<div class="field full"><span>自主空间</span><div class="segmented" role="group" aria-label="自主空间">${["低", "中", "高"].map(v => `<button type="button" data-choice="freedom" data-value="${v}" class="${d.freedom === v ? "is-selected" : ""}">${v}</button>`).join("")}</div><small>高自主空间需要更严格的动态安全验证。</small></div><div class="field full"><span>允许使用的能力</span><div class="chip-group">${["读取工作区", "联网检索", "运行脚本", "外部写入"].map(v => `<button type="button" class="chip ${d.capabilities.includes(v) ? "is-selected" : ""}" data-chip="capabilities" data-value="${v}">${v}</button>`).join("")}</div></div></div>`, "04") + card(`执行流程与失败路径 <b class="required-mark" aria-hidden="true">*</b>`, "至少填写三个步骤；失败路径与主流程同等重要。", `<div class="repeat-list">${d.flows.map((flow, i) => `<div class="repeat-row"><span class="repeat-index">${i + 1}</span><input data-flow-index="${i}" value="${esc(ui(flow))}" aria-label="流程步骤 ${i + 1}" required aria-required="true"><button class="row-action" data-remove-flow="${i}" aria-label="删除流程步骤 ${i + 1}">×</button></div>`).join("")}<button class="add-row" type="button" data-add-flow>＋ 添加流程步骤</button></div><div class="field-grid action-spaced">${field("输入、证据或权限不足时", "failure", d.failure, { full: true, textarea: true })}</div>`) + card("关键约束与资源路由", "详细规则按需进入 references，重复且脆弱的动作才进入 scripts。", `<div class="field-grid">
      ${field("必须做到", "mustDo", d.mustDo, { required: true, full: true, textarea: true, placeholder: "例如：事实必须对应来源，结论必须说明依据。" })}
      ${field("绝不能做", "mustNot", d.mustNot, { required: true, full: true, textarea: true, placeholder: "例如：不得编造来源，不得静默执行外部写入。" })}
      <div class="field full"><span>交付前检查</span><div class="check-list">
        ${checkItems.map((text, i) => `<label class="check-row"><input type="checkbox" data-check-index="${i}" ${d.checklist[i] ? "checked" : ""}><span>${text}</span><small>${d.checklist[i] ? "已启用" : "未启用"}</small></label>`).join("")}
      </div></div>
    </div>`);
  }
  if (index === 4) {
    return card("候选结构", "先生成到隔离草稿，不安装、不发布、不执行第三方脚本。", `<pre class="file-tree">research-brief-builder/
├── <b>SKILL.md</b>
├── references/
│   ├── evaluation-rubric.md
│   └── source-rules.md
└── evals/
    └── cases.json</pre>`, "05") + card("静态预检", "结构、发现性、许可、依赖和安全必须先通过。", `<div class="layer-grid">${[
      ["S1", "格式结构", d.staticPreflight ? "通过" : "待检查", d.staticPreflight ? "pass" : ""],
      ["S2", "触发描述", d.staticPreflight ? "通过" : "待检查", d.staticPreflight ? "pass" : ""],
      ["S3", "引用层级", d.staticPreflight ? "1 层" : "待检查", d.staticPreflight ? "pass" : ""],
      ["S4", "依赖许可", d.staticPreflight ? "可确认" : "待检查", d.staticPreflight ? "pass" : ""],
      ["S5", "Secret/危险指令", d.staticPreflight ? "0 项" : "待检查", d.staticPreflight ? "pass" : ""]
    ].map(x => `<div class="layer-card ${x[3]}"><span>${x[0]}</span><strong>${x[1]}</strong><small>${x[2]}</small></div>`).join("")}</div><button class="run-button action-spaced ${d.staticRunning ? "is-running" : ""}" data-run-static ${d.staticRunning ? "disabled" : ""}>${d.staticRunning ? "正在预检" : d.staticPreflight ? "重新预检" : "运行静态预检"}</button>`);
  }
  if (index === 5) {
    return card("冻结评价世界", "候选摘要、规则版本和评价输入一同保存，便于以后复核。", `<div class="run-panel"><div><strong>本地确定性评价</strong><div class="run-world"><span class="world-chip">ruleset: local-v1</span><span class="world-chip">scripts: not-run</span><span class="world-chip">digest: fixed</span></div></div><button class="run-button ${d.running ? "is-running" : ""}" data-run-template ${d.running ? "disabled" : ""}>${d.running ? "正在评价" : d.evaluated ? "重新评价" : "开始评价"}</button></div>`, "06") +
      card("分层结果", "L5 只有在另行授权沙箱执行后才能得出结论。", `<div class="layer-grid">
        ${[
          ["L1", "结构", d.evaluated ? "通过" : "待运行", d.evaluated ? "pass" : ""],
          ["L2", "静态安全", d.evaluated ? "通过" : "待运行", d.evaluated ? "pass" : ""],
          ["L3", "触发证据", d.evaluated ? `${(Number(d.scores.trigger || 0) / 10).toFixed(1)} / 10` : "待运行", d.evaluated ? "pass" : ""],
          ["L4", "任务质量", d.evaluated ? `${(Number(d.scores.quality || 0) / 10).toFixed(1)} / 10` : "待运行", d.evaluated ? "pass" : ""],
          ["L5", "动态安全", "未授权", "warning"]
        ].map(x => `<div class="layer-card ${x[3]}"><span>${x[0]}</span><strong>${x[1]}</strong><small>${x[2]}</small></div>`).join("")}
      </div>`) + expertReviewCard();
  }
  if (index === 6) {
    return card("结构化评价", "总分只用于概览，改进仍按维度与证据逐项判断。", `<div class="score-grid">${scoreTiles(d.scores)}</div>`) +
      card("改进建议", "以下为预置人工检查清单，不是本轮 AI 输出；接受后必须运行受影响回归", `<div class="proposal-list">${proposalCards(d.proposals)}</div>`) +
      card("影响分析与回归", "触发、资源路由和失败路径证据已被修改影响。", `<div class="notice ${d.regressionVerified ? "success" : "warning"}"><span class="notice-icon">${d.regressionVerified ? "✓" : "!"}</span><div><strong>${d.regressionVerified ? "受影响证据已刷新" : "3 项证据等待回归"}</strong><small>${d.regressionVerified ? "未发现关键退化；L5 仍保持未授权。" : "只有受影响的评价切片需要重跑，未修改证据保持有效。"}</small></div></div><button class="run-button action-spaced ${d.regressionRunning ? "is-running" : ""}" data-run-regression ${d.regressionRunning ? "disabled" : ""}>${d.regressionRunning ? "正在回归" : d.regressionVerified ? "重新回归" : "运行受影响回归"}</button>`);
  }
  if (index === 7) {
    const version = state.backend.versions[0];
    return card("交付预览", "生成不可变 ZIP；不会写入 Skill 安装目录。", `<pre class="file-tree">${esc(d.skillName || "generated-skill")}/
├── <b>SKILL.md</b>
├── references/
│   ├── evaluation-rubric.md
│   └── source-rules.md
└── evals/
    └── cases.json</pre>`) +
      card("版本与目标", "交付前确认摘要、权限和恢复位置。", `<div class="delivery-facts"><div class="fact"><span>版本</span><strong>${esc(version?.version || "待生成")}</strong></div><div class="fact"><span>摘要</span><strong>${esc(String(version?.artifact_digest || "待生成").slice(0, 22))}</strong></div><div class="fact"><span>状态</span><strong>${esc(version?.status || "待生成")}</strong></div><div class="fact"><span>回滚点</span><strong>上一不可变候选</strong></div></div><div class="notice ${d.deliveryConfirmed ? "success" : "warning"} notice-spaced"><span class="notice-icon">${d.deliveryConfirmed ? "✓" : "!"}</span><div><strong>${d.deliveryConfirmed ? "交付包已生成" : "需要人工确认"}</strong><small>${d.deliveryConfirmed ? "可以下载 ZIP，原目录未被安装或执行。" : "确认后由后端重新检查当前版本阶段门。"}</small></div></div>${version?.download_url ? `<a class="download-button" href="${esc(version.download_url)}">下载交付 ZIP</a>` : `<button class="run-button action-spaced" data-open-delivery>${d.deliveryConfirmed ? "查看交付确认" : "确认交付信息"}</button>`}`);
  }
  return card("更新来源", "只订阅你明确允许的来源；更新不会自动覆盖本地版本。", `<div class="toggle-row"><div><strong>参考 Skill 与方法规则</strong><small>发现依赖的 Skill 或模式发生变化。</small></div>${toggle("updates.source", d.updates.source)}</div><div class="toggle-row"><div><strong>平台结构与安全策略</strong><small>发现结构规范或安全基线升级。</small></div>${toggle("updates.policy", d.updates.policy)}</div><div class="toggle-row"><div><strong>手动上传的本地材料</strong><small>仅在你主动选择后纳入比较。</small></div>${toggle("updates.manual", d.updates.manual)}</div>`, "09") +
    card("更新策略", "保存计划并运行真实 Base / Local / Upstream 摘要比较。", `<div class="field-grid"><label class="field"><span>检查频率</span><select data-bind="frequency"><option value="每天" ${d.frequency === "每天" ? "selected" : ""}>每天</option><option value="每周" ${d.frequency === "每周" ? "selected" : ""}>每周</option><option value="每月" ${d.frequency === "每月" ? "selected" : ""}>每月</option></select></label>${field("变更策略", "updatePolicy", d.updatePolicy, { full: true, textarea: true })}</div>${updateResultMarkup(d.updateResult)}<button class="run-button action-spaced" data-run-update>${d.updateResult ? "重新检查" : "保存并检查更新"}</button>`);
}

function renderExistingStep(index) {
  const d = state.existing;
  if (index === 0) {
    const source = state.backend.sources.find(item => item.id === d.selectedSourceId);
    return card("选择已授权 Skill", "只从服务端授权目录静态读取，不运行其中的脚本。", `<div class="skill-picker">${skillSourceCards(d.selectedSkill, d.selectedSourceId, "source")}</div>`) + card("安全导入与版本冻结", "来源、摘要、许可和依赖冻结后，后续修改才有可靠基线。", `<div class="delivery-facts"><div class="fact"><span>来源</span><strong>${esc(source?.origin || "待选择")}</strong></div><div class="fact"><span>固定摘要</span><strong>${esc(String(source?.artifact_digest || "待冻结").slice(0, 22))}</strong></div><div class="fact"><span>文件</span><strong>${source?.manifest?.file_count ?? 0}</strong></div><div class="fact"><span>导入方式</span><strong>静态读取 · 不执行</strong></div></div><div class="notice ${d.snapshotFrozen ? "success" : "warning"} notice-spaced"><span class="notice-icon">${d.snapshotFrozen ? "✓" : "!"}</span><div><strong>${d.snapshotFrozen ? "不可变基线已冻结" : "尚未冻结版本"}</strong><small>符号链接、Secret 文件和越过授权根目录的路径会被后端阻断。</small></div></div><button class="run-button action-spaced" data-freeze-snapshot>${d.snapshotFrozen ? "重新校验快照" : "冻结来源与版本"}</button>`);
  }
  if (index === 1) {
    return card("冻结评价世界", "先评价原 Skill，形成可复核的制品基线和问题证据。", `<div class="run-panel"><div><strong>${esc(d.selectedSkill)}</strong><div class="run-world"><span class="world-chip">baseline: fixed</span><span class="world-chip">ruleset: local-v1</span><span class="world-chip">scripts: not-run</span></div></div><button class="run-button ${d.running ? "is-running" : ""}" data-run-baseline ${d.running ? "disabled" : ""}>${d.running ? "正在评价" : d.baselineEvaluated ? "重新评价" : "开始基线评价"}</button></div>${d.baselineEvaluated ? `<div class="score-grid action-spaced">${scoreTiles(d.baselineScores || d.scores)}</div><div class="notice warning notice-spaced"><span class="notice-icon">!</span><div><strong>基线问题来自可查看证据</strong><small>评价分数、静态发现、规则版本与制品摘要已保存在验证记录中。</small></div></div>` : ""}`, "02") + expertReviewCard();
  }
  if (index === 2) {
    return card("修改假设", "以下为预置人工检查清单，不是本轮 AI 输出；请结合当前 Skill 的证据逐项审阅", `<div class="proposal-list">${proposalCards(d.proposals)}</div>`, "03") + `<div class="notice warning"><span class="notice-icon">!</span><div><strong>没有静默改写</strong><small>只有接受的方向会进入候选版本；拒绝或暂缓决定同样保留。</small></div></div>`;
  }
  if (index === 3) {
    return card("平台结构预览", "在候选版本中完成改动与资源路由，原 Skill 保持不变。", `<pre class="file-tree">professional-report-writing-next/
├── <b>SKILL.md</b>
├── references/
│   └── <span class="added">skillsentra-improvements.md</span>
└── <span class="added">原有文件按清单复制</span></pre>`) + card("结构化动作", "结构调整将使触发、路由和任务证据暂时失效，必须在下一步回归。", `<div class="check-list"><div class="check-row"><span>绑定固定来源摘要与许可状态</span><small>已准备</small></div><div class="check-row"><span>把评价基线绑定到候选版本</span><small>已准备</small></div><div class="check-row"><span>标记受影响证据为 Stale</span><small>3 项</small></div></div><button class="run-button action-spaced" data-platformize>${d.platformized ? "重新生成候选结构" : "实施修改并结构化"}</button>`, "04");
  }
  if (index === 4) {
    const baseline = d.baselineScores || {};
    const candidate = d.candidateScores || {};
    const comparisonRows = [
      ["触发质量", "trigger"],
      ["任务质量", "quality"],
      ["稳健性", "robust"],
      ["安全透明", "safety"]
    ].map(([label, key]) => {
      const before = Number(baseline[key]);
      const after = Number(candidate[key]);
      const hasBefore = Number.isFinite(before);
      const hasAfter = d.verified && Number.isFinite(after);
      const delta = hasBefore && hasAfter ? (after - before) / 10 : null;
      const deltaClass = delta > 0 ? "delta-up" : "delta-same";
      return `<div class="baseline-row"><strong>${label}</strong><span>${hasBefore ? (before / 10).toFixed(1) : "—"}</span><span>${hasAfter ? (after / 10).toFixed(1) : "—"}</span><span class="${deltaClass}">${delta === null ? "待验证" : `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`}</span></div>`;
    }).join("");
    return card("稳定版与候选版", "使用相同评测世界，按硬门和维度比较。", `<div class="baseline-table"><div class="baseline-row head"><span>评价维度</span><span>稳定版</span><span>候选版</span><span>变化</span></div>
      ${comparisonRows}
      <div class="baseline-row"><strong>动态安全 L5</strong><span>未授权</span><span>未授权</span><span class="delta-same">保持 Unknown</span></div>
    </div><div class="notice ${d.verified ? "success" : "warning"} notice-spaced"><span class="notice-icon">${d.verified ? "✓" : "!"}</span><div><strong>${d.verified ? "改进通过且未发现关键退化" : "结构化后的证据等待回归"}</strong><small>L5 动态安全未授权，继续保持 Unknown，不参与虚假通过。</small></div></div><button class="run-button action-spaced ${d.running ? "is-running" : ""}" data-run-existing ${d.running ? "disabled" : ""}>${d.running ? "正在验证" : d.verified ? "重新验证" : "开始对照与回归"}</button>`, "05");
  }
  if (index === 5) {
    const version = state.backend.versions[0];
    return card("候选版本", "新版本与原版本并存，可随时回退。", `<div class="delivery-facts"><div class="fact"><span>基础摘要</span><strong>${esc(String(state.backend.sources.find(item => item.id === d.selectedSourceId)?.artifact_digest || "—").slice(0, 18))}</strong></div><div class="fact"><span>候选版本</span><strong>${esc(version?.version || "待生成")}</strong></div><div class="fact"><span>修改方向</span><strong>${d.proposals.filter(p => p.decision === "accept").length} 项已接受</strong></div><div class="fact"><span>回滚</span><strong>原 Skill 保持不变</strong></div></div><div class="notice ${d.deliveryConfirmed ? "success" : "warning"} notice-spaced"><span class="notice-icon">${d.deliveryConfirmed ? "✓" : "!"}</span><div><strong>${d.deliveryConfirmed ? "ZIP 与回滚信息已生成" : "等待交付确认"}</strong><small>交付只生成隔离制品，不覆盖已安装 Skill。</small></div></div>${version?.download_url ? `<a class="download-button" href="${esc(version.download_url)}">下载交付 ZIP</a>` : `<button class="run-button action-spaced" data-open-delivery>${d.deliveryConfirmed ? "查看交付确认" : "确认候选交付"}</button>`}`, "06");
  }
  return card("监测来源", "更新检查与候选交付解耦，不会自动覆盖本地改进。", `<div class="toggle-row"><div><strong>上游 Skill 版本</strong><small>重新计算授权来源的完整文件摘要。</small></div>${toggle("updates.upstream", d.updates.upstream)}</div><div class="toggle-row"><div><strong>平台安全基线</strong><small>检测新的静态规则和权限策略。</small></div>${toggle("updates.security", d.updates.security)}</div><div class="toggle-row"><div><strong>手动材料</strong><small>只在主动选择后参与比较。</small></div>${toggle("updates.manual", d.updates.manual)}</div>`, "07") +
    card("三方差异", "基础版用于判定本地修改和上游修改是否冲突。", `${updateResultMarkup(d.updateResult)}<button class="run-button action-spaced" data-detect-diff>${d.diffDetected ? "重新检查" : "检查更新"}</button>`);
}

function updateResultMarkup(result) {
  if (!result) return `<div class="notice"><span class="notice-icon">i</span><div><strong>尚未检查更新</strong><small>运行检查后会显示 Base、Local 与 Upstream 的文件级证据。</small></div></div>`;
  const diff = result.diff || { local_changes: [], upstream_changes: [], conflicts: [] };
  return `<div class="update-summary"><div><span>本地变化</span><strong>${diff.local_changes.length}</strong></div><div><span>上游变化</span><strong>${diff.upstream_changes.length}</strong></div><div><span>冲突</span><strong>${diff.conflicts.length}</strong></div></div><div class="notice ${diff.conflicts.length ? "warning" : "success"} notice-spaced"><span class="notice-icon">${diff.conflicts.length ? "!" : "✓"}</span><div><strong>${result.status === "no_change" ? "没有变化" : diff.conflicts.length ? "需要人工处理冲突" : "变化可独立审阅"}</strong><small>比较基于版本化 SHA-256 文件清单，没有自动合并或覆盖。</small></div></div>`;
}

function scoreTiles(scores) {
  if (!scores || !Object.values(scores).some(value => typeof value === "number" && Number.isFinite(value))) return `<div class="empty-inline"><small>尚无已执行的评价记录，不显示预置分数</small></div>`;
  const map = { structure: "结构", trigger: "触发", quality: "任务增量", robust: "稳健", cost: "成本", safety: "安全" };
  return Object.entries(scores).filter(([, value]) => typeof value === "number" && Number.isFinite(value)).map(([key, value]) => {
    const score = Math.max(0, Math.min(100, Number(value) || 0));
    return `<div class="score-tile"><span>${esc(map[key] || key)}</span><strong>${(score / 10).toFixed(1)}</strong><progress class="score-bar" max="100" value="${score}" aria-label="${esc(map[key] || key)}评分"></progress></div>`;
  }).join("");
}

function proposalCards(proposals) {
  return proposals.map(p => `<article class="proposal-card ${p.decision === "accept" ? "is-accepted" : p.decision === "reject" ? "is-rejected" : ""}" data-proposal-card="${esc(p.id)}"><div><h4>${esc(p.title)}</h4><p>${esc(p.copy)}</p></div><div class="proposal-actions"><button data-proposal="${esc(p.id)}" data-decision="reject">${p.decision === "reject" ? "已拒绝" : "拒绝"}</button><button class="accept" data-proposal="${esc(p.id)}" data-decision="accept">${p.decision === "accept" ? "已接受" : "接受"}</button></div></article>`).join("");
}

function toggle(key, checked) {
  const label = ({
    "updates.source": "监测参考 Skill 与方法规则",
    "updates.policy": "监测平台结构与安全策略",
    "updates.upstream": "监测上游 Skill 版本",
    "updates.security": "监测平台安全基线",
    "updates.manual": "监测手动材料"
  })[key] || key;
  return `<label class="switch"><input type="checkbox" aria-label="${esc(label)}" data-nested-check="${key}" ${checked ? "checked" : ""}><i></i></label>`;
}

function renderInspector() {
  const container = $("#inspector-content");
  if (!state.started) {
    container.innerHTML = `<section class="inspector-card ai-inspector-card"><h3>AI 协作</h3><div class="blueprint-line ${state.ai.connected ? "is-ready" : ""}"><i></i><span>${aiModelSummary()}</span><strong>${state.ai.connected ? state.ai.mode : "可选"}</strong></div></section><section class="inspector-card"><h3>创建闭环</h3>${["目标与边界", "方法与流程", "评价与试运行", "交付与更新"].map(x => `<div class="blueprint-line"><i></i><span>${x}</span><strong>待开始</strong></div>`).join("")}</section><div class="inspector-note"><strong>先选择创建路线</strong><small>每一步的输入都会实时映射成 Skill 结构和验证状态。</small></div>`;
    localizeUI(container);
    return;
  }
  const d = activeData();
  const completed = state.completed[state.mode];
  const lines = state.mode === "template"
    ? [
      ["目标", Boolean(d.goal && d.audience), d.goal || "待定义"],
      ["复用", completed.includes(1), `${d.reuseDecision} · ${d.referenceSkill}`],
      ["评价", completed.includes(2), `${d.dimensions.filter(x => x.on).length} 个维度`],
      ["设计", completed.includes(3), `${d.flows.length} 个步骤`],
      ["预检", d.staticPreflight, d.staticPreflight ? "Critical = 0" : "待运行"],
      ["试运行", d.evaluated, d.evaluated ? "本地静态评价已运行" : "未运行"],
      ["交付", d.deliveryConfirmed, d.deliveryConfirmed ? "候选版" : "待确认"],
      ["更新", completed.includes(8), completed.includes(8) ? "已配置" : "待配置"]
    ]
    : [
      ["冻结", d.snapshotFrozen, d.snapshotFrozen ? "摘要已固定" : "待确认"],
      ["基线", d.baselineEvaluated, d.selectedSkill],
      ["改进", completed.includes(2), `${d.proposals.filter(p => p.decision === "accept").length} 项接受`],
      ["结构化", d.platformized, d.platformized ? "候选结构" : "待确认"],
      ["回归", d.verified, d.verified ? "对照通过" : "待运行"],
      ["交付", d.deliveryConfirmed, d.deliveryConfirmed ? "rc.1" : "待确认"],
      ["更新", d.diffDetected, d.diffDetected ? `${d.updateResult?.diff?.conflicts?.length || 0} 个冲突` : "待检查"]
    ];
  const scores = state.mode === "template" && d.evaluated ? d.scores : state.mode === "existing" && d.baselineEvaluated ? d.scores : null;
  const latestVersion = state.backend.versions[0];
  const digestText = latestVersion
    ? `${latestVersion.status}:${latestVersion.artifact_digest}`
    : state.mode === "existing" && d.snapshotFrozen ? `base:${d.skillSource}` : "候选版本尚未生成";
  container.innerHTML = `<section class="inspector-card"><h3>${state.mode === "template" ? "结构状态" : "改进状态"}</h3>${lines.map(x => `<div class="blueprint-line ${x[1] ? "is-ready" : ""}"><i></i><span title="${esc(x[2])}">${esc(x[0])} · ${esc(x[2])}</span><strong>${x[1] ? "就绪" : "待办"}</strong></div>`).join("")}</section>
    ${scores ? `<section class="inspector-card"><h3>评价侧写</h3>${Object.entries(scores).slice(0,4).map(([key, value]) => { const score = Math.max(0, Math.min(100, Number(value) || 0)); const label = ({structure:"结构",trigger:"触发",quality:"增量",robust:"稳健"})[key] || key; return `<div class="mini-score"><span>${esc(label)}</span><progress max="100" value="${score}" aria-label="${esc(label)}评分"></progress><strong>${(score/10).toFixed(1)}</strong></div>`; }).join("")}</section>` : ""}
    <section class="inspector-card"><h3>版本指纹</h3><code class="digest">${esc(digestText)}</code></section>
    <div class="inspector-note"><strong>${esc(inspectorHint())}</strong><small>状态来自当前输入、后端验证和不可变制品；未授权的动态安全始终保持 Unknown。</small></div>`;
  localizeUI(container);
}

function inspectorHint() {
  const index = currentIndex();
  if (state.mode === "template") {
    return ["目标会生成触发与输出契约", "先比较，再决定复用、扩展或新建", "先定义怎么测，再开始生成", "失败路径与主流程同样重要", "静态硬门先于动态试运行", "L5 未授权时必须保持 Unknown", "改动会使相关证据失效并触发回归", "交付的是精确版本与回滚点", "更新先比较，再决定"][index];
  }
  return ["导入不等于执行，先冻结不可变版本", "用代表性任务定位真实问题", "修改假设不会自动写入", "原 Skill 保持不变，证据标记失效", "全部改动后再做对照与回归", "新旧版本可以并存回退", "三方差异保护本地改进"][index];
}

function validateCurrent() {
  const index = currentIndex();
  const d = activeData();
  const invalid = (message, selectors = []) => ({ message, selectors });
  if (state.mode === "template") {
    if (index === 0) {
      const missing = ["skillName", "audience", "goal", "success"].filter(key => !String(d[key] || "").trim());
      if (missing.length) return invalid("请填写标记的必填项：Skill 名称、主要使用者、目标和成功输出。", missing.map(key => `[data-bind='${key}']`));
    }
    if (index === 1 && !(d.reuseDecision && d.differenceGoal.trim())) return invalid("请选择复用方式，并说明与已有 Skill 的差异化目标。", d.differenceGoal.trim() ? ["[aria-label='采用方式'] button"] : ["[data-bind='differenceGoal']"]);
    if (index === 2) {
      const total = d.dimensions.filter(x => x.on).reduce((sum, x) => sum + Number(x.weight || 0), 0);
      if (total !== 100) return invalid(`当前评价权重合计为 ${total}%，请调整为 100%。`, ["[data-dimension-weight]"]);
    }
    if (index === 3) {
      const missing = [];
      if (!d.method.trim()) missing.push("[data-bind='method']");
      d.flows.forEach((flow, flowIndex) => { if (!flow.trim()) missing.push(`[data-flow-index='${flowIndex}']`); });
      if (d.flows.length < 3) missing.push("[data-add-flow]");
      if (!d.mustDo.trim()) missing.push("[data-bind='mustDo']");
      if (!d.mustNot.trim()) missing.push("[data-bind='mustNot']");
      if (missing.length) return invalid("请完成标记的核心方法、至少三个流程步骤、必须做到和绝不能做。", missing);
    }
    if (index === 4 && !d.staticPreflight) return invalid("请先运行静态预检，确认结构、来源、依赖和安全硬门。", ["[data-run-static]"]);
    if (index === 5 && !d.evaluated) return invalid("请先完成一次本地确定性评价，再进入优化。", ["[data-run-template]"]);
    if (index === 6 && !d.proposals.some(p => p.decision !== "pending")) return invalid("请至少审阅并决定一条改进建议。", ["[data-proposal]"]);
    if (index === 6 && !d.regressionVerified) return invalid("请完成修改影响分析后的受影响回归，再进入交付。", ["[data-run-regression]"]);
    if (index === 7 && !d.deliveryConfirmed) return invalid("请先确认交付版本、目标和回滚信息。", ["[data-open-delivery]"]);
  } else {
    if (index === 0 && !d.snapshotFrozen) return invalid("请先固定来源、摘要、许可和依赖，形成不可变基线。", ["[data-freeze-snapshot]"]);
    if (index === 1 && !d.baselineEvaluated) return invalid("请先运行基线评价，定位现有 Skill 的真实问题。", ["[data-run-baseline]"]);
    if (index === 2 && !d.proposals.some(p => p.decision === "accept")) return invalid("请至少接受一个修改假设，形成候选版本。", ["[data-proposal][data-decision='accept']"]);
    if (index === 3 && !d.platformized) return invalid("请确认生成平台可管理的候选结构。", ["[data-platformize]"]);
    if (index === 4 && !d.verified) return invalid("请先完成所有修改后的对照、回归与安全验证。", ["[data-run-existing]"]);
    if (index === 5 && !d.deliveryConfirmed) return invalid("请先确认候选版本、迁移说明与回滚点。", ["[data-open-delivery]"]);
    if (index === 6 && !d.diffDetected) return invalid("请至少运行一次三方更新检查。", ["[data-detect-diff]"]);
  }
  return null;
}

async function nextStep() {
  clearTimeout(backendSaveTimer);
  $$('[aria-invalid="true"]', $("#step-content")).forEach(element => { element.removeAttribute("aria-invalid"); element.removeAttribute("aria-describedby"); });
  $$(".field.is-error", $("#step-content")).forEach(element => element.classList.remove("is-error"));
  const error = validateCurrent();
  const gate = $("#gate-message");
  if (error) {
    gate.textContent = error.message;
    gate.hidden = false;
    const targets = error.selectors.flatMap(selector => $$(selector, $("#step-content")));
    targets.forEach(element => {
      if (element.matches("input, textarea, select")) {
        element.setAttribute("aria-invalid", "true");
        element.setAttribute("aria-describedby", "gate-message");
        element.closest(".field")?.classList.add("is-error");
      }
    });
    const first = targets[0] || gate;
    first.focus?.({ preventScroll: true });
    first.scrollIntoView({ behavior: "auto", block: "center" });
    return;
  }
  gate.hidden = true;
  const index = currentIndex();
  try {
    await persistActiveStep("complete", index);
  } catch (saveError) {
    if (state.backend.available) {
      gate.textContent = `无法保存本步：${saveError.message}`;
      gate.hidden = false;
      return;
    }
  }
  const completed = state.completed[state.mode];
  if (!completed.includes(index)) completed.push(index);
  if (index < activeSteps().length - 1) {
    state.current[state.mode] += 1;
    renderStep();
  } else {
    activeData().delivered = true;
    renderStep();
    openCompletionSheet();
  }
  setSaveState();
}

function previousStep() {
  if (currentIndex() === 0) return;
  state.current[state.mode] -= 1;
  renderStep();
}

function openSheet(html, variant = "") {
  if ($("#sheet-backdrop").hidden) sheetReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  $("#sheet-content").innerHTML = html;
  $("#sheet-panel").className = `sheet ${variant}`.trim();
  $("#sheet-backdrop").hidden = false;
  $("#app-window").inert = true;
  document.body.classList.add("sheet-open");
  localizeUI($("#sheet-panel"));
  $("#sheet-close").focus({ preventScroll: true });
}

function closeSheet() {
  $("#sheet-backdrop").hidden = true;
  $("#app-window").inert = false;
  document.body.classList.remove("sheet-open");
  if (sheetReturnFocus?.isConnected) sheetReturnFocus.focus({ preventScroll: true });
  sheetReturnFocus = null;
}

function selectOptions(values, selected) {
  return values.map(value => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
}

function modelOptions(models, selected) {
  const items = models.length ? models : [{ id: selected || "待同步", label: selected || "待同步" }];
  return items.map(model => `<option value="${esc(model.id)}" ${model.id === selected ? "selected" : ""}>${esc(model.label || model.id)} · ${esc(model.id)}</option>`).join("");
}

function openAICenter() {
  const a = state.ai;
  const providers = ["OpenAI API", "兼容网关"];
  const connectionStatus = !a.enabled ? "AI 已关闭" : a.connecting ? "正在测试" : a.connected ? "连接正常" : "尚未连接";
  const external = a.provider !== "内置 Mock";
  const connectionHint = a.connectionError
    ? `<div class="notice warning compact"><span class="notice-icon">!</span><div><strong>连接未完成</strong><small>${esc(a.connectionError)} 请在启动后端服务的环境中配置 ${esc(a.credentialEnv)}，然后重新测试连接。</small></div></div>`
    : "";
  openSheet(`<div class="ai-settings">
    <span class="eyebrow">全过程 AI · ${state.backend.available ? "数据库已连接" : "离线演示"}</span><div class="ai-settings-heading"><div><h2 id="sheet-title">AI 控制中心</h2><p>为创建、评价和安全复核选择模型。每一步都可以单独运行或关闭。</p></div><span class="connection-badge ${a.connected ? "is-connected" : ""}"><i></i>${connectionStatus}</span></div>
    <div class="notice warning"><span class="notice-icon">!</span><div><strong>API Key 永不进入浏览器</strong><small>这里只填写服务端环境变量名称；后端调用模型时再读取真实凭据，数据库仅保存连接引用和末四位。</small></div></div>${connectionHint}
    <section class="ai-settings-section"><div class="settings-section-title"><div><strong>1. 选择接入方式</strong><small>可使用宿主模型、供应商 API 或组织自建网关。</small></div><label class="switch"><input type="checkbox" aria-label="启用全过程 AI" data-ai-check="enabled" ${a.enabled ? "checked" : ""}><i></i></label></div>
      <div class="provider-grid compact">${providers.map(provider => `<button data-ai-provider="${provider}" class="provider-card ${provider === a.provider ? "is-selected" : ""}"><span class="provider-icon">${provider === "内置 Mock" ? "⌘" : provider.includes("OpenAI") ? "◉" : "↗"}</span><span><strong>${provider}</strong><small>${provider === "内置 Mock" ? "零密钥开发联调" : provider === "兼容网关" ? "企业代理或私有服务" : "Responses API"}</small></span></button>`).join("")}</div>
      <div class="field-grid ai-connection-fields"><label class="field"><span>连接名称</span><input data-ai-setting="connectionName" value="${esc(a.connectionName)}"></label><label class="field"><span>API Endpoint</span><input data-ai-setting="baseUrl" value="${external ? esc(a.baseUrl) : "由内置适配器提供"}" ${external ? "" : "disabled"}></label><label class="field full"><span>服务端凭据引用</span><input data-ai-setting="credentialEnv" value="${external ? esc(a.credentialEnv) : "不需要凭据"}" ${external ? "" : "disabled"}><small>支持 OPENAI_API_KEY 或 SKILLSENTRA_ 开头的服务端环境变量名称；请勿在此粘贴真实 Key。</small></label></div>
      <div class="connection-actions"><span>${a.connected ? `连接 ID：${esc(a.connectionId)} · ${a.models.length} 个模型已同步${a.credentialLast4 ? ` · 凭据末四位 ${esc(a.credentialLast4)}` : ""}` : state.backend.available ? "连接后由后端读取模型目录并记录审计事件。" : "请先通过本地服务启动完整联调模式。"}</span><button class="secondary-button" data-ai-connect ${a.connecting || !a.enabled || !state.backend.available ? "disabled" : ""}>${a.connecting ? "正在测试…" : a.connected ? "重新测试连接" : external ? "连接并测试" : "连接开发模型"}</button></div>
    </section>
    <section class="ai-settings-section"><div class="settings-section-title"><div><strong>2. 分配模型角色</strong><small>创建与评价默认分离，降低模型自评偏差。</small></div></div>
      <div class="field-grid three"><label class="field"><span>创建模型</span><select data-ai-setting="creatorModel">${modelOptions(a.models, a.creatorModel)}</select></label><label class="field"><span>评价模型</span><select data-ai-setting="evaluatorModel">${modelOptions(a.models, a.evaluatorModel)}</select></label><label class="field"><span>安全复核</span><select data-ai-setting="safetyModel">${modelOptions(a.models, a.safetyModel)}</select></label></div>
      <p class="settings-footnote">模型列表来自后端连接测试；每次调用会固定实际模型、输入摘要、用量、耗时和请求 ID。费用由服务端价格表 ${esc(state.backend.pricebook.version)} 计算。</p>
    </section>
    <section class="ai-settings-section"><div class="settings-section-title"><div><strong>3. 控制运行与数据</strong><small>默认先给建议，到确定性阶段门自动暂停。</small></div></div>
      <div class="field-grid"><label class="field"><span>运行方式</span><select data-ai-setting="mode">${selectOptions(["建议模式", "自动协作", "仅人工"], a.mode)}</select></label><label class="field"><span>项目每日预算</span><input type="number" min="0" data-ai-setting="budget" value="${a.budget}"><small>按服务端价格表币种控制；实际费用以供应商账单复核。</small></label></div>
      <div class="settings-toggle-list"><div class="toggle-row"><div><strong>启用备用模型</strong><small>模型故障时可见地降级；交付硬门默认失败关闭。</small></div><label class="switch"><input type="checkbox" aria-label="启用备用模型" data-ai-check="fallback" ${a.fallback ? "checked" : ""}><i></i></label></div><div class="toggle-row"><div><strong>发送前脱敏</strong><small>自动移除疑似密钥、个人信息和无关字段。</small></div><label class="switch"><input type="checkbox" aria-label="发送前脱敏" data-ai-check="redact" ${a.redact ? "checked" : ""}><i></i></label></div><div class="toggle-row"><div><strong>允许外部文件</strong><small>默认关闭；打开后每次调用仍展示实际文件清单。</small></div><label class="switch"><input type="checkbox" aria-label="允许外部文件" data-ai-check="externalFiles" ${a.externalFiles ? "checked" : ""}><i></i></label></div></div>
    </section>
    <div class="sheet-actions"><button class="primary-button" data-close-sheet>完成设置</button></div>
  </div>`, "ai-sheet");
}

function openAIScope() {
  const step = activeSteps()[currentIndex()];
  const a = state.ai;
  openSheet(`<span class="eyebrow">调用前预览</span><h2 id="sheet-title">本步会发送什么</h2><p>${esc(step.aiTask)}</p><div class="scope-grid"><div class="scope-card allow"><strong>本次包含</strong><ul><li>当前步骤已填写字段</li><li>必要的蓝图摘要与阶段门状态</li><li>已选择参考 Skill 的名称和固定摘要</li></ul></div><div class="scope-card deny"><strong>默认不包含</strong><ul><li>API Key 与任何 Secret</li><li>无关工作区文件和历史对话</li><li>${a.externalFiles ? "未主动选择的外部文件" : "全部外部文件"}</li></ul></div></div><div class="delivery-facts action-spaced"><div class="fact"><span>模型</span><strong>${esc(aiModelSummary())}</strong></div><div class="fact"><span>运行方式</span><strong>${esc(a.mode)}</strong></div><div class="fact"><span>脱敏</span><strong>${a.redact ? "已开启" : "未开启"}</strong></div><div class="fact"><span>预算上限</span><strong>${Number(a.budget) || 0} / 天</strong></div></div><div class="sheet-actions"><button class="primary-button" data-close-sheet>返回本步</button></div>`);
}

async function connectDemoAI() {
  const a = state.ai;
  a.connecting = true;
  openAICenter();
  try {
    let connectionId = a.connectionId;
    const expectedProvider = providerApiId(a.provider);
    if (!connectionId) {
      const created = await apiRequest("/api/v1/ai/connections", {
        method: "POST",
        body: {
          provider: expectedProvider,
          display_name: a.connectionName,
          base_url: a.baseUrl,
          credential_env: a.credentialEnv
        }
      });
      connectionId = created.id;
    }
    const tested = await apiRequest(`/api/v1/ai/connections/${connectionId}/test`, { method: "POST", body: {} });
    a.connecting = false;
    a.connected = tested.status === "healthy";
    a.connectionId = tested.id;
    a.connectionError = tested.last_error || "";
    state.backend.connections = [tested, ...state.backend.connections.filter(item => item.id !== tested.id)];
    a.credentialLast4 = tested.credential_last4 || "";
    a.models = tested.model_catalog || [];
    if (a.models.length) {
      a.creatorModel = a.models[0].id;
      a.evaluatorModel = a.models[1]?.id || a.models[0].id;
      a.safetyModel = a.models[2]?.id || a.models[0].id;
    }
    await ensureBackendProject();
    schedulePolicySave();
    renderAIStatus();
    if (state.started) { renderAICollab(); renderInspector(); }
    openAICenter();
    showToast(`${a.provider} 连接测试通过；模型目录已同步`);
  } catch (error) {
    a.connecting = false;
    a.connected = false;
    a.connectionError = error.message;
    state.backend.lastError = error.message;
    renderAIStatus();
    if (state.started) { renderAICollab(); renderInspector(); }
    openAICenter();
    showToast(`连接失败：${error.message}`);
  }
}

async function runAIStep() {
  const a = state.ai;
  if (a.runningStep) return;
  if (!a.enabled || a.mode === "仅人工") return showToast("当前为仅人工模式，未调用 AI");
  if (!a.connected || providerApiId(a.provider) === "mock") {
    showToast("请先连接模型 API");
    return openAICenter();
  }
  const key = aiStepKey();
  const route = state.mode;
  const stepIndex = currentIndex();
  const task = activeSteps()[stepIndex].aiTask;
  const stepSnapshot = JSON.parse(JSON.stringify(activeData()));
  delete stepSnapshot._ai;
  const blueprintHint = inspectorHint();
  a.runningStep = key;
  a.lastRunError = null;
  renderAICollab();
  try {
    if (!state.backend.available) throw new Error("后端未连接");
    const projectId = await ensureBackendProject();
    await persistActiveStep("draft", stepIndex);
    await apiRequest(`/api/v1/projects/${projectId}/ai-policy`, {
      method: "PUT",
      body: {
        enabled: a.enabled,
        mode: modeApiValue(a.mode),
        connection_id: a.connectionId,
        creator_model: a.creatorModel,
        evaluator_model: a.evaluatorModel,
        safety_model: a.safetyModel,
        fallback_enabled: a.fallback,
        redact_enabled: a.redact,
        external_files_enabled: a.externalFiles,
        daily_budget: a.budget
      }
    });
    const orchestration = await apiRequest(`/api/v1/projects/${projectId}/ai-orchestrations`, {
      method: "POST",
      body: {
        connection_id: a.connectionId,
        route,
        step_index: stepIndex,
        require_real: true,
        roles: ["creator", "evaluator", "safety"],
        task,
        context: { step: stepSnapshot, blueprint_hint: blueprintHint }
      }
    });
    const result = aiTruth.resultFromOrchestration(orchestration);
    result.inputSignature = aiInputSignature(stepSnapshot);
    result.timestamp = new Date().toISOString();
    a.runningStep = "";
    if (state.backend.projectId !== projectId || state.ai !== a) return;
    a.stepResults[key] = result;
    renderAICollab();
    renderInspector();
    showToast("三角色真实 API 结果已回写，等待人工审阅");
  } catch (error) {
    a.runningStep = "";
    a.lastRunError = { key, message: error.message };
    renderAICollab();
    showToast(`AI 调用失败：${error.message}`);
  }
}

async function requestHostAI() {
  const a = state.ai;
  if (a.runningStep || !a.enabled || a.mode === "仅人工") return;
  const route = state.mode;
  const stepIndex = currentIndex();
  const key = aiStepKey();
  const instruction = activeSteps()[stepIndex].aiTask;
  a.runningStep = key;
  a.hostSending = true;
  a.lastRunError = null;
  renderAICollab();
  try {
    if (!state.backend.available) throw new Error("后端未连接");
    const projectId = await ensureBackendProject();
    await persistActiveStep("draft", stepIndex);
    const request = await apiRequest(`/api/v1/projects/${projectId}/host-requests`, {
      method: "POST", body: { route, step_index: stepIndex, instruction }
    });
    if (state.backend.projectId !== projectId || state.ai !== a) return;
    a.hostRequests = [request, ...a.hostRequests.filter(item => item.id !== request.id)];
    showToast("请求已保存，等待 ChatGPT / Codex 插件领取");
  } catch (error) {
    a.lastRunError = { key, message: error.message };
    showToast(`宿主协作请求失败：${error.message}`);
  } finally {
    a.runningStep = "";
    a.hostSending = false;
    if (state.ai === a && state.started) renderAICollab();
  }
}

let aiSyncInFlight = false;
let aiDecisionInFlight = false;
async function syncAIResults() {
  if (aiSyncInFlight || aiDecisionInFlight || document.hidden || !state.started || !state.backend.available || !state.backend.projectId || state.ai.runningStep) return;
  const projectId = state.backend.projectId;
  const a = state.ai;
  const before = JSON.stringify({ results: a.stepResults, requests: a.hostRequests, error: a.syncError });
  aiSyncInFlight = true;
  try {
    const [requests, runs] = await Promise.all([
      apiRequest(`/api/v1/projects/${projectId}/host-requests`),
      apiRequest(`/api/v1/projects/${projectId}/ai-runs?limit=100`)
    ]);
    if (aiDecisionInFlight || state.backend.projectId !== projectId || state.ai !== a) return;
    a.hostRequests = Array.isArray(requests) ? requests : [];
    a.syncError = "";
    const candidates = [];
    for (const request of a.hostRequests) {
      const result = aiTruth.resultFromHost(request);
      const key = `${request.route}-${request.step_index}`;
      if (result) candidates.push({ key, result });
    }
    const orchestrationIds = [...new Set((runs || []).filter(run => run.orchestration_id).map(run => run.orchestration_id))];
    for (const id of orchestrationIds) {
      const attempts = runs.filter(run => run.orchestration_id === id);
      const first = attempts[0];
      const key = `${first.route}-${first.step_index}`;
      try {
        const successful = ["creator", "evaluator", "safety"].map(role => attempts.find(run => run.role === role && run.status === "completed")).filter(Boolean);
        const result = aiTruth.resultFromOrchestration({ id, status: "completed", runs: successful, attempts, currency: first.currency,
          total_cost: attempts.reduce((sum, run) => sum + Number(run.estimated_cost || 0), 0) });
        result.stale = true;
        candidates.push({ key, result });
      } catch (_) { /* Incomplete, failed or simulated runs never become a proposal. */ }
    }
    const completedKeys = new Set();
    candidates.sort((left, right) => (Date.parse(right.result.timestamp) || 0) - (Date.parse(left.result.timestamp) || 0));
    for (const { key, result } of candidates) {
      if (completedKeys.has(key)) continue;
      completedKeys.add(key);
      const prior = a.stepResults[key];
      if (prior?.requestId === result.requestId) {
        if (result.source === "host") prior.stale = result.stale;
      } else if (!prior || (Date.parse(result.timestamp) || 0) > (Date.parse(prior.timestamp) || 0)) {
        a.stepResults[key] = result;
      }
    }
    if (before !== JSON.stringify({ results: a.stepResults, requests: a.hostRequests, error: a.syncError })) renderAICollab();
  } catch (error) {
    if (state.backend.projectId === projectId && state.ai === a) {
      a.syncError = `协作同步失败：${error.message}`;
      renderAICollab();
    }
  } finally { aiSyncInFlight = false; }
}

function assertHostResultCurrent(request, result) {
  const contextStep = Array.isArray(request?.context?.steps)
    ? request.context.steps.find(item => item?.step_index === request.step_index)
    : null;
  if (request?.id !== result.hostRequestId || request.context_current !== true || request.is_stale ||
      !contextStep || aiInputSignature(contextStep.payload || {}) !== aiInputSignature()) {
    result.stale = true;
    throw new Error("输入已变化，请重新发起协作");
  }
}

async function rebaseHostDecisionRevision(projectId, key, result, decision) {
  const [request, project] = await Promise.all([
    apiRequest(`/api/v1/projects/${projectId}/host-requests/${result.hostRequestId}`),
    apiRequest(`/api/v1/projects/${projectId}`)
  ]);
  assertHostResultCurrent(request, result);
  if (key !== aiStepKey() || state.backend.projectId !== projectId || state.ai.stepResults[key] !== result) throw new Error("工作项目已切换，请在当前项目重新操作");
  const step = (project.steps || []).find(item => item.route === state.mode && item.step_index === currentIndex());
  if (!step) throw new Error("当前步骤尚未保存，请刷新后重试");
  if (aiInputSignature(step.payload || {}) !== aiInputSignature()) {
    result.stale = true;
    throw new Error("输入已在其他页面变化，请刷新后重新发起协作");
  }
  const persisted = step.payload?._ai;
  if (persisted && (persisted.requestId !== result.requestId || !["pending", decision].includes(persisted.decision || "pending"))) {
    throw new Error("此建议已在其他页面处理，请刷新后确认");
  }
  state.backend.revisions[key] = step.revision;
}

async function persistHostReviewState(projectId, key, result, decision) {
  try {
    return await persistActiveStep("draft");
  } catch (error) {
    if (error.code !== "step_revision_conflict") throw error;
    await rebaseHostDecisionRevision(projectId, key, result, decision);
    return persistActiveStep("draft");
  }
}

async function decideAIResult(decision) {
  if (aiDecisionInFlight) return;
  const key = aiStepKey();
  const result = state.ai.stepResults[key];
  if (!result) return;
  const previousDecision = result.decision;
  aiDecisionInFlight = true;
  try {
    if (isAIResultStale(result)) throw new Error("输入已变化，请重新发起协作");
    if (result.source === "host") {
      const projectId = state.backend.projectId;
      const request = await apiRequest(`/api/v1/projects/${projectId}/host-requests/${result.hostRequestId}`);
      assertHostResultCurrent(request, result);
      if (key !== aiStepKey() || state.backend.projectId !== projectId || state.ai.stepResults[key] !== result) return;
      result.decision = decision;
      await persistHostReviewState(projectId, key, result, decision);
      if (key !== aiStepKey() || state.backend.projectId !== projectId || state.ai.stepResults[key] !== result) return;
      $("#save-status").textContent = ui("已同步至本地数据库");
    } else {
      result.decision = decision;
      setSaveState();
    }
    showToast(decision === "accepted" ? "已确认建议，内容尚未自动应用；请编辑后验证" : "AI 建议已忽略并记录决定");
  } catch (error) {
    result.decision = previousDecision;
    showToast(error.message);
  } finally {
    aiDecisionInFlight = false;
  }
  renderAICollab();
  renderInspector();
}

function openGuide() {
  const step = activeSteps()[currentIndex()];
  openSheet(`<span class="eyebrow">本步说明</span><h2 id="sheet-title">${step.title}</h2><p>${step.intro}</p><div class="notice"><span class="notice-icon">i</span><div><strong>${inspectorHint()}</strong><small>填写内容会实时进入右侧蓝图；只有通过本步阶段门后才能继续。</small></div></div><div class="sheet-actions"><button class="primary-button" data-close-sheet>知道了</button></div>`);
}

function openDeliverySheet() {
  const existing = state.mode === "existing";
  const version = state.backend.versions[0];
  const gateSummary = existing ? "静态、基线与回归" : "静态、evaluation 与回归";
  openSheet(`<span class="eyebrow">交付确认</span><h2 id="sheet-title">${existing ? "确认候选版本" : "确认 Skill 包"}</h2><p>系统会把经过${gateSummary}阶段门的当前候选目录压缩为不可变 ZIP，并记录版本与 SHA-256；不会安装、执行或覆盖原 Skill。</p>
    <div class="delivery-facts"><div class="fact"><span>版本</span><strong>${esc(version?.version || "待生成")}</strong></div><div class="fact"><span>摘要</span><strong>${esc(String(version?.artifact_digest || "待生成").slice(0, 22))}</strong></div><div class="fact"><span>目标</span><strong>隔离制品目录</strong></div><div class="fact"><span>回滚</span><strong>${existing ? "原 Skill 保持不变" : "保留上一候选版本"}</strong></div></div>
    <label class="check-row action-spaced"><input type="checkbox" id="delivery-agreement"><span>我已确认版本、目标、权限边界和回滚信息</span></label>
    <div class="sheet-actions"><button class="secondary-button" data-close-sheet>取消</button><button class="primary-button" data-confirm-delivery>确认交付信息</button></div>`);
}

async function confirmDelivery() {
  const confirmButton = $("[data-confirm-delivery]");
  if (confirmButton) {
    confirmButton.disabled = true;
    confirmButton.textContent = "正在复核阶段门…";
  }
  try {
    const projectId = await ensureBackendProject();
    const version = state.backend.versions[0];
    if (!version) throw new Error("请先生成并验证候选版本");
    await ensureDeliveryEvidence(projectId, version);
    const delivered = await apiRequest(`/api/v1/projects/${projectId}/deliveries`, {
      method: "POST",
      body: { version_id: version.id }
    });
    rememberVersion(delivered);
    activeData().deliveryConfirmed = true;
    closeSheet();
    renderStep();
    showToast("交付 ZIP、版本摘要与回滚记录已生成");
  } catch (error) {
    showToast(`交付失败：${error.message}`);
  } finally {
    if (confirmButton && document.body.contains(confirmButton)) {
      confirmButton.disabled = false;
      confirmButton.textContent = "确认交付信息";
    }
  }
}

async function openProjectCenter() {
  openSheet(`<span class="eyebrow">项目、版本与审计</span><h2 id="sheet-title">项目中心</h2><div class="manager-loading" role="status">正在读取本地项目记录…</div>`, "manager-sheet");
  try {
    const projects = await apiRequest("/api/v1/projects");
    state.backend.projects = projects;
    let detail = null;
    let audit = [];
    let runs = [];
    let updates = [];
    let contribution = null;
    if (state.backend.projectId) {
      [detail, audit, runs, updates, contribution] = await Promise.all([
        apiRequest(`/api/v1/projects/${state.backend.projectId}`),
        apiRequest(`/api/v1/projects/${state.backend.projectId}/audit?limit=20`),
        apiRequest(`/api/v1/projects/${state.backend.projectId}/ai-runs?limit=20`),
        apiRequest(`/api/v1/projects/${state.backend.projectId}/update-checks?limit=10`),
        apiRequest(`/api/v1/projects/${state.backend.projectId}/ai-contribution`)
      ]);
    }
    const projectRows = projects.length ? projects.map(project => `<button type="button" class="manager-row ${project.id === state.backend.projectId ? "is-current" : ""}" data-resume-project="${esc(project.id)}"><span><strong>${esc(project.name)}</strong><small>${project.route === "template" ? "模板创建" : "现有 Skill 改进"} · ${esc(project.status)}</small></span><span class="manager-row-meta">${project.id === state.backend.projectId ? "当前" : "继续"}</span></button>`).join("") : `<div class="empty-inline"><strong>还没有项目</strong><small>从任一创建路线开始后，项目会出现在这里。</small></div>`;
    const versions = detail?.versions || [];
    const versionRows = versions.length ? versions.map(version => `<div class="ledger-row"><span><strong>${esc(version.version)}</strong><small>${esc(version.artifact_digest.slice(0, 24))}…</small></span><span class="ledger-status">${esc(version.status)}</span>${version.download_url ? `<a href="${esc(version.download_url)}">下载</a>` : ""}</div>`).join("") : `<div class="empty-inline"><small>当前项目还没有候选版本。</small></div>`;
    const auditRows = audit.length ? audit.map(item => `<div class="ledger-row"><span><strong>${esc(item.action)}</strong><small>${esc(item.entity_type)} · ${esc(formatTime(item.created_at))}</small></span><code>${esc(item.actor_type)}</code></div>`).join("") : `<div class="empty-inline"><small>选择项目后显示审计事件。</small></div>`;
    const contributionRows = contribution ? `<div class="contribution-summary"><div><span>ChatGPT 贡献</span><strong>${esc(contributionStatusLabel(contribution.chatgpt_contribution_status))}</strong></div><div><span>${contribution.usage_complete ? "总 Token" : "已知 Token 小计（含模拟）"}</span><strong>${aiTruth.tokenLabel(contribution.total_tokens)}</strong></div><div><span>模拟 Token（不计入真实 AI）</span><strong>${aiTruth.tokenLabel(contribution.mock_tokens)}</strong></div><div><span>未知用量调用</span><strong>${aiTruth.tokenLabel(contribution.unknown_usage_run_count)}</strong></div>${(contribution.warnings || []).length ? `<small>${esc(contribution.warnings.join(" "))}</small>` : ""}</div>` : "";
    const runRows = runs.length ? runs.map(run => `<div class="ledger-row"><span><strong>${esc(run.role)} · ${esc(run.provider_model || run.model)}</strong><small>${esc(run.status)} · ${esc(providerLabel(run.connection_provider))} · 输入 ${aiTruth.tokenLabel(run.input_tokens)} / 输出 ${aiTruth.tokenLabel(run.output_tokens)} / 总计 ${aiTruth.tokenLabel(run.total_tokens)} Token · ${esc(run.usage_status || "unknown")} · ${run.estimated_cost == null ? "费用未知" : `估算费用 ${esc(run.currency)} ${Number(run.estimated_cost).toFixed(4)}`}</small><small>${esc(run.provider_request_id || "供应商请求 ID 未知")}</small></span><code>${esc(run.orchestration_id || run.id)}</code></div>`).join("") : `<div class="empty-inline"><small>当前项目还没有 AI 调用。</small></div>`;
    const updateRows = updates.length ? updates.map(update => `<div class="ledger-row"><span><strong>${esc(update.status)}</strong><small>${formatTime(update.created_at)} · 冲突 ${update.diff?.conflicts?.length || 0}</small></span><code>${esc(update.id)}</code></div>`).join("") : `<div class="empty-inline"><small>当前项目还没有更新检查。</small></div>`;
    $("#sheet-content").innerHTML = `<span class="eyebrow">项目、版本与审计</span><div class="manager-heading"><div><h2 id="sheet-title">项目中心</h2><p>恢复工作、核对版本、AI 费用与更新证据。</p></div><button type="button" class="secondary-button" data-refresh-catalog>刷新 Skill</button></div><div class="manager-grid"><section class="manager-section projects"><h3>项目</h3><div class="manager-list">${projectRows}</div></section><section class="manager-section"><h3>不可变版本</h3><div class="ledger-list">${versionRows}</div></section><section class="manager-section"><h3>AI 调用台账</h3>${contributionRows}<div class="ledger-list">${runRows}</div></section><section class="manager-section"><h3>更新检查</h3><div class="ledger-list">${updateRows}</div></section><section class="manager-section wide"><h3>最近审计</h3><div class="ledger-list">${auditRows}</div></section></div><div class="sheet-actions"><button class="primary-button" data-close-sheet>完成</button></div>`;
    localizeUI($("#sheet-panel"));
  } catch (error) {
    $("#sheet-content").innerHTML = `<span class="eyebrow">项目中心</span><h2 id="sheet-title">无法读取项目</h2><div class="notice danger"><span class="notice-icon">!</span><div><strong>${esc(error.message)}</strong><small>请确认本地服务和数据库可用。</small></div></div>`;
    localizeUI($("#sheet-panel"));
  }
}

async function refreshSkillCatalog() {
  try {
    state.backend.sources = await apiRequest("/api/v1/skill-sources");
    showToast(`已刷新 ${state.backend.sources.length} 个授权 Skill`);
    await openProjectCenter();
  } catch (error) {
    showToast(`刷新失败：${error.message}`);
  }
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(i18n.isEnglish() ? "en-US" : "zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function openCompletionSheet() {
  const d = activeData();
  const delivered = state.backend.versions.find(version => version.status === "delivered");
  openSheet(`<span class="eyebrow">流程完成</span><h2 id="sheet-title">${state.mode === "template" ? "Skill 交付包已准备" : "改进版交付包已准备"}</h2><p>项目、验证证据、不可变版本、交付记录和动态更新策略已经关联。</p><div class="notice success"><span class="notice-icon">✓</span><div><strong>可追溯闭环完成</strong><small>${state.mode === "template" ? "9 个步骤" : "7 个步骤"}均已通过后端阶段门；交付包不会自动安装或执行。</small></div></div><div class="delivery-facts action-spaced"><div class="fact"><span>交付版本</span><strong>${esc(delivered?.version || "已交付")}</strong></div><div class="fact"><span>动态更新</span><strong>${esc(d.frequency || "已配置")}</strong></div></div><div class="sheet-actions"><button class="secondary-button" data-close-sheet>返回工作台</button><button class="primary-button" data-switch-finished>体验另一条路线</button></div>`);
}

async function loadDemo() {
  const mode = state.mode;
  const backend = state.backend;
  state = defaultState();
  state.backend = backend;
  state.backend.projectId = "";
  state.backend.projectRoute = "";
  state.backend.revisions = {};
  state.mode = mode;
  state.started = true;
  const connection = backend.defaultConnection;
  if (connection) {
    Object.assign(state.ai, {
      connected: connection.status === "healthy",
      provider: providerDisplayName(connection.provider),
      connectionName: connection.display_name,
      connectionId: connection.id,
      credentialLast4: connection.credential_last4 || "",
      models: connection.model_catalog || []
    });
    if (state.ai.models.length) {
      state.ai.creatorModel = state.ai.models[0].id;
      state.ai.evaluatorModel = state.ai.models[1]?.id || state.ai.models[0].id;
      state.ai.safetyModel = state.ai.models[2]?.id || state.ai.models[0].id;
    }
  }
  Object.assign(state.template, {
    skillName: "research-brief-builder",
    audience: "研究员、咨询顾问与决策支持人员",
    goal: "将分散的研究材料整理成有来源、可复核、可直接支持决策的中文简报。",
    trigger: "用户要求整理调研、比较多个方案或形成决策简报时",
    success: "结论先行的 Markdown 简报、逐条来源台账和风险项",
    nonGoal: "不处理简单翻译或单句润色；不补写没有来源的事实",
    differenceGoal: "保留结论先行的报告骨架，新增逐条来源台账，并把外部事实、内部判断和行动建议分层。",
    method: "先确认目标与证据范围，再提取外部事实，区分内部判断与建议，最后按输出契约和安全边界复核。",
    mustDo: "事实逐条对应来源；判断说明依据；建议明确行动主体。",
    mustNot: "不得编造案例、数字或来源；不得静默扩大权限或执行外部写入。"
  });
  state.existing.selectedSkill = "professional-report-writing";
  const demoSource = backend.sources.find(item => item.name === "professional-report-writing") || backend.sources[0];
  if (demoSource) {
    state.existing.selectedSkill = demoSource.name;
    state.existing.selectedSourceId = demoSource.id;
    state.existing.skillSource = `${demoSource.origin} · ${demoSource.artifact_digest.slice(0, 18)}…`;
  }
  switchMode(mode, true);
  try {
    await ensureBackendProject();
    await persistActiveStep("draft");
    showToast(`已载入${modeLabel()}场景并写入本地数据库`);
  } catch (error) {
    showToast(`已载入离线演示场景：${error.message}`);
  }
}

function resetProject() {
  openSheet(`<span class="eyebrow">重新开始</span><h2 id="sheet-title">清除当前工作区草稿？</h2><p>这会清空当前页面中的输入、评价与完成状态；已经同步到本地数据库的项目版本和审计记录会保留。</p><div class="sheet-actions"><button class="secondary-button" data-close-sheet>取消</button><button class="primary-button" data-confirm-reset>清除并返回起点</button></div>`);
}

async function runValidation(kind) {
  const d = activeData();
  d.running = true;
  renderStep();
  try {
    await persistActiveStep("draft");
    const stage = kind === "template" ? "evaluation" : kind;
    const versionId = kind === "baseline"
      ? state.backend.versions.find(item => item.status === "baseline")?.id
      : state.backend.versions[0]?.id;
    const body = { stage };
    if (versionId) body.version_id = versionId;
    const validation = await apiRequest(`/api/v1/projects/${await ensureBackendProject()}/validations`, {
      method: "POST",
      body
    });
    d.running = false;
    const passed = validation.status === "passed";
    if (kind === "template") d.evaluated = passed;
    if (kind === "baseline") {
      d.baselineEvaluated = passed;
      d.baselineScores = validation.evidence?.dimensions || null;
    }
    if (kind === "existing") d.verified = passed;
    if (validation.evidence?.dimensions) d.scores = validation.evidence.dimensions;
    rememberValidation(validation);
    renderStep();
    showToast(`${kind === "baseline" ? "基线评价" : "确定性验证"}完成：${validation.status === "passed" ? "通过" : "发现阻断项"}`);
  } catch (error) {
    d.running = false;
    renderStep();
    showToast(`验证失败：${error.message}`);
  }
}

async function runStaticPreflight() {
  state.template.staticRunning = true;
  renderStep();
  try {
    await persistActiveStep("draft");
    const result = await apiRequest(`/api/v1/projects/${await ensureBackendProject()}/candidate`, {
      method: "POST",
      body: {}
    });
    state.template.staticRunning = false;
    state.template.staticPreflight = result.validation.status === "passed";
    rememberVersion(result.version);
    rememberValidation(result.validation);
    renderStep();
    showToast(state.template.staticPreflight ? `静态预检通过 · ${result.version.artifact_digest.slice(0, 20)}…` : "静态预检发现阻断项");
  } catch (error) {
    state.template.staticRunning = false;
    renderStep();
    showToast(`生成或预检失败：${error.message}`);
  }
}

async function runRegression() {
  state.template.regressionRunning = true;
  renderStep();
  try {
    const projectId = await ensureBackendProject();
    await persistActiveStep("draft");
    const generated = await apiRequest(`/api/v1/projects/${projectId}/candidate`, { method: "POST", body: {} });
    rememberVersion(generated.version);
    rememberValidation(generated.validation);
    state.template.staticPreflight = generated.validation.status === "passed";
    if (!state.template.staticPreflight) {
      state.template.regressionRunning = false;
      renderStep();
      return showToast("新候选版本未通过静态阶段门，未进入评价和回归");
    }
    const evaluation = await apiRequest(`/api/v1/projects/${projectId}/validations`, {
      method: "POST",
      body: { stage: "evaluation", version_id: generated.version.id }
    });
    rememberValidation(evaluation);
    state.template.evaluated = evaluation.status === "passed";
    if (evaluation.evidence?.dimensions) state.template.scores = evaluation.evidence.dimensions;
    if (!state.template.evaluated) {
      state.template.regressionRunning = false;
      renderStep();
      return showToast("新候选版本评价未通过，未进入回归");
    }
    const validation = await apiRequest(`/api/v1/projects/${projectId}/validations`, {
      method: "POST",
      body: { stage: "regression", version_id: generated.version.id }
    });
    state.template.regressionRunning = false;
    state.template.regressionVerified = validation.status === "passed";
    rememberValidation(validation);
    renderStep();
    showToast(state.template.regressionVerified ? "受影响证据已由后端刷新" : "回归发现阻断项");
  } catch (error) {
    state.template.regressionRunning = false;
    renderStep();
    showToast(`回归失败：${error.message}`);
  }
}

async function freezeSelectedSource() {
  const d = state.existing;
  if (!d.selectedSourceId) return showToast("请先选择一个授权 Skill");
  try {
    const result = await apiRequest(`/api/v1/projects/${await ensureBackendProject()}/import`, {
      method: "POST",
      body: { source_id: d.selectedSourceId }
    });
    d.snapshotFrozen = true;
    d.skillSource = `${result.source.origin} · ${result.source.artifact_digest.slice(0, 18)}…`;
    if (result.baseline) rememberVersion(result.baseline);
    state.backend.projectRoute = "existing";
    renderStep();
    showToast("来源、文件摘要与基线清单已由后端冻结");
  } catch (error) {
    showToast(`导入失败：${error.message}`);
  }
}

async function platformizeCandidate() {
  const d = state.existing;
  try {
    await persistActiveStep("draft");
    const result = await apiRequest(`/api/v1/projects/${await ensureBackendProject()}/candidate`, { method: "POST", body: {} });
    d.platformized = result.validation.status === "passed";
    d.verified = false;
    rememberVersion(result.version);
    rememberValidation(result.validation);
    renderStep();
    showToast(d.platformized ? "候选结构已生成并通过静态规则" : "候选结构存在阻断项");
  } catch (error) {
    showToast(`结构化失败：${error.message}`);
  }
}

async function runExistingRegression() {
  const d = state.existing;
  d.running = true;
  renderStep();
  try {
    const versionId = state.backend.versions[0]?.id;
    const body = { stage: "regression" };
    if (versionId) body.version_id = versionId;
    const validation = await apiRequest(`/api/v1/projects/${await ensureBackendProject()}/validations`, { method: "POST", body });
    d.running = false;
    d.verified = validation.status === "passed";
    if (validation.evidence?.dimensions) {
      d.scores = validation.evidence.dimensions;
      d.candidateScores = validation.evidence.dimensions;
    }
    rememberValidation(validation);
    renderStep();
    showToast(d.verified ? "候选版本对照与回归通过" : "候选版本存在退化或阻断项");
  } catch (error) {
    d.running = false;
    renderStep();
    showToast(`对照验证失败：${error.message}`);
  }
}

async function runUpdateCheck() {
  const d = activeData();
  try {
    const projectId = await ensureBackendProject();
    await apiRequest(`/api/v1/projects/${projectId}/update-policy`, {
      method: "PUT",
      body: {
        enabled: true,
        frequency: ({ "每天": "daily", "每周": "weekly", "每月": "monthly" })[d.frequency] || "weekly",
        sources: Object.entries(d.updates || {}).filter(([, enabled]) => enabled).map(([key]) => key),
        strategy: "review"
      }
    });
    const check = await apiRequest(`/api/v1/projects/${projectId}/update-checks`, { method: "POST", body: {} });
    d.diffDetected = true;
    d.updateResult = check;
    renderStep();
    showToast(check.status === "no_change" ? "更新检查完成：没有变化" : `更新检查完成：${check.diff.conflicts.length} 个冲突`);
  } catch (error) {
    showToast(`更新检查失败：${error.message}`);
  }
}

document.addEventListener("input", event => {
  const target = event.target;
  const d = activeData();
  if (target.getAttribute("aria-invalid") === "true") {
    target.removeAttribute("aria-invalid");
    target.removeAttribute("aria-describedby");
    target.closest(".field")?.classList.remove("is-error");
    if (!$("#step-content [aria-invalid='true']")) $("#gate-message").hidden = true;
  }
  if (target.matches("[data-ai-setting]")) {
    const key = target.dataset.aiSetting;
    state.ai[key] = key === "budget" ? Number(target.value) : target.value;
    if (["baseUrl", "credentialEnv"].includes(key)) {
      state.ai.connected = false;
      state.ai.connectionId = "";
      state.ai.models = [];
    }
    renderAIStatus();
    if (state.started) { renderAICollab(); renderInspector(); }
    schedulePolicySave();
    setSaveState();
  }
  if (target.matches("[data-bind]")) {
    d[target.dataset.bind] = target.value;
    setSaveState();
    renderInspector();
    if (target.dataset.bind === "skillName") renderSidebar();
  }
  if (target.matches("[data-flow-index]")) {
    d.flows[Number(target.dataset.flowIndex)] = target.value;
    setSaveState();
  }
  if (target.matches("[data-dimension-weight]")) {
    d.dimensions[Number(target.dataset.dimensionWeight)].weight = Number(target.value);
    setSaveState();
  }
});

document.addEventListener("change", event => {
  const target = event.target;
  const d = activeData();
  if (target.matches("[data-ai-setting]")) {
    const key = target.dataset.aiSetting;
    state.ai[key] = key === "budget" ? Number(target.value) : target.value;
    renderAIStatus();
    if (state.started) { renderAICollab(); renderInspector(); }
    schedulePolicySave();
  }
  if (target.matches("[data-ai-check]")) {
    state.ai[target.dataset.aiCheck] = target.checked;
    if (target.dataset.aiCheck === "enabled" && !target.checked) state.ai.runningStep = "";
    renderAIStatus();
    if (state.started) { renderAICollab(); renderInspector(); }
    schedulePolicySave();
    if (target.dataset.aiCheck === "enabled") openAICenter();
  }
  if (target.matches("[data-bind-check]")) d[target.dataset.bindCheck] = target.checked;
  if (target.matches("[data-nested-check]")) {
    const [parent, child] = target.dataset.nestedCheck.split(".");
    d[parent][child] = target.checked;
  }
  if (target.matches("[data-check-index]")) d.checklist[Number(target.dataset.checkIndex)] = target.checked;
  if (target.matches("[data-dimension-on]")) d.dimensions[Number(target.dataset.dimensionOn)].on = target.checked;
  if (target.matches("[data-bind]")) d[target.dataset.bind] = target.value;
  setSaveState();
});

document.addEventListener("click", event => {
  const button = event.target.closest("button, [data-close-sheet]");
  if (!button) return;

  if (button.matches("[data-language]")) {
    i18n.setLanguage(button.dataset.language);
    if (state.started) renderStep(); else switchMode(state.mode, false);
    return;
  }
  if (button.matches("[data-start-mode]")) return startMode(button.dataset.startMode);
  if (button.matches("[data-resume-project]")) {
    closeSheet();
    return resumeProject(button.dataset.resumeProject);
  }
  if (button.matches("[data-refresh-catalog]")) return refreshSkillCatalog();
  if (button.matches("[data-refresh-expert-reviews]")) return refreshExpertReviews();
  if (button.matches("[data-mode]")) return switchMode(button.dataset.mode, state.started);
  if (button.matches("[data-ai-provider]")) {
    state.ai.provider = button.dataset.aiProvider;
    state.ai.connected = false;
    state.ai.connectionId = "";
    state.ai.connectionError = "";
    state.ai.credentialLast4 = "";
    state.ai.models = [];
    if (state.ai.provider === "OpenAI API") {
      state.ai.connectionName = "OpenAI API";
      state.ai.baseUrl = "https://api.openai.com/v1";
      state.ai.credentialEnv = "SKILLSENTRA_OPENAI_API_KEY";
    } else if (state.ai.provider === "兼容网关") {
      state.ai.connectionName = "兼容网关";
      state.ai.baseUrl = "https://gateway.example/v1";
      state.ai.credentialEnv = "SKILLSENTRA_CUSTOM_API_KEY";
    }
    renderAIStatus();
    if (state.started) { renderAICollab(); renderInspector(); }
    return openAICenter();
  }
  if (button.matches("[data-ai-connect]")) return connectDemoAI();
  if (button.matches("[data-ai-run]")) return runAIStep();
  if (button.matches("[data-ai-host]")) return requestHostAI();
  if (button.matches("[data-copy-host-command]")) {
    const command = button.dataset.copyHostCommand || "";
    if (navigator.clipboard && command) navigator.clipboard.writeText(command).catch(() => null);
    showToast("已复制 ChatGPT / Codex 协作指令");
    return;
  }
  if (button.matches("[data-ai-scope]")) return openAIScope();
  if (button.matches("[data-ai-decision]")) {
    return decideAIResult(button.dataset.aiDecision);
  }
  if (button.matches("[data-step]")) {
    if (button.classList.contains("is-locked")) return showToast("请先完成前面的阶段门");
    state.current[state.mode] = Number(button.dataset.step);
    renderStep();
    closeMobileSidebar();
    return;
  }
  if (button.matches("[data-choice]")) {
    activeData()[button.dataset.choice] = button.dataset.value;
    return renderStep();
  }
  if (button.matches("[data-chip]")) {
    const list = activeData()[button.dataset.chip];
    const value = button.dataset.value;
    const at = list.indexOf(value);
    if (at >= 0) list.splice(at, 1); else list.push(value);
    return renderStep();
  }
  if (button.matches("[data-add-flow]")) {
    state.template.flows.push("新的流程步骤");
    return renderStep();
  }
  if (button.matches("[data-remove-flow]")) {
    if (state.template.flows.length <= 3) return showToast("至少保留三个流程步骤");
    state.template.flows.splice(Number(button.dataset.removeFlow), 1);
    return renderStep();
  }
  if (button.matches("[data-run-template]")) return runValidation("template");
  if (button.matches("[data-run-static]")) return runStaticPreflight();
  if (button.matches("[data-run-regression]")) return runRegression();
  if (button.matches("[data-run-baseline]")) return runValidation("baseline");
  if (button.matches("[data-run-existing]")) return runExistingRegression();
  if (button.matches("[data-proposal]")) {
    const proposal = activeData().proposals.find(p => p.id === button.dataset.proposal);
    proposal.decision = button.dataset.decision;
    if (state.mode === "template") state.template.regressionVerified = false;
    if (state.mode === "existing") {
      state.existing.platformized = false;
      state.existing.verified = false;
    }
    renderStep();
    return showToast(button.dataset.decision === "accept" ? "已接受，相关证据将在下次试运行重测" : "已拒绝并记录决定");
  }
  if (button.matches("[data-reference]")) {
    state.template.referenceSkill = button.dataset.reference;
    state.template.staticPreflight = false;
    return renderStep();
  }
  if (button.matches("[data-skill]")) {
    state.existing.selectedSkill = button.dataset.skill;
    state.existing.selectedSourceId = button.dataset.sourceId;
    const source = state.backend.sources.find(item => item.id === button.dataset.sourceId);
    state.existing.skillSource = `${source?.origin || "local"} · ${String(source?.artifact_digest || "").slice(0, 18)}…`;
    state.existing.baselineEvaluated = false;
    state.existing.snapshotFrozen = false;
    return renderStep();
  }
  if (button.matches("[data-freeze-snapshot]")) {
    return freezeSelectedSource();
  }
  if (button.matches("[data-platformize]")) {
    return platformizeCandidate();
  }
  if (button.matches("[data-open-delivery]")) return openDeliverySheet();
  if (button.matches("[data-confirm-delivery]")) {
    if (!$("#delivery-agreement")?.checked) return showToast("请先确认版本、权限与回滚信息");
    return confirmDelivery();
  }
  if (button.matches("[data-detect-diff]")) {
    return runUpdateCheck();
  }
  if (button.matches("[data-run-update]")) return runUpdateCheck();
  if (button.matches("[data-diff]")) {
    state.existing.diffDecision = button.dataset.diff;
    renderStep();
    return showToast(button.dataset.diff === "accept" ? "已接受无冲突变更" : "冲突变更已暂缓");
  }
  if (button.matches("[data-close-sheet]")) return closeSheet();
  if (button.matches("[data-confirm-reset]")) {
    const backend = state.backend;
    state = defaultState();
    state.backend = backend;
    state.backend.projectId = "";
    state.backend.projectRoute = "";
    state.backend.revisions = {};
    localStorage.removeItem("skillsentra.activeProject");
    closeSheet();
    return switchMode("template", false);
  }
  if (button.matches("[data-switch-finished]")) {
    closeSheet();
    return switchMode(state.mode === "template" ? "existing" : "template", true);
  }
});

function closeMobileSidebar({ restoreFocus = false } = {}) {
  const toggle = $("#sidebar-toggle");
  $("#app-window").classList.remove("mobile-sidebar-open");
  $("#sidebar-scrim").hidden = true;
  toggle.setAttribute("aria-expanded", "false");
  if (restoreFocus) toggle.focus();
}

$("#sidebar-toggle").addEventListener("click", () => {
  const mobile = matchMedia("(max-width: 760px)").matches;
  if (mobile) {
    const open = $("#app-window").classList.toggle("mobile-sidebar-open");
    $("#sidebar-scrim").hidden = !open;
    $("#sidebar-toggle").setAttribute("aria-expanded", String(open));
    if (open) $("#step-sidebar [data-step].is-active, #step-sidebar button")?.focus();
  } else {
    const hidden = $("#app-window").classList.toggle("sidebar-hidden");
    $("#sidebar-toggle").setAttribute("aria-expanded", String(!hidden));
  }
});
$("#sidebar-scrim").addEventListener("click", () => closeMobileSidebar({ restoreFocus: true }));
$("#load-demo").addEventListener("click", event => { event.currentTarget.closest("details").open = false; loadDemo(); });
$("#project-center-button").addEventListener("click", event => {
  event.currentTarget.closest("details")?.removeAttribute("open");
  openProjectCenter();
});
$("#ai-center-button").addEventListener("click", openAICenter);
$("#help-button").addEventListener("click", event => { event.currentTarget.closest("details").open = false; openSheet(`<span class="eyebrow">开发版 · V3</span><h2 id="sheet-title">双路径 + 全过程 AI 协作</h2><p>当前工作台已连接 Python 后端与 SQLite；项目、步骤、AI 策略、调用结果和审计事件可以真实保存。</p><div class="notice"><span class="notice-icon">i</span><div><strong>真实存储与高风险执行分开</strong><small>内置 Mock 用于完整联调；可通过服务端环境变量接入 Responses API。系统仍不运行第三方脚本、不安装或发布 Skill，L5 动态安全保持未授权。</small></div></div><div class="sheet-actions"><button class="primary-button" data-close-sheet>开始体验</button></div>`); });
$("#step-guide").addEventListener("click", openGuide);
$("#next-step").addEventListener("click", nextStep);
$("#previous-step").addEventListener("click", previousStep);
$("#mobile-next").addEventListener("click", nextStep);
$("#mobile-previous").addEventListener("click", previousStep);
$("#save-draft").addEventListener("click", async () => {
  setSaveState();
  try {
    await persistActiveStep("draft");
    showToast(state.backend.available ? "草稿已保存到本地数据库" : "草稿已保存到当前演示会话");
  } catch (error) {
    showToast(`草稿保存失败：${error.message}`);
  }
});
$("#reset-project").addEventListener("click", resetProject);
$("#sheet-close").addEventListener("click", closeSheet);
$("#sheet-backdrop").addEventListener("click", event => { if (event.target === $("#sheet-backdrop")) closeSheet(); });
document.addEventListener("click", event => {
  if (!event.target.closest(".toolbar-menu")) $$(".toolbar-menu[open]").forEach(menu => { menu.open = false; });
});
document.addEventListener("keydown", event => {
  const sheetOpen = !$("#sheet-backdrop").hidden;
  const sidebarOpen = $("#app-window").classList.contains("mobile-sidebar-open");
  if (event.key === "Escape") {
    if (sheetOpen) return closeSheet();
    if (sidebarOpen) return closeMobileSidebar({ restoreFocus: true });
    const openMenu = $(".toolbar-menu[open]");
    if (openMenu) { openMenu.open = false; openMenu.querySelector("summary")?.focus(); return; }
  }
  if (event.key !== "Tab" || (!sheetOpen && !sidebarOpen)) return;
  const focusRoot = sheetOpen ? $("#sheet-panel") : $("#step-sidebar");
  const focusable = $$("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])", focusRoot);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!focusRoot.contains(document.activeElement)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

$("#sidebar-toggle").setAttribute("aria-expanded", String(!matchMedia("(max-width: 760px)").matches));
switchMode("template", false);
initializeBackend();
window.setInterval(syncAIResults, 5000);
window.addEventListener("focus", syncAIResults);
document.addEventListener("visibilitychange", () => { if (!document.hidden) syncAIResults(); });
