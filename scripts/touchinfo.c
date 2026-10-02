/* touchinfo.c —— 词典笔触摸设备探测 / 注入工具（支持不同机型的坐标映射）
 *
 * 背景（实测）：
 *   * YDPX6-2（X6 Pro）：触摸 /dev/input/event1（fts_ts），ABS 范围与显示一致（960x266）
 *     —— 老 tap 工具直接发逻辑坐标就行。
 *   * YDPX7-1（X7 Pro）：触摸 /dev/input/event4（hyn_ts），ABS 是**竖屏物理坐标 0..480 x 0..960**，
 *     而框架界面逻辑是 960x266，且界面在 960x480 的逻辑屏里**居中**（上下各留 107px）。
 *     实测换算（逻辑 -> 物理）：
 *         phys_x = logical_y + 107
 *         phys_y = 959 - logical_x
 *     校验：逻辑(30,42) -> 物理(149,929) 点击命中"返回"；逻辑正中文字行 -> phys_y≈458。
 *
 * 用法（笔上执行）：
 *   ./touchinfo /dev/input/event4 info                 # 打印 ABS 范围/能力（判断要不要映射）
 *   ./touchinfo /dev/input/event4 tap <px> <py>        # 直接注入物理坐标
 *   ./touchinfo /dev/input/event4 tapL <x> <y> [off]   # 注入“逻辑坐标”，自动换算（off 默认 107）
 *   ./touchinfo /dev/input/event4 rep <x> <y> [n] [off]# 连点 n 次（逻辑坐标，默认 12 次）
 *   ./touchinfo /dev/input/event4 read <secs>          # 读原始事件，用来标定坐标
 */
#include <linux/input.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>

#define APP_OFFSET_DEFAULT 107   /* 960x266 界面在 960x480 逻辑屏里的上边距 */

static void show_abs(int fd, int code, const char *name)
{
    struct input_absinfo ai;
    memset(&ai, 0, sizeof(ai));
    if (ioctl(fd, EVIOCGABS(code), &ai) == 0)
        printf("%-22s min=%-6d max=%-6d fuzz=%-3d flat=%-3d res=%d\n",
               name, ai.minimum, ai.maximum, ai.fuzz, ai.flat, ai.resolution);
    else
        printf("%-22s (不支持)\n", name);
}

static int bit_get(const unsigned long *bits, int bit)
{
    return (bits[bit / (8 * sizeof(unsigned long))] >> (bit % (8 * sizeof(unsigned long)))) & 1UL;
}

static void emit(int fd, int type, int code, int value)
{
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type; ev.code = code; ev.value = value;
    if (write(fd, &ev, sizeof(ev)) < 0) perror("write");
}

/* 完整 type-B 触摸序列：SLOT -> TRACKING_ID -> 坐标 -> 按下 -> SYN -> 抬起 */
static void do_tap(int fd, int x, int y, int tid)
{
    emit(fd, EV_ABS, ABS_MT_SLOT, 0);
    emit(fd, EV_ABS, ABS_MT_TRACKING_ID, tid);
    emit(fd, EV_ABS, ABS_MT_POSITION_X, x);
    emit(fd, EV_ABS, ABS_MT_POSITION_Y, y);
    emit(fd, EV_ABS, ABS_MT_TOUCH_MAJOR, 8);
    emit(fd, EV_KEY, BTN_TOUCH, 1);
    emit(fd, EV_SYN, SYN_REPORT, 0);
    usleep(80000);
    emit(fd, EV_ABS, ABS_MT_TRACKING_ID, -1);
    emit(fd, EV_KEY, BTN_TOUCH, 0);
    emit(fd, EV_SYN, SYN_REPORT, 0);
}

