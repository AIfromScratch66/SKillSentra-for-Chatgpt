# YAML 多行 description 维护说明

日期：2026-10-24。适用：独立仓库 `SKillSentra-for-Chatgpt` 的 v0.8.1 源码发行包。用户已追加要求更新 GitHub 下载包；新增 v0.8.1 标签与下载资产，保留 v0.8.0 历史包，不部署服务。

## 问题与修复

旧实现逐行按第一个冒号拆分 frontmatter，因此把下面的 `description` 读取为 `>`，遗漏两行实际说明。现在通过 PyYAML 的安全加载器解析整个 frontmatter，说明按 YAML 语义读取完整；元数据的隐式类型转换被关闭，以保留原有名称与版本字符串。

```yaml
---
name: character-design
description: >
  创建并保持一致的角色设计。
  支持角色参考图、表情图和角色转视频。
---
```

此例的结果为 `创建并保持一致的角色设计。 支持角色参考图、表情图和角色转视频。`，末尾含一个换行。`>` 折叠相邻普通行，`|` 保留行间换行；`-` 去除末尾换行，`+` 保留末尾空行。适用规则参考 [YAML block scalar specification](https://yaml.org/spec/1.2.2/#81-block-scalar-styles)。

支持单行/带引号的说明、带引号或不带引号的多行说明、缩进指示、头部注释、中文、UTF-8 BOM 和 CRLF。只把独立成行且未缩进的 `---` 当成 frontmatter 分隔符；说明内部的 `---` 保留。嵌套字段不再被当成顶层名称或说明。

无效 YAML、不安全的对象标签和非映射根节点不产生部分有效元数据；非字符串字段不会被强制转成说明。后续扫描仍通过原有缺失名称/说明检查报告问题，不执行 Skill 中的脚本或 YAML 对象构造指令。此改动不宣称支持任意自定义 YAML 标签。

## 安装和运行

后端新增 `requirements.txt` 中固定的 `PyYAML==6.0.3`。使用启动和测试所用的同一个 Python 安装：

```powershell
$env:SKILLSENTRA_PYTHON = (Get-Command python).Source
& $env:SKILLSENTRA_PYTHON -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
```

如果使用 Codex 随附 Python，改用该解释器的完整路径设置 `SKILLSENTRA_PYTHON`。后端 Dockerfile 与 GitHub CI 安装同一固定依赖。独立 MCP 网关容器仍只使用标准库；它调用后端服务，无需在网关内安装 PyYAML。源码 ZIP 包含依赖文件，SBOM 列出 PyYAML。

现有源文件、冻结基线和制品摘要不被重写。已有来源再次扫描/注册时会重读其说明；已发布目录记录仍保留原内容，本次不批量改写既有发布数据。

## 验证和回退

测试范围见[测试计划](../04-quality/test-plan-yaml-description.md)，实际结果见[验证报告](../../evidence/validation-yaml-description-20261024.md)。本次只针对已授权的公开源码仓库提交和推送；不更新其他 SkillSentra 副本、AI Devloop 配置、历史任务、用户数据库或生产服务。

本地执行结果：24 项针对性测试、完整后端复测 259 项、浏览器与前端契约 22 项全部通过。干净提交的 107 文件源码包通过清单/摘要校验；在全新目录重新安装包内固定依赖后，解析、注册、扫描和目录摘要均保留完整中文说明。首次完整测试中的一次既有 HTTP 并发连接中断及复测过程保留在报告中。本机 Docker 引擎未启动，未记录本机容器运行通过。

需要回退时，对本次修复提交执行常规 `git revert`，并从回退后的源重新构建；不要修改历史标签或覆盖用户数据。AI Devloop 原生工程门因历史任务绑定不一致保持 `BLOCKED`；用户已明确授权独立候选继续，测试结果不将该门改记为通过。
