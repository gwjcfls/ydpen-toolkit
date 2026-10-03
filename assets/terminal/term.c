/* term.c —— 词典笔「终端」jsapi 原生插件（模块名 term）
 *
 * 给 JS 侧的能力（全部同步/轮询式，**绝不跨线程回调 JS**，避免框架黑屏）：
 *   term.info()                     环境信息
 *   term.spawn(cmd, opts)           PTY 起一个进程（opts: cols/rows/cwd/env/args/autopassword）
 *   term.spawnShell(cwd)            本地 /bin/sh -i
 *   term.spawnSsh(host,user,port,pw)  /bin/ssh -tt user@host -p port（可自动填密码）
 *   term.write(sid, data)           写入
 *   term.read(sid[,max])            非阻塞取走新输出
 *   term.resize(sid, cols, rows)    TIOCSWINSZ
 *   term.alive(sid) / term.kill(sid) / term.sessions()
 *   term.execSync(cmd, timeoutMs)   一次性执行并等结果 {code,out}
 *   term.sshdStart(port, pubkey?)   在笔上起 OpenSSH sshd（供电脑 ssh 进来）
 *   term.sshdStop() / term.sshdStatus()
 *
 * 设计要点：
 *   - PTY 用 forkpty()（glibc/libutil）。子进程在 execve 前只用 async-signal-safe 调用，
 *     argv/envp 在 fork 之前就构造好，避免多线程进程里 fork 后 malloc 死锁。
 *   - 每个会话一个 reader 线程，把 PTY 输出塞进环形缓冲；JS 侧用 setInterval 轮询 read()。
 *   - 自动填密码在 reader 线程里做（匹配 "password:" 就 write 密码），不碰 JS。
 */

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

#include "quickjs.h"
#include "term_vt.c"   /* VT 仿真 + PNG 渲染（自带 font8x16.h） */

#define MAX_SESSIONS 8
#define RING_SIZE (256 * 1024)

typedef struct {
    int used;
    int sid;
    int master;
    pid_t pid;
    volatile int alive;
    char cmd[256];
    char autopw[128];
    int autopw_sent;
    unsigned char *ring;
    size_t head, tail;          /* head=写入位置 tail=读取位置 */
    pthread_mutex_t lock;
    pthread_t reader;
    vt_t *vt;
    char vt_dir[200];
    int reader_started;
} session_t;

static session_t g_sess[MAX_SESSIONS];
static pthread_mutex_t g_sess_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_next_sid = 1;

/* ------------------------------------------------------------------ 基础工具 */

static void ring_push(session_t *s, const unsigned char *buf, size_t n)
{
    pthread_mutex_lock(&s->lock);
    for (size_t i = 0; i < n; i++) {
        size_t nh = (s->head + 1) % RING_SIZE;
        if (nh == s->tail) s->tail = (s->tail + 1) % RING_SIZE;   /* 满了丢最老的 */
        s->ring[s->head] = buf[i];
        s->head = nh;
    }
    pthread_mutex_unlock(&s->lock);
}

static size_t ring_pop(session_t *s, unsigned char *out, size_t max)
{
    size_t n = 0;
    pthread_mutex_lock(&s->lock);
    while (n < max && s->tail != s->head) {
        out[n++] = s->ring[s->tail];
        s->tail = (s->tail + 1) % RING_SIZE;
    }
    pthread_mutex_unlock(&s->lock);
    return n;
}

/* reader 线程：读 PTY -> 环形缓冲；顺带处理自动填密码 */
static void *reader_main(void *arg)
{
    session_t *s = (session_t *)arg;
    unsigned char buf[4096];
    char tailbuf[512];
    size_t tail_len = 0;

    for (;;) {
        ssize_t n = read(s->master, buf, sizeof(buf));
        if (n > 0) {
            ring_push(s, buf, (size_t)n);
            if (s->autopw[0] && !s->autopw_sent) {
                /* 把最近输出拼起来找 password 提示 */
                size_t take = (size_t)n < sizeof(tailbuf) - tail_len - 1 ? (size_t)n : sizeof(tailbuf) - tail_len - 1;
                memcpy(tailbuf + tail_len, buf, take);
                tail_len += take;
                tailbuf[tail_len] = 0;
                if (strstr(tailbuf, "assword") || strstr(tailbuf, "assword:")) {
                    char line[160];
                    int l = snprintf(line, sizeof(line), "%s\n", s->autopw);
                    if (write(s->master, line, (size_t)l) < 0) { /* ignore */ }
                    s->autopw_sent = 1;
                }
                if (tail_len > 400) {
                    memmove(tailbuf, tailbuf + tail_len - 200, 200);
                    tail_len = 200;
                    tailbuf[tail_len] = 0;
                }
            }
            continue;
        }
        if (n == 0) break;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            struct pollfd p = { s->master, POLLIN, 0 };
            poll(&p, 1, 200);
            continue;
        }
        break;
    }
    s->alive = 0;
    return NULL;
}

static int get_int(JSContext *ctx, JSValueConst v) { int32_t t = 0; JS_ToInt32(ctx, &t, v); return (int)t; }

static session_t *find_sid(int sid)
{
    for (int i = 0; i < MAX_SESSIONS; i++)
        if (g_sess[i].used && g_sess[i].sid == sid) return &g_sess[i];
    return NULL;
}

static session_t *alloc_session(void)
{
    pthread_mutex_lock(&g_sess_lock);
    for (int i = 0; i < MAX_SESSIONS; i++) {
        if (!g_sess[i].used) {
            memset(&g_sess[i], 0, sizeof(g_sess[i]));
            g_sess[i].used = 1;
            g_sess[i].sid = g_next_sid++;
            g_sess[i].autopw_sent = 0;
            pthread_mutex_init(&g_sess[i].lock, NULL);
            pthread_mutex_unlock(&g_sess_lock);
            return &g_sess[i];
        }
    }
    pthread_mutex_unlock(&g_sess_lock);
    return NULL;
}

