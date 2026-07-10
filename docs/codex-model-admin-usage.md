# Codex Model Admin 使用说明

`codex-model-admin` 是一个面向 Codex CLI + cc-switch CLI 的管理工具，用于在服务器上统一管理第三方模型供应商、模型列表、Codex 配置、cc-switch 代理和配置备份。

本文档基于当前已完成代码编写，面向 Linux 服务器环境；Ubuntu/Debian 等其他 Linux 可尝试使用（未验证），默认部署目录为 `/opt/codex-model-admin`，默认入口命令为 `/usr/local/bin/codex-model-admin`。

## 1. 适用场景

本工具适用于以下场景：

- 在远程 Linux 服务器上使用 Codex CLI。
- 通过 cc-switch CLI 作为中间层接入第三方 OpenAI-compatible 模型服务。
- 需要维护多个 provider 和多个模型。
- 需要让 Codex CLI 使用自定义模型 catalog。
- 需要管理 cc-switch proxy 的监听地址、端口和 systemd 服务。
- 需要在每次写配置前自动备份，方便回滚。

## 2. 关键文件和默认路径

| 类型 | 默认路径 | 说明 |
|---|---|---|
| 项目目录 | `/opt/codex-model-admin` | 工具源码、测试和脚本 |
| 命令入口 | `/usr/local/bin/codex-model-admin` | 全局命令 wrapper |
| Codex 配置 | `/root/.codex/config.toml` | Codex CLI 主配置 |
| Codex 模型 catalog | `/root/.codex/cc-switch-model-catalog.json` | Codex 自定义模型列表 |
| cc-switch 数据库 | `/root/.cc-switch/cc-switch.db` | provider 和模型配置 |
| 代理 systemd 服务 | `/etc/systemd/system/cc-switch-codex-proxy.service` | cc-switch Codex proxy 服务 |
| 备份目录 | `/root/.codex/model-admin-backups` | 自动/手动备份 |

## 3. 安装和部署

如果项目已经部署到 `/opt/codex-model-admin`，执行：

```bash
cd /opt/codex-model-admin
./scripts/install-codex-model-admin.sh
```

安装后可直接运行：

```bash
codex-model-admin --help
```

检查运行环境：

```bash
codex-model-admin doctor
```

自动安装缺失组件：

```bash
codex-model-admin install all --yes
```

也可以只安装某一个组件：

```bash
codex-model-admin install codex --yes
codex-model-admin install cc-switch --yes
```

## 4. 快速开始

查看当前 provider：

```bash
codex-model-admin provider list
```

查看当前 Codex 可见模型：

```bash
codex-model-admin model list
```

查看代理状态：

```bash
codex-model-admin proxy status
```

测试代理和模型链路：

```bash
codex-model-admin proxy test --model example-model
```

启动中文 TUI：

```bash
codex-model-admin tui
```

## 5. TUI 交互界面

执行：

```bash
codex-model-admin tui
```

临时指定界面语言：

```bash
codex-model-admin tui --lang zh
codex-model-admin tui --lang en
```

TUI 会显示类似 Codex CLI 风格的界面，按功能分组：

- 环境 / 安装
- 供应商
- 模型
- 代理
- 备份
- 设置

主菜单支持以下快捷键：

- `Tab` / `↑↓`：移动当前菜单项
- `Enter`：执行当前菜单项
- 数字：直接跳转并执行对应动作
- `q` / `Esc`：退出

表单页支持以下快捷键：

- `Tab` / `Shift+Tab` / `↑↓`：切换字段
- 普通文本字段：直接输入内容
- 选项字段：`Space` / `←→` 切换选项
- `Ctrl+P`：预览即将执行的命令
- `Ctrl+S`：执行
- `Esc`：返回

写入类操作会要求输入 `yes` 二次确认，并在写入前自动创建备份。

当前菜单编号如下：

| 编号 | 功能 |
|---|---|
| 1-2 | 环境检查、安装缺失组件 |
| 3-7 | 供应商列表、新增、修改、切换、删除 |
| 8-11 | 模型列表、新增、设置默认、删除 |
| 12-16 | 代理状态、设置、重启、日志、测试 |
| 17-20 | 创建备份、备份列表、恢复备份、删除备份 |
| 21 | 界面语言 |

