/*
 * player_gs.c —— PenBili 的 player jsapi 插件（aarch64 / Weston-Wayland 版）
 *
 * 为什么需要重写：
 *   原版 native/csrc/player.c 是给有道词典笔 X5（Cvitek CV1826, 32 位 armv7）写的，
 *   渲染路径是「/usr/bin/ffmpeg 输出 rawvideo → 自己 mmap/pan /dev/fb0」。
 *   YDPX6-2 / X6 Pro（RK3562, aarch64）上：
 *     ① 原 .so 是 ELFCLASS32 → dlopen 直接失败（framework 日志：wrong ELF class）
 *     ② 这台笔**没有 /dev/fb0**（连 /sys/class/graphics 都没有），显示走 Weston/Wayland + DRM
 *   所以本文件用 GStreamer（souphttpsrc/qtdemux/mppvideodec/faad/waylandsink/alsasink）
 *   实现同一份 JS 契约，视频以独立的 Wayland 窗口呈现。
 *
 * JS 契约（见 src/services/player.js，全部返回对象或 JSON 字符串）：
 *   open(input, startMs, durationMs, fps, audio, transpose, x, y, w, h,
 *        audioDevice?, userAgent?, referer?, input2?, startBufMs?)
 *        → {ok, state, outWidth, outHeight, fps, audioRate, audioBt, code?, error?}
 *   pause() / resume() / seek(ms) / stop() / release()
 *   status() / info() → {ok, state, frames, positionMs, durationMs, audioBytes, ...}
 *   pauseRender() / resumeRender() / redraw() → {ok, state}
 *   state ∈ idle | loading | playing | paused | ended | error
 *
 * 编译（Windows + zig）：
 *   zig cc -target aarch64-linux-gnu.2.29 -O2 -g0 -Wl,-s -fPIC -shared -fvisibility=hidden \
 *     -I <sdk>/include -o libjsapi_player.so player_gs.c \
 *     stubs/libgstreamer-1.0.so.0 -lpthread
 */

#define _GNU_SOURCE 1

#include <quickjs.h>

#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern void registerCModuleLoader(const char *name,
                                 JSModuleDef *(*loader)(JSContext *ctx, const char *module_name));

/* ------------------------------------------------------------------ GStreamer 自声明原型
 * 不依赖 gstreamer 开发头文件：只用少量 C API，签名与 GStreamer 1.x 一致。
 * GstState: VOID_PENDING=0 NULL=1 READY=2 PAUSED=3 PLAYING=4
 * GstFormat: TIME=3
 * GstMessageType: EOS=1<<0, ERROR=1<<1
 */
extern void gst_init(int *argc, char ***argv);
extern void *gst_parse_launch(const char *pipeline_description, void **error);
extern int gst_element_set_state(void *element, int state);
extern int gst_element_get_state(void *element, int *state, int *pending, unsigned long long timeout);
extern int gst_element_seek_simple(void *element, int format, int seek_flags, long long seek_pos);
extern int gst_element_query_position(void *element, int format, long long *cur);
extern int gst_element_query_duration(void *element, int format, long long *cur);
extern void *gst_element_get_bus(void *element);
extern void *gst_bus_pop_filtered(void *bus, int types);
extern void gst_object_unref(void *object);
extern void gst_message_unref(void *message);
extern void gst_message_parse_error(void *message, void **gerror, char **debug);
extern void g_error_free(void *error);
extern void g_free(void *mem);
extern char *gst_message_type_get_name(int type);

/* GstVideoOverlay：把视频画到指定矩形（app 的 open(x,y,w,h) 就是它算好的视频区域） */
extern unsigned long gst_video_overlay_get_type(void);
extern void *gst_bin_get_by_interface(void *bin, unsigned long iface_type);
extern int gst_video_overlay_set_render_rectangle(void *overlay, int x, int y, int w, int h);
extern void gst_video_overlay_expose(void *overlay);
extern void g_object_set(void *obj, const char *first_property_name, ...);

