/* term_vt.c —— 终端 VT 仿真 + 位图渲染（被 term.c #include）
 *
 * 为什么要在原生侧画：框架只有比例字体（实测 i×61=241px, M×61=790px），
 * 纯文本行做不出列对齐，跑 vim/top 会错位。所以这里自己做字符网格 + 8x16 点阵，
 * 渲染成 PNG 给页面用 <image> 显示 —— 对齐、颜色、光标、制表符全部可控。
 *
 * 支持范围：C0(BS/HT/LF/CR)、CSI(A B C D E F G H f J K L M P @ S T X d m h l r s u n c)、
 *           SGR(0 1 4 7 22 24 27 30-37 39 40-47 49 90-97 100-107 38;5;n 48;5;n)、
 *           DEC(7/8 存光标、?25 光标显隐、?7 自动换行、?47/1047/1049 备用屏)、OSC 忽略、UTF-8 解码。
 */

#include "zlib_min.h"
#include "font8x16.h"
#include "font_cjk.h"

#define VT_MAX_COLS 240
#define VT_MAX_ROWS 80
#define VT_HIST 600          /* 滚动回看保留的行数 */

#define VT_DEF_FG 0xc9d1d9u
#define VT_DEF_BG 0x0b0f14u

typedef struct {
    unsigned int cp;
    unsigned int fg, bg;
    unsigned char attr;            /* 1=bold 2=underline 4=reverse */
    unsigned char wide;            /* 0=单宽 1=宽字符头 2=宽字符尾 */
} vt_cell_t;

typedef struct {
    int cols, rows;
    vt_cell_t *cells;
    vt_cell_t *alt;
    vt_cell_t *hist;              /* 滚出屏幕的历史行（环形） */
    int hist_count;               /* 累计压入行数 */
    int view;                     /* 向上回看的行数（0=贴底/实时） */
    int use_alt;
    int cx, cy;
    int save_x, save_y, save_fg, save_bg, save_attr;
    int scroll_top, scroll_bot;
    int autowrap, cursor_vis, wrap_pending;
    int dirty;
    int frame;
    /* SGR 当前状态 */
    unsigned int fg, bg;
    unsigned char attr;
    /* 解析状态机 */
    int cs;                        /* 0 ground 1 esc 2 csi 3 osc 4 osc_esc */
    int params[16];
    int nparams, cur, has_cur, priv;
    int utf8_need;
    unsigned int utf8_cp;
} vt_t;

/* 前向声明 */
static void vt_push_hist(vt_t *v);
static int vt_scroll_view(vt_t *v, int delta);

/* ---------------------------------------------------------------- 256 色表 */
static unsigned int vt_color256(int i)
{
    static const unsigned int base[16] = {
        0x0b0f14, 0xf85149, 0x3fb950, 0xd29922, 0x58a6ff, 0xbc8cff, 0x39c5cf, 0xc9d1d9,
        0x6e7681, 0xff7b72, 0x56d364, 0xe3b341, 0x79c0ff, 0xd2a8ff, 0x56d4dd, 0xffffff
    };
    static const int lv[6] = { 0, 95, 135, 175, 215, 255 };
    if (i < 0) return VT_DEF_FG;
    if (i < 16) return base[i];
    if (i < 232) {
        int n = i - 16;
        int r = lv[n / 36], g = lv[(n / 6) % 6], b = lv[n % 6];
        return ((unsigned)r << 16) | ((unsigned)g << 8) | (unsigned)b;
    }
    if (i < 256) {
        int v = 8 + (i - 232) * 10;
        return ((unsigned)v << 16) | ((unsigned)v << 8) | (unsigned)v;
    }
    return VT_DEF_FG;
}

/* ---------------------------------------------------------------- 基础操作 */
static vt_cell_t *vt_at(vt_t *v, int x, int y)
{
    vt_cell_t *buf = v->use_alt ? v->alt : v->cells;
    if (x < 0 || y < 0 || x >= v->cols || y >= v->rows) return NULL;
    return &buf[y * v->cols + x];
}

static void vt_blank_cell(vt_t *v, vt_cell_t *c)
{
    (void)v;
    c->cp = ' ';
    c->fg = VT_DEF_FG;
    c->bg = VT_DEF_BG;
    c->attr = 0;
    c->wide = 0;
}

/* 宽字符（CJK/全角）判定：占两列 */
static int vt_is_wide(unsigned int cp)
{
    return (cp >= 0x1100 && cp <= 0x115F) || (cp >= 0x2E80 && cp <= 0xA4CF) ||
           (cp >= 0xAC00 && cp <= 0xD7A3) || (cp >= 0xF900 && cp <= 0xFAFF) ||
           (cp >= 0xFE30 && cp <= 0xFE4F) || (cp >= 0xFF00 && cp <= 0xFF60) ||
           (cp >= 0xFFE0 && cp <= 0xFFE6);
}