第 9 项“新增模型”会自动读取当前选中的 provider 作为默认供应商 ID。提示 `供应商 ID [当前provider]` 时直接回车，即表示把模型添加到当前 provider。

第 21 项“界面语言 (Language) / Language (界面语言)”用于在中文和英文之间切换。通过第 21 项切换会保存到：

```bash
/root/.config/codex-model-admin/settings.json
```

保存后下次执行 `codex-model-admin tui` 会自动使用上次选择的语言。`--lang zh|en` 只影响本次启动，不会改写设置文件。

## 6. Provider 管理

### 6.1 查看 provider

```bash
codex-model-admin provider list
```

输出包含：

- 当前 provider 标记
- provider ID
- provider 名称
- Base URL
- 模型数量
- 默认模型
- `codex-official / OpenAI Official` 只作为官方内置只读项展示，模型数量为动态，不能通过本工具切换或删除

### 6.2 新增 provider

```bash
codex-model-admin provider add example-provider \
  --name example-provider \
  --base-url https://api.example.com/v1 \
  --api-key sk-xxxx \
  --default-model example-model \
  --context-window 200000 \
  --api-format responses \
  --yes
```

参数说明：

| 参数 | 说明 |
|---|---|
| `provider_id` | provider 唯一 ID |
| `--name` | provider 显示名称 |
| `--base-url` | 上游模型服务地址，建议填 `/v1` 根路径 |
| `--api-key` | 上游服务 API Key |
| `--default-model` | 初始默认模型 |
| `--context-window` | 上下文窗口 |
| `--api-format` | `responses` 或 `chat` |
| `--switch` | 新增后立即切换到该 provider |
| `--yes` | 确认执行写操作 |

新增并立即切换：

```bash
codex-model-admin provider add my-provider \
  --name my-provider \
  --base-url http://example.com/v1 \
  --api-key sk-xxxx \
  --default-model my-model \
  --context-window 128000 \
  --switch \
  --yes
```

在 TUI 中新增 provider 时，填写 `Base URL` 和 `API Key` 后，可在“默认模型”字段按 `Ctrl+L` 或 `Space` 从当前 Base URL 拉取模型列表。选中模型后会自动执行健康检测；健康检测通过后才能作为默认模型执行新增。

### 6.3 修改 provider

```bash
codex-model-admin provider update my-provider \
  --new-id my-provider-renamed \
  --name my-provider \
  --base-url http://example.com/v1 \
  --api-key sk-xxxx \
  --default-model my-model \
  --context-window 128000 \
  --api-format responses \
  --sync-current \
  --restart \
  --yes
```

修改 provider 用于更新已存在供应商的 ID、名称、Base URL、API Key、默认模型、上下文窗口和 API 格式。`--new-id` 用于把当前 provider ID 改成新 ID；不需要改 ID 时可省略。`--sync-current` 表示如果修改的是当前 provider，则同步 Codex catalog 和默认模型；`--restart` 表示修改后重启代理。

在 TUI 中使用“修改供应商”时，供应商 ID 字段可从已有可管理供应商中切换，切换后会自动带出该供应商现有配置；新供应商 ID 字段用于修改 ID。

### 6.4 切换 provider

```bash
codex-model-admin provider switch example-provider --yes
```

切换 provider 会做三件事：

1. 更新 cc-switch 当前 provider。
2. 根据 provider 的 `modelCatalog` 重写 Codex catalog 文件。
3. 根据 provider 配置更新 Codex 默认模型。

如果不希望自动重启代理：

```bash
codex-model-admin provider switch example-provider --yes --no-restart
```

在 TUI 中使用“切换供应商”时，供应商 ID 字段是选项字段，可用 `Space` / `←→` 在可管理供应商之间切换。官方内置只读供应商不会出现在可切换列表中。

### 6.5 删除 provider

```bash
codex-model-admin provider delete my-provider --yes
```

删除当前 provider 需要强制参数：

```bash
codex-model-admin provider delete my-provider --force --yes
```

`codex-official / OpenAI Official` 是系统只读项，不允许通过本工具删除。

## 7. Model 管理

### 7.1 查看模型

```bash
codex-model-admin model list
```

在 TUI 中进入“模型列表”后，会显示已加入 Codex catalog 的模型、显示名称、上下文窗口和健康状态。快捷键：