/* GError 的最小布局：{GQuark domain; gint code; gchar *message;} */
typedef struct {
    unsigned int domain;
    int code;
    char *message;
} gerror_t;

#define GST_STATE_NULL 1
#define GST_STATE_READY 2
#define GST_STATE_PAUSED 3
#define GST_STATE_PLAYING 4
#define GST_FORMAT_TIME 3
#define GST_SEEK_FLAG_FLUSH 1
#define GST_SEEK_FLAG_ACCURATE 2
#define GST_SEEK_FLAG_KEY_UNIT 4
#define GST_MSG_EOS 1
#define GST_MSG_ERROR 2

#define NS_PER_MS 1000000LL

/* ------------------------------------------------------------------ 会话状态 */

enum { ST_IDLE = 0, ST_LOADING, ST_PLAYING, ST_PAUSED, ST_ENDED, ST_ERROR };

static const char *state_name(int s)
{
    switch (s) {
    case ST_LOADING: return "loading";
    case ST_PLAYING: return "playing";
    case ST_PAUSED: return "paused";
    case ST_ENDED: return "ended";
    case ST_ERROR: return "error";
    default: return "idle";
    }
}

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static void *g_pipe = NULL;          /* GstElement* (GstPipeline) */
static void *g_bus = NULL;           /* GstBus* */
static int g_state = ST_IDLE;
static int g_gst_inited = 0;
static int g_fps = 24;
static int g_out_w = 0, g_out_h = 0;
static int g_audio_rate = 44100;
static int g_audio_bt = 0;
static char g_err[256];
static long long g_seek_base_ms = 0;   /* 本次管道启动时的起点（ms） */
static struct timespec g_t0;           /* 进入 playing 的墙钟 */
static long long g_paused_total_ms = 0;
static struct timespec g_pause_t0;
static long long g_dur_ms = 0;
static long long g_pos_ms = 0;
static int g_explicit_pos = 0;
static long long g_seek_target = 0;          /* 最近一次 seek 的目标（UI 立刻反馈用） */
static long long g_seek_pending_until = 0;   /* 该时刻之前，若管道位置还没跳到位就先报目标值 */

/* 可调项（可用 /userdisk/Favorite/penbili_player.conf 覆盖）
 *   video_width=480     视频窗口宽度（物理像素）。waylandsink 按视频尺寸建窗口，
 *                       原尺寸在 480x960 的面板/窄显示条上会被裁掉，所以默认缩到 480 宽。
 *                       0 = 不缩放（用视频原始尺寸）
 *   fullscreen=1        视频全屏（fill-mode=fit，整幅可见，但会盖住 app 界面）
 *   videosink=waylandsink
 *   audiosink=alsasink
 */
static int g_fullscreen = 0;              /* waylandsink fullscreen */
static int g_video_width = 480;           /* 缩放目标宽度，0=原始尺寸 */
static int g_use_rect = 0;                /* 0=整窗模式（实测可用：视频完整居中、app 控件可见）
                                           * 1=铺满窗口+渲染矩形（会把界面盖黑，waylandsink 窗口不透明，已弃用） */
static char g_videosink[128] = "waylandsink";
static char g_audiosink[128] = "alsasink";
static void *g_overlay = NULL;            /* GstVideoOverlay* */
static int g_rect_x = 0, g_rect_y = 0, g_rect_w = 0, g_rect_h = 0;

/* 把 app 给的矩形应用到视频 sink（窗口铺满时即可"任意定位"视频） */
static void apply_rect_locked(void)
{
    if (g_overlay && g_rect_w > 0 && g_rect_h > 0) {
        gst_video_overlay_set_render_rectangle(g_overlay, g_rect_x, g_rect_y, g_rect_w, g_rect_h);
        gst_video_overlay_expose(g_overlay);
    }
}

