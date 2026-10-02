#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一把 Windows 控制台切到 UTF-8，避免中文/符号在 GBK 代码页下报 UnicodeEncodeError。"""

import os
import sys


def init_console() -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


init_console()