- `↑↓` / `Tab`：移动高亮行
- `Enter`：查看当前模型详情
- `Space`：检测当前模型健康
- `Ctrl+A`：检测全部模型健康
- `s`：将当前 provider 的 `modelCatalog` 同步到 Codex catalog
- `d`：直接删除健康检测失败的模型
- `Esc` / `q`：返回

如果当前 provider 的模型数和 Codex catalog 模型数不一致，模型列表会显示同步提示。也可以在命令行显式同步：

```bash
codex-model-admin model sync-current --yes
```

### 7.2 新增模型

```bash
codex-model-admin model add example-model-fast \
  --display-name example-model-fast \
  --context-window 128000 \
  --provider-id example-provider \
  --yes
```

新增模型会同时更新：

- cc-switch provider 的 `modelCatalog`
- Codex 的 `cc-switch-model-catalog.json`

在 TUI 中使用“新增模型”时，供应商 ID 默认取当前选中的 provider；命令行模式建议通过 `--provider-id` 明确指定目标 provider，避免依赖环境默认值。

在 TUI 的“模型 ID”字段按 `Ctrl+L` 或 `Space`，可以从当前供应商 `/v1/models` 拉取模型列表并选择：

- 输入文字可模糊筛选
- `↑↓` / `Tab` 移动
- `Space` 选中或取消选中模型
- `Enter` 对已选模型执行健康检测，并只加入通过检测的模型
- 已存在的模型会在批量新增时跳过

批量新增不会自动设置默认模型；如需切换默认模型，请使用“设置默认模型”。

新增并设为默认模型：

```bash
codex-model-admin model add example-model-low \
  --display-name example-model-low \
  --context-window 128000 \
  --provider-id example-provider \
  --default \
  --yes
```

基于已有模型作为模板新增：

```bash
codex-model-admin model add new-model \
  --display-name new-model \
  --context-window 128000 \
  --provider-id example-provider \
  --template example-model \
  --yes
```

创建单独 profile：

```bash
codex-model-admin model add new-model \
  --provider-id example-provider \
  --profile new-model-profile \
  --yes
```

如果只写 `--profile` 不带名称，会自动按模型名生成 profile 名称：

```bash
codex-model-admin model add new-model \
  --provider-id example-provider \
  --profile \
  --yes
```

### 7.3 设置默认模型

```bash
codex-model-admin model set-default example-model --yes
```

该命令会修改 `/root/.codex/config.toml` 中的：

```toml
model = "example-model"
```

在 TUI 中，“模型 ID”字段可按 `Ctrl+L` 或 `Space` 从已加入模型列表中选择，避免手动输入错误。

### 7.4 删除模型

```bash
codex-model-admin model delete old-model \
  --provider-id example-provider \
  --yes
```

如果要删除最后一个模型，需要 `--force`：

```bash
codex-model-admin model delete old-model \
  --provider-id example-provider \
  --force \
  --yes
```

删除模型时同时删除 profile：

```bash
codex-model-admin model delete old-model \
  --provider-id example-provider \
  --profile old-model-profile \
  --yes
```

在 TUI 中，“模型 ID”字段可按 `Ctrl+L` 或 `Space` 从当前 provider 已加入模型列表中选择：

- 输入文字可模糊筛选
- `↑↓` / `Tab` 移动
- `Space` 选中或取消选中模型
- `Ctrl+A` 全选当前筛选结果；如果当前筛选结果已全选，则清空这些选择
- `Enter` 填入选中项；如果没有显式选中，则填入当前高亮模型

选择多个模型后，`Ctrl+S` 会批量删除；是否允许删除最后一个模型仍由“强制删除”字段控制。

## 8. Proxy 管理

### 8.1 查看代理状态

```bash
codex-model-admin proxy status
```

输出包含：

- systemd 服务名
- active 状态
- 监听地址和端口
- 当前路由

### 8.2 设置代理监听地址和端口

只允许本机访问：

```bash
codex-model-admin proxy set \
  --listen-address 127.0.0.1 \
  --listen-port 15721 \
  --yes
```

允许局域网/远程访问：

```bash
codex-model-admin proxy set \
  --listen-address 0.0.0.0 \
  --listen-port 15721 \
  --restart \
  --yes
```

`--restart` 会在写入 systemd 服务后立即重启代理。

### 8.3 重启代理

```bash
codex-model-admin proxy restart --yes
```