/* 调试日志（排错用）：/tmp/player_gs.log */
static void plog(const char *fmt, ...)
{
    FILE *f = fopen("/tmp/player_gs.log", "a");
    va_list ap;

    if (!f)
        return;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void read_conf(void)
{
    FILE *f = fopen("/userdisk/Favorite/penbili_player.conf", "r");
    char line[256];

    if (!f) {
        /* 也认 app 私有目录，方便 app 侧写配置 */
        f = fopen("/userdisk/miniapp/data/mini_app/8002026091900004/player.conf", "r");
    }
    if (!f)
        return;
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        char *nl;
        if (!eq)
            continue;
        *eq = 0;
        nl = strchr(eq + 1, '\n');
        if (nl)
            *nl = 0;
        if (!strcmp(line, "fullscreen"))
            g_fullscreen = atoi(eq + 1) ? 1 : 0;
        else if (!strcmp(line, "use_rect"))
            g_use_rect = atoi(eq + 1) ? 1 : 0;
        else if (!strcmp(line, "video_width"))
            g_video_width = atoi(eq + 1);
        else if (!strcmp(line, "videosink"))
            snprintf(g_videosink, sizeof(g_videosink), "%s", eq + 1);
        else if (!strcmp(line, "audiosink"))
            snprintf(g_audiosink, sizeof(g_audiosink), "%s", eq + 1);
    }
    fclose(f);
}

/* 把字符串里会破坏 gst-launch 描述语法的字符转义 */
static void esc(char *dst, size_t dstsz, const char *src)
{
    size_t o = 0;

    for (size_t i = 0; src && src[i] && o + 2 < dstsz; i++) {
        if (src[i] == '"' || src[i] == '\\')
            dst[o++] = '\\';
        if (src[i] == '\n' || src[i] == '\r')
            continue;
        dst[o++] = src[i];
    }
    dst[o] = 0;
}

static void teardown_locked(void)
{
    if (g_overlay) {
        gst_object_unref(g_overlay);
        g_overlay = NULL;
    }
    if (g_pipe) {
        gst_element_set_state(g_pipe, GST_STATE_NULL);
    }
    if (g_bus) {
        gst_object_unref(g_bus);
        g_bus = NULL;
    }
    if (g_pipe) {
        gst_object_unref(g_pipe);
        g_pipe = NULL;
    }
    g_state = ST_IDLE;
}

/* 构造管道描述 */
static int build_pipeline(char *desc, size_t descsz,
                          const char *vurl, const char *aurl,
                          const char *ua, const char *referer,
                          int audio, const char *audio_dev)
{
    char v[600], a[600], u[400], r[600];
    char vsink[384], asink[256], scale[192] = "";
    char ua_opt[512] = "", ref_opt[768] = "";
    int n;

    esc(v, sizeof(v), vurl);
    esc(a, sizeof(a), aurl ? aurl : "");
    esc(u, sizeof(u), ua ? ua : "");
    esc(r, sizeof(r), referer ? referer : "");

    if (u[0])
        snprintf(ua_opt, sizeof(ua_opt), " user-agent=\"%s\"", u);
    if (r[0])
        snprintf(ref_opt, sizeof(ref_opt), " extra-headers=\"extra-headers,Referer=%s\"", r);

    /* waylandsink 按视频尺寸建窗口：原尺寸在窄屏上会被裁，所以先缩到目标框。
     * 注意必须**同时**给出宽高：只钉 width 时 videoscale 会用 sink 要的高度补齐，
     * 宽高比就被破坏（实测"宽不变、长变窄、画面变形"）。
     * add-borders=true 让非 16:9 的片源加黑边而不是拉伸。 */
    if (!g_use_rect && g_video_width > 0) {
        int vh = g_video_width * 9 / 16;

        if (vh < 2)
            vh = 2;
        snprintf(scale, sizeof(scale),
                 "videoscale add-borders=true ! video/x-raw,width=%d,height=%d,pixel-aspect-ratio=1/1 ! ",
                 g_video_width, vh);
    }

    if (g_fullscreen && !g_use_rect)
        snprintf(vsink, sizeof(vsink), "%s%s fullscreen=true", scale, g_videosink);
    else
        snprintf(vsink, sizeof(vsink), "%s%s", scale, g_videosink);

    if (!audio)
        snprintf(asink, sizeof(asink), "fakesink");
    else if (audio_dev && audio_dev[0])
        snprintf(asink, sizeof(asink), "%s device=%s", g_audiosink, audio_dev);
    else
        snprintf(asink, sizeof(asink), "%s", g_audiosink);

    if (aurl && aurl[0]) {
        /* DASH：视频轨与音频轨分开（B 站 DASH 即如此），两条 souphttpsrc 分支同管线、同步走 */
        n = snprintf(desc, descsz,
                     "souphttpsrc location=\"%s\"%s%s ! qtdemux ! h264parse ! mppvideodec ! queue ! %s "
                     "souphttpsrc location=\"%s\"%s%s ! qtdemux ! aacparse ! faad ! audioconvert ! audioresample ! queue ! %s",
                     v, ua_opt, ref_opt, vsink,
                     a, ua_opt, ref_opt, asink);
    } else {
        /* 单文件：交给 playbin 自动处理容器与解码 */
        n = snprintf(desc, descsz,
                     "playbin uri=\"%s\"%s video-sink=\"%s\" audio-sink=\"%s\"",
                     v, ua_opt, vsink, asink);
    }
    return n > 0 && n < (int)descsz;
}

