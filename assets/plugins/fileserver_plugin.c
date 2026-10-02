/*
 * fileserver_plugin.c —— 给有道词典笔的“文件互传”换一个**支持上传**的本地 HTTP 文件服务器
 *
 * 背景 / 为什么需要它：
 *   file-manager-1.2.0.amr 自带的 libjsapi_fileserver_*.so 里，HTTP 服务只实现了 GET：
 *   内嵌页面就是一个只读的目录列表（"Index of ..." 只有 Name/Size 两列），
 *   插件字符串里连 upload / POST / multipart 都没有，只有一句 "get method failed %s"。
 *   所以网页上根本不存在上传入口和上传接口 —— 这就是“不能上传文件”的根因。
 *
 * 本插件用同一个框架 ABI 重新实现 fileServer 模块（方法名与原来一致，额外支持上传）：
 *     FileServer.start(port, rootPath)   启动服务器
 *     FileServer.stop()                  停止
 *     FileServer.getStatus()             {running, port, root, url, ip}
 *     FileServer.getPort()               当前端口
 * 注册两个模块名（fileServer / FileServer），兼容 app 里两种写法。
 *
 * HTTP 接口：
 *     GET  /                  目录页面（含拖拽上传 / 选择文件上传 / 新建文件夹 / 删除）
 *     GET  /?path=sub         浏览子目录
 *     GET  /files/<rel>       下载（支持 Range 断点续传、视频拖动）
 *     POST /upload?path=sub   multipart/form-data 上传（流式落盘，不限大小，支持多选）
 *     POST /mkdir?path=sub&name=xxx
 *     POST /delete?path=rel
 *
 * 编译（见 build.cmd）：
 *   zig cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 -I quickjs fileserver_plugin.c -o libjsapi_fileserver_9000000001.so -lpthread
 * 本地自测（编成普通可执行文件，在笔上或任何 Linux 上跑）：
 *   zig cc -target aarch64-linux-gnu.2.29 -O2 -DFS_TEST_MAIN fileserver_plugin.c -o fssrv -lpthread
 *   ./fssrv 18080 /userdisk/Favorite
 */

#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#ifndef FS_TEST_MAIN
#include "quickjs.h"
extern void registerCModuleLoader(const char *name,
                                 JSModuleDef *(*loader)(JSContext *ctx, const char *module_name));
#endif

#ifdef FS_DEBUG
#define LOG(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); fflush(stderr); } while (0)
#else
#define LOG(...) do { } while (0)
#endif

#define PATHMAX 4096
#define RECVBUF 65536
#define BOUNDMAX 200
#define PARTNAME 256

/* ------------------------------------------------------------------ 全局状态 */

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_listen_fd = -1;
static volatile int g_running = 0;
static volatile int g_stop_flag = 0;
static int g_port = 0;
static char g_root[PATHMAX] = "/userdisk";
static pthread_t g_accept_thread;
static int g_accept_thread_valid = 0;
static char g_boundary[2 * BOUNDMAX + 8];

/* ------------------------------------------------------------------ 小工具 */

static int send_all(int fd, const void *buf, size_t len)
{
    const char *p = (const char *)buf;

    while (len > 0) {
        ssize_t n = send(fd, p, len, MSG_NOSIGNAL);

        if (n <= 0) {
            if (n < 0 && errno == EINTR)
                continue;
            return -1;
        }
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static int send_str(int fd, const char *s) { return send_all(fd, s, strlen(s)); }

static void url_decode(const char *src, char *dst, size_t dstsz)
{
    size_t o = 0;

    for (size_t i = 0; src[i] && o + 1 < dstsz; i++) {
        if (src[i] == '%' && isxdigit((unsigned char)src[i + 1]) && isxdigit((unsigned char)src[i + 2])) {
            char h[3] = {src[i + 1], src[i + 2], 0};
            dst[o++] = (char)strtol(h, NULL, 16);
            i += 2;
        } else if (src[i] == '+') {
            dst[o++] = ' ';
        } else {
            dst[o++] = src[i];
        }
    }
    dst[o] = 0;
}

/* 去掉 ../ 之类，拼出 root 下的真实路径；返回 0 成功 */
static int safe_join(const char *root, const char *rel, char *out, size_t outsz)
{
    char clean[PATHMAX];
    size_t o = 0;

    while (*rel == '/')
        rel++;

    for (size_t i = 0; rel[i] && o + 1 < sizeof(clean); i++) {
        if (rel[i] == '\\')
            clean[o++] = '/';
        else
            clean[o++] = rel[i];
    }
    clean[o] = 0;

    /* 拒绝任何 .. 片段 */
    if (strstr(clean, ".."))
        return -1;

    if (snprintf(out, outsz, "%s/%s", root, clean) >= (int)outsz)
        return -1;
    return 0;
}

static void html_escape(const char *src, char *dst, size_t dstsz)
{
    size_t o = 0;

    for (size_t i = 0; src[i] && o + 6 < dstsz; i++) {
        switch (src[i]) {
        case '&': memcpy(dst + o, "&amp;", 5); o += 5; break;
        case '<': memcpy(dst + o, "&lt;", 4); o += 4; break;
        case '>': memcpy(dst + o, "&gt;", 4); o += 4; break;
        case '"': memcpy(dst + o, "&quot;", 6); o += 6; break;
        default: dst[o++] = src[i]; break;
        }
    }
    dst[o] = 0;
}

/* URL 编码（用于 href 里的路径） */
static void url_encode(const char *src, char *dst, size_t dstsz)
{
    static const char *hex = "0123456789ABCDEF";
    size_t o = 0;

    for (size_t i = 0; src[i] && o + 4 < dstsz; i++) {
        unsigned char c = (unsigned char)src[i];

        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~' || c == '/') {
            dst[o++] = (char)c;
        } else {
            dst[o++] = '%';
            dst[o++] = hex[c >> 4];
            dst[o++] = hex[c & 15];
        }
    }
    dst[o] = 0;
}

static const char *mime_of(const char *name)
{
    const char *dot = strrchr(name, '.');

    if (!dot)
        return "application/octet-stream";
    if (!strcasecmp(dot, ".txt") || !strcasecmp(dot, ".log") || !strcasecmp(dot, ".md")) return "text/plain; charset=utf-8";
    if (!strcasecmp(dot, ".html") || !strcasecmp(dot, ".htm")) return "text/html; charset=utf-8";
    if (!strcasecmp(dot, ".css")) return "text/css";
    if (!strcasecmp(dot, ".js")) return "application/javascript";
    if (!strcasecmp(dot, ".json")) return "application/json";
    if (!strcasecmp(dot, ".png")) return "image/png";
    if (!strcasecmp(dot, ".jpg") || !strcasecmp(dot, ".jpeg")) return "image/jpeg";
    if (!strcasecmp(dot, ".gif")) return "image/gif";
    if (!strcasecmp(dot, ".webp")) return "image/webp";
    if (!strcasecmp(dot, ".bmp")) return "image/bmp";
    if (!strcasecmp(dot, ".svg")) return "image/svg+xml";
    if (!strcasecmp(dot, ".mp3")) return "audio/mpeg";
    if (!strcasecmp(dot, ".flac")) return "audio/flac";
    if (!strcasecmp(dot, ".wav")) return "audio/wav";
    if (!strcasecmp(dot, ".m4a")) return "audio/mp4";
    if (!strcasecmp(dot, ".mp4")) return "video/mp4";
    if (!strcasecmp(dot, ".mkv")) return "video/x-matroska";
    if (!strcasecmp(dot, ".pdf")) return "application/pdf";
    if (!strcasecmp(dot, ".apk") || !strcasecmp(dot, ".amr")) return "application/octet-stream";
    return "application/octet-stream";
}

static void human_size(long long n, char *out, size_t outsz)
{
    if (n < 1024) snprintf(out, outsz, "%lld B", n);
    else if (n < 1024LL * 1024) snprintf(out, outsz, "%.1f KB", n / 1024.0);
    else if (n < 1024LL * 1024 * 1024) snprintf(out, outsz, "%.1f MB", n / 1048576.0);
    else snprintf(out, outsz, "%.2f GB", n / 1073741824.0);
}

/* 取本机 IPv4（用 getifaddrs，不用 popen —— popen 在多线程的框架进程里会 fork，
 * 有死锁风险，之前那一版黑屏很可能就是它导致的） */
static int source_ip(char *out, size_t outsz)
{
    struct ifaddrs *ifa = NULL, *p;
    char fallback[64] = "";

    out[0] = 0;
    if (getifaddrs(&ifa) != 0)
        return -1;

    for (p = ifa; p; p = p->ifa_next) {
        struct sockaddr_in *sin;

        if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET)
            continue;
        sin = (struct sockaddr_in *)p->ifa_addr;
        if (!sin->sin_addr.s_addr)
            continue;
        {
            char ip[INET_ADDRSTRLEN] = "";
            inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof(ip));
            if (!ip[0] || !strcmp(ip, "127.0.0.1"))
                continue;
            if (p->ifa_name && !strncmp(p->ifa_name, "wlan", 4)) {
                snprintf(out, outsz, "%s", ip);       /* 优先无线网卡 */
                freeifaddrs(ifa);
                return 0;
            }
            if (!fallback[0])
                snprintf(fallback, sizeof(fallback), "%s", ip);
        }
    }
    freeifaddrs(ifa);

    if (fallback[0]) {
        snprintf(out, outsz, "%s", fallback);
        return 0;
    }
    return -1;
}

