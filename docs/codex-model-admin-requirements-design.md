# Codex Model Admin 需求与设计文档

## 1. 背景

示例目标服务器上需要完成如下链路：

```text
Codex CLI
  -> http://127.0.0.1:15721/v1
  -> cc-switch-cli Codex proxy
  -> 第三方 OpenAI-compatible 上游
```

目标验证项：

- `cc-switch-cli` 已安装在 `/usr/local/bin/cc-switch`
- Codex provider 已配置为 `example-provider`
- systemd 服务 `cc-switch-codex-proxy.service` 已运行并开机自启
- Codex CLI 默认模型为 `example-model`
- 自定义模型通过 `codex debug models` 可见
- 自定义模型可通过 `codex -m <model>` 或 `codex -p <profile>` 使用

现有痛点：

- Codex CLI 自带 `/model` 菜单只展示官方推荐模型，不能展示自定义模型。
- 新增模型需要同时更新 cc-switch provider、Codex model catalog、profile 和 proxy 服务。
- 供应商、模型、代理、Codex 配置分散，手工维护容易出错。
- 需要一个统一 TUI 管理工具，面向服务器环境使用。
- 新服务器部署时，需要一次完成代码安装、组件安装和现有 Codex/cc-switch 持久化环境迁移。

## 2. 目标

实现一个远程服务器本地 TUI 工具：

```bash
codex-model-admin
```

用于统一管理：

- Codex 自定义 Provider
- Provider 下的模型列表
- cc-switch-cli 本地代理
- Codex CLI 配置与模型 catalog
- profile、测试、备份、恢复
- 通过免密 SSH 在 Linux 服务器之间迁移完整 Codex/cc-switch 持久化环境

## 3. 非目标

- 不修改 Codex CLI 自带 `/model` 菜单。
- 不依赖 Codex Desktop。
- 不强制安装图形环境。
- 不默认暴露代理到公网或局域网。
- 不默认修改 iptables 放行规则，除非用户明确确认。
- 服务器迁移不是 Linux 整机镜像，不复制系统用户、SSH 私钥、Git 凭据、防火墙、软件包数据库、二进制、日志或缓存。

## 4. 运行环境

目标环境：

```text
OS: Linux server; Ubuntu/Debian 等其他 Linux 可尝试使用（未验证）
User: root
Codex CLI: v0.143.0+（当前部署），最低兼容目标为 v0.142.5+
cc-switch-cli: 5.8.7+
Python: 3.9+
```

运行时依赖：

```text
systemd
curl
tar
node / npm
python3
sqlite3 Python module
ss
```

一键迁移额外依赖：

```text
目标服务器: x86_64、Git、OpenSSH client、GNU tar、gzip、pgrep
源服务器: root 免密 SSH、systemd、GNU tar、pgrep
```

迁移脚本可通过 `dnf`、`yum` 或 `apt-get` 在目标服务器补齐 Git、SSH、Python、Node.js/npm、curl、tar、gzip 和 pgrep。Codex CLI 与 cc-switch 在目标服务器重新安装，不复制源服务器二进制。

关键路径：

```text
/usr/bin/codex
/usr/local/bin/cc-switch
/root/.cc-switch/cc-switch.db
/root/.codex/config.toml
/root/.codex/cc-switch-model-catalog.json
/root/.codex/*.config.toml
/etc/systemd/system/cc-switch-codex-proxy.service
```

## 4.1 前置条件检查与自动安装

`codex-model-admin` 启动时必须先执行前置检查。检查结果以明确状态展示：

```text
环境检查
────────────────────────────────────────
✅ codex       /usr/bin/codex              codex-cli 0.143.0
✅ cc-switch   /usr/local/bin/cc-switch    cc-switch 5.8.7
✅ node        /usr/bin/node               v20.20.2
✅ npm         /usr/bin/npm                11.18.0
✅ python3     /usr/bin/python3            3.9+
✅ systemd     可用
✅ curl        可用
✅ tar         可用
```

检查策略：

- 如果已安装，显示路径和版本，不重复安装。
- 如果未安装，提示用户确认后自动安装。
- 如果自动安装失败，显示失败命令、stderr 摘要和手动修复建议。
- 如果缺少 Node/npm，则 Codex CLI 不能自动安装，需先提示安装 Node/npm。
- 如果缺少 curl/tar/systemd/python3，则阻止继续进入主流程。

Codex CLI 安装策略：

```bash
npm install -g @openai/codex
```

安装后验证：

```bash
command -v codex
codex --version
```

cc-switch-cli 安装策略：

```bash
curl -L -o /tmp/cc-switch-cli.tar.gz \
  https://github.com/saladday/cc-switch-cli/releases/latest/download/cc-switch-cli-linux-x64-musl.tar.gz
tar -xzf /tmp/cc-switch-cli.tar.gz -C /tmp
install -m 0755 /tmp/cc-switch /usr/local/bin/cc-switch
```

安装后验证：

```bash
command -v cc-switch
cc-switch --version
```

前置检查应同时支持非交互命令：

