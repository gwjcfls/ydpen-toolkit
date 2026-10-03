#!/bin/sh
# ============================================================================
# sideload-keeper —— 侧载应用的「保活兜底 + 数据镜像」
# ============================================================================
# 背景（实测，详见 skill reference/09）：
#   桌面里的 AppWhitelistCleaner 会在开机后 ~35s 拉云端白名单
#   （https://api-overmind.youdao.com/.../appWhitelist），不在 allowedApps 里的应用
#   会被 pm.removePackage 直接卸载，而且应用数据就在 <包>/data/ 里 → 连数据一起没。
#
#   两条防线：
#     ① DNS 劫持该域名（阶段 0）→ cleaner 拉不到名单 → 它自己跳过（最干净，无删除动作）
#     ② 本脚本兜底：万一官方改了验证链接/域名，名单又能拉到 → 应用会被删 → 我们装回来，
#        并用硬链接数据镜像恢复 <包>/data/
#
#   实测 cleaner **每次开机只跑一次**。所以一旦确认这次开机它跳过了，就没什么可守的了 →
#   本脚本做完最后一次数据镜像后**自行退出**，不常驻。
#   为防止"退出后数据变旧"，退出前把镜像任务交给 cron（每 5 分钟一次，进程不在）；
#   若 cron 不可用，则退回常驻轮询。
#
# 三种模式：
#   默认（开机由 /etc/init.d/S99_run_test_scripts 拉起）：劫持 DNS → 等 cleaner 判定
#        · 判定为"跳过" → 最终镜像 → 装好 cron 镜像任务 → 退出
#        · 判定为"有删除" → 修复（重装+恢复数据）→ 转入常驻轮询
#        · 超时没等到判定 → 转入常驻轮询（保守）
#   KEEPER_MODE=mirror ：只做一次数据镜像就退出（供 cron 调用）
#   KEEPER_ONESHOT=1   ：跑一轮判定即退出（离线自测用）
#
# 部署：本脚本放 /userdisk/skip_re/skip_login.sh（chmod +x）；
#       要保活的 .amr 放 /userdisk/skip_re/amr/（appid 自动从包内 manifest.json 解析）
# 日志：/userdisk/skip_re/sideload-keeper.log
# 停止：kill $(cat /tmp/sideload-keeper.pid)
#       ⚠ 别用 `pkill -f skip_login.sh` —— 会匹配到 sh 自己的命令行，把调用方一起杀掉
# ============================================================================

# ---- 路径与开关（都可用环境变量覆盖，便于离线自测）----
SELF=$0
AMRDIR=${KEEPER_AMRDIR:-/userdisk/skip_re/amr}
APPDIR=${KEEPER_APPDIR:-/userdisk/secondary/miniapp/data/mini_app/pkg}
REG=${KEEPER_REG:-/userdata/miniapp/data/mini_app/pkg/packages.json}
BAK=${KEEPER_BAK:-/userdisk/skip_re/data-backup}
LOG=${KEEPER_LOG:-/userdisk/skip_re/sideload-keeper.log}
LOCK=${KEEPER_LOCK:-/tmp/sideload-keeper.pid}
READY=${KEEPER_READY:-/tmp/SecondaryStartupReady}
MINIAPP=${KEEPER_MINIAPP:-miniapp_cli}
FWLOG_GLOB=${KEEPER_FWLOG:-/data/applog/DictPen_*.log}
DISTRO=${KEEPER_DISTRO:-/userdisk/skip_re}
MODE=${KEEPER_MODE:-boot}
INTERVAL=${KEEPER_INTERVAL:-30}          # 常驻轮询间隔
WAIT_DECISION=${KEEPER_WAIT_DECISION:-300}  # 等 cleaner 判定的上限（秒）
CRONTAB_CMD=${KEEPER_CRONTAB:-crontab}   # 可覆盖（离线自测用）
WAIT_READY=${KEEPER_WAIT_READY:-1}       # 自测设 0
WAIT_DNS=${KEEPER_WAIT_DNS:-0}           # DNS 劫持生效的额外等待

log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }

# 日志轮转（别把 /userdisk 写满）
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 300000 ]; then
    tail -c 80000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
fi

# ---------------------------------------------------------------------------
# 硬链接镜像：src → dst
#   cp -al 只加目录项、不复制数据块（零成本）；原文件被 unlink 后镜像 inode 依然存活。
#   失败（跨文件系统等）则退回真实复制。
# ---------------------------------------------------------------------------
mirror() {
    src=$1; dst=$2
    [ -d "$src" ] || return 1
    rm -rf "$dst.tmp"
    mkdir -p "$dst.tmp" || return 1
    if cp -al "$src/." "$dst.tmp/" 2>/dev/null; then
        rm -rf "$dst"; mv "$dst.tmp" "$dst"; return 0
    fi
    rm -rf "$dst.tmp"
    cp -a "$src/." "$dst" 2>/dev/null
}