/* 启动一个会话（在 JS 线程调用；阻塞最多 ~2s 等 preroll） */
static int session_open(const char *vurl, const char *aurl, long long start_ms,
                        int fps, int audio, const char *ua, const char *referer,
                        const char *audio_dev, int rx, int ry, int rw, int rh)
{
    char desc[4096];
    void *pipe;
    int st = 0;

    read_conf();

    pthread_mutex_lock(&g_lock);
    teardown_locked();
    if (g_overlay) {
        gst_object_unref(g_overlay);
        g_overlay = NULL;
    }
    g_rect_x = rx; g_rect_y = ry; g_rect_w = rw; g_rect_h = rh;
    g_err[0] = 0;
    g_fps = fps > 0 ? fps : 24;
    g_dur_ms = 0;
    g_pos_ms = 0;
    g_seek_base_ms = start_ms > 0 ? start_ms : 0;
    g_paused_total_ms = 0;
    g_explicit_pos = 0;
    g_state = ST_LOADING;
    pthread_mutex_unlock(&g_lock);

    if (!g_gst_inited) {
        gst_init(NULL, NULL);
        g_gst_inited = 1;
    }

    if (!build_pipeline(desc, sizeof(desc), vurl, aurl, ua, referer, audio, audio_dev)) {
        snprintf(g_err, sizeof(g_err), "管道描述过长");
        g_state = ST_ERROR;
        return -1;
    }

    pipe = gst_parse_launch(desc, NULL);
    if (!pipe) {
        snprintf(g_err, sizeof(g_err), "gst_parse_launch 失败");
        g_state = ST_ERROR;
        plog("open 失败：gst_parse_launch 返回 NULL");
        return -1;
    }
    plog("open: fullscreen=%d width=%d start=%lldms fps=%d audio=%d rect=%d,%d %dx%d uri=%.150s%s",
         g_fullscreen, g_video_width, start_ms, fps, audio, rx, ry, rw, rh,
         vurl, (aurl && aurl[0]) ? " +audio2" : "");
    plog("管道: %.900s", desc);

    /* 先 PAUSED 做 preroll，再按 startMs seek，最后 PLAYING —— 这样能从指定位置起播 */
    gst_element_set_state(pipe, GST_STATE_PAUSED);
    gst_element_get_state(pipe, &st, NULL, 2000 * NS_PER_MS);

    {
        long long dur = 0;
        if (gst_element_query_duration(pipe, GST_FORMAT_TIME, &dur) && dur > 0)
            g_dur_ms = dur / NS_PER_MS;
    }

    if (start_ms > 0)
        gst_element_seek_simple(pipe, GST_FORMAT_TIME,
                                GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE,
                                start_ms * NS_PER_MS);

    gst_element_set_state(pipe, GST_STATE_PLAYING);

    pthread_mutex_lock(&g_lock);
    g_pipe = pipe;
    g_bus = gst_element_get_bus(pipe);
    g_state = ST_PLAYING;
    clock_gettime(CLOCK_MONOTONIC, &g_t0);

    /* use_rect：窗口铺满屏幕，视频按 app 给的矩形绘制（可定位、可随 app 挪动） */
    if (g_use_rect) {
        g_overlay = gst_bin_get_by_interface(pipe, gst_video_overlay_get_type());
        if (g_overlay) {
            g_object_set(g_overlay, "fullscreen", 1 /* TRUE，gboolean */, NULL);
            apply_rect_locked();
            plog("overlay: fullscreen 窗口 + 视频矩形 %d,%d %dx%d", g_rect_x, g_rect_y, g_rect_w, g_rect_h);
        } else {
            plog("overlay: 没拿到 GstVideoOverlay 接口 → 退回整窗模式");
        }
    }
    pthread_mutex_unlock(&g_lock);
    return 0;
}

