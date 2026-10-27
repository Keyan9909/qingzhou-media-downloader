# 轻舟下载器 CLI 使用说明

CLI 的目标是让 AI、脚本和其他软件能够完整控制下载器，而不依赖鼠标坐标。所有命令只在标准输出返回 UTF-8 JSON。

## 1. 调用方式

源码版：

```powershell
.\media-cli.cmd <命令> [参数]
```

商业版：

```powershell
.\media-cli.exe <命令> [参数]
```

返回码：

- `0`：命令成功。
- `2`：可预期的业务错误或参数错误。
- `130`：用户中断。

错误结构固定为：

```json
{
  "ok": false,
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "面向用户的中文说明",
    "details": {}
  }
}
```

AI 应先判断进程返回码和 `ok`，再读取其他字段。不得从日志猜测成功。

## 2. 能力发现与诊断

```powershell
.\media-cli.exe platforms
.\media-cli.exe doctor
.\media-cli.exe status
```

- `platforms`：列出平台、支持内容和所用引擎。
- `doctor`：检查 Electron、yt-dlp、ffmpeg 和数据目录。
- `status`：读取内置浏览器、当前页面、浏览器下载和直接下载状态。

## 3. 识别分享文本

```powershell
.\media-cli.exe detect "复制这段文案 https://www.bilibili.com/video/BV..."
.\media-cli.exe normalize "分享文本 https://v.douyin.com/..."
```

`detect` 返回 `platform.id`、中文名、是否支持、支持内容及下载引擎。

## 4. 设置

读取：

```powershell
.\media-cli.exe config-get
```

设置统一默认目录：

```powershell
.\media-cli.exe config-set --default-output-dir "D:\媒体下载"
```

设置某个平台独立目录：

```powershell
.\media-cli.exe config-set --platform-dir youtube "D:\媒体下载\YouTube"
.\media-cli.exe config-set --platform-dir xiaohongshu "D:\媒体下载\小红书"
```

恢复某个平台使用默认目录：

```powershell
.\media-cli.exe config-set --platform-dir youtube ""
```

其他偏好：

```powershell
.\media-cli.exe config-set --quality 1080
.\media-cli.exe config-set --browser-cookies firefox
.\media-cli.exe config-set --show-browser
.\media-cli.exe config-set --no-show-browser
.\media-cli.exe config-set --accept-terms
```

平台 ID 只能是：

```text
youtube  douyin  xiaohongshu  bilibili  xiaoetong
```

## 5. 推荐的一键下载命令

YouTube 使用前请先在 Firefox 登录 YouTube、确认目标视频能正常播放，然后完全退出 Firefox。推荐命令：

```powershell
.\media-cli.exe auto-download "YouTube链接" --browser-cookies firefox --confirm-rights
```

Firefox 仍在运行时，登录数据库可能被占用；遇到 Cookie 读取错误时先关闭全部 Firefox 进程再重试。

```powershell
.\media-cli.exe auto-download "作品链接或分享文本" --confirm-rights
```

它会：

1. 提取并规范化链接。
2. 识别平台。
3. 读取平台独立目录或默认目录。
4. 选择直接下载或内置浏览器引擎。
5. 完成后写入结构化历史。

常用参数：

```powershell
--quality best|2160|1440|1080|720|480|360
--platform youtube|douyin|xiaohongshu|bilibili|xiaoetong
--output-dir "D:\临时目录"
--browser-cookies firefox|edge|chrome
--cookie-file "D:\cookies.txt"
--show-browser
--no-show-browser
--media-timeout 60
--download-timeout 1800
```

涉及下载的命令必须显式提供 `--confirm-rights`。这只是权限确认，不代表软件替用户判断内容是否合法。

示例：

```powershell
.\media-cli.exe auto-download "https://youtu.be/VIDEO_ID" --quality 1080 --browser-cookies firefox --confirm-rights
.\media-cli.exe auto-download "https://www.bilibili.com/video/BV..." --quality best --confirm-rights
.\media-cli.exe auto-download "小红书分享文案 https://xhslink.com/..." --show-browser --confirm-rights
.\media-cli.exe auto-download "抖音分享文案 https://v.douyin.com/..." --show-browser --confirm-rights
.\media-cli.exe auto-download "https://课程域名.xiaoeknow.com/..." --show-browser --confirm-rights
.\media-cli.exe auto-download "https://商家定制域名/课程播放链接" --platform xiaoetong --show-browser --confirm-rights
```

## 6. 细粒度命令

直接下载：

```powershell
.\media-cli.exe download-direct "链接" --quality 1080 --confirm-rights
.\media-cli.exe youtube-metadata "YouTube 链接"
.\media-cli.exe youtube-download "YouTube 链接" --quality 1080 --confirm-rights
```

内置浏览器：

```powershell
.\media-cli.exe browser-open "https://www.douyin.com/" --show
.\media-cli.exe current
.\media-cli.exe download-current --confirm-rights
.\media-cli.exe close-browser
```

小红书：

```powershell
.\media-cli.exe xiaohongshu-download "笔记链接" --show-browser --confirm-rights
```

抖音检索接口（适合 AI 批量研究，但每次下载仍需合法权限）：

```powershell
.\media-cli.exe search "关键词" --limit 20 --show-browser
.\media-cli.exe profile "抖音主页链接" --limit 50 --show-browser
```

## 7. 历史记录

```powershell
.\media-cli.exe history --limit 20
.\media-cli.exe history-clear --yes
```

清空历史不会删除已下载文件。AI 不应在用户没有明确要求时调用 `history-clear`。

## 8. AI 控制建议

一个稳妥的 AI 流程是：

1. `doctor`，确认组件可用。
2. `detect`，确认是支持平台。
3. `config-get`，向用户说明将保存到哪里。
4. 如果链接需要登录，使用 `browser-open --show`，由用户本人完成登录或验证码。
5. 得到用户下载授权后调用 `auto-download --confirm-rights`。
6. 检查返回码、`ok` 与 `output`。
7. 用 `history` 或 `status` 做一次结果核对。

AI 不应：

- 自动接受或绕过验证码、付费墙、DRM 或账号权限。
- 读取、显示、导出或上传 Cookie。
- 未经用户授权清空历史或删除下载文件。
- 把命令启动成功当作下载完成。
