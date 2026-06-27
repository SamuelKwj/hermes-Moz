# Hermes Voice Desktop

一个本地语音桌面悬浮窗：按住说话，走本地 STT -> Hermes Gateway -> edge-tts 播放。

## 当前状态

- 业务链路已跑通：录音、识别、LLM 回复、TTS、播放。
- UI 已可用：桌面悬浮窗、语音波形、按住说话、对话展示。
- 产品化基础已补齐：健康检查、持久化设置、设置页、首次启动诊断、系统托盘、日志落盘、设备列表、依赖诊断、可配置端口、Windows 启动脚本。

## 环境要求

- Windows 10/11
- Python 3.11+
- ffmpeg 已加入 PATH
- Hermes Gateway 运行在 `http://127.0.0.1:8642`，默认密钥 `bridge-secret-key`

## 安装

```powershell
.\scripts\setup.ps1
```

如果 PowerShell 阻止脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

## 启动桌面版

```powershell
.\scripts\start.ps1
```

也可以手动启动：

```powershell
.\.venv\Scripts\python.exe launcher.py
```

## 只启动后端调试

```powershell
.\.venv\Scripts\python.exe backend\run_server.py
```

打开：

- UI: `http://127.0.0.1:8765`
- 健康检查: `http://127.0.0.1:8765/health`
- 诊断状态: `http://127.0.0.1:8765/api/status`
- 音频设备: `http://127.0.0.1:8765/api/devices`

## 打包

PyInstaller one-folder:

```powershell
.\scripts\build_pyinstaller.ps1
```

输出：

```text
dist\HermesVoice\HermesVoice.exe
```

Inno Setup 安装器：

```powershell
.\scripts\build_installer.ps1
```

输出：

```text
dist\installer\HermesVoiceSetup.exe
```

## 配置

用户设置保存在：

```text
%LOCALAPPDATA%\HermesVoiceWidget\settings.json
```

日志和模型缓存：

```text
%LOCALAPPDATA%\HermesVoiceWidget\logs\app.log
%LOCALAPPDATA%\HermesVoiceWidget\models
```

常用环境变量：

- `VOICE_WIDGET_HOST`: 默认 `127.0.0.1`
- `VOICE_WIDGET_PORT`: 默认 `8765`
- `HERMES_GATEWAY_URL`: 默认 `http://127.0.0.1:8642`
- `API_SERVER_KEY`: 默认 `bridge-secret-key`
- `VOICE_STT_MODEL`: 默认 `base`
- `VOICE_STT_LANG`: 默认 `zh`
- `VOICE_USE_CUDA`: 设为 `1` 时使用 CUDA
- `VOICE_TTS_VOICE`: 默认 `zh-CN-XiaoxiaoNeural`

## 产品化待办

1. 在干净 Windows 环境验证 WebView2、ffmpeg、麦克风权限。
2. 验证开始菜单、桌面快捷方式、开机启动和卸载残留。
3. 做日志导出按钮和错误报告包。
4. 根据目标机器决定是否预置 faster-whisper 模型和 ffmpeg。

更多打包说明见 `docs\packaging.md`。