/* 轮询总线（EOS / ERROR），并刷新 position / duration */
static void poll_locked(void)
{
    long long pos = 0, dur = 0;

    if (!g_pipe)
        return;

    if (g_bus) {
        void *msg = gst_bus_pop_filtered(g_bus, GST_MSG_EOS);
        if (msg) {
            g_state = ST_ENDED;
            gst_message_unref(msg);
            return;
        }
        msg = gst_bus_pop_filtered(g_bus, GST_MSG_ERROR);
        if (msg) {
            gerror_t *gerr = NULL;
            char *dbg = NULL;
            gst_message_parse_error(msg, (void **)&gerr, &dbg);
            if (gerr && gerr->message) {
                snprintf(g_err, sizeof(g_err), "%.180s", gerr->message);
                plog("总线错误: %s | debug: %.200s", gerr->message, dbg ? dbg : "");
                g_error_free(gerr);
            } else {
                snprintf(g_err, sizeof(g_err), "GStreamer 播放错误");
            }
            if (dbg)
                g_free(dbg);
            g_state = ST_ERROR;
            gst_message_unref(msg);
            return;
        }
    }

    if (g_state == ST_PLAYING || g_state == ST_PAUSED) {
        if (gst_element_query_position(g_pipe, GST_FORMAT_TIME, &pos) && pos >= 0) {
            long long q = pos / NS_PER_MS;
            /* seek 刚发出时管道位置可能还是旧值：短暂上报目标位置，避免 UI 弹回 */
            if (g_seek_pending_until > now_ms() && llabs(q - g_seek_target) > 3000) {
                g_pos_ms = g_seek_target;
            } else {
                g_pos_ms = q;
                g_seek_pending_until = 0;
            }
        }
        if (gst_element_query_duration(g_pipe, GST_FORMAT_TIME, &dur) && dur > 0)
            g_dur_ms = dur / NS_PER_MS;

        /* 位置查询不可靠时用墙钟兜底（app 需要 positionMs 递增来判断起播） */
        if (g_state == ST_PLAYING && g_pos_ms <= 0) {
            long long wall = now_ms() - ((long long)g_t0.tv_sec * 1000 + g_t0.tv_nsec / 1000000) - g_paused_total_ms;
            if (wall > 0)
                g_pos_ms = g_seek_base_ms + wall;
        }
    }
}

static long long est_frames_locked(void)
{
    if (g_state != ST_PLAYING && g_state != ST_PAUSED && g_state != ST_ENDED)
        return 0;
    return (g_pos_ms * g_fps) / 1000;
}

/* ------------------------------------------------------------------ JSON 输出 */