```bash
codex-model-admin doctor
codex-model-admin install codex
codex-model-admin install cc-switch
codex-model-admin install all
```

## 5. 用户角色

主要用户：

- 服务器管理员
- Codex CLI 使用者
- 需要接入多个第三方 OpenAI-compatible 模型服务的开发者

用户默认有 root 权限。

## 6. 核心概念

### 6.1 Provider

Provider 表示一个上游模型服务或账号，例如：

```text
example-provider
openrouter
siliconflow
custom-company-api
```

Provider 负责：

- base URL
- API key
- API format
- 默认模型
- 模型目录 `modelCatalog`

底层存储：

```text
/root/.cc-switch/cc-switch.db
providers where app_type = 'codex'
```

### 6.2 Model

Model 是某个 Provider 下可用的模型，例如：

```text
example-model
example-model-fast
example-model-low
```

同一个模型需要同步到两处：

```text
cc-switch provider settings_config.modelCatalog
/root/.codex/cc-switch-model-catalog.json
```

### 6.3 代理（Proxy）

代理是本机 cc-switch-cli 协议转换服务：

```text
127.0.0.1:15721
```

负责把 Codex CLI 请求转发到当前 Provider，并支持：

```text
/v1/responses
/v1/chat/completions
```

### 6.4 Codex Catalog

Codex CLI 通过：

```toml
model_catalog_json = "cc-switch-model-catalog.json"
```

读取本地模型目录。该目录不等同于 `/model` 菜单，但可被：

```bash
codex debug models
codex -m <model>
```

识别。

## 7. 功能需求

## 7.1 主菜单

TUI 启动后显示：

```text
>_ Codex Model Admin

当前路由:   cc-switch managed / Codex CLI proxy
供应商:     example-provider
默认模型:   example-model
工作目录:   /opt/codex-model-admin

环境 / 安装
  1. 环境检查
  2. 安装缺失组件

供应商
  3. 供应商列表
  4. 新增供应商
  5. 修改供应商
  6. 切换供应商
  7. 删除供应商

模型
  8. 模型列表
  9. 新增模型
 10. 设置默认模型
 11. 删除模型

代理
 12. 代理状态
 13. 设置代理
 14. 重启代理
 15. 查看代理日志
 16. 测试代理

备份
 17. 创建备份
 18. 备份列表
 19. 恢复备份
 20. 删除备份

设置
 21. 界面语言
```

主菜单交互：

- `Tab` / `↑↓` 移动菜单项，当前行整行高亮。
- `Enter` 执行当前菜单项。
- 数字可直接执行对应动作。
- `q` / `Esc` 退出。
- 写入类动作执行前自动备份，并要求显式确认。

## 7.1.1 环境检查 / 安装

显示当前环境是否满足后续管理动作的前置条件：

```text
环境检查 / 安装
────────────────────────────────────────
✅ codex       /usr/bin/codex              codex-cli 0.143.0
✅ cc-switch   /usr/local/bin/cc-switch    cc-switch 5.8.7
✅ node        /usr/bin/node               v20.20.2
✅ npm         /usr/bin/npm                11.18.0
✅ systemd     available
✅ curl        available
✅ tar         available

a 安装全部缺失项
c 安装 codex
s 安装 cc-switch
r 刷新
q 返回
```

行为要求：

- 缺失项用 `❌` 标识。
- 版本过低用 `⚠` 标识，并提示当前版本和最低建议版本。
- 安装完成后自动刷新状态。
- 安装 Codex CLI 前必须检查 npm。
- 安装 cc-switch-cli 前必须检查 curl 和 tar。
- 安装动作写入 `/root/.codex/model-admin.log`。

## 7.2 供应商（Provider）管理

### 7.2.1 供应商列表

显示所有 Codex 供应商：

```text
供应商列表
────────────────────────────────────────
当前  ID                名称              Base URL                                 模型  默认模型 / 状态
✓     example-provider  Example Provider  https://api.example.com/v1               3  example-model
      codex-official    OpenAI Official   官方内置                                 动态  系统只读，不可切换
      custom            custom            https://example.com/v1                     1  custom-model
```

字段：

- 是否当前供应商
- 供应商 ID
- 供应商名称
- base URL
- 模型数量
- 默认模型
- 只读/动态状态

底层：

```sql
select id, name, settings_config, is_current
from providers
where app_type = 'codex';
```

### 7.2.2 新增供应商

交互字段：

```text
供应商 ID:
供应商名称:
Base URL:
API Key:
API 格式: responses/chat
默认模型:
上下文窗口:
立即切换到该供应商? yes/no
```

TUI 中默认模型字段支持按 `Ctrl+L` 或 `Space` 从当前 `Base URL` 拉取 `/v1/models`，选中后立即进行健康检测；只有健康检测通过的默认模型才允许提交。

生成 `settings_config`：

```json
{
  "auth": {
    "OPENAI_API_KEY": "sk-example"
  },
  "config": "model_provider = \"custom\"\nmodel = \"...\"\n...",
  "modelCatalog": {
    "models": [
      {
        "model": "...",
        "displayName": "...",
        "contextWindow": 128000
      }
    ]
  }
}
```