/* ------------------------------------------------------------------ HTTP 请求解析 */

typedef struct {
    char method[16];
    char path[PATHMAX];
    char query[PATHMAX];
    long long content_length;
    char content_type[512];
    char range[128];
    char cookie[512];
} http_req;

static int read_headers(int fd, http_req *r, unsigned char *leftover, size_t *leftover_len)
{
    char buf[8192];
    size_t len = 0;
    char *hdr_end = NULL;
    ssize_t n;

    memset(r, 0, sizeof(*r));
    r->content_length = -1;

    while (len < sizeof(buf) - 1) {
        n = recv(fd, buf + len, sizeof(buf) - 1 - len, 0);
        if (n <= 0)
            return -1;
        len += (size_t)n;
        buf[len] = 0;
        hdr_end = strstr(buf, "\r\n\r\n");
        if (hdr_end)
            break;
    }
    if (!hdr_end)
        return -1;

    /* 请求行 */
    char *line_end = strstr(buf, "\r\n");
    if (!line_end)
        return -1;
    *line_end = 0;
    {
        char full[PATHMAX * 2];
        if (sscanf(buf, "%15s %4095s", r->method, full) != 2)
            return -1;
        char *q = strchr(full, '?');
        if (q) {
            *q = 0;
            snprintf(r->query, sizeof(r->query), "%s", q + 1);
        }
        url_decode(full, r->path, sizeof(r->path));
    }

    /* 头 */
    char *p = line_end + 2;
    while (p < hdr_end) {
        char *eol = strstr(p, "\r\n");
        char *colon;
        if (!eol)
            break;
        *eol = 0;
        colon = strchr(p, ':');
        if (colon) {
            char *v = colon + 1;
            while (*v == ' ' || *v == '\t')
                v++;
            if (!strncasecmp(p, "Content-Length:", 15))
                r->content_length = atoll(v);
            else if (!strncasecmp(p, "Content-Type:", 13))
                snprintf(r->content_type, sizeof(r->content_type), "%s", v);
            else if (!strncasecmp(p, "Range:", 6))
                snprintf(r->range, sizeof(r->range), "%s", v);
            else if (!strncasecmp(p, "Cookie:", 7))
                snprintf(r->cookie, sizeof(r->cookie), "%s", v);
        }
        p = eol + 2;
    }

    /* 已读进来的 body 部分 */
    {
        unsigned char *body = (unsigned char *)(hdr_end + 4);
        size_t bodylen = len - (size_t)(body - (unsigned char *)buf);
        if (bodylen > *leftover_len)
            bodylen = *leftover_len;
        memcpy(leftover, body, bodylen);
        *leftover_len = bodylen;
    }
    return 0;
}

static void send_common_headers(int fd, const char *status, const char *ctype, long long clen)
{
    char h[512];
    snprintf(h, sizeof(h),
             "HTTP/1.1 %s\r\n"
             "Content-Type: %s\r\n"
             "Content-Length: %lld\r\n"
             "Access-Control-Allow-Origin: *\r\n"
             "Cache-Control: no-store\r\n"
             "Connection: close\r\n\r\n",
             status, ctype, clen);
    send_str(fd, h);
}