/* 覆盖/擦掉一个格子时，把它的宽字符伙伴一起清掉，否则会留半截字 */
static void vt_fix_wide(vt_t *v, int x, int y)
{
    vt_cell_t *c = vt_at(v, x, y);
    if (!c) return;
    if (c->wide == 1) {
        vt_cell_t *r = vt_at(v, x + 1, y);
        if (r && r->wide == 2) vt_blank_cell(v, r);
    } else if (c->wide == 2) {
        vt_cell_t *l = vt_at(v, x - 1, y);
        if (l && l->wide == 1) vt_blank_cell(v, l);
    }
}

/* CJK 点阵（16x16，二分查找） */
static const unsigned char *vt_cjk_glyph(unsigned int cp)
{
    int lo = 0, hi = FONT_CJK_COUNT - 1;
    while (lo <= hi) {
        int mid = (lo + hi) >> 1;
        unsigned int v = g_cjk_cp[mid];
        if (v == cp) return g_cjk_bits[mid];
        if (v < cp) lo = mid + 1;
        else hi = mid - 1;
    }
    return NULL;
}

static void vt_erase(vt_t *v, int x0, int y0, int x1, int y1)
{
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++) {
            vt_cell_t *c = vt_at(v, x, y);
            if (c) { vt_fix_wide(v, x, y); vt_blank_cell(v, c); }
        }
}

static void vt_scroll_up(vt_t *v, int n)
{
    if (n <= 0) return;
    if (v->scroll_top == 0 && !v->use_alt) {
        for (int i = 0; i < n; i++) vt_push_hist(v);
    }
    if (n > v->scroll_bot - v->scroll_top + 1) n = v->scroll_bot - v->scroll_top + 1;
    vt_cell_t *buf = v->use_alt ? v->alt : v->cells;
    for (int y = v->scroll_top; y <= v->scroll_bot - n; y++)
        memcpy(&buf[y * v->cols], &buf[(y + n) * v->cols], sizeof(vt_cell_t) * (size_t)v->cols);
    vt_erase(v, 0, v->scroll_bot - n + 1, v->cols - 1, v->scroll_bot);
    v->dirty = 1;
}

static void vt_scroll_down(vt_t *v, int n)
{
    if (n <= 0) return;
    if (n > v->scroll_bot - v->scroll_top + 1) n = v->scroll_bot - v->scroll_top + 1;
    vt_cell_t *buf = v->use_alt ? v->alt : v->cells;
    for (int y = v->scroll_bot; y >= v->scroll_top + n; y--)
        memcpy(&buf[y * v->cols], &buf[(y - n) * v->cols], sizeof(vt_cell_t) * (size_t)v->cols);
    vt_erase(v, 0, v->scroll_top, v->cols - 1, v->scroll_top + n - 1);
    v->dirty = 1;
}

static void vt_reset_hard(vt_t *v)
{
    v->fg = VT_DEF_FG; v->bg = VT_DEF_BG; v->attr = 0;
    v->cx = v->cy = 0;
    v->scroll_top = 0; v->scroll_bot = v->rows - 1;
    v->autowrap = 1; v->cursor_vis = 1; v->wrap_pending = 0;
    vt_erase(v, 0, 0, v->cols - 1, v->rows - 1);
    v->dirty = 1;
}

static void vt_clear_alt(vt_t *v)
{
    for (int i = 0; i < v->cols * v->rows; i++) vt_blank_cell(v, &v->alt[i]);
}

static int vt_init(vt_t *v, int cols, int rows)
{
    memset(v, 0, sizeof(*v));
    if (cols < 20) cols = 20;
    if (rows < 4) rows = 4;
    if (cols > VT_MAX_COLS) cols = VT_MAX_COLS;
    if (rows > VT_MAX_ROWS) rows = VT_MAX_ROWS;
    v->cols = cols; v->rows = rows;
    v->cells = (vt_cell_t *)malloc(sizeof(vt_cell_t) * (size_t)cols * (size_t)rows);
    v->alt = (vt_cell_t *)malloc(sizeof(vt_cell_t) * (size_t)cols * (size_t)rows);
    v->hist = (vt_cell_t *)malloc(sizeof(vt_cell_t) * (size_t)cols * (size_t)VT_HIST);
    if (!v->cells || !v->alt || !v->hist) {
        free(v->cells); free(v->alt); free(v->hist);
        v->cells = v->alt = v->hist = NULL;
        return -1;
    }
    memset(v->hist, 0, sizeof(vt_cell_t) * (size_t)cols * (size_t)VT_HIST);
    vt_clear_alt(v);
    vt_reset_hard(v);
    return 0;
}

