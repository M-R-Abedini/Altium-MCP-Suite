# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md) · [العربية](README.ar.md)

通过一个名为 **mcp altium suite** 的 MCP 连接，让 AI 读取和编辑 Altium 原理图、PCB 和库。Altium 保持打开，操作针对明确指定的项目；设计、旧版操作和库文件后端在同一连接内部协调运行。

**Windows · Python 3.12 · 已在 Altium Designer 26 上实测**

## 能做什么

- 读写打开的原理图：添加元件、画线、放置网络标签和电源端口、改参数、导出 BOM 和网表。
- 操作 PCB：移动元件、布线和过孔、检查布局间距、拼板。
- 设计审查：孤立网络标签、悬空端口、IC 未连接引脚、缺失去耦电容、位号冲突、偏离网格的元件——一次执行 33 项 lint 检查，可选 DRC。
- 四个只读工具做连接检查：引脚-网络映射快照（SHA-256 摘要）、逐引脚约定（期望网络，或注明原因的有意悬空）、前后 diff、原理图↔PCB 焊盘双向一致性。
- 计算线宽、阻抗和长度预算。只是计算器，不是信号完整性证明。
- 读写 `.SchLib` / `.PcbLib` 库文件。
- 搜索 ChilaBoard 公共目录并下载固定版本的库，保留来源、哈希和许可证；暂存从 SnapMagic/SamacSys 官方渠道获得的本地文件。尚未实现这两家服务的直接 API 或 EPW 转换。[云库工作流程](review/CLOUD_LIBRARIES_2026-10-27.md)。
- `$altium-circuit-design` 技能覆盖需求、数据手册、符号/封装核验、结构化原理图计划和连接检查。下载成功不等于引脚映射或封装正确。
- 通过唯一标识管理原生装配变体，批量更新元件的装配状态并导出元件矩阵；修改前备份项目，修改后通过 Altium 接口回读验证。参见[变体工作流程](review/STORAGE_VARIANTS_2026-10-08.md)。
- 两个桥，一个脚本引擎：共享锁让两个 Python 桥不会抢 Altium 的脚本引擎；每个请求带 ID，过期的旧响应不会被接受。
- 多客户端并发：共享 FIFO 队列顺序执行 Altium 命令，一次遍历读取元件、网络和焊盘。客户端可并行分析同一快照；快照仅代表采集时的状态，不能作为实时编辑的前置条件。[用法与验证](review/CONCURRENT_CLIENTS_2026-10-14.md)。
- EDA 与旧版桥使用不同的脚本项目名。EDA 启动时关闭经过验证的旧生成项目，保留已编辑的源文件。[生命周期验证](review/SCRIPT_PROJECT_LIFECYCLE_2026-10-10.md)。

## 还在开发中的功能

- 辅助布局：批量布局和间距检查可用，更精细的布局工具还在做。
- 桥的稳定性：持续修复 DelphiScript 故障，并验证异常处理与恢复行为。
- 目前只在 Altium Designer 26 上实测过，其他版本还没测。

## 当前限制

- 批量编辑失败后可能留下部分修改；合并撤销步骤不保证完整回滚。大批量编辑前请保存备份。

- 实验性质。某些操作可能让 Altium 的 DelphiScript 引擎崩溃、轮询停掉；让 AI 改图之前先备份。
- 执行命令期间，原生保存和脚本按钮可能需要等待。空闲约两秒后释放引擎，后台 ping 不延长此期限；下一次协调工具调用会自动重启轮询。
- EDA JSON 参数中高于 U+00FF 的字符（Ω、中文）在发送前被拒绝，避免静默替换为 `?`；尚未实现完整 Unicode 传输。
- EDA 传输保留 UNC 路径；共享目录的访问取决于 Windows 权限。
- `proj_sync_pcb` 必须显式设置 `allow_modal=True`；原理图到 PCB 的 ECO 仍会打开交互对话框。
- 连接检查只查引脚分配，不查铜。你没喂给它的数据，它看不见。
- 模态框阻塞脚本命令，但 Win32 对话框诊断仍可使用。处理器忙碌时拒绝发送新命令。超时后撤回尚未读取的请求；已开始的编辑无法取消，重试前应检查设计。

## 安装

### Codex 插件

插件将统一 MCP 连接、电路设计技能和环境初始化流程一起提供。[插件指南](PLUGIN_PACKAGE.md)说明实际构建、从本地 marketplace 安装和环境诊断命令。安装后打开新对话并完成初始化。插件不包含 Altium 软件或许可证。不要同时启用手动连接和同一插件的连接。

### 手动连接及其他客户端

命令超时或引擎故障后的脚本所有权处理，参见[传输检查与手动恢复](review/TRANSPORT_FINDINGS_2026-10-08.md)。

在 Windows 上安装 Git、Python 3.12 和 Altium。把可执行文件路径换成你自己的：

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

设置程序现在在 `mcp.local.json`（或 `codex.local.toml`）中只生成一个 `mcp-altium-suite` 连接。将其合并到客户端配置并重新连接。后端的私有配置保存在 `suite-backends.local.json`。在 Altium 打开项目后，用 `suite_catalog` 发现 `app_context`，再用 `suite_invoke` 调用确切名称 `eda__app_context`。

默认 `--toolset minimal` 只公布 `suite_catalog`、`suite_invoke` 和 `suite_status`。用 `suite_catalog(backend="eda", query="...", with_schema=true)` 获取参数，再用 `suite_invoke(name="eda__...", arguments={...})` 执行。命名空间为 `eda__`、`legacy__`、`libraries__`、`cloud__`。所有操作仍可用，参数在转发前验证。`--toolset full` 公布全部工具；`--separate-servers` 显式生成旧版多连接配置。[统一连接及迁移](review/UNIFIED_SUITE_2026-10-27.md)。

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

从源码构建库后端需要锁定版本的 Rust 工具链和 Visual Studio C++ 构建工具；已有可执行文件可用 `--library-binary` 指定。多个目录可重复 `--library-dir`。设置保留已有目录并加入专用云库缓存目录。Altium 许可证与供应商账户不包含在内。

## 问题与贡献

安装和使用问题请到 [Q&A](https://github.com/M-R-Abedini/Altium-MCP-Suite/discussions/categories/q-a) 提问。报告错误和功能建议请使用 [Issues 表单](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues/new/choose)。[英文贡献指南](../CONTRIBUTING.md)介绍开发环境、测试、翻译和共同作者署名。

## 许可

协调代码采用 MIT；EDA 后端采用 Apache-2.0，旧版后端采用 MIT，Rust 库后端采用 GPL-3.0-or-later。参见[许可范围与例外](../THIRD_PARTY_NOTICES.md)及[上游快照](../UPSTREAM.json)。
