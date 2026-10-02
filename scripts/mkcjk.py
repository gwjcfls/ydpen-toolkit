#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkcjk.py —— 生成 CJK 点阵字库（16×16，汉字 + 全角 + 中文标点）

用法: python mkcjk.py <cjk.ttf> <font_cjk.h> [size=16]

输出（被 term_vt.c include）：
    g_cjk_count            字形数
    g_cjk_cp[]             码点（升序，二分查找用）
    g_cjk_bits[][32]       16×16 单色点阵：每行 2 字节，MSB 在左

字集：GB2312 一级+二级全部汉字（约 6763）+ CJK 标点(U+3000-303F)
      + 全角(U+FF01-FF60) + 若干常用符号。终端里出现的中文文件名/输出都能覆盖。
"""
import sys
from PIL import Image, ImageDraw, ImageFont

CW, CH = 16, 16          # 一个汉字占两个 8x16 单元
THRESHOLD = 100


def charset():
    cps = set()
    # GB2312 一/二级汉字（按 GB2312 编码枚举后解码，不需要外部字表）
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            cps.add(ord(ch))
    for cp in range(0x3000, 0x3040):      # CJK 标点
        cps.add(cp)
    for cp in range(0xFF01, 0xFF61):      # 全角字母数字标点
        cps.add(cp)
    for cp in (0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x2014, 0x00B7,
               0x2190, 0x2191, 0x2192, 0x2193, 0x25A0, 0x25CF, 0x2605, 0x00A5):
        cps.add(cp)
    return sorted(cps)


def render(font, cp):
    img = Image.new("L", (CW, CH), 0)
    d = ImageDraw.Draw(img)
    try:
        d.text((0, 0), chr(cp), fill=255, font=font)
    except Exception:
        return None
    px = img.load()
    rows = []
    for y in range(CH):
        b0 = b1 = 0
        for x in range(CW):
            if px[x, y] >= THRESHOLD:
                if x < 8:
                    b0 |= 0x80 >> x
                else:
                    b1 |= 0x80 >> (x - 8)
        rows.append(b0)
        rows.append(b1)
    return rows


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ttf, out = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    font = ImageFont.truetype(ttf, size)

    cps, bits = [], []
    for cp in charset():
        g = render(font, cp)
        if not g:
            continue
        cps.append(cp)
        bits.append(g)
    n = len(cps)

    name = ttf.replace("\\", "/").split("/")[-1]
    lines = []
    lines.append("/* 自动生成，勿手改：%s @ %dpx，%dx%d 点阵，%d 个字形 */" % (name, size, CW, CH, n))
    lines.append("#ifndef FONT_CJK_H")
    lines.append("#define FONT_CJK_H")
    lines.append("#define FONT_CJK_W %d" % CW)
    lines.append("#define FONT_CJK_H %d" % CH)
    lines.append("#define FONT_CJK_COUNT %d" % n)
    lines.append("static const unsigned short g_cjk_cp[FONT_CJK_COUNT] = {")
    for i in range(0, n, 16):
        lines.append("  " + ",".join("0x%04x" % c for c in cps[i:i + 16]) + ",")
    lines.append("};")
    lines.append("static const unsigned char g_cjk_bits[FONT_CJK_COUNT][32] = {")
    for g in bits:
        lines.append("  {" + ",".join("0x%02x" % b for b in g) + "},")
    lines.append("};")
    lines.append("#endif")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("生成 %s：%d 个 CJK 字形（约 %d KB）" % (out, n, n * 34 // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