static void vt_free(vt_t *v)
{
    free(v->cells); free(v->alt); free(v->hist);
    v->cells = v->alt = v->hist = NULL;
}

/* 把屏幕顶行压进历史（仅在整屏滚动时调用，所以 scroll_top 恒为 0） */
static void vt_push_hist(vt_t *v)
{
    if (!v->hist) return;
    vt_cell_t *buf = v->use_alt ? v->alt : v->cells;
    int slot = v->hist_count % VT_HIST;
    memcpy(&v->hist[(size_t)slot * v->cols], &buf[(size_t)v->scroll_top * v->cols],
           sizeof(vt_cell_t) * (size_t)v->cols);
    v->hist_count++;
}

/* 向上/向下回看；返回新的 view 值 */
static int vt_scroll_view(vt_t *v, int delta)
{
    v->view += delta;
    if (v->view < 0) v->view = 0;
    if (v->view > v->hist_count) v->view = v->hist_count;
    v->dirty = 1;
    return v->view;
}

static void vt_resize(vt_t *v, int cols, int rows)
{
    if (cols == v->cols && rows == v->rows) return;
    int oc = v->cols, orows = v->rows;
    if (cols < 20) cols = 20;
    if (rows < 4) rows = 4;
    if (cols > VT_MAX_COLS) cols = VT_MAX_COLS;
    if (rows > VT_MAX_ROWS) rows = VT_MAX_ROWS;
    vt_cell_t *nc = (vt_cell_t *)malloc(sizeof(vt_cell_t) * (size_t)cols * (size_t)rows);
    vt_cell_t *na = (vt_cell_t *)malloc(sizeof(vt_cell_t) * (size_t)cols * (size_t)rows);
    if (!nc || !na) { free(nc); free(na); return; }
    for (int i = 0; i < cols * rows; i++) vt_blank_cell(v, &nc[i]);
    for (int i = 0; i < cols * rows; i++) vt_blank_cell(v, &na[i]);
    int mc = cols < oc ? cols : oc, mr = rows < orows ? rows : orows;
    for (int y = 0; y < mr; y++)
        memcpy(&nc[y * cols], &v->cells[y * oc], sizeof(vt_cell_t) * (size_t)mc);
    free(v->cells); free(v->alt); free(v->hist);
    v->cells = nc; v->alt = na; v->cols = cols; v->rows = rows;
    v->hist = (vt_cell_t *)malloc(sizeof(vt_cell_t) * (size_t)cols * (size_t)VT_HIST);
    if (v->hist) memset(v->hist, 0, sizeof(vt_cell_t) * (size_t)cols * (size_t)VT_HIST);
    v->hist_count = 0; v->view = 0;
    if (v->cx >= cols) v->cx = cols - 1;
    if (v->cy >= rows) v->cy = rows - 1;
    v->scroll_top = 0; v->scroll_bot = rows - 1;
    v->dirty = 1;
}

/* ---------------------------------------------------------------- 输出字符 */
static void vt_newline(vt_t *v)
{
    v->wrap_pending = 0;
    if (v->cy == v->scroll_bot) vt_scroll_up(v, 1);
    else if (v->cy < v->rows - 1) v->cy++;
}

static void vt_putc(vt_t *v, unsigned int cp)
{
    int w = vt_is_wide(cp) ? 2 : 1;
    if (v->wrap_pending) {
        v->wrap_pending = 0;
        v->cx = 0;
        vt_newline(v);
    }
    if (v->cx + w > v->cols) { v->cx = 0; vt_newline(v); }
    vt_cell_t *c = vt_at(v, v->cx, v->cy);
    if (c) {
        vt_fix_wide(v, v->cx, v->cy);
        c->cp = cp ? cp : ' ';
        c->fg = v->fg; c->bg = v->bg; c->attr = v->attr;
        c->wide = (w == 2) ? 1 : 0;
        v->dirty = 1;
    }
    if (w == 2) {
        vt_cell_t *c2 = vt_at(v, v->cx + 1, v->cy);
        if (c2) { c2->cp = 0; c2->fg = v->fg; c2->bg = v->bg; c2->attr = v->attr; c2->wide = 2; }
    }
    v->cx += w;
    if (v->cx >= v->cols) {
        v->cx = v->cols - 1;
        if (v->autowrap) v->wrap_pending = 1;
    }
}

/* ---------------------------------------------------------------- CSI 处理 */
static int vt_param(vt_t *v, int idx, int def)
{
    if (idx >= v->nparams || v->params[idx] == -1) return def;
    return v->params[idx];
}