调用：

```bash
cc-switch -a codex provider add \
  --id <id> \
  --name <name> \
  --config-file <tmp-json>
```

### 7.2.3 修改供应商

支持修改已存在 provider 的 ID 和连接信息：

- 供应商 ID
- 供应商名称
- base URL
- API key
- 默认模型
- 上下文窗口
- API format

规则：

- 不提供任意 JSON 编辑，避免破坏 `settings_config` 结构。
- 如果修改供应商 ID，旧 ID 用于定位原 provider，新 ID 必须不存在。
- 如果修改的是当前 provider，可选择同步 Codex catalog/config 并重启代理。
- TUI 供应商 ID 字段从可管理供应商中选择，不包含 `codex-official`；新供应商 ID 字段用于改名。

CLI：

```bash
codex-model-admin provider update <id> \
  --new-id <new-id> \
  --name <name> \
  --base-url <url> \
  --api-key <key> \
  --default-model <model> \
  --context-window 128000 \
  --api-format responses \
  --sync-current \
  --restart \
  --yes
```

### 7.2.4 删除供应商

删除前检查：

- 是否 `codex-official`
- 是否当前 provider
- 是否有关联 profile
- 是否会导致 Codex 当前默认模型失效

规则：

- 不允许删除 `codex-official`
- 删除当前 provider 必须显式 `--force`
- 删除前自动备份

调用：

```bash
cc-switch -a codex provider delete <id>
```

### 7.2.5 切换供应商

切换流程：

```text
1. cc-switch -a codex provider switch <id>
2. 从该 provider 的 modelCatalog 生成 /root/.codex/cc-switch-model-catalog.json
3. 更新 /root/.codex/config.toml 默认 model
4. 重启 cc-switch-codex-proxy.service
5. 测试 /v1/responses
```

调用：

```bash
cc-switch -a codex provider switch <id>
systemctl restart cc-switch-codex-proxy.service
```

### 7.2.6 测试供应商

测试方式：

- 当前 provider：通过本地 proxy 测试
- 非当前 provider：直接请求 provider base URL，不切换当前 provider

测试请求：

```http
POST /v1/responses
{
  "model": "<model>",
  "input": "只回复 pong"
}
```

## 7.3 模型（Model）管理

### 7.3.1 模型列表

显示当前 Provider 的模型：

```text
模型列表
────────────────────────────────────────
  模型                                        显示名称                                                  上下文  健康
  example-model                        example-model                                      200000  OK
› example-model-low                          example-model-low                                        128000  FAIL
```

字段：

- model slug
- 显示名称
- 上下文窗口
- 是否当前默认模型
- 是否存在于 cc-switch provider
- 是否存在于 Codex catalog
- 健康检测状态

TUI 模型列表交互：

- `↑↓` / `Tab` 移动高亮行。
- `Enter` 查看完整模型详情。
- `Space` 检测当前模型。
- `Ctrl+A` 检测全部模型。
- `d` 删除健康检测失败的模型；当前默认模型不能直接删除。
- `Esc` / `q` 返回。

### 7.3.2 新增模型

交互字段：

```text
模型 ID:
显示名称:
上下文窗口:
供应商 ID:
profile 名称:
设为默认模型?
```

模型 ID 字段支持按 `Ctrl+L` 或 `Space` 从当前供应商 `/v1/models` 拉取模型列表。选择器支持输入筛选、`Space` 多选、`Enter` 健康检测并确认。批量新增只加入健康检测通过的模型，已存在模型跳过；批量新增不自动设置默认模型。

动作：

```text
1. 写入 provider.settings_config.modelCatalog
2. 写入 /root/.codex/cc-switch-model-catalog.json
3. 可选创建 /root/.codex/<profile>.config.toml
4. 可选设为 /root/.codex/config.toml 默认 model
5. 可选健康检测模型
6. 重启 proxy
```

CLI 命令为 `codex-model-admin model add`。

### 7.3.3 删除模型

删除前确认：

- 是否当前默认模型
- 是否被 profile 引用
- 是否是 provider 最后一个模型

动作：

```text
1. 从 provider modelCatalog 删除
2. 从 Codex catalog 删除
3. 可选删除 profile
4. 如果删除默认模型，要求选择新的默认模型
5. 重启 proxy
```

### 7.3.4 设置默认模型

修改：

```text
/root/.codex/config.toml
```

字段：

```toml
model = "<selected-model>"
```

TUI 中模型 ID 字段支持按 `Ctrl+L` 或 `Space` 从已加入模型列表选择。

### 7.3.5 创建 Profile

创建：

```text
/root/.codex/<profile>.config.toml
```

内容：

```toml
model = "<model>"
```

用法：

```bash
codex -p <profile>
```

## 7.4 代理（Proxy）管理

### 7.4.1 代理状态

显示：

```text
Codex 代理
────────────────────────────────────────
状态:          active
监听:          127.0.0.1:15721
路由:          Codex on
供应商:        example-provider
服务:          cc-switch-codex-proxy.service
Codex 配置:    base_url = http://127.0.0.1:15721/v1
```

