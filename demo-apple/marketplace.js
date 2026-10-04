"use strict";

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>'"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]));

const copy = {
  navDiscover:["发现","Discover"], navRanking:["排行榜","Ranking"], navCreator:["创作者中心","Creator"], navAdmin:["管理后台","Admin"],
  signIn:["登录","Sign in"], startFree:["免费开始","Start free"], heroEyebrow:["SKILL 发现 · 创建 · 信任","SKILL DISCOVERY · CREATION · TRUST"],
  heroLine1:["找到好用的 Skill","Find Skills that actually work"], heroLine2:["每一次选择，都有证据","Every choice has evidence"],
  heroBody:["发现社区优秀作品，用可验证流程创建和优化自己的 Skill，自主设置免费或收费，并让真实用户评价决定长期排名","Discover community work, create and improve with verifiable workflows, choose free or paid, and let genuine user reviews shape long-term ranking"],
  createSkill:["创建我的 Skill","Create my Skill"], viewRanking:["查看排行榜","View ranking"], trustDigest:["不可变 Digest","Immutable Digest"], trustReview:["真实获取后评价","Reviews after acquisition"], trustBilling:["透明沙箱计费","Transparent sandbox billing"],
  rankingEyebrow:["精选 PRIVATE BETA","CURATED PRIVATE BETA"], rankingTitle:["本周 Skill 排行榜","This week’s Skill ranking"], rankingBody:["使用平滑后的真实评分、评价数量、获取量与策展状态生成；收费高低不影响排名。","Built from smoothed verified ratings, review volume, acquisitions, and curation status. Price never buys ranking."], liveRanking:["受控更新","Controlled updates"],
  discoverEyebrow:["发现 SKILLS","EXPLORE SKILLS"], discoverTitle:["按你的工作方式发现","Discover by the way you work"], searchPlaceholder:["搜索 Skill 或能力","Search Skills or capabilities"], allPricing:["全部价格","All prices"], free:["免费","Free"], paid:["收费","Paid"],
  creatorEyebrow:["面向创作者","FOR CREATORS"], creatorTitle:["从想法到发布，不再跨越五个平台","Go from idea to publication without crossing five tools"], stepCreate:["创建与优化","Create & improve"], stepCreateBody:["模板或现有 Skill，两条证据化路线","Two evidence-backed paths: template or existing Skill"], stepVerify:["测试与交付","Test & deliver"], stepVerifyBody:["结构、安全、回归硬门独立验证","Independent structure, security, and regression gates"], stepPublish:["定价与发布","Price & publish"], stepPublishBody:["自主选择免费或收费，版本始终可追溯","Choose free or paid while every version stays traceable"], stepGrow:["评价与结算","Review & settle"], stepGrowBody:["真实获取后评价，收入形成可对账分录","Reviews follow acquisition and revenue becomes reconcilable entries"], openCreator:["打开创作者中心","Open creator center"], footerLine:["让 Skill 的价值可创建、可验证、可结算","Make Skill value creatable, verifiable, and reconcilable"], footerState:["v0.6 · 策展 Private Beta · 生产付款关闭","v0.6 · Curated Private Beta · Production payout off"],
};

const state = {
  language: localStorage.getItem("skillsentra-market-language") || "zh-CN",
  user: null,
  publications: [],
  publicationsStatus: "loading",
  publicationsError: "",
  ranking: [],
  githubTopSkills: [],
  githubPage: 1,
  category: "",
  search: "",
  pricing: "",
  modalReturn: null,
};
const tr = (zh, en) => state.language === "en" ? en : zh;
const money = (minor, currency="USD") => Number(minor || 0) === 0 ? tr("免费", "Free") : new Intl.NumberFormat(state.language === "en" ? "en-US" : "zh-CN", {style:"currency",currency}).format(Number(minor)/100);
const shortDigest = value => value ? `${value.slice(0,14)}…${value.slice(-7)}` : "—";
const stars = rating => `${"★".repeat(Math.round(Number(rating || 0)))}${"☆".repeat(5-Math.round(Number(rating || 0)))}`;

function cookie(name) {
  return document.cookie.split(";").map(item => item.trim()).find(item => item.startsWith(`${name}=`))?.slice(name.length + 1) || "";
}

async function api(path, options={}) {
  const method = options.method || "GET";
  const headers = {"Content-Type":"application/json", ...(options.headers || {})};
  const csrf = decodeURIComponent(cookie("skillsentra_csrf"));
  if (["POST","PUT"].includes(method) && csrf) headers["X-CSRF-Token"] = csrf;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 12000);
  let response;
  try {
    response = await fetch(path, {method, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: controller.signal});
  } catch (cause) {
    const error = new Error(cause?.name === "AbortError" ? tr("请求等待过久，请检查连接后重试。", "The request took too long. Check your connection and try again.") : tr("网络连接失败，请重试。", "Network connection failed. Please try again."));
    error.code = cause?.name === "AbortError" ? "request_timeout" : "network_error";
    throw error;
  } finally { clearTimeout(timeout); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `${response.status}`);
    error.status = response.status; error.code = payload.error?.code || "request_failed";
    throw error;
  }
  return payload.data;
}

