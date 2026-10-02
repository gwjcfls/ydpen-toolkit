#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkfont.py —— 从 TTF 生成终端点阵字库（C 头文件）

- 8x16 单色点阵，每个字形 16 字节（MSB 在左）
- ASCII 0x20..0x7E 用字体渲染；制表符/方块字符用程序化绘制（多数等宽字体没有这些字形）
- 生成 font8x16.h：g_font_ascii[95][16] + g_font_special[] (codepoint -> 16 字节)

用法: python mkfont.py <字体.ttf> <输出.h> [字号]
"""
import sys
from PIL import Image, ImageDraw, ImageFont

CELL_W, CELL_H = 8, 16
THRESHOLD = 100


def render_glyph(font, ch):
    """把单个字形画进 8x16 单元，返回 16 字节（每行 1 字节，MSB 左）"""
    img = Image.new("L", (CELL_W, CELL_H), 0)
    d = ImageDraw.Draw(img)
    d.text((0, 1), ch, fill=255, font=font)
    px = img.load()
    rows = []
    for y in range(CELL_H):
        b = 0
        for x in range(CELL_W):
            if px[x, y] >= THRESHOLD:
                b |= 0x80 >> x
        rows.append(b)
    return rows


def blank():
    return [0] * CELL_H


def hline(rows, x0, x1, y0, y1):
    for y in range(y0, y1):
        for x in range(x0, x1):
            rows[y] |= 0x80 >> x


def vline(rows, x0, x1, y0, y1):
    for y in range(y0, y1):
        for x in range(x0, x1):
            rows[y] |= 0x80 >> x


def arms(up=False, down=False, left=False, right=False, double=False):
    """程序化画制表符：以单元中心线为轴"""
    rows = blank()
    cx0, cx1 = (CELL_W // 2 - 1, CELL_W // 2 + 1) if not double else (CELL_W // 2 - 2, CELL_W // 2)
    cy0, cy1 = (CELL_H // 2 - 1, CELL_H // 2 + 1) if not double else (CELL_H // 2 - 2, CELL_H // 2)
    if left:
        hline(rows, 0, cx1 if not double else cx1, cy0, cy1)
    if right:
        hline(rows, cx0 if not double else cx0, CELL_W, cy0, cy1)
    if up:
        vline(rows, cx0, cx1, 0, cy1)
    if down:
        vline(rows, cx0, cx1, cy0, CELL_H)
    return rows


def block(kind):
    rows = blank()
    if kind == "full":
        return [0xFF] * CELL_H
    if kind == "upper":
        return [0xFF] * (CELL_H // 2) + [0] * (CELL_H // 2)
    if kind == "lower":
        return [0] * (CELL_H // 2) + [0xFF] * (CELL_H // 2)
    if kind == "left":
        return [0xF0] * CELL_H
    if kind == "right":
        return [0x0F] * CELL_H
    if kind == "l1":
        return [0xC0] * CELL_H
    if kind == "l2":
        return [0xE0] * CELL_H
    if kind == "l3":
        return [0xF0] * CELL_H
    if kind == "l4":
        return [0xF8] * CELL_H
    if kind == "l5":
        return [0xFC] * CELL_H
    if kind == "l6":
        return [0xFE] * CELL_H
    if kind == "shade1":          # ░
        return [0x88 if y % 2 == 0 else 0x22 for y in range(CELL_H)]
    if kind == "shade2":          # ▒
        return [0xAA if y % 2 == 0 else 0x55 for y in range(CELL_H)]
    if kind == "shade3":          # ▓
        return [0xEE if y % 2 == 0 else 0xBB for y in range(CELL_H)]
    return rows


def arrow(direction):
    rows = blank()
    if direction == "up":
        for i in range(7):
            hline(rows, 3 - i if i < 3 else 0, 5 + i if i < 3 else CELL_W, 3 + i, 4 + i)
        vline(rows, 3, 5, 3, 13)
    return rows


SPECIALS = {
    0x2500: arms(left=True, right=True),
    0x2502: arms(up=True, down=True),
    0x250C: arms(down=True, right=True),
    0x2510: arms(down=True, left=True),
    0x2514: arms(up=True, right=True),
    0x2518: arms(up=True, left=True),
    0x251C: arms(up=True, down=True, right=True),
    0x2524: arms(up=True, down=True, left=True),
    0x252C: arms(left=True, right=True, down=True),
    0x2534: arms(left=True, right=True, up=True),
    0x253C: arms(up=True, down=True, left=True, right=True),
    0x2550: arms(left=True, right=True, double=True),
    0x2551: arms(up=True, down=True, double=True),
    0x2580: block("upper"),
    0x2584: block("lower"),
    0x2588: block("full"),
    0x258C: block("left"),
    0x2590: block("right"),
    0x2591: block("shade1"),
    0x2592: block("shade2"),
    0x2593: block("shade3"),
    0x2581: block("l1"), 0x2582: block("l2"), 0x2583: block("l3"),
    0x2585: block("l4"), 0x2586: block("l5"), 0x2587: block("l6"),
    0x2594: block("upper"), 0x2595: block("l6"),
    0x2190: arms(left=True, right=False) or blank(),
}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ttf, out = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    font = ImageFont.truetype(ttf, size)

    # 简单的居中修正：用 getbbox 算一下整体偏移
    lines = []
    lines.append("/* 自动生成，勿手改：%s @ %dpx，单元 %dx%d */" % (ttf.split("\\")[-1].split("/")[-1], size, CELL_W, CELL_H))
    lines.append("#ifndef FONT8X16_H")
    lines.append("#define FONT8X16_H")
    lines.append("#define FONT_W %d" % CELL_W)
    lines.append("#define FONT_H %d" % CELL_H)
    lines.append("static const unsigned char g_font_ascii[95][16] = {")
    for cp in range(0x20, 0x7F):
        g = render_glyph(font, chr(cp))
        lines.append("  {" + ",".join("0x%02x" % b for b in g) + "}, /* %s */" % (chr(cp) if cp != 0x20 else "space"))
    lines.append("};")
    lines.append("typedef struct { unsigned short cp; unsigned char bits[16]; } font_special_t;")
    lines.append("static const font_special_t g_font_special[] = {")
    for cp, g in sorted(SPECIALS.items()):
        lines.append("  {0x%04x, {%s}}," % (cp, ",".join("0x%02x" % b for b in g)))
    lines.append("};")
    lines.append("#define FONT_SPECIAL_COUNT (sizeof(g_font_special)/sizeof(g_font_special[0]))")
    lines.append("#endif")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("生成 %s：ASCII 95 字形 + 特殊 %d 字形" % (out, len(SPECIALS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