static void free_session(session_t *s)
{
    if (s->master >= 0) { close(s->master); s->master = -1; }
    if (s->reader_started) { pthread_join(s->reader, NULL); s->reader_started = 0; }
    if (s->ring) { free(s->ring); s->ring = NULL; }
    if (s->vt) { vt_free(s->vt); free(s->vt); s->vt = NULL; }
    pthread_mutex_destroy(&s->lock);
    s->used = 0;
}

/* forkpty + exec；argv/envp 必须在 fork 前构造好 */
static session_t *spawn_pty(const char *path, char *const argv[], char *const envp[],
                            int cols, int rows, const char *cmdline, const char *autopw)
{
    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_col = (unsigned short)(cols > 0 ? cols : 80);
    ws.ws_row = (unsigned short)(rows > 0 ? rows : 24);

    int master = -1;
    pid_t pid = forkpty(&master, NULL, NULL, &ws);
    if (pid < 0) return NULL;
    if (pid == 0) {
        /* 子进程：只做 async-signal-safe 的事，然后 exec */
        if (envp) execve(path, argv, envp);
        else execv(path, argv);
        _exit(127);
    }

    session_t *s = alloc_session();
    if (!s) { close(master); kill(pid, SIGKILL); return NULL; }
    s->master = master;
    s->pid = pid;
    s->alive = 1;
    s->ring = (unsigned char *)malloc(RING_SIZE);
    if (!s->ring) { free_session(s); return NULL; }
    if (cmdline) { strncpy(s->cmd, cmdline, sizeof(s->cmd) - 1); }
    if (autopw) { strncpy(s->autopw, autopw, sizeof(s->autopw) - 1); }

    int fl = fcntl(master, F_GETFL, 0);
    fcntl(master, F_SETFL, fl | O_NONBLOCK);

    /* 显式设定 termios：框架进程的 stdin 不是 tty，forkpty 继承来的设置不可靠 ——
     * 不设这个，页面发 '\r' 不会被当成回车（命令只回显不执行），'\n' 输出也会变成阶梯状。 */
    {
        struct termios tio;
        if (tcgetattr(master, &tio) == 0) {
            tio.c_iflag |= (ICRNL | IXON | BRKINT);
            tio.c_iflag &= ~(INLCR | IGNCR | ISTRIP);
            tio.c_oflag |= (OPOST | ONLCR);
            tio.c_lflag |= (ICANON | ECHO | ECHOE | ECHOK | ISIG | IEXTEN);
            tio.c_cflag |= (CS8 | CREAD | CLOCAL);
            tio.c_cc[VEOF] = 4;
            tio.c_cc[VERASE] = 127;
            tio.c_cc[VINTR] = 3;
            tio.c_cc[VKILL] = 21;
            tio.c_cc[VQUIT] = 28;
            tio.c_cc[VSUSP] = 26;
            tcsetattr(master, TCSANOW, &tio);
        }
    }

    if (pthread_create(&s->reader, NULL, reader_main, s) == 0) s->reader_started = 1;
    return s;
}

/* ------------------------------------------------------------------ JS 层 */

static JSValue js_info(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "uid", JS_NewInt32(ctx, (int)getuid()));
    JS_SetPropertyStr(ctx, o, "sessions", JS_NewInt32(ctx, MAX_SESSIONS));
    JS_SetPropertyStr(ctx, o, "term", JS_NewString(ctx, "term-plugin 1.0"));
    const char *paths[] = {"/bin/sh", "/bin/bash", "/bin/ssh", "/sbin/sshd", "/sbin/dropbear",
                           "/usr/bin/ssh-keygen", "/bin/curl", "/bin/telnet", NULL};
    JSValue arr = JS_NewArray(ctx);
    uint32_t k = 0;
    for (int i = 0; paths[i]; i++) {
        if (access(paths[i], X_OK) == 0)
            JS_SetPropertyUint32(ctx, arr, k++, JS_NewString(ctx, paths[i]));
    }
    JS_SetPropertyStr(ctx, o, "tools", arr);
    char host[128] = "pen";
    if (gethostname(host, sizeof(host) - 1) != 0) strcpy(host, "pen");
    JS_SetPropertyStr(ctx, o, "hostname", JS_NewString(ctx, host));
    return o;
}

/* 通用：把 JS 参数里的字符串转成 C 串 */
static char *arg_str(JSContext *ctx, JSValueConst v)
{
    const char *s = JS_ToCString(ctx, v);
    return s ? (char *)s : NULL;
}

static JSValue make_sid_result(JSContext *ctx, session_t *s)
{
    JSValue o = JS_NewObject(ctx);
    if (!s) {
        JS_SetPropertyStr(ctx, o, "ok", JS_FALSE);
        JS_SetPropertyStr(ctx, o, "err", JS_NewString(ctx, "no free session / spawn failed"));
        return o;
    }
    JS_SetPropertyStr(ctx, o, "ok", JS_TRUE);
    JS_SetPropertyStr(ctx, o, "sid", JS_NewInt32(ctx, s->sid));
    JS_SetPropertyStr(ctx, o, "pid", JS_NewInt32(ctx, (int)s->pid));
    return o;
}

/* term.spawnShell([cwd]) */
static JSValue js_spawn_shell(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    const char *cwd = (argc > 0) ? JS_ToCString(ctx, argv[0]) : NULL;
    char *cargv[4];
    cargv[0] = (char *)"/bin/sh";
    cargv[1] = (char *)"-i";
    cargv[2] = NULL;
    cargv[3] = NULL;
    char *cenvp[6];
    cenvp[0] = (char *)"TERM=xterm-256color";
    cenvp[1] = (char *)"HOME=/root";
    cenvp[2] = (char *)"PATH=/usr/sbin:/usr/bin:/sbin:/bin";
    cenvp[3] = (char *)"LANG=C.UTF-8";
    cenvp[4] = (char *)"PS1=\\w # ";
    cenvp[5] = NULL;
    if (cwd && cwd[0] == '/') { if (chdir(cwd) != 0) { /* ignore */ } }
    session_t *s = spawn_pty("/bin/sh", cargv, cenvp, 100, 20, "sh -i", NULL);
    if (cwd) JS_FreeCString(ctx, cwd);
    return make_sid_result(ctx, s);
}