function localize() {
  document.documentElement.lang = state.language;
  document.title = state.language === "en" ? "SkillSentra · Skill Marketplace" : "SkillSentra · Skill 市场";
  $$('[data-copy]').forEach(element => {
    const value = copy[element.dataset.copy];
    if (!value) return;
    element.textContent = value[state.language === "en" ? 1 : 0];
  });
  $$('[data-placeholder]').forEach(element => { const value = copy[element.dataset.placeholder]; if (value) element.placeholder = value[state.language === "en" ? 1 : 0]; });
  $$('[data-language]').forEach(button => { const active = button.dataset.language === state.language; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", String(active)); });
  $('[data-action="account"]').setAttribute("aria-label", tr("当前账户", "Current account"));
  const mobileNav = $(".mobile-nav");
  if (mobileNav) mobileNav.querySelector("summary").setAttribute("aria-label", mobileNav.open ? tr("关闭主导航", "Close main navigation") : tr("打开主导航", "Open main navigation"));
  renderCategories(); renderRanking(); renderCatalog();
}

async function initialize() {
  const params = new URLSearchParams(location.search);
  const requestedGitHubPage = Number(params.get("github_page"));
  if (Number.isInteger(requestedGitHubPage) && requestedGitHubPage > 0) state.githubPage = requestedGitHubPage;
  localize();
  await Promise.all([loadPublic(), loadSession()]);
  bind();
  if (params.get("signin") === "google") toast(tr("Google 登录成功。", "Google sign-in succeeded."));
  if (params.get("signin") === "chatgpt") toast(tr("ChatGPT 登录成功。", "ChatGPT sign-in succeeded."));
  if (params.get("verified") === "1") toast(tr("邮箱验证成功，现在可以登录。", "Email verified. You can now sign in."));
  if (params.get("auth_error")) toast(tr("第三方登录未完成，请重新开始。", "Third-party sign-in did not complete. Please try again."));
  if (params.get("reset_token")) openPasswordReset(params.get("reset_token"));
  if (params.has("signin") || params.has("auth_error") || params.has("verified") || params.has("reset_token")) history.replaceState({}, "", `${location.pathname}${params.has("github_page") ? `?github_page=${state.githubPage}#ranking` : ""}`);
}

async function loadPublic() {
  state.publicationsStatus = "loading";
  state.publicationsError = "";
  renderCatalog();
  const results = await Promise.allSettled([
    api("/api/v1/marketplace/publications"),
    api("/api/v1/marketplace/leaderboard?limit=10"),
    api("/api/v1/discovery/github/top-skills"),
  ]);
  if (results[0].status === "fulfilled") {
    state.publications = results[0].value;
    state.publicationsStatus = state.publications.length ? "ready" : "empty";
  } else {
    state.publications = [];
    state.publicationsStatus = "error";
    state.publicationsError = results[0].reason?.message || tr("请稍后重试", "Try again shortly");
  }
  if (results[1].status === "fulfilled") state.ranking = results[1].value;
  if (results[2].status === "fulfilled") {
    state.githubTopSkills = Array.isArray(results[2].value?.items) ? results[2].value.items : [];
  }
  const failures = results.filter(result => result.status === "rejected");
  if (failures.length) {
    const firstFailure = failures[0].reason;
    toast(tr(
      `部分内容暂时无法刷新：${firstFailure?.message || "请稍后重试"}`,
      `Some content could not be refreshed: ${firstFailure?.message || "try again shortly"}`,
    ));
  }
  renderRanking(); renderCategories(); renderCatalog();
}

async function retryPublications() {
  state.publicationsStatus = "loading";
  state.publicationsError = "";
  renderCatalog();
  try {
    state.publications = await api("/api/v1/marketplace/publications");
    state.publicationsStatus = state.publications.length ? "ready" : "empty";
  } catch (error) {
    state.publications = [];
    state.publicationsStatus = "error";
    state.publicationsError = error.message || tr("请稍后重试", "Try again shortly");
  }
  renderCategories();
  renderCatalog();
}

async function loadSession() {
  try {
    const session = await api("/api/v1/session");
    state.user = session.user || null;
  } catch (error) {
    if (error.status === 401 || error.status === 403) state.user = null;
    else toast(tr("账户状态暂时无法确认，请刷新后重试", "Your session could not be verified. Refresh and try again"));
  }
  renderIdentity();
}

function renderIdentity() {
  const signedIn = Boolean(state.user);
  $('[data-action="login"]').hidden = signedIn;
  $('[data-action="register"]').hidden = signedIn;
  const account = $('[data-action="account"]');
  account.hidden = !signedIn;
  if (signedIn) account.querySelector("span").textContent = state.user.display_name.slice(0,1).toUpperCase();
}

function renderRanking() {
  const hasWeeklyRanking = state.ranking.length > 0;
  const eyebrow = $("#ranking-eyebrow");
  const title = $("#ranking-title");
  const body = $("#ranking-body");
  const liveLabel = $("#ranking-live-label");

  const sourceTabs = $("#ranking-source-tabs");
  const pagination = $("#github-pagination");
  sourceTabs.hidden = true;
  if (hasWeeklyRanking) {
    eyebrow.textContent = tr("COMMUNITY SIGNAL", "COMMUNITY SIGNAL");
    title.textContent = tr("本周 Skill 排行榜", "Weekly Skill Ranking");
    body.textContent = tr("综合用户评分、评价数量与真实获取量生成，收费高低不影响排名。", "Built from user ratings, review volume, and verified acquisitions. Price does not affect rank.");
    liveLabel.textContent = tr("动态更新", "Live updates");
    const top = state.ranking.slice(0, 3);
    $("#podium").hidden = false;
    pagination.hidden = true;
    $("#podium").innerHTML = top.map((item,index) => `<button type="button" class="podium-card" data-publication="${esc(item.id)}" aria-label="${esc(tr(`查看第 ${index+1} 名 ${item.name}`, `View number ${index+1}: ${item.name}`))}"><span class="rank-number">0${index+1}</span><h3>${esc(item.name)}</h3><p>${esc(item.publisher_name)}</p><div class="rank-meta"><span>${stars(item.average_rating)} ${Number(item.average_rating).toFixed(1)}</span><span>${money(item.price_minor,item.currency)}</span></div></button>`).join("");
    $("#ranking-list").innerHTML = state.ranking.slice(3).map((item,index) => `<button type="button" class="rank-row" data-publication="${esc(item.id)}"><b>${index+4}</b><strong>${esc(item.name)}</strong><span>${stars(item.average_rating)} ${Number(item.average_rating).toFixed(1)}</span><span>${item.purchase_count} ${tr("次获取","acquired")}</span></button>`).join("");
    return;
  }

  const items = state.githubTopSkills;
  const pageSize = 10;
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const requestedPage = Number(new URLSearchParams(location.search).get("github_page"));
  if (Number.isInteger(requestedPage) && requestedPage > 0) state.githubPage = requestedPage;
  if (items.length) state.githubPage = Math.min(Math.max(1, state.githubPage), pageCount);
  const pageItems = items.slice((state.githubPage - 1) * pageSize, state.githubPage * pageSize);
  eyebrow.textContent = tr("GITHUB STAR RANKING", "GITHUB STAR RANKING");
  title.textContent = tr("GitHub Skill 星标榜", "GitHub Skill Star Ranking");
  body.textContent = tr("平台近 7 天暂无足够真实信号；以下按 GitHub topic:skill 星标排序，均为未验证候选，不代表平台周榜或已可安装。", "There are not enough platform signals from the last 7 days. The following topic:skill candidates are ordered by GitHub stars, remain unverified, and are not a platform weekly ranking or installation claim.");
  liveLabel.textContent = tr(`${items.length} 个候选`, `${items.length} candidates`);
  $("#podium").hidden = true;
  $("#podium").innerHTML = "";
  $("#ranking-list").innerHTML = pageItems.length ? pageItems.map(item => `<a class="rank-row" href="${esc(item.repository)}" target="_blank" rel="noopener noreferrer"><b>${item.source_rank}</b><div><strong>${esc(item.name)}</strong>${skillTags(item, true)}</div><span>★ ${Number(item.github_stars || 0).toLocaleString()}</span><span>${tr("未验证候选", "Unverified candidate")}</span></a>`).join("") : empty(tr("GitHub 候选尚未载入", "GitHub candidates are not loaded yet"), tr("请稍后刷新页面，或由管理员刷新候选目录", "Refresh the page shortly, or ask an administrator to refresh the candidate catalog"));
  pagination.hidden = items.length <= pageSize;
  pagination.innerHTML = Array.from({length: pageCount}, (_, index) => `<a class="${state.githubPage === index + 1 ? "is-active" : ""}" href="marketplace.html?github_page=${index + 1}#ranking" aria-label="${tr(`第 ${index + 1} 页`, `Page ${index + 1}`)}">${index + 1}</a>`).join("");
}

function skillTags(item, github = false) {
  const text = `${item.name || ""} ${item.description || item.summary || ""}`.toLowerCase();
  const profile = /data|dataset|治理|数据库/.test(text) ? [tr("数据与分析", "Data & analytics"), tr("数据治理与质量", "Data governance & quality")] : /ppt|slide|presentation|演示/.test(text) ? [tr("设计与演示", "Design & presentation"), tr("演示文稿制作", "Presentation creation")] : /write|writing|写作|论文/.test(text) ? [tr("写作与研究", "Writing & research"), tr("内容撰写与文献工作", "Content & literature work")] : /code|react|开发|编程/.test(text) ? [tr("开发与自动化", "Development & automation"), tr("编码与工程流程", "Coding & engineering workflows")] : [tr("效率与工作流", "Productivity & workflow"), tr("通用任务提效", "General task productivity")];
  const updatedSource = github ? item.repository_updated_at : (item.updated_at || item.refreshed_at);
  const updated = updatedSource ? new Date(updatedSource).toLocaleDateString(state.language) : tr("未知", "Unknown");
  const rating = github ? tr("SkillSentra：待评估", "SkillSentra: pending review") : Number(item.average_rating || 0) > 0 ? tr(`SkillSentra：${Number(item.average_rating).toFixed(1)}/5`, `SkillSentra: ${Number(item.average_rating).toFixed(1)}/5`) : tr("SkillSentra：暂无评价", "SkillSentra: no rating yet");
  return `<div class="skill-tags"><span>${tr("类别（自动推断）：", "Category (inferred): ")}${esc(profile[0])}</span><span>${tr("适用（自动推断）：", "For (inferred): ")}${esc(profile[1])}</span><span>${tr("仓库更新：", "Repository updated: ")}${esc(updated)}</span><span>${esc(rating)}</span></div>`;
}

function renderCategories() {
  const values = [["",tr("全部","All")],["productivity",tr("效率","Productivity")],["research",tr("研究","Research")],["writing",tr("写作","Writing")],["coding",tr("编程","Coding")],["data",tr("数据","Data")],["design",tr("设计","Design")],["operations",tr("运营","Operations")]];
  $("#category-row").innerHTML = values.map(([value,label]) => `<button type="button" data-category="${value}" class="${state.category===value?"is-active":""}">${label}</button>`).join("");
}

function renderCatalog() {
  const discover = $("#discover");
  const status = $("#catalog-status");
  const grid = $("#skill-grid");
  const loading = state.publicationsStatus === "loading";
  discover.hidden = false;
  discover.setAttribute("aria-busy", String(loading));
  status.hidden = state.publicationsStatus === "ready";
  grid.hidden = state.publicationsStatus !== "ready";
  status.setAttribute("role", state.publicationsStatus === "error" ? "alert" : "status");

  if (loading) {
    status.innerHTML = catalogState(
      "loading",
      tr("正在加载 Skill", "Loading Skills"),
      tr("正在读取可验证的发布目录…", "Reading the verifiable publication catalog…"),
    );
    grid.innerHTML = "";
    return;
  }
  if (state.publicationsStatus === "error") {
    status.innerHTML = catalogState(
      "error",
      tr("Skill 目录暂时无法加载", "The Skill catalog is temporarily unavailable"),
      state.publicationsError,
      "retry",
    );
    grid.innerHTML = "";
    return;
  }
  if (state.publicationsStatus === "empty") {
    status.innerHTML = catalogState(
      "empty",
      tr("首批 Skill 正在准备", "The first Skills are being prepared"),
      tr("完成交付并发布后会出现在这里", "Delivered and published Skills will appear here"),
    );
    grid.innerHTML = "";
    return;
  }

  const needle = state.search.toLowerCase();
  const rows = state.publications.filter(item => (!state.category || item.category===state.category) && (!state.pricing || item.pricing_type===state.pricing) && (!needle || `${item.name} ${item.summary}`.toLowerCase().includes(needle)));
  const filtered = Boolean(state.category || state.pricing || state.search);
  grid.innerHTML = rows.length ? rows.map((item,index) => `<article class="skill-card"><div class="skill-card-top"><span class="skill-icon">${String(index+1).padStart(2,"0")}</span><span class="price-pill">${money(item.price_minor,item.currency)}</span></div><h3>${esc(item.name)}</h3><p>${esc(item.summary)}</p>${skillTags(item)}<div class="skill-card-foot"><span>${stars(item.average_rating)} ${Number(item.average_rating).toFixed(1)} · ${item.purchase_count} ${tr("次获取","acquired")}</span><button type="button" data-publication="${esc(item.id)}">${tr("查看","View")} →</button></div></article>`).join("") : empty(tr(filtered ? "没有匹配结果" : "首批 Skill 正在准备", filtered ? "No matching results" : "The first Skills are being prepared"), tr(filtered ? "清除筛选后查看全部内容" : "完成交付并发布后会出现在这里", filtered ? "Clear filters to see everything" : "Delivered and published Skills will appear here"), filtered ? "clear" : "");
}

function catalogState(kind, title, note, action = "") {
  const loader = kind === "loading" ? `<span class="catalog-loader" aria-hidden="true"><i></i><i></i><i></i></span>` : "";
  const control = action === "retry" ? `<button class="empty-action" type="button" data-action="retry-publications">${tr("重试加载 Skill", "Retry loading Skills")}</button>` : "";
  return `<div class="empty-state catalog-state is-${kind}">${loader}<div><h3>${esc(title)}</h3><span>${esc(note)}</span>${control}</div></div>`;
}

function empty(title, note, action = "") {
  const control = action === "clear" ? `<br><button class="empty-action" type="button" data-action="clear-filters">${tr("清除筛选", "Clear filters")}</button>` : action === "create" ? `<br><a class="empty-action" href="studio.html">${tr("创建或优化 Skill", "Create or improve a Skill")} →</a>` : "";
  return `<div class="empty-state" role="status"><div><h3>${esc(title)}</h3><span>${esc(note)}</span>${control}</div></div>`;
}

function bind() {
  document.addEventListener("click", async event => {
    const language = event.target.closest("[data-language]");
    if (language) { state.language=language.dataset.language; localStorage.setItem("skillsentra-market-language",state.language); localize(); return; }
    const category = event.target.closest("[data-category]");
    if (category) { state.category=category.dataset.category; renderCategories(); renderCatalog(); return; }
    const githubPage = event.target.closest("[data-github-page]");
    if (githubPage) { state.githubPage=Number(githubPage.dataset.githubPage); renderRanking(); return; }
    const publication = event.target.closest("[data-publication]");
    if (publication) { await openPublication(publication.dataset.publication); return; }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "login" || action === "register") openAuth(action);
    if (action === "password-reset") openPasswordReset();
    if (action === "verification-resend") openVerificationResend();
    if (action === "close-modal") closeModal();
    if (action === "creator") await openCreator();
    if (action === "clear-filters") {
      state.category = ""; state.pricing = ""; state.search = "";
      $("#search").value = ""; $("#pricing-filter").value = "";
      renderCategories(); renderCatalog();
    }
    if (action === "retry-publications") await retryPublications();
    if (action === "account") await openAccount();
    if (action === "logout") await logout();
    if (action === "publish") await openPublish();
    if (action === "statement") await confirmStatement();
    if (action === "confirm-statement") await createStatement();
    if (action === "buy") await confirmPurchase(event.target.closest("[data-id]").dataset.id);
    if (action === "confirm-purchase") await purchase(event.target.closest("[data-id]").dataset.id);
    if (action === "review") openReview(event.target.closest("[data-id]").dataset.id);
  });
  $("#search").addEventListener("input", event => { state.search=event.target.value; renderCatalog(); });
  $("#pricing-filter").addEventListener("change", event => { state.pricing=event.target.value; renderCatalog(); });
  $("#modal-backdrop").addEventListener("click", event => { if (event.target === $("#modal-backdrop")) closeModal(); });
  const mobileNav = $(".mobile-nav");
  const mobileSummary = mobileNav.querySelector("summary");
  const syncMobileNav = () => mobileSummary.setAttribute("aria-label", mobileNav.open ? tr("关闭主导航", "Close main navigation") : tr("打开主导航", "Open main navigation"));
  mobileNav.addEventListener("toggle", syncMobileNav);
  document.addEventListener("keydown", event => {
    const backdrop = $("#modal-backdrop");
    if (event.key === "Escape" && mobileNav.open && backdrop.hidden) { mobileNav.open = false; syncMobileNav(); mobileSummary.focus(); return; }
    if (event.key === "Escape" && !backdrop.hidden) closeModal();
    if (event.key !== "Tab" || backdrop.hidden) return;
    const focusable = [...backdrop.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href]')]
      .filter(element => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener("click", event => {
    if (mobileNav.open && !mobileNav.contains(event.target)) { mobileNav.open = false; syncMobileNav(); }
  });
  syncMobileNav();
}

function openModal(html, returnFocus=document.activeElement) {
  state.modalReturn=returnFocus;
  $("#modal-content").innerHTML=html;
  $("#modal-backdrop").hidden=false;
  document.body.style.overflow="hidden";
  const modal = $(".modal");
  modal.scrollTop = 0;
  setTimeout(() => {
    modal.scrollTop = 0;
    $(".modal-close")?.focus({ preventScroll: true });
  }, 30);
}
function closeModal() { $("#modal-backdrop").hidden=true; document.body.style.overflow=""; state.modalReturn?.focus?.(); }

function bindFormErrorRecovery(form) {
  const error = form.querySelector(".form-error");
  if (error && !error.id) error.id = `${form.id || "form"}-error`;
  form.addEventListener("input", event => {
    const field = event.target.closest("input, textarea, select");
    if (!field || field.getAttribute("aria-invalid") !== "true") return;
    field.removeAttribute("aria-invalid");
    field.removeAttribute("aria-describedby");
    if (!form.querySelector('[aria-invalid="true"]') && error) error.textContent = "";
  });
}

function presentFormError(form, message, fieldName = "") {
  const error = form.querySelector(".form-error");
  if (error && !error.id) error.id = `${form.id || "form"}-error`;
  if (error) error.textContent = message;
  const field = fieldName ? form.elements.namedItem(fieldName) : null;
  if (field instanceof HTMLElement) {
    field.setAttribute("aria-invalid", "true");
    if (error?.id) field.setAttribute("aria-describedby", error.id);
    field.focus();
  }
}

function openAuth(mode="login") {
  const login = mode === "login";
  const recovery = login ? `<div class="auth-recovery"><button class="text-button" type="button" data-action="password-reset">${tr("忘记密码？", "Forgot password?")}</button><button class="text-button" type="button" data-action="verification-resend">${tr("重发验证邮件", "Resend verification")}</button></div>` : "";
  openModal(`<span class="eyebrow">SKILLSENTRA ACCOUNT</span><h2 id="modal-title">${login?tr("欢迎回来","Welcome back"):tr("创建个人工作区","Create your workspace")}</h2><p>${tr("使用已注册邮箱和密码即可登录","Sign in with your registered email and password")}</p><div class="auth-tabs"><button type="button" class="${login?"is-active":""}" data-auth-tab="login">${tr("邮箱登录","Email sign in")}</button><button type="button" class="${!login?"is-active":""}" data-auth-tab="register">${tr("注册","Register")}</button></div><form id="auth-form" class="form-grid">${login?"":`<label>${tr("显示名称","Display name")}<input name="display_name" required minlength="2" maxlength="60" autocomplete="name"></label>`}<label>${tr("邮箱","Email")}<input name="email" type="email" required autocomplete="email"></label><label>${tr("密码","Password")}<input name="password" type="password" aria-label="${tr("密码","Password")}" required minlength="10" maxlength="128" pattern="(?=.*[A-Za-z])(?=.*\\d).{10,128}" title="${tr("至少 10 位，且同时包含英文字母和数字", "Use at least 10 characters including a letter and a number")}" autocomplete="${login?"current-password":"new-password"}"><small>${tr("至少 10 位，且同时包含英文字母和数字", "At least 10 characters with a letter and a number")}</small></label>${recovery}${login?"":`<label>${tr("确认密码","Confirm password")}<input name="confirm_password" type="password" required minlength="10" maxlength="128" autocomplete="new-password"></label><label class="consent-row"><input name="beta_ack" type="checkbox" required><span>${tr("我了解当前为受控 Private Beta，计费和结算仅为沙箱演示，不发生真实资金划转","I understand this is a controlled Private Beta; billing and settlement are sandbox-only and move no real funds")}</span></label>`}<p class="form-error" role="alert"></p><button class="primary" type="submit">${login?tr("登录","Sign in"):tr("创建账户","Create account")}</button></form><div id="auth-providers" class="auth-providers" aria-live="polite"></div>`);
  $$('[data-auth-tab]').forEach(button => button.addEventListener("click",()=>openAuth(button.dataset.authTab)));
  const authForm = $("#auth-form");
  loadAuthProviders();
  bindFormErrorRecovery(authForm);
  authForm.addEventListener("submit", async event => {
    event.preventDefault(); const form=new FormData(event.currentTarget); const body=Object.fromEntries(form.entries()); const error=event.currentTarget.querySelector(".form-error");
    if (!login && body.password !== body.confirm_password) { presentFormError(event.currentTarget,tr("两次输入的密码不一致。","Passwords do not match."),"confirm_password"); return; }
    delete body.confirm_password; delete body.beta_ack;
    const submit = event.currentTarget.querySelector("button[type='submit']"); const original = submit.textContent;
    submit.disabled=true; submit.textContent=tr("正在处理…", "Working…"); event.currentTarget.setAttribute("aria-busy", "true");
    try { const result=await api(`/api/v1/auth/${login?"login":"register"}`,{method:"POST",body}); if (result.verification_required) { state.user=null; renderIdentity(); closeModal(); toast(tr("账户已创建，请查收验证邮件后登录。", "Account created. Check your email to verify it before signing in.")); return; } state.user=result.user; renderIdentity(); closeModal(); toast(login?tr("登录成功","Signed in"):tr("个人工作区已创建","Workspace created")); }
    catch (failure) {
      const fieldByCode = { invalid_display_name:"display_name", account_exists:"email", invalid_email:"email", invalid_password:"password", weak_password:"password", login_failed:"email", email_unverified:"email" };
      presentFormError(event.currentTarget, failure.message, fieldByCode[failure.code] || "");
    } finally { submit.disabled=false; submit.textContent=original; event.currentTarget.removeAttribute("aria-busy"); }
  });
}

function openPasswordReset(token="") {
  const complete = Boolean(token);
  openModal(complete
    ? `<span class="eyebrow">SKILLSENTRA ACCOUNT</span><h2 id="modal-title">${tr("设置新密码", "Set a new password")}</h2><p>${tr("重置链接仅可使用一次，完成后所有旧登录会话都会退出。", "This one-time link revokes all previous sessions after the password is changed.")}</p><form id="password-reset-form" class="form-grid"><input type="hidden" name="token" value="${esc(token)}"><label>${tr("新密码", "New password")}<input name="password" type="password" required minlength="10" maxlength="128" pattern="(?=.*[A-Za-z])(?=.*\\d).{10,128}" autocomplete="new-password"><small>${tr("至少 10 位，且同时包含英文字母和数字", "At least 10 characters with a letter and a number")}</small></label><label>${tr("确认新密码", "Confirm new password")}<input name="confirm_password" type="password" required minlength="10" maxlength="128" autocomplete="new-password"></label><p class="form-error" role="alert"></p><div class="modal-actions"><button class="secondary" type="button" data-action="close-modal">${tr("取消", "Cancel")}</button><button class="primary" type="submit">${tr("保存新密码", "Save password")}</button></div></form>`
    : `<span class="eyebrow">SKILLSENTRA ACCOUNT</span><h2 id="modal-title">${tr("重置密码", "Reset password")}</h2><p>${tr("输入注册邮箱，我们会发送一次性重置链接。", "Enter your registered email and we will send a one-time reset link.")}</p><form id="password-reset-form" class="form-grid"><label>${tr("邮箱", "Email")}<input name="email" type="email" required autocomplete="email"></label><p class="form-error" role="alert"></p><div class="modal-actions"><button class="secondary" type="button" data-action="login">${tr("返回登录", "Back to sign in")}</button><button class="primary" type="submit">${tr("发送重置链接", "Send reset link")}</button></div></form>`);
  const form = $("#password-reset-form");
  bindFormErrorRecovery(form);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.currentTarget).entries());
    if (complete && body.password !== body.confirm_password) { presentFormError(event.currentTarget, tr("两次输入的密码不一致。", "Passwords do not match."), "confirm_password"); return; }
    delete body.confirm_password;
    const submit = event.currentTarget.querySelector("button[type='submit']");
    const original = submit.textContent;
    submit.disabled = true; submit.textContent = tr("正在处理…", "Working…");
    try {
      if (complete) {
        await api("/api/v1/auth/password-reset/complete", {method: "POST", body});
        closeModal(); toast(tr("密码已更新，请使用新密码登录。", "Password updated. Sign in with your new password.")); openAuth("login");
      } else {
        await api("/api/v1/auth/password-reset/request", {method: "POST", body});
        closeModal(); toast(tr("如果邮箱已注册，请查收密码重置邮件。", "If the email is registered, a password reset message is on its way."));
      }
    } catch (error) { presentFormError(event.currentTarget, error.message, error.code === "password_reset_invalid" ? "token" : ""); }
    finally { submit.disabled = false; submit.textContent = original; }
  });
}

function openVerificationResend() {
  openModal(`<span class="eyebrow">SKILLSENTRA ACCOUNT</span><h2 id="modal-title">${tr("重发验证邮件", "Resend verification")}</h2><p>${tr("输入注册邮箱，我们会发送新的验证链接。", "Enter your registered email and we will send a fresh verification link.")}</p><form id="verification-resend-form" class="form-grid"><label>${tr("邮箱", "Email")}<input name="email" type="email" required autocomplete="email"></label><p class="form-error" role="alert"></p><div class="modal-actions"><button class="secondary" type="button" data-action="login">${tr("返回登录", "Back to sign in")}</button><button class="primary" type="submit">${tr("发送验证链接", "Send verification link")}</button></div></form>`);
  const form = $("#verification-resend-form");
  bindFormErrorRecovery(form);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const submit = event.currentTarget.querySelector("button[type='submit']");
    const original = submit.textContent;
    submit.disabled = true; submit.textContent = tr("正在处理…", "Working…");
    try {
      await api("/api/v1/auth/verify-email/resend", {method: "POST", body: Object.fromEntries(new FormData(event.currentTarget).entries())});
      closeModal(); toast(tr("如果账户需要验证，请查收新的验证邮件。", "If the account needs verification, a new email is on its way."));
    } catch (error) { presentFormError(event.currentTarget, error.message, error.code === "invalid_email" ? "email" : ""); }
    finally { submit.disabled = false; submit.textContent = original; }
  });
}

