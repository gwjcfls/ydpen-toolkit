# 待提交的 issue 草稿

我不能直接提交（没有这些仓库的写权限/账号）。**下面每份都是可以直接复制粘贴的 issue 正文**，
标题我也起好了。发之前建议把「我这边有…可以整理成 PR」那句话改成你自己的说法（或删掉）。

| 文件 | 目标仓库 | 标题 | 提交地址 |
|---|---|---|---|
| `01-penbili-x6pro-aarch64.md` | [56dz/PenBili](https://github.com/56dz/PenBili) | X6 Pro（YDPX6-2 / RK3562 / aarch64）无法运行：插件是 32 位 ARM，且播放器依赖 /dev/fb0 | https://github.com/56dz/PenBili/issues/new |
| `02-file-manager-upload.md` | [Mxzsan/file-manager-miniapp](https://github.com/Mxzsan/file-manager-miniapp) | 「文件互传」的 HTTP 服务只实现了 GET —— 网页上无法上传文件 | https://github.com/Mxzsan/file-manager-miniapp/issues/new |
| `03-doge-fs-jsapi.md` | [adogecheems/doge-reader](https://github.com/adogecheems/doge-reader) + [doge-comic](https://github.com/adogecheems/doge-comic) | PenOS 4.3.5 上缺少 `fs` jsapi —— 阅读器「本地文件」与漫画库空白 | https://github.com/adogecheems/doge-reader/issues/new<br>https://github.com/adogecheems/doge-comic/issues/new |
| `04-community-jsapi-notes.md` | PenUniverse 讨论区（或各自仓库 Wiki/docs） | 词典笔 jsapi 原生插件开发笔记 + X6 Pro 平台差异 | https://github.com/orgs/PenUniverse/discussions |

## 发的时候可以顺带附上的东西（都在工作区里）

| 用途 | 路径 |
|---|---|
| 我们的 `fileServer` 插件源码（支持上传） | `tools/build/fileserver_plugin.c` |
| 我们的 `fs` 插件源码 | `miniapps/fs-plugin/fs_plugin.c` |
| 我们的 PenBili `player` 插件源码（GStreamer 后端） | `miniapps/penbili/build/player_gs.c` |
| 改造好的安装包 | `miniapps/dist/file-manager-1.2.0-upload.amr`、`miniapps/dist/doge-*-fs.amr`、`miniapps/penbili/PenBili-2.7.0-aarch64.amr` |
| 证据截图 | `out/play_scaled.png`（PenBili 实机播放）、`out/size_*.png`（窗口尺寸标定）、`out/pattern_shot.png`（屏幕几何测试） |

## 提交顺序建议

1. **PenBili**（影响面最大：同机型用户都装不上/放不出画面）；
2. **文件管理器**（明确的功能缺失 + 我们已有可用实现）；
3. **Doge 阅读/漫画**（依赖固件版本，属于"环境要求未说明"）；
4. **社区技术笔记**（把这次摸清的 ABI 与平台差异沉淀下来，避免后人重踩）。