static void send_text(int fd, const char *status, const char *body)
{
    send_common_headers(fd, status, "text/plain; charset=utf-8", (long long)strlen(body));
    send_all(fd, body, strlen(body));
}

static char *html_page(const char *rel);
static int handle_get(int fd, http_req *r, unsigned char *leftover, size_t leftover_len);
static int handle_upload(int fd, http_req *r, unsigned char *leftover, size_t leftover_len);

/* ------------------------------------------------------------------ multipart 流式解析 */

typedef struct {
    int fd;
    unsigned char *buf;
    size_t cap;
    size_t len;     /* 已缓冲的数据长度 */
    size_t pos;     /* 已消费到哪 */
    int eof;
} mp_reader;

#define MP_MAXCAP (16u * 1024u * 1024u)

/* 保证缓冲区里还有没被消费的数据；返回 1 表示有新数据，0 表示读不到更多 */
static int mp_fill(mp_reader *m)
{
    if (m->pos > 0) {
        memmove(m->buf, m->buf + m->pos, m->len - m->pos);
        m->len -= m->pos;
        m->pos = 0;
    }
    if (m->len == m->cap) {
        if (m->cap >= MP_MAXCAP)
            return 0;                       /* 到此为止，交给调用方判断 */
        {
            size_t ncap = m->cap ? m->cap * 2 : RECVBUF;
            unsigned char *nb;
            if (ncap > MP_MAXCAP)
                ncap = MP_MAXCAP;
            nb = (unsigned char *)realloc(m->buf, ncap);
            if (!nb)
                return 0;
            m->buf = nb;
            m->cap = ncap;
        }
    }
    {
        ssize_t n = recv(m->fd, m->buf + m->len, m->cap - m->len, 0);
        if (n <= 0) {
            m->eof = 1;
            return 0;
        }
        m->len += (size_t)n;
    }
    return 1;
}

/* 在 [pos, len) 中找 "\r\n--boundary"，返回相对偏移；找不到返回 -1。
 * 一定会推进：找不到且缓冲很满时，把安全部分丢弃/交由调用方写出。 */
static long mp_find(mp_reader *m, int flush_to_file, FILE *out)
{
    size_t blen = strlen(g_boundary);
    size_t keep = blen + 4;                 /* 可能被截断的分隔符长度 */

    for (;;) {
        size_t avail = m->len - m->pos;

        if (avail >= 2) {
            unsigned char *start = m->buf + m->pos;
            size_t i;
            for (i = 0; i + keep <= avail; i++) {
                if (start[i] == '\r' && start[i + 1] == '\n' &&
                    !memcmp(start + i + 2, g_boundary, blen)) {
                    /* 关键：分隔符之前的数据属于文件内容，必须先落盘再返回 */
                    if (flush_to_file && i > 0) {
                        if (out && fwrite(start, 1, i, out) != i)
                            return -2;      /* 写盘失败 */
                    }
                    return (long)i;
                }
            }
            /* 没找到：把 [0, avail-keep) 这段安全数据吐出去，保证前进 */
            if (avail > keep) {
                size_t safe = avail - keep;
                if (flush_to_file) {
                    if (out && fwrite(start, 1, safe, out) != safe)
                        return -2;          /* 写盘失败 */
                }
                m->pos += safe;
                continue;
            }
        }
        if (m->eof)
            return -1;
        if (!mp_fill(m))
            return m->eof ? -1 : -3;   /* -3 = 缓冲超限；绝不在此自旋 */
    }
}

/* 跳过第一个 --boundary 之前的 preamble */
static int mp_skip_preamble(mp_reader *m)
{
    size_t blen = strlen(g_boundary);

    for (;;) {
        size_t avail = m->len - m->pos;
        unsigned char *start = m->buf + m->pos;
        size_t i;

        for (i = 0; i + blen <= avail; i++) {
            if (!memcmp(start + i, g_boundary, blen)) {
                m->pos += i + blen;
                while (m->len - m->pos < 2 && mp_fill(m)) { }
                if (m->len - m->pos >= 2 && m->buf[m->pos] == '\r' && m->buf[m->pos + 1] == '\n')
                    m->pos += 2;
                return 0;
            }
        }
        /* 没找到：丢弃安全部分（保留可能被截断的尾巴） */
        if (avail > blen) {
            m->pos += avail - blen;
            continue;
        }
        if (m->eof)
            return -1;
        if (!mp_fill(m))
            return m->eof ? -1 : -3;
    }
}

/* 读一行（以 \r\n 结尾）用于 part 头；返回 0 成功 */
static int mp_read_line(mp_reader *m, char *out, size_t outsz)
{
    size_t o = 0;

    for (;;) {
        while (m->pos < m->len) {
            unsigned char c = m->buf[m->pos++];
            if (c == '\n') {
                if (o > 0 && out[o - 1] == '\r')
                    o--;
                out[o] = 0;
                return 0;
            }
            if (o + 1 < outsz)
                out[o++] = (char)c;
        }
        if (m->eof)
            break;
        if (!mp_fill(m))
            break;
    }
    out[o] = 0;
    return o ? 0 : -1;
}

static void sanitize_filename(const char *in, char *out, size_t outsz)
{
    size_t o = 0;
    const char *base = strrchr(in, '/');
    const char *base2 = strrchr(in, '\\');

    if (base2 && (!base || base2 > base))
        base = base2;
    base = base ? base + 1 : in;

    for (size_t i = 0; base[i] && o + 1 < outsz; i++) {
        unsigned char c = (unsigned char)base[i];
        if (c == '/' || c == '\\' || c == ':' || c < 32)
            continue;
        out[o++] = (char)c;
    }
    out[o] = 0;
    if (!out[0])
        snprintf(out, outsz, "upload_%ld", (long)time(NULL));
}

static void unique_path(const char *dir, const char *name, char *out, size_t outsz)
{
    char cand[PATHMAX];
    struct stat st;
    int i;

    snprintf(cand, sizeof(cand), "%s/%s", dir, name);
    if (stat(cand, &st) != 0) {
        snprintf(out, outsz, "%s", cand);
        return;
    }
    for (i = 1; i < 10000; i++) {
        const char *dot = strrchr(name, '.');
        if (dot && dot != name)
            snprintf(cand, sizeof(cand), "%.*s-%d%s", (int)(dot - name), name, i, dot);
        else
            snprintf(cand, sizeof(cand), "%s-%d", name, i);
        char full[PATHMAX];
        snprintf(full, sizeof(full), "%s/%s", dir, cand);
        if (stat(full, &st) != 0) {
            snprintf(out, outsz, "%s", full);
            return;
        }
    }
    snprintf(out, outsz, "%s/%s-%ld", dir, name, (long)time(NULL));
}