async function loadAuthProviders() {
  const container=$("#auth-providers"); if (!container) return;
  try {
    const {providers}=await api("/api/v1/auth/providers", {timeout:4000});
    const google=providers.google || {}; const chatgpt=providers.chatgpt || {};
    container.innerHTML=`<p class="auth-divider"><span>${tr("第三方登录", "Connected providers")}</span></p><button class="provider-login" type="button" data-provider="google" ${google.available?"":"disabled"}>G&nbsp;&nbsp;${tr("使用 Google 登录", "Continue with Google")}</button><small>${esc(google.reason || tr("将跳转到 Google 的安全授权页面", "You will be redirected to Google's secure authorization page"))}</small>${chatgpt.available ? `<button class="provider-login" type="button" data-provider="chatgpt">◉&nbsp;&nbsp;${tr("使用 ChatGPT 登录", "Continue with ChatGPT")}</button><small>${tr("将跳转到 ChatGPT 的安全授权页面", "You will be redirected to ChatGPT's secure authorization page")}</small>` : `<small>${esc(chatgpt.reason || "")}</small>`}`;
    container.querySelector("[data-provider='google']")?.addEventListener("click", beginGoogleLogin);
    container.querySelector("[data-provider='chatgpt']")?.addEventListener("click", beginChatGPTLogin);
  } catch { container.innerHTML=""; }
}