static JSValue status_obj(JSContext *ctx, int with_extra)
{
    JSValue o;

    pthread_mutex_lock(&g_lock);
    poll_locked();
    apply_rect_locked();      /* app 挪动视频区域后，每次查询状态都会重新应用矩形 */

    o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "ok", JS_NewBool(ctx, g_state != ST_ERROR));
    JS_SetPropertyStr(ctx, o, "state", JS_NewString(ctx, state_name(g_state)));
    JS_SetPropertyStr(ctx, o, "positionMs", JS_NewInt64(ctx, g_pos_ms));
    JS_SetPropertyStr(ctx, o, "posMs", JS_NewInt64(ctx, g_pos_ms));
    JS_SetPropertyStr(ctx, o, "durationMs", JS_NewInt64(ctx, g_dur_ms));
    JS_SetPropertyStr(ctx, o, "frames", JS_NewInt64(ctx, est_frames_locked()));
    JS_SetPropertyStr(ctx, o, "fps", JS_NewInt32(ctx, g_fps));
    JS_SetPropertyStr(ctx, o, "outWidth", JS_NewInt32(ctx, g_out_w));
    JS_SetPropertyStr(ctx, o, "outHeight", JS_NewInt32(ctx, g_out_h));
    JS_SetPropertyStr(ctx, o, "audioRate", JS_NewInt32(ctx, g_audio_rate));
    JS_SetPropertyStr(ctx, o, "audioBt", JS_NewBool(ctx, g_audio_bt));
    JS_SetPropertyStr(ctx, o, "avDriftMs", JS_NewInt32(ctx, 0));
    JS_SetPropertyStr(ctx, o, "audioBytes", JS_NewInt64(ctx, (g_pos_ms * 44100LL * 4) / 1000));
    JS_SetPropertyStr(ctx, o, "audioDropped", JS_NewInt32(ctx, 0));
    JS_SetPropertyStr(ctx, o, "audioUnderruns", JS_NewInt32(ctx, 0));
    JS_SetPropertyStr(ctx, o, "audioWrErrors", JS_NewInt32(ctx, 0));
    JS_SetPropertyStr(ctx, o, "audioRingDrops", JS_NewInt32(ctx, 0));
    if (with_extra)
        JS_SetPropertyStr(ctx, o, "engine", JS_NewString(ctx, "gstreamer/aarch64"));
    if (g_err[0])
        JS_SetPropertyStr(ctx, o, "error", JS_NewString(ctx, g_err));
    pthread_mutex_unlock(&g_lock);
    return o;
}

/* ------------------------------------------------------------------ JS 方法 */