/* 在 query 里取值，如 path=xxx */
static int query_get(const char *query, const char *key, char *out, size_t outsz)
{
    size_t klen = strlen(key);

    out[0] = 0;
    for (const char *p = query; p && *p; ) {
        const char *amp = strchr(p, '&');
        size_t seglen = amp ? (size_t)(amp - p) : strlen(p);
        if (seglen > klen && !strncmp(p, key, klen) && p[klen] == '=') {
            char tmp[PATHMAX];
            size_t vlen = seglen - klen - 1;
            if (vlen >= sizeof(tmp))
                vlen = sizeof(tmp) - 1;
            memcpy(tmp, p + klen + 1, vlen);
            tmp[vlen] = 0;
            url_decode(tmp, out, outsz);
            return 0;
        }
        p = amp ? amp + 1 : NULL;
    }
    return -1;
}

/* ------------------------------------------------------------------ 上传处理 */

static int handle_upload(int fd, http_req *r, unsigned char *leftover, size_t leftover_len)
{
    char rel[PATHMAX] = "", dir[PATHMAX];
    char *bp;
    mp_reader m;
    char saved[32][PARTNAME];
    int nsaved = 0;
    char json[4096];
    int ok = 1;

    if (query_get(r->query, "path", rel, sizeof(rel)) != 0)
        rel[0] = 0;
    if (safe_join(g_root, rel, dir, sizeof(dir)) != 0) {
        send_text(fd, "400 Bad Request", "bad path");
        return 0;
    }

    bp = strstr(r->content_type, "boundary=");
    if (!bp) {
        send_text(fd, "400 Bad Request", "missing boundary (need multipart/form-data)");
        return 0;
    }
    bp += 9;
    if (*bp == '"')
        bp++;
    g_boundary[0] = '-';
    g_boundary[1] = '-';
    {
        size_t o = 2;
        while (*bp && *bp != '"' && *bp != '\r' && *bp != '\n' && *bp != ';' && o + 2 < sizeof(g_boundary))
            g_boundary[o++] = *bp++;
        g_boundary[o] = 0;
    }

    LOG("upload: dir=%s boundary=%s content_length=%lld leftover=%zu", dir, g_boundary, r->content_length, leftover_len);
    memset(&m, 0, sizeof(m));
    m.fd = fd;
    m.cap = RECVBUF * 2;
    m.buf = (unsigned char *)malloc(m.cap);
    if (!m.buf) {
        send_text(fd, "500 Internal Server Error", "oom");
        return 0;
    }
    if (leftover_len) {
        size_t n = leftover_len < m.cap ? leftover_len : m.cap;
        memcpy(m.buf, leftover, n);
        m.len = n;
    }
    if (mp_skip_preamble(&m) != 0) {
        free(m.buf);
        send_text(fd, "400 Bad Request", "bad multipart: boundary not found");
        return 0;
    }

    for (;;) {
        char line[1024];
        char filename[PARTNAME] = "";
        char fieldname[PARTNAME] = "";
        FILE *out = NULL;
        char outpath[PATHMAX] = "";
        long hit;

        /* part 头 */
        for (;;) {
            if (mp_read_line(&m, line, sizeof(line)) != 0)
                break;
            if (!line[0])
                break;
            if (!strncasecmp(line, "Content-Disposition:", 20)) {
                char *fn = strstr(line, "filename=");
                char *nm = strstr(line, "name=");
                if (nm) {
                    nm += 5;
                    if (*nm == '"') {
                        nm++;
                        char *e = strchr(nm, '"');
                        if (e) *e = 0;
                    } else {
                        char *e = strpbrk(nm, "; \r\n");
                        if (e) *e = 0;
                    }
                    snprintf(fieldname, sizeof(fieldname), "%s", nm);
                }
                if (fn) {
                    fn += 9;
                    if (*fn == '"') {
                        fn++;
                        char *e = strchr(fn, '"');
                        if (e) *e = 0;
                    } else {
                        char *e = strpbrk(fn, ";\r\n");
                        if (e) *e = 0;
                    }
                    sanitize_filename(fn, filename, sizeof(filename));
                }
            }
        }

        /* part 内容：流式找下一个 boundary */
        if (filename[0]) {
            unique_path(dir, filename, outpath, sizeof(outpath));
            out = fopen(outpath, "wb");
            if (!out) {
                ok = 0;
                /* 仍然要把这一段数据吃掉 */
            }
        }

        {
            int done = 0;
            while (!done) {
                hit = mp_find(&m, 1, out);      /* 分隔符之前的文件数据会被它直接写盘 */
                if (hit == -2) {
                    ok = 0;
                    done = 1;
                    break;
                }
                if (hit < 0) {
                    /* 再没有分隔符了：把剩余数据写出去，收尾 */
                    if (out && m.len > m.pos)
                        fwrite(m.buf + m.pos, 1, m.len - m.pos, out);
                    m.pos = m.len;
                    done = 1;
                    break;
                }
                m.pos += (size_t)hit;
                m.pos += 2 + strlen(g_boundary);   /* 跳过 \r\n--boundary */

                if (out) {
                    fclose(out);
                    out = NULL;
                    if (nsaved < 32)
                        snprintf(saved[nsaved++], PARTNAME, "%s",
                                 strrchr(outpath, '/') ? strrchr(outpath, '/') + 1 : outpath);
                    LOG("saved: %s", outpath);
                }

                /* 后面是 --（整体结束）还是 \r\n（下一段） */
                while (m.len - m.pos < 2 && mp_fill(&m)) { }
                if (m.len - m.pos >= 2 && m.buf[m.pos] == '-' && m.buf[m.pos + 1] == '-') {
                    m.pos += 2;
                    done = 1;
                    goto multipart_done;
                }
                if (m.len - m.pos >= 2 && m.buf[m.pos] == '\r' && m.buf[m.pos + 1] == '\n')
                    m.pos += 2;
                done = 1;
            }
        }
        if (out) {
            fclose(out);
            out = NULL;
        }
        if (filename[0] && nsaved > 0) {
            /* 已在上面登记 */
        } else if (fieldname[0]) {
            /* 普通表单字段：忽略（目标目录走 query 参数） */
        }
        continue;

multipart_done:
        break;
    }

    if (m.buf) {
        free(m.buf);
        m.buf = NULL;
    }

    {
        snprintf(json, sizeof(json),
                 "{\"ok\":%s,\"count\":%d,\"files\":[", ok ? "true" : "false", nsaved);
        for (int i = 0; i < nsaved; i++) {
            char esc[PARTNAME * 6];
            html_escape(saved[i], esc, sizeof(esc));
            strncat(json, i ? ",\"" : "\"", sizeof(json) - strlen(json) - 1);
            strncat(json, esc, sizeof(json) - strlen(json) - 1);
            strncat(json, "\"", sizeof(json) - strlen(json) - 1);
        }
        strncat(json, "]}", sizeof(json) - strlen(json) - 1);
    }
    (void)source_ip;
    send_common_headers(fd, "200 OK", "application/json; charset=utf-8", (long long)strlen(json));
    send_all(fd, json, strlen(json));
    return 0;
}