# 从 amr 里解析 appid（keeper 按 *.amr 遍历，不再依赖手写 .appid）
read_appid() {
    out=$(unzip -p "$1" manifest.json 2>/dev/null)
    [ -n "$out" ] || out=$(busybox unzip -p "$1" manifest.json 2>/dev/null)
    echo "$out" | tr -d ' \r\n' | sed -n 's/.*"appid":"\([0-9][0-9]*\)".*/\1/p'
}

# 遍历所有受保护应用；$1=only-mirror 时只做镜像、不做修复
sweep() {
    only_mirror=$1
    [ -d "$AMRDIR" ] || return 0
    for amr in "$AMRDIR"/*.amr; do
        [ -f "$amr" ] || continue
        base=$(basename "$amr" .amr)
        idfile="$AMRDIR/$base.appid"

        appid=""
        [ -f "$idfile" ] && appid=$(tr -d ' \r\n' < "$idfile" 2>/dev/null)
        case "$appid" in ''|*[!0-9]*) appid="" ;; esac     # 只接受纯数字
        if [ -z "$appid" ]; then
            appid=$(read_appid "$amr")
            [ -n "$appid" ] && echo "$appid" > "$idfile"
        fi
        [ -n "$appid" ] || { log "!! $base 读不出 appid，跳过"; continue; }

        if [ -d "$APPDIR/$appid/data" ]; then
            mirror "$APPDIR/$appid/data" "$BAK/$appid" && log "镜像 $base/$appid（$(du -sk "$BAK/$appid" 2>/dev/null | cut -f1)KB）"
        fi
        [ "$only_mirror" = "1" ] && continue

        pkgok=0; [ -f "$APPDIR/$appid/a/manifest.json" ] && pkgok=1
        regok=0; grep -q "$appid" "$REG" 2>/dev/null && regok=1
        [ "$pkgok" = 1 ] && [ "$regok" = 1 ] && continue

        log "修复 $base (appid=$appid)：包目录=$pkgok 注册表=$regok"
        [ -d "$APPDIR/$appid" ] && rm -rf "$APPDIR/$appid"
        log "  install → $($MINIAPP install "$amr" 2>&1 | tail -1)"

        if [ -d "$BAK/$appid" ] && [ -n "$(ls -A "$BAK/$appid" 2>/dev/null)" ]; then
            mkdir -p "$APPDIR/$appid/data"
            if cp -a "$BAK/$appid/." "$APPDIR/$appid/data/" 2>/dev/null; then
                log "  已恢复 data/（$(du -sk "$BAK/$appid" 2>/dev/null | cut -f1)KB）"
            else
                log "  !! data/ 恢复失败（镜像仍在 $BAK/$appid）"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# 阶段 0：DNS 劫持（可关：把 dns-blackhole.conf 清空即可）
# ---------------------------------------------------------------------------
BHS=$DISTRO/dns-blackhole.sh
if [ -x "$BHS" ] && [ -s "$DISTRO/dns-blackhole.conf" ] \
   && grep -qv '^[[:space:]]*#' "$DISTRO/dns-blackhole.conf" 2>/dev/null; then
    sh "$BHS" apply >/dev/null 2>&1
    log "DNS 劫持已应用：$(grep '127.0.0.1' "$DISTRO/hosts-custom" 2>/dev/null | grep -v localhost | awk '{print $2}' | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# 阶段 0b：USB 模式（可选持久化 —— 见 tools/usb-mode.sh）
#   /tmp/.usb_config 是 tmpfs，重启即丢；丢了就回落到只读区那份（原厂=仅 MTP），
#   连 adbd 都不会启动。若用户用 usb-mode.sh persist 记录过选择，这里每开机恢复一次。
# ---------------------------------------------------------------------------
UMC=$DISTRO/usb-mode.conf
if [ -s "$UMC" ] && [ -x "$DISTRO/usb-mode.sh" ]; then
    cp "$UMC" /tmp/.usb_config 2>/dev/null
    /usr/bin/S98usbdevice start >/dev/null 2>&1
    log "USB 模式已恢复：$(tr '\n' ' ' < /tmp/.usb_config 2>/dev/null)"
fi

# ---------------------------------------------------------------------------
# 模式：只做一次镜像（cron 调用）
# ---------------------------------------------------------------------------
if [ "$MODE" = "mirror" ]; then
    mkdir -p "$BAK"
    sweep 1
    exit 0
fi

# ---------------------------------------------------------------------------
# 单实例（除 mirror 模式外）
# ---------------------------------------------------------------------------
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
    exit 0
fi
echo $$ > "$LOCK"

log "===== keeper 启动 pid=$$ mode=$MODE ====="

# ---------------------------------------------------------------------------
# 阶段 1：等框架就绪
# ---------------------------------------------------------------------------
i=0
if [ "$WAIT_READY" = 1 ]; then
    while [ "$i" -lt 100 ] && [ ! -f "$READY" ]; do
        sleep 3
        i=$((i + 1))
    done
fi
[ "$WAIT_DNS" -gt 0 ] && sleep "$WAIT_DNS"
log "框架就绪（等待 $((i * 3))s）"

# ---------------------------------------------------------------------------
# 阶段 2：等 cleaner 的判定（本次开机的日志，只看启动之后新增的内容）
#   removed  → 有应用被删，需要修复
#   skipped  → cleaner 跳过了，本次开机不会再动手 → 可以退出
#   timeout  → 没等到判定，保守起见转入常驻轮询
# ---------------------------------------------------------------------------
FWLOG=$(ls -t $FWLOG_GLOB 2>/dev/null | head -1)
POS=0
[ -n "$FWLOG" ] && POS=$(wc -c < "$FWLOG" 2>/dev/null)
SEEN=${KEEPER_SEEN:-${LOG%.log}.seen}      # 与日志同目录（一定可写；重定向失败会让 sh 直接退出）
: > "$SEEN" 2>/dev/null

decision() {
    [ -n "$FWLOG" ] && [ -f "$FWLOG" ] || { echo timeout; return; }
    tail -c +$((POS + 1)) "$FWLOG" 2>/dev/null >> "$SEEN"
    POS=$(wc -c < "$FWLOG" 2>/dev/null)
    grep -q 'removing:' "$SEEN" 2>/dev/null && { echo removed; return; }
    grep -qE 'allowedApps list is empty, skip|appWhitelist is disabled or empty, skip|fetch overmind appWhitelist failed' "$SEEN" 2>/dev/null \
        && { echo skipped; return; }
    echo pending
}

if [ "$MODE" = "boot" ] && [ -n "$FWLOG" ]; then
    waited=0
    while [ "$waited" -lt "$WAIT_DECISION" ]; do
        d=$(decision)
        case "$d" in
            removed)
                log "检测到 cleaner 执行了清理（removing）→ 进入修复+常驻模式"
                break ;;
            skipped)
                log "cleaner 已跳过（白名单拉不到/为空）→ 做最终镜像后退出"
                mkdir -p "$BAK"
                sweep 1
                # 镜像交给 cron，退出后依然保持新鲜（cron 不可用则退回常驻）
                if command -v ${CRONTAB_CMD%% *} >/dev/null 2>&1; then
                    ct=${LOG%.log}.cron.$$          # 与日志同目录（一定可写，别用 /tmp）
                    $CRONTAB_CMD -l 2>/dev/null | grep -v 'KEEPER_MODE=mirror' > "$ct"
                    echo "*/5 * * * * KEEPER_MODE=mirror /bin/sh $SELF >/dev/null 2>&1" >> "$ct"
                    if $CRONTAB_CMD "$ct" 2>/dev/null; then
                        log "已把镜像任务交给 cron（每 5 分钟）"
                        rm -f "$ct" "$LOCK"
                        log "===== keeper 正常退出（本次开机无需守护）====="
                        exit 0
                    fi
                    rm -f "$ct"
                    log "!! crontab 安装失败 → 保持常驻"
                else
                    log "!! 没有 crontab → 保持常驻"
                fi
                break ;;
            *)
                [ "$MODE" = "oneshot" ] && break
                sleep 5
                waited=$((waited + 5)) ;;
        esac
    done
    if [ "$MODE" = "boot" ] && [ "$waited" -ge "$WAIT_DECISION" ]; then
        log "等 cleaner 判定超时（${WAIT_DECISION}s）→ 保守转入常驻"
    fi
fi

# 自测模式：只跑一轮
if [ "$MODE" = "oneshot" ] || [ "$ONESHOT" = "1" ]; then
    mkdir -p "$BAK"
    sweep "${KEEPER_SWEEP_ONLY:-0}"
    log "-- oneshot 结束"
    exit 0
fi

# ---------------------------------------------------------------------------
# 阶段 3：常驻轮询（只在"没确认跳过"时走到这里）
# ---------------------------------------------------------------------------
log "进入常驻轮询（间隔 ${INTERVAL}s）"
while true; do
    mkdir -p "$BAK" 2>/dev/null
    sweep 0
    sleep "$INTERVAL"
done
