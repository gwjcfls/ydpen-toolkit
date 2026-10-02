/* swipe.c —— 触控手势注入（给词典笔远程操作用）
 * 用法: swipe <event_dev> <x1> <y1> <x2> <y2> [duration_ms]
 *   例: ./swipe /dev/input/event1 480 250 480 30 300     # 从下往上滑
 * 编译: zig cc -target aarch64-linux-gnu.2.29 -O2 swipe.c -o swipe
 */
#include <linux/input.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void emit(int fd, int type, int code, int val)
{
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type;
    ev.code = code;
    ev.value = val;
    if (write(fd, &ev, sizeof(ev)) < 0) { /* ignore */ }
}

static void syn(int fd) { emit(fd, EV_SYN, SYN_REPORT, 0); }

int main(int argc, char **argv)
{
    int fd, x1, y1, x2, y2, dur = 300, steps, i;
    long step_us;

    if (argc < 6) {
        fprintf(stderr, "usage: %s <dev> <x1> <y1> <x2> <y2> [duration_ms]\n", argv[0]);
        return 2;
    }
    fd = open(argv[1], O_WRONLY);
    if (fd < 0) { perror("open"); return 1; }

    x1 = atoi(argv[2]); y1 = atoi(argv[3]);
    x2 = atoi(argv[4]); y2 = atoi(argv[5]);
    if (argc > 6) dur = atoi(argv[6]);
    if (dur < 20) dur = 20;

    steps = dur / 10;
    if (steps < 3) steps = 3;
    step_us = (long)dur * 1000 / steps;

    emit(fd, EV_ABS, ABS_MT_TRACKING_ID, 1);
    emit(fd, EV_ABS, ABS_MT_POSITION_X, x1);
    emit(fd, EV_ABS, ABS_MT_POSITION_Y, y1);
    emit(fd, EV_KEY, BTN_TOUCH, 1);
    syn(fd);

    for (i = 1; i <= steps; i++) {
        int x = x1 + (x2 - x1) * i / steps;
        int y = y1 + (y2 - y1) * i / steps;
        emit(fd, EV_ABS, ABS_MT_POSITION_X, x);
        emit(fd, EV_ABS, ABS_MT_POSITION_Y, y);
        syn(fd);
        usleep(step_us);
    }

    emit(fd, EV_ABS, ABS_MT_TRACKING_ID, -1);
    emit(fd, EV_KEY, BTN_TOUCH, 0);
    syn(fd);
    close(fd);
    printf("swiped (%d,%d) -> (%d,%d) in %dms\n", x1, y1, x2, y2, dur);
    return 0;
}
