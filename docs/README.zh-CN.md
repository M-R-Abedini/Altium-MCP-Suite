# Altium MCP Suite

**[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md)**

[![Suite tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml)

将兼容 MCP 的 AI 助手连接到 Altium Designer，用于原理图、PCB 和库文件操作。本套件整合三个开源服务器，并提供统一的脚本执行协调、连接恢复和明确的引脚连接检查。

**Windows · 已在 Altium Designer 26 上测试实时集成 · Python 3.12**

## 功能

| 领域 | 功能 |
| --- | --- |
| 原理图 | 读取元件和网络，放置与修改对象，从结构化设计计划生成原理图，检查连接与布局 |
| PCB | 读取几何数据和设计规则，放置元件与铜线，规划布线，计算线宽、阻抗和长度预算 |
| 元件库 | 创建和编辑符号及封装；将封装几何数据与依据制造商资料建立的焊盘规范进行比较 |
| 连接验证 | 生成引脚到网络的快照，检查预期网络与明确的 NC，比较版本，核对原理图引脚与 PCB 焊盘 |
| 检查与输出 | 生成可视化预览、检查报告和 BOM；调用 Altium 输出作业 |

连接验证保留完整的层级网络名称，并拒绝无效输入。即使已提供的连接全部匹配，不完整的数据也不能通过验证。参见[示例与输入要求](../CONNECTIVITY_VERIFICATION.md)。

## 相比单独部署原项目，套件增加了什么

设计工具来自所包含的原项目。本仓库新增统一部署与执行协调层，以及四个连接验证工具。

| 单独使用原项目 | 使用本套件 |
| --- | --- |
| 分别配置各服务器入口 | 一个 Windows 安装流程生成 MCP JSON 和 Codex TOML 配置 |
| 各 Python 桥接器独立管理脚本执行 | 通过共享锁和控制权交接，协调 Altium 脚本引擎的使用 |
| 各桥接器独立管理连接生命周期 | 健康检查与受保护的恢复流程；编辑命令超时后不会自动重放 |
| 使用各自的验证工具 | 统一使用快照、引脚契约、连接差异和原理图/PCB 焊盘双向检查 |
| 分别管理源码和测试说明 | 记录上游提交、保留原许可证，并提供套件级 CI |

此比较仅针对仓库内的源码快照，不代表其他 MCP 的所有最新版本。原项目仍可独立使用：

| 项目 | 用途 | 许可证 |
| --- | --- | --- |
| [eda-agent](../eda-agent/README.md) | 主要的原理图、PCB 和库自动化工具 | Apache-2.0 |
| [coffeenmusic/altium-mcp](../coffeenmusic/README.md) | 通过旧版桥接器提供补充 Altium 命令 | MIT |
| [altium-designer-mcp](../altium-designer-mcp/README.md) | 独立的 Rust 服务器，用于 Altium 库文件操作 | GPL-3.0-or-later |

各原项目的仓库地址与确切提交记录在 [UPSTREAM.json](../UPSTREAM.json) 中。

## 快速开始

在 Windows 上安装 Git、Python 3.12 和 Altium Designer。实时设计操作需要可正常运行的 Altium 安装。

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

请替换为实际的 Altium 可执行文件路径。安装程序生成 `mcp.local.json` 和 `codex.local.toml`；将所需条目合并到客户端配置后，重新启动 MCP 连接。现有客户端配置不会被覆盖。

在 Altium 中打开项目，编辑前让助手调用 `app_context`，确认当前文档及桥接器/脚本状态。使用生成的协调入口，不要同时运行另一份旧桥接器。

### 可选：库文件服务器

Rust 服务器需要单独构建。项目固定使用 Rust 1.95.0 工具链；在 Windows 上还需要 Visual Studio C++ 构建工具。

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

库路径必须指向已存在的目录。可以重复使用 `--library-dir`；安装程序会将库服务器加入生成的配置。构建后的可执行文件位于 `altium-designer-mcp/target/release/altium-designer-mcp.exe`。

### 减少工具发现占用的上下文

如果客户端把全部工具 schema 加载到上下文中，可在生成配置的 **eda-agent** 参数列表中追加 `--toolset`、`minimal`。客户端只看到用于发现和执行工具的 `tool_catalog` 与 `tool_invoke`，仍可访问完整工具集。若不需要本地检查面板，可追加 `--no-dashboard`。

## 使用限制

- Altium 实时控制仅支持 Windows，已在 AD26 上测试；本套件尚未验证其他 Altium 版本。
- 模态对话框可能阻塞桥接器。完成或关闭对话框后再重试。编辑命令超时后，先检查设计状态：修改可能已经生效。
- 连接检查通过不等于 ERC/DRC、封装几何、实际铜连接或制造准备全部通过。调用方必须确认输入数据完整且为最新状态。
- 部分工具只规划或计算修改，另一些工具会实际应用修改。请检查返回结果与作用范围。本套件不保证完全自主生成可制造的设计。

## 文档与验证

- [连接验证](../CONNECTIVITY_VERIFICATION.md)：工具输入、示例与通过条件。
- [检查报告](../REVIEW.md)：发布验证记录与已知限制。
- [本地修改](../MODIFICATIONS.md)：对原项目所做的修改。
- [CI 工作流](../.github/workflows/tests.yml)：Python 回归/发现测试与 Rust 测试。

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

stdio 冒烟测试检查服务器启动与四个连接工具。可选的 `--live` 模式会停止并恢复已打开的 Altium 桥接器，请在当前操作结束后运行。CI 不会在真实 Altium 会话中验证全部工具。

对于可复现的问题，请[提交 issue](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues)，并提供 Altium 版本、工具名称、复现步骤和返回的错误。

## 致谢与许可证

本发行版保留原作者署名和许可证。协调代码与新增连接验证模块采用 [MIT](../LICENSE)，但该许可证不会取代所包含项目的 Apache-2.0、MIT 或 GPL 许可证。重新分发组件前，请查阅[源码来源](../UPSTREAM.json)。
