# Genshin Tweak — 原神工具箱

单文件原生 exe（C# / WinForms / .NET Framework 4.x）。无 Python、无 PowerShell 依赖，双击即用。

> 已把 **Volume Controller** 项目的全部功能合并进来：前台应用独立音量 / 静音、全局热键、
> HUD 悬浮提示、多进程自动聚拢。原 Python 版本不再需要运行。

## 功能

| 功能 | 说明 |
|------|------|
| 一键场景预设 | 🎬 极致省电看视频（GPU 300MHz/100W, CPU 20%）· 💻 多网页/IDE 办公（600MHz/120W, 65%）· 🔥 火力全开玩游戏（GPU 解锁/250W, CPU 100%） |
| 自定义限频 | GPU 时钟 / GPU 功耗墙 / CPU 占用上限 三滑条 |
| 前台应用音量 | 只调**当前焦点应用**（Chrome / Spotify / 游戏）的音量，不动系统主音量、不影响后台应用。静音 **Pause**，音量 **Ctrl+Alt+↑ / Ctrl+Alt+↓**，长按连续调节 |
| 音量 HUD | 屏幕顶部居中悬浮卡片，实时显示应用名 / 百分比 / 进度条，原地刷新不闪烁，1.2 秒后自动消失 |
| 多进程聚拢 | Chrome / Electron 等多进程应用，按进程名自动同步所有音频会话 |
| 游戏时停 | 让原神窗口进入系统“移动”模态循环冻结游戏线程；全局热键 **Alt+`** 切换 |
| 托盘常驻 | 托盘菜单可切预设、切时停、调前台音量、退出时自动恢复硬件默认 |
| CSV 事件记录 | `genshin_tweak_log.csv`（exe 同目录），记录预设切换 / 时停 / 静音 |

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| **Pause** | 切换当前前台应用 静音 / 取消静音 |
| **Ctrl + Alt + ↑** | 前台应用音量 +（按住 0.25 秒后以 20 步/秒连续调节） |
| **Ctrl + Alt + ↓** | 前台应用音量 −（同上） |
| **Alt + `** | 游戏时停 / 恢复 |

- 音量热键可在主界面「前台应用音量」里直接修改：点一下输入框，按下新组合键即可（Esc 取消，
  10 秒无操作自动放弃），点「默认热键」一键恢复。热键保存在 `ui.ini`，下次启动沿用。
- 热键由低级键盘钩子（`WH_KEYBOARD_LL`）接管，按下时**不会再传给前台程序**；
  想恢复 Pause / PrintScreen 的原始用途，取消勾选「启用全局热键」即可。
- 默认静音键从原项目的 `PrintScreen` 改成 `Pause`：Win10/11 的截图（Snipping Tool）会抢占 PrintScreen。
  仍想用截图键，把热键框改成 `PrintScreen` 即可（此时会拦截截图）。

## 静音 / 音量没反应？

每次按静音键都会在「运行日志」里留一行，先看这行：

| 日志 | 含义 / 处理 |
|---|---|
| `[OK] chrome.exe · 3 个音频会话 → 已静音` | 正常。多进程应用会一次性把**全部**会话静音（只要有一个没静音就全静音） |
| `[WARN] xxx 没有可控制的音频会话` | 当前前台窗口不是正在放声音的进程（桌面 / 商店 UWP 应用 / 声音来自别的进程）。把焦点切到真正播放的窗口再按 |
| `[ERR] 静音切换失败: SetMute 0x...` | 驱动/会话拒绝，`0x...` 是 HRESULT |
| 完全没有新日志、也没有 HUD | 热键没触发：确认「启用全局热键」已勾选，启动日志里写着「钩子已安装」；也可用托盘菜单「静音前台应用」验证音频通路本身是否正常 |

热键要求**修饰键完全匹配**：静音是 Pause 时，按住 Ctrl/Shift 再按 Pause 不会触发；
音量组合是 Ctrl+Alt+↑/↓ 时，多按一个 Shift 也不会触发。

焦点停在本工具自己的窗口上时，热键会继续控制**你刚才离开的那个应用**（10 分钟内有效），
日志里会带 `(上次)` 前缀——所以在工具窗口里直接按 Pause 也能静音刚才的 Chrome。

## 构建（Windows 上，零安装）

```bat
build.bat
```

使用系统自带 `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` 编译 `src\*.cs`，
生成 `genshin-tweak.exe`。无需安装 .NET SDK / Visual Studio / Python。

## 运行

双击 `genshin-tweak.exe` → UAC 提权（exe 内嵌 `requireAdministrator` manifest）→ 主窗口。

