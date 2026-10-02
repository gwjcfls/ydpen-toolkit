/* termtest.c —— 终端插件独立测试宿主
 *
 * 作用：在没有 miniapp 框架的情况下，模拟框架给插件提供的环境：
 *   - registerCModuleLoader() 桩：插件调用它注册模块，我们存下来
 *   - 模块解析器：把 JS 里的 import 'term' 路由到插件注册的 loader
 *   - console.log 输出到 stdout
 *   - 跑完 JS 后把待执行任务队列跑干净
 *
 * 用法：./termtest test.js
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "quickjs.h"

void custom_init_jsapis(void);

static JSModuleDef *(*g_loader)(JSContext *ctx, const char *module_name) = NULL;

/* 插件要用的框架符号 */
void registerCModuleLoader(const char *name, JSModuleDef *(*loader)(JSContext *, const char *))
{
    printf("[host] registerCModuleLoader(%s) -> %p\n", name, (void *)loader);
    g_loader = loader;
}

static JSModuleDef *host_module_loader(JSContext *ctx, const char *module_name, void *opaque)
{
    (void)opaque;
    printf("[host] module requested: %s\n", module_name);
    if (g_loader) return g_loader(ctx, module_name);
    return NULL;
}

static JSValue js_print(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    for (int i = 0; i < argc; i++) {
        const char *s = JS_ToCString(ctx, argv[i]);
        printf("%s%s", i ? " " : "", s ? s : "(?)");
        if (s) JS_FreeCString(ctx, s);
    }
    printf("\n");
    fflush(stdout);
    return JS_UNDEFINED;
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: %s test.js\n", argv[0]); return 2; }

    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("open"); return 1; }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *src = (char *)malloc((size_t)n + 1);
    if (fread(src, 1, (size_t)n, f) != (size_t)n) { perror("read"); return 1; }
    src[n] = 0;
    fclose(f);

    JSRuntime *rt = JS_NewRuntime();
    JSContext *ctx = JS_NewContext(rt);
    JS_SetModuleLoaderFunc(rt, NULL, host_module_loader, NULL);

    JSValue g = JS_GetGlobalObject(ctx);
    JSValue con = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, con, "log", JS_NewCFunction(ctx, js_print, "log", 1));
    JS_SetPropertyStr(ctx, con, "warn", JS_NewCFunction(ctx, js_print, "warn", 1));
    JS_SetPropertyStr(ctx, con, "error", JS_NewCFunction(ctx, js_print, "error", 1));
    JS_SetPropertyStr(ctx, g, "console", con);
    JS_FreeValue(ctx, g);

    custom_init_jsapis();
    printf("[host] custom_init_jsapis() 已执行\n");

    JSValue v = JS_Eval(ctx, src, (size_t)n, argv[1], JS_EVAL_TYPE_MODULE);
    if (JS_IsException(v)) {
        JSValue e = JS_GetException(ctx);
        const char *s = JS_ToCString(ctx, e);
        fprintf(stderr, "[host] JS 异常: %s\n", s ? s : "(?)");
        if (s) JS_FreeCString(ctx, s);
        return 3;
    }
    JSContext *jctx = NULL;
    int jobs = 0;
    while (JS_ExecutePendingJob(rt, &jctx) > 0) jobs++;
    printf("[host] 完成，挂起任务 %d\n", jobs);
    return 0;
}
