/* tap.c ?? ???????????????????????
 * ??: tap <event_dev> <x> <y>      ?: ./tap /dev/input/event1 480 133
 */
#include <linux/input.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>

static void emit(int fd, int type, int code, int val) {
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type; ev.code = code; ev.value = val;
    write(fd, &ev, sizeof(ev));
}
int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s <dev> <x> <y>\n", argv[0]); return 2; }
    int fd = open(argv[1], O_WRONLY);
    if (fd < 0) { perror("open"); return 1; }
    int x = atoi(argv[2]), y = atoi(argv[3]);
    emit(fd, EV_ABS, ABS_MT_TRACKING_ID, 1);
    emit(fd, EV_ABS, ABS_MT_POSITION_X, x);
    emit(fd, EV_ABS, ABS_MT_POSITION_Y, y);
    emit(fd, EV_KEY, BTN_TOUCH, 1);
    emit(fd, EV_SYN, SYN_REPORT, 0);
    usleep(60000);
    emit(fd, EV_ABS, ABS_MT_TRACKING_ID, -1);
    emit(fd, EV_KEY, BTN_TOUCH, 0);
    emit(fd, EV_SYN, SYN_REPORT, 0);
    close(fd);
    printf("tapped %d,%d on %s\n", x, y, argv[1]);
    return 0;
}