static void vt_sgr(vt_t *v)
{
    if (v->nparams == 0) { v->fg = VT_DEF_FG; v->bg = VT_DEF_BG; v->attr = 0; return; }
    for (int i = 0; i < v->nparams; i++) {
        int p = v->params[i] < 0 ? 0 : v->params[i];
        if (p == 0) { v->fg = VT_DEF_FG; v->bg = VT_DEF_BG; v->attr = 0; }
        else if (p == 1) v->attr |= 1;
        else if (p == 4) v->attr |= 2;
        else if (p == 7) v->attr |= 4;
        else if (p == 22) v->attr &= (unsigned char)~1;
        else if (p == 24) v->attr &= (unsigned char)~2;
        else if (p == 27) v->attr &= (unsigned char)~4;
        else if (p >= 30 && p <= 37) v->fg = vt_color256(p - 30);
        else if (p == 39) v->fg = VT_DEF_FG;
        else if (p >= 40 && p <= 47) v->bg = vt_color256(p - 40);
        else if (p == 49) v->bg = VT_DEF_BG;
        else if (p >= 90 && p <= 97) v->fg = vt_color256(p - 90 + 8);
        else if (p >= 100 && p <= 107) v->bg = vt_color256(p - 100 + 8);
        else if (p == 38 || p == 48) {
            int is_fg = (p == 38);
            if (i + 1 < v->nparams && v->params[i + 1] == 5 && i + 2 < v->nparams) {
                unsigned int col = vt_color256(v->params[i + 2]);
                if (is_fg) v->fg = col; else v->bg = col;
                i += 2;
            } else if (i + 1 < v->nparams && v->params[i + 1] == 2 && i + 4 < v->nparams) {
                unsigned int col = ((unsigned)(v->params[i + 2] & 255) << 16) |
                                   ((unsigned)(v->params[i + 3] & 255) << 8) |
                                   (unsigned)(v->params[i + 4] & 255);
                if (is_fg) v->fg = col; else v->bg = col;
                i += 4;
            }
        }
    }
}

