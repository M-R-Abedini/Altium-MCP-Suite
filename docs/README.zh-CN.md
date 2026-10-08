# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md) · [العربية](README.ar.md)

让 AI 读取和编辑你的 Altium 原理图、PCB 和库的 MCP 服务器。Altium 保持打开，AI 直接在你打开的项目上实时操作。

**Windows · Python 3.12 · 已在 Altium Designer 26 上实测**

## 能做什么

- 读写打开的原理图：添加元件、画线、放置网络标签和电源端口、改参数、导出 BOM 和网表。
- 操作 PCB：移动元件、布线和过孔、检查布局间距、拼板。
- 设计审查：孤立网络标签、悬空端口、IC 未连接引脚、缺失去耦电容、位号冲突、偏离网格的元件——或一次跑完 31 项 lint 检查。
- 四个只读工具做连接检查：引脚-网络映射快照（SHA-256 摘要）、逐引脚约定（期望网络，或注明原因的有意悬空）、前后 diff、原理图↔PCB 焊盘双向一致性。
- 计算线宽、阻抗和长度预算。只是计算器，不是信号完整性证明。
- 读写 `.SchLib` / `.PcbLib` 库文件。
- 两个桥，一个脚本引擎：共享锁让两个 Python 桥不会抢 Altium 的脚本引擎；每个请求带 ID，过期的旧响应不会被接受。

## 还在开发中的功能

- 辅助布局：批量布局和间距检查可用，更精细的布局工具还在做。
- 桥的稳定性：已知的 DelphiScript 崩溃都会修掉或加上防护，但新的还是会冒出来——这事没完。
- 目前只在 Altium Designer 26 上实测过，其他版本还没测。

## 当前限制

- 实验性质。某些操作可能让 Altium 的 DelphiScript 引擎崩溃、轮询停掉；让 AI 改图之前先备份。
- 轮询运行时，Altium 自带的一些脚本按钮会暂时没反应。用 Detach 把引擎还给 Altium。
- EDA JSON 参数中高于 U+00FF 的字符（Ω、中文）在发送前被拒绝，避免静默替换为 `?`；尚未实现完整 Unicode 传输。
- 网络路径要用映射盘符，UNC 路径打不开。
- `proj_sync_pcb` 必须显式设置 `allow_modal=True`；原理图到 PCB 的 ECO 仍会打开交互对话框。
- 连接检查只查引脚分配，不查铜。你没喂给它的数据，它看不见。
- 模态框阻塞脚本命令，但 Win32 对话框诊断仍可使用。处理器忙碌时拒绝发送新命令。超时后撤回尚未读取的请求；已开始的编辑无法取消，重试前应检查设计。

## 安装

在 Windows 上安装 Git、Python 3.12 和 Altium。把可执行文件路径换成你自己的：

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

把生成的 `mcp.local.json`（或 `codex.local.toml`）条目合并到 MCP 客户端配置，重启连接。在 Altium 里打开项目，调用 `app_context` 检查桥的状态。

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

库服务器是可选的（需要锁定版本的 Rust 工具链和 Visual Studio C++ 构建工具）。多个目录可重复 `--library-dir`。

## 许可

套件新代码 MIT。收录的项目保留各自许可——见 [UPSTREAM.json](../UPSTREAM.json)。
