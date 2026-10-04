# SKillSentra for Chatgpt

> 在 ChatGPT 中基于证据治理精确版本的 Agent Skill。

[English](README.md)

## 当前状态

当前公开源码版本为 **v0.8.0**，基于 SkillSentra Personal v0.6.5。该版本增加面向 Streamable HTTP 客户端的无状态 JSON 响应式 MCP-over-HTTP 适配，同时保留精确版本证据、写操作明确确认和人工发布门禁。

专用名称为 **SKillSentra for Chatgpt（ChatGPT 专用协作版）**，短说明为 **在 ChatGPT 中治理 Skills**。这是 SkillSentra 的独立适配，并非由 OpenAI 制作或背书。

本仓库及 v0.8.0 GitHub Release 是公开源码发行版；它不等于公网 MCP 已部署、真实 ChatGPT 已联通、生产批准或 ChatGPT Directory 审核通过。便携 ZIP 仍是需要由操作方配置并验证的候选骨架。详见 [ChatGPT 配置与安全边界](CHATGPT.md)、[v0.8.0 版本说明](product/05-release/release-notes-v0.8.0.md)和[本轮验证报告](evidence/validation-report-v0.8.0.md)。

## v0.8.0 新增能力

- 新增 `POST /mcp` 的无额外依赖、无状态 JSON 响应式 MCP-over-HTTP 预览网关，与 stdio 桥复用同一组 19 项版本化工具定义和执行层；尚未实现 GET/SSE 和会话生命周期。
- 默认只监听本机回环地址，限制 JSON 请求体并支持 Origin 白名单；所有监听默认校验独立网关密钥，匿名只允许显式启用的 loopback 测试模式。
- 新增便携 Agent Plugins manifest，专用标识为 `skillsentra-for-chatgpt`；原 Codex manifest 仅保留为兼容入口。
- 新增确定性的便携插件构建器：由操作方提供外观上符合要求的公网 HTTPS `/mcp` URL，再生成根级清单正确的候选骨架，不虚构注册元数据。静态 URL 检查不验证端点真实存在、OAuth、ChatGPT 兼容性或提交就绪状态。
- 增加 ChatGPT 专用部署、测试和证据边界，不虚构公网地址、注册 ID、OAuth 准入或目录上架状态。

## 本地启动 ChatGPT 适配器

先启动 SkillSentra，再在第二个终端启动 MCP 网关：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
$env:SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN = "<生成的网关密钥>"
$env:SKILLSENTRA_API_TOKEN = "<另一个 SkillSentra 服务令牌>"
python plugins/skillsentra/scripts/chatgpt_mcp_server.py
```

本地 MCP 地址为 `http://127.0.0.1:8787/mcp`。非 ChatGPT 客户端可把网关密钥作为 Bearer，网关向上游只使用另一个服务令牌；两者不得复用。若只做隔离的 loopback 协议测试，必须显式设置 `SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK=true` 才能匿名。ChatGPT 不能直接呈现这个自定义静态 API Key 或共享 Bearer；不要把预览服务直接暴露到公网，也不能把增加 TLS 反向代理视为认证已经完成。