async function beginGoogleLogin(event) {
  const button=event.currentTarget; button.disabled=true; button.textContent=tr("正在跳转…", "Redirecting…");
  try { const result=await api("/api/v1/auth/google/start", {timeout:6000}); location.assign(result.authorization_url); }
  catch (error) { button.disabled=false; button.textContent=tr("使用 Google 登录", "Continue with Google"); toast(error.message); }
}

async function beginChatGPTLogin(event) {
  const button=event.currentTarget; button.disabled=true; button.textContent=tr("正在跳转…", "Redirecting…");
  try { const result=await api("/api/v1/auth/chatgpt/start", {timeout:6000}); location.assign(result.authorization_url); }
  catch (error) { button.disabled=false; button.textContent=tr("使用 ChatGPT 登录", "Continue with ChatGPT"); toast(error.message); }
}

async function openAccount() {
  if (!state.user) return openAuth("login");
  openModal(`<span class="eyebrow">YOUR ACCOUNT</span><h2 id="modal-title">${esc(state.user.display_name)}</h2><p>${esc(state.user.email)}</p><div class="detail-meta"><span>${esc(state.user.tenant_id)}</span><span>${esc(state.user.roles.join(" · "))}</span></div><div class="modal-actions"><button class="secondary" type="button" data-action="logout">${tr("退出登录","Sign out")}</button><button class="primary" type="button" data-action="creator">${tr("创作者中心","Creator center")}</button></div>`);
}

