# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md)

用于读取和编辑 Altium 原理图、PCB 和库的 MCP 服务器集合。实时操作通过 Altium 的 DelphiScript 引擎执行；库文件操作由独立的 Rust 服务器处理。

**Windows · Python 3.12 · 实时集成已在 Altium Designer 26 上测试**

## 架构与功能

| 后端 | 范围 | 许可证 |
| --- | --- | --- |
| [eda-agent](../eda-agent/README.md) | 原理图对象和编译后的网络；PCB 元件、焊盘和铜；符号及封装编辑 | Apache-2.0 |
| [coffeenmusic/altium-mcp](../coffeenmusic/README.md) | 通过文件请求/响应桥执行其他 Altium 命令 | MIT |
| [altium-designer-mcp](../altium-designer-mcp/README.md) | 在配置的库目录内读写 `.SchLib` 和 `.PcbLib` | GPL-3.0-or-later |

布线功能包含离线 Manhattan A* 规划器，输出走线和过孔操作。线宽、阻抗及长度预算计算用于辅助设计判断，不构成信号完整性验证，也不能替代场求解器。封装审查将焊盘几何与调用者提供的制造商规格进行比较。

与单独运行[所收录的上游版本](../UPSTREAM.json)相比，本套件增加了：

- **脚本协调：**共享锁串行化两个 Python 桥的执行；旧桥接管脚本引擎时，暂停主桥轮询。
- **响应校验与恢复：**原子发布请求和请求 ID 防止接受过期响应。恢复前检查编辑器及桥状态；超时的编辑命令不会自动重放。
- **连接回归检查：**以下四个只读工具比较引脚/焊盘与网络的映射。保留完整层级网络名，例如 `/Camera0/RESET` 与 `/Camera1/RESET` 不会合并。

| 工具 | 检查内容 |
| --- | --- |
| `design_connectivity_snapshot` | 规范化元件/引脚/网络映射及 SHA-256 摘要 |
| `design_check_pin_contracts` | 每个引脚的预期网络，或注明原因的有意悬空 |
| `design_diff_connectivity` | 引脚增删及网络分配变化 |
| `design_check_schematic_pcb_parity` | 双向检查缺失/多余焊盘及引脚/焊盘网络不一致 |

这些检查由本套件新增，运行时不依赖 KiCad。

## 安装

在 Windows 上安装 Git、Python 3.12 和 Altium。将可执行文件路径替换为实际安装路径：

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

将生成的 `mcp.local.json` 或 `codex.local.toml` 条目合并到 MCP 客户端配置，然后重启连接。在 Altium 中打开项目，调用 `app_context` 检查活动文档与桥状态。

若要减少启动时的工具描述，向生成的 **eda-agent** 参数列表添加 `"--toolset", "minimal"`。通过 `tool_catalog` 和 `tool_invoke` 仍可访问完整工具集。

可选库文件服务器需要锁定版本的 Rust 工具链和 Visual Studio C++ 构建工具。指定已存在的库目录；多个目录可重复使用 `--library-dir`：

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

## 验证边界

重新编译后，采集未过滤的原理图网络及同一项目版本的 PCB 焊盘。`complete=true` 是调用者对覆盖范围的声明；工具无法发现源数据中遗漏的记录。标记为不完整的数据不能通过验证。参见[输入约定与示例](../CONNECTIVITY_VERIFICATION.md)。

引脚/焊盘网络一致性不验证铜连接、间距、差分对长度偏差、封装方向或 ERC/DRC。发布制造文件前，应执行并审查 Altium 原生检查。编辑命令超时后，先检查文档再重试；首次操作可能已生效。模态对话框可能阻塞实时执行。

## 测试与维护

```powershell
.\.venv\Scripts\python.exe -m pytest tests eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

stdio 冒烟测试使用合成数据检查服务器启动及四个连接工具。CI 不会在实时 Altium 中验证每条命令。参见[验证范围](../REVIEW.md)、[套件变更](../MODIFICATIONS.md)和 [CI 工作流](../.github/workflows/tests.yml)。报告问题时请提供 Altium 版本、工具名、复现步骤及返回错误。

协调代码和新增连接模块采用 [MIT](../LICENSE)。各收录项目保留原许可证和作者信息；参见[来源记录](../UPSTREAM.json)。
