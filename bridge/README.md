# SkillSentra Local Bridge v0.1

当前 Bridge 是可运行的 `metadata-only` 客户端骨架，只支持：

- 向 SkillSentra 服务登记本机 Bridge；
- 主动向服务端上报心跳、能力等级和非敏感 observed state；
- 查看 Host、Connector 和 Bridge 数量。

当前版本不会读取本地文件、执行命令、安装或卸载 Skill，也不会接收云端推送。非本机服务地址必须使用 HTTPS；访问令牌只从 `SKILLSENTRA_AUTH_TOKEN` 环境变量读取，不接受 URL 中的凭据。

本机开发示例：

```powershell
& python bridge/skillsentra_bridge.py register --name "My Windows Bridge" --platform windows
```

服务端启用 token、accounts 或 hybrid 鉴权后，应通过安全的进程环境提供 `SKILLSENTRA_AUTH_TOKEN`。请勿把令牌写入命令历史、配置文件、日志或截图。