async function logout() { try { await api("/api/v1/auth/logout",{method:"POST",body:{}}); state.user=null; renderIdentity(); closeModal(); toast(tr("已退出登录","Signed out")); } catch(error){toast(error.message);} }

async function openCreator() {
  if (!state.user) return openAuth("register");
  try {
    const dashboard=await api("/api/v1/marketplace/creator");
    openModal(`<span class="eyebrow">CREATOR CENTER</span><h2 id="modal-title">${tr("发布与收入中心","Publishing & earnings")}</h2><p>${tr("只有通过交付硬门的不可变版本可以发布。","Only immutable versions that passed delivery gates can be published.")}</p><div class="creator-metrics"><article><strong>${dashboard.publications.length}</strong><span>${tr("已发布","Publications")}</span></article><article><strong>${dashboard.billing.orders}</strong><span>${tr("沙箱订单","Sandbox orders")}</span></article><article><strong>${money(dashboard.billing.payable_minor,"USD")}</strong><span>${tr("待结算","Payable")}</span></article></div><div class="modal-actions"><a class="secondary" href="studio.html">${tr("创建或优化 Skill","Create or improve")}</a>${Number(dashboard.billing.payable_minor)>0?`<button class="secondary" type="button" data-action="statement">${tr("生成结算单","Create statement")}</button>`:""}<button class="primary" type="button" data-action="publish" ${dashboard.delivered_versions.length?"":"disabled"}>${tr("发布 Skill","Publish Skill")}</button></div><div class="creator-list">${dashboard.publications.length?dashboard.publications.map(item=>`<div class="creator-row"><div><strong>${esc(item.name)}</strong><br><small>${money(item.price_minor,item.currency)} · ${item.review_count} ${tr("条评价","reviews")} · ${shortDigest(item.artifact_digest)}</small></div><span>${item.status}</span></div>`).join(""):empty(tr("还没有发布内容","Nothing published yet"),dashboard.delivered_versions.length?tr("选择一个已交付版本开始发布。","Choose a delivered version to publish."):tr("请先在 Studio 完成创建、验证和交付。","Complete creation, validation, and delivery in Studio first."),"create")}</div>`);
    state.creatorDashboard=dashboard;
  } catch(error) { if(error.status===401||error.status===403) openAuth("login"); else toast(error.message); }
}

