# Dataset Audit Studio

面向图片训练集的本地优先审计与安全导出 WebUI。它让数据集的自动分析、人工复核和导出决策保持可追溯，同时不修改源图片或其配套文件。

项目将运行时、模型、缓存和任务数据限制在项目目录中；服务默认只监听 `127.0.0.1`。

## 能力概览

- 发现常见静态图片、GIF 和视频；GIF 与视频默认审计第一帧。
- 针对技术质量、美学/目标域、AI、OCR、水印、画风、重复和语义信息保存可追溯证据。
- 提供 `artist_concept`、`character_concept` 与 `general` 三种内置数据集 profile。
- 自动分析仅提供报告，不会改写 broad 基线；人工保留/排除决定可撤销，并保留审计链。
- 按子文件夹、类别、严重度或分档审阅风险、画风、重复和美学结果；语义重复分析默认按需启用。
- 以面积公式 `width * height >= resolution^2` 判断分辨率资格，支持 `512/768/1024/1216/1536` 档位。
- 通过预览、不可变输入快照、checkpoint、staging、碰撞检查、大小和 SHA-256 验证安全执行 copy export；已完成任务可后台重复导出。

## 工作方式

1. 在本机选择数据集并创建审计任务。
2. 审阅风险、画风、重复和美学证据，按需追加可撤销的人工决定。
3. 预览导出资格、文件夹映射和 warning。
4. 向新的空目录执行 copy export；源图片和同 stem 的 `.txt`/`.json` 文件保持原始字节不变。

自动分析不会删除、移动或原地改写源文件。输出目录拒绝覆盖已有内容，历史输出和既有 export-run snapshot 不会被复用。

## 技术组成

- 后端：Python 3.11、FastAPI、SQLAlchemy、Alembic 与本地受控模型运行时。
- 前端：React、TypeScript、Vite、Playwright。
- 质量门：Ruff、Pytest、前端单元测试、构建和项目内受控浏览器的 E2E 测试。

完整依赖、模型来源和许可证信息见 [`docs/THIRD_PARTY_NOTICES.md`](docs/THIRD_PARTY_NOTICES.md)。模型权重不随源码发布，按需下载并受各自许可证约束。

## 环境要求

- Windows 11
- PowerShell 7 或 Windows PowerShell 5.1
- NVIDIA 驱动和 CUDA 可用显卡（启用相关模型时）
- 足够的模型、缓存、staging 和输出磁盘空间

无需预装 Python、Node.js、uv 或全局包。安装脚本使用锁定版本，并将依赖写入项目内环境。

## 安装

双击根目录 `setup.bat` 即可安装项目内锁定的运行时和依赖。安装过程不会下载模型权重。

也可在 PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```

仅在明确更新依赖锁时运行：

```powershell
.\scripts\setup.ps1 -UpdateLocks
```

## 启动与停止

双击 `start_webui.bat`，或在 PowerShell 中运行：

```powershell
.\scripts\start.ps1
```

默认地址为 <http://127.0.0.1:7865>。使用其他端口：

```powershell
.\scripts\start.ps1 -Port 7866
```

关闭浏览器不会停止任务。安全停止前先暂停或终止运行中的任务，再运行 `stop_webui.bat`。

## 验证

```powershell
.\scripts\test.ps1
```

该入口检查环境隔离、Ruff、第三方依赖报告、Pytest、前端单元测试与构建，以及项目配置的 E2E 门。

## 项目结构

```text
backend/       FastAPI 应用、领域服务、数据访问和模型适配器
frontend/      React/Vite WebUI、前端测试和 E2E 测试
scripts/       项目内环境安装、启动、停止、测试和验证脚本
tests/         后端合同、服务、架构和集成测试
docs/          第三方依赖与许可证声明
data/          本地任务和数据库，默认不纳入版本控制
models/        本地模型与缓存，默认不纳入版本控制
output/        本地导出结果，默认不纳入版本控制
```

## 数据与安全边界

- 扫描、审计和人工排除不修改或删除源图片、TXT、JSON 或 latent。
- 缓存和运行时路径在启动时接受项目边界检查；检测到全局 Python 或项目外模型缓存会拒绝启动。
- `data/`、`models/`、`output/`、项目内运行环境、测试产物和临时诊断目录均不纳入版本控制。

## 许可证

项目包含 GPL-3.0 的 LSNet 画师风格特征架构，因此按 **GPL-3.0-only** 发布。完整文本见 [`LICENSE`](LICENSE)。