/* term.spawn(cmdStr, opts) —— cmdStr 走 /bin/sh -c；opts={cols,rows,autopassword} */
static JSValue js_spawn(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NewString(ctx, "usage: spawn(cmd, opts)");
    const char *cmd = JS_ToCString(ctx, argv[0]);
    if (!cmd) return JS_NewString(ctx, "bad cmd");
    int cols = 100, rows = 20;
    const char *autopw = NULL;
    if (argc > 1 && JS_IsObject(argv[1])) {
        JSValue v = JS_GetPropertyStr(ctx, argv[1], "cols");
        if (JS_IsNumber(v)) { int32_t t; JS_ToInt32(ctx, &t, v); cols = t; }
        JS_FreeValue(ctx, v);
        v = JS_GetPropertyStr(ctx, argv[1], "rows");
        if (JS_IsNumber(v)) { int32_t t; JS_ToInt32(ctx, &t, v); rows = t; }
        JS_FreeValue(ctx, v);
        v = JS_GetPropertyStr(ctx, argv[1], "autopassword");
        if (JS_IsString(v)) autopw = JS_ToCString(ctx, v);
        JS_FreeValue(ctx, v);
    }
    char *cargv[4];
    cargv[0] = (char *)"/bin/sh";
    cargv[1] = (char *)"-c";
    cargv[2] = (char *)cmd;
    cargv[3] = NULL;
    char *cenvp[6];
    cenvp[0] = (char *)"TERM=xterm-256color";
    cenvp[1] = (char *)"HOME=/root";
    cenvp[2] = (char *)"PATH=/usr/sbin:/usr/bin:/sbin:/bin";
    cenvp[3] = (char *)"LANG=C.UTF-8";
    cenvp[4] = NULL;
    cenvp[5] = NULL;
    session_t *s = spawn_pty("/bin/sh", cargv, cenvp, cols, rows, cmd, autopw);
    if (autopw) JS_FreeCString(ctx, autopw);
    JS_FreeCString(ctx, cmd);
    return make_sid_result(ctx, s);
}

/* term.spawnSsh(host, user, port, password, extra) */
static JSValue js_spawn_ssh(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    const char *host = (argc > 0) ? JS_ToCString(ctx, argv[0]) : NULL;
    const char *user = (argc > 1) ? JS_ToCString(ctx, argv[1]) : NULL;
    int port = (argc > 2 && JS_IsNumber(argv[2])) ? get_int(ctx, argv[2]) : 22;
    const char *pw = (argc > 3 && JS_IsString(argv[3])) ? JS_ToCString(ctx, argv[3]) : NULL;
    const char *extra = (argc > 4 && JS_IsString(argv[4])) ? JS_ToCString(ctx, argv[4]) : NULL;
    if (!host) return JS_NewString(ctx, "usage: spawnSsh(host,user,port,password,extra)");

    char target[300];
    snprintf(target, sizeof(target), "%s@%s", (user && user[0]) ? user : "root", host);
    char portstr[16];
    snprintf(portstr, sizeof(portstr), "%d", port);

    char *cargv[12];
    int n = 0;
    cargv[n++] = (char *)"/bin/ssh";
    cargv[n++] = (char *)"-tt";
    cargv[n++] = (char *)"-p";
    cargv[n++] = portstr;
    cargv[n++] = (char *)"-o";
    cargv[n++] = (char *)"StrictHostKeyChecking=no";
    cargv[n++] = (char *)"-o";
    cargv[n++] = (char *)"UserKnownHostsFile=/dev/null";
    if (extra && extra[0]) { cargv[n++] = (char *)extra; }
    cargv[n++] = target;
    cargv[n++] = NULL;

    char *cenvp[4];
    cenvp[0] = (char *)"TERM=xterm-256color";
    cenvp[1] = (char *)"HOME=/root";
    cenvp[2] = (char *)"PATH=/usr/sbin:/usr/bin:/sbin:/bin";
    cenvp[3] = NULL;

    session_t *s = spawn_pty("/bin/ssh", cargv, cenvp, 100, 20, target, pw);
    if (pw) JS_FreeCString(ctx, pw);
    if (extra) JS_FreeCString(ctx, extra);
    if (user) JS_FreeCString(ctx, user);
    JS_FreeCString(ctx, host);
    return make_sid_result(ctx, s);
}

static JSValue js_write(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 2) return JS_NewInt32(ctx, -1);
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || s->master < 0) return JS_NewInt32(ctx, -1);
    size_t len = 0;
    const char *d = JS_ToCStringLen(ctx, &len, argv[1]);
    if (!d) return JS_NewInt32(ctx, -1);
    ssize_t w = write(s->master, d, len);
    JS_FreeCString(ctx, d);
    return JS_NewInt32(ctx, (int)w);
}

static JSValue js_read(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NewString(ctx, "");
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->ring) return JS_NewString(ctx, "");
    int max = 32768;
    if (argc > 1 && JS_IsNumber(argv[1])) { int32_t t; JS_ToInt32(ctx, &t, argv[1]); if (t > 0) max = t; }
    unsigned char *buf = (unsigned char *)malloc((size_t)max);
    if (!buf) return JS_NewString(ctx, "");
    size_t n = ring_pop(s, buf, (size_t)max);
    JSValue ret = JS_NewStringLen(ctx, (const char *)buf, n);
    free(buf);
    return ret;
}