### 8.4 查看代理日志

```bash
codex-model-admin proxy logs -n 100
```

### 8.5 测试代理

```bash
codex-model-admin proxy test --model example-model
```

指定 Base URL：

```bash
codex-model-admin proxy test \
  --model example-model \
  --base-url http://127.0.0.1:15721/v1
```

正常情况下会返回类似：

```json
{
  "status": "completed",
  "output": [
    {
      "content": [
        {
          "text": "pong"
        }
      ]
    }
  ]
}
```

## 9. Backup 管理

### 9.1 自动备份机制

所有写操作都会在执行前自动创建备份，例如：

- `provider add`
- `provider delete`
- `provider switch`
- `model add`
- `model delete`
- `model set-default`
- `proxy set`
- `backup restore`

备份默认保存到：

```bash
/root/.codex/model-admin-backups
```

备份 ID 使用时间戳；如果同一秒内连续写入，会自动添加后缀：

```text
20260706-160857
20260706-160857-001
20260706-160857-002
```

### 9.2 手动创建备份

```bash
codex-model-admin backup create --reason manual-before-change
```

指定额外备份源：

```bash
codex-model-admin backup create \
  --reason custom-backup \
  --source /root/.codex/config.toml \
  --source /root/.cc-switch/cc-switch.db
```

### 9.3 查看备份

```bash
codex-model-admin backup list
```

### 9.4 恢复备份

```bash
codex-model-admin backup restore 20260706-170533 --yes
```

恢复会把备份中的文件复制回原路径。

### 9.5 删除备份

```bash
codex-model-admin backup delete 20260706-170533 --yes
```

也可以一次删除多个备份：

```bash
codex-model-admin backup delete 20260706-170533 20260706-170602 --yes
```

删除备份只允许删除备份根目录下的合法备份 ID，非法路径或路径穿越会被拒绝。

在 TUI 中进入“删除备份”后支持单选和多选：

- `↑↓` / `Tab`：移动高亮行
- `Space`：选中或取消当前备份
- `Ctrl+A`：全选；如果已经全选，则清空选择
- `Enter`：删除选中项；未选中时删除当前高亮项
- `y`：在二次确认页确认删除
- `Esc` / `q`：取消返回

## 10. Codex 配置关系说明

核心关系如下：

```text
cc-switch provider 配置
        |
        | provider switch / model add
        v
/root/.codex/cc-switch-model-catalog.json
        |
        | model_catalog_json
        v
/root/.codex/config.toml
        |
        v
codex CLI 启动时读取模型 catalog
```

`/root/.codex/config.toml` 中关键字段示例：

```toml
model_provider = "custom"
model = "example-model"
model_reasoning_effort = "high"
disable_response_storage = true
model_catalog_json = "cc-switch-model-catalog.json"

[model_providers]
[model_providers.custom]
name = "custom"
use_response_api = false
requires_openai_auth = true
base_url = "http://127.0.0.1:15721/v1"
wire_api = "responses"
```

注意：

- `model_catalog_json` 指向 Codex 可见模型列表。
- `base_url` 通常指向本机 cc-switch proxy，例如 `http://127.0.0.1:15721/v1`。
- provider 的真实上游地址保存在 cc-switch 数据库中。

## 11. 常见问题

### 11.1 Codex 报 `missing field supported_reasoning_levels`

错误示例：

```text
Error loading configuration: failed to parse model_catalog_json path `/root/.codex/cc-switch-model-catalog.json` as JSON: missing field `supported_reasoning_levels`
```

原因：

Codex CLI 新版本要求 model catalog 中包含 `supported_reasoning_levels`、`shell_type` 等字段。旧格式 catalog 会导致 Codex 启动失败。

处理：

```bash
codex-model-admin provider switch example-provider --yes --no-restart
```

该命令会用当前代码重新生成兼容 Codex CLI 的 catalog。

### 11.2 `/v1/models` 返回很多模型，不是只返回配置的三个模型

如果上游 `/v1/models` 返回的是上游所有模型，这是上游服务行为；Codex 实际可见模型由 `/root/.codex/cc-switch-model-catalog.json` 控制。

查看 Codex 当前可见模型：

```bash
codex-model-admin model list
```

### 11.3 Codex 启动后没有看到自定义模型

检查三项：

