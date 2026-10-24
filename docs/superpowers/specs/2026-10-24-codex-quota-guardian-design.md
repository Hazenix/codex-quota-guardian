# Codex Quota Guardian 设计

日期：2026-10-24
状态：待用户批准

## 1. 目标

为 macOS 上的 Codex Desktop App 和 Codex CLI 提供同一套额度降级机制：

1. 每 5 分钟读取一次 Codex 周额度。
2. 周额度确实耗尽时，只询问一次是否切换到 DeepSeek。
3. 用户同意后，安全地修改共享的 `~/.codex/config.toml`，让 Desktop App 和 CLI 都改用 DeepSeek。
4. GPT 周额度恢复后，自动还原 GPT 配置。
5. Desktop App 正在运行时不强制重启；先询问用户，避免中断正在执行的任务。
6. DeepSeek API Key 只保存在 macOS Keychain，不写入配置、日志或项目文件。

## 2. 事实与约束

- Codex Desktop App 与 CLI 共享 `~/.codex/config.toml`，因此必须处理共享配置，单纯的 CLI profile 不能覆盖 App 场景。
- Codex 自定义 provider 使用 Responses API；DeepSeek 已提供兼容 Codex 的 Responses API。
- `codex app-server --stdio` 提供 `account/rateLimits/read`，读取额度不需要启动模型推理。
- 轮询过程只发送 `initialize` 和 `account/rateLimits/read`，不发送 `thread/start`、`turn/start` 或模型请求，因此不消耗 Codex 模型额度。它仍会产生很小的本机 CPU、网络和账户状态查询开销。
- 单独启动的 app-server 进程无法可靠获知另一个 Desktop App 进程里是否有活跃任务。因此恢复时不能把“没有检测到活跃 thread”当成可以强制退出 App 的证据。
- 第一版仅支持 macOS；不覆盖 ChatGPT Work Cloud 或远程 Codex 云任务。

## 3. 方案选择

采用“独立守护工具 + 共享配置 + CLI 包装器”：

- Python 3.9 标准库实现 `codex-quota-guardian`，避免增加运行时依赖。
- macOS LaunchAgent 每 300 秒执行一次一次性检查，不常驻占用资源。
- App 和 CLI 都通过共享配置得到同一 provider 状态。
- `codex-auto` 包装器在启动 CLI 前主动检查一次，然后把全部参数原样交给真实 Codex CLI。
- App 切换或恢复后需要重启才能稳定加载新配置；所有重启都遵循安全恢复策略。

未采用的方案：

- 仅用 Codex CLI profiles：无法覆盖 Desktop App。
- Codex Skill、插件或自动化：额度耗尽时仍依赖模型执行，不适合作为故障切换控制面；定时自动化本身也可能消耗额度。
- 检测到额度恢复后直接杀掉 App：可能中断用户任务，不符合安全恢复要求。
- 引入 LiteLLM 等代理：DeepSeek 已直接支持 Responses API，额外代理只会增加故障点和维护成本。

## 4. 组件

### 4.1 `quota_reader.py`

一次检查执行以下协议：

1. 启动已解析到绝对路径的 `codex app-server --stdio`。
2. 发送 `initialize`。
3. 发送 `account/rateLimits/read`。
4. 解析响应后主动退出子进程。

解析规则：

- 优先读取 `rateLimitsByLimitId["codex"]`。
- 只把 `windowDurationMins == 10080` 的窗口认作周额度。
- 周窗口 `usedPercent >= 100` 时认定为耗尽。
- `rateLimitReachedType` 或 `ordinaryUsageAllowed == false` 只能作为辅助证据，不能在找不到周窗口时触发切换。
- 五小时窗口耗尽、网络错误、超时、JSON-RPC 异常、接口字段变化均为 fail closed：记录错误但不切换 provider。

### 4.2 `state_machine.py`

状态保存在：

`~/Library/Application Support/CodexQuotaGuardian/state.json`

状态值：

- `gpt`：使用 GPT。
- `deepseek_prompted_declined`：本周额度窗口内已经拒绝切换，不再每 5 分钟重复询问。
- `deepseek_active`：已切换 DeepSeek。
- `gpt_restored_restart_pending`：配置已恢复 GPT，但正在等待 App 安全重启或自然退出。

状态同时记录当前周窗口的 `resetsAt`、受管配置哈希、原配置备份位置和一次性通知标记。状态目录权限为 `0700`，文件权限为 `0600`，写入采用临时文件加原子替换。

### 4.3 `config_manager.py`

切换前备份当前 `~/.codex/config.toml`，然后只修改以下受管项：