检查命令：

```bash
systemctl status cc-switch-codex-proxy.service
ss -lntp | grep 15721
cc-switch -a codex proxy show
```

### 7.4.2 代理绑定地址

支持：

```text
127.0.0.1   仅本机访问，默认推荐
0.0.0.0     所有网卡监听，允许局域网访问
指定 IP     绑定指定网卡
```

底层命令：

```bash
cc-switch -a codex proxy config \
  --listen-address <address> \
  --listen-port <port>
```

同时更新 systemd：

```ini
ExecStart=/usr/local/bin/cc-switch -a codex proxy serve --listen-address <address> --listen-port <port>
```

### 7.4.3 代理端口

支持自定义端口：

```text
15721
15722
18080
```

修改端口后必须同步：

```text
1. cc-switch proxy config
2. systemd ExecStart
3. /root/.codex/config.toml base_url
4. restart service
```

### 7.4.4 客户端 Base URL

如果 listen address 是：

```text
0.0.0.0
```

Codex 本机仍应使用：

```toml
base_url = "http://127.0.0.1:<port>/v1"
```

远程其他机器访问时使用：

```text
http://<server-ip>:<port>/v1
```

### 7.4.5 重启代理

执行：

```bash
systemctl daemon-reload
systemctl restart cc-switch-codex-proxy.service
```

### 7.4.6 代理日志

显示：

```bash
journalctl -u cc-switch-codex-proxy.service -n 100 --no-pager
```

### 7.4.7 代理安全提示

当用户选择：

```text
0.0.0.0
```

必须提示：

```text
Warning: 0.0.0.0 exposes the proxy to LAN. Make sure firewall allows only trusted clients.
```

不默认自动改 iptables。

可提示用户手动限制：

```bash
iptables -A INPUT -p tcp -s 192.168.3.0/24 --dport 15721 -j ACCEPT
iptables -A INPUT -p tcp --dport 15721 -j DROP
```

## 7.5 Codex 配置关系

当前实现没有独立的“Codex 管理”主菜单。Codex 配置通过 provider/model/proxy/backup 等动作间接维护；调试命令作为运维参考保留在文档中。

### 7.5.1 配置预览

显示：

```text
/root/.codex/config.toml
```

重点字段：

```toml
model_provider
model
model_catalog_json
base_url
wire_api
```

### 7.5.2 调试模型列表

执行：

```bash
codex debug models
```

### 7.5.3 执行测试

执行：

```bash
codex exec -m <model> --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "只回复 pong" </dev/null
```

### 7.5.4 动态模型启动器

已有：

```bash
codex-model
```

它动态读取：

```text
/root/.codex/cc-switch-model-catalog.json
```

并启动：

```bash
codex -m <selected-model>
```

## 7.6 备份 / 恢复 / 删除

### 7.6.1 备份

每次写操作前自动备份：

```text
/root/.codex/model-admin-backups/YYYYmmdd-HHMMSS/
```

备份内容：

```text
/root/.cc-switch/cc-switch.db
/root/.codex/config.toml
/root/.codex/cc-switch-model-catalog.json
/root/.codex/*.config.toml
/etc/systemd/system/cc-switch-codex-proxy.service
```

### 7.6.2 恢复

允许从备份目录恢复：

```text
1. 停止 cc-switch-codex-proxy.service
2. 恢复文件
3. systemctl daemon-reload
4. 重启服务
5. 验证代理和 codex debug models
```

### 7.6.3 删除备份

允许删除一个或多个备份：

```bash
codex-model-admin backup delete <backup_id> [backup_id ...] --yes
```

安全要求：

- 只接受备份根目录下的直接子目录 ID。
- 拒绝空 ID、绝对路径、路径分隔符和路径穿越。
- 删除前必须显式确认。

TUI 删除备份选择器：

- `↑↓` / `Tab` 移动高亮备份。
- `Space` 选中或取消当前备份。
- `Ctrl+A` 全选；已经全选时再次按下会清空选择。
- `Enter` 删除选中项；未选中时删除当前高亮项。
- `y` 在二次确认页确认删除。
- `Esc` / `q` 取消返回。

## 7.7 服务器一键迁移

提供独立脚本：

```bash
scripts/migrate-from-server.sh <源服务器SSH地址> [选项]
```

脚本在新服务器以 root 执行，负责：

1. 检查目标端 systemd、x86_64 架构和基础命令。
2. 通过免密 SSH 检查源端 root、systemd、GNU tar、pgrep 和核心配置文件。
3. 从 GitHub 克隆或更新代码，安装 `codex-model-admin`、Codex CLI 和 cc-switch。
4. 记录源端代理 enabled/active 状态，暂停源端 systemd proxy。
5. 自动暂停 Codex app-server、Node wrapper、codex-model-admin 和独立 cc-switch daemon/worker，不终止源端进程。
6. 启动独立 watchdog，并通过 SSH stdout 流式创建归档；源服务器不落地包含密钥的临时包。
7. 校验归档路径和核心文件，在目标端覆盖前保存回滚目录。
8. 恢复配置、权限和 SELinux context，并在目标端镜像源端迁移前的代理启用/运行状态。
9. 执行 doctor、provider、model、proxy 和模型健康检查。

