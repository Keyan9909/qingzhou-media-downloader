# 轻舟下载器 Qingzhou Media Downloader

一个面向 Windows 的本地媒体保存工具，提供图形界面和稳定的 JSON CLI。它只适用于用户自己拥有、已获明确授权，或法律允许保存的内容。

## 支持范围

| 平台 | 内容 | 方式 |
|---|---|---|
| YouTube | 单视频、Shorts、直播回放 | yt-dlp |
| 哔哩哔哩 | 单视频、分 P 视频 | yt-dlp |
| 抖音 | 单条视频 | 本地浏览器辅助 |
| 小红书 | 视频笔记、图文笔记 | 本地浏览器辅助 |
| 小鹅通 | 用户有权离线保存的课程 | 本地浏览器辅助 |

平台、登录状态、地区、付费权限、DRM 和内容下架都会影响可用性。本项目不会绕过 DRM、付费墙或访问控制，也不保证所有链接永久可用。

## 安全与隐私

- 设置、任务记录、浏览器资料和缓存保存在 `%LOCALAPPDATA%\QingzhouMediaDownloader`，不在源码目录中。
- 仓库不包含 Cookie、账号、下载历史、浏览器 Profile、日志、媒体文件或预打包第三方运行时。
- 不要把密码、验证码、Cookie、私钥或 API Key 提交到 Issue。
- 下载命令要求显式传入 `--confirm-rights`。

## 从源码运行

需要 Windows、Python 3.11+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

也可以双击 `启动轻舟下载器.vbs`。

## CLI

```powershell
.\media-cli.cmd doctor
.\media-cli.cmd platforms
.\media-cli.cmd auto-download "作品页面链接" --confirm-rights
```

CLI 只向标准输出写 JSON。成功返回码为 `0`，业务失败返回码为 `2`。完整参数见 [CLI 使用说明](CLI使用说明.md)。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 打包提示

仓库只发布源码。若你自行打包并分发 yt-dlp、FFmpeg、Python 或其他二进制组件，必须遵守每个组件各自的许可证；详见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

## 合法使用

使用者必须确认自己有权保存相应内容。平台许可、权利人授权与适用法律始终优先于本工具。更多说明见[使用声明与免责声明](使用声明与免责声明.md)。

## License

本项目自有源码采用 MIT License。第三方依赖不受本许可证重新授权。
