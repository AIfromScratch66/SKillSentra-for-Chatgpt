# SkillSentra Studio 本地开发指南

> 开发版本：0.6.3 生产强化候选
> 验证日期：2026-08-29
> 边界：本地开发与受控 Private Beta 基线；生产外部证据见就绪报告

## 1. 已实现能力

- Apple 风格双路径工作台：统一为定义、创建、验证、发布、迭代五阶段；模板保留 9 项检查，现有 Skill 保留 7 项检查。
- 可信护照：展示 Digest、硬门/证据、策展、许可、兼容、权限、数据策略、更新状态，动态安全无证据时保持 Unknown。
- 影响预览：注册、获取、Statement、沙箱 payout、部署和撤销需要显式理解与确认。
- 真实 Skill 引擎：发现授权目录、冻结来源清单、生成隔离候选、静态扫描、结构化评价、回归、ZIP 交付。
- 项目管理：自动恢复上次项目，使用步骤修订号避免静默覆盖，在项目中心查看版本、验证、AI、更新和审计。
- 全过程 AI：内置 Mock、OpenAI Responses API 与兼容网关；创建、独立评价、安全复核三角色顺序编排。
- 费用与安全：服务端价格表按实际 token 计算；浏览器不接收 Key；输入只保留字段、字节数和 SHA-256 摘要。
- 动态更新：保存更新策略，生成 Base / Local / Upstream 文件级差异，冲突只提示、不自动覆盖。
- 证据控制面：固定 GitHub commit 或授权本地来源，生成不可变快照和分项 Evidence Attestation。
- Agent 与安全：desired/observed Digest、Adapter 能力等级、漂移、精确撤销及撤销优先传播。
- 结算沙箱：E2/E3 收据资格、版本化计价和分成、整数双重记账、Statement、幂等 payout。
- 对外私测基线：Bearer Token、角色、租户过滤、可信 Origin、限流、有界并发、容器加固和哈希审计链。
- 账户与个人工作区：注册登录、加盐 `scrypt`、会话 Cookie、CSRF、个人项目所有权。
- Skill 市场：已交付版本发布、免费/收费、沙箱获取、个人资料库、获取后评价和排行榜。
- 自动发现：后台配置 GitHub/指定 HTTPS 目标、周期、启停、立即运行和运行记录。
- 外部关键词发现：搜索 GitHub 仓库或用户明确指定的公开 HTTPS JSON 目录；所有结果保持 `unverified_candidate`，不会下载或执行仓库代码。
- 内部模拟专家矩阵：十席角色、八维权重、E0-E5 证据、精确 Artifact Digest、Evaluation World 和六个硬门；内部评分不能授权发布。
- 七日真实信号榜：只使用近七日获取后评价和已支付沙箱获取，无信号不上榜，终身累计值单独返回。
- Codex 插件与 MCP：`plugins/skillsentra` 提供显式 `$skillsentra-for-chatgpt` Skill 和独立 `skillsentra-for-chatgpt` MCP 服务；写操作必须在用户确认后传入 `confirmed: true`。
- 运行状态：`/livez` 区分进程存活，`/readyz` 验证 SQLite 与迁移账本，`/metrics` 提供不含路径、查询、租户或请求体的有界本机摘要。
- 生产强化候选：可选邮箱验证/密码重置、STARTTLS 邮件投递、ChatGPT 授权码 + PKCE 适配器，以及管理员只读 `/api/v1/control/production-readiness` 门禁快照；所有外部证据与人工发布门仍需单独完成。

## 2. 启动

在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
```

启动和测试脚本在可用时优先使用 Codex 随附的 Python 运行时，再回退到系统 Python，避免 WindowsApps 的无效 Python 占位程序导致服务无法启动。

访问 `http://127.0.0.1:8766/` 进入 Skill 市场，`/studio.html` 进入 Skill Studio，`/platform.html` 进入证据控制台。数据库和隔离制品默认位于 `data/`，均已加入 `.gitignore`。

可通过环境变量改变位置：