static JSValue js_alive(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_FALSE;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s) return JS_FALSE;
    if (s->alive) {
        int st = 0;
        pid_t r = waitpid(s->pid, &st, WNOHANG);
        if (r == s->pid) s->alive = 0;
    }
    return s->alive ? JS_TRUE : JS_FALSE;
}

static JSValue js_resize(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 3) return JS_FALSE;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s) return JS_FALSE;
    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_col = (unsigned short)get_int(ctx, argv[1]);
    ws.ws_row = (unsigned short)get_int(ctx, argv[2]);
    return ioctl(s->master, TIOCSWINSZ, &ws) == 0 ? JS_TRUE : JS_FALSE;
}

static JSValue js_kill(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_FALSE;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s) return JS_FALSE;
    if (s->pid > 0) kill(s->pid, SIGHUP);
    if (s->master >= 0) { close(s->master); s->master = -1; }
    free_session(s);
    return JS_TRUE;
}

static JSValue js_sessions(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    JSValue arr = JS_NewArray(ctx);
    uint32_t k = 0;
    for (int i = 0; i < MAX_SESSIONS; i++) {
        if (!g_sess[i].used) continue;
        JSValue o = JS_NewObject(ctx);
        JS_SetPropertyStr(ctx, o, "sid", JS_NewInt32(ctx, g_sess[i].sid));
        JS_SetPropertyStr(ctx, o, "pid", JS_NewInt32(ctx, (int)g_sess[i].pid));
        JS_SetPropertyStr(ctx, o, "cmd", JS_NewString(ctx, g_sess[i].cmd));
        JS_SetPropertyStr(ctx, o, "alive", g_sess[i].alive ? JS_TRUE : JS_FALSE);
        JS_SetPropertyUint32(ctx, arr, k++, o);
    }
    return arr;
}

/* term.execSync(cmd[, timeoutMs]) -> {code, out} —— 阻塞，短命令用 */
static JSValue js_exec_sync(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NULL;
    const char *cmd = JS_ToCString(ctx, argv[0]);
    int timeout = 5000;
    if (argc > 1 && JS_IsNumber(argv[1])) { int32_t t; JS_ToInt32(ctx, &t, argv[1]); if (t > 0) timeout = t; }

    char *cargv[4];
    cargv[0] = (char *)"/bin/sh";
    cargv[1] = (char *)"-c";
    cargv[2] = (char *)cmd;
    cargv[3] = NULL;
    char *cenvp[3];
    cenvp[0] = (char *)"PATH=/usr/sbin:/usr/bin:/sbin:/bin";
    cenvp[1] = (char *)"HOME=/root";
    cenvp[2] = NULL;

    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_col = 200; ws.ws_row = 50;
    int master = -1;
    pid_t pid = forkpty(&master, NULL, NULL, &ws);
    if (pid < 0) { JS_FreeCString(ctx, cmd); return JS_NULL; }
    if (pid == 0) { execve("/bin/sh", cargv, cenvp); _exit(127); }

    int fl = fcntl(master, F_GETFL, 0);
    fcntl(master, F_SETFL, fl | O_NONBLOCK);

    size_t cap = 64 * 1024, len = 0;
    char *out = (char *)malloc(cap);
    int elapsed = 0;
    int st = 0;
    for (;;) {
        struct pollfd p = { master, POLLIN, 0 };
        int pr = poll(&p, 1, 100);
        elapsed += 100;
        if (pr > 0) {
            char tmp[4096];
            ssize_t n = read(master, tmp, sizeof(tmp));
            if (n > 0) {
                if (len + (size_t)n + 1 > cap) {
                    cap *= 2;
                    out = (char *)realloc(out, cap);
                }
                memcpy(out + len, tmp, (size_t)n);
                len += (size_t)n;
            } else if (n == 0) break;
        }
        pid_t r = waitpid(pid, &st, WNOHANG);
        if (r == pid) break;
        if (elapsed > timeout) { kill(pid, SIGKILL); waitpid(pid, &st, 0); break; }
    }
    out[len] = 0;
    close(master);

    JSValue o = JS_NewObject(ctx);
    int code = WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    JS_SetPropertyStr(ctx, o, "code", JS_NewInt32(ctx, code));
    JS_SetPropertyStr(ctx, o, "out", JS_NewStringLen(ctx, out, len));
    free(out);
    JS_FreeCString(ctx, cmd);
    return o;
}

/* ---- 在笔上起 OpenSSH sshd（供电脑 ssh 进来）---- */

/* 守护进程化：fork + setsid + 日志重定向 + execv（子进程只调用 async-signal-safe 的东西） */
static pid_t spawn_daemon(const char *path, char *const argv[], char *const envp[], const char *logfile)
{
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        setsid();
        int fd = open(logfile, O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd >= 0) {
            dup2(fd, 0); dup2(fd, 1); dup2(fd, 2);
            if (fd > 2) close(fd);
        }
        if (envp) execve(path, argv, envp);
        else execv(path, argv);
        _exit(127);
    }
    return pid;
}
#define SSH_DIR "/userdisk/ssh"
static pid_t g_sshd_pid = 0;
static int g_sshd_port = 0;