- 关闭按钮 = 最小化到托盘；真正退出走托盘菜单 **退出并恢复硬件**
- GPU 限频需要 `nvidia-smi`（NVIDIA 显卡驱动自带）；无 NVIDIA 时 CPU 与音量功能不受影响
- 退出时自动执行：`nvidia-smi -rgc` + `-pl 250` + CPU `PROCTHROTTLEMAX 100`
- 音量功能基于 Windows Core Audio（WASAPI），Win7+ 自带，无额外依赖；
  以管理员身份运行时，对管理员权限运行的游戏同样有效（不受 UIPI 限制）

## 时停原理

`WM_SYSCOMMAND + SC_MOVE` 让窗口进入键盘移动模态循环（等同右键标题栏→"移动"），
`DefWindowProc` 阻塞游戏主线程 → 游戏逻辑冻结；恢复时发 ESC + `WM_CANCELMODE`。
全屏时会先 `Alt+Enter` 切窗口化，恢复时自动切回。

## 目录结构

```
genshin-tweak/
├── src/genshin-tweak.cs   ← 主程序：界面 / 托盘 / 功耗预设 / 时停 / 日志
├── src/CoreAudio.cs       ← WASAPI COM 互操作：会话枚举 + 前台进程音量/静音
├── src/VolumeKeys.cs      ← 低级键盘钩子：全局热键、长按连发、热键捕获
├── src/VolumeHud.cs       ← 音量 HUD 悬浮卡片（原地刷新）
├── src/app.manifest       ← requireAdministrator UAC 清单
├── src/app.ico            ← 程序图标 (csc /win32icon 嵌入 exe, 标题栏/托盘/资源管理器)
├── src/tray_*.ico         ← 各模式纯色托盘图标
├── build.bat              ← 一键编译（系统自带 csc.exe，编译 src\*.cs）
├── genshin-tweak.exe      ← 构建产物（不入库）
├── _check_eol.py          ← 行尾/BOM 检查（Linux/Windows 通用）
└── .gitattributes         ← 行尾策略
```

## 配置文件

`%APPDATA%\GenshinTweak\ui.ini`（窗口位置/大小、上次模式、自定义参数、音量热键）：

```ini
state=max
x=0
y=0
w=1920
h=1080
mode=game
gpu=2100
watt=250
cpu=100
vol=1
volstep=2
volmute=Pause
volup=Ctrl+Alt+Up
voldown=Ctrl+Alt+Down
```

## 合并说明（Volume Controller → 本工具）

原 `Volume Controller` 是 Python + pycaw + keyboard/pynput + Tkinter 的独立小工具，现全部用 C# 重写：

| 原实现 | 现实现 |
|--------|--------|
| pycaw（`IAudioSessionManager2` / `ISimpleAudioVolume`） | `src/CoreAudio.cs` 手写 COM 互操作，零第三方库 |
| pynput 全局监听 + 线程连发 | `src/VolumeKeys.cs` `WH_KEYBOARD_LL` 钩子 + 连发线程（0.25s 延迟 / 50ms 间隔） |
| Tkinter OSD 窗口 | `src/VolumeHud.cs` 双缓冲原位绘制，无闪烁、不抢焦点、点击穿透 |
| psutil 取前台进程 | `GetForegroundWindow` + `GetWindowThreadProcessId` + `Process` |
| 控制台/隐藏启动 bat+vbs | 直接并入 exe，托盘常驻 |
| PrintScreen 静音（会和截图冲突） | 默认改为 **Pause**，并支持界面自定义热键 |

原项目里的“休眠唤醒后重建热键”逻辑不再需要：钩子常驻本进程，睡眠唤醒不会丢失。

## 开发注意（Linux → Windows 工作流）

1. **行尾**：Linux 上编辑后必须保证 `.bat/.cs/.manifest` 为 CRLF。
   提交前运行 `python3 _check_eol.py`（`.cs` 额外要求 UTF-8 BOM，否则 csc.exe 按 ANSI 解析中文会乱码）。
2. **C# 5 语法**：csc.exe (4.0.30319) 不支持 C# 6+（无 `$""`、`?.`、`nameof`、表达式体成员）。
3. 中文文件名会导致 Windows 双击异常，所有文件/脚本名一律用英文。
4. Linux 侧可用 mono 做语法校验：`mcs -langversion:5 -target:winexe src/*.cs -r:System.Windows.Forms.dll ...`。
5. 新增 `.cs` 文件无需改 `build.bat`，它编译 `src\*.cs`。