复制范围：

```text
/root/.cc-switch
/root/.codex
/root/.config/codex-model-admin
/root/.config/cc-switch
/root/.npmrc
/etc/systemd/system/cc-switch-codex-proxy.service
/etc/systemd/system/cc-switch-codex-proxy.service.d
```

排除范围：

```text
/root/.codex/model-admin-backups
/root/.codex/log
/root/.codex/app-server-control
/root/.codex/.tmp
/root/.cc-switch/*.log
/root/.cc-switch/*.lock
```

参数：

| 参数 | 默认值 | 约束 |
|---|---|---|
| `<源服务器SSH地址>` / `--source` | 必填 | 不能以 `-` 开头或包含空白字符 |
| `--repo-url` | 当前 GitHub 仓库 | 不能以 `-` 开头或包含空白字符 |
| `--branch` | `main` | 仅允许安全的 Git 分支字符 |
| `--install-dir` | `/opt/codex-model-admin` | 必须是非根目录的绝对路径 |
| `--keep-archive` | 关闭 | 保留权限为 `0600` 的敏感归档 |
| `-y` / `--yes` | 关闭 | 跳过覆盖确认 |
| `-h` / `--help` | 关闭 | 显示帮助后退出，不执行环境修改 |

目标端原配置移动到：

```text
/root/codex-model-admin-rollback-YYYYmmdd-HHMMSS
```

自动回滚边界：

- 目标配置移动后、配置和服务状态恢复完成前发生错误或收到 INT/TERM 时，恢复原配置和原服务状态。
- 配置与服务状态恢复完成后，doctor 或健康检查失败不自动回滚，保留迁移结果和回滚目录供管理员判断。
- 源端代理通过远程 EXIT trap 恢复；恢复失败必须返回非零并提示人工检查。
- 源端暂停进程通过正常 cleanup 或独立 watchdog 恢复；远程 shell 消失或 600 秒超时时必须发送 SIGCONT。
- watchdog 超时必须使归档作废并返回非零，不能继续使用进程恢复后生成的归档。
- 回滚目录只保存原配置路径，不持久化迁移前服务 enabled/active 元数据；人工回滚后由管理员按原状态启停服务。

## 8. 设计方案

## 8.1 技术选型

推荐：

```text
Python 3.9 + curses + sqlite3 + json + subprocess
```

原因：

- Linux 服务器环境常见组件；Ubuntu/Debian 等其他 Linux 可尝试使用（未验证）
- 无额外 Python 包依赖
- 可在纯 SSH 环境运行
- 易于维护和审计

不推荐第一版使用：

```text
Textual / Rich / Prompt Toolkit
```

原因：

- 需要安装额外依赖
- 服务器维护复杂度更高

## 8.2 程序结构

建议文件：

```text
/usr/local/bin/codex-model-admin
/opt/codex-model-admin/scripts/install-codex-model-admin.sh
/opt/codex-model-admin/scripts/migrate-from-server.sh
/opt/codex-model-admin/scripts/uninstall-codex-model-admin.sh
```

`migrate-from-server.sh` 是独立 Bash 入口。它必须在 Python 管理工具尚未安装的新服务器上运行，因此不依赖项目 Python 模块；完成 Git clone 后再调用 `install-codex-model-admin.sh` 和 `codex-model-admin install all --yes`。

`uninstall-codex-model-admin.sh` 也是独立 Bash 入口。它提供 `--dry-run`、绝对路径白名单、迁移任务冲突检查和主机名确认，用于永久删除本项目安装的命令、服务、配置、认证、会话、备份和迁移残留；共享系统依赖不属于卸载范围。

内部模块划分：

```text
PreflightManager
InstallerManager
ConfigStore
ProviderStore
ModelStore
ProxyManager
CodexManager
BackupManager
TuiApp
```

### PreflightManager

负责检查：

```text
codex
cc-switch
node
npm
python3
systemd
curl
tar
ss
```

输出统一结构：

```json
{
  "name": "codex",
  "required": true,
  "installed": true,
  "path": "/usr/bin/codex",
  "version": "codex-cli 0.143.0",
  "minimum_version": "0.142.5",
  "status": "ok"
}
```

### InstallerManager

负责自动安装缺失组件：

```text
install_codex_cli()
install_cc_switch_cli()
verify_after_install()
```

安装前要求：

- root 用户确认
- 网络连通性检查
- 日志记录
- 不覆盖已存在二进制，除非用户明确选择 upgrade/reinstall

## 8.3 数据来源

### ProviderStore

读取：

```text
/root/.cc-switch/cc-switch.db
```

表：

```sql
providers
proxy_config
provider_health
proxy_request_logs
```

### ModelStore

读取：

```text
provider.settings_config.modelCatalog
/root/.codex/cc-switch-model-catalog.json
```

### ProxyManager

读取：

