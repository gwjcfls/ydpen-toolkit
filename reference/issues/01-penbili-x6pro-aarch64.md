# X6 Pro（YDPX6-2 / RK3562 / aarch64）无法运行：插件是 32 位 ARM，且播放器依赖 /dev/fb0

## 环境

| 项 | 值 |
|---|---|
| 机型 | 有道词典笔 **YDPX6-2**（X6 Pro，CHN PLUS 版） |
| SoC | Rockchip **RK3562**（MELON LP4 V10 Board），aarch64 |
| 系统 | PenOS **4.3.5**，Linux 5.10.160，框架 `/usr/bin/miniapp` |
| PenBili | v2.7.0（appid `8002026091900004`） |
| 安装方式 | `miniapp_cli install PenBili.amr`（未改动任何包内文件） |

## 现象

打开 app 报：

```
播放器模块不可用（未随包安装 libjsapi_player.so）
（app 内错误码 player_native_missing）
```

但 `.amr` 里**确实带了** `libs/libjsapi_player.so` 与 `libs/libjsapi_httpjson.so`，
安装后也确实落在 app 目录 `.../a/libs/` 下。

## 根因（框架日志原文）

```
ERROR: .../8002026091900004/a/libs/libjsapi_player_474480617.so:
       wrong ELF class: ELFCLASS32 : dlopen in dynamic_load_jsapi
ERROR: .../8002026091900004/a/libs/libjsapi_httpjson_474480617.so:
       wrong ELF class: ELFCLASS32 : dlopen in dynamic_load_jsapi
```

两个插件是 **32 位 ARM（armv7, `arm-linux-gnueabihf`）**，而 X6 Pro 是 **aarch64** → `dlopen` 直接失败 → 模块未注册 → app 报"播放器模块不可用"。

### 第二层问题（更关键）：播放器的渲染路径在本机不存在

`native/csrc/player.c` 的显示方案是：

```
/usr/bin/ffmpeg → rawvideo → 自己 mmap /dev/fb0（cvifb, 32bpp, 双缓冲 pan 切换）
```

而 X6 Pro **没有 `/dev/fb0`**，连 `/sys/class/graphics/` 都不存在：

```
$ ls /dev/fb*
ls: cannot access '/dev/fb*': No such file or directory
$ ls /sys/class/graphics/
ls: cannot access '/sys/class/graphics/': No such file or directory
$ ps aux | grep weston
877 root  /usr/bin/weston --debug -B=drm-backend.so --idle-time=0
$ ls /dev/dri/
card0  card1  renderD128  renderD129
```

即：本机显示走 **Weston（Wayland）+ DRM**。所以**即使把插件编成 aarch64，`open("/dev/fb0")` 也会失败**。

### 第三层：屏幕几何/方向与 X5 不同

`wayland-info` 实测：逻辑输出 **960×480**，物理模式 **480×960**（合成器旋转 90°）。

app 在本机传来的视频矩形是：

```
open(..., x=0, y=174, w=254, h=452, ... transpose=1, ...)
```

`254×452` 是**竖屏**矩形 —— 与 X5（逻辑 800×254 / 物理 254×800）一致。
也就是说 app 的版式数学（`probeVideoRects` / `transpose`）建立在 X5 的竖屏几何上，
在横屏机型上算出的视频区域与真实屏幕方向不匹配。

## 我这边做的移植（已在 X6 Pro 上跑通，可供参考/合并）

1. **`httpjson`**：`native/csrc/httpjson.c` 源码原样用 zig 重编为 aarch64
   （`-target aarch64-linux-gnu.2.29`，链接本机 `/usr/lib/libcurl.so.4.7.0`）→ 直接可用，无需改动。
2. **`player`**：保留 node 侧完全相同的 JS 契约（`open/pause/resume/seek/stop/release/status/info/pauseRender/resumeRender/redraw`，
   返回 `{ok,state,positionMs,durationMs,frames,...}`），**把渲染核心从 ffmpeg→fb0 换成 GStreamer**：

   - 单文件流（HTML5）：`playbin uri=<url> user-agent=<ua> video-sink="waylandsink"`
   - **DASH 双流**（本机实测 app 传 `input` + `input2` 分离音视频轨）：
     ```
     souphttpsrc location=<video> user-agent=… extra-headers="extra-headers,Referer=…"
       ! qtdemux ! h264parse ! mppvideodec ! queue ! videoscale add-borders=true
       ! video/x-raw,width=480,height=270,pixel-aspect-ratio=1/1 ! waylandsink
     souphttpsrc location=<audio> user-agent=… extra-headers="extra-headers,Referer=…"
       ! qtdemux ! aacparse ! faad ! audioconvert ! audioresample ! queue ! alsasink
     ```
   - 用 GStreamer C API（`gst_parse_launch` / `gst_element_set_state` / `gst_element_seek_simple` /
     `gst_element_query_position` / `gst_bus_pop_filtered`）实现 pause/resume/seek/position/EOS。
   - 状态上报要点：app 用 **`status().frames > 0`** 判断"起播成功"，所以插件必须回报递增的 `frames`。

   实测结果：B 站视频**正常播放**，进度/暂停/seek 都可用（MPP 硬解 `mppvideodec` 工作正常）。

## 移植中踩到、并建议写进文档的三个坑

1. **`waylandsink fullscreen=true` 会吞掉所有点击**：窗口是不透明的顶层窗口，铺满后
   app 的 UI 全被盖住、**整个界面点不动**（只能返回桌面）。除非另做输入转发，否则不要用 fullscreen。
2. **`video/x-raw,width=N` 单维 caps 会破坏宽高比**：只钉宽度时 `videoscale` 会用 sink 要求的高度补齐，
   表现为"宽度不变、纵向变窄、画面拉伸变形"。必须**宽高成对给出**（或用 `add-borders=true` 加黑边而不是拉伸）。
3. **不要照搬 app 传来的竖屏矩形**：在横屏机型上应该忽略该 rect，由播放器按屏幕方向自己定尺寸。

## 建议的修法（任选）

- **A（推荐）**：`build.ps1` 增加 aarch64 目标；`player.c` 增加一条"无 `/dev/fb0` → 用 GStreamer/waylandsink"的渲染后端
  （可用运行时探测：`access("/dev/fb0", W_OK)` 决定走 fb 还是 GStreamer）。
- **B**：至少在 README「支持设备」里写明**仅 X5（armv7 + fbdev）**，并在 amr 里让 `hasModule()` 失败时给出
  "本机型不支持"的明确提示（现在提示是"未随包安装 libjsapi_player.so"，容易误导——包其实装了）。

我这边有可用的 `player_gs.c`（GStreamer 后端，约 700 行，含日志/尺寸/全屏开关）
和打好的 `PenBili-2.7.0-aarch64.amr`（含 aarch64 的 `player` + `httpjson`，cert 校验通过），
**如果愿意收，我可以整理成 PR**。

## 附：可复现的最小检查清单

```bash
adb shell "ls -la /userdisk/miniapp/data/mini_app/pkg/8002026091900004/a/libs/"   # 插件在不在
adb shell "grep -iE 'ELFCLASS|dynamic_load_jsapi' /data/applog/YD_PEN_APP.log"   # 是否 32/64 位不匹配
adb shell "ls /dev/fb0; ls /sys/class/graphics/; ps aux | grep weston"           # 本机是否 fbdev
adb shell "XDG_RUNTIME_DIR=/run WAYLAND_DISPLAY=wayland-0 wayland-info | head -40"  # 屏幕几何/旋转
```