static void vt_csi_final(vt_t *v, int final)
{
    int n;
    switch (final) {
    case 'A': n = vt_param(v, 0, 1); v->cy -= n; if (v->cy < v->scroll_top) v->cy = v->scroll_top; v->wrap_pending = 0; break;
    case 'B': n = vt_param(v, 0, 1); v->cy += n; if (v->cy > v->scroll_bot) v->cy = v->scroll_bot; v->wrap_pending = 0; break;
    case 'C': n = vt_param(v, 0, 1); v->cx += n; if (v->cx > v->cols - 1) v->cx = v->cols - 1; v->wrap_pending = 0; break;
    case 'D': n = vt_param(v, 0, 1); v->cx -= n; if (v->cx < 0) v->cx = 0; v->wrap_pending = 0; break;
    case 'E': n = vt_param(v, 0, 1); v->cx = 0; v->cy += n; if (v->cy > v->scroll_bot) v->cy = v->scroll_bot; break;
    case 'F': n = vt_param(v, 0, 1); v->cx = 0; v->cy -= n; if (v->cy < v->scroll_top) v->cy = v->scroll_top; break;
    case 'G': v->cx = vt_param(v, 0, 1) - 1; if (v->cx < 0) v->cx = 0; if (v->cx > v->cols - 1) v->cx = v->cols - 1; break;
    case 'd': v->cy = vt_param(v, 0, 1) - 1; if (v->cy < 0) v->cy = 0; if (v->cy > v->rows - 1) v->cy = v->rows - 1; break;
    case 'H': case 'f': {
        int r = vt_param(v, 0, 1) - 1, c = vt_param(v, 1, 1) - 1;
        if (r < 0) r = 0; if (r > v->rows - 1) r = v->rows - 1;
        if (c < 0) c = 0; if (c > v->cols - 1) c = v->cols - 1;
        v->cy = r; v->cx = c; v->wrap_pending = 0;
        break;
    }
    case 'J': {
        int m = vt_param(v, 0, 0);
        if (m == 0) { vt_erase(v, v->cx, v->cy, v->cols - 1, v->cy); vt_erase(v, 0, v->cy + 1, v->cols - 1, v->rows - 1); }
        else if (m == 1) { vt_erase(v, 0, 0, v->cols - 1, v->cy - 1); vt_erase(v, 0, v->cy, v->cx, v->cy); }
        else { vt_erase(v, 0, 0, v->cols - 1, v->rows - 1); }
        v->dirty = 1;
        break;
    }
    case 'K': {
        int m = vt_param(v, 0, 0);
        if (m == 0) vt_erase(v, v->cx, v->cy, v->cols - 1, v->cy);
        else if (m == 1) vt_erase(v, 0, v->cy, v->cx, v->cy);
        else vt_erase(v, 0, v->cy, v->cols - 1, v->cy);
        v->dirty = 1;
        break;
    }
    case 'L': n = vt_param(v, 0, 1); if (v->cy >= v->scroll_top && v->cy <= v->scroll_bot) {
                  int save_bot = v->scroll_bot; v->scroll_bot = v->scroll_bot;
                  vt_scroll_down(v, n); (void)save_bot; } break;
    case 'M': n = vt_param(v, 0, 1); if (v->cy >= v->scroll_top && v->cy <= v->scroll_bot) vt_scroll_up(v, n); break;
    case 'P': n = vt_param(v, 0, 1);
        for (int x = v->cx; x < v->cols; x++) {
            vt_cell_t *d = vt_at(v, x, v->cy);
            vt_cell_t *s = vt_at(v, x + n, v->cy);
            if (d) { if (s) *d = *s; else vt_blank_cell(v, d); }
        }
        v->dirty = 1; break;
    case '@': n = vt_param(v, 0, 1);
        for (int x = v->cols - 1; x >= v->cx + n; x--) {
            vt_cell_t *d = vt_at(v, x, v->cy);
            vt_cell_t *s = vt_at(v, x - n, v->cy);
            if (d && s) *d = *s;
        }
        vt_erase(v, v->cx, v->cy, v->cx + n - 1, v->cy);
        v->dirty = 1; break;
    case 'X': n = vt_param(v, 0, 1); vt_erase(v, v->cx, v->cy, v->cx + n - 1, v->cy); v->dirty = 1; break;
    case 'S': vt_scroll_up(v, vt_param(v, 0, 1)); break;
    case 'T': vt_scroll_down(v, vt_param(v, 0, 1)); break;
    case 'r': {
        int t = vt_param(v, 0, 1) - 1, b = vt_param(v, 1, v->rows) - 1;
        if (t < 0) t = 0; if (b > v->rows - 1) b = v->rows - 1;
        if (t < b) { v->scroll_top = t; v->scroll_bot = b; v->cx = 0; v->cy = 0; }
        break;
    }
    case 'm': vt_sgr(v); break;
    case 's': v->save_x = v->cx; v->save_y = v->cy; break;
    case 'u': v->cx = v->save_x; v->cy = v->save_y; break;
    case 'h': case 'l': {
        int on = (final == 'h');
        for (int i = 0; i < v->nparams; i++) {
            int p = v->params[i];
            if (p == 25) v->cursor_vis = on;
            else if (p == 7) v->autowrap = on;
            else if (p == 1049 || p == 47 || p == 1047) {
                if (on) { v->use_alt = 1; vt_clear_alt(v); v->cx = v->cy = 0; }
                else { v->use_alt = 0; v->cx = v->cy = 0; }
                v->dirty = 1;
            }
        }
        break;
    }
    default: break;
    }
}

/* ---------------------------------------------------------------- 解析入口 */
static void vt_esc_final(vt_t *v, int c)
{
    switch (c) {
    case 'D': vt_newline(v); break;
    case 'E': v->cx = 0; vt_newline(v); break;
    case 'M': if (v->cy == v->scroll_top) vt_scroll_down(v, 1); else if (v->cy > 0) v->cy--; break;
    case '7': v->save_x = v->cx; v->save_y = v->cy; v->save_fg = (int)v->fg; v->save_bg = (int)v->bg; v->save_attr = v->attr; break;
    case '8': v->cx = v->save_x; v->cy = v->save_y; v->fg = (unsigned)v->save_fg; v->bg = (unsigned)v->save_bg; v->attr = (unsigned char)v->save_attr; break;
    case 'c': vt_reset_hard(v); break;
    default: break;
    }
}