/* ------------------------------------------------------------------ 目录页面 */

static char *html_page(const char *rel)
{
    char *page = malloc(64 * 1024 + PATHMAX * 8);
    char up[PATHMAX], enc[PATHMAX * 3], encfull[PATHMAX * 3];
    DIR *d;
    struct dirent *de;
    size_t cap = 64 * 1024 + PATHMAX * 8;
    char dir[PATHMAX];
    char tmp[PATHMAX];
    char esc[PATHMAX * 2];

    if (!page)
        return NULL;

    if (safe_join(g_root, rel, dir, sizeof(dir)) != 0)
        snprintf(dir, sizeof(dir), "%s", g_root);

    url_encode(rel, enc, sizeof(enc));

    snprintf(page, cap,
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>文件互传 - %s</title><style>"
        "*{box-sizing:border-box}"
        "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;padding:24px;background:#141824;color:#e6e8ee}"
        "h1{font-size:20px;margin:0 0 4px}.root{color:#8b93a7;font-size:13px;margin-bottom:18px;word-break:break-all}"
        ".card{background:#1c2130;border:1px solid #2a3145;border-radius:12px;padding:18px;margin-bottom:18px}"
        ".drop{border:2px dashed #3a86ff;border-radius:12px;padding:28px;text-align:center;cursor:pointer;transition:.15s;background:#171c29}"
        ".drop.hover{background:#1f2941;border-color:#66a6ff}"
        ".drop b{color:#3a86ff}"
        "button{background:#3a86ff;color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;margin-right:8px}"
        "button.ghost{background:#2a3145}button:disabled{opacity:.5;cursor:default}"
        "table{width:100%%;border-collapse:collapse;margin-top:6px}th,td{padding:9px 10px;text-align:left;border-bottom:1px solid #262d40}"
        "th{color:#8b93a7;font-weight:600;font-size:13px}a{color:#7cb2ff;text-decoration:none}a:hover{text-decoration:underline}"
        ".sz{text-align:right;color:#8b93a7;white-space:nowrap;font-variant-numeric:tabular-nums}"
        ".dir{color:#f0c040}.bar{height:8px;background:#2a3145;border-radius:4px;overflow:hidden;margin-top:12px;display:none}"
        ".bar i{display:block;height:100%%;width:0;background:#3a86ff;transition:.1s}"
        ".log{margin-top:10px;font-size:13px;color:#8b93a7;white-space:pre-wrap}"
        ".toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}"
        "</style></head><body>"
        "<h1>文件互传</h1><div class=\"root\">共享目录：%s%s</div>"
        "<div class=\"card\"><div class=\"toolbar\"><b>上传到当前目录</b>"
        "<button class=\"ghost\" id=\"pick\">选择文件</button>"
        "<button class=\"ghost\" id=\"mkdir\">新建文件夹</button>"
        "<input id=\"file\" type=\"file\" multiple style=\"display:none\"></div>"
        "<div class=\"drop\" id=\"drop\">把文件拖到这里，或<b>点击选择</b>（支持多选、大文件）</div>"
        "<div class=\"bar\" id=\"bar\"><i></i></div><div class=\"log\" id=\"log\"></div></div>"
        "<div class=\"card\"><table><tr><th>名称</th><th class=\"sz\">大小</th></tr>",
        rel[0] ? rel : "/", g_root, rel[0] ? "/" : "");

    snprintf(esc, sizeof(esc), "%s", rel[0] ? rel : "");
    if (rel[0]) {
        /* 上级目录 */
        char parent[PATHMAX];
        snprintf(parent, sizeof(parent), "%s", rel);
        char *slash = strrchr(parent, '/');
        if (slash) *slash = 0; else parent[0] = 0;
        url_encode(parent, encfull, sizeof(encfull));
        snprintf(tmp, sizeof(tmp), "<tr><td><a href=\"/?path=%s\">../</a></td><td class=\"sz\">-</td></tr>", encfull);
        strncat(page, tmp, cap - strlen(page) - 1);
    }

    d = opendir(dir);
    if (d) {
        /* 先列目录再列文件 */
        for (int pass = 0; pass < 2; pass++) {
            rewinddir(d);
            while ((de = readdir(d))) {
                char full[PATHMAX], childrel[PATHMAX], cenc[PATHMAX * 3];
                struct stat st;
                int isdir;
                char sz[64];

                if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, ".."))
                    continue;
                snprintf(full, sizeof(full), "%s/%s", dir, de->d_name);
                if (stat(full, &st) != 0)
                    continue;
                isdir = S_ISDIR(st.st_mode);
                if ((pass == 0) != isdir)
                    continue;

                if (rel[0])
                    snprintf(childrel, sizeof(childrel), "%s/%s", rel, de->d_name);
                else
                    snprintf(childrel, sizeof(childrel), "%s", de->d_name);
                url_encode(childrel, cenc, sizeof(cenc));
                html_escape(de->d_name, esc, sizeof(esc));
                if (isdir)
                    snprintf(sz, sizeof(sz), "-");
                else
                    human_size((long long)st.st_size, sz, sizeof(sz));

                if (isdir)
                    snprintf(tmp, sizeof(tmp), "<tr><td class=\"dir\">[DIR] <a href=\"/?path=%s\">%s/</a></td><td class=\"sz\">%s</td></tr>",
                             cenc, esc, sz);
                else
                    snprintf(tmp, sizeof(tmp), "<tr><td><a href=\"/files/%s\">%s</a></td><td class=\"sz\">%s</td></tr>",
                             cenc, esc, sz);
                strncat(page, tmp, cap - strlen(page) - 1);
            }
        }
        closedir(d);
    }

    strncat(page,
        "</table></div><script>"
        "var cur='", cap - strlen(page) - 1);
    /* 注入当前目录（JS 转义） */
    {
        char jsesc[PATHMAX * 2];
        size_t o = 0;
        for (size_t i = 0; rel[i] && o + 3 < sizeof(jsesc); i++) {
            if (rel[i] == '\\' || rel[i] == '\'') jsesc[o++] = '\\';
            jsesc[o++] = rel[i];
        }
        jsesc[o] = 0;
        strncat(page, jsesc, cap - strlen(page) - 1);
    }
    strncat(page,
        "';"
        "var drop=document.getElementById('drop'),input=document.getElementById('file'),bar=document.getElementById('bar'),"
        "fill=bar.firstElementChild,log=document.getElementById('log');"
        "function show(){bar.style.display='block'}"
        "function say(t){log.textContent=t}"
        "drop.onclick=function(){input.click()};"
        "document.getElementById('pick').onclick=function(){input.click()};"
        "input.onchange=function(){if(input.files.length)upload(input.files)};"
        "drop.ondragover=function(e){e.preventDefault();drop.classList.add('hover')};"
        "drop.ondragleave=function(){drop.classList.remove('hover')};"
        "drop.ondrop=function(e){e.preventDefault();drop.classList.remove('hover');if(e.dataTransfer.files.length)upload(e.dataTransfer.files)};"
        "function upload(files){"
        " var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('files',files[i],files[i].name);"
        " var xhr=new XMLHttpRequest();"
        " xhr.open('POST','/upload?path='+encodeURIComponent(cur),true);"
        " show();xhr.upload.onprogress=function(e){if(e.lengthComputable){fill.style.width=(e.loaded/e.total*100)+'%';"
        "  say('上传中 '+(e.loaded/1048576).toFixed(1)+' / '+(e.total/1048576).toFixed(1)+' MB')}};"
        " xhr.onload=function(){try{var r=JSON.parse(xhr.responseText);if(r.ok){say('完成，共 '+r.count+' 个文件，正在刷新…');setTimeout(function(){location.reload()},600)}"
        "  else{say('服务器返回失败：'+xhr.responseText)}}catch(e){say('返回异常：'+xhr.responseText)}};"
        " xhr.onerror=function(){say('网络错误（连接被断开？）')};xhr.send(fd);"
        "}"
        "document.getElementById('mkdir').onclick=function(){var n=prompt('文件夹名');if(!n)return;"
        " var xhr=new XMLHttpRequest();xhr.open('POST','/mkdir?path='+encodeURIComponent(cur)+'&name='+encodeURIComponent(n),true);"
        " xhr.onload=function(){location.reload()};xhr.send();};"
        "</script></body></html>",
        cap - strlen(page) - 1);

    return page;
}

