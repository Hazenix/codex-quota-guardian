# Codex Quota Guardian 实现计划

> 设计依据：`docs/superpowers/specs/2026-10-24-codex-quota-guardian-design.md`

**目标：** 实现一个仅依赖 Python 3.9 标准库的 macOS 工具，安全监控 Codex 周额度，在用户确认后切换 DeepSeek，并在额度恢复后切回 GPT。

**架构：** 命令行入口负责编排；额度读取、状态机、配置编辑、Keychain、通知和安装分别作为低耦合模块。所有真实用户目录都通过显式路径依赖注入，测试使用临时 HOME。LaunchAgent 每次启动只检查一次，不常驻。

**技术栈：** Python 3.9、`unittest`、JSON-RPC over stdio、macOS `security` / `osascript` / `launchctl` / `plutil`。

---

## Task 1：项目骨架与领域模型

**文件：**

- 新建 `pyproject.toml`
- 新建 `src/codex_quota_guardian/__init__.py`
- 新建 `src/codex_quota_guardian/models.py`
- 新建 `tests/test_models.py`

**步骤：**

1. 先写周额度快照、持久化状态、状态枚举的失败测试。
2. 运行 `python3 -m unittest tests.test_models -v`，确认测试因模块缺失而失败。
3. 实现最小数据模型和严格的 JSON 序列化/反序列化。
4. 重跑测试并确认通过。

## Task 2：周额度解析器

**文件：**

- 新建 `src/codex_quota_guardian/quota.py`
- 新建 `tests/test_quota.py`

**步骤：**

1. 编写覆盖 10080 分钟周窗口、五小时窗口、字段缺失、耗尽和恢复的测试。
2. 实现 `parse_weekly_quota`，优先解析 `rateLimitsByLimitId["codex"]`。
3. 对未知响应 fail closed，返回不可判定结果而不是切换信号。
4. 运行 `python3 -m unittest tests.test_quota -v`。

## Task 3：App Server JSON-RPC 客户端

**文件：**

- 新建 `src/codex_quota_guardian/app_server.py`
- 新建 `tests/test_app_server.py`

**步骤：**

1. 用假的子进程流测试请求 ID 匹配、通知穿插、EOF、超时和敏感字段不进入错误信息。
2. 实现 `initialize` 与 `account/rateLimits/read` 两次请求。
3. 确保客户端永不发送 `thread/start`、`turn/start`。
4. 运行 `python3 -m unittest tests.test_app_server -v`。

## Task 4：状态仓库与状态转换

**文件：**

- 新建 `src/codex_quota_guardian/state.py`
- 新建 `tests/test_state.py`

**步骤：**

1. 测试缺省状态、原子写入、文件权限、拒绝提示去重和 reset 窗口变化。
2. 实现 `StateStore` 和纯函数状态转换。
3. 测试非法或损坏 state 时不触发危险切换。
4. 运行 `python3 -m unittest tests.test_state -v`。

## Task 5：配置切换与安全恢复

**文件：**

- 新建 `src/codex_quota_guardian/config.py`
- 新建 `tests/fixtures/config_minimal.toml`
- 新建 `tests/fixtures/config_full.toml`
- 新建 `tests/test_config.py`

**步骤：**

1. 测试保留无关配置、替换顶层路由键、生成受管 provider、外部 provider 冲突。
2. 测试备份、原子替换、精确恢复和哈希冲突拒绝覆盖。
3. 实现 section-aware 文本编辑器，不整体重排 TOML。
4. 实现配置哈希和权限设置。
5. 运行 `python3 -m unittest tests.test_config -v`。

## Task 6：模型目录与 Keychain 认证

**文件：**

- 新建 `resources/models.json`
- 新建 `src/codex_quota_guardian/keychain.py`
- 新建 `tests/test_keychain.py`
- 新建 `tests/test_resources.py`

**步骤：**

1. 写 DeepSeek 模型目录结构测试，并验证 `deepseek-flash` 与 `deepseek-v4-pro`。
2. 写 Keychain 命令参数和错误脱敏测试。
3. 实现隐藏输入存储、只输出 token 的读取模式；任何异常都不拼接 stdout/stderr 中的密钥。
4. 运行相关测试。

## Task 7：通知、App 生命周期与编排器

**文件：**

- 新建 `src/codex_quota_guardian/notifier.py`
- 新建 `src/codex_quota_guardian/guardian.py`
- 新建 `tests/test_notifier.py`
- 新建 `tests/test_guardian.py`

**步骤：**

1. 用 fake 依赖测试额度耗尽同意/拒绝、额度恢复、App 运行/未运行、配置冲突。
2. 实现 `osascript` 对话框、App 运行检测、礼貌退出与重新打开。
3. 实现一次检查的编排，不在非交互 launchd 运行中无限等待。
4. 运行相关测试。

## Task 8：CLI 与包装器

**文件：**

- 新建 `src/codex_quota_guardian/cli.py`
- 新建 `src/codex_quota_guardian/__main__.py`
- 新建 `tests/test_cli.py`

**步骤：**

1. 测试 `check`、`status`、`switch-deepseek`、`restore-gpt`、`restart-app` 的退出码。
2. 测试包装器生成内容使用 Codex 绝对路径、参数原样透传且不递归。
3. 实现命令解析和清晰的中文诊断。
4. 运行 `python3 -m unittest tests.test_cli -v`。

## Task 9：安装与卸载

**文件：**

- 新建 `src/codex_quota_guardian/installer.py`
- 新建 `resources/com.hazehacker.codex-quota-guardian.plist`
- 新建 `tests/test_installer.py`

**步骤：**

1. 在临时 HOME 中测试安装布局、权限、plist 替换、包装器和幂等性。
2. 测试卸载默认保留状态/备份，且在无法安全恢复时拒绝删除运行部件。
3. 实现 `--dry-run`，默认只展示计划；真实安装必须显式确认。
4. 用 `plutil -lint` 检查生成的 plist。

## Task 10：文档与端到端验证

**文件：**

- 新建 `README.md`
- 新建 `.gitignore`

**步骤：**

1. 记录安装、Keychain、App/CLI 用法、安全恢复、日志和卸载流程。
2. 明确“额度轮询不发起模型请求”和“真实 DeepSeek 请求会产生 DeepSeek API 费用”。
3. 运行完整测试：`python3 -m unittest discover -s tests -t . -v`。
4. 运行语法检查：`python3 -m compileall -q src tests`。
5. 使用临时 HOME 执行安装 dry-run 和模拟切换/恢复。
6. 使用真实 app-server 只读执行一次额度检查，核对未发出模型调用。
7. 检查项目内无密钥模式：`rg -n 'sk-[A-Za-z0-9_-]{10,}|api[_-]?key\s*=' .`。
8. 记录所有通过项和未执行的真实安装/真实 DeepSeek 计费请求。

## 实施边界

- 在代码和测试完成前，不写入 `~/.codex`、`~/Library`、Keychain 或 `~/.local/bin`。
- 真实安装是后续独立步骤，需用户确认系统目录写入。
- 真实 DeepSeek API 调用会计费，需用户再次明确授权。
- 当前目录不是 Git 仓库，因此本计划不包含 commit 或 push。