static void vt_feed(vt_t *v, const unsigned char *buf, size_t len)
{
    if (len && v->view != 0) { v->view = 0; v->dirty = 1; }   /* 有新输出就跳回实时 */
    for (size_t i = 0; i < len; i++) {
        unsigned char ch = buf[i];

        /* UTF-8 续字节 */
        if (v->utf8_need > 0) {
            if ((ch & 0xC0) == 0x80) {
                v->utf8_cp = (v->utf8_cp << 6) | (unsigned)(ch & 0x3F);
                v->utf8_need--;
                if (v->utf8_need == 0 && v->cs == 0) vt_putc(v, v->utf8_cp);
                continue;
            }
            v->utf8_need = 0;   /* 坏序列，丢掉重来 */
        }

        switch (v->cs) {
        case 0: /* ground */
            if (ch == 0x1B) { v->cs = 1; break; }
            if (ch == 0x0D) { v->cx = 0; v->wrap_pending = 0; break; }
            if (ch == 0x0A || ch == 0x0B || ch == 0x0C) { vt_newline(v); break; }
            if (ch == 0x08) { if (v->cx > 0) v->cx--; v->wrap_pending = 0; break; }
            if (ch == 0x09) { int nx = (v->cx / 8 + 1) * 8; v->cx = nx > v->cols - 1 ? v->cols - 1 : nx; break; }
            if (ch == 0x07 || ch == 0x00) break;
            if (ch < 0x20) break;
            if (ch < 0x80) { vt_putc(v, ch); break; }
            if ((ch & 0xE0) == 0xC0) { v->utf8_cp = ch & 0x1F; v->utf8_need = 1; break; }
            if ((ch & 0xF0) == 0xE0) { v->utf8_cp = ch & 0x0F; v->utf8_need = 2; break; }
            if ((ch & 0xF8) == 0xF0) { v->utf8_cp = ch & 0x07; v->utf8_need = 3; break; }
            break;

        case 1: /* ESC */
            if (ch == '[') { v->cs = 2; v->nparams = 0; v->cur = -1; v->has_cur = 0; v->priv = 0; break; }
            if (ch == ']') { v->cs = 3; break; }
            if (ch == '(' || ch == ')' || ch == '*' || ch == '+' || ch == '#') { v->cs = 5; break; }
            v->cs = 0;
            vt_esc_final(v, ch);
            break;

        case 5: /* ESC ( x 之类的字符集选择，吃一个字节 */
            v->cs = 0;
            break;

        case 2: /* CSI */
            if (ch >= '0' && ch <= '9') {
                if (!v->has_cur) { v->cur = 0; v->has_cur = 1; }
                v->cur = v->cur * 10 + (ch - '0');
                if (v->cur > 100000) v->cur = 100000;
                break;
            }
            if (ch == ';') {
                if (v->nparams < 16) v->params[v->nparams++] = v->has_cur ? v->cur : -1;
                v->cur = -1; v->has_cur = 0;
                break;
            }
            if (ch == '?' || ch == '<' || ch == '=' || ch == '>') { v->priv = ch; break; }
            if (ch >= 0x20 && ch <= 0x2F) break;   /* 中间字节 */
            /* 终止字节 */
            if (v->nparams < 16) v->params[v->nparams++] = v->has_cur ? v->cur : -1;
            v->cs = 0;
            vt_csi_final(v, ch);
            break;

        case 3: /* OSC */
            if (ch == 0x07) { v->cs = 0; break; }
            if (ch == 0x1B) { v->cs = 4; break; }
            break;

        case 4: /* OSC 里的 ESC，等 ST(\) */
            v->cs = (ch == '\\') ? 0 : 3;
            break;
        }
    }
}

/* ---------------------------------------------------------------- PNG 编码 */
static void be32(unsigned char *p, unsigned int v)
{
    p[0] = (unsigned char)(v >> 24); p[1] = (unsigned char)(v >> 16);
    p[2] = (unsigned char)(v >> 8);  p[3] = (unsigned char)v;
}

static void png_chunk(FILE *f, const char *type, const unsigned char *data, unsigned int len)
{
    unsigned char hdr[8], crcbuf[4];
    be32(hdr, len);
    memcpy(hdr + 4, type, 4);
    fwrite(hdr, 1, 8, f);
    if (len) fwrite(data, 1, len, f);
    uLong crc = crc32(0L, Z_NULL, 0);
    crc = crc32(crc, (const Bytef *)type, 4);
    if (len) crc = crc32(crc, data, len);
    be32(crcbuf, (unsigned int)crc);
    fwrite(crcbuf, 1, 4, f);
}