static JSValue js_sshd_start(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    int port = (argc > 0 && JS_IsNumber(argv[0])) ? get_int(ctx, argv[0]) : 2222;
    const char *pubkey = (argc > 1 && JS_IsString(argv[1])) ? JS_ToCString(ctx, argv[1]) : NULL;

    JSValue o = JS_NewObject(ctx);
    if (access("/sbin/sshd", X_OK) != 0) {
        JS_SetPropertyStr(ctx, o, "ok", JS_FALSE);
        JS_SetPropertyStr(ctx, o, "msg", JS_NewString(ctx, "没有 /sbin/sshd"));
        return o;
    }
    /* 准备目录 / 主机密钥 / authorized_keys */
    char cmd[1600];
    snprintf(cmd, sizeof(cmd),
             "mkdir -p " SSH_DIR " /run/sshd; "
             "[ -f " SSH_DIR "/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N '' -f " SSH_DIR "/ssh_host_ed25519_key; "
             "[ -f " SSH_DIR "/ssh_host_rsa_key ] || ssh-keygen -q -t rsa -b 2048 -N '' -f " SSH_DIR "/ssh_host_rsa_key; "
             "chmod 600 " SSH_DIR "/ssh_host_*_key; ");
    if (pubkey && pubkey[0]) {
        char *q = (char *)pubkey;
        while (*q == ' ' || *q == '\n') q++;
        strncat(cmd, "printf '%s\\n' '", sizeof(cmd) - strlen(cmd) - 1);
        /* 简单过滤单引号，避免命令注入 */
        for (char *p = q; *p && strlen(cmd) < sizeof(cmd) - 40; p++) {
            char c[2] = { *p, 0 };
            if (*p != '\'') strncat(cmd, c, 1);
        }
        strncat(cmd, "' > " SSH_DIR "/authorized_keys; chmod 600 " SSH_DIR "/authorized_keys; ", sizeof(cmd) - strlen(cmd) - 1);
    }
    JSValue prep = js_exec_sync(ctx, JS_UNDEFINED, 1, (JSValueConst[]){ JS_NewString(ctx, cmd) });
    (void)prep;

    /* 干净地守护化：fork + setsid + 重定向日志 + execv（不要走 shell &，会被 PTY 会话带走） */
    char portstr[16];
    snprintf(portstr, sizeof(portstr), "%d", port);
    char *cargv[24];
    int n = 0;
    cargv[n++] = (char *)"/sbin/sshd";
    cargv[n++] = (char *)"-D";
    cargv[n++] = (char *)"-p";        cargv[n++] = portstr;
    cargv[n++] = (char *)"-h";        cargv[n++] = (char *)SSH_DIR "/ssh_host_ed25519_key";
    cargv[n++] = (char *)"-h";        cargv[n++] = (char *)SSH_DIR "/ssh_host_rsa_key";
    cargv[n++] = (char *)"-o";        cargv[n++] = (char *)"AuthorizedKeysFile=" SSH_DIR "/authorized_keys";
    cargv[n++] = (char *)"-o";        cargv[n++] = (char *)"StrictModes=no";
    cargv[n++] = (char *)"-o";        cargv[n++] = (char *)"PermitRootLogin=yes";
    cargv[n++] = (char *)"-o";        cargv[n++] = (char *)"PasswordAuthentication=no";
    cargv[n++] = (char *)"-o";        cargv[n++] = (char *)"PidFile=/tmp/sshd_term.pid";
    cargv[n++] = NULL;
    char *cenvp2[3];
    cenvp2[0] = (char *)"PATH=/usr/sbin:/usr/bin:/sbin:/bin";
    cenvp2[1] = (char *)"HOME=/root";
    cenvp2[2] = NULL;
    g_sshd_pid = spawn_daemon("/sbin/sshd", cargv, cenvp2, SSH_DIR "/sshd.log");
    g_sshd_port = port;
    JS_SetPropertyStr(ctx, o, "ok", JS_TRUE);
    JS_SetPropertyStr(ctx, o, "port", JS_NewInt32(ctx, port));
    JS_SetPropertyStr(ctx, o, "key", JS_NewString(ctx, SSH_DIR "/authorized_keys"));
    JS_SetPropertyStr(ctx, o, "hint", JS_NewString(ctx, "ssh -p <port> root@<笔IP>"));
    if (pubkey) JS_FreeCString(ctx, pubkey);
    return o;
}

static JSValue js_sshd_stop(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    JSValue r = js_exec_sync(ctx, JS_UNDEFINED, 1,
                             (JSValueConst[]){ JS_NewString(ctx, "pkill -f 'sshd -D -p' ; echo stopped") });
    (void)r;
    g_sshd_port = 0;
    return JS_TRUE;
}

static JSValue js_sshd_status(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    JSValue o = JS_NewObject(ctx);
    int alive = (g_sshd_pid > 0 && kill(g_sshd_pid, 0) == 0) ? 1 : 0;
    JS_SetPropertyStr(ctx, o, "port", JS_NewInt32(ctx, g_sshd_port));
    JS_SetPropertyStr(ctx, o, "pid", JS_NewInt32(ctx, (int)g_sshd_pid));
    JS_SetPropertyStr(ctx, o, "count", JS_NewInt32(ctx, alive));
    return o;
}

/* ------------------------------------------------------------------ VT 屏幕 API
 * 页面流程：vtStart -> (write 输入) -> 轮询 vtFeed -> 脏了 vtRender -> <image src=path>
 * 之所以不用框架文字渲染：框架只有比例字体，列对不齐；这里插件端逐格画位图。
 */

static JSValue js_vt_start(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NULL;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s) return JS_NewString(ctx, "no such session");
    int cols = (argc > 1) ? get_int(ctx, argv[1]) : 110;
    int rows = (argc > 2) ? get_int(ctx, argv[2]) : 11;
    const char *dir = (argc > 3 && JS_IsString(argv[3])) ? JS_ToCString(ctx, argv[3]) : NULL;
    if (!s->vt) {
        s->vt = (vt_t *)malloc(sizeof(vt_t));
        if (!s->vt) return JS_NewString(ctx, "oom");
        if (vt_init(s->vt, cols, rows) != 0) { free(s->vt); s->vt = NULL; return JS_NewString(ctx, "vt_init failed"); }
    } else {
        vt_resize(s->vt, cols, rows);
    }
    snprintf(s->vt_dir, sizeof(s->vt_dir), "%s", dir ? dir : "/userdisk/term");
    mkdir(s->vt_dir, 0755);
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "ok", JS_TRUE);
    JS_SetPropertyStr(ctx, o, "cols", JS_NewInt32(ctx, s->vt->cols));
    JS_SetPropertyStr(ctx, o, "rows", JS_NewInt32(ctx, s->vt->rows));
    JS_SetPropertyStr(ctx, o, "dir", JS_NewString(ctx, s->vt_dir));
    if (dir) JS_FreeCString(ctx, dir);
    return o;
}

