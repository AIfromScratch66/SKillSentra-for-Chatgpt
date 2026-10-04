# SkillSentra MVP 发布、回滚与已知限制

> 版本：1.0  
> 状态：v0.4 已实现账户、市场与自动发现基线；外部 C4 发布批准尚未执行
> 生产真实付款：默认关闭，MVP 仅允许沙箱链路

## 1. 发布原则

SkillSentra 以“证据链不被跨版本切断”为发布底线。应用、数据库、扫描规则、策略包、Agent Adapter、价格表和分成规则分别版本化；任何发布必须能够回答“什么版本、影响哪些租户、怎样停止、怎样恢复、财务怎样不重算历史”。

发布判断采用硬门而非平均分：租户隔离、重大风险门禁、撤销、账本平衡、幂等和生产付款开关中任一项失败，均不得扩大流量。

## 2. 环境与权限

| 环境 | 数据 | 外部集成 | 资金能力 | 允许人群 |
|---|---|---|---|---|
| Local/CI | 合成 fixture | Mock/GitHub 测试仓库 | 假 Provider | 开发与测试 |
| Dev | 非敏感测试数据 | GitHub App 开发实例 | Test mode | 项目团队 |
| Staging | 脱敏/合成近生产数据 | 独立安装与 webhook | Test mode | 发布、Security、Finance |
| Production | 真实租户数据 | 生产 GitHub App | **Payout hard-off**，须另过 C4/C5 | 最小授权运维角色 |

生产密钥、Webhook Secret、签名密钥和支付凭据不得从低环境复制。生产控制台启用强身份验证、最小权限、审批和完整审计。

## 3. 渐进发布

| 阶段 | 范围 | 进入条件 | 观察窗口 | 退出/停止条件 |
|---|---|---|---|---|
| R0 | 团队内部测试租户 | CI、迁移、属性测试、恢复演练通过 | ≥3 个工作日 | 任一 P0/P1 或不变量失败 |
| R1 | 3–5 个设计合作方 | 安全评审、租户矩阵、回滚演练通过 | ≥7 天 | 数据串租、错误 Allow、账不平 |
| R2 | 受邀个人与团队 | R1 SLO 达标、支持流程可用 | ≥14 天 | 错误率/积压越阈，争议不可解释 |
| R3 | 一般可用但受控 | C4 发布批准、容量与灾备通过 | 持续 | 依据事故级别回退 |
| R4 | 真实付款试点 | 法务/税务/KYC/支付运营与 C5 批准 | 逐批次 | 对账差异或重复支付 |

首发采用租户 allowlist、功能开关和服务端策略三层控制。UI 隐藏不能替代服务端拒绝。

## 4. 功能开关

| 开关 | 默认 | 回退行为 |
|---|---|---|
| `github_private_import` | Off | 仅公共仓库，保留已固定快照 |
| `dynamic_sandbox_scan` | Off | 只做静态、不执行扫描 |
| `agent_mutations` | Off | Agent 页面只读，保留观测 |
| `policy_enforcement` | Shadow | 记录建议，不宣称 Enforced |
| `settlement_freeze` | Off | 允许预览，不生成最终 Statement |
| `payout_sandbox` | On in staging | 只写内部沙箱状态 |
| `payout_production` | **Hard Off** | API 和 Worker 双重拒绝 |

`payout_production` 不能由普通管理员、前端参数或数据库单字段开启；须具备独立权限、双人批准、变更单和部署级配置。

## 5. 发布前检查

- 构建来源、依赖锁、SBOM、签名/证明可核验；
- 数据库向前迁移与应用向后兼容窗口验证；
- 所有 P0 需求有可定位测试证据，追踪台账无孤儿项；
- 租户越权、Webhook 重放、Archive 解包和权限矩阵专项通过；
- Agent Adapter conformance 与 Unsupported 展示验证；
- 完整 Artifact Identity 静态契约测试和 Signed Envelope 入库/恢复后复验通过；
- Policy cache 失效、撤销优先和 kill switch 演练通过；
- Ledger 属性、周期冻结、冲正、对账和 payout 幂等通过；
- 备份恢复演练满足 RPO/RTO；
- 值班、告警、状态页、客户通知和证据保全责任人就绪。

### 5.1 Go/No-Go RACI