/* 写 24 位 RGB PNG */
static int png_write_rgb(const char *path, const unsigned char *rgb, int w, int h)
{
    size_t rawlen = (size_t)h * (1 + (size_t)w * 3);
    unsigned char *raw = (unsigned char *)malloc(rawlen);
    if (!raw) return -1;
    for (int y = 0; y < h; y++) {
        unsigned char *dst = &raw[(size_t)y * (1 + (size_t)w * 3)];
        const unsigned char *src = rgb + (size_t)y * (size_t)w * 3;
        if (y == 0) {
            dst[0] = 0;
            memcpy(dst + 1, src, (size_t)w * 3);
        } else {
            const unsigned char *up = rgb + (size_t)(y - 1) * (size_t)w * 3;
            dst[0] = 2;   /* Up 滤波：终端画面大片同色，压缩率极好 */
            for (size_t i = 0; i < (size_t)w * 3; i++)
                dst[1 + i] = (unsigned char)(src[i] - up[i]);
        }
    }
    uLongf clen = compressBound((uLong)rawlen);
    unsigned char *cbuf = (unsigned char *)malloc(clen);
    if (!cbuf) { free(raw); return -1; }
    if (compress2(cbuf, &clen, raw, (uLong)rawlen, 3) != Z_OK) { free(raw); free(cbuf); return -1; }
    free(raw);

    FILE *f = fopen(path, "wb");
    if (!f) { free(cbuf); return -1; }
    static const unsigned char sig[8] = { 137, 'P', 'N', 'G', 13, 10, 26, 10 };
    fwrite(sig, 1, 8, f);
    unsigned char ihdr[13];
    be32(ihdr, (unsigned)w); be32(ihdr + 4, (unsigned)h);
    ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    png_chunk(f, "IHDR", ihdr, 13);
    png_chunk(f, "IDAT", cbuf, (unsigned)clen);
    png_chunk(f, "IEND", NULL, 0);
    fclose(f);
    free(cbuf);
    return 0;
}

/* ---------------------------------------------------------------- 渲染 */
static const unsigned char *vt_glyph(unsigned int cp)
{
    static unsigned char box[16];
    if (cp >= 0x20 && cp < 0x7F) return g_font_ascii[cp - 0x20];
    for (unsigned i = 0; i < FONT_SPECIAL_COUNT; i++)
        if (g_font_special[i].cp == (unsigned short)cp) return g_font_special[i].bits;
    /* 未知字符：画个空心框，至少能看出占位 */
    memset(box, 0, sizeof(box));
    box[0] = 0xFF; box[15] = 0xFF; box[1] |= 0x81; box[14] |= 0x81;
    for (int y = 0; y < 16; y++) { box[y] |= 0x80; box[y] |= 0x01; }
    return box;
}

static void vt_blit_cell(unsigned char *buf, int stride, int px, int py,
                         unsigned int fg, unsigned int bg, unsigned char attr, unsigned int cp)
{
    if (attr & 4) { unsigned int t = fg; fg = bg; bg = t; }   /* reverse */
    for (int y = 0; y < FONT_H; y++) {
        unsigned char *row = buf + (size_t)(py + y) * (size_t)stride + (size_t)px * 3;
        for (int x = 0; x < FONT_W; x++) {
            row[x * 3 + 0] = (unsigned char)(bg >> 16);
            row[x * 3 + 1] = (unsigned char)(bg >> 8);
            row[x * 3 + 2] = (unsigned char)bg;
        }
    }
    const unsigned char *g = vt_glyph(cp);
    for (int y = 0; y < FONT_H; y++) {
        unsigned char bits = g[y];
        if (!bits) continue;
        unsigned char *row = buf + (size_t)(py + y) * (size_t)stride + (size_t)px * 3;
        for (int x = 0; x < FONT_W; x++) {
            if (bits & (0x80 >> x)) {
                row[x * 3 + 0] = (unsigned char)(fg >> 16);
                row[x * 3 + 1] = (unsigned char)(fg >> 8);
                row[x * 3 + 2] = (unsigned char)fg;
            }
        }
    }
    if (attr & 2) {   /* underline */
        unsigned char *row = buf + (size_t)(py + FONT_H - 2) * (size_t)stride + (size_t)px * 3;
        for (int x = 0; x < FONT_W; x++) {
            row[x * 3 + 0] = (unsigned char)(fg >> 16);
            row[x * 3 + 1] = (unsigned char)(fg >> 8);
            row[x * 3 + 2] = (unsigned char)fg;
        }
    }
}