static JSValue js_vt_feed(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_FALSE;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_FALSE;
    unsigned char buf[8192];
    int fed = 0;
    for (int i = 0; i < 64; i++) {
        size_t n = ring_pop(s, buf, sizeof(buf));
        if (!n) break;
        vt_feed(s->vt, buf, n);
        fed += (int)n;
        if (n < sizeof(buf)) break;
    }
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "fed", JS_NewInt32(ctx, fed));
    JS_SetPropertyStr(ctx, o, "dirty", s->vt->dirty ? JS_TRUE : JS_FALSE);
    return o;
}

/* 直接喂字节给 VT（自测/回放用） */
static JSValue js_vt_write(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 2) return JS_FALSE;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_FALSE;
    size_t len = 0;
    const char *d = JS_ToCStringLen(ctx, &len, argv[1]);
    if (!d) return JS_FALSE;
    vt_feed(s->vt, (const unsigned char *)d, len);
    JS_FreeCString(ctx, d);
    return JS_TRUE;
}

static JSValue js_vt_render(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NULL;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_NULL;
    /* 关键：框架的 <image> 按 URL 缓存，同名文件改了内容不会重载。
     * 所以每帧写一个**新文件名** f<gen>.png（gen 单调递增），页面 src 一变就会重新加载。
     * 旧帧保留最近若干个（异步加载可能还在用），太老的删掉。 */
    char path[320];
    int gen = s->vt->frame;
    int rc = 0;
    if (s->vt->dirty || s->vt->frame == 0) {
        snprintf(path, sizeof(path), "%s/f%d_%d.png", s->vt_dir, (int)s->pid, gen);
        rc = vt_render(s->vt, path);
        if (gen >= 12) {
            char old[320];
            snprintf(old, sizeof(old), "%s/f%d_%d.png", s->vt_dir, (int)s->pid, gen - 12);
            unlink(old);
        }
    } else {
        snprintf(path, sizeof(path), "%s/f%d_%d.png", s->vt_dir, (int)s->pid, gen - 1);
    }
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "ok", rc == 0 ? JS_TRUE : JS_FALSE);
    JS_SetPropertyStr(ctx, o, "path", JS_NewString(ctx, path));
    JS_SetPropertyStr(ctx, o, "slot", JS_NewInt32(ctx, gen % 2));
    JS_SetPropertyStr(ctx, o, "frame", JS_NewInt32(ctx, s->vt->frame));
    JS_SetPropertyStr(ctx, o, "cols", JS_NewInt32(ctx, s->vt->cols));
    JS_SetPropertyStr(ctx, o, "rows", JS_NewInt32(ctx, s->vt->rows));
    return o;
}

static JSValue js_vt_text(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NewString(ctx, "");
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_NewString(ctx, "");
    size_t cap = (size_t)(s->vt->cols + 2) * (size_t)s->vt->rows + 8;
    char *out = (char *)malloc(cap);
    if (!out) return JS_NewString(ctx, "");
    vt_text(s->vt, out, cap);
    JSValue r = JS_NewString(ctx, out);
    free(out);
    return r;
}

static JSValue js_vt_scroll(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 2) return JS_NULL;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_NULL;
    int view = vt_scroll_view(s->vt, get_int(ctx, argv[1]));
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "view", JS_NewInt32(ctx, view));
    JS_SetPropertyStr(ctx, o, "hist", JS_NewInt32(ctx, s->vt->hist_count));
    JS_SetPropertyStr(ctx, o, "rows", JS_NewInt32(ctx, s->vt->rows));
    return o;
}

static JSValue js_vt_history(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NULL;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_NULL;
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "hist", JS_NewInt32(ctx, s->vt->hist_count));
    JS_SetPropertyStr(ctx, o, "view", JS_NewInt32(ctx, s->vt->view));
    return o;
}
static JSValue js_vt_resize(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 3) return JS_FALSE;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_FALSE;
    vt_resize(s->vt, get_int(ctx, argv[1]), get_int(ctx, argv[2]));
    return JS_TRUE;
}

static JSValue js_vt_info(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val;
    if (argc < 1) return JS_NULL;
    session_t *s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_NULL;
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "cols", JS_NewInt32(ctx, s->vt->cols));
    JS_SetPropertyStr(ctx, o, "rows", JS_NewInt32(ctx, s->vt->rows));
    JS_SetPropertyStr(ctx, o, "frame", JS_NewInt32(ctx, s->vt->frame));
    JS_SetPropertyStr(ctx, o, "dirty", s->vt->dirty ? JS_TRUE : JS_FALSE);
    JS_SetPropertyStr(ctx, o, "dir", JS_NewString(ctx, s->vt_dir));
    JS_SetPropertyStr(ctx, o, "alt", JS_NewInt32(ctx, s->vt->use_alt));
    return o;
}
/* term.vtCursorLine(sid) → 光标所在那一行的可见文本（UTF-8，去尾部空格）
 * 用途：在 shell 里按 Up/Dn 召回历史后，把"命令行里正在编辑的内容"捞出来灌进页面输入框。 */
