#!/bin/sh
# dns-blackhole.sh —— 用 bind mount 劫持指定域名的 DNS（笔上 / 是只读 erofs，但可以覆盖挂载）
#
# 用途：让应用商店的 AppWhitelistCleaner 拉不到云端白名单
#       （拉不到 → allowedApps 为空 → cleaner 自己跳过，不会清理侧载应用）
#
# 原理：
#   /etc/hosts 是普通文件 → mount --bind 一个我们写的 hosts 覆盖它
#   自定义 hosts 放 /userdisk（ext4 持久化）→ 重启后由开机钩子重新挂载
#
# 用法（笔上）：
#   sh dns-blackhole.sh apply     # 写入并挂载（可重复执行）
#   sh dns-blackhole.sh status    # 看当前状态
#   sh dns-blackhole.sh remove    # 卸载并停用（恢复正常 DNS）
#
# 环境变量（便于自测）：
#   BH_HOSTS   自定义 hosts 路径（默认 /userdisk/skip_re/hosts-custom）
#   BH_CONF    域名清单（默认 /userdisk/skip_re/dns-blackhole.conf）
#   BH_TARGET  被覆盖的目标（默认 /etc/hosts）

BH_DIR=${BH_DIR:-/userdisk/skip_re}
BH_HOSTS=${BH_HOSTS:-$BH_DIR/hosts-custom}
BH_CONF=${BH_CONF:-$BH_DIR/dns-blackhole.conf}
BH_TARGET=${BH_TARGET:-/etc/hosts}
BH_LOG=${BH_LOG:-$BH_DIR/dns-blackhole.log}

log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$BH_LOG"; }

# 默认黑名单：只掐"云端白名单"这一个，尽量不影响别的功能
if [ ! -f "$BH_CONF" ]; then
    mkdir -p "$BH_DIR"
    cat > "$BH_CONF" <<'EOF'
# 每行一个要劫持到 127.0.0.1 的域名（# 开头是注释）
# AppWhitelistCleaner 的白名单来源：拉不到 → 名单为空 → 它自己跳过，不会清理侧载应用
api-overmind.youdao.com
EOF
fi

build_hosts() {
    mkdir -p "$BH_DIR"
    cat > "$BH_HOSTS" <<'EOF'
127.0.0.1	localhost
127.0.1.1	Orange-3562
::1		localhost
EOF
    grep -v '^[[:space:]]*#' "$BH_CONF" 2>/dev/null | grep -v '^[[:space:]]*$' | while read -r d; do
        echo "127.0.0.1	$d"
        echo "::1		$d"
    done >> "$BH_HOSTS"
}

is_mounted() {
    grep -q " $BH_TARGET " /proc/mounts 2>/dev/null
}

do_apply() {
    build_hosts
    if is_mounted; then
        umount "$BH_TARGET" 2>/dev/null
    fi
    if mount --bind "$BH_HOSTS" "$BH_TARGET" 2>/dev/null; then
        log "apply 成功：$(grep -c '127.0.0.1' "$BH_HOSTS") 条劫持 → $BH_TARGET"
        echo "[✓] 已劫持以下域名到 127.0.0.1："
        grep '127.0.0.1' "$BH_HOSTS" | awk '{print "      " $2}' | grep -v localhost
        return 0
    fi
    log "apply 失败（bind mount 被拒）"
    echo "[!] bind mount 失败 —— 可能内核/权限限制"
    return 1
}

do_remove() {
    if is_mounted; then
        umount "$BH_TARGET" 2>/dev/null && log "remove：已卸载" && echo "[✓] 已卸载，DNS 恢复正常"
    else
        echo "[i] 当前没有挂载"
    fi
    # 停用清单（保留文件但清空域名，避免下次开机又挂上）
    mkdir -p "$BH_DIR"
    cat > "$BH_CONF" <<'EOF'
# 已停用（dns-blackhole.sh remove）
EOF
}

do_status() {
    echo "== dns-blackhole 状态 =="
    echo "  自定义 hosts: $BH_HOSTS"
    echo "  域名清单    : $BH_CONF"
    echo "  覆盖目标    : $BH_TARGET"
    if is_mounted; then
        echo "  挂载状态    : ✔ 已挂载（劫持生效）"
        cat "$BH_TARGET" | sed 's/^/      /'
    else
        echo "  挂载状态    : ✗ 未挂载（DNS 正常）"
        cat "$BH_TARGET" 2>/dev/null | sed 's/^/      /'
    fi
    echo "  --- 实际解析 ---"
    for d in $(grep -v '^[[:space:]]*#' "$BH_CONF" 2>/dev/null | grep -v '^[[:space:]]*$'); do
        # PING <host> (<ip>) ... —— 用括号做分隔取第 2 段（贪婪匹配会取到 56(84) 里的 84）
        r=$(ping -c1 -W2 "$d" 2>/dev/null | head -1 | awk -F'[()]' '{print $2}')
        echo "      $d → ${r:-解析失败}"
    done
}

case "$1" in
    apply)  do_apply ;;
    remove) do_remove ;;
    status|"") do_status ;;
    *) echo "用法: $0 {apply|remove|status}"; exit 2 ;;
esac