async function openPublish() {
  const dashboard=state.creatorDashboard || await api("/api/v1/marketplace/creator");
  if (!dashboard.delivered_versions.length) return toast(tr("请先交付一个 Skill 版本","Deliver a Skill version first"));
  openModal(`<span class="eyebrow">PUBLISH SKILL</span><h2 id="modal-title">${tr("设置发布信息","Set publication details")}</h2><p>${tr("首次发布标记为“未策展”。平台核验后才会显示“精选”，声明项不会被包装成验证结论。","New listings are marked Unreviewed. Curated appears only after platform verification; declarations are never presented as verified facts.")}</p><form id="publish-form" class="form-grid"><label>${tr("已交付版本","Delivered version")}<select name="version_id" required>${dashboard.delivered_versions.map(item=>`<option value="${esc(item.id)}">${esc(item.version)} · ${shortDigest(item.artifact_digest)}</option>`).join("")}</select></label><div class="form-grid two"><label>${tr("名称","Name")}<input name="name" required maxlength="100"></label><label>${tr("短链接","Slug")}<input name="slug" required pattern="[a-z0-9-]+" placeholder="my-skill"></label></div><label>${tr("简介","Summary")}<textarea name="summary" required maxlength="600"></textarea></label><div class="form-grid two"><label>${tr("分类","Category")}<select name="category"><option value="productivity">Productivity</option><option value="research">Research</option><option value="writing">Writing</option><option value="coding">Coding</option><option value="data">Data</option><option value="design">Design</option><option value="operations">Operations</option></select></label><label>${tr("定价","Pricing")}<select name="pricing_type" id="publish-pricing"><option value="free">${tr("免费","Free")}</option><option value="paid">${tr("收费","Paid")}</option></select></label></div><div class="form-grid two" id="price-fields" hidden><label>${tr("价格（最小货币单位）","Price in minor units")}<input name="price_minor" type="number" min="50" value="500"></label><label>${tr("币种","Currency")}<select name="currency"><option>USD</option><option>CNY</option><option>EUR</option></select></label></div><div class="form-grid two"><label>${tr("许可证","License")}<input name="license_id" maxlength="80" placeholder="MIT / Apache-2.0 / Proprietary"></label><label>${tr("数据处理","Data handling")}<select name="data_policy"><option value="not_declared">${tr("未声明","Not declared")}</option><option value="local_only">${tr("仅本地处理","Local only")}</option><option value="declared_remote">${tr("声明远程处理","Declared remote processing")}</option></select></label></div><label>${tr("兼容环境（逗号分隔）","Compatible environments (comma-separated)")}<input name="compatibility" maxlength="640" placeholder="Codex, Markdown Skill hosts"></label><label>${tr("所需权限（逗号分隔）","Required permissions (comma-separated)")}<input name="permissions" maxlength="1000" placeholder="Read selected files, Network access"></label><label>${tr("支持方式","Support policy")}<select name="support_policy"><option value="community">${tr("社区支持","Community")}</option><option value="maintained">${tr("持续维护","Maintained")}</option><option value="enterprise">${tr("企业支持","Enterprise")}</option></select></label><p class="form-error" role="alert"></p><div class="modal-actions"><button class="secondary" type="button" data-action="close-modal">${tr("取消","Cancel")}</button><button class="primary" type="submit">${tr("确认发布","Publish")}</button></div></form>`);
  $("#publish-pricing").addEventListener("change",event=>$("#price-fields").hidden=event.target.value!=="paid");
  const publishForm = $("#publish-form");
  bindFormErrorRecovery(publishForm);
  publishForm.addEventListener("submit",async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget).entries());body.price_minor=Number(body.price_minor||0);try{await api("/api/v1/marketplace/publications",{method:"POST",body});closeModal();await loadPublic();toast(tr("Skill 已发布","Skill published"));}catch(error){const fieldByCode={invalid_publication_name:"name",invalid_publication_slug:"slug",publication_slug_exists:"slug",invalid_publication_category:"category",invalid_publication_price:"price_minor"};presentFormError(event.currentTarget,error.message,fieldByCode[error.code]||"");}});
}