```bash
codex-model-admin provider list
codex-model-admin model list
grep -n 'model_catalog_json\|model_provider\|model =' /root/.codex/config.toml
```

重新生成 catalog：

```bash
codex-model-admin provider switch example-provider --yes --no-restart
```

### 11.4 代理无法访问

查看状态：

```bash
codex-model-admin proxy status
```

查看日志：

```bash
codex-model-admin proxy logs -n 100
```

重启代理：

```bash
codex-model-admin proxy restart --yes
```

测试链路：

```bash
codex-model-admin proxy test --model example-model
```

### 11.5 远程机器需要给其他机器访问 proxy

把监听地址改成 `0.0.0.0`：

```bash
codex-model-admin proxy set \
  --listen-address 0.0.0.0 \
  --listen-port 15721 \
  --restart \
  --yes
```

然后从其他机器访问：

```bash
curl http://<server-ip>:15721/v1/models
```

同时要确认防火墙允许该端口。

## 12. 推荐操作流程

首次部署后建议按以下顺序执行：

```bash
codex-model-admin doctor
codex-model-admin backup create --reason initial-state
codex-model-admin provider list
codex-model-admin model list
codex-model-admin proxy status
codex-model-admin proxy test --model example-model
```

新增 provider 后：

```bash
codex-model-admin provider add my-provider \
  --name my-provider \
  --base-url http://example.com/v1 \
  --api-key sk-xxxx \
  --default-model my-model \
  --context-window 128000 \
  --switch \
  --yes

codex-model-admin model list
codex-model-admin proxy test --model my-model
```

新增模型后：

```bash
codex-model-admin model add new-model \
  --display-name new-model \
  --context-window 128000 \
  --provider-id example-provider \
  --yes

codex-model-admin model set-default new-model --yes
codex
```

## 13. 开发和验证

在项目目录中运行测试：

```bash
cd /opt/codex-model-admin
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

当前测试覆盖：

- 环境检查
- 安装流程
- provider list/add/update/switch/delete
- model list/add/delete/set-default/sync-current、模型选择、批量新增、健康检测
- Codex catalog schema 兼容性
- proxy status/set/restart/test
- backup create/list/restore/delete
- TUI 菜单导航、聚焦表单、模型列表、备份删除多选等交互

## 14. 安全建议

- 不要把真实 API Key 写进文档、截图或聊天记录。
- 写操作都应带 `--yes`，并确认命令目标正确。
- 删除 provider/model 前先执行 `backup create` 或确认自动备份 ID。
- 允许远程访问 proxy 时，优先通过内网和防火墙限制来源。
- 生产环境建议使用 `127.0.0.1` 监听，只有确实需要跨机器访问时才使用 `0.0.0.0`。

## 15. 服务器一键迁移

新旧服务器能够通过 root 用户免密 SSH 登录时，可以在新服务器上一键复制完整运行环境。整个迁移只在新服务器的独立 root SSH shell 中执行，不需要登录源服务器停止进程：

运行前提：

- 新服务器必须使用 systemd、以 root 执行，并且是 x86_64 Linux；当前 cc-switch 安装包不支持其他架构。
- 源服务器必须允许 root 免密 SSH 登录，且提供 systemd、GNU tar 和 `pgrep`。
- 不要从目标服务器上的 Codex CLI 任务内部启动迁移；目标端旧 Codex/cc-switch 进程会在配置替换前自动停止。
- 源端活动的 Codex app-server、Node wrapper、codex-model-admin 和独立 cc-switch daemon/worker 会由脚本短暂暂停并自动恢复。
- 目标安装目录如果已经是 Git 仓库，必须没有未提交修改；非空且不是 Git 仓库时脚本会拒绝覆盖。

```bash
curl -fL \
  https://raw.githubusercontent.com/chismi1017/codex-model-admin/main/scripts/migrate-from-server.sh \
  -o /root/migrate-from-server.sh
