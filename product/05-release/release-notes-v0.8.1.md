# SKillSentra for Chatgpt v0.8.1

发布日期：2026-10-24。公开源码补丁版，包含 YAML 多行说明读取修复。

## 修复内容

- 完整读取 `SKILL.md` 中 `description: >` 和 `description: |` 的多行文本，不再只读取 `>` 或 `|`。
- 支持末尾换行控制、缩进指示、带引号的多行文本、中文、UTF-8 BOM 和 CRLF；说明中的 `---` 不再造成截断。
- 使用固定版本 `PyYAML==6.0.3` 的安全加载器，保留文本型名称/版本字段；无效 YAML 和不安全标签不会被当作部分有效元数据。
- 后端、插件清单、容器本地镜像标识和下载包版本统一为 0.8.1。源码包包含 `requirements.txt`，SBOM 列出运行依赖。

## 用户如何更新

1. 从本发布下载 `skillsentra-for-chatgpt-0.8.1.zip`，解压到新目录，保留并沿用原有数据路径和配置。
2. 在实际运行 SkillSentra 后端的同一个 Python 中执行：

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. 按原部署方式重启后端。使用 Docker 的部署者重新构建后端镜像，沿用原数据卷。
4. 重新扫描或导入相关 Skill，确认完整说明已读取。已发布的历史目录记录不会被自动批量改写。

普通用户若连接他人提供的远程服务，应由服务提供方完成后端更新。独立 MCP 网关仍只依赖标准库；本次新增依赖属于后端服务。

## 下载资产与验证

- `skillsentra-for-chatgpt-0.8.1.zip`：源码下载包。
- `build-manifest.json`：精确提交、文件清单和 SHA-256 摘要。
- `sbom.cdx.json`：依赖清单。
- `skillsentra-for-chatgpt-0.8.1-unsigned-build-metadata.intoto.jsonl`：明确未签名的构建元数据，不是认证或签名证明。

详见[版本验证报告](../../evidence/validation-report-v0.8.1.md)、[版本测试计划](../04-quality/test-plan-v0.8.1.md)及[YAML 修复执行记录](../../evidence/validation-yaml-description-20261024.md)。发布流水线从精确标签重新执行测试、构建和源码摘要核验，失败时不发布下载资产。v0.8.0 历史标签和资产保留。

## 范围

用户本次授权更新 GitHub 下载包；没有更改 AI Devloop 全局配置、用户数据库或生产部署。AI Devloop 原生工程门 `BLOCKED / CHANGE_CHAIN_CONTEXT_MISMATCH` 记录保留，按用户已授权的独立候选流程交付。此公开源码发行不宣称公网 MCP 已部署、ChatGPT Directory 已批准或真实用户效果已验证。