| 决策项 | R | A | C | I |
|---|---|---|---|---|
| 构建/迁移/回滚 | Platform + SRE | Release Owner | QA、CTO | Product/Support |
| 租户/安全/供应链 | Security | CTO | Agent、Platform、Privacy | 全体 Gate owners |
| Agent conformance/撤销 | Agent + Security | CTO | QA、SRE | Product/客户 Security |
| Ledger/Statement/对账 | Settlement + QA | Finance Owner | Security、CTO | Product |
| 真实 payout/地区 | Finance + Legal + Security | C5 Sponsor | Provider、Privacy、CTO | Operations/Support |
| 客户公告/限制 | Product + Comms | Incident/Release Owner | Legal、Security | 受影响客户 |

No-Go 后由对应 A 指定修复与复核；Release Owner 不能覆盖 Security/Finance hard stop。会议记录使用 Gate 模板并链接构建与证据版本。

## 6. 回滚策略

### 6.1 应用与 Worker

使用向后兼容的 expand/migrate/contract：先扩展 schema，再发布双读/双写兼容版本，最后在观察期后收缩。回滚应用时不得回滚已提交账本分录；通过修复版本或 adjustment 纠正。

### 6.2 扫描规则与策略

- 扫描结果保留 engine/ruleset 版本；错误规则下线后，对受影响 Digest 创建重新扫描任务；
- Policy Bundle 只发布签名版本，保留最近两个稳定版本；
- 新策略异常时切回上一版本，但显式撤销列表始终优先，不能随策略回退而恢复。

### 6.3 Agent 部署

- 将 desired state 指回上一个已批准 Artifact Digest；
- 先撤销新 Digest，再执行 Agent rollback；
- Adapter 不支持原子回滚时标记 `degraded/manual_action_required`，不得显示成功；
- Agent 离线时保存高优先级撤销意图，重连后在普通任务前执行。

### 6.4 结算

- 未冻结周期：重新计算草稿并留下版本差异；
- 已冻结周期：禁止改写，创建下一周期 adjustment；
- 已提交支付：先查询 Provider 最终态；不得仅因本地超时重新发起；
- 错付或退款：使用冲正、准备金或负余额规则，不删除原交易。

## 7. 事故级停止条件

以下任一情况立即冻结相关能力并启动事故流程：

- 已确认或疑似跨租户数据访问；
- 已撤销/高危 Digest 获得 Allow 或继续运行；
- Webhook/Agent 身份被伪造且影响状态；
- Journal 不平、重复 payout、Statement 与 Provider 无法解释地不一致；
- 生产付款开关被绕过；
- 扫描器执行了未授权仓库代码或发生沙箱逃逸；
- 关键签名密钥、支付凭据或 GitHub App 私钥疑似泄露。

## 8. 已知限制

- MVP 仅支持 GitHub；GitLab、Bitbucket 和离线镜像不在首期承诺内；
- 静态扫描只能给出风险证据，不能证明 Skill 安全；
- 仅对通过 conformance 的 Adapter 声明 Enforced，其余仅为 Observed/Unsupported；
- 可视化是事实投影，可能有短暂延迟，详情页显示数据时间；
- 首期采用人工/受邀开发者准入，不提供开放市场排名；
- 生产付款、跨境税务、资金托管、发票代开与多司法辖区规则不属于已批准 MVP；
- 收益分成参数是商业假设，须经财务、法务和开发者访谈后定版；
- v0.4 已有账户、个人创作/发布、免费/收费沙箱、评价、排行、结算防重和后台自动发现代码；邮箱验证/找回密码、防滥用、市场审核、真实支付、独立渗透测试、生产恢复/容量与 SLO 仍缺外部环境证据，因此不构成 GA 或 C4 已批准证明。

## 9. C4/C5 批准包

### C4 发布批准

必须包含：构建清单、测试报告、未关闭缺陷、威胁模型差异、迁移/回滚证据、SLO 仪表盘、支持排班、已知限制和 Go/No-Go 记录。

企业试点包额外包含：数据驻留/处理地点矩阵、分包商与数据类别、客户管理密钥选项及限制、数据删除/导出证明、Support access 模式、漏洞披露与事故通知流程、SSO/身份边界、可访问性状态和未通过项。没有完成的项目标记 Planned/Not supported，不用问卷中的“是”替代证据。

### C5 生产付款批准

必须额外包含：公司主体与资金流法律意见、服务地区、KYC/KYB/制裁筛查、税务与发票责任、Provider 合同、收费/退款/拒付责任、准备金政策、对账证明、双人审批与小额灰度方案。
