/* gluetrace.c —— 在"框架桩"环境里执行官方 app.js.bin，dump 它对 $falcon / App.meta / globalThis 的所有写入
 *
 * 目的：官方 miniapp 的 app.js.bin 里有一段 aiot-vue-cli 注入的"胶水"（用户源码里看不到），
 *       它负责 __AppClazz / App.meta / __loadModuleDefault / __KEYFRAMES / 页面注册。
 *       这个工具把胶水在桩环境里跑一遍，把它的行为原样打出来，照着写自己的 entry。
 *
 * 用法: ./gluetrace <app.js.bin> <glueboot.js>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "quickjs.h"

static void dump_exc(JSContext *ctx, const char *where)
{
    JSValue e = JS_GetException(ctx);
    const char *s = JS_ToCString(ctx, e);
    printf("[EXC] %s: %s\n", where, s ? s : "?");
    if (s) JS_FreeCString(ctx, s);
    JSValue st = JS_GetPropertyStr(ctx, e, "stack");
    const char *ss = JS_ToCString(ctx, st);
    if (ss) { printf("[EXC] stack: %s\n", ss); JS_FreeCString(ctx, ss); }
    JS_FreeValue(ctx, st);
    JS_FreeValue(ctx, e);
}

static JSValue js_trace(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    const char *a = (argc > 0) ? JS_ToCString(ctx, argv[0]) : NULL;
    const char *b = (argc > 1) ? JS_ToCString(ctx, argv[1]) : NULL;
    printf("[T] %s :: %s\n", a ? a : "?", b ? b : "");
    fflush(stdout);
    if (a) JS_FreeCString(ctx, a);
    if (b) JS_FreeCString(ctx, b);
    return JS_UNDEFINED;
}

static int stub_init(JSContext *ctx, JSModuleDef *m)
{
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "ok", JS_TRUE);
    JS_SetModuleExport(ctx, m, "default", o);
    return 0;
}

static JSModuleDef *stub_loader(JSContext *ctx, const char *name, void *opaque)
{
    (void)opaque;
    printf("[IMPORT] %s\n", name ? name : "?");
    fflush(stdout);
    JSModuleDef *m = JS_NewCModule(ctx, name, stub_init);
    if (!m) return NULL;
    JS_AddModuleExport(ctx, m, "default");
    return m;
}

/* 解析 .js.bin 容器头，返回字节码在文件里的偏移；0 = 解析失败 */
static size_t parse_header(const unsigned char *b, size_t len)
{
    if (len < 2 || b[0] != 0x01) return 0;
    size_t p = 1;
    unsigned long count = 0;
    int shift = 0;
    while (p < len) {
        unsigned char c = b[p++];
        count |= (unsigned long)(c & 0x7F) << shift;
        if (!(c & 0x80)) break;
        shift += 7;
    }
    for (unsigned long i = 0; i < count; i++) {
        unsigned long v = 0;
        shift = 0;
        while (p < len) {
            unsigned char c = b[p++];
            v |= (unsigned long)(c & 0x7F) << shift;
            if (!(c & 0x80)) break;
            shift += 7;
        }
        size_t n = (size_t)(v >> 1);
        if (v & 1) n *= 2;              /* wide = UTF-16LE */
        p += n;
        if (p > len) return 0;
    }
    printf("[HDR] 字符串 %lu 个，字节码 @0x%zx（%zu 字节）\n", count, p, len - p);
    return p;
}

static unsigned char *read_all(const char *path, size_t *out_len)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return NULL; }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *buf = (unsigned char *)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return NULL; }
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(buf); return NULL; }
    fclose(f);
    buf[n] = 0;
    *out_len = (size_t)n;
    return buf;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <app.js.bin> <glueboot.js>\n", argv[0]);
        return 2;
    }
    size_t blen = 0, boot_len = 0;
    unsigned char *bin = read_all(argv[1], &blen);
    if (!bin) return 1;
    unsigned char *boot = read_all(argv[2], &boot_len);
    if (!boot) return 1;

    size_t off = parse_header(bin, blen);
    if (!off) { fprintf(stderr, "容器格式不识别\n"); return 1; }

    JSRuntime *rt = JS_NewRuntime();
    JSContext *ctx = JS_NewContext(rt);
    JS_SetModuleLoaderFunc(rt, NULL, stub_loader, NULL);

    JSValue g = JS_GetGlobalObject(ctx);
    JS_SetPropertyStr(ctx, g, "__trace", JS_NewCFunction(ctx, js_trace, "__trace", 2));
    JS_FreeValue(ctx, g);

    /* 1) 先立框架桩 */
    JSValue bv = JS_Eval(ctx, (const char *)boot, boot_len, "glueboot.js", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(bv)) { dump_exc(ctx, "glueboot"); return 3; }
    JS_FreeValue(ctx, bv);

    /* 2) 执行官方 app.js.bin */
    printf("[RUN] 执行 %s\n", argv[1]);
    fflush(stdout);
    /* 关键：容器头本身就是 QuickJS 字节码的一部分（首字节 0x01 = 本厂商版的 BC_VERSION），
     * 必须把整个文件喂进去 —— 这是 jsfmc 能跑通的做法，跳过头部会报 invalid version。 */
    JSValue obj = JS_ReadObject(ctx, bin, blen, 0);
    if (JS_IsException(obj)) { dump_exc(ctx, "JS_ReadObject"); return 4; }
    JSValue r = JS_EvalFunction(ctx, obj);
    if (JS_IsException(r)) dump_exc(ctx, "JS_EvalFunction");
    else JS_FreeValue(ctx, r);

    JSContext *jc = NULL;
    int jobs = 0;
    while (JS_ExecutePendingJob(rt, &jc) > 0 && jobs < 200) jobs++;
    printf("[OK] 执行结束，挂起任务 %d\n", jobs);

    /* 3) 出报告 */
    JSValue rep = JS_Eval(ctx, "__snapshot(); __report();", 25, "report", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(rep)) dump_exc(ctx, "__report");
    else JS_FreeValue(ctx, rep);

    fflush(stdout);
    return 0;
}