static JSValue js_vt_cursor_line(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    session_t *s;
    vt_t *v;
    char buf[1024];
    int n = 0, last = -1, x;
    (void)t;
    if (argc < 1) return JS_NewString(ctx, "");
    s = find_sid(get_int(ctx, argv[0]));
    if (!s || !s->vt) return JS_NewString(ctx, "");
    v = s->vt;
    if (v->cy < 0 || v->cy >= v->rows) return JS_NewString(ctx, "");
    {
        vt_cell_t *row = &v->cells[(size_t)v->cy * (size_t)v->cols];
        for (x = 0; x < v->cols; x++) {
            if (row[x].cp != ' ' && row[x].cp != 0) last = x;
        }
        for (x = 0; x <= last && n < (int)sizeof(buf) - 6; x++) {
            unsigned int cp = row[x].cp;
            if (cp == 0) continue;                  /* 宽字符右半格 */
            if (cp < 0x80) buf[n++] = (char)cp;
            else if (cp < 0x800) {
                buf[n++] = (char)(0xC0 | (cp >> 6));
                buf[n++] = (char)(0x80 | (cp & 0x3F));
            } else {
                buf[n++] = (char)(0xE0 | (cp >> 12));
                buf[n++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                buf[n++] = (char)(0x80 | (cp & 0x3F));
            }
        }
    }
    buf[n] = '\0';
    return JS_NewString(ctx, buf);
}

/* ---------------------------------------------------------------- 数据存储
 * 页面要「查看/编辑全部历史命令」和「常用命令」→ 需要持久化。
 * 存放位置按需求放在 U 盘可见目录：
 *     /userdisk/Favorite/PenTerm/history.json     命令历史
 *     /userdisk/Favorite/PenTerm/favorites.json   常用命令
 * 这样插电脑（MTP）或用文件管理器都能看到、能备份/编辑。
 * 写盘用「临时文件 + rename」，避免掉电写出半截 JSON。
 */
#define TERM_STORE_DIR "/userdisk/Favorite/PenTerm"

/* 确保目录存在，成功时把路径写进 out */
static int store_ensure(char *out, size_t cap)
{
    struct stat st;
    if (stat("/userdisk/Favorite", &st) != 0) return 0;
    if (stat(TERM_STORE_DIR, &st) != 0) {
        if (mkdir(TERM_STORE_DIR, 0777) != 0 && errno != EEXIST) return 0;
    }
    snprintf(out, cap, "%s", TERM_STORE_DIR);
    return 1;
}

/* 文件名白名单（只允许字母数字下划线横线），防路径穿越 */
static int store_name_ok(const char *n)
{
    const char *p;
    if (!n || !*n || strlen(n) >= 64) return 0;
    for (p = n; *p; p++) {
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) return 0;
    }
    return 1;
}

/* 读整个文件；返回 malloc 的字符串（调用者 free），失败返回 NULL */
static char *read_file_all(const char *path, size_t *lenp)
{
    FILE *f = fopen(path, "rb");
    char *buf;
    long n;
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    n = ftell(f);
    if (n < 0) { fclose(f); return NULL; }
    if (n > 4 * 1024 * 1024) n = 4 * 1024 * 1024;      /* 兜底上限 */
    rewind(f);
    buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return NULL; }
    if (n > 0 && fread(buf, 1, (size_t)n, f) != (size_t)n) { /* 短读也接受 */ }
    buf[n] = '\0';
    fclose(f);
    if (lenp) *lenp = (size_t)n;
    return buf;
}

/* term.storeDir() → 目录路径（顺带创建） */
static JSValue js_store_dir(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    char dir[256];
    (void)t; (void)argc; (void)argv;
    if (!store_ensure(dir, sizeof(dir))) return JS_NewString(ctx, "");
    return JS_NewString(ctx, dir);
}

/* term.storeLoad(name) → 文件内容（不存在返回空串） */
static JSValue js_store_load(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    const char *name;
    char dir[256], path[340];
    char *data;
    JSValue ret;
    (void)t;
    if (argc < 1) return JS_NewString(ctx, "");
    name = JS_ToCString(ctx, argv[0]);
    if (!store_name_ok(name) || !store_ensure(dir, sizeof(dir))) {
        JS_FreeCString(ctx, name);
        return JS_NewString(ctx, "");
    }
    snprintf(path, sizeof(path), "%s/%s.json", dir, name);
    JS_FreeCString(ctx, name);
    data = read_file_all(path, NULL);
    if (!data) return JS_NewString(ctx, "");
    ret = JS_NewString(ctx, data);
    free(data);
    return ret;
}

/* term.storeSave(name, text) → 写入字节数（失败 -1） */
static JSValue js_store_save(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    const char *name, *text;
    char dir[256], path[340], tmp[360];
    FILE *f;
    size_t len;
    int ok;
    (void)t;
    if (argc < 2) return JS_NewInt32(ctx, -1);
    name = JS_ToCString(ctx, argv[0]);
    text = JS_ToCString(ctx, argv[1]);
    if (!store_name_ok(name) || !text || !store_ensure(dir, sizeof(dir))) {
        JS_FreeCString(ctx, name);
        if (text) JS_FreeCString(ctx, text);
        return JS_NewInt32(ctx, -1);
    }
    snprintf(path, sizeof(path), "%s/%s.json", dir, name);
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    len = strlen(text);
    f = fopen(tmp, "wb");
    ok = (f && fwrite(text, 1, len, f) == len);
    if (f) fclose(f);
    if (ok) ok = (rename(tmp, path) == 0);
    if (!ok) unlink(tmp);
    JS_FreeCString(ctx, name);
    JS_FreeCString(ctx, text);
    return JS_NewInt32(ctx, ok ? (int)len : -1);
}

/* term.storeList() → 目录里有哪些 json（调试/自检用） */
static JSValue js_store_list(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv)
{
    char dir[256], cmd[420], buf[2048];
    FILE *p;
    size_t n = 0;
    (void)t; (void)argc; (void)argv;
    if (!store_ensure(dir, sizeof(dir))) return JS_NewString(ctx, "");
    snprintf(cmd, sizeof(cmd), "ls -l '%s' 2>/dev/null", dir);
    p = popen(cmd, "r");
    if (!p) return JS_NewString(ctx, "");
    while (n + 1 < sizeof(buf) && fgets(buf + n, (int)(sizeof(buf) - n), p)) n = strlen(buf);
    pclose(p);
    return JS_NewString(ctx, buf);
}

