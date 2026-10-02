/* jsfmc.c —— 有道词典笔 miniapp 的 JS 工具（用**笔上的 libquickjs.so**，保证字节码与宿主完全匹配）
 *
 * 模式：
 *   -o out.js.bin -n module.js in.js     把 JS 源码编译成 .js.bin
 *   -g                                    按"全局脚本"编译（默认按 ES module）
 *   -d file.js.bin                        反查：读字节码 → 解析模块 → 执行 → 打印导出/全局变化
 *
 * 为什么必须用笔上的库：.js.bin 就是 JS_WriteObject(ctx,&len,obj,JS_WRITE_OBJ_BYTECODE)
 * 的原始输出，格式与 QuickJS 版本/厂商补丁强相关；笔上的 QuickJS 是打过补丁的（比上游大 50%）。
 *
 * .js.bin 结构：
 *   [0x01][varint 字符串数][N×(varint (字节长<<1|wide) + 内容)][字节码体]
 *   字符串表 = atom 表（模块名 / import 说明符 / 标识符 / 字符串常量），顺序即索引。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "quickjs.h"

static int g_global = 0;

static uint8_t *read_file(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return NULL; }
    uint8_t *buf = (uint8_t *)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return NULL; }
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) { free(buf); fclose(f); return NULL; }
    buf[n] = 0;
    fclose(f);
    *len = (size_t)n;
    return buf;
}

static const char *type_of(JSContext *ctx, JSValueConst v)
{
    if (JS_IsUndefined(v)) return "undefined";
    if (JS_IsNull(v)) return "null";
    if (JS_IsBool(v)) return "boolean";
    if (JS_IsNumber(v)) return "number";
    if (JS_IsString(v)) return "string";
    if (JS_IsFunction(ctx, v)) return "function";
    if (JS_IsObject(v)) return "object";
    return "?";
}

/* 模块解析桩：真机上 app 的 JS 会 import 'fs' / 'term' 等，
 * 这里只是编译/反查，给个空壳模块即可（否则编译期就 ReferenceError）。 */
static int stub_module_init(JSContext *ctx, JSModuleDef *m) { (void)ctx; (void)m; return 0; }

static JSModuleDef *stub_module_loader(JSContext *ctx, const char *module_name, void *opaque)
{
    (void)opaque;
    JSModuleDef *m = JS_NewCModule(ctx, module_name, stub_module_init);
    if (m) {
        JS_AddModuleExport(ctx, m, "default");
        JS_AddModuleExport(ctx, m, "FileServer");
        JS_AddModuleExport(ctx, m, "fileServer");
        JS_AddModuleExport(ctx, m, "Shell");
        JS_AddModuleExport(ctx, m, "shell");
        JS_AddModuleExport(ctx, m, "Term");
        JS_AddModuleExport(ctx, m, "term");
    }
    return m;
}

/* console.log/warn 落到 C 的 stdout，便于在反查时看到模块内部日志 */
static JSValue js_print(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    for (int i = 0; i < argc; i++) {
        const char *s = JS_ToCString(ctx, argv[i]);
        printf("      [js] %s\n", s ? s : "(?)");
        if (s) JS_FreeCString(ctx, s);
    }
    return JS_UNDEFINED;
}

static void install_stubs(JSContext *ctx)
{
    JSValue g = JS_GetGlobalObject(ctx);
    JS_SetPropertyStr(ctx, g, "__print", JS_NewCFunction(ctx, js_print, "__print", 1));
    JS_FreeValue(ctx, g);
    const char *stubs =
        "globalThis.console = { log:function(){__print.apply(null,arguments)},"
        "  warn:function(){__print.apply(null,arguments)},"
        "  error:function(){__print.apply(null,arguments)} };"
        "globalThis.window = globalThis;"
        "globalThis.process = { env:{ NODE_ENV:'production' } };"
        "globalThis.requestAnimationFrame = function(){ return 0 };"
        "globalThis.cancelAnimationFrame = function(){};"
        "globalThis.__loadModuleDefault = function(m){"
        "  if (m && typeof m === 'object' && 'default' in m) return m.default; return m; };"
        "class FPage { constructor(){} on(){} off(){} trigger(){} release(){} }"
        "class FApp { constructor(){} onLaunch(){} onShow(){} onHide(){} onDestroy(){} setViewPort(){} }"
        "globalThis.$falcon = { App: FApp, Page: FPage, on:function(){return 0}, off:function(){},"
        "  navTo:function(){}, finish:function(){}, useDefaultBasePageClass:function(){}, setViewPort:function(){} };";
    JSValue r = JS_Eval(ctx, stubs, strlen(stubs), "<stubs>", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(r)) {
        JSValue e = JS_GetException(ctx);
        const char *s = JS_ToCString(ctx, e);
        fprintf(stderr, "[!] stub 初始化失败: %s\n", s ? s : "?");
    }
    JS_FreeValue(ctx, r);
}