```text
cc-switch -a codex proxy show
systemctl status cc-switch-codex-proxy.service
ss -lntp
```

写入：

```text
cc-switch proxy config
/etc/systemd/system/cc-switch-codex-proxy.service
/root/.codex/config.toml
```

## 8.4 配置文件

工具自身配置：

```text
/root/.codex/model-admin.json
```

建议内容：

```json
{
  "provider_id": "example-provider",
  "proxy": {
    "listen_address": "127.0.0.1",
    "listen_port": 15721,
    "client_base_url": "http://127.0.0.1:15721/v1",
    "service_name": "cc-switch-codex-proxy.service"
  },
  "paths": {
    "ccswitch_db": "/root/.cc-switch/cc-switch.db",
    "codex_config": "/root/.codex/config.toml",
    "codex_catalog": "/root/.codex/cc-switch-model-catalog.json"
  }
}
```

## 8.5 TUI 页面

当前 TUI 使用“单层菜单 + 聚焦表单 + 专项选择器”的结构，不再使用多级页面。

### 主菜单

```text
Codex Model Admin
当前路由 / 供应商 / 默认模型 / 工作目录
按环境、供应商、模型、代理、备份、设置分组展示 1-21 个动作
```

主菜单快捷键：

- `Tab` / `↑↓`：移动
- `Enter`：执行
- 数字：直达并执行
- `q` / `Esc`：退出

### 聚焦表单

新增、修改、切换、删除等写入动作使用统一聚焦表单：

- `Tab` / `Shift+Tab` / `↑↓` 切换字段。
- 文本字段直接输入。
- 选项字段使用 `Space` / `←→` 切换。
- `Ctrl+P` 预览命令。
- `Ctrl+S` 执行。
- `Esc` 返回。

### 模型选择器

用于新增模型和设置默认模型：

- 输入文本实时筛选。
- `↑↓` / `Tab` 移动。
- 新增模型选择器支持 `Space` 多选，`Enter` 健康检测并确认。
- 设置默认模型选择器使用 `Enter` / `Space` 填入模型 ID。
- `Ctrl+U` 清空筛选。
- `Esc` 返回表单。

### 模型列表

模型列表是可交互表格：

- 当前行整行高亮。
- `Space` 检测当前模型健康。
- `Ctrl+A` 检测全部模型。
- `d` 删除健康检测失败且非当前默认的模型。
- `Enter` 查看详情。

### 备份删除选择器

备份删除使用多选列表：

- `Space` 选中/取消当前备份。
- `Ctrl+A` 全选/清空。
- `Enter` 删除选中项或当前项。
- 删除前进入二次确认页，按 `y` 确认。

## 9. 关键流程

## 9.0 启动前置检查流程

```text
启动 codex-model-admin
  -> 检查 root 权限
  -> 检查 codex / cc-switch / node / npm / python3 / systemd / curl / tar
  -> 已安装项显示路径和版本
  -> 缺失关键项时进入环境检查 / 安装页面
  -> 用户确认安装缺失项
  -> 安装 Codex CLI 或 cc-switch CLI
  -> 验证 command -v 和 --version
  -> 继续进入主菜单
```

安装失败时：

```text
显示失败项
  -> 显示日志路径
  -> 保留已成功安装项
  -> 阻止执行依赖该组件的功能
```

## 9.1 新增模型流程

```text
输入模型信息，或从供应商 /v1/models 拉取并多选
  -> 健康检测选中模型
  -> 过滤失败模型
  -> 跳过已存在模型
  -> 备份
  -> 写 provider modelCatalog
  -> 写 Codex catalog
  -> 可选写 profile
  -> 可选设默认模型
  -> 重启代理
  -> 刷新 UI
```

## 9.2 删除模型流程

```text
选择模型
  -> 检查是否默认模型
  -> 检查是否最后一个模型
  -> 二次确认
  -> 备份
  -> 删除 provider modelCatalog
  -> 删除 Codex catalog
  -> 可选删除 profile
  -> 如果需要，选择新默认模型
  -> 重启代理
  -> 刷新 UI
```

## 9.3 新增供应商流程

```text
输入 provider 信息
  -> 从 base_url 拉取默认模型或手动输入
  -> 检测默认模型健康
  -> 备份
  -> 创建 provider
  -> 写 modelCatalog
  -> 可选切换 provider
  -> 如果切换，生成 Codex catalog
  -> 重启代理
  -> 刷新 UI
```

## 9.3.1 修改供应商流程

```text
选择可管理 provider
  -> 载入现有 id/name/base_url/api_key/default_model/context/api_format
  -> 修改字段
  -> 检测默认模型健康
  -> 备份
  -> 更新 provider id 和 settings_config
  -> 如选择同步当前 provider，重写 Codex catalog/config
  -> 如选择重启，重启代理
  -> 刷新 UI
```

## 9.4 切换供应商流程

```text
从可管理 provider 列表选择 provider
  -> 检查 provider modelCatalog 不为空
  -> 备份
  -> cc-switch provider switch
  -> 生成 Codex catalog
  -> 更新 Codex config.toml model
  -> 重启代理
  -> 测试默认模型
```