chmod 700 /root/migrate-from-server.sh
/root/migrate-from-server.sh root@old-server --yes
```

也可以使用 `~/.ssh/config` 中已经配置好的主机别名：

```bash
/root/migrate-from-server.sh old-server-alias --yes
```

迁移脚本会执行：

1. 检查目标端 root、systemd、x86_64 架构，以及源端 root 免密 SSH、systemd 和 GNU tar。
2. 通过 `dnf`、`yum` 或 `apt-get` 补齐 Git、SSH、Python、Node.js/npm、curl、tar、gzip 和 pgrep。
3. 从 GitHub 克隆或更新项目到 `/opt/codex-model-admin`。
4. 安装 `codex-model-admin`、Codex CLI 和 cc-switch。
5. 停止源服务器 systemd proxy，自动暂停其他 Codex/cc-switch 相关进程，然后流式生成一致性归档。
6. 将目标服务器原配置保存到 `/root/codex-model-admin-rollback-<时间戳>`。
7. 恢复配置、权限、SELinux context 和 systemd 状态，并运行环境检查。

源端自动暂停与恢复机制：

1. 记录源端 proxy 的 enabled/active 状态并停止 systemd 服务。
2. 对 Codex app-server、Node app-server wrapper、codex-model-admin 和独立 cc-switch daemon/worker 发送 `SIGSTOP`。
3. 创建只允许 root 读取的暂停 PID 文件，并启动独立 watchdog。
4. 归档正常结束时发送 `SIGCONT`，随后恢复源端 proxy 原状态。
5. SSH 归档 shell 异常退出时，watchdog 自动发送 `SIGCONT`。
6. 暂停超过 600 秒时，watchdog 自动恢复进程、标记超时并使迁移归档作废。

目标端配置即将被源端配置替换，因此目标端旧 Codex/cc-switch 进程不会恢复：脚本先发送 `SIGTERM`，最多等待约 10 秒，仍未退出时发送 `SIGKILL`。这不会影响源服务器上的对应进程。

复制内容包括：

- `/root/.cc-switch` 中的 provider、模型、API Key 和 SQLite 状态。
- `/root/.codex` 中的配置、认证、全局 `AGENTS.md`、rules、skills、memory 和会话状态。
- `/root/.config/codex-model-admin`、`/root/.config/cc-switch` 和 `/root/.npmrc`（存在时）。
- `cc-switch-codex-proxy.service` 及其 drop-in 配置。

不会复制 Codex、Node.js、cc-switch 等二进制文件，也不会复制日志、缓存、锁文件和旧的 model-admin 备份。这些组件会在目标服务器重新安装。迁移归档可能包含 API Key、Codex 认证、npm registry 凭据和会话信息，默认只在迁移期间临时保存并使用 `0600` 权限，成功后自动删除。

这不是 Linux 整机克隆：不会迁移系统用户、SSH 私钥、Git 凭据、防火墙、软件包数据库、系统日志或项目源码中的本地未提交文件。项目目录中被 `.gitignore` 排除的 `AGENTS.md` 也不会复制；迁移的是 `/root/.codex/AGENTS.md`。源端和目标端仍是两台独立服务器；复制认证状态后，两端可能同时持有相同凭据。

参数说明：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `<源服务器SSH地址>` | 必填 | 源服务器地址，或 `~/.ssh/config` 中的别名 |
| `--source <地址>` | 无 | `<源服务器SSH地址>` 的显式写法 |
| `--repo-url <URL>` | 当前 GitHub 仓库 | 用于克隆或更新项目代码 |
| `--branch <分支>` | `main` | 克隆或更新的 Git 分支 |
| `--install-dir <目录>` | `/opt/codex-model-admin` | 新服务器项目安装目录，必须是非根目录的绝对路径 |
| `--keep-archive` | 关闭 | 成功后保留包含敏感数据的临时迁移归档，并输出实际路径 |
| `-y` / `--yes` | 关闭 | 跳过目标配置覆盖确认 |
| `-h` / `--help` | 关闭 | 显示参数、示例和运行要求后退出 |

常用选项：

```bash
# 指定项目分支和安装目录
/root/migrate-from-server.sh old-server-alias \
  --repo-url https://github.com/chismi1017/codex-model-admin.git \
  --branch main \
  --install-dir /opt/codex-model-admin \
  --yes