static JSValue js_open(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *vurl = NULL, *aurl = NULL, *ua = NULL, *ref = NULL, *adev = NULL;
    int32_t start_ms = 0, dur_ms = 0, fps = 24, audio = 1, transpose = 1;
    int32_t x = 0, y = 0, w = 0, h = 0, start_buf = 0;
    int rc;

    (void)this_val;
    if (argc < 1)
        return JS_NewString(ctx, "{\"ok\":false,\"code\":\"PLAYER_BAD_INPUT\",\"state\":\"error\"}");

    vurl = JS_ToCString(ctx, argv[0]);
    if (argc > 1) JS_ToInt32(ctx, &start_ms, argv[1]);
    if (argc > 2) JS_ToInt32(ctx, &dur_ms, argv[2]);
    if (argc > 3) JS_ToInt32(ctx, &fps, argv[3]);
    if (argc > 4) JS_ToInt32(ctx, &audio, argv[4]);
    if (argc > 5) JS_ToInt32(ctx, &transpose, argv[5]);
    if (argc > 6) JS_ToInt32(ctx, &x, argv[6]);
    if (argc > 7) JS_ToInt32(ctx, &y, argv[7]);
    if (argc > 8) JS_ToInt32(ctx, &w, argv[8]);
    if (argc > 9) JS_ToInt32(ctx, &h, argv[9]);
    if (argc > 10) adev = JS_ToCString(ctx, argv[10]);
    if (argc > 11) ua = JS_ToCString(ctx, argv[11]);
    if (argc > 12) ref = JS_ToCString(ctx, argv[12]);
    if (argc > 13) aurl = JS_ToCString(ctx, argv[13]);
    if (argc > 14) JS_ToInt32(ctx, &start_buf, argv[14]);
    (void)dur_ms; (void)transpose; (void)x; (void)y; (void)start_buf;

    /* 输出尺寸：按窗口矩形给个合理值（waylandsink 自己按视频尺寸建窗口） */
    pthread_mutex_lock(&g_lock);
    g_out_w = w > 0 ? w : 480;
    g_out_h = h > 0 ? h : 240;
    if (adev && adev[0] && strstr(adev, "blue"))
        g_audio_bt = 1;
    else
        g_audio_bt = 0;
    pthread_mutex_unlock(&g_lock);

    rc = session_open(vurl ? vurl : "", aurl, start_ms, fps, audio, ua, ref, adev,
                      x, y, w > 0 ? w : 0, h > 0 ? h : 0);

    if (vurl) JS_FreeCString(ctx, vurl);
    if (aurl) JS_FreeCString(ctx, aurl);
    if (ua) JS_FreeCString(ctx, ua);
    if (ref) JS_FreeCString(ctx, ref);
    if (adev) JS_FreeCString(ctx, adev);

    if (rc != 0) {
        JSValue e = JS_NewObject(ctx);
        JS_SetPropertyStr(ctx, e, "ok", JS_NewBool(ctx, 0));
        JS_SetPropertyStr(ctx, e, "state", JS_NewString(ctx, "error"));
        JS_SetPropertyStr(ctx, e, "code", JS_NewString(ctx, "PLAYER_FFMPEG_FAILED"));
        JS_SetPropertyStr(ctx, e, "error", JS_NewString(ctx, g_err[0] ? g_err : "管道启动失败"));
        return e;
    }
    return status_obj(ctx, 1);
}

static int set_state_simple(int state)
{
    int ok = 0;

    pthread_mutex_lock(&g_lock);
    if (g_pipe) {
        gst_element_set_state(g_pipe, state);
        ok = 1;
        if (state == GST_STATE_PAUSED && g_state == ST_PLAYING) {
            g_state = ST_PAUSED;
            clock_gettime(CLOCK_MONOTONIC, &g_pause_t0);
        } else if (state == GST_STATE_PLAYING && g_state == ST_PAUSED) {
            g_state = ST_PLAYING;
            g_paused_total_ms += now_ms() - ((long long)g_pause_t0.tv_sec * 1000 + g_pause_t0.tv_nsec / 1000000);
        }
    }
    pthread_mutex_unlock(&g_lock);
    return ok;
}

static JSValue js_pause(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    (void)t; (void)argc; (void)argv;
    set_state_simple(GST_STATE_PAUSED);
    return status_obj(ctx, 0);
}

static JSValue js_resume(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    (void)t; (void)argc; (void)argv;
    set_state_simple(GST_STATE_PLAYING);
    return status_obj(ctx, 0);
}

static JSValue js_seek(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    int32_t ms = 0;

    (void)t;
    if (argc > 0)
        JS_ToInt32(ctx, &ms, argv[0]);
    if (ms < 0)
        ms = 0;

    pthread_mutex_lock(&g_lock);
    if (g_pipe) {
        int was_paused = (g_state == ST_PAUSED);
        int ok;

        /* 暂停态下直接 flush seek 常常不落地：先临时 PLAYING，seek 完成后再回到 PAUSED */
        if (was_paused)
            gst_element_set_state(g_pipe, GST_STATE_PLAYING);

        ok = gst_element_seek_simple(g_pipe, GST_FORMAT_TIME,
                                     GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE,
                                     (long long)ms * NS_PER_MS);
        if (ok) {
            int st = 0;
            gst_element_get_state(g_pipe, &st, NULL, 1500 * NS_PER_MS);   /* 等 seek 落地 */
            g_pos_ms = ms;
            g_seek_base_ms = ms;
            g_seek_target = ms;
            g_seek_pending_until = now_ms() + 1500;
            clock_gettime(CLOCK_MONOTONIC, &g_t0);
            g_paused_total_ms = 0;
            if (g_state == ST_ENDED)
                g_state = ST_PLAYING;
        } else {
            plog("seek(%d) 失败：源可能不支持跳转", ms);
        }
        if (was_paused)
            gst_element_set_state(g_pipe, GST_STATE_PAUSED);
        plog("seek -> %dms ok=%d was_paused=%d", ms, ok, was_paused);
    }
    pthread_mutex_unlock(&g_lock);
    return status_obj(ctx, 0);
}