- 顶层 `model`
- 顶层 `model_provider`
- `model_catalog_json`
- `[model_providers.deepseek]`
- DeepSeek provider 所需的认证命令

DeepSeek 默认模型为 `deepseek-flash`，允许安装时或命令行改为 `deepseek-v4-pro`。

认证通过 Codex provider 的 `auth.command` 调用本地 Keychain helper 获取 bearer token。配置内不保存明文 API Key，也不使用 `experimental_bearer_token` 写入真实密钥。

工具不把 `forced_login_method` 改成 `api`：DeepSeek 认证由 provider 自己的 `auth.command` 完成，同时保留原有 ChatGPT 登录状态，确保切到 DeepSeek 后仍能继续读取 GPT 周额度并判断何时恢复。

配置编辑器按 TOML section 处理，不对整个文件重新序列化，以保留用户已有的 MCP、projects、trust、desktop 和其他设置。若发现同名 `[model_providers.deepseek]` 不是本工具管理的配置，则停止并提示冲突，不覆盖用户设置。

每次替换前必须完成：

1. 临时文件权限检查。
2. 受管字段结构检查。
3. 模型目录 JSON 解析检查。
4. Codex 严格配置加载检查。

全部通过后才原子替换活动配置。

### 4.4 `keychain_helper.py`

- 安装时在本机终端使用隐藏输入读取 DeepSeek API Key。
- 使用 `/usr/bin/security add-generic-password` 保存到 macOS Keychain。
- 运行时只把 Keychain 返回值写到 Codex 认证命令的 stdout。
- 日志、异常和诊断输出永不包含 key、完整配置内容或账户 ID。
- 缺少 Keychain 项时停止切换，并给出本地补录命令；不在聊天中索要密钥。

### 4.5 `notifier.py`

使用 `osascript display dialog` 提供两类确认：

1. 周额度耗尽：`周额度已用完，是否切换到 DeepSeek 继续？`
   - `暂不切换`
   - `切换 DeepSeek`
2. 周额度恢复且 App 正在运行：`GPT 额度已恢复，配置已切回；是否立即重启 App？`
   - `稍后`
   - `立即重启`

无图形会话时，LaunchAgent 不阻塞等待输入，而是记录待决状态并发送一次通知；下次运行 `codex-auto` 或手动命令时在终端询问。

### 4.6 LaunchAgent

LaunchAgent 使用 `StartInterval = 300` 调用：

`codex-quota-guardian check --non-interactive`

每次运行只做一次检查后退出。标准输出和错误输出写入用户日志目录；日志轮转由工具自身控制，且不记录敏感字段。

### 4.7 CLI 包装器

安装 `codex-auto`：

1. 先执行一次交互式额度检查。
2. 依据共享配置选择当前 provider。
3. 使用安装时记录的 Codex CLI 绝对路径 `exec`，原样传递全部参数和退出码。

包装器不替换系统 `codex`，避免 PATH 递归和破坏现有使用方式。用户可以逐步改用 `codex-auto`，也可以设置自己的 shell alias。

## 5. 状态转换

### 5.1 GPT 周额度耗尽

1. 读到明确的周窗口耗尽。
2. 若本 `resetsAt` 窗口已拒绝过，则静默退出。
3. 询问是否切换。
4. 拒绝：进入 `deepseek_prompted_declined`。
5. 同意：检查 Keychain、备份配置、生成并验证 DeepSeek 配置、原子替换，进入 `deepseek_active`。
6. App 正在运行时，切换确认本身同时构成重启授权；工具礼貌退出并重新打开 App。CLI 下次启动直接使用 DeepSeek。

### 5.2 GPT 周额度恢复

1. 原额度窗口已重置，且新周窗口 `usedPercent < 100`。
2. 核对当前 DeepSeek 配置哈希是否仍是本工具生成的版本。
3. 哈希一致：原子恢复原 GPT 路由配置。
4. App 未运行：进入 `gpt`；下次启动使用 GPT。
5. App 正在运行：显示安全恢复对话框。
   - 立即重启：礼貌退出并重新打开，进入 `gpt`。
   - 稍后：进入 `gpt_restored_restart_pending`，不每 5 分钟重复弹窗。App 自然退出后，下次启动自动使用 GPT。

### 5.3 配置冲突

如果用户在 `deepseek_active` 期间修改了活动配置，导致哈希不一致：

- 不覆盖用户改动。
- 生成一份不含密钥的建议恢复文件。
- 记录冲突并提示用户执行 `codex-quota-guardian status`。
- 保留备份，等待人工处理。