async function openPublication(id) {
  try {
    const item=await api(`/api/v1/marketplace/publications/${id}`);
    let owned=false; if(state.user){try{const library=await api("/api/v1/marketplace/library");owned=library.some(row=>row.publication_id===id);}catch{}}
    const passport=item.trust_passport || {};
    const evidence=passport.evidence || [];
    const label=value=>({verified_delivery:tr("交付已验证","Verified delivery"),static_verified:tr("静态已验证","Static verified"),declared:tr("仅声明","Declared"),curated:tr("精选","Curated"),unreviewed:tr("未策展","Unreviewed"),passed:tr("通过","Passed"),partial:tr("部分","Partial"),unknown:tr("未知","Unknown")})[value]||value||tr("未知","Unknown");
    const list=(values,emptyText)=>values?.length?values.map(value=>`<li>${esc(value)}</li>`).join(""):`<li class="muted">${esc(emptyText)}</li>`;
    const gateLabel = passport.gate_status === "passed" && passport.dynamic_safety === "unknown" ? tr("静态门禁通过","Static gates passed") : label(passport.gate_status);
    openModal(`<span class="eyebrow">${esc(item.category.toUpperCase())}</span><div class="passport-title"><div><h2 id="modal-title">${esc(item.name)}</h2><p>${esc(item.summary)}</p></div><div class="trust-badges"><span class="trust-badge ${esc(passport.curation_status)}">${esc(label(passport.curation_status))}</span><span class="trust-badge ${esc(passport.status)}">${esc(label(passport.status))}</span></div></div><div class="detail-meta"><span>${esc(item.publisher_name)}</span><span>${stars(item.average_rating)} ${Number(item.average_rating).toFixed(1)} (${item.review_count})</span><span>${item.purchase_count} ${tr("次获取","acquired")}</span></div><section class="passport"><header><div><span class="eyebrow">TRUST PASSPORT</span><h3>${tr("可信护照","Trust passport")}</h3></div><span class="passport-state">${esc(gateLabel)}</span></header><div class="passport-alert"><strong>${tr("综合状态：部分验证","Overall status: Partially verified")}</strong><span>${tr("静态门禁已通过；动态安全未知不等于通过。AI 建议不能替代确定性验证。","Static gates passed; Unknown dynamic safety is not a pass. AI advice never replaces deterministic validation.")}</span></div><div class="passport-grid"><article><small>Artifact Digest</small><strong class="mono-value">${esc(passport.identity?.artifact_digest||item.artifact_digest||"—")}</strong></article><article><small>${tr("动态安全","Dynamic safety")}</small><strong class="unknown-value">${esc(label(passport.dynamic_safety))}</strong></article><article><small>${tr("许可证","License")}</small><strong>${esc(passport.license_id||"unspecified")}</strong></article><article><small>${tr("数据处理","Data handling")}</small><strong>${esc(passport.data_policy||"not_declared")}</strong></article><article><small>${tr("更新状态","Update status")}</small><strong>${esc(passport.update_status||"not_configured")}</strong></article><article><small>${tr("最后验证","Last verified")}</small><strong>${esc(passport.last_verified_at?new Date(passport.last_verified_at).toLocaleString(state.language):"—")}</strong></article></div><div class="passport-columns"><div><h4>${tr("验证证据","Validation evidence")}</h4><ul>${evidence.length?evidence.map(row=>`<li><span>${esc(row.stage)}</span><strong>${esc(label(row.status))}${row.score?` · ${Number(row.score).toFixed(0)}`:""}</strong></li>`).join(""):`<li class="muted">${tr("尚无独立验证证据","No independent validation evidence yet")}</li>`}</ul></div><div><h4>${tr("兼容环境","Compatibility")}</h4><ul>${list(passport.compatibility,tr("未声明","Not declared"))}</ul><h4>${tr("所需权限","Required permissions")}</h4><ul>${list(passport.permissions,tr("未声明","Not declared"))}</ul></div></div></section><div class="modal-actions">${owned?`<button class="secondary" type="button" data-action="review" data-id="${esc(id)}">${tr("写评价","Write review")}</button><button class="primary" type="button" disabled>${tr("已在资料库","In library")}</button>`:`<button class="primary" type="button" data-action="buy" data-id="${esc(id)}">${item.pricing_type==="free"?tr("查看并免费获取","Review & get free"):tr(`查看并沙箱购买 ${money(item.price_minor,item.currency)}`,`Review & sandbox buy ${money(item.price_minor,item.currency)}`)}</button>`}</div><div class="creator-list">${item.reviews.length?item.reviews.map(review=>`<div class="creator-row"><div><strong>${stars(review.rating)} ${esc(review.title||review.reviewer_name)}</strong><br><small>${esc(review.body||review.reviewer_name)}</small></div></div>`).join(""):`<p>${tr("还没有用户评价。","No reviews yet.")}</p>`}</div>`);
  } catch(error){toast(error.message);}
}