## 9.6 删除备份流程

```text
列出备份
  -> 单选或多选备份
  -> 可用 Ctrl+A 全选/清空
  -> 二次确认
  -> 校验 backup_id 只能指向备份根目录直接子目录
  -> 删除备份目录
  -> 返回结果
```

## 9.5 修改代理流程

```text
输入 listen_address / listen_port
  -> 如果 listen_address = 0.0.0.0，显示安全警告
  -> 备份
  -> cc-switch proxy config
  -> 更新 systemd ExecStart
  -> 更新 Codex base_url
  -> systemctl daemon-reload
  -> 重启代理
  -> 测试 /v1/responses
```

## 9.7 服务器迁移流程

```text
新服务器执行 migrate-from-server.sh
  -> 校验参数、root、systemd、x86_64
  -> 确认覆盖目标配置
  -> 安装 Git/SSH/Python/Node.js/npm/curl/tar/pgrep
  -> BatchMode SSH 检查源服务器
  -> Git clone 或更新项目，拒绝覆盖脏工作树
  -> 安装 Codex CLI 和 cc-switch，并验证版本命令
  -> 读取源端代理 enabled/active 状态
  -> 暂停源端代理
  -> SIGSTOP 暂停 codex/Node app-server/codex-model-admin/独立 cc-switch
  -> 启动父进程监控 + 600 秒超时 watchdog
  -> SSH 流式生成归档
  -> SIGCONT 恢复源端进程
  -> 源端 EXIT trap 恢复代理
  -> 校验归档路径、cc-switch.db、config.toml
  -> 停止目标代理
  -> 移动目标原配置到回滚目录
  -> 提取源配置并恢复权限/SELinux context
  -> systemctl daemon-reload
  -> 镜像源端代理 enabled/active 状态
  -> 标记配置恢复完成
  -> doctor/provider/model/proxy 检查
  -> 上游健康检测失败只告警，不自动撤销迁移
```

## 10. 安全要求

- API key 默认脱敏显示。
- 显示完整 API key 必须二次确认。
- 删除 provider/model 必须二次确认。
- 自动安装 Codex CLI 或 cc-switch CLI 必须用户确认。
- 已存在二进制默认不覆盖。
- 修改代理为 `0.0.0.0` 必须显示风险提示。
- 写操作前必须备份。
- 不默认修改防火墙规则。
- 不在日志中输出完整 API key。
- 迁移脚本只允许 root 执行，并使用 `BatchMode=yes` 禁止 SSH 交互式密码回退。
- 迁移归档和回滚目录必须限制为 root 可访问；临时归档权限为 `0600`，工作目录和回滚目录权限为 `0700`。
- 归档提取前必须拒绝绝对路径和 `..` 路径穿越，并验证核心配置文件存在。
- 迁移脚本不得复制系统 SSH 私钥、Git 凭据、系统用户、防火墙或软件包数据库。
- 默认在成功后删除临时迁移归档；使用 `--keep-archive` 时必须提示归档可能包含 API Key、Codex 认证、npm registry 凭据和会话信息。
- 不终止源端 Codex/cc-switch 进程，只允许短时 SIGSTOP/SIGCONT；必须有独立 watchdog 防止永久暂停。
- 目标端旧配置将被替换，目标端旧 Codex/cc-switch 进程允许 TERM、超时后 KILL，但迁移脚本必须从独立 SSH shell 运行。

## 11. 错误处理

常见错误：

```text
Codex CLI 未安装
cc-switch CLI 未安装
Node/npm 不存在，无法安装 Codex CLI
curl/tar 不存在，无法安装 cc-switch CLI
cc-switch db 不存在
provider 不存在
modelCatalog 为空
Codex catalog 解析失败
systemd 服务重启失败
代理端口被占用
上游认证失败
上游模型不存在
Codex exec 超时
源服务器 SSH 免密登录失败
源服务器进程暂停或恢复失败
源服务器暂停 watchdog 超时
目标安装目录存在未提交修改或非 Git 文件
迁移归档损坏、缺少核心文件或包含路径穿越
目标配置恢复失败
源端代理未能自动恢复
```

错误展示要求：

- TUI 底部显示简短错误
- 详细错误写入日志
- 写操作失败时提示备份路径

日志路径建议：

```text
/root/.codex/model-admin.log
```

## 12. 验收标准

### 环境检查 / 安装

- 可以检测 Codex CLI 是否安装，并显示路径和版本
- 可以检测 cc-switch CLI 是否安装，并显示路径和版本
- 已安装时不重复安装，并显示 `✅`
- Codex CLI 缺失且 npm 可用时，可以自动安装并验证 `codex --version`
- cc-switch CLI 缺失且 curl/tar 可用时，可以自动安装并验证 `cc-switch --version`
- 缺失 Node/npm 时，Codex CLI 安装入口给出明确提示
- 安装失败时写入日志且不影响已安装组件

### 供应商

- 可以列出/新增/修改/删除/切换供应商
- 切换供应商后 `cc-switch -a codex provider current` 正确
- 切换供应商后 Codex catalog 同步为当前供应商模型
- `codex-official / OpenAI Official` 作为官方只读项展示，不允许切换或删除