static void dump_header(const uint8_t *d, size_t n)
{
    if (n < 2) { printf("    (太短)\n"); return; }
    size_t i = 1;
    unsigned cnt = 0, shift = 0;
    while (i < n) {
        uint8_t b = d[i++];
        cnt |= (unsigned)(b & 0x7f) << shift;
        if (!(b & 0x80)) break;
        shift += 7;
    }
    printf("    ver=%u 字符串数=%u\n", d[0], cnt);
}

static void print_props(JSContext *ctx, JSValueConst obj, const char *label)
{
    if (!JS_IsObject(obj)) { printf("    %s: 不是对象，跳过\n", label); return; }
    JSPropertyEnum *tab = NULL; uint32_t n = 0;
    if (JS_GetOwnPropertyNames(ctx, &tab, &n, obj, JS_GPN_STRING_MASK) != 0) {
        printf("    %s: 枚举失败\n", label);
        return;
    }
    printf("    %s (%u 个):\n", label, n);
    for (uint32_t i = 0; i < n && i < 40; i++) {
        const char *name = JS_AtomToCString(ctx, tab[i].atom);
        JSValue v = JS_GetProperty(ctx, obj, tab[i].atom);
        printf("      %-26s %s%s\n", name ? name : "?", type_of(ctx, v),
               JS_IsFunction(ctx, v) ? " (可调用)" : "");
        JS_FreeValue(ctx, v);
        JS_FreeCString(ctx, name);
        JS_FreeAtom(ctx, tab[i].atom);
    }
    js_free(ctx, tab);
}

/* ---------------- 反查 ---------------- */
static int dump_module(const char *path)
{
    size_t len = 0;
    uint8_t *buf = read_file(path, &len);
    if (!buf) { fprintf(stderr, "[!] 读不到 %s\n", path); return 1; }

    JSRuntime *rt = JS_NewRuntime();
    JSContext *ctx = JS_NewContext(rt);
    JS_SetModuleLoaderFunc(rt, NULL, stub_module_loader, NULL);
    install_stubs(ctx);

    printf("[*] %s (%zu 字节)\n", path, len);
    dump_header(buf, len);

    JSValue obj = JS_ReadObject(ctx, buf, len, JS_READ_OBJ_BYTECODE);
    if (JS_IsException(obj)) {
        JSValue e = JS_GetException(ctx);
        const char *s = JS_ToCString(ctx, e);
        fprintf(stderr, "[!] JS_ReadObject 失败: %s\n", s ? s : "?");
        return 2;
    }
    int tag = JS_VALUE_GET_TAG(obj);
    printf("    tag=%d  (MODULE=%d, FUNCTION_BYTECODE=%d, OBJECT=%d)\n",
           tag, (int)JS_TAG_MODULE, (int)JS_TAG_FUNCTION_BYTECODE, (int)JS_TAG_OBJECT);

    if (tag == JS_TAG_MODULE) {
        int rr = JS_ResolveModule(ctx, obj);
        printf("    JS_ResolveModule -> %d\n", rr);
    }

    printf("    --- 执行模块 ---\n");
    JSValue res = JS_EvalFunction(ctx, obj);
    if (JS_IsException(res)) {
        JSValue e = JS_GetException(ctx);
        const char *s = JS_ToCString(ctx, e);
        fprintf(stderr, "[!] 执行异常: %s\n", s ? s : "?");
        return 3;
    }
    printf("    执行返回 tag=%d 类型=%s\n", JS_VALUE_GET_TAG(res), type_of(ctx, res));

    JSContext *jctx = NULL;
    int jobs = 0;
    while (JS_ExecutePendingJob(rt, &jctx) > 0 && jobs < 1000) jobs++;
    printf("    跑了 %d 个挂起任务（模块求值通常在这里完成）\n", jobs);
    printf("    任务后返回 tag=%d 类型=%s\n", JS_VALUE_GET_TAG(res), type_of(ctx, res));
    print_props(ctx, res, "执行返回值上的属性");

    JSValue g = JS_GetGlobalObject(ctx);
    const char *names[] = {"__AppClazz", "__pages", "__loadModuleDefault", "__TERM_MARK", "App", "default"};
    printf("    关键全局量:\n");
    for (int i = 0; i < 6; i++) {
        JSValue v = JS_GetPropertyStr(ctx, g, names[i]);
        printf("      globalThis.%-20s %s\n", names[i], type_of(ctx, v));
        JS_FreeValue(ctx, v);
    }
    JSValue pages = JS_GetPropertyStr(ctx, g, "__pages");
    if (JS_IsObject(pages)) print_props(ctx, pages, "__pages");
    JS_FreeValue(ctx, pages);
    JS_FreeValue(ctx, g);

    return 0;
}