async function confirmPurchase(id) {
  if (!state.user) return openAuth("register");
  try {
    const item=await api(`/api/v1/marketplace/publications/${id}`);
    openModal(`<span class="eyebrow">PURCHASE PREVIEW</span><h2 id="modal-title">${item.pricing_type==="free"?tr("确认免费获取","Confirm free acquisition"):tr("确认沙箱购买","Confirm sandbox purchase")}</h2><p>${tr("请先核对制品身份、价格和资金影响。","Review artifact identity, price, and financial impact before continuing.")}</p><div class="confirmation-facts"><div><span>Skill</span><strong>${esc(item.name)}</strong></div><div><span>Artifact Digest</span><strong class="mono-value">${esc(item.artifact_digest)}</strong></div><div><span>${tr("金额","Amount")}</span><strong>${money(item.price_minor,item.currency)}</strong></div><div><span>${tr("资金影响","Financial impact")}</span><strong>${tr("仅沙箱记录 · 无真实扣款","Sandbox record only · no real charge")}</strong></div></div><form id="purchase-confirm-form" class="form-grid"><label class="consent-row"><input name="ack" type="checkbox" required><span>${tr("我已核对 Digest 和价格，并了解此操作只生成沙箱订单与账本记录。","I verified the Digest and price and understand this creates only sandbox order and ledger records.")}</span></label><div class="modal-actions"><button class="secondary" type="button" data-action="close-modal">${tr("返回","Back")}</button><button class="primary" type="submit">${tr("确认获取","Confirm acquisition")}</button></div></form>`);
    $("#purchase-confirm-form").addEventListener("submit",event=>{event.preventDefault();purchase(id);});
  } catch(error){toast(error.message);}
}

async function purchase(id) {
  if (!state.user) return openAuth("register");
  try { const result=await api(`/api/v1/marketplace/publications/${id}/purchase`,{method:"POST",headers:{"Idempotency-Key":`web-${crypto.randomUUID()}`},body:{accept_sandbox_charge:true}}); toast(result.already_owned?tr("已经在你的资料库中","Already in your library"):tr("已加入个人资料库","Added to your library")); await openPublication(id); }
  catch(error){toast(error.message);}
}

function openReview(id) {
  openModal(`<span class="eyebrow">VERIFIED REVIEW</span><h2 id="modal-title">${tr("评价这个 Skill","Review this Skill")}</h2><p>${tr("只有真实获取者可以评价；修改评价会更新排行榜。","Only verified acquirers can review; edits update the ranking.")}</p><form id="review-form" class="form-grid"><label>${tr("评分","Rating")}<select name="rating"><option value="5">★★★★★ · 5</option><option value="4">★★★★☆ · 4</option><option value="3">★★★☆☆ · 3</option><option value="2">★★☆☆☆ · 2</option><option value="1">★☆☆☆☆ · 1</option></select></label><label>${tr("标题","Title")}<input name="title" maxlength="100"></label><label>${tr("使用体验","Experience")}<textarea name="body" maxlength="1200"></textarea></label><p class="form-error" role="alert"></p><button class="primary" type="submit">${tr("提交评价","Submit review")}</button></form>`);
  const reviewForm = $("#review-form");
  bindFormErrorRecovery(reviewForm);
  reviewForm.addEventListener("submit",async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget).entries());body.rating=Number(body.rating);try{await api(`/api/v1/marketplace/publications/${id}/reviews`,{method:"POST",body});closeModal();await loadPublic();toast(tr("评价已发布","Review published"));}catch(error){presentFormError(event.currentTarget,error.message,error.code==="invalid_rating"?"rating":"");}});
}

async function createStatement() {
  try { const end=new Date();const start=new Date(end.getFullYear(),end.getMonth(),1);await api("/api/v1/marketplace/statements",{method:"POST",body:{cycle_start:start.toISOString(),cycle_end:end.toISOString()}});closeModal();toast(tr("结算单已生成，可在管理后台查看","Statement created; view it in the control plane"));await openCreator();}catch(error){toast(error.message);}
}

function confirmStatement() {
  const end=new Date(); const start=new Date(end.getFullYear(),end.getMonth(),1);
  openModal(`<span class="eyebrow">STATEMENT PREVIEW</span><h2 id="modal-title">${tr("确认生成结算单","Confirm statement creation")}</h2><p>${tr("生成后，本周期符合条件的订单将被固定到不可变结算血缘；历史记录不会被覆盖。","Eligible orders in this cycle will be frozen into immutable settlement lineage; history is never overwritten.")}</p><div class="confirmation-facts"><div><span>${tr("周期","Cycle")}</span><strong>${start.toLocaleDateString(state.language)} – ${end.toLocaleDateString(state.language)}</strong></div><div><span>${tr("模式","Mode")}</span><strong>${tr("沙箱结算 · 生产付款关闭","Sandbox settlement · production payout off")}</strong></div></div><form id="statement-confirm-form" class="form-grid"><label class="consent-row"><input name="ack" type="checkbox" required><span>${tr("我已核对结算周期，并了解此操作会固定历史血缘但不会划转真实资金。","I verified the cycle and understand this freezes historical lineage but moves no real funds.")}</span></label><div class="modal-actions"><button class="secondary" type="button" data-action="close-modal">${tr("取消","Cancel")}</button><button class="primary" type="submit">${tr("确认生成","Confirm creation")}</button></div></form>`);
  $("#statement-confirm-form").addEventListener("submit",event=>{event.preventDefault();createStatement();});
}

let toastTimer; function toast(message){const element=$("#toast");element.textContent=message;element.classList.add("is-visible");clearTimeout(toastTimer);toastTimer=setTimeout(()=>element.classList.remove("is-visible"),2800);}

initialize();