```powershell
$env:SKILLSENTRA_PORT = '8767'
$env:SKILLSENTRA_DATABASE_PATH = 'C:\temp\skillsentra-dev.db'
$env:SKILLSENTRA_ARTIFACT_DIR = 'C:\temp\skillsentra-artifacts'
$env:SKILLSENTRA_ALLOWED_SKILL_ROOTS = 'C:\skills\team;C:\skills\personal'
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
```

服务默认只监听 `127.0.0.1`。示例目录 `sample-skills/` 始终作为演示来源加入允许列表；本地启动脚本默认载入诚实的静态精选目录，生产配置必须保持 `SKILLSENTRA_DEMO_SEED=false`。

GitHub Top 100 刷新可使用短期只读 Token，并把单次超时、尝试次数、退避和总预算分别限制：

```powershell
$env:SKILLSENTRA_GITHUB_TOKEN = '<short-lived-read-only-token>'
$env:SKILLSENTRA_GITHUB_TIMEOUT_SECONDS = '10'
$env:SKILLSENTRA_GITHUB_MAX_ATTEMPTS = '2'
$env:SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS = '0.25'
$env:SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS = '11'
```

认证失败不会重试；暂时性失败只在总预算内有限重试。有最后成功快照时返回 `stale_fallback`，冷启动失败返回 `unavailable`，不会虚构候选。

## 3. Skill 制品与安全边界

导入流程只读取允许根目录内的文件，不运行其中的脚本。以下情况会失败关闭：

- 符号链接、越过允许根目录的路径；
- `.env`、私钥、证书和常见凭据文件；
- 单文件超过 2 MB、总量超过 8 MB、文件数超过 250；
- 管道到 Shell、递归删除和疑似明文密钥等静态高风险模式。

制品摘要使用 `artifact-canon-v1`：路径与内容共同进入域隔离 SHA-256。现有 Skill 先复制为只读基线；候选只从基线生成，原目录不会被覆盖。交付前必须通过静态、评价/基线和当前候选回归阶段门。

## 4. 接入 AI 模型

### 内置 Mock

无需密钥，用于完整功能联调。三角色输出和 token 固定，不代表真实模型评价。

### OpenAI API

```powershell
$env:SKILLSENTRA_OPENAI_API_KEY = '<your-key>'
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
```

在“AI 控制中心”选择 OpenAI API。界面只提交 `SKILLSENTRA_OPENAI_API_KEY` 这个环境变量名称，真实 Key 由服务端读取。

### 企业兼容网关

```powershell
$env:SKILLSENTRA_CUSTOM_API_KEY = '<your-key>'
```

兼容网关必须使用 HTTPS；只有本机回环地址允许 HTTP。服务会阻断用户信息、私网/链路本地/保留 IP、重定向和超过 2 MB 的响应。当前 DNS 检查不能替代生产级出站代理与网络层固定解析。

### 价格表

费用只由服务端根据返回的实际 token 计算。通过以下变量维护可审计版本：

```powershell
$env:SKILLSENTRA_PRICEBOOK_VERSION = 'team-pricebook-2026-08'
$env:SKILLSENTRA_PRICEBOOK_CURRENCY = 'USD'
$env:SKILLSENTRA_MODEL_PRICES_JSON = '{"gpt-":{"input_per_million":2,"output_per_million":8}}'
```

这里的价格必须由团队按供应商账单维护；系统不会声称内置默认价格等于供应商当前公开价格。

