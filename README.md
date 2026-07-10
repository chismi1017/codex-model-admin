# 🤖 Codex Model Admin

<img width="1694" height="929" alt="企业微信截图_17835929488680" src="https://github.com/user-attachments/assets/cb2b1507-be98-4d37-bf4b-59ebf5024bec" />

`codex-model-admin` 是一个面向 Codex CLI 和 cc-switch CLI 的轻量级 CLI/TUI 管理工具，用于在服务器上统一管理第三方 OpenAI-compatible 模型供应商、模型 catalog、Codex 配置、cc-switch 代理和配置备份。

项目面向 Linux 服务器环境，Ubuntu/Debian 等其他 Linux 可尝试使用（未验证），适合通过 SSH 管理远程 Codex CLI 环境。

## ✨ 功能特性

- 环境检查：检查 `codex`、`cc-switch`、`node`、`systemd`、`curl`、`tar` 等依赖。
- 组件安装：可安装缺失的 Codex CLI 或 cc-switch CLI。
- 供应商管理：列出、新增、修改、切换、删除 provider。
- 官方供应商保护：`codex-official / OpenAI Official` 作为系统只读项展示，不允许误切换或删除。
- 模型管理：列出、新增、删除、设置默认模型。
- 模型拉取：从 provider `/v1/models` 拉取模型列表，支持筛选、多选和批量新增。
- 健康检测：新增模型、默认模型和模型列表支持健康检测。
- 代理管理：查看、设置、重启和测试 cc-switch Codex proxy。
- 备份恢复：写入前自动备份，支持手动创建、查看、恢复和删除备份。
- 服务器迁移：通过免密 SSH 一次完成代码安装、依赖安装和完整环境复制。
- 完全卸载：安全移除管理器、Codex CLI、cc-switch、服务、配置和迁移残留。
- TUI 交互：支持主菜单快捷键、聚焦表单、模型选择器、模型列表、模型多选删除和备份多选删除。
- 中英文界面：TUI 支持中文和英文切换。

## 📁 项目结构

```text
.
├── docs/
│   ├── codex-model-admin-requirements-design.md
│   └── codex-model-admin-usage.md
├── scripts/
│   ├── install-codex-model-admin.sh
│   ├── migrate-from-server.sh
│   └── uninstall-codex-model-admin.sh
├── src/
│   ├── cli.py
│   ├── tui.py
│   ├── operations.py
│   ├── stores.py
│   ├── backups.py
│   └── ...
└── tests/
    └── test_*.py
```

## 🚀 安装

在服务器上进入项目目录：

```bash
cd /opt/codex-model-admin
```

安装命令入口：

```bash
./scripts/install-codex-model-admin.sh
```

安装后检查：

```bash
codex-model-admin --help
codex-model-admin doctor
```

从另一台已经配置完成的服务器迁移完整环境，可在新服务器执行：

```bash
curl -fL \
  https://raw.githubusercontent.com/chismi1017/codex-model-admin/main/scripts/migrate-from-server.sh \
  -o /root/migrate-from-server.sh
chmod 700 /root/migrate-from-server.sh
/root/migrate-from-server.sh root@old-server --yes
```

脚本会从 GitHub 克隆项目、安装 Codex CLI 与 cc-switch，并通过免密 SSH 复制供应商、模型、API Key、认证、全局 `/root/.codex/AGENTS.md`、rules、skills、memory 和会话状态。源服务器名称通过参数传入，不写入仓库。

运行条件：新服务器必须是以 systemd 启动的 x86_64 Linux，并通过独立 root SSH shell 执行；源服务器必须允许 root 免密 SSH 登录并提供 GNU tar。无需登录源服务器手工停止进程：脚本会自动停止并恢复源端 systemd proxy，短暂暂停并恢复 Codex app-server、Node wrapper 和独立 cc-switch daemon/worker；目标端旧进程会在配置替换前自动停止。

源端暂停由 watchdog 保护。SSH 归档 shell 意外退出时会自动恢复进程；暂停超过 10 分钟时也会恢复进程并使本次归档失败，避免把不一致归档用于迁移。

