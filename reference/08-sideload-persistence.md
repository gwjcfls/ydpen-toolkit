# 侧载应用与数据持久化（YDPX7-1 / PenOS 4.8.7 实测）

## 一、现象

侧载（自己 `miniapp_cli install` 的）应用**重启后会消失**：
开机瞬间桌面还有，过一小会（约 40~70 秒）就没了。

## 二、成因（实测）

> ⚠ **本节的早期结论已修正**：真正执行删除的**不是 APP_UPD**，而是**桌面（launcher）里的
> `AppWhitelistCleaner`**（它开机拉云端白名单，不在名单里的应用直接 `pm.removePackage`）。
> APP_UPD 只订阅 PMS 的 `packageRemoved` 事件做记录（`[APP_UPD] Package(%s) removed from pms`）。
> 完整逆向过程与三个模块的职责边界见 **[09-appupd-and-whitelist-cleaner.md](09-appupd-and-whitelist-cleaner.md)**。
> 下面关于"数据位置 / 数据一起被删 / keeper 机制"的内容依然有效。

固件里有应用更新器 **APP_UPD**（它是记录者，不是执行者），而桌面的 `AppWhitelistCleaner`
会按**云端白名单**清理"非官方应用"。被清理时是**连包带数据一起删**：

```
[APP_UPD] Package(8001999000000001) removed from pms     ← 包目录 + 注册表条目一起消失
[APP_UPD] Package(8001771940015915) markExpired, state=1
```

关键点：**应用数据就放在包目录里面**：

```
/userdisk/secondary/miniapp/data/mini_app/pkg/<appid>/a/      ← 代码
/userdisk/secondary/miniapp/data/mini_app/pkg/<appid>/data/   ← 应用数据（数据库/下载/草稿/配置）
```

这不是侧载应用的特例——**官方应用也这么存**：

| 应用 | 数据位置 | 内容 |
|---|---|---|
| 网易云音乐 | `.../8001649731775833/data/` | `database/cloudmusic.db`、下载的歌曲 `.lrc`/封面 |
| 有道云笔记 | `.../8001656491465980/data/` | `draft/`、`sharedpreferences/`、`weixinobU.../` |

所以 `removed from pms` 一发生，**`data/` 一起被删** → 应用的登录态、词书、下载内容、配置全丢。
（实测：在 `data/` 里放标记文件，重启被删包后标记消失。）

另外两个次要现象：
- 侧载应用的注册表条目 `flag = 16384`（商店应用是 `0` 或 `3`），可作为「侧载」的判据
- 注册表条目丢了但包还在时，桌面同样不显示（日志 `pm name=<名字> type=removed`），重新
  `install` 即可恢复（`type=installed`）

## 三、为什么不能直接改 APP_UPD

- `/` 是 **erofs 只读**（`/etc/hosts` 都写不进去：`Read-only file system`），`app`/`mgr` 都在只读区
- 机型分区有 A/B 槽 + 校验，直接改要重刷固件（成本高、有风险）

## 四、对策：`sideload-keeper`（开机钩子 + 数据镜像）

固件自己留了一个可写区的开机钩子：

```sh
# /etc/init.d/S99_run_test_scripts
[ -f /userdisk/skip_re/skip_login.sh ] || exit 0
/userdisk/skip_re/skip_login.sh &
```

`/userdisk` 是 ext4 可写且持久化 —— 正好可以放我们的守护脚本。

### 4.1 守护做什么

```
① 应用 DNS 劫持（阶段 0，早于 cleaner 的 ~35s）—— 首选防线，让 cleaner 拉不到白名单
② 等框架就绪 → 盯本次开机日志判定 cleaner 干了什么：
   ├ 出现 removing:  → 官方改了验证链接/劫持失效 → 修复：
   │      先把残留 data/ 并进镜像 → rm 残包 → miniapp_cli install → 镜像 cp -a 回 data/
   │      然后转入常驻轮询（每 30s 复查）
   ├ 确认跳过（拉不到白名单/名单为空）→ 本次开机安全：
   │      做最后一次数据镜像 → 把镜像任务交给 cron（每 5 分钟）→ **keeper 自行退出**
   └ 超时没等到判定 → 保守转入常驻轮询
③ 数据镜像用 `cp -al` 硬链接：几乎不占空间；APP_UPD/cleaner 用 unlink 删原文件后，
   镜像里的 inode 依然存活（等于免费快照）
```

**为什么是"自愈 + 自退出"**：cleaner 每次开机只跑一次（实测），确认它跳过之后就没有可守的了，
常驻只是浪费；但官方随时可能改验证链接，所以 keeper 必须保留。两者结合 = 平时不占进程、
出事立刻修复。镜像交给 cron 后依然保持新鲜，不牺牲数据安全。

### 4.2 部署

```powershell
# 一键（推荐）
python tools\deploy_keeper.py                          # 终端 + 文件管理器
python tools\deploy_keeper.py --amr 你的包.amr          # 指定包
python tools\deploy_keeper.py --restart-only           # 只重启 keeper
```

**保活的应用数量不限** —— keeper 按 `/userdisk/skip_re/amr/*.amr` 遍历，
丢一个新 amr 进去（appid 自动从包内 `manifest.json` 解析）就多保护一个，
不用改脚本。实测同时保护 3 个（终端 / 文件管理器 / PStore）正常工作。

手动等价操作：