/* ------------------------------------------------------------------ 模块注册 */

static int term_module_init(JSContext *ctx, JSModuleDef *m)
{
    JSValue obj = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, obj, "info", JS_NewCFunction(ctx, js_info, "info", 0));
    JS_SetPropertyStr(ctx, obj, "spawn", JS_NewCFunction(ctx, js_spawn, "spawn", 2));
    JS_SetPropertyStr(ctx, obj, "spawnShell", JS_NewCFunction(ctx, js_spawn_shell, "spawnShell", 1));
    JS_SetPropertyStr(ctx, obj, "spawnSsh", JS_NewCFunction(ctx, js_spawn_ssh, "spawnSsh", 5));
    JS_SetPropertyStr(ctx, obj, "write", JS_NewCFunction(ctx, js_write, "write", 2));
    JS_SetPropertyStr(ctx, obj, "read", JS_NewCFunction(ctx, js_read, "read", 2));
    JS_SetPropertyStr(ctx, obj, "alive", JS_NewCFunction(ctx, js_alive, "alive", 1));
    JS_SetPropertyStr(ctx, obj, "resize", JS_NewCFunction(ctx, js_resize, "resize", 3));
    JS_SetPropertyStr(ctx, obj, "kill", JS_NewCFunction(ctx, js_kill, "kill", 1));
    JS_SetPropertyStr(ctx, obj, "sessions", JS_NewCFunction(ctx, js_sessions, "sessions", 0));
    JS_SetPropertyStr(ctx, obj, "execSync", JS_NewCFunction(ctx, js_exec_sync, "execSync", 2));
    JS_SetPropertyStr(ctx, obj, "sshdStart", JS_NewCFunction(ctx, js_sshd_start, "sshdStart", 2));
    JS_SetPropertyStr(ctx, obj, "sshdStop", JS_NewCFunction(ctx, js_sshd_stop, "sshdStop", 0));
    JS_SetPropertyStr(ctx, obj, "sshdStatus", JS_NewCFunction(ctx, js_sshd_status, "sshdStatus", 0));
    JS_SetPropertyStr(ctx, obj, "vtStart", JS_NewCFunction(ctx, js_vt_start, "vtStart", 4));
    JS_SetPropertyStr(ctx, obj, "vtFeed", JS_NewCFunction(ctx, js_vt_feed, "vtFeed", 1));
    JS_SetPropertyStr(ctx, obj, "vtWrite", JS_NewCFunction(ctx, js_vt_write, "vtWrite", 2));
    JS_SetPropertyStr(ctx, obj, "vtRender", JS_NewCFunction(ctx, js_vt_render, "vtRender", 1));
    JS_SetPropertyStr(ctx, obj, "vtText", JS_NewCFunction(ctx, js_vt_text, "vtText", 1));
    JS_SetPropertyStr(ctx, obj, "vtResize", JS_NewCFunction(ctx, js_vt_resize, "vtResize", 3));
    JS_SetPropertyStr(ctx, obj, "vtScroll", JS_NewCFunction(ctx, js_vt_scroll, "vtScroll", 2));
    JS_SetPropertyStr(ctx, obj, "vtHistory", JS_NewCFunction(ctx, js_vt_history, "vtHistory", 1));
    JS_SetPropertyStr(ctx, obj, "vtInfo", JS_NewCFunction(ctx, js_vt_info, "vtInfo", 1));
    JS_SetPropertyStr(ctx, obj, "vtCursorLine", JS_NewCFunction(ctx, js_vt_cursor_line, "vtCursorLine", 1));
    /* 历史/常用命令的持久化（/userdisk/Favorite/PenTerm/） */
    JS_SetPropertyStr(ctx, obj, "storeDir", JS_NewCFunction(ctx, js_store_dir, "storeDir", 0));
    JS_SetPropertyStr(ctx, obj, "storeLoad", JS_NewCFunction(ctx, js_store_load, "storeLoad", 1));
    JS_SetPropertyStr(ctx, obj, "storeSave", JS_NewCFunction(ctx, js_store_save, "storeSave", 2));
    JS_SetPropertyStr(ctx, obj, "storeList", JS_NewCFunction(ctx, js_store_list, "storeList", 0));

    JS_SetModuleExport(ctx, m, "default", JS_DupValue(ctx, obj));
    JS_SetModuleExport(ctx, m, "Term", JS_DupValue(ctx, obj));
    JS_SetModuleExport(ctx, m, "term", JS_DupValue(ctx, obj));
    JS_FreeValue(ctx, obj);
    return 0;
}

static JSModuleDef *term_loader(JSContext *ctx, const char *module_name)
{
    JSModuleDef *m = JS_NewCModule(ctx, module_name, term_module_init);
    if (!m) return NULL;
    JS_AddModuleExport(ctx, m, "default");
    JS_AddModuleExport(ctx, m, "Term");
    JS_AddModuleExport(ctx, m, "term");
    return m;
}

/* 框架加载插件时用 dlsym 找这两个符号 —— 必须显式导出（-fvisibility=hidden 会把它们藏起来！） */
extern void registerCModuleLoader(const char *name, JSModuleDef *(*loader)(JSContext *, const char *));

__attribute__((visibility("default"))) void custom_init_jsapis(void)
{
    registerCModuleLoader("term", term_loader);
}

/* 框架还会找这个（JS 模块注册）；没有它就会去 /etc/miniapp/.../js_modules/<name>.js 找，
 * 于是 import 'term' 会 ReferenceError。两个都注册最稳。 */
__attribute__((visibility("default"))) void custom_init_jsmodules(void)
{
    registerCModuleLoader("term", term_loader);
}