该功能复制的是 Codex/cc-switch 的完整持久化运行环境，不是 Linux 整机镜像。二进制、日志、缓存、锁文件和旧备份不会复制；目标原配置保存在 `/root/codex-model-admin-rollback-<时间戳>`。完整参数和回滚说明参见[使用说明](docs/codex-model-admin-usage.md#15-服务器一键迁移)。

## 🧹 完全卸载

先使用 dry-run 查看删除范围，不会修改系统：

```bash
cd /root
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh --dry-run
```

确认后执行完全卸载：

```bash
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh --yes
```

默认保留可能被其他 npm 软件共用的 `/root/.npmrc`。确认它也属于迁移环境时，可执行：

```bash
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh --yes --purge-npmrc
```

卸载会永久删除 Codex 认证、API Key、供应商、模型、memory、会话、备份和迁移回滚目录。Node.js、npm、Python、Git、curl、tar、SSH 等共享系统依赖不会删除。详细范围参见[使用说明](docs/codex-model-admin-usage.md#16-完全卸载)。

## ⚡ 快速开始

启动 TUI：

```bash
codex-model-admin tui
```

临时指定界面语言：

```bash
codex-model-admin tui --lang zh
codex-model-admin tui --lang en
```

常用 CLI：

```bash
codex-model-admin provider list
codex-model-admin model list
codex-model-admin proxy status
codex-model-admin backup list
```

测试代理和模型链路：

```bash
codex-model-admin proxy test --model example-model
```

## ⌨️ TUI 快捷键

主菜单：

- `Tab` / `↑↓`：移动菜单项
- `Enter`：执行当前菜单项
- 数字：直接执行对应动作
- `q` / `Esc`：退出

表单页：

- `Tab` / `Shift+Tab` / `↑↓`：切换字段
- 文本字段：直接输入
- 选项字段：`Space` / `←→` 切换
- `Ctrl+P`：预览命令
- `Ctrl+S`：执行
- `Esc`：返回

特殊列表：

- 新增模型：`Ctrl+L` 或 `Space` 拉取模型列表，支持筛选、多选和健康检测。
- 模型列表：`Space` 检测当前模型，`Ctrl+A` 检测全部，`s` 同步当前 provider 到 Codex catalog，`d` 删除失败模型。
- 删除模型：模型 ID 字段按 `Ctrl+L` 或 `Space` 从当前 provider 已加入模型选择，支持筛选、多选和 `Ctrl+A` 全选/清空当前筛选。
- 删除备份：`Space` 多选，`Ctrl+A` 全选/清空，`Enter` 删除选中项。

## 🛠️ 典型命令

新增 provider：

```bash
codex-model-admin provider add my-provider \
  --name my-provider \
  --base-url https://example.com/v1 \
  --api-key sk-xxxx \
  --default-model my-model \
  --context-window 128000 \
  --api-format responses \
  --yes
```

修改 provider：

```bash
codex-model-admin provider update my-provider \
  --new-id my-provider-renamed \
  --name my-provider \
  --base-url https://example.com/v1 \
  --api-key sk-xxxx \
  --default-model my-model \
  --context-window 128000 \
  --api-format responses \
  --sync-current \
  --restart \
  --yes
```

新增模型：

```bash
codex-model-admin model add new-model \
  --display-name new-model \
  --context-window 128000 \
  --provider-id my-provider \
  --yes
```

设置默认模型：

```bash
codex-model-admin model set-default new-model --yes
```

同步当前 provider 到 Codex catalog：

```bash
codex-model-admin model sync-current --yes
```

删除备份：

```bash
codex-model-admin backup delete 20260706-170533 --yes
```

## 🧪 测试

运行完整测试：

```bash
cd /opt/codex-model-admin
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests
```

`PYTHONDONTWRITEBYTECODE=1` 用于避免生成 `__pycache__` 和 `.pyc` 缓存文件。

## 🔐 安全注意事项

- 不要提交 API Key、token、密码、私钥或真实用户配置。
- 不要提交 `/root/.codex/config.toml`、`/root/.cc-switch/cc-switch.db` 或备份目录。
- 写操作应保留显式确认参数 `--yes`。
- 删除 provider、model 或 backup 前建议先确认自动备份是否已生成。
- 将 proxy 监听地址设置为 `0.0.0.0` 前，请确认防火墙和访问来源限制。

## 📚 文档

- [使用说明](docs/codex-model-admin-usage.md)
- [需求与设计文档](docs/codex-model-admin-requirements-design.md)

## 📄 许可证

当前仓库未包含许可证文件。如需开源发布，请先添加明确的 `LICENSE`。
