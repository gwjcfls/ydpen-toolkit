/*
 * fs_plugin.c —— 给有道词典笔 PenOS (falcon/aiot miniapp 框架) 补一个 `fs` jsapi
 *
 * 逆向出来的插件契约（用 file-manager 自带的 libjsapi_shell.so 反汇编确认）：
 *
 *   extern "C" void custom_init_jsapis(void) {
 *       registerCModuleLoader("模块名", loader);
 *   }
 *   JSModuleDef *loader(JSContext *ctx, const char *module_name) {
 *       if (strcmp(module_name, "模块名")) return NULL;
 *       JSModuleDef *m = JS_NewCModule(ctx, module_name, module_init);
 *       JS_AddModuleExport(ctx, m, "default");
 *       return m;
 *   }
 *
 * 需要的 fs API（从 doge-reader / doge-comic 源码里统计出来的实际用法）：
 *   await fs.exists(p)                       -> boolean
 *   await fs.mkdir(p)                        -> 真值表示成功（支持多级）
 *   await fs.rm(p)                           -> 真值表示成功（递归删目录）
 *   await fs.unlink(p)                       -> 删文件
 *   await fs.readdir(p)                      -> ['a.txt', 'dir', ...]
 *   await fs.readdir(p, {withFileTypes:true})-> [{name, isDirectory(), isFile()}]
 *   await fs.stat(p)                         -> {size, mode, mtimeMs, isFile, isDirectory}
 *   await fs.readFile(p)                     -> 文本（UTF-8）
 *   await fs.writeFile(p, text)              -> undefined
 *
 * 编译（zig 交叉编译，目标 aarch64 + glibc 2.29）：
 *   zig cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 \
 *          -I quickjs fs_plugin.c -o libjsapi_fs_1000000001.so
 */

#include "quickjs.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

/* 宿主（miniapp 进程 / libquickjs）导出的注册函数 */
extern void registerCModuleLoader(const char *name,
                                  JSModuleDef *(*loader)(JSContext *ctx, const char *module_name));

/* ------------------------------------------------------------------ 工具 */

static JSValue make_error(JSContext *ctx, const char *op)
{
    return JS_ThrowInternalError(ctx, "%s: %s", op, strerror(errno));
}

static JSValue promise_resolved(JSContext *ctx, JSValue value)
{
    JSValue funcs[2];
    JSValue promise = JS_NewPromiseCapability(ctx, funcs);

    if (JS_IsException(promise)) {
        JS_FreeValue(ctx, value);
        return promise;
    }

    JSValue arg = value;
    JSValue ret = JS_Call(ctx, funcs[0], JS_UNDEFINED, 1, (JSValueConst *)&arg);
    JS_FreeValue(ctx, ret);
    JS_FreeValue(ctx, funcs[0]);
    JS_FreeValue(ctx, funcs[1]);
    JS_FreeValue(ctx, value);

    return promise;
}

static JSValue promise_rejected(JSContext *ctx, JSValue error)
{
    JSValue funcs[2];
    JSValue promise = JS_NewPromiseCapability(ctx, funcs);

    if (JS_IsException(promise)) {
        JS_FreeValue(ctx, error);
        return promise;
    }

    JSValue arg = error;
    JSValue ret = JS_Call(ctx, funcs[1], JS_UNDEFINED, 1, (JSValueConst *)&arg);
    JS_FreeValue(ctx, ret);
    JS_FreeValue(ctx, funcs[0]);
    JS_FreeValue(ctx, funcs[1]);
    JS_FreeValue(ctx, error);

    return promise;
}

static int mkdir_p(const char *path)
{
    char tmp[PATH_MAX];
    size_t len = strlen(path);

    if (len == 0 || len >= sizeof(tmp))
        return -1;

    memcpy(tmp, path, len + 1);

    if (len > 1 && tmp[len - 1] == '/')
        tmp[len - 1] = '\0';

    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (mkdir(tmp, 0755) != 0 && errno != EEXIST)
                return -1;
            *p = '/';
        }
    }

    if (mkdir(tmp, 0755) != 0 && errno != EEXIST)
        return -1;

    return 0;
}

static int rm_rf(const char *path)
{
    struct stat st;

    if (lstat(path, &st) != 0)
        return -1;

    if (S_ISDIR(st.st_mode)) {
        DIR *d = opendir(path);
        int rc = 0;

        if (!d)
            return -1;

        struct dirent *e;
        while ((e = readdir(d))) {
            char child[PATH_MAX];

            if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
                continue;

            snprintf(child, sizeof(child), "%s/%s", path, e->d_name);

            if (rm_rf(child) != 0)
                rc = -1;
        }

        closedir(d);

        if (rmdir(path) != 0)
            rc = -1;

        return rc;
    }

    return unlink(path);
}