## 5. API 概览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/livez` | 进程存活探针；不代表数据库就绪 |
| GET | `/readyz` | SQLite 与迁移账本就绪探针；失败返回 503 |
| GET | `/metrics` | 有界进程内请求量、状态类别和时延摘要 |
| GET | `/api/v1/bootstrap` | 项目、模型、价格表和授权 Skill 启动数据 |
| GET / POST | `/api/v1/projects` | 项目列表 / 创建项目 |
| GET | `/api/v1/projects/{id}` | 恢复步骤、策略、版本、验证和更新策略 |
| PUT | `/api/v1/projects/{id}/steps/{index}` | 带修订号保存，并执行后端阶段门 |
| POST | `/api/v1/projects/{id}/import` | 导入授权 Skill 并冻结基线 |
| POST | `/api/v1/projects/{id}/candidate` | 生成隔离候选并静态扫描 |
| GET / POST | `/api/v1/projects/{id}/validations` | 查询 / 运行评价或回归 |
| POST | `/api/v1/projects/{id}/deliveries` | 生成不可变 ZIP |
| POST | `/api/v1/projects/{id}/ai-orchestrations` | 顺序运行创建、评价和安全角色 |
| GET | `/api/v1/projects/{id}/ai-runs` | token、费用、模型和编排台账 |
| GET | `/api/v1/projects/{id}/audit` | 项目审计事件 |
| GET / PUT | `/api/v1/projects/{id}/update-policy` | 查询 / 保存更新策略 |
| GET / POST | `/api/v1/projects/{id}/update-checks` | 查询 / 运行三方差异 |
| POST | `/api/v1/update-jobs/run-due` | 运行到期更新检查 |
| POST | `/api/v1/auth/register` / `login` / `logout` | 个人用户注册、登录与退出 |
| GET / POST | `/api/v1/marketplace/publications` | 公开目录 / 发布已交付 Skill |
| GET | `/api/v1/marketplace/leaderboard` | 排行榜 |
| POST | `/api/v1/discovery/search` | GitHub / HTTPS 目录外部候选搜索；结果保持未验证 |
| GET / POST | `/api/v1/discovery/github/top-skills` | 读取 Top 100 快照 / 管理员触发有界刷新 |
| GET | `/api/v1/expert-framework` | 十席内部模拟专家框架、维度、证据等级和硬门 |
| GET / POST | `/api/v1/projects/{id}/expert-reviews` | 查询 / 写入绑定精确摘要和 Evaluation World 的专家评价 |
| POST | `/api/v1/marketplace/publications/{id}/purchase` | 免费获取或沙箱购买 |
| POST | `/api/v1/marketplace/publications/{id}/reviews` | 获取后评价 |
| GET / POST | `/api/v1/admin/scan-targets` | 自动扫描目标列表 / 新增 |
| POST | `/api/v1/admin/scan-targets/{id}/run` | 立即运行指定扫描 |
| GET | `/api/v1/control/production-readiness` | 管理员只读生产就绪检查；`no_go` 不改变发布状态 |

所有 API 使用统一 JSON 信封；下载端点返回 ZIP。

## 6. 数据库

SQLite 开启外键、WAL 和 busy timeout。主要表为：

- `projects`、`step_states`：项目、当前步骤、步骤修订号和结构化输入；
- `skill_sources`、`skill_versions`：授权来源、冻结清单、候选与交付包；
- `validation_runs`：规则版本、分数、发现项和证据；
- `ai_connections`、`ai_policies`、`ai_runs`：连接引用、三角色策略、用量和价格表版本；
- `update_policies`、`update_checks`：频率、冻结 Base 和三方差异；
- `audit_events`：关键状态变化。
- `users`、`tenant_memberships`、`user_sessions`：账户、工作区成员和会话。
- `marketplace_publications`、`marketplace_orders`、`user_skill_library`、`marketplace_reviews`：发布、订单、个人资料库和评价。
- `scan_targets`、`scan_runs`：自动发现配置和运行记录。
- `expert_reviews`：绑定项目、制品摘要、Evaluation World、维度、硬门和决策的内部模拟评价记录。
- `account_action_tokens`：邮箱验证和密码重置的一次性哈希令牌。

数据库不保存真实 API Key，也不保存 AI 原始输入上下文。

## 7. 测试与 CI

本地确定性测试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

当前公开源码版仍需以最终确定性测试脚本重新记录 Python、前端语法与文档链接结果；通过结果不等于生产授权。Personal 版本的内部过程记录不包含在公开快照中。

浏览器测试定义在 `tests/e2e/`，覆盖 Studio、双语、移动端、市场、证据控制面、外部发现和专家矩阵。当前 v0.6.3 候选已在本机 Chromium 完成 10/10 条旅程；GitHub Actions 在 Ubuntu 与 Windows 运行 Python 测试，并在 Chromium 运行浏览器测试。

本机性能证据可重复生成：

