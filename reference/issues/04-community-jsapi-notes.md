# 词典笔 jsapi 原生插件开发笔记 + X6 Pro 平台差异（可直接发讨论区/文档）

> 面向想给 PenOS 写原生 jsapi 插件、或想把 X5 上的项目移植到 X6 Pro 的人。
> 全部结论来自真机（YDPX6-2 / RK3562 / PenOS 4.3.5）实测，非推测。

## 一、jsapi 插件 ABI（可自己写 .so 补框架能力）

插件就是一个共享库，**只导出一个符号**：

```c
extern void registerCModuleLoader(const char *name,
                                  JSModuleDef *(*loader)(JSContext *ctx, const char *module_name));

__attribute__((visibility("default")))     /* 若编译加了 -fvisibility=hidden，这句必须有 */
void custom_init_jsapis(void) {
    registerCModuleLoader("<moduleName>", loader);
}
```

- `registerCModuleLoader` 来自宿主（`/usr/lib/libjsapi_proxy.so`），
  `JS_*` 来自宿主全局符号 —— **插件不需要链接 libquickjs**（官方插件 NEEDED 里也没有它）；
- **QuickJS 头文件版本必须与宿主一致**（本机是 `20200705`），否则 JSValue ABI 不匹配；
- 用 zig 交叉编译即可（Windows 上就能做）：

```powershell
zig cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 -I quickjs my_plugin.c -o libjsapi_x_1.so
# 产物自检：只导出 custom_init_jsapis；NEEDED 只有 libc（+用到线程时 libpthread）
```

### 三件套：模块名 / 导出名 / 事件名（错一个就"黑屏"）

| 契约 | 怎么查 |
|---|---|
| **模块名** | 反汇编原插件的 `custom_init_jsapis`，看它 `strcmp(module_name, "…")` 用的是哪个字符串 |
| **导出名** | 翻原插件字符串表。**app 常用具名导入**（如 `import { FileServer } from 'fileServer'`），少一个就是 `SyntaxError: Could not find export 'FileServer' in module 'fileServer'` → **整个页面黑屏**。稳妥做法：`default` / 原名 / 首字母大写名 三个都导出 |
| **事件名** | app 字节码里 `.on('…')` 之后的原子（例：`server_started` / `server_stopped`）。插件要实现 `on(event, cb)` 存回调，并在**本来就在 JS 线程上的入口**（如 `start()`）里同步触发 |

### 线程与稳定性（血泪）

- **绝不要跨线程碰 JSValue**；需要回调 JS 就在 JS 线程的入口里同步做；
- **不要在插件里用 `popen`/`fork`** —— 框架是多线程进程，fork 有死锁风险（我踩过：页面黑屏）；
  取本机 IP 用 `getifaddrs()`；
- 自己起的线程/循环要保证**每轮都有进展**，否则打满 CPU 会把笔拖到 ADB 掉线（表现为"整机卡死"）。

### 安装语义（容易踩）

- `miniapp_cli install` 在**同 appid 已存在**时**只更新 JS，不重新提取 `libs/*.so`** → 升级带插件的包前先 `uninstall`；
- 框架加载插件后会把文件名规范成 `libjsapi_<name>_<hash>.so` —— **看到 hash 后缀就说明被接纳了**；
- **替换正在被 mmap 的 `.so`（热替换）有概率让框架崩 → 黑屏**；改完插件请**重启**，别热替；
- 排错第一站永远是框架日志 **`/data/applog/YD_PEN_APP.log`**（JS 报错原文、`dynamic_load_jsapi`、`topAppId` 都在里面）。

## 二、X6 Pro（YDPX6-2 / RK3562）与 X5 的平台差异

| 项 | X5（YDPX5-1 / Cvitek CV1826） | **X6 Pro（YDPX6-2 / RK3562）** |
|---|---|---|
| 架构 | **armv7（32 位）** | **aarch64（64 位）** → 32 位 `.so` dlopen 会 `wrong ELF class: ELFCLASS32` |
| 显示 | **fbdev `/dev/fb0`**（cvifb，32bpp，可 pan 切换） | **无 `/dev/fb0`**，无 `/sys/class/graphics/`；走 **Weston + DRM**（`/dev/dri/card0`，`weston -B=drm-backend.so`） |
| 屏幕几何 | 逻辑 800×254 / 物理 254×800（旋转 270°） | 逻辑 **960×480** / 物理 480×960（旋转 90°，`wayland-info` 实测） |
| 媒体 | ffmpeg（禁用了 outdevs/indevs）+ aplay | ffmpeg 4.4.1（**outdevs/alsa 都开着**）、**GStreamer 1.22 + MPP 硬解**（`mppvideodec`）、`libcurl`、`libgstplayer` |