真实 ChatGPT Developer Mode 测试必须采用 [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)，或在公网 HTTPS `/mcp` 前部署满足 [MCP OAuth 2.1 认证要求](https://developers.openai.com/plugins/build/auth)的安全网关，包括发现元数据、授权码与 PKCE `S256`、resource/audience 校验和 scope 强制执行。当前候选尚未实现或验证任一路径。这里的 MCP OAuth 与旧版“使用 ChatGPT 登录”是两个独立流程。

## 兼容性与证据边界

单元测试、协议测试和本地浏览器测试通过，只能证明被测提交下的本地行为；不能单独证明官方 MCP Inspector 互操作、ChatGPT 已成功连接、公共目录已获批、已生产部署或已产生真实用户价值。v0.8.0 尚未记录官方 MCP Inspector 或真实 ChatGPT 通过。真实 ChatGPT 证据需要在 Developer Mode 中，针对精确部署提交完成工具发现和有代表性的读写旅程；该证据仍不授权安装、应用更新、生产部署或目录发布。

ChatGPT 写入继续受既有工具 schema 约束。状态变更工具要求明确确认，建议回写仍绑定精确 request ID 和 context hash。提交成功不等于采纳、阶段完成或发布批准。

## v0.6.3 新增能力

- 策略资格判断复用收据事务，不再为每次撤销检查新建 SQLite 连接。最终本机合成运行中，事务内谓词 p95 为 `0.0115ms`，完整收据处理 p95 为 `53.4933ms`；二者都只是本地工程证据，不是生产容量证明。
- 新增 `/livez`、`/readyz` 和有界 `/metrics`；容器健康检查在 SQLite 或迁移账本不可用时失败关闭，请求指标不保留路径、查询参数、租户或请求体。
- GitHub Top 100 刷新支持可配置超时、有限指数退避、可审计刷新元数据和最后成功快照回退；冷启动失败明确显示不可用，不虚构候选。
- Google/ChatGPT 登录诊断明确列出缺少的服务端前置条件且不暴露配置值；ChatGPT 登录在真实外部应用准入和回调实现前继续不可用。
- GitHub 发布打包默认只读；只有标签专用的预发布任务在验证标签与包版本一致后获得内容写权限。

## v0.6.3 生产强化候选增量

- 增加可选的生产邮箱验证、一次性密码重置、会话撤销和验证邮件重发；邮件适配器强制使用 SMTP STARTTLS，令牌只保存哈希。
- 增加服务器端 ChatGPT 授权码 + PKCE 适配器和安全回调边界；默认关闭，未完成外部准入、官方凭据和真实宿主验证前不会伪造可用状态。
- 增加管理员只读生产就绪检查：`GET /api/v1/control/production-readiness`。该接口只输出无密钥值的 `go/no_go` 快照，不会发布、部署或授权发布。

该历史生产强化候选仍未关闭 SQLite 单节点、真实 OAuth/ChatGPT 宿主证据、TLS/托管密钥/备份/监控及人工无障碍等硬门；这些边界不影响 v0.8.0 的公开源码发布，但继续阻止生产/GA。

## v0.6.2 新增能力

- AI 连接和本地 Skill 源改为按租户隔离；项目策略绑定会拒绝使用其他租户的连接。
- 新增有界 GitHub `topic:skill` Top 100 候选快照，按仓库星标排序。每一项仍为 `unverified_candidate`，必须完成静态扫描；它不是已可安装目录，也不是平台已验证周榜。
- Windows MCP 启动显式使用 UTF-8 输入输出，服务版本从插件 manifest 读取，并使用专用 composer/logo 图标资产。
- 修复有界发现传输、MCP 非 ASCII 输出、市场榜单降级状态和浏览器旅程的本地回归问题。精确候选制品仍须在安装前通过全量测试。

## v0.6.1 新增能力

- 注册/登录交互会立即反馈“正在处理”、防止重复提交、在网络等待过久时给出明确提示，并在浏览器中提前说明密码规则。
- 支持由环境变量启用的 Google OAuth 授权码登录：短时一次性状态校验、经验证邮箱的身份绑定，不保存第三方访问令牌。请在 `.env.example` 中配置三个 `SKILLSENTRA_GOOGLE_*` 变量，并在 Google Cloud 登记回调地址。
- 加入“使用 ChatGPT 登录”入口，但不会伪造可用状态：它依赖 OpenAI 对外部应用的合作准入和凭据，在获得准入前保持不可用。

## v0.6.0 新增能力

- 十席内部模拟专家团：八个加权维度、E0-E5 证据等级、精确制品摘要、Evaluation World 和默认失败的硬门封顶规则。
- GitHub 仓库与用户指定 HTTPS 目录关键词发现：所有外部结果均标记为 `unverified_candidate`，必须固定 commit 并完成静态扫描后才能进入后续流程。
- 真实滚动七日榜：只使用近七日的获取后评价与已支付沙箱获取信号；终身累计值分开显示，无信号项目不上榜。
- Codex 插件与 stdio MCP 桥：共十一项工具，七项只读；四项写操作必须在用户明确确认后传入 `confirmed: true`。
- 可视化 Studio 与证据控制面：支持创建、评价、比较、交付和检查更新，且不会静默覆盖本地内容。

插件桥不会安装 Skill、运行已下载 Skill、自动应用更新、发布版本、生产部署或批准发布。本地 MCP 调用成功、内部模拟评分或自动化测试通过，都不等于真实 ChatGPT 云端联通或发布授权。

## 本地运行

环境：Windows PowerShell、Python 3.11 及以上版本。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
```

访问：

- Skill 市场：<http://127.0.0.1:8766/>
- Skill Studio：<http://127.0.0.1:8766/studio.html>
- 证据控制面：<http://127.0.0.1:8766/platform.html>

运行确定性测试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

v0.6.3 会在安装前运行确定性测试与浏览器旅程；本地通过仍不同于生产发布授权。

## 八项价值实现状态

| 用户价值 | 当前状态 | 边界 |
|---|---|---|
| 一站式全生命周期管理 | 部分完成 | 本地已覆盖创建、评价、比较、交付与更新检查；不自动安装或运行 Skill |
| GitHub/网站搜索 Skill | 本地实现 | GitHub 与显式 HTTPS 目录均为有界读取；候选保持未验证 |
| 顶级专家评价团 | 本地实现 | 十席为 ChatGPT/Codex 内部模拟，不代表外部专家背书 |
| ChatGPT 结构化协作 | 部分完成 | 便携候选元数据、stdio 和无状态 JSON 响应式 MCP-over-HTTP 协议预览已实现；官方 MCP Inspector 与真实 ChatGPT 均未验证 |
| 用户显式选择 Skill | 部分完成 | `$skillsentra-for-chatgpt` 配置为禁止隐式调用；仍需在新宿主会话中完成真实联通 |
| 已下载 Skill 自动更新 | 部分完成 | 可定时检查并生成 Base/Local/Upstream 差异，不自动合并、覆盖或安装 |
| 统计排名与热门榜 | 本地实现 | 采用七日真实信号，沙箱获取不等于真实市场结果 |
| ChatGPT 插件与联通测试 | 部分完成 | 已有候选骨架与本地自动化覆盖；官方 MCP Inspector、OAuth 和真实 ChatGPT 联通均为 NO_GO |

## 评分与发布边界

v0.8.0 已获授权作为公开 GitHub 源码发行版发布，但并未关闭生产硬门。公网生产/GA、真实资金划转、Skill 自动安装或自动应用更新、公网 MCP 云部署和 ChatGPT Directory 提交仍未获授权。

仍待完成：真实 ChatGPT 云端联通、真实用户任务成效、与精确对象绑定的可验签审批回执、生产可观测性，以及包含 TLS、托管密钥、加密备份与恢复、独立人工发布授权的生产环境。内部评分和本地测试不能覆盖这些硬门。

## 文档入口

- [开发指南](DEVELOPMENT.md)
- [ChatGPT 适配指南](CHATGPT.md)
- [v0.8.0 版本说明](product/05-release/release-notes-v0.8.0.md)
- [v0.8.0 验证报告](evidence/validation-report-v0.8.0.md)
- [安全策略](SECURITY.md)
- [第三方组件说明](THIRD_PARTY_NOTICES.md)

## 许可证

本仓库未授予开源许可证，沿用 Personal 版本既有的许可状态：源码公开可见，保留所有权利。你可以在获得授权的个人使用范围内查看和运行软件；复制、修改、再分发、再许可或分发衍生作品，须事先取得著作权人的许可。第三方组件继续适用其各自许可证，详见[第三方组件说明](THIRD_PARTY_NOTICES.md)。