static JSValue js_stop(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    (void)t; (void)argc; (void)argv;
    pthread_mutex_lock(&g_lock);
    teardown_locked();
    g_pos_ms = 0;
    g_err[0] = 0;
    pthread_mutex_unlock(&g_lock);
    return status_obj(ctx, 0);
}

static JSValue js_release(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    return js_stop(ctx, t, argc, argv);
}

static JSValue js_status(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    (void)t; (void)argc; (void)argv;
    return status_obj(ctx, 0);
}

static JSValue js_info(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    (void)t; (void)argc; (void)argv;
    return status_obj(ctx, 1);
}

static JSValue js_ok(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    (void)t; (void)argc; (void)argv;
    return status_obj(ctx, 0);
}

/* 运行期切换全屏（下次 open 生效）；app 不调用它，也可以用配置文件或手动调用 */
static JSValue js_set_fullscreen(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    int32_t f = 0;

    (void)t;
    if (argc > 0)
        JS_ToInt32(ctx, &f, argv[0]);
    g_fullscreen = f ? 1 : 0;
    plog("setFullscreen=%d（下次 open 生效）", g_fullscreen);
    return status_obj(ctx, 1);
}

/* ------------------------------------------------------------------ 模块注册 */

typedef struct {
    const char *name;
    JSCFunction *fn;
    int nargs;
} method_t;

static const method_t g_methods[] = {
    {"open", js_open, 15},
    {"pause", js_pause, 0},
    {"resume", js_resume, 0},
    {"seek", js_seek, 1},
    {"stop", js_stop, 0},
    {"release", js_release, 0},
    {"status", js_status, 0},
    {"info", js_info, 0},
    {"pauseRender", js_ok, 0},
    {"resumeRender", js_ok, 0},
    {"redraw", js_ok, 0},
    {"setFullscreen", js_set_fullscreen, 1},
};
#define NMETHODS ((int)(sizeof(g_methods) / sizeof(g_methods[0])))

static int player_module_init(JSContext *ctx, JSModuleDef *m)
{
    JSValue o = JS_NewObject(ctx);

    for (int i = 0; i < NMETHODS; i++) {
        JSValue f = JS_NewCFunction(ctx, g_methods[i].fn, g_methods[i].name, g_methods[i].nargs);
        JS_SetPropertyStr(ctx, o, g_methods[i].name, JS_DupValue(ctx, f));
        JS_SetModuleExport(ctx, m, g_methods[i].name, f);   /* 接管所有权 */
    }
    JS_SetModuleExport(ctx, m, "default", o);
    return 0;
}

static JSModuleDef *loader(JSContext *ctx, const char *module_name)
{
    JSModuleDef *m;

    if (strcmp(module_name, "player") != 0)
        return NULL;
    m = JS_NewCModule(ctx, module_name, player_module_init);
    if (!m)
        return NULL;
    JS_AddModuleExport(ctx, m, "default");
    for (int i = 0; i < NMETHODS; i++)
        JS_AddModuleExport(ctx, m, g_methods[i].name);
    return m;
}

/* 必须显式可见：编译时用了 -fvisibility=hidden（与上游 build.ps1 一致） */
__attribute__((visibility("default"))) void custom_init_jsapis(void)
{
    registerCModuleLoader("player", loader);
}