int main(int argc, char **argv)
{
    const char *dev = (argc > 1) ? argv[1] : "/dev/input/event4";
    int fd = open(dev, O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    struct input_absinfo xi, yi;
    memset(&xi, 0, sizeof(xi)); memset(&yi, 0, sizeof(yi));
    ioctl(fd, EVIOCGABS(ABS_MT_POSITION_X), &xi);
    ioctl(fd, EVIOCGABS(ABS_MT_POSITION_Y), &yi);

    if (argc < 3 || strcmp(argv[2], "info") == 0) {
        printf("=== %s ===\n", dev);
        show_abs(fd, ABS_MT_POSITION_X, "ABS_MT_POSITION_X");
        show_abs(fd, ABS_MT_POSITION_Y, "ABS_MT_POSITION_Y");
        show_abs(fd, ABS_MT_SLOT, "ABS_MT_SLOT");
        show_abs(fd, ABS_MT_TRACKING_ID, "ABS_MT_TRACKING_ID");
        show_abs(fd, ABS_MT_TOUCH_MAJOR, "ABS_MT_TOUCH_MAJOR");
        unsigned long absbits[8] = {0}, keybits[16] = {0}, propbits[2] = {0};
        ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(absbits)), absbits);
        ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keybits)), keybits);
        ioctl(fd, EVIOCGPROP(sizeof(propbits)), propbits);
        printf("cap: MT_SLOT=%d MT_TRACKING_ID=%d BTN_TOUCH=%d BTN_TOOL_FINGER=%d PROP_DIRECT=%d\n",
               bit_get(absbits, ABS_MT_SLOT), bit_get(absbits, ABS_MT_TRACKING_ID),
               bit_get(keybits, BTN_TOUCH), bit_get(keybits, BTN_TOOL_FINGER),
               bit_get(propbits, INPUT_PROP_DIRECT));
        printf("提示：若 X 范围≈0..480 且 Y 范围≈0..960，说明是竖屏物理坐标，"
               "用 tapL 并配合 --offset=%d 做映射\n", APP_OFFSET_DEFAULT);
        return 0;
    }

    if (strcmp(argv[2], "tap") == 0 && argc >= 5) {
        int x = atoi(argv[3]), y = atoi(argv[4]);
        do_tap(fd, x, y, 100);
        printf("tapped phys(%d,%d) on %s  [ABS %d..%d x %d..%d]\n",
               x, y, dev, xi.minimum, xi.maximum, yi.minimum, yi.maximum);
        return 0;
    }

    if (strcmp(argv[2], "tapL") == 0 && argc >= 5) {
        int X = atoi(argv[3]), Y = atoi(argv[4]);
        int off = (argc >= 6) ? atoi(argv[5]) : APP_OFFSET_DEFAULT;
        int px = Y + off;          /* 物理 X = 逻辑 Y + 上边距 */
        int py = (yi.maximum ? yi.maximum : 959) - X;  /* 物理 Y = 最大 - 逻辑 X */
        do_tap(fd, px, py, 100);
        printf("tapL logical(%d,%d) -> phys(%d,%d)  offset=%d on %s\n", X, Y, px, py, off, dev);
        return 0;
    }

    if (strcmp(argv[2], "rep") == 0 && argc >= 5) {
        int X = atoi(argv[3]), Y = atoi(argv[4]);
        int n = (argc >= 6) ? atoi(argv[5]) : 12;
        int off = (argc >= 7) ? atoi(argv[6]) : APP_OFFSET_DEFAULT;
        int px = Y + off, py = (yi.maximum ? yi.maximum : 959) - X;
        for (int i = 0; i < n; i++) {
            do_tap(fd, px, py, 100 + i);
            usleep(120000);
        }
        printf("rep logical(%d,%d) x%d -> phys(%d,%d) on %s\n", X, Y, n, px, py, dev);
        return 0;
    }

    if (strcmp(argv[2], "read") == 0) {
        int secs = (argc >= 4) ? atoi(argv[3]) : 15;
        struct input_event ev;
        time_t t0 = time(NULL);
        printf("读 %d 秒原始事件（请用手指点一下屏幕）…\n", secs);
        while (time(NULL) - t0 < secs) {
            fd_set rf;
            struct timeval tv = {0, 200000};
            FD_ZERO(&rf); FD_SET(fd, &rf);
            if (select(fd + 1, &rf, NULL, NULL, &tv) <= 0) continue;
            if (read(fd, &ev, sizeof(ev)) != (ssize_t)sizeof(ev)) continue;
            if (ev.type == EV_ABS)
                printf("ABS code=%d value=%d   (X范围 %d..%d, Y范围 %d..%d)\n",
                       ev.code, ev.value, xi.minimum, xi.maximum, yi.minimum, yi.maximum);
            else if (ev.type == EV_KEY)
                printf("KEY code=%d value=%d\n", ev.code, ev.value);
        }
        return 0;
    }

    fprintf(stderr, "usage: %s <evdev> [info | tap px py | tapL x y [off] | rep x y [n] [off] | read secs]\n", argv[0]);
    return 2;
}