# 迁移成功后保留源环境归档
/root/migrate-from-server.sh old-server-alias --keep-archive --yes
```

目标配置开始移动后，如果归档提取、权限恢复或 systemd 状态恢复尚未完成就发生错误或收到 `Ctrl+C`/终止信号，脚本会尝试恢复目标服务器原配置和原服务状态。配置与服务状态恢复完成后，环境检查失败会返回非零，模型健康检测失败会输出警告；两者都不会自动撤销迁移，管理员可以使用回滚目录人工恢复。

迁移成功后仍会保留目标服务器迁移前的回滚目录，确认新环境正常后再手动删除。使用 `--keep-archive` 时还会保留包含源端 API Key、认证、npm registry 凭据和会话状态的归档，使用后应及时安全删除。

人工恢复回滚目录时，先停止代理，再删除迁移后的对应路径并从回滚目录复制回来。以下命令中的时间戳必须替换成脚本输出的实际目录：

```bash
ROLLBACK=/root/codex-model-admin-rollback-YYYYmmdd-HHMMSS

systemctl stop cc-switch-codex-proxy.service 2>/dev/null || true

rm -rf \
  /root/.cc-switch \
  /root/.codex \
  /root/.config/codex-model-admin \
  /root/.config/cc-switch \
  /etc/systemd/system/cc-switch-codex-proxy.service \
  /etc/systemd/system/cc-switch-codex-proxy.service.d
rm -f /root/.npmrc

for path in \
  root/.cc-switch \
  root/.codex \
  root/.config/codex-model-admin \
  root/.config/cc-switch \
  root/.npmrc \
  etc/systemd/system/cc-switch-codex-proxy.service \
  etc/systemd/system/cc-switch-codex-proxy.service.d; do
  if [ -e "$ROLLBACK/$path" ] || [ -L "$ROLLBACK/$path" ]; then
    mkdir -p "/$(dirname "$path")"
    cp -a "$ROLLBACK/$path" "/$(dirname "$path")/"
  fi
done

systemctl daemon-reload
```

回滚目录不记录迁移前服务是否 enabled/active。人工恢复后，应根据迁移前状态选择 `systemctl enable --now cc-switch-codex-proxy.service`，或保持服务停止/禁用。

## 16. 完全卸载

`scripts/uninstall-codex-model-admin.sh` 用于永久删除本项目及其管理的 Codex/cc-switch 环境。必须以 root 在独立 SSH shell 中执行，不要从即将被终止的 Codex 会话内部启动。

先执行 dry-run 检查删除范围：

```bash
cd /root
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh --dry-run
```

确认输出无误后执行：

```bash
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh --yes
```

默认会删除：

- `cc-switch-codex-proxy.service`，以及已存在的 `cc-switch.service`、`cc-switch-daemon.service` 和对应 drop-in、开机启动链接。
- 活动的 Codex、cc-switch、Node Codex wrapper 和 codex-model-admin 进程；先发送 `SIGTERM`，超时后发送 `SIGKILL`。
- npm 全局包 `@openai/codex`、标准路径中的 Codex CLI 入口、`/usr/local/bin/cc-switch` 和 `/usr/local/bin/codex-model-admin`。
- `/root/.codex`、`/root/.cc-switch`、`/root/.config/codex-model-admin` 和 `/root/.config/cc-switch`。
- `/opt/codex-model-admin`，或 `--project-dir` 指定且通过安全校验的项目目录。
- `/root/codex-model-admin-rollback-*`、迁移临时目录、迁移锁、`/root/migrate-from-server.sh` 和 cc-switch 安装临时文件。

这些目录可能包含 API Key、Codex 认证、供应商、模型 catalog、memory、会话、skills、rules 和备份，删除后无法恢复。

默认保留 `/root/.npmrc`，因为它可能包含其他 npm 软件共用的 registry 配置。确认该文件也应删除时使用：

```bash
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh --yes --purge-npmrc
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--project-dir <目录>` | `/opt/codex-model-admin` | 指定项目目录；目录名必须为 `codex-model-admin` |
| `--purge-npmrc` | 关闭 | 同时永久删除 `/root/.npmrc` |
| `--dry-run` | 关闭 | 只显示计划执行的命令，不修改服务、进程和文件 |
| `-y` / `--yes` | 关闭 | 跳过 `DELETE <hostname>` 交互确认 |
| `-h` / `--help` | 关闭 | 显示帮助 |

卸载脚本不会删除 Node.js、npm、Python、Git、curl、tar、gzip、SSH、systemd 等共享系统组件，也不会清理共享 journald 数据库或 shell history。脚本检测到服务器迁移正在运行时会拒绝执行；只有锁目录而没有迁移进程时，将其视为可清理的残留。