```sh
adb push sideload-keeper.sh /userdisk/skip_re/skip_login.sh
adb shell "chmod +x /userdisk/skip_re/skip_login.sh"
adb push PenTerm-9.2.0.amr    /userdisk/skip_re/amr/PenTerm.amr
adb push FileManager-1.2.0.amr /userdisk/skip_re/amr/FileManager.amr
# appid 会从 amr 内的 manifest.json 自动读，也可以手写 <名字>.appid（必须纯数字）
```

日志：`/userdisk/skip_re/sideload-keeper.log`（自带轮转，超 300KB 截断）。
要停：`kill $(cat /tmp/sideload-keeper.pid)`。

**上线确认（三处一致 + 链路通）**：

```sh
md5sum /userdisk/skip_re/skip_login.sh          # 应与本地 tools\sideload-keeper.sh 相同
grep -n skip_login /etc/init.d/S99_run_test_scripts   # 固件钩子指向它
ps | grep skip_login | grep -v grep             # 进程在跑
tail -f /userdisk/skip_re/sideload-keeper.log   # 每 30s 出现「镜像 xxx 完成」
```

### 4.3 注意

- **别用 `pkill -f skip_login.sh` 停它**——脚本名会匹配到 `sh` 自己的命令行，把调用方一起杀掉
  （表现为 `device offline`）。用 pid 文件。
- 硬链接镜像只能防「包目录被一起删」；如果 APP_UPD 改成保留 `data/`，镜像也无害。
- 恢复用 `cp -a`（不是硬链接），避免新包的 `data/` 与原镜像纠缠。
- shell 用 `kill -0` 做单实例判定；`.appid` 必须是**纯数字**（见第六节的 bug）。

## 五、实机证据

```
# keeper 日志
22:52:39 ===== keeper 启动 pid=1201 =====
22:54:09 修复 FileManager (appid=8001771940015915)：包目录=0 注册表=0
22:54:10   install → {"appid": "8001771940015915", "ret": 0}
22:54:30 修复 PenTerm (appid=8001999000000001)：包目录=0 注册表=0
22:54:30   install → {"appid": "8001999000000001", "ret": 0}

# 镜像建立
/userdisk/skip_re/data-backup/8001999000000001/userdata-marker.txt
/userdisk/skip_re/data-backup/8001771940015915/fm-marker.txt
```

## 六、离线自测（不必反复重启笔）

shell 脚本改完就上笔重启试错代价太高。**用 busybox-w32 在 PC 上离线跑完整流程**：

```powershell
# 一次性准备
Invoke-WebRequest https://frippery.org/files/busybox/busybox64.exe -OutFile tools\busybox\busybox.exe
$env:PYTHONIOENCODING='utf-8'
python scripts\keeper_selftest.py       # 造夹具 + 跑 keeper + 断言
```

keeper 脚本的路径**全部可用环境变量覆盖**（默认值是笔上的真路径），这就是离线可测的原因：

```sh
AMRDIR=${KEEPER_AMRDIR:-/userdisk/skip_re/amr}
APPDIR=${KEEPER_APPDIR:-/userdisk/secondary/miniapp/data/mini_app/pkg}
MINIAPP=${KEEPER_MINIAPP:-miniapp_cli}      # 测试时换成假 CLI
WAIT_READY=${KEEPER_WAIT_READY:-1}          # 测试时设 0，跳过等待
WAIT_UPD=${KEEPER_WAIT_UPD:-90}             # 测试时设 0
ONESHOT=${KEEPER_ONESHOT:-0}                # 测试时设 1，跑一轮就退出
```

自测输出（真 zip amr + 假包目录 + 假 `miniapp_cli`）：

```
===== 第 1 轮：应用健在 + .appid 写坏 → 应自解析 appid 并建立镜像 =====
  PenTerm 的 appid 由 amr 解析得到：8001999000000001
  镜像 PenTerm/8001999000000001 完成（16KB）
  镜像 big.txt 与源逐字节一致: ✓

===== 模拟 APP_UPD：连包带数据一起删 =====
  包目录: 已删
  镜像仍在（硬链接 inode 存活）: ✓        ← 关键：unlink 后镜像 inode 依然有效
  镜像里的数据: PRECIOUS-DATA

===== 第 2 轮：应用已被删 → 应装回并恢复 data/ =====
  修复 PenTerm (appid=8001999000000001)：包目录=0 注册表=0
  install → {"appid": "8001999000000001", "ret": 0}
  已恢复 data/（16KB）
  user.txt 恢复: ✓ PRECIOUS-DATA
  big.txt（300 行）逐字节一致: ✓
  注册表已重登记: ✓
=== 结论：全部通过 ✓✓✓ ===
```

**离线自测抓到的一个真 bug**：`.appid` 文件若被写坏（例如整段 JSON 塞进去），
老版脚本会把它当 appid 用（日志里出现 `appid={"appid":"8001999..."}`），
导致目录判断全错。修法：只接受**纯数字**的 appid，不合格就回退到从 amr 解析：

```sh
case "$appid" in
    ''|*[!0-9]*) appid="" ;;      # 非纯数字 → 丢弃，改从 amr 解析
esac
```

## 七、相关文件

- `scripts/sideload-keeper.sh` —— 守护脚本（部署到 `/userdisk/skip_re/skip_login.sh`）
- `scripts/deploy_keeper.py` —— 一键部署/重启/查状态
- `scripts/keeper_selftest.py` —— 离线自测（busybox，不需要笔）
- `scripts/pen_registry.py` —— 注册表巡检/备份/补条目/`flag` 归一化