static int handle_get(int fd, http_req *r, unsigned char *leftover, size_t leftover_len)
{
    (void)leftover;
    (void)leftover_len;

    if (!strcmp(r->path, "/") || !strcmp(r->path, "/index.html")) {
        char rel[PATHMAX] = "";
        char *page;

        query_get(r->query, "path", rel, sizeof(rel));
        page = html_page(rel);
        if (!page) {
            send_text(fd, "500 Internal Server Error", "oom");
            return 0;
        }
        send_common_headers(fd, "200 OK", "text/html; charset=utf-8", (long long)strlen(page));
        send_all(fd, page, strlen(page));
        free(page);
        return 0;
    }

    if (!strncmp(r->path, "/files/", 7)) {
        char full[PATHMAX], hdr[512];
        struct stat st;
        int f;
        long long start = 0, end, total;
        const char *range = r->range;

        if (safe_join(g_root, r->path + 7, full, sizeof(full)) != 0) {
            send_text(fd, "400 Bad Request", "bad path");
            return 0;
        }
        if (stat(full, &st) != 0 || !S_ISREG(st.st_mode)) {
            send_text(fd, "404 Not Found", "not found");
            return 0;
        }
        total = (long long)st.st_size;
        end = total - 1;

        if (range && !strncmp(range, "bytes=", 6)) {
            const char *p = range + 6;
            if (*p != '-')
                start = atoll(p);
            const char *dash = strchr(p, '-');
            if (dash && dash[1])
                end = atoll(dash + 1);
            if (start < 0) start = 0;
            if (end >= total) end = total - 1;
            if (start > end) {
                send_common_headers(fd, "416 Range Not Satisfiable", "text/plain", 0);
                return 0;
            }
            snprintf(hdr, sizeof(hdr),
                     "HTTP/1.1 206 Partial Content\r\nContent-Type: %s\r\nContent-Length: %lld\r\n"
                     "Content-Range: bytes %lld-%lld/%lld\r\nAccept-Ranges: bytes\r\n"
                     "Content-Disposition: attachment; filename=\"%s\"\r\nConnection: close\r\n\r\n",
                     mime_of(full), end - start + 1, start, end, total, strrchr(full, '/') + 1);
        } else {
            snprintf(hdr, sizeof(hdr),
                     "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %lld\r\nAccept-Ranges: bytes\r\n"
                     "Content-Disposition: attachment; filename=\"%s\"\r\nConnection: close\r\n\r\n",
                     mime_of(full), total, strrchr(full, '/') + 1);
        }
        send_str(fd, hdr);

        f = open(full, O_RDONLY);
        if (f >= 0) {
            char buf[RECVBUF];
            if (lseek(f, start, SEEK_SET) >= 0) {
                long long remain = end - start + 1;
                while (remain > 0) {
                    ssize_t n = read(f, buf, (size_t)(remain < (long long)sizeof(buf) ? remain : (long long)sizeof(buf)));
                    if (n <= 0)
                        break;
                    if (send_all(fd, buf, (size_t)n) != 0)
                        break;
                    remain -= n;
                }
            }
            close(f);
        }
        return 0;
    }

    send_text(fd, "404 Not Found", "not found");
    return 0;
}