/* ------------------------------------------------- dirent 对象的两个方法 */

static JSValue dirent_is_dir(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    JSValue p = JS_GetPropertyStr(ctx, this_val, "_p");
    const char *path = JS_ToCString(ctx, p);
    struct stat st;
    int is_dir = 0;

    if (path) {
        is_dir = (stat(path, &st) == 0 && S_ISDIR(st.st_mode));
        JS_FreeCString(ctx, path);
    }

    JS_FreeValue(ctx, p);

    return JS_NewBool(ctx, is_dir);
}

static JSValue dirent_is_file(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    JSValue p = JS_GetPropertyStr(ctx, this_val, "_p");
    const char *path = JS_ToCString(ctx, p);
    struct stat st;
    int is_file = 0;

    if (path) {
        is_file = (stat(path, &st) == 0 && S_ISREG(st.st_mode));
        JS_FreeCString(ctx, path);
    }

    JS_FreeValue(ctx, p);

    return JS_NewBool(ctx, is_file);
}

/* --------------------------------------------------------------- 各方法 */

static JSValue fs_exists(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    struct stat st;
    int ok;

    if (!path)
        return JS_EXCEPTION;

    ok = (stat(path, &st) == 0);
    JS_FreeCString(ctx, path);

    return promise_resolved(ctx, JS_NewBool(ctx, ok));
}

static JSValue fs_mkdir(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    int ok;

    if (!path)
        return JS_EXCEPTION;

    ok = (mkdir_p(path) == 0);
    JS_FreeCString(ctx, path);

    return promise_resolved(ctx, JS_NewBool(ctx, ok));
}

static JSValue fs_rm(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    int ok;

    if (!path)
        return JS_EXCEPTION;

    ok = (rm_rf(path) == 0);
    JS_FreeCString(ctx, path);

    return promise_resolved(ctx, JS_NewBool(ctx, ok));
}

static JSValue fs_unlink(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    int rc;

    if (!path)
        return JS_EXCEPTION;

    rc = unlink(path);
    JS_FreeCString(ctx, path);

    if (rc != 0)
        return promise_rejected(ctx, make_error(ctx, "unlink"));

    return promise_resolved(ctx, JS_UNDEFINED);
}

static JSValue fs_readdir(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    int with_types = 0;
    DIR *dir;
    JSValue arr;
    struct dirent *ent;
    uint32_t idx = 0;

    if (!path)
        return JS_EXCEPTION;

    if (argc > 1 && JS_IsObject(argv[1])) {
        JSValue wt = JS_GetPropertyStr(ctx, argv[1], "withFileTypes");

        if (!JS_IsUndefined(wt))
            with_types = (JS_ToBool(ctx, wt) > 0);

        JS_FreeValue(ctx, wt);
    }

    dir = opendir(path);

    if (!dir) {
        JS_FreeCString(ctx, path);
        return promise_rejected(ctx, make_error(ctx, "readdir"));
    }

    arr = JS_NewArray(ctx);

    while ((ent = readdir(dir))) {
        if (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, ".."))
            continue;

        if (with_types) {
            JSValue obj = JS_NewObject(ctx);
            char full[PATH_MAX];

            snprintf(full, sizeof(full), "%s/%s", path, ent->d_name);

            JS_SetPropertyStr(ctx, obj, "name", JS_NewString(ctx, ent->d_name));
            JS_SetPropertyStr(ctx, obj, "isDirectory",
                              JS_NewCFunction(ctx, dirent_is_dir, "isDirectory", 0));
            JS_SetPropertyStr(ctx, obj, "isFile",
                              JS_NewCFunction(ctx, dirent_is_file, "isFile", 0));
            JS_SetPropertyStr(ctx, obj, "path", JS_NewString(ctx, full));
            JS_SetPropertyStr(ctx, obj, "_p", JS_NewString(ctx, full));

            JS_SetPropertyUint32(ctx, arr, idx++, obj);
        } else {
            JS_SetPropertyUint32(ctx, arr, idx++, JS_NewString(ctx, ent->d_name));
        }
    }

    closedir(dir);
    JS_FreeCString(ctx, path);

    return promise_resolved(ctx, arr);
}