### 模型

- 可以列出/新增/删除模型
- 可以从供应商拉取模型列表、筛选、多选并健康检测
- 可以在模型列表中检测当前或全部模型健康
- 可以直接删除健康检测失败且非当前默认的模型
- 新增模型后 `codex debug models` 可见
- `codex -m <model>` 可用
- `codex-model` 菜单自动出现新增模型

### 代理

- 可以配置 `127.0.0.1` 或 `0.0.0.0`
- 可以自定义端口
- 修改后 systemd 服务正常
- 修改后 `/root/.codex/config.toml` base_url 同步
- `/v1/responses` 测试通过

### Codex

- `codex debug models` 显示当前供应商模型
- `codex exec -m <model> "只回复 pong" </dev/null` 返回 `pong`

### 备份

- 每次写操作前生成备份
- 可从备份恢复到可用状态
- 可单选或多选删除备份
- 删除备份拒绝非法备份 ID 和路径穿越

### 服务器迁移

- 可在新服务器通过一个脚本完成基础依赖、Git clone、组件安装和持久化环境复制
- 必须使用 root 免密 SSH，SSH 交互认证失败时不进入迁移
- 源端存在活动 Codex 或独立 cc-switch 进程时自动暂停，归档后恢复进程和 systemd proxy 原状态
- SSH 归档 shell 被终止时 watchdog 自动恢复源端进程
- watchdog 超时时恢复进程、归档作废且迁移返回失败
- 归档包含 provider、模型、API Key、Codex 认证、全局 `/root/.codex/AGENTS.md`、rules、skills、memory 和会话状态
- 不复制二进制、日志、缓存、锁文件和旧备份
- 目标配置覆盖前生成时间戳回滚目录
- 配置恢复完成前发生错误、INT 或 TERM 时自动恢复目标原配置和服务状态
- 配置恢复完成后的健康检查失败只告警，保留迁移结果和回滚目录
- 迁移结束后源端 proxy 状态不变，目标端 proxy 状态与源端迁移前状态一致

### 完全卸载

- 支持 `--dry-run` 无副作用预览，并在真实执行前要求 `DELETE <hostname>` 或显式 `--yes`
- 检测到活动服务器迁移任务时拒绝执行；无活动进程的迁移锁作为残留清理
- 停止并禁用 cc-switch 相关服务，终止 Codex、cc-switch 和 codex-model-admin 进程
- 删除 Codex CLI、cc-switch、管理器入口、项目目录、持久化配置、认证、备份和迁移残留
- 删除操作只能命中脚本内的绝对路径白名单；自定义项目目录必须通过名称和标识文件校验
- 默认保留可能由其他 npm 软件共用的 `/root/.npmrc`，仅在 `--purge-npmrc` 下删除
- 不卸载 Node.js、npm、Python、Git、curl、tar、SSH、systemd 等共享组件
- 完成后验证服务、进程、命令、npm 包和配置路径均无残留

## 13. 第一版实现范围

必须实现：

- 环境检查 / 安装 / Doctor
- 供应商列出/新增/修改/删除/切换
- 模型列出/新增/删除/设为默认/创建 profile/健康检测
- 代理状态/配置地址/配置端口/重启/日志/测试
- 自动备份、恢复备份、删除备份
- 服务器间一键安装和完整 Codex/cc-switch 持久化环境迁移
- 完全卸载 Codex/cc-switch/管理器环境，并提供 dry-run 和强确认
- TUI 主菜单快捷键、聚焦表单、模型选择器、模型列表、备份多选删除

可延后：

- 供应商配额
- 用量统计
- 流式健康检查
- WebDAV 同步
- 图形化密钥轮换

## 14. 推荐命令兼容层

保留已有脚本：

```bash
codex-add-model
codex-model
```

新增 TUI：

```bash
codex-model-admin
```

未来也可支持非交互命令：

```bash
codex-model-admin doctor
codex-model-admin install all
codex-model-admin install codex
codex-model-admin install cc-switch
codex-model-admin provider list
codex-model-admin provider add
codex-model-admin provider update
codex-model-admin provider switch
codex-model-admin provider delete
codex-model-admin model list
codex-model-admin model add
codex-model-admin model set-default
codex-model-admin model delete
codex-model-admin proxy set
codex-model-admin backup create/list/restore/delete
scripts/migrate-from-server.sh <source-host> [--yes]
scripts/uninstall-codex-model-admin.sh [--dry-run|--yes] [--purge-npmrc]
```

## 15. 示例部署参考

一套典型部署可包含：

```text
Codex CLI: /usr/bin/codex, codex-cli 0.143.0
cc-switch CLI: /usr/local/bin/cc-switch, cc-switch 5.8.7
供应商: example-provider
Base URL: https://api.example.com/v1
代理: 127.0.0.1:15721
服务: cc-switch-codex-proxy.service
默认模型: example-model
模型:
  - example-model
  - example-model-fast
  - example-model-coder
```