static int handle_mkdir(int fd, http_req *r)
{
    char rel[PATHMAX], name[PATHMAX], dir[PATHMAX], full[PATHMAX];

    if (query_get(r->query, "path", rel, sizeof(rel)) != 0) rel[0] = 0;
    if (query_get(r->query, "name", name, sizeof(name)) != 0 || !name[0]) {
        send_text(fd, "400 Bad Request", "missing name");
        return 0;
    }
    if (safe_join(g_root, rel, dir, sizeof(dir)) != 0 || strstr(name, "..") || strchr(name, '/')) {
        send_text(fd, "400 Bad Request", "bad path");
        return 0;
    }
    snprintf(full, sizeof(full), "%s/%s", dir, name);
    if (mkdir(full, 0755) != 0 && errno != EEXIST) {
        send_text(fd, "500 Internal Server Error", strerror(errno));
        return 0;
    }
    send_text(fd, "200 OK", "ok");
    return 0;
}

/* ------------------------------------------------------------------ 连接线程 */

static void *conn_thread(void *arg)
{
    int fd = (int)(long)arg;
    http_req r;
    unsigned char leftover[RECVBUF];
    size_t leftover_len = sizeof(leftover);
    struct timeval tv = {30, 0};

    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    if (read_headers(fd, &r, leftover, &leftover_len) == 0) {
        LOG("REQ %s %s ct=%s len=%lld", r.method, r.path, r.content_type, r.content_length);
        if (!strcmp(r.method, "GET") || !strcmp(r.method, "HEAD"))
            handle_get(fd, &r, leftover, leftover_len);
        else if (!strcmp(r.method, "POST") && !strncmp(r.path, "/upload", 7))
            handle_upload(fd, &r, leftover, leftover_len);
        else if (!strcmp(r.method, "POST") && !strncmp(r.path, "/mkdir", 6))
            handle_mkdir(fd, &r);
        else
            send_text(fd, "405 Method Not Allowed", "only GET / POST(upload,mkdir)");
    }
    close(fd);
    return NULL;
}

static void *accept_thread(void *arg)
{
    (void)arg;

    while (!g_stop_flag) {
        struct sockaddr_in cli;
        socklen_t cl = sizeof(cli);
        int cfd = accept(g_listen_fd, (struct sockaddr *)&cli, &cl);
        pthread_t t;

        if (cfd < 0) {
            if (g_stop_flag)
                break;
            if (errno == EINTR || errno == EAGAIN)
                continue;
            break;
        }
        if (pthread_create(&t, NULL, conn_thread, (void *)(long)cfd) == 0)
            pthread_detach(t);
        else
            close(cfd);
    }
    return NULL;
}

static int server_start(int port, const char *root)
{
    struct sockaddr_in addr;
    int fd;

    pthread_mutex_lock(&g_lock);
    if (g_running) {
        pthread_mutex_unlock(&g_lock);
        return 0;   /* 已在运行 */
    }
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    {
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    }
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0 || listen(fd, 16) != 0) {
        close(fd);
        pthread_mutex_unlock(&g_lock);
        return -1;
    }

    g_listen_fd = fd;
    g_port = port;
    snprintf(g_root, sizeof(g_root), "%s", root && root[0] ? root : "/userdisk");
    g_stop_flag = 0;
    g_running = 1;
    if (pthread_create(&g_accept_thread, NULL, accept_thread, NULL) == 0)
        g_accept_thread_valid = 1;
    pthread_mutex_unlock(&g_lock);
    return 0;
}

static void server_stop(void)
{
    pthread_mutex_lock(&g_lock);
    if (g_running) {
        g_stop_flag = 1;
        if (g_listen_fd >= 0) {
            shutdown(g_listen_fd, SHUT_RDWR);
            close(g_listen_fd);
            g_listen_fd = -1;
        }
        g_running = 0;
    }
    pthread_mutex_unlock(&g_lock);
    if (g_accept_thread_valid) {
        pthread_join(g_accept_thread, NULL);
        g_accept_thread_valid = 0;
    }
}

#ifndef FS_TEST_MAIN

/* ------------------------------------------------------------------ QuickJS 模块 */

/* 事件回调表：app 里会 `FileServer.on('server_started', fn)`。
 * 我们的服务器在自己的线程里跑，跨线程调 JS 不安全；所以回调在 start()/stop()
 * 这两个**本来就在 JS 线程上执行**的入口里同步触发，完全安全。 */
#define MAX_CB 8
static struct {
    char name[32];
    JSValue fn;
} g_cbs[MAX_CB];
static int g_ncbs = 0;

static JSValue make_status(JSContext *ctx)
{
    JSValue o = JS_NewObject(ctx);
    char ip[64] = "";
    char url[128] = "";

    source_ip(ip, sizeof(ip));
    if (ip[0])
        snprintf(url, sizeof(url), "http://%s:%d/", ip, g_port);

    JS_SetPropertyStr(ctx, o, "running", JS_NewBool(ctx, g_running));
    JS_SetPropertyStr(ctx, o, "ok", JS_NewBool(ctx, g_running));
    JS_SetPropertyStr(ctx, o, "code", JS_NewInt32(ctx, g_running ? 0 : -1));
    JS_SetPropertyStr(ctx, o, "port", JS_NewInt32(ctx, g_port));
    JS_SetPropertyStr(ctx, o, "root", JS_NewString(ctx, g_root));
    JS_SetPropertyStr(ctx, o, "ip", JS_NewString(ctx, ip));
    JS_SetPropertyStr(ctx, o, "url", JS_NewString(ctx, url));
    JS_SetPropertyStr(ctx, o, "upload", JS_NewBool(ctx, 1));
    return o;
}

static void fire_event(JSContext *ctx, const char *name)
{
    for (int i = 0; i < g_ncbs; i++) {
        JSValue arg, ret;

        if (strcmp(g_cbs[i].name, name) != 0)
            continue;
        arg = make_status(ctx);
        ret = JS_Call(ctx, g_cbs[i].fn, JS_UNDEFINED, 1, &arg);
        JS_FreeValue(ctx, ret);
        JS_FreeValue(ctx, arg);
    }
}