static JSValue fs_stat(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    struct stat st;
    JSValue obj;

    if (!path)
        return JS_EXCEPTION;

    if (stat(path, &st) != 0) {
        JS_FreeCString(ctx, path);
        return promise_rejected(ctx, make_error(ctx, "stat"));
    }

    JS_FreeCString(ctx, path);

    obj = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, obj, "size", JS_NewInt64(ctx, (int64_t)st.st_size));
    JS_SetPropertyStr(ctx, obj, "mode", JS_NewInt32(ctx, (int32_t)st.st_mode));
    JS_SetPropertyStr(ctx, obj, "mtimeMs", JS_NewFloat64(ctx, (double)st.st_mtime * 1000.0));
    JS_SetPropertyStr(ctx, obj, "isFile", JS_NewBool(ctx, S_ISREG(st.st_mode)));
    JS_SetPropertyStr(ctx, obj, "isDirectory", JS_NewBool(ctx, S_ISDIR(st.st_mode)));

    return promise_resolved(ctx, obj);
}

static JSValue fs_readFile(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    FILE *fp;
    long size;
    char *buf;
    JSValue result;

    if (!path)
        return JS_EXCEPTION;

    fp = fopen(path, "rb");

    if (!fp) {
        JS_FreeCString(ctx, path);
        return promise_rejected(ctx, make_error(ctx, "readFile"));
    }

    fseek(fp, 0, SEEK_END);
    size = ftell(fp);
    rewind(fp);

    if (size < 0) {
        fclose(fp);
        JS_FreeCString(ctx, path);
        return promise_rejected(ctx, make_error(ctx, "readFile"));
    }

    buf = (char *)malloc((size_t)size + 1);

    if (!buf) {
        fclose(fp);
        JS_FreeCString(ctx, path);
        return promise_rejected(ctx, JS_ThrowOutOfMemory(ctx));
    }

    if (size > 0 && fread(buf, 1, (size_t)size, fp) != (size_t)size) {
        free(buf);
        fclose(fp);
        JS_FreeCString(ctx, path);
        return promise_rejected(ctx, make_error(ctx, "readFile"));
    }

    fclose(fp);
    buf[size] = '\0';

    result = JS_NewStringLen(ctx, buf, (size_t)size);
    free(buf);
    JS_FreeCString(ctx, path);

    return promise_resolved(ctx, result);
}

static JSValue fs_writeFile(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *path = JS_ToCString(ctx, argv[0]);
    size_t len = 0;
    const char *data;
    FILE *fp;

    if (!path)
        return JS_EXCEPTION;

    data = JS_ToCStringLen(ctx, &len, argv[1]);

    if (!data) {
        JS_FreeCString(ctx, path);
        return JS_EXCEPTION;
    }

    fp = fopen(path, "wb");

    if (!fp) {
        JS_FreeCString(ctx, path);
        JS_FreeCString(ctx, data);
        return promise_rejected(ctx, make_error(ctx, "writeFile"));
    }

    fwrite(data, 1, len, fp);
    fclose(fp);
    JS_FreeCString(ctx, path);
    JS_FreeCString(ctx, data);

    return promise_resolved(ctx, JS_UNDEFINED);
}

/* ------------------------------------------------------------ 模块注册 */

static int fs_module_init(JSContext *ctx, JSModuleDef *m)
{
    JSValue fs = JS_NewObject(ctx);

    JS_SetPropertyStr(ctx, fs, "exists", JS_NewCFunction(ctx, fs_exists, "exists", 1));
    JS_SetPropertyStr(ctx, fs, "mkdir", JS_NewCFunction(ctx, fs_mkdir, "mkdir", 1));
    JS_SetPropertyStr(ctx, fs, "rm", JS_NewCFunction(ctx, fs_rm, "rm", 1));
    JS_SetPropertyStr(ctx, fs, "rmdir", JS_NewCFunction(ctx, fs_rm, "rmdir", 1));
    JS_SetPropertyStr(ctx, fs, "unlink", JS_NewCFunction(ctx, fs_unlink, "unlink", 1));
    JS_SetPropertyStr(ctx, fs, "readdir", JS_NewCFunction(ctx, fs_readdir, "readdir", 2));
    JS_SetPropertyStr(ctx, fs, "stat", JS_NewCFunction(ctx, fs_stat, "stat", 1));
    JS_SetPropertyStr(ctx, fs, "readFile", JS_NewCFunction(ctx, fs_readFile, "readFile", 1));
    JS_SetPropertyStr(ctx, fs, "writeFile", JS_NewCFunction(ctx, fs_writeFile, "writeFile", 2));

    JS_SetModuleExport(ctx, m, "default", fs);

    return 0;
}

static JSModuleDef *fs_module_loader(JSContext *ctx, const char *module_name)
{
    JSModuleDef *m;

    if (strcmp(module_name, "fs") != 0)
        return NULL;

    m = JS_NewCModule(ctx, module_name, fs_module_init);

    if (!m)
        return NULL;

    JS_AddModuleExport(ctx, m, "default");

    return m;
}

/* 框架 dlsym 的入口 */
void custom_init_jsapis(void)
{
    registerCModuleLoader("fs", fs_module_loader);
}