```powershell
python scripts/benchmark_control_plane.py --iterations 300 --output evidence/performance/control-plane-benchmark.json
```

该命令是单节点合成测量，不替代 Staging 容量、并发、可靠性或长稳测试。

本地合成备份恢复演练：

```powershell
python scripts/rehearse_restore.py --output evidence/recovery/sqlite-restore-rehearsal.json
```

演练验证数据库完整性、Artifact Identity、账本平衡、审计链和合格 Receipt；生产加密备份、RPO/RTO 和实际运维恢复仍须在目标环境执行。

## 8. 动态更新任务

可由 Windows 任务计划程序或 CI 定时调用一次性命令：

```powershell
python -m scripts.run_due_updates --limit 20
```

命令只处理已到期且启用的策略，输出 JSON，并把下一次检查时间向后推进。它不会自动合并、覆盖或安装 Skill。

## 9. Codex 插件与 MCP 桥

插件源码位于 `plugins/skillsentra/`。它包含插件 manifest、显式调用 Skill、MCP 配置和标准输入输出桥接脚本。当前本机 ChatGPT 插件固定连接隔离实例 `http://127.0.0.1:8866`；其他部署可在插件 MCP 配置中把 `SKILLSENTRA_BASE_URL` 改为受控 SkillSentra 服务。

十一项工具中，`health`、`search_skills`、`discover_skills`、`get_skill`、`leaderboard`、`list_projects` 和 `get_expert_framework` 为只读；`create_project`、`evaluate_project`、`record_expert_review` 和 `check_updates` 会写入审计记录，必须在用户明确确认后传入 `confirmed: true`。

该桥不会安装、调用或自动更新已下载 Skill，也不会发布、生产部署或批准发布。官方插件/Skill validator 和本地 MCP 协议测试通过，只构成 E2 证据；真实 ChatGPT 云端连接仍需新宿主会话 E4 验证。

## 10. 当前限制

- v0.6 延续账户会话、操作员 Bearer Token 和 hybrid 模式；邮箱验证、密码找回、MFA/step-up、企业 OIDC/SSO 和防自动化滥用仍是对外 Gate。
- 支持 GitHub 公共仓库和由服务端注入短期 Token 的私有读取，但不负责生成 GitHub App installation token。
- 不执行导入 Skill 的脚本，因此动态运行安全 L5 保持 `Unknown`。
- 确定性评价是结构与启发式证据，不等同于真实任务执行；AI 评价独立展示，也不能替代硬门。
- 外部模型需用户配置服务端密钥；本轮未使用真实密钥进行联网调用。
- 项目动态更新仍可由外部调度器调用；GitHub/指定网站发现已有有界后台调度线程，生产多实例调度仍需专用 Worker/锁。
- SQLite 配置适用于单节点 Private Beta；一般可用、多节点或高写入多租户服务须按 RFD 迁移 PostgreSQL。
- 支付仅为沙箱，生产 payout 在数据约束和 API 中都不可启用。
- 本轮实际加载 AI Devloop `24.0.0+codex.20260829125719` 并同步 T9 工作上下文；这不代表 T9 门禁已完成，也不构成发布授权。
- 内部基线为 7.23/10，实施后建议原始分为 8.29/10；`real_host_connection` 缺 E4 证据导致 7.9 封顶和生产 `NO_GO`，不能伪造超过 9.999/10。
- 真实 ChatGPT 云端插件联通、真实用户任务证据、自动应用更新、人工无障碍以及生产 TLS/密钥/备份仍未完成。

## 11. 对外 Private Beta 配置

复制 `.env.production.example` 到部署平台的密钥/配置系统，至少配置高熵访问令牌、精确可信 HTTPS Origin、加密持久卷和备份。容器默认非 root、只读根文件系统、删除全部 Linux capabilities，并只把服务端口绑定到宿主回环地址；外部流量必须经过维护中的 HTTPS 反向代理。

```powershell
docker compose up --build -d
```

完整进入条件、未完成的外部 Gate 和真实付款边界见 `product/05-release/production-readiness-report-v0.6.md` 与 `SECURITY.md`。当前只允许本地/受控 Private Beta；不得据此创建生产标签或公开发布。