static JSValue js_on(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *ev;

    if (argc < 2 || !JS_IsFunction(ctx, argv[1]))
        return JS_UNDEFINED;
    ev = JS_ToCString(ctx, argv[0]);
    if (!ev)
        return JS_UNDEFINED;

    for (int i = 0; i < g_ncbs; i++) {
        if (strcmp(g_cbs[i].name, ev) == 0) {
            JS_FreeValue(ctx, g_cbs[i].fn);
            g_cbs[i].fn = JS_DupValue(ctx, argv[1]);
            JS_FreeCString(ctx, ev);
            return JS_UNDEFINED;
        }
    }
    if (g_ncbs < MAX_CB) {
        snprintf(g_cbs[g_ncbs].name, sizeof(g_cbs[g_ncbs].name), "%s", ev);
        g_cbs[g_ncbs].fn = JS_DupValue(ctx, argv[1]);
        g_ncbs++;
    }
    JS_FreeCString(ctx, ev);
    return JS_UNDEFINED;
}

static JSValue js_off(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    const char *ev;

    if (argc < 1)
        return JS_UNDEFINED;
    ev = JS_ToCString(ctx, argv[0]);
    if (!ev)
        return JS_UNDEFINED;
    for (int i = 0; i < g_ncbs; i++) {
        if (strcmp(g_cbs[i].name, ev) == 0) {
            JS_FreeValue(ctx, g_cbs[i].fn);
            g_cbs[i] = g_cbs[g_ncbs - 1];
            g_ncbs--;
            break;
        }
    }
    JS_FreeCString(ctx, ev);
    return JS_UNDEFINED;
}

static JSValue js_start(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    int32_t port = 8080;
    const char *root = NULL;
    int rc;

    if (argc >= 1)
        JS_ToInt32(ctx, &port, argv[0]);
    if (argc >= 2)
        root = JS_ToCString(ctx, argv[1]);

    if (port <= 0 || port > 65535)
        port = 8080;
    if (g_running)
        server_stop();

    rc = server_start(port, root ? root : g_root);
    if (root)
        JS_FreeCString(ctx, root);

    if (rc == 0)
        fire_event(ctx, "server_started");
    else
        fire_event(ctx, "server_error");

    /* 返回状态对象（而不是裸 bool）：app 里既可能 `if (start(...))`，
     * 也可能读 `res.running` —— 对象两种都满足。 */
    return make_status(ctx);
}

static JSValue js_stop(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    server_stop();
    fire_event(ctx, "server_stopped");
    return make_status(ctx);
}

static JSValue js_get_port(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    return JS_NewInt32(ctx, g_port);
}

static JSValue js_get_status(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    return make_status(ctx);
}

static JSValue js_is_running(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    return JS_NewBool(ctx, g_running);
}

static JSValue js_get_root(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
{
    (void)this_val; (void)argc; (void)argv;
    return JS_NewString(ctx, g_root);
}

static int fs_module_init(JSContext *ctx, JSModuleDef *m)
{
    JSValue o = JS_NewObject(ctx);

    JS_SetPropertyStr(ctx, o, "start", JS_NewCFunction(ctx, js_start, "start", 2));
    JS_SetPropertyStr(ctx, o, "stop", JS_NewCFunction(ctx, js_stop, "stop", 0));
    JS_SetPropertyStr(ctx, o, "getPort", JS_NewCFunction(ctx, js_get_port, "getPort", 0));
    JS_SetPropertyStr(ctx, o, "getStatus", JS_NewCFunction(ctx, js_get_status, "getStatus", 0));
    JS_SetPropertyStr(ctx, o, "isRunning", JS_NewCFunction(ctx, js_is_running, "isRunning", 0));
    JS_SetPropertyStr(ctx, o, "getRoot", JS_NewCFunction(ctx, js_get_root, "getRoot", 0));
    /* 事件订阅（app 里是 FileServer.on('server_started'|'server_stopped', fn)） */
    JS_SetPropertyStr(ctx, o, "on", JS_NewCFunction(ctx, js_on, "on", 2));
    JS_SetPropertyStr(ctx, o, "addEventListener", JS_NewCFunction(ctx, js_on, "addEventListener", 2));
    JS_SetPropertyStr(ctx, o, "off", JS_NewCFunction(ctx, js_off, "off", 1));
    JS_SetPropertyStr(ctx, o, "removeEventListener", JS_NewCFunction(ctx, js_off, "removeEventListener", 1));

    /* 原版插件导出的三个名字：default / FileServer / fileServer。
     * app 用的是具名导入 `import { FileServer } from 'fileServer'`，
     * 少一个就会 "Could not find export 'FileServer' in module 'fileServer'" 并让页面黑屏。
     * 每个导出名各持一份引用（JS_SetModuleExport 接管所有权）。 */
    JS_SetModuleExport(ctx, m, "default", JS_DupValue(ctx, o));
    JS_SetModuleExport(ctx, m, "FileServer", JS_DupValue(ctx, o));
    JS_SetModuleExport(ctx, m, "fileServer", o);
    return 0;
}

static JSModuleDef *loader(JSContext *ctx, const char *module_name)
{
    JSModuleDef *m;

    /* 与原插件完全一致：只认 "fileServer" */
    if (strcmp(module_name, "fileServer") != 0)
        return NULL;

    m = JS_NewCModule(ctx, module_name, fs_module_init);
    if (!m)
        return NULL;
    JS_AddModuleExport(ctx, m, "default");
    JS_AddModuleExport(ctx, m, "FileServer");
    JS_AddModuleExport(ctx, m, "fileServer");
    return m;
}

void custom_init_jsapis(void)
{
    registerCModuleLoader("fileServer", loader);
}

#else  /* -DFS_TEST_MAIN：编成普通可执行文件，方便在笔上/主机上用 curl 自测 */

int main(int argc, char **argv)
{
    int port = argc > 1 ? atoi(argv[1]) : 18080;
    const char *root = argc > 2 ? argv[2] : "/userdisk/Favorite";

    signal(SIGPIPE, SIG_IGN);
    if (server_start(port, root) != 0) {
        fprintf(stderr, "start failed: %s\n", strerror(errno));
        return 1;
    }
    printf("serving %s on http://0.0.0.0:%d/\n", g_root, port);
    fflush(stdout);
    for (;;)
        sleep(1);
    return 0;
}

#endif