/* ---------------- 编译 ---------------- */
static int compile_one(const char *in, const char *out, const char *modname)
{
    size_t srclen = 0;
    uint8_t *src = read_file(in, &srclen);
    if (!src) { fprintf(stderr, "[!] 读不到 %s\n", in); return 1; }
    if (!modname) modname = in;

    JSRuntime *rt = JS_NewRuntime();
    JSContext *ctx = JS_NewContext(rt);
    JS_SetModuleLoaderFunc(rt, NULL, stub_module_loader, NULL);

    int etype = g_global ? JS_EVAL_TYPE_GLOBAL : JS_EVAL_TYPE_MODULE;
    JSValue obj = JS_Eval(ctx, (const char *)src, srclen, modname,
                          etype | JS_EVAL_FLAG_COMPILE_ONLY);
    if (JS_IsException(obj)) {
        JSValue e = JS_GetException(ctx);
        const char *s = JS_ToCString(ctx, e);
        fprintf(stderr, "[!] 编译失败 %s: %s\n", in, s ? s : "(?)");
        if (s) JS_FreeCString(ctx, s);
        return 2;
    }

    size_t len = 0;
    uint8_t *buf = JS_WriteObject(ctx, &len, obj, JS_WRITE_OBJ_BYTECODE);
    if (!buf || len == 0) { fprintf(stderr, "[!] JS_WriteObject 失败\n"); return 3; }

    printf("[+] %s (%zuB 源码) -> %zuB 字节码  module=%s  type=%s\n",
           in, srclen, len, modname, g_global ? "global" : "module");
    if (out) {
        FILE *f = fopen(out, "wb");
        if (!f) { fprintf(stderr, "[!] 写不了 %s\n", out); return 4; }
        fwrite(buf, 1, len, f);
        fclose(f);
    } else {
        dump_header(buf, len);
    }

    js_free(ctx, buf);
    JS_FreeValue(ctx, obj);
    JS_FreeContext(ctx);
    JS_FreeRuntime(rt);
    free(src);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr,
                "usage:\n"
                "  %s [-g] -o out.js.bin [-n module.js] in.js   # 编译（-g=全局脚本）\n"
                "  %s -d file.js.bin                            # 反查导出/全局\n",
                argv[0], argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "-d") == 0) return dump_module(argv[2]);
    if (strcmp(argv[1], "-g") == 0) { g_global = 1; argv++; argc--; }

    const char *out = NULL, *modname = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) out = argv[++i];
        else if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) modname = argv[++i];
        else {
            int rc = compile_one(argv[i], out, modname);
            if (rc) return rc;
            out = NULL; modname = NULL;
        }
    }
    return 0;
}