这是“自动恢复”唯一允许暂停的安全边界，目的是避免把用户后续配置更改静默抹掉。

## 6. 用户命令

- `codex-quota-guardian check`：立即检查并允许交互。
- `codex-quota-guardian status`：显示当前 provider、周额度摘要、下一次 reset、App 重启待决状态。
- `codex-quota-guardian switch-deepseek`：人工触发切换。
- `codex-quota-guardian restore-gpt`：人工恢复 GPT。
- `codex-quota-guardian restart-app`：在用户明确调用时礼貌重启 App。
- `codex-quota-guardian install`：安装 Keychain 项、CLI 包装器和 LaunchAgent。
- `codex-quota-guardian uninstall`：优先安全恢复 GPT，卸载 LaunchAgent 和包装器；默认保留备份和状态，除非用户显式要求清除。

## 7. 文件布局

项目仓库：

```text
codex-quota-guardian/
├── pyproject.toml
├── README.md
├── src/codex_quota_guardian/
├── tests/
├── resources/com.hazehacker.codex-quota-guardian.plist
└── docs/superpowers/specs/
```

安装后的用户文件：

```text
~/Library/Application Support/CodexQuotaGuardian/
├── state.json
├── install.json
├── models.json
└── backups/
~/Library/LaunchAgents/com.hazehacker.codex-quota-guardian.plist
~/Library/Logs/codex-quota-guardian.log
~/.local/bin/codex-quota-guardian
~/.local/bin/codex-auto
```

## 8. 验证与测试

自动测试使用标准库 `unittest`：

- 精确识别 10080 分钟周窗口，不把五小时窗口当成周额度。
- 覆盖耗尽、恢复、字段缺失、超时、通知穿插和 JSON-RPC 错误。
- 覆盖“拒绝后本周不再弹窗”和新 reset 窗口重新允许询问。
- 覆盖四个状态及全部合法/非法转换。
- 覆盖保留无关 TOML 设置、已有 `[desktop]`、外部 DeepSeek provider 冲突。
- 覆盖原子备份、精确恢复、哈希冲突不覆盖用户修改。
- 覆盖 Keychain 错误脱敏。
- 覆盖 CLI 参数/退出码透传和防递归。
- 使用临时 HOME 做安装 dry-run，不触碰真实 `~/.codex`。
- 用 `plutil -lint` 验证 LaunchAgent plist。

本机验收分两阶段：

1. 无副作用验收：真实读取额度、临时目录内模拟切换/恢复、检查日志无敏感信息。
2. 真实安装验收：需要用户批准写入 `~/Library`、`~/.local/bin`、Keychain 和 `~/.codex/config.toml`。实际调用 DeepSeek 会产生其 API 费用，只有用户再次明确授权后才执行。

## 9. 验收标准

- 定时读取额度不发起模型请求。
- 只有明确的周额度耗尽才弹出切换询问。
- 同一周额度窗口拒绝后不重复打扰。
- App 和 CLI 在同一时间使用同一 provider。
- API Key 不出现在配置、日志、Git diff 或进程参数中。
- 切换前一定有可恢复备份；写配置失败时活动配置保持不变。
- 额度恢复后配置自动回到 GPT。
- App 运行时不会未经确认被强制重启。
- 用户修改过活动配置时不会被自动恢复覆盖。

## 10. 非目标

- 不保证正在进行中的同一条对话在 GPT 与 DeepSeek 之间无缝续接。
- 不因临时网络错误、普通 HTTP 429 或五小时额度窗口自动切换。
- 不自动购买或充值任何服务。
- 不接管原始 `codex` 命令，不劫持系统 PATH。
- 第一版不支持 Windows、Linux 或云端 Codex 任务。

## 11. Builder 自检结论

- 输入、输出、状态持久化、冲突处理和错误边界均已定义，可以按测试先行拆分实现。
- 最关键的不确定性是 Codex 配置加载验证的具体命令；实现阶段先探测当前版本支持的只读校验入口。如果没有独立校验命令，则用隔离的临时 `CODEX_HOME` 启动 app-server 并完成 `initialize` 作为验收，不触碰真实配置。
- 无需在实现前补充新的产品选择；默认使用 `deepseek-flash`，同时保留安装参数切换 `deepseek-v4-pro`。
- 实现自检发现官方 DeepSeek 示例会设置 `forced_login_method = "api"`，这会让恢复监控失去 ChatGPT 账户额度来源；本工具改用官方 Codex 支持的 provider `auth.command`，保留原 ChatGPT 登录方式。