/* 宽字符渲染：把一个 16x16 汉字画进相邻两个 8x16 单元 */
static void vt_blit_wide(unsigned char *buf, int stride, int px, int py,
                         unsigned int fg, unsigned int bg, unsigned char attr,
                         const unsigned char *bits)
{
    static unsigned char box[32];
    int w = FONT_CJK_W, h = FONT_CJK_H;
    if (attr & 4) { unsigned int t2 = fg; fg = bg; bg = t2; }
    if (!bits) {                        /* 字库没有该字：画宽占位框 */
        memset(box, 0, sizeof(box));
        for (int y = 0; y < h; y++) { box[y * 2] |= 0x80; box[y * 2 + 1] |= 0x01; }
        box[0] = 0xFF; box[1] = 0xFF;
        box[(h - 1) * 2] = 0xFF; box[(h - 1) * 2 + 1] = 0xFF;
        bits = box;
    }
    for (int y = 0; y < h; y++) {
        unsigned char *row = buf + (size_t)(py + y) * (size_t)stride + (size_t)px * 3;
        unsigned char b0 = bits[y * 2], b1 = bits[y * 2 + 1];
        for (int x = 0; x < w; x++) {
            int on = (x < 8) ? (b0 & (0x80 >> x)) : (b1 & (0x80 >> (x - 8)));
            unsigned int col = on ? fg : bg;
            row[x * 3 + 0] = (unsigned char)(col >> 16);
            row[x * 3 + 1] = (unsigned char)(col >> 8);
            row[x * 3 + 2] = (unsigned char)col;
        }
    }
    if (attr & 2) {
        unsigned char *row = buf + (size_t)(py + h - 2) * (size_t)stride + (size_t)px * 3;
        for (int x = 0; x < w; x++) {
            row[x * 3 + 0] = (unsigned char)(fg >> 16);
            row[x * 3 + 1] = (unsigned char)(fg >> 8);
            row[x * 3 + 2] = (unsigned char)fg;
        }
    }
}
/* 渲染到 PNG；path 形如 /userdisk/term/f0.png */
static int vt_render(vt_t *v, const char *path)
{
    int w = v->cols * FONT_W;
    int h = v->rows * FONT_H;
    size_t sz = (size_t)w * (size_t)h * 3;
    unsigned char *buf = (unsigned char *)malloc(sz);
    if (!buf) return -1;
    /* 底色 */
    for (size_t i = 0; i < sz; i += 3) {
        buf[i + 0] = (unsigned char)(VT_DEF_BG >> 16);
        buf[i + 1] = (unsigned char)(VT_DEF_BG >> 8);
        buf[i + 2] = (unsigned char)VT_DEF_BG;
    }
    vt_cell_t *cells = v->use_alt ? v->alt : v->cells;
    for (int y = 0; y < v->rows; y++) {
        /* 视图偏移：先取历史行，再取屏幕行 */
        int vi = v->hist_count - v->view + y;      /* 该显示行在"历史+屏幕"里的序号 */
        vt_cell_t *rowp;
        if (vi < v->hist_count) rowp = &v->hist[(size_t)(vi % VT_HIST) * v->cols];
        else rowp = &cells[(size_t)(vi - v->hist_count) * v->cols];
        for (int x = 0; x < v->cols; x++) {
            vt_cell_t *c = &rowp[x];
            if (c->wide == 2) continue;                    /* 右半格由头格一起画 */
            if (c->cp == ' ' && c->bg == VT_DEF_BG && !(c->attr & 4)) continue;
            if (c->wide == 1) {
                vt_blit_wide(buf, w * 3, x * FONT_W, y * FONT_H, c->fg, c->bg, c->attr, vt_cjk_glyph(c->cp));
            } else {
                vt_blit_cell(buf, w * 3, x * FONT_W, y * FONT_H, c->fg, c->bg, c->attr, c->cp);
            }
        }
    }
    /* 光标：反白当前格 */
    if (v->cursor_vis && v->view == 0 && v->cx >= 0 && v->cx < v->cols && v->cy >= 0 && v->cy < v->rows) {
        int cxp = v->cx;
        vt_cell_t *c = &cells[v->cy * v->cols + cxp];
        if (c->wide == 2 && cxp > 0) { cxp--; c = &cells[v->cy * v->cols + cxp]; }
        if (c->wide == 1) vt_blit_wide(buf, w * 3, cxp * FONT_W, v->cy * FONT_H, c->fg, c->fg, 0, vt_cjk_glyph(c->cp));
        else vt_blit_cell(buf, w * 3, cxp * FONT_W, v->cy * FONT_H, c->fg, c->fg, 0, c->cp);
    }
    int rc = png_write_rgb(path, buf, w, h);
    free(buf);
    if (rc == 0) { v->dirty = 0; v->frame++; }
    return rc;
}

/* 把屏幕导出成纯文本（调试用） */
static void vt_text(vt_t *v, char *out, size_t cap)
{
    size_t n = 0;
    vt_cell_t *cells = v->use_alt ? v->alt : v->cells;
    for (int y = 0; y < v->rows && n + 2 < cap; y++) {
        for (int x = 0; x < v->cols && n + 2 < cap; x++) {
            unsigned int cp = cells[y * v->cols + x].cp;
            if (cp == 0) continue;                 /* 宽字符右半格 */
            if (cp < 0x80) out[n++] = (char)cp;
            else if (cp < 0x800) { out[n++] = (char)(0xC0 | (cp >> 6)); out[n++] = (char)(0x80 | (cp & 0x3F)); }
            else { out[n++] = (char)(0xE0 | (cp >> 12)); out[n++] = (char)(0x80 | ((cp >> 6) & 0x3F)); out[n++] = (char)(0x80 | (cp & 0x3F)); }
        }
        out[n++] = '\n';
    }
    out[n] = 0;
}
