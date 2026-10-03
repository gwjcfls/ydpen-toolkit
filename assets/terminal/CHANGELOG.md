# 更新日志 / CHANGELOG

PenTerm 从 **0.1.0** 到 **9.3.6** 的完整版本记录（共 48 个发布版本）。

每个版本的 `.amr` 都在 [`dist/`](dist/)，GitHub 上每个版本也各自带一个 Release（附件就是这个包）。

下表里的**包体 / 原生插件 / 页面字节码**都是从对应 `.amr` 里实测出来的：
插件 = `libs/*/libjsapi_term.so` 的大小，页面 = `Component.js.bin` 的大小。

## 版本速览

| 版本 | 包体 | 原生插件 | 页面字节码 | 条目 | 主要变化 |
|---|---:|---:|---:|---:|---|
| [`0.1.0`](dist/terminal-0.1.0.amr) | 17,023 | — | — | 4 | 第一个能装进框架的最小 miniapp（manifest + 页面），验证「自制包能被安装/启动」。 |
| [`0.2.0`](dist/terminal-0.2.0.amr) | 17,080 | — | — | 4 | 修 manifest 字段（appid/version/minPlatformVersion），页面能正常渲染。 |
| [`0.3.0`](dist/terminal-0.3.0.amr) | 20,489 | — | — | 5 | 加入按钮与文本输入试验，摸清页面生命周期（onShow / attach / 挂载时机）。 |
| [`0.4.0`](dist/terminal-0.4.0.amr) | 20,684 | — | — | 5 | 输入回调整理：系统键盘 startTextEdit 的最小可用路径。 |
| [`0.5.0`](dist/terminal-0.5.0.amr) | 20,732 | — | — | 5 | 页面布局按 960×266 适配（顶栏 + 内容 + 输入行）。 |
| [`0.6.0`](dist/terminal-0.6.0.amr) | 20,781 | — | — | 5 | 多次安装/卸载实验，确认参数形态。 |
| [`0.7.0`](dist/terminal-0.7.0.amr) | 18,613 | — | — | 4 | 精简包体；确认**覆盖安装不会重新解包原生库**，必须 uninstall → install。 |
| [`0.8.0`](dist/terminal-0.8.0.amr) | 18,777 | — | — | 4 | 安装流程稳定（打包脚本成型）。 |
| [`1.0.0`](dist/terminal-1.0.0.amr) | 57,619 | 46,920 | — | 7 | **第一个原生 jsapi 插件**：打通自定义 .so 的加载（custom_init_jsapis 必须 __a… |
| [`1.1.0`](dist/terminal-1.1.0.amr) | 76,885 | 68,120 | — | 7 | 插件里加 PTY（forkpty）+ 轮询式非阻塞读，把 shell 输出吐给页面。 |
| [`1.2.0`](dist/terminal-1.2.0.amr) | 77,182 | 68,120 | — | 7 | 会话结构整理（sid 管理、写入口 term.write）。 |
| [`1.3.0`](dist/terminal-1.3.0.amr) | 77,182 | 68,120 | — | 7 | reader 线程 + 环形缓冲（绝不跨线程回调 JS，避免框架黑屏）。 |
| [`2.0.0`](dist/terminal-2.0.0.amr) | 128,418 | 133,568 | — | 7 | **真 PTY shell 跑起来了**：/bin/sh -i + 输出显示；此时仍用框架文本渲染，列对不齐。 |
| [`2.1.0`](dist/terminal-2.1.0.amr) | 128,444 | 133,568 | — | 7 | 会话列表 / alive / kill 等 API 补齐。 |
| [`2.2.0`](dist/terminal-2.2.0.amr) | 128,922 | 133,568 | — | 7 | resize（TIOCSWINSZ）与窗口变化处理。 |
| [`2.3.0`](dist/terminal-2.3.0.amr) | 128,861 | 133,568 | — | 7 | 输出缓冲上限与丢帧策略。 |
| [`2.4.0`](dist/terminal-2.4.0.amr) | 129,379 | 133,728 | — | 7 | execSync（一次性执行等结果）加入，用于自检。 |
| [`2.5.0`](dist/terminal-2.5.0.amr) | 130,097 | 134,648 | — | 7 | 环境变量 / cwd 参数化。 |
| [`2.6.0`](dist/terminal-2.6.0.amr) | 130,120 | 134,648 | — | 7 | 启动顺序与错误码整理。 |
| [`2.7.0`](dist/terminal-2.7.0.amr) | 130,182 | 134,648 | — | 7 | 命令写入与回显处理。 |
| [`2.8.0`](dist/terminal-2.8.0.amr) | 130,216 | 134,664 | — | 7 | 插件体积优化（去调试符号）。 |
| [`2.9.0`](dist/terminal-2.9.0.amr) | 130,600 | 134,664 | — | 7 | 稳定版：本地 shell 基本可用（但显示不理想）。 |
| [`3.0.0`](dist/terminal-3.0.0.amr) | 130,550 | 134,664 | — | 7 | 输入通道打通：系统键盘 → 页面 → PTY。 |
| [`3.1.0`](dist/terminal-3.1.0.amr) | 130,563 | 134,696 | — | 7 | 快捷键条雏形（Enter/Tab/^C…）。 |
| [`3.2.0`](dist/terminal-3.2.0.amr) | 130,083 | 134,696 | — | 7 | 确认框架比例字体无法对齐（i×61=241px、M×61=790px），决定自绘。 |
| [`4.0.0`](dist/terminal-4.0.0.amr) | 137,702 | 140,832 | — | 7 | **改用原生渲染**：VT/ANSI 解析 → cols×rows 字符网格 → 8×16 点阵绘制 → PNG，页… |
| [`4.1.0`](dist/terminal-4.1.0.amr) | 138,094 | 140,832 | — | 7 | 滚动缓冲（回看历史行）。 |
| [`4.2.0`](dist/terminal-4.2.0.amr) | 138,770 | 140,832 | — | 7 | 光标渲染 + 属性（粗体/反显/颜色）。 |
| [`4.3.0`](dist/terminal-4.3.0.amr) | 138,214 | 140,832 | — | 7 | 布局调整：快捷键条可折叠，避免挤占输出区。 |
| [`4.4.0`](dist/terminal-4.4.0.amr) | 138,575 | 140,832 | — | 7 | SIGWINCH 联动：折叠/展开时重排 top。 |
| [`4.5.0`](dist/terminal-4.5.0.amr) | 139,024 | 140,832 | — | 7 | 渲染性能优化（脏行重绘 + PNG 编码优化）。 |
| [`5.0.0`](dist/terminal-5.0.0.amr) | 140,774 | 140,832 | — | 7 | 滚动回看 API（vtScroll / vtHistory）+ 自动回底。 |
| [`5.1.0`](dist/terminal-5.1.0.amr) | 140,773 | 140,832 | — | 7 | 滑动/上翻/下翻手势接上（含误触抑制）。 |
| [`6.0.0`](dist/terminal-6.0.0.amr) | 140,443 | 140,832 | — | 7 | **自研入口打通**：不再借用官方 app.js.bin，自己写 App/glue 启动流程。 |
| [`7.0.0`](dist/terminal-7.0.0.amr) | 141,435 | 140,832 | — | 8 | 入口 glue 完善（glueboot.js / gluetrace.c 作为探针工具）。 |
| [`7.1.0`](dist/terminal-7.1.0.amr) | 141,526 | 140,832 | — | 8 | 页面模块布局改为 pages/index/*.js.bin（框架按页面路径找模块）。 |
| [`7.3.0`](dist/terminal-7.3.0.amr) | 135,633 | 140,832 | 15,084 | 11 | 包结构定型（11 条目、含 Component.js.bin）；**SSH 客户端**加入（/bin/ssh -tt… |
| [`8.0.0`](dist/terminal-8.0.0.amr) | 136,360 | 140,832 | 15,084 | 11 | SSH 自动填密码（在 reader 线程里匹配 password 提示）+ 一键在笔上起 sshd。 |
| [`9.0.0`](dist/terminal-9.0.0.amr) | 136,946 | 140,832 | 15,157 | 11 | **完全自研 entry 稳定版**：挂载调用必须写在基类页面里（不是页面类），可独立启动。 |
| [`9.1.0`](dist/terminal-9.1.0.amr) | 462,776 | 383,880 | 15,317 | 11 | **中文支持**：16×16 CJK 点阵表加入插件（插件 141KB → 384KB），全角字符双宽、标点/制表符… |
| [`9.2.0`](dist/terminal-9.2.0.amr) | 453,544 | 383,880 | 15,317 | 11 | CJK 字形表按常用度裁剪压缩（包体 462.8KB → 453.5KB），渲染不变。 |
| [`9.3.0`](dist/terminal-9.3.0.amr) | 465,395 | 397,296 | 19,851 | 11 | **命令历史 + 常用命令**：新增 /userdisk/Favorite/PenTerm/{history,fav… |
| [`9.3.1`](dist/terminal-9.3.1.amr) | 465,391 | 397,296 | 19,848 | 11 | 删除按钮 ✕ 在框架字体里是方框(tofu) → 换 ASCII x；面板加调试日志。 |
| [`9.3.2`](dist/terminal-9.3.2.amr) | 467,674 | 400,392 | 20,588 | 11 | 点终端画面**不再**呼出键盘（只有下方输入框呼出）；点快捷键条 Up/Dn 后把 shell 命令行内容载入输入框… |
| [`9.3.3`](dist/terminal-9.3.3.amr) | 467,673 | 400,392 | 20,588 | 11 | 交互收尾：提示文案改为「点下方输入框输入」；清理无用代码（histPrev/histIdx）。 |
| [`9.3.4`](dist/terminal-9.3.4.amr) | 467,677 | 400,392 | 20,598 | 11 | 尝试用 flex 把 × 撑到行右侧 —— 该框架里 text 的 flex 不生效，未达成（记录在案）。 |
| [`9.3.5`](dist/terminal-9.3.5.amr) | 467,689 | 400,392 | 20,629 | 11 | × 改用**绝对定位钉在行最右端**，与 ▶ 隔开整行，避免误触。 |
| [`9.3.6`](dist/terminal-9.3.6.amr) | 467,821 | 400,392 | 20,939 | 11 | **修 bug**：Up/Dn 载入后 shell 原命令行仍有同一条命令，点「发送」会被追加成 `toptop`（… |

## 阶段

| 阶段 | 主题 | 说明 |
|---|---|---|
| 0.x | 起步 | 摸清 miniapp 包结构与生命周期，能装、能启动、能收输入。 |
| 1.x | 原生插件 | 写出第一个 jsapi 插件，打通自定义 .so 与 PTY。 |
| 2.x | 真 PTY | 本地 shell 真正跑起来（显示仍不理想）。 |
| 3.x | 输入通道 | 系统键盘接上，快捷键条出现；确认框架字体无法对齐。 |
| 4.x | 自绘渲染 | VT 解析 + 点阵字体 + PNG，终端画面列对齐。 |
| 5.x | 滚动回看 | 历史行回看与手势。 |
| 6.x | 自研入口 | 脱离官方 app.js.bin。 |
| 7.x | 结构定型 | 页面模块路径、包结构、SSH 客户端。 |
| 8.x | SSH 完善 | 自动填密码、一键 sshd。 |
| 9.0 | 自研入口稳定 | 挂载写在基类页面里，可独立启动。 |
| 9.1–9.2 | 中文 | 16×16 CJK 点阵，全角双宽；9.2 压缩字库。 |
| 9.3 | 历史与常用命令 | 全部历史可查看/编辑、常用命令收藏、交互与 bug 修复。 |

## 逐版本明细

### 0.1.0

- 实测：包体 **17,023 B**，4 个条目，`md5:5a9f9761…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：第一个能装进框架的最小 miniapp（manifest + 页面），验证「自制包能被安装/启动」。
- 下载：[`terminal-0.1.0.amr`](dist/terminal-0.1.0.amr)

### 0.2.0

- 实测：包体 **17,080 B**，4 个条目，`md5:be96b25b…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：修 manifest 字段（appid/version/minPlatformVersion），页面能正常渲染。
- 下载：[`terminal-0.2.0.amr`](dist/terminal-0.2.0.amr)

### 0.3.0

- 实测：包体 **20,489 B**，5 个条目，`md5:06b80e73…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：加入按钮与文本输入试验，摸清页面生命周期（onShow / attach / 挂载时机）。
- 下载：[`terminal-0.3.0.amr`](dist/terminal-0.3.0.amr)

### 0.4.0

- 实测：包体 **20,684 B**，5 个条目，`md5:c205caaf…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：输入回调整理：系统键盘 startTextEdit 的最小可用路径。
- 下载：[`terminal-0.4.0.amr`](dist/terminal-0.4.0.amr)

### 0.5.0

- 实测：包体 **20,732 B**，5 个条目，`md5:3df40b6b…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：页面布局按 960×266 适配（顶栏 + 内容 + 输入行）。
- 下载：[`terminal-0.5.0.amr`](dist/terminal-0.5.0.amr)

### 0.6.0

- 实测：包体 **20,781 B**，5 个条目，`md5:4f9e6ce2…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：多次安装/卸载实验，确认参数形态。
- 下载：[`terminal-0.6.0.amr`](dist/terminal-0.6.0.amr)

### 0.7.0

- 实测：包体 **18,613 B**，4 个条目，`md5:2b7aba0b…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：精简包体；确认**覆盖安装不会重新解包原生库**，必须 uninstall → install。
- 下载：[`terminal-0.7.0.amr`](dist/terminal-0.7.0.amr)

### 0.8.0

- 实测：包体 **18,777 B**，4 个条目，`md5:579a32f4…`
- **无原生插件**（纯 JS 页面阶段）
- 变化：安装流程稳定（打包脚本成型）。
- 下载：[`terminal-0.8.0.amr`](dist/terminal-0.8.0.amr)

### 1.0.0

- 实测：包体 **57,619 B**，7 个条目，`md5:03022bc7…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **46,920 B**，ABI: arm64-orange、arm64
- 变化：★ **第一个原生 jsapi 插件**：打通自定义 .so 的加载（custom_init_jsapis 必须 __attribute__((visibility("default")))）与 JS 侧绑定。
- 下载：[`terminal-1.0.0.amr`](dist/terminal-1.0.0.amr)

### 1.1.0

- 实测：包体 **76,885 B**，7 个条目，`md5:a5ade5d8…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **68,120 B**，ABI: arm64-orange、arm64
- 变化：插件里加 PTY（forkpty）+ 轮询式非阻塞读，把 shell 输出吐给页面。
- 下载：[`terminal-1.1.0.amr`](dist/terminal-1.1.0.amr)

### 1.2.0

- 实测：包体 **77,182 B**，7 个条目，`md5:702177a9…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **68,120 B**，ABI: arm64-orange、arm64
- 变化：会话结构整理（sid 管理、写入口 term.write）。
- 下载：[`terminal-1.2.0.amr`](dist/terminal-1.2.0.amr)

### 1.3.0

- 实测：包体 **77,182 B**，7 个条目，`md5:7cfd6493…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **68,120 B**，ABI: arm64-orange、arm64
- 变化：reader 线程 + 环形缓冲（绝不跨线程回调 JS，避免框架黑屏）。
- 下载：[`terminal-1.3.0.amr`](dist/terminal-1.3.0.amr)

### 2.0.0

- 实测：包体 **128,418 B**，7 个条目，`md5:40aec604…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **133,568 B**，ABI: arm64-orange、arm64
- 变化：★ **真 PTY shell 跑起来了**：/bin/sh -i + 输出显示；此时仍用框架文本渲染，列对不齐。
- 下载：[`terminal-2.0.0.amr`](dist/terminal-2.0.0.amr)

### 2.1.0

- 实测：包体 **128,444 B**，7 个条目，`md5:0a033961…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **133,568 B**，ABI: arm64-orange、arm64
- 变化：会话列表 / alive / kill 等 API 补齐。
- 下载：[`terminal-2.1.0.amr`](dist/terminal-2.1.0.amr)

### 2.2.0

- 实测：包体 **128,922 B**，7 个条目，`md5:7cede6ec…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **133,568 B**，ABI: arm64-orange、arm64
- 变化：resize（TIOCSWINSZ）与窗口变化处理。
- 下载：[`terminal-2.2.0.amr`](dist/terminal-2.2.0.amr)

### 2.3.0

- 实测：包体 **128,861 B**，7 个条目，`md5:51fc7097…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **133,568 B**，ABI: arm64-orange、arm64
- 变化：输出缓冲上限与丢帧策略。
- 下载：[`terminal-2.3.0.amr`](dist/terminal-2.3.0.amr)

### 2.4.0

- 实测：包体 **129,379 B**，7 个条目，`md5:6efdc79d…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **133,728 B**，ABI: arm64-orange、arm64
- 变化：execSync（一次性执行等结果）加入，用于自检。
- 下载：[`terminal-2.4.0.amr`](dist/terminal-2.4.0.amr)

### 2.5.0

- 实测：包体 **130,097 B**，7 个条目，`md5:2564cc07…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,648 B**，ABI: arm64-orange、arm64
- 变化：环境变量 / cwd 参数化。
- 下载：[`terminal-2.5.0.amr`](dist/terminal-2.5.0.amr)

### 2.6.0

- 实测：包体 **130,120 B**，7 个条目，`md5:4ee17fad…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,648 B**，ABI: arm64-orange、arm64
- 变化：启动顺序与错误码整理。
- 下载：[`terminal-2.6.0.amr`](dist/terminal-2.6.0.amr)

### 2.7.0

- 实测：包体 **130,182 B**，7 个条目，`md5:c10c7f6b…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,648 B**，ABI: arm64-orange、arm64
- 变化：命令写入与回显处理。
- 下载：[`terminal-2.7.0.amr`](dist/terminal-2.7.0.amr)

### 2.8.0

- 实测：包体 **130,216 B**，7 个条目，`md5:1e62dc04…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,664 B**，ABI: arm64-orange、arm64
- 变化：插件体积优化（去调试符号）。
- 下载：[`terminal-2.8.0.amr`](dist/terminal-2.8.0.amr)

### 2.9.0

- 实测：包体 **130,600 B**，7 个条目，`md5:de103aaa…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,664 B**，ABI: arm64-orange、arm64
- 变化：稳定版：本地 shell 基本可用（但显示不理想）。
- 下载：[`terminal-2.9.0.amr`](dist/terminal-2.9.0.amr)

### 3.0.0

- 实测：包体 **130,550 B**，7 个条目，`md5:788e292f…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,664 B**，ABI: arm64-orange、arm64
- 变化：输入通道打通：系统键盘 → 页面 → PTY。
- 下载：[`terminal-3.0.0.amr`](dist/terminal-3.0.0.amr)

### 3.1.0

- 实测：包体 **130,563 B**，7 个条目，`md5:28967ca3…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,696 B**，ABI: arm64-orange、arm64
- 变化：快捷键条雏形（Enter/Tab/^C…）。
- 下载：[`terminal-3.1.0.amr`](dist/terminal-3.1.0.amr)

### 3.2.0

- 实测：包体 **130,083 B**，7 个条目，`md5:2bbefb20…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **134,696 B**，ABI: arm64-orange、arm64
- 变化：确认框架比例字体无法对齐（i×61=241px、M×61=790px），决定自绘。
- 下载：[`terminal-3.2.0.amr`](dist/terminal-3.2.0.amr)

### 4.0.0

- 实测：包体 **137,702 B**，7 个条目，`md5:b3a38936…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：★★ **改用原生渲染**：VT/ANSI 解析 → cols×rows 字符网格 → 8×16 点阵绘制 → PNG，页面用 <image file://…> 显示（每帧新文件名绕开图片缓存）。top/vi 列完美对齐，中文还不支持。
- 下载：[`terminal-4.0.0.amr`](dist/terminal-4.0.0.amr)

### 4.1.0

- 实测：包体 **138,094 B**，7 个条目，`md5:ad44a0aa…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：滚动缓冲（回看历史行）。
- 下载：[`terminal-4.1.0.amr`](dist/terminal-4.1.0.amr)

### 4.2.0

- 实测：包体 **138,770 B**，7 个条目，`md5:55ed7dd1…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：光标渲染 + 属性（粗体/反显/颜色）。
- 下载：[`terminal-4.2.0.amr`](dist/terminal-4.2.0.amr)

### 4.3.0

- 实测：包体 **138,214 B**，7 个条目，`md5:e0ce2b21…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：布局调整：快捷键条可折叠，避免挤占输出区。
- 下载：[`terminal-4.3.0.amr`](dist/terminal-4.3.0.amr)

### 4.4.0

- 实测：包体 **138,575 B**，7 个条目，`md5:6740c336…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：SIGWINCH 联动：折叠/展开时重排 top。
- 下载：[`terminal-4.4.0.amr`](dist/terminal-4.4.0.amr)

### 4.5.0

- 实测：包体 **139,024 B**，7 个条目，`md5:c3c1d986…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：渲染性能优化（脏行重绘 + PNG 编码优化）。
- 下载：[`terminal-4.5.0.amr`](dist/terminal-4.5.0.amr)

### 5.0.0

- 实测：包体 **140,774 B**，7 个条目，`md5:7d75c292…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：★ 滚动回看 API（vtScroll / vtHistory）+ 自动回底。
- 下载：[`terminal-5.0.0.amr`](dist/terminal-5.0.0.amr)

### 5.1.0

- 实测：包体 **140,773 B**，7 个条目，`md5:9d11eeba…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：滑动/上翻/下翻手势接上（含误触抑制）。
- 下载：[`terminal-5.1.0.amr`](dist/terminal-5.1.0.amr)

### 6.0.0

- 实测：包体 **140,443 B**，7 个条目，`md5:5860701c…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：★★ **自研入口打通**：不再借用官方 app.js.bin，自己写 App/glue 启动流程。
- 下载：[`terminal-6.0.0.amr`](dist/terminal-6.0.0.amr)

### 7.0.0

- 实测：包体 **141,435 B**，8 个条目，`md5:1fdce050…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：入口 glue 完善（glueboot.js / gluetrace.c 作为探针工具）。
- 下载：[`terminal-7.0.0.amr`](dist/terminal-7.0.0.amr)

### 7.1.0

- 实测：包体 **141,526 B**，8 个条目，`md5:c1347c3f…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 变化：页面模块布局改为 pages/index/*.js.bin（框架按页面路径找模块）。
- 下载：[`terminal-7.1.0.amr`](dist/terminal-7.1.0.amr)

### 7.3.0

- 实测：包体 **135,633 B**，11 个条目，`md5:b668c3be…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **15,084 B**
- 变化：包结构定型（11 条目、含 Component.js.bin）；**SSH 客户端**加入（/bin/ssh -tt）。
- 下载：[`terminal-7.3.0.amr`](dist/terminal-7.3.0.amr)

### 8.0.0

- 实测：包体 **136,360 B**，11 个条目，`md5:599b33d4…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **15,084 B**
- 变化：SSH 自动填密码（在 reader 线程里匹配 password 提示）+ 一键在笔上起 sshd。
- 下载：[`terminal-8.0.0.amr`](dist/terminal-8.0.0.amr)

### 9.0.0

- 实测：包体 **136,946 B**，11 个条目，`md5:48ebcb0e…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **140,832 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **15,157 B**
- 变化：★ **完全自研 entry 稳定版**：挂载调用必须写在基类页面里（不是页面类），可独立启动。
- 下载：[`terminal-9.0.0.amr`](dist/terminal-9.0.0.amr)

### 9.1.0

- 实测：包体 **462,776 B**，11 个条目，`md5:d5000e20…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **383,880 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **15,317 B**
- 变化：★★ **中文支持**：16×16 CJK 点阵表加入插件（插件 141KB → 384KB），全角字符双宽、标点/制表符正确。
- 下载：[`terminal-9.1.0.amr`](dist/terminal-9.1.0.amr)

### 9.2.0

- 实测：包体 **453,544 B**，11 个条目，`md5:0705c1c2…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **383,880 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **15,317 B**
- 变化：CJK 字形表按常用度裁剪压缩（包体 462.8KB → 453.5KB），渲染不变。
- 下载：[`terminal-9.2.0.amr`](dist/terminal-9.2.0.amr)

### 9.3.0

- 实测：包体 **465,395 B**，11 个条目，`md5:de01d0dd…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **397,296 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **19,851 B**
- 变化：★ **命令历史 + 常用命令**：新增 /userdisk/Favorite/PenTerm/{history,favorites}.json，插件加 storeDir/storeLoad/storeSave；面板可查看**全部**历史、点条目载入键盘编辑、▶ 直接执行、★ 收藏。
- 下载：[`terminal-9.3.0.amr`](dist/terminal-9.3.0.amr)

### 9.3.1

- 实测：包体 **465,391 B**，11 个条目，`md5:f53fdbfe…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **397,296 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **19,848 B**
- 变化：删除按钮 ✕ 在框架字体里是方框(tofu) → 换 ASCII x；面板加调试日志。
- 下载：[`terminal-9.3.1.amr`](dist/terminal-9.3.1.amr)

### 9.3.2

- 实测：包体 **467,674 B**，11 个条目，`md5:a1eb3a79…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **400,392 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **20,588 B**
- 变化：点终端画面**不再**呼出键盘（只有下方输入框呼出）；点快捷键条 Up/Dn 后把 shell 命令行内容载入输入框（新增插件 API vtCursorLine）。
- 下载：[`terminal-9.3.2.amr`](dist/terminal-9.3.2.amr)

### 9.3.3

- 实测：包体 **467,673 B**，11 个条目，`md5:f79b6bfa…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **400,392 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **20,588 B**
- 变化：交互收尾：提示文案改为「点下方输入框输入」；清理无用代码（histPrev/histIdx）。
- 下载：[`terminal-9.3.3.amr`](dist/terminal-9.3.3.amr)

### 9.3.4

- 实测：包体 **467,677 B**，11 个条目，`md5:d92fbb46…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **400,392 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **20,598 B**
- 变化：尝试用 flex 把 × 撑到行右侧 —— 该框架里 text 的 flex 不生效，未达成（记录在案）。
- 下载：[`terminal-9.3.4.amr`](dist/terminal-9.3.4.amr)

### 9.3.5

- 实测：包体 **467,689 B**，11 个条目，`md5:6ef40b48…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **400,392 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **20,629 B**
- 变化：★ × 改用**绝对定位钉在行最右端**，与 ▶ 隔开整行，避免误触。
- 下载：[`terminal-9.3.5.amr`](dist/terminal-9.3.5.amr)

### 9.3.6

- 实测：包体 **467,821 B**，11 个条目，`md5:d137932a…`
- 原生插件 `libs/arm64/libjsapi_term.so` = **400,392 B**，ABI: arm64-orange、arm64
- 页面字节码 `Component.js.bin` = **20,939 B**
- 变化：★ **修 bug**：Up/Dn 载入后 shell 原命令行仍有同一条命令，点「发送」会被追加成 `toptop`（执行两遍）→ 载入后立刻 Ctrl-A+Ctrl-K 清行（全屏程序 vi/less/top 里跳过）。
- 下载：[`terminal-9.3.6.amr`](dist/terminal-9.3.6.amr)

---

安装任意历史版本：

```sh
adb push dist/terminal-9.3.6.amr /tmp/
adb shell "miniapp_cli uninstall 8001999000000001"; adb shell "miniapp_cli install /tmp/terminal-9.3.6.amr"
adb shell "miniapp_cli start 8001999000000001 index"
```

> 注意：**必须 uninstall 再 install**（覆盖安装不会重新解包原生库）；
> 装的版本号可以从日志确认：`grep -a appResumed /data/applog/DictPen_*.log | tail -1`。
