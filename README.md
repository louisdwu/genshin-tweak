# Genshin Tweak — 原神工具箱

单文件原生 exe（C# / WinForms / .NET Framework 4.x）。无 Python、无 PowerShell 依赖，双击即用。

## 功能

| 功能 | 说明 |
|------|------|
| 一键场景预设 | 🎬 极致省电看视频（GPU 300MHz/100W, CPU 20%）· 💻 多网页/IDE 办公（600MHz/120W, 65%）· 🔥 火力全开玩游戏（GPU 解锁/250W, CPU 100%） |
| 自定义限频 | GPU 时钟 / GPU 功耗墙 / CPU 占用上限 三滑条 |
| 游戏时停 | 让原神窗口进入系统"移动"模态循环冻结游戏线程；全局热键 **Alt+`** 切换 |
| 托盘常驻 | 托盘菜单可切预设、切换时停、退出时自动恢复硬件默认 |
| CSV 事件记录 | `genshin_tweak_log.csv`（exe 同目录），记录每次预设切换/时停 |

## 构建（Windows 上，零安装）

```bat
build.bat
```

使用系统自带 `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` 编译，
生成 `genshin-tweak.exe`。无需安装 .NET SDK / Visual Studio / Python。

## 运行

双击 `genshin-tweak.exe` → UAC 提权（exe 内嵌 `requireAdministrator` manifest）→ 主窗口。

- 关闭按钮 = 最小化到托盘；真正退出走托盘菜单 **退出并恢复硬件**
- GPU 限频需要 `nvidia-smi`（NVIDIA 显卡驱动自带）；无 NVIDIA 时 CPU 功能不受影响
- 退出时自动执行：`nvidia-smi -rgc` + `-pl 250` + CPU `PROCTHROTTLEMAX 100`

## 时停原理

`WM_SYSCOMMAND + SC_MOVE` 让窗口进入键盘移动模态循环（等同右键标题栏→"移动"），
`DefWindowProc` 阻塞游戏主线程 → 游戏逻辑冻结；恢复时发 ESC + `WM_CANCELMODE`。
全屏时会先 `Alt+Enter` 切窗口化，恢复时自动切回。

## 目录结构

```
genshin-tweak/
├── src/genshin-tweak.cs   ← 全部源码（单文件）
├── src/app.manifest       ← requireAdministrator UAC 清单
├── src/app.ico            ← 程序图标 (csc /win32icon 嵌入 exe, 标题栏/托盘/资源管理器)
├── build.bat              ← 一键编译（系统自带 csc.exe）
├── genshin-tweak.exe      ← 构建产物（不入库）
├── _check_eol.py          ← 行尾/BOM 检查（Linux/Windows 通用）
└── .gitattributes         ← 行尾策略
```

## 开发注意（Linux → Windows 工作流）

1. **行尾**：Linux 上编辑后必须保证 `.bat/.cs/.manifest` 为 CRLF。
   提交前运行 `python3 _check_eol.py`（`.cs` 额外要求 UTF-8 BOM，否则 csc.exe 按 ANSI 解析中文会乱码）。
2. **C# 5 语法**：csc.exe (4.0.30319) 不支持 C# 6+（无 `$""`、`?.`、`nameof`、表达式体成员）。
3. 中文文件名会导致 Windows 双击异常，所有文件/脚本名一律用英文。
