# ydpen-toolkit —— 有道词典笔改造工具集与逆向笔记（DSH Skill）

给 **有道词典笔**（YDPX6-2 / X6 Pro、YDPX7-1 / X7 Pro；Rockchip RK3562；PenOS 4.3.5 ~ 4.8.7）
做越权与改造的**技能包**：一份 `SKILL.md` 索引 + 11 篇分主题逆向/操作笔记 + 65 个可直接用的脚本 +
自研 miniapp 原生插件的源码与成品包。

这是一个 **DeepSeek Harness 技能**：把整个目录放到 `~/.dsh/skills/ydpen-toolkit/` 即可被 agent 加载。

## 能力范围

| 主题 | 内容 |
|---|---|
| 接入与 root | `adb shell auth`（口令 `ydpen2026`，固件补丁已持久化到 A/B 槽）、USB offline 排错、IP 发现 |
| miniapp / .amr | 打包/解包、manifest、双 ABI 原生库分包、自研工具链（QuickJS 字节码编译器 `jsfmc`） |
| jsapi 原生插件 | 插件 ABI（`jsapi_check_unload`、无跨线程回 JS 回调）、fs / fileServer 等插件源码 |
| OTA 固件 | 固件包结构、MITM 解锁、A/B 槽补丁（root 口令注入） |
| 持久化 | 逆向 `AppWhitelistCleaner`（幕后删应用的真正元凶）、DNS 劫持、镜像自愈、cron |
| USB / MTP | 为什么插电脑没有 MTP（`/tmp/.usb_config` 被 ADB 调试覆盖）、`usb-mode.sh` |
| 排错 | 框架日志、`miniapp_cli` 用法、截图唤醒、触控注入（`touchinfo`）、顶栏注入失效的坑 |
| 终端应用 | PenTerm：自绘 VT 终端、中文点阵、命令历史/常用命令（含 9 个版本的构建坑） |

## 目录

```
SKILL.md              技能入口（何时用、怎么用、排错表、文件索引）
reference/            11 篇分主题笔记 + issues/（实战问题记录）+ workspace-docs/（原始文档）
scripts/              65 个脚本：工具链、部署、自测、逆向（ELF/JS 字节码分析）
assets/               quickjs 头与库、终端源码与成品 amr、字体、截图
```

## 几篇值得看的

- `reference/07-miniapp-dev-toolchain.md` —— 自研 miniapp 工具链与入口契约
- `reference/08-sideload-persistence.md` —— 侧载应用与数据持久化（保活方案）
- `reference/09-appupd-and-whitelist-cleaner.md` —— **谁在删你的应用**（APP_UPD 真相 + `AppWhitelistCleaner`）
- `reference/10-usb-modes-and-mtp.md` —— USB 模式与 MTP
- `reference/11-penterm-command-history.md` —— 终端命令历史/常用命令 + 构建三坑

配套仓库：
- **pen-term** —— 终端应用（源码 + 构建工具链 + 52 个历史版本）
- **sideload-keeper** —— 侧载保活（完整脚本 + README + 逆向报告）

## 许可

MIT。逆向笔记与工具仅用于自己设备的改造研究。