### 在 X6 Pro 上放视频的正确姿势（实测可用）

```bash
export XDG_RUNTIME_DIR=/run WAYLAND_DISPLAY=wayland-0   # 框架进程里是 /var/run
gst-launch-1.0 playbin uri=<url> video-sink=waylandsink
```

- 可用元件：`souphttpsrc` `qtdemux` `flvdemux` `h264parse` `aacparse` `faad` `mppvideodec`
  `waylandsink` `alsasink` `hlsdemux`（**没有** `dashdemux` `curlhttpsrc` `bluealsasink`）；
- **`waylandsink fullscreen=true` 会吞掉所有点击**（不透明顶层窗口盖满屏幕，整个 UI 点不动）—— 慎用；
- **`video/x-raw,width=N` 单维 caps 会破坏宽高比**（`videoscale` 会用 sink 要求的高度补齐，
  表现为"宽度不变、纵向变窄、画面拉伸"）→ **宽高必须成对给出**，并配 `add-borders=true`；
- **DASH 双流**（B 站那种视频/音频分开）用两条 `souphttpsrc` 分支同管线即可同步：
  `souphttpsrc ! qtdemux ! h264parse ! mppvideodec ! … ! waylandsink` +
  `souphttpsrc ! qtdemux ! aacparse ! faad ! … ! alsasink`（UA/Referer 用 `user-agent` / `extra-headers`）。

### 两个超好用的调试技巧

1. **抓整个屏幕（含视频层）**：`miniapp_cli capture` 只能抓到框架自己的 surface，
   抓视频/其他客户端要用 Weston 自带工具：
   ```bash
   XDG_RUNTIME_DIR=/run WAYLAND_DISPLAY=wayland-0 weston-screenshooter   # 在 /usr/bin
   ```
   它输出 480×960 的**物理**帧缓冲，能直接看到旋转与窗口摆放的真实情况。
2. **远程操作 UI**：自己写输入注入即可（`/dev/input/event1` 是触摸设备）——
   单点触控写 `ABS_MT_TRACKING_ID/POSITION_X/POSITION_Y + BTN_TOUCH + SYN_REPORT`；
   滑动手势在按下后分步更新坐标并每次 `SYN_REPORT`。
   ⚠️ **息屏时框架暂停渲染**：截图会是空白（约 1.6 KB），要先
   `echo 200 > /sys/class/backlight/backlight/brightness` 并注入一次触摸。
3. **看 JS 侧到底拿到了什么**：`miniapp_cli dumpMemory` 会把 QuickJS 堆导成
   `/tmp/httpdump.heapsnapshot`（V8 格式：nodes/edges/strings），
   用 script 解析 `strings` 就能还原 app 内存里的数据（我用它把商店目录 34 条补全了）。

## 三、常见故障速查

| 症状 | 原因 |
|---|---|
| `wrong ELF class: ELFCLASS32` | 插件架构不对（32 位 vs aarch64） |
| `Could not find export 'X' in module 'Y'` + 页面黑屏 | 插件缺具名导出 |
| 装了插件但功能没变 | ① 覆盖安装没提取 libs（先 uninstall）；② 没重启（dlopen 缓存） |
| 打开 app 黑屏 | 热替换了 mmap 中的 `.so`；或插件里用了 popen/fork；或导出名不全 |
| 界面显示"未启动"但服务真的起了 | app 靠事件刷新 UI：插件要发 `server_started`/`server_stopped`，且 `start()` 返回带 `running` 的对象 |
| `miniapp_cli start X --page` 无反应 | 页面路由名写错（从 app 的 `app.js.bin` 原子串里查），日志会有 `fpath(.../--page.js.bin) open failed` |
