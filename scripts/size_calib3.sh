#!/bin/sh
# 视频窗口尺寸标定（v3）—— 带设备校验，避免跑错笔
export XDG_RUNTIME_DIR=/run
export WAYLAND_DISPLAY=wayland-0

# 设备校验：必须是我们那台（CHN PLUS，有 pstore 设备密钥）
if [ ! -f /userdisk/pstore/.device_key ]; then
  echo "!! 这不是目标设备（找不到 /userdisk/pstore/.device_key），中止"
  exit 1
fi
echo "设备校验通过: $(cat /userdisk/pstore/.device_key)"

cd /userdisk/Favorite || { echo "!! 进不去 /userdisk/Favorite"; exit 2; }
rm -f wayland-screenshot-*.png size_*.png 2>/dev/null

try() {
  W=$1
  echo "=== 目标宽度: $W ${W:+px} ==="
  if [ "$W" = "0" ]; then
    gst-launch-1.0 -q playbin uri=file:///tmp/s360.mp4 video-sink=waylandsink >/tmp/g_$W.log 2>&1 &
  else
    gst-launch-1.0 -q filesrc location=/tmp/s360.mp4 ! qtdemux ! h264parse ! mppvideodec \
      ! videoscale ! video/x-raw,width=$W ! waylandsink >/tmp/g_$W.log 2>&1 &
  fi
  GPID=$!
  sleep 5
  weston-screenshooter >/dev/null 2>&1
  sleep 1
  LATEST=$(ls -t wayland-screenshot-*.png 2>/dev/null | head -1)
  if [ -n "$LATEST" ]; then
    cp "$LATEST" "size_${W}.png"
    echo "  -> size_${W}.png  $(ls -l size_${W}.png | awk '{print $5}') 字节"
    rm -f "$LATEST"
  else
    echo "  !! 截图失败"
  fi
  kill $GPID 2>/dev/null
  sleep 2
}

try 0
try 480
try 320
try 266
try 200
echo "=== 结果 ==="
ls -l size_*.png 2>/dev/null
