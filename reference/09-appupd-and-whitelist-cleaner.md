# APP_UPD 与「侧载应用被清」的真实机制（YDPX7-1 / PenOS 4.8.7 实测逆向）

> 结论先行：**删你应用的不是 APP_UPD，而是桌面（launcher）里的 `AppWhitelistCleaner`**；
> APP_UPD 只是"事后记录者"。这个区分很重要 —— 之前一直盯着 APP_UPD 找对策，方向是错的。

## 一、APP_UPD 是什么（libfalcon.so 符号表 + 字符串表实测）

APP_UPD 全称 **AppUpdateManager**，是框架的应用**更新器**，不是清理器。

### 1.1 类结构（来自 `libfalcon.so` 未 strip 的符号表）

```
JQuick::Updater::AppUpdateManager
    init()  /  notifyReady() / addReadyCb() / isConnected() / isReady() / clearCache()
    getUpdateInfo(appid...)        ← 查更新
    startDownload(appid...)        ← 下载
    installUpdate(appid...)        ← 安装
    markCacheExpired(appid...)     ← 让缓存过期，强制下次重查
    addGlobalCallback() / registerAppUpdateInfoCallback() / registerAppUpdateInfoReportCallback()
JQuick::Updater::UpdateInfoCache      ← 每个 appid 一份状态
Updater::JSAppUpdateManager           ← JS 绑定（JQFunctionTemplate::SetProtoMethodAsync）
```

**关键：这个类里没有任何 `removePackage` / `uninstall` 方法** —— 它没有删应用的能力。

### 1.2 状态机（每个 appid 一份）

```
markCheckPending ──► markUpToDate | markHasNewVersion(version) | markCheckError | (超时) CheckPending timeout
                                        │
                                        ▼
        markDownloadPending(version) ──► markDownloadPercent(n) ──► markDownloadDone
                                        │            └─► markDownloadPaused(n) / markDownloadError(code)
                                        ▼
        markInstalling ──► markInstallDone(version) | markInstallError
另有：markVersion(v)、markExpired(state)
```

### 1.3 服务端交互

| 项 | 值 |
|---|---|
| 接口 | `POST https://hardware-iot.youdao.com/app/store/version/info/batch`（批量版本检查） |
| 请求/响应字段 | `appId`、`env`、`version`、**`rollingBack`**（日志里叫 `rbCode`） |
| 日志 | `Do checking appid[%s] oldversion(%s)` → `appid %s no updates, rbCode: %d` |
| 缓存 | `UpdateInfo(%s) cache state=%d.` / `result is not expired, return.` |
| 推送通道 | `service_upgrade_push` payload（`params.app` 数组），校验失败会 `Unexpected app update pushed from server, ignored` |
| 未初始化 | `API [getUpdateInfo] called with appid [%s] not initialed, return UP_TO_DATE` |

> **`rbCode: 407` 是正常值** —— 笔自带应用（桌面、设置…）查更新时也是 `rbCode: 407`，
> 说明 407 = "无需回滚"。**它不是删除触发条件**（这点我一开始判断错了）。

### 1.4 它和"删除"的真实关系

它订阅 PMS 事件（`pm::JSPackageManager::packageInstalled / packageUpdated / packageRemoved`）。
当**别人**把包删掉后，PMS 发事件，APP_UPD 打印：

```
[APP_UPD] Package(%s) removed from pms      ← 被动收到通知，不是它删的
[APP_UPD] Package(%s) markExpired, state=%d ← 把它自己的更新缓存标记过期
```

日志顺序也印证：`removed from pms` **在前**，`markExpired` 在后。

## 二、真正清应用的是谁：桌面的 `AppWhitelistCleaner`

### 2.1 载体

```
/userdata/miniapp/data/mini_app/pkg/8080222437664451/b/indexPage-93295cbd.js.bin   ← 桌面首页 bundle
/userdata/miniapp/data/mini_app/pkg/8080222437664451/b/libs/libjsapi_dictpen_home_*.so
/userdata/miniapp/data/mini_app/pkg/8080222437664451/b/libs/libbusiness_dictpen_home_*.so
```

（注意：**桌面装在主根 `/userdata/miniapp/...`**，不在 `/userdisk/secondary/...`。）

### 2.2 算法（原子表 + 实机日志双证据）

原子表里的关键字符串（按顺序）：

```
start cleanUnofficialApps
httpGetOvermindConfig          ← 拉云端配置
appWhitelist / allowedApps     ← 白名单字段
enabled                        ← 开关
appWhitelist is disabled or empty, skip.
allowedApps list is empty, skip to avoid accidental removal.   ← 空名单保护
installed:  whitelist:  removing:  flag:
app_whitelist                  ← 配置 key
remove_unofficial
removePackage                  ← 真正调用 PMS 卸载
done, removed:   clean_done
```

实机日志（本次开机，删了 4 个应用）：

```
09:13:37.509 (console(8080222437664451).warn.0): [AppWhitelistCleaner]
09:13:37.509 (console(8080222437664451).warn.1): start cleanUnofficialApps
09:13:37.510 [YHttpOptions] Http task is queued: method=Get.
             url=https://api-overmind.youdao.com/openapi/get/luna/hardware/dictpen-client/prod/appWhitelist
             timeout=5000
09:13:38.930 (console(8080222437664451)...): httpGetOvermindConfig:configKey=appWhitelist.
             {"data":{"value":{"enabled":true,"allowedApps":["8001649731775833", ...共 116 个...]}},
              "code":0,"msg":"OK"}
09:13:38.936 [AppWhitelistCleaner] installed: 47
09:13:38.936 [AppWhitelistCleaner] whitelist: 116
09:13:38.937 [AppWhitelistCleaner] removing: 8001145141919811  Doge阅读    flag: 16384
09:13:38.937 [AppWhitelistCleaner] removing: 8001771940015915  文件管理器  flag: 16384
09:13:38.937 [AppWhitelistCleaner] removing: 8001782559140681  学习计划    flag: 16384
09:13:38.938 [AppWhitelistCleaner] removing: 8001999000000001  终端        flag: 16384
09:13:38.98x [APP_UPD] Package(...) removed from pms        ← 结果
```

**判定规则**：
1. 取云端白名单 `allowedApps`（116 项）
2. 若 `enabled=false` 或列表为空 → **跳过**（有防误删保护）
3. 否则遍历本地已安装应用：**appid 不在 `allowedApps` 里 → `pm.removePackage(appid)`**
4. 日志里同时打印 `flag`（侧载应用是 **16384**，商店应用是 0/3）

### 2.3 白名单从哪来

| 项 | 值 |
|---|---|
| 来源 | **云端**（有道 overmind 配置中心），不是本地文件 |
| URL | `https://api-overmind.youdao.com/openapi/get/luna/hardware/dictpen-client/prod/appWhitelist` |
| 本地缓存 | **没有落盘文件** —— 全盘只有 JS bundle 和日志里出现 `app_whitelist`，`cache/persistent/` 是空的 |
| 超时 | 5s |

**所以"断网能不能避免"的答案**：取决于这次 fetch 是否失败。
- 若 fetch 失败且此前没有名单 → `allowedApps` 为空 → **跳过**（这就是"有时离线不删"的原因）
- 若 fetch 成功（哪怕笔上其它域名解析失败，这个域名通了就行）→ **照删**

实测：09:13 那次开机，`hardware-iot.youdao.com` 和 `dictpen-appstore.youdao.com` 都 DNS 失败，
但 **`api-overmind.youdao.com` 通了** → 拿到 116 条名单 → 照删 ✗。**所以靠断网不可靠。**

## 三、三个模块的职责边界（一张表说清）

| 模块 | 位置 | 职责 | 会不会删应用 |
|---|---|---|---|
| **APP_UPD**（AppUpdateManager） | `/usr/lib/libfalcon.so` | 查更新、下载、安装；订阅 PMS 事件记录状态 | **不会**（类里没有 remove） |
| **PMS**（PackageManager / JSPackageManager） | `/usr/lib/libfalcon.so` | 安装/卸载/注册表；cert(size+md5) 校验；`remove package(%s) failed, not allowed to remove system app.` | 提供 `removePackage` **被人调用**；自己不做判定 |
| **AppWhitelistCleaner** | 桌面包 `indexPage-*.js.bin` | 开机比对云端白名单，**不在名单就调 `removePackage`** | **就是它** |

PMS 里还有一段开机 import 检查（会删注册表，但本次没触发）：

```
[PMS]import Apps Install start check !!!
[PMS]check import Apps Install deleteFile packges.json: %s !!!
[PMS]check import Apps Install deleteFile preference.json: %s !!!
[PMS]import Apps Install finish check, Have import apps install unfinished, need reInstall !!!
[PMS]import Apps Install finish check !!!
```
（它内嵌了几个硬编码 appid：`8080252464522508`/`8080282263329158`/`8080282888534774`/`8080272425914438`/`8001670042434355`；
本次开机 105µs 内走了 happy path。）

## 四、对策（按彻底程度排序）

### 结论：**方案②（DNS 劫持）已实现并实测有效**，是目前最优解

| 方案 | 做法 | 状态 |
|---|---|---|
| **① 用白名单内的 appid** | 打包时把 appid 换成 `allowedApps` 里**存在但本机没装**的（如 `8001742000000001`） | **实测有效**（白名单内同 flag=16384 的包平安过重启）；风险是该 appid 日后被云端移除或商店要装同 id 会冲突 |
| **② DNS 劫持云端白名单域名** | 把 `api-overmind.youdao.com` 指到 `127.0.0.1` → 拉不到 → 名单空 → cleaner **自己跳过** | **✅ 已实现并实测有效**（`scripts/dns-blackhole.sh`，开机钩子自动挂载） |
| ③ sideload-keeper | 开机钩子重装 + 硬链接数据镜像 | 通用兜底（已实测）；方案②生效后它基本不再被触发 |
| ④ 改 flag | 16384 → 0 | **实测无效**（判定依据是"不在白名单"，flag 只是日志信息） |

**推荐：② + ③**（DNS 劫持根治 + keeper 兜底），需要时再考虑①。

### 4.1 词典笔能不能改 DNS —— 能，三条路

| 途径 | 可行性 | 说明 |
|---|---|---|
| **覆盖挂载 `/etc/hosts`** | ✅ **最好用** | `/etc/hosts` 是普通文件（42B），`/` 虽为 erofs 只读，但 **`mount --bind` 一个可写文件覆盖它完全可行**（实测通过）。自定义 hosts 放 `/userdisk`（ext4 持久化），开机钩子重新挂载即可长期生效 |
| 改 `/etc/resolv.conf` | ⚠ 可行但不推荐 | 它其实是 **软链 → `/tmp/resolv.conf`**（tmpfs，可写），内容是 dhcpcd 从 wlan0 DHCP 租约生成的（`nameserver 192.168.137.1` + 阿里/谷歌公共 DNS）。**dhcpcd 会在租约变化时重写它** → 手工改不持久、且会影响所有域名的解析 |
| 静态 DNS / dnsmasq 配置 | ✗ 不可行 | 笔上跑着 `dnsmasq`（pid 1172，无参数启动，读只读的 `/etc/dnsmasq.conf`）与 `wpa_supplicant -c/userdata/cfg/wpa_supplicant.conf`；dnsmasq 配置在只读区，改不了 |

### 4.2 用法（`scripts/dns-blackhole.sh`）

```sh
sh /userdisk/skip_re/dns-blackhole.sh apply     # 写 hosts + 挂载（可重复执行）
sh /userdisk/skip_re/dns-blackhole.sh status    # 看挂载状态与实际解析
sh /userdisk/skip_re/dns-blackhole.sh remove    # 卸载并停用（恢复正常 DNS）
```

- 域名清单：`/userdisk/skip_re/dns-blackhole.conf`（默认只有 `api-overmind.youdao.com` 一条）
- 自定义 hosts：`/userdisk/skip_re/hosts-custom`
- 持久化：`sideload-keeper.sh` 启动时（开机钩子，早于 cleaner 的 ~35s）自动 `apply`

**实测证据（重启后）**：

```
09:37:04  keeper: 已应用 DNS 劫持：api-overmind.youdao.com
09:37:32  [AppWhitelistCleaner] start cleanUnofficialApps
09:37:32  Http task queued: GET https://api-overmind.youdao.com/.../appWhitelist
09:37:32  [YHttpManager] request failed
09:37:32  fetch overmind appWhitelist failed, code: undefined      ← 拉不到 → 名单空 → 跳过
（此后没有任何 removing: / done, removed:）
本次开机 removing: 次数 = 0      ← 之前每次开机 3~4 次
```

**精准度验证**（只掐一个域名，不影响商店/更新）：

```
api-overmind.youdao.com     → 127.0.0.1        （劫持）
dictpen-appstore.youdao.com → 117.135.207.132  （正常）
hardware-iot.youdao.com     → 117.135.207.132  （正常）
```

### 4.3 代价与风险

- 所有 **overmind 云端配置**都拉不到了（不只是白名单）→ 相关功能回落默认值。
  目前已知受影响的就是 cleaner 本身；如果以后发现某个功能依赖 overmind，把该域名从
  `dns-blackhole.conf` 里去掉、改用方案①即可。
- 挂载是**内存态**（重启消失），由开机钩子重新挂载；hook 未生效时会退回 keeper 兜底。

### 4.4 组合策略：DNS 劫持 + keeper 自愈（当前线上配置）

官方随时可能改验证域名/链接，所以 **keeper 不能删，但也不该常驻**。实测 cleaner
**每次开机只跑一次**，于是 keeper 设计成：

```
阶段 0  DNS 劫持（早于 cleaner 的 ~35s）
阶段 1  等框架就绪
阶段 2  盯本次开机的框架日志，判定 cleaner 这次干了什么：
        · 出现 removing:            → 官方改链接了/劫持失效 → 修复(重装+恢复data) → 转入常驻轮询
        · 出现 fetch overmind appWhitelist failed / allowedApps list is empty, skip
                                    → 本次开机安全 → 做最后一次数据镜像 →
                                      把镜像任务交给 cron（每 5 分钟）→ **keeper 自行退出**
        · 超时没等到判定            → 保守转入常驻轮询
```

实测（真机重启）：

```
09:51:25  DNS 劫持已应用：api-overmind.youdao.com
09:51:57  cleaner 已跳过（白名单拉不到/为空）→ 做最终镜像后退出
09:51:57  已把镜像任务交给 cron（每 5 分钟）
09:51:57  ===== keeper 正常退出（本次开机无需守护）=====
          → 进程不在 ✔   removing: 次数 = 0 ✔   三个应用健在 ✔
```

**为什么退出前要装 cron**：keeper 退出后如果不再刷新数据镜像，下次开机万一劫持失效、
应用被删时，恢复的会是"退出那一刻"的旧数据。交给 cron（每 5 分钟一次，不占进程）后，
镜像始终新鲜，且屏幕上不会有任何常驻脚本。cron 不可用时会自动退回常驻轮询（保守）。

## 五、复现与取证方法（可复用）

```powershell
# 1) 拉框架 native 层与桌面 bundle
adb pull /usr/lib/libfalcon.so out\appupd\libfalcon.so                 # 10.5MB，未 strip
adb pull /userdata/miniapp/data/mini_app/pkg/8080222437664451/b/indexPage-93295cbd.js.bin out\appupd\

# 2) 字符串/符号/原子 三件套（本 skill scripts\ 下）
python scripts\elf_strings.py out\appupd\libfalcon.so --grep "APP_UPD" --max 80     # 59 条日志串
python scripts\elf_syms.py    out\appupd\libfalcon.so --grep "AppUpdater|N2pm" --raw
python scripts\jsbin_atoms.py out\appupd\indexPage.js.bin --grep "whitelist|clean" --context 3

# 3) 实机日志（最关键）
adb shell "grep -a 'AppWhitelistCleaner\|cleanUnofficialApps\|allowedApps\|removing:' /data/applog/DictPen_*.log | tail -40"
```

要点：
- `libfalcon.so` **没有 strip**，符号名直接暴露类/方法结构，比啃反汇编快得多
- `.js.bin` 的**原子表是明文**，容器格式 `[0x01][varint 数][varint(len<<1|wide)+bytes][字节码]`，
  抽出来就能看到函数名和日志字符串，等于半个源码
- 桌面 bundle 的 `console.warn` 会带 appid 前缀（`(console(8080222437664451).warn.N)`），
  能直接定位是哪个应用打的日志

## 六、相关文件

- `scripts/elf_strings.py` —— 抽 ELF 字符串并按主题/邻近聚类
- `scripts/elf_syms.py` —— 解析符号表（未 strip 时的神器）
- `scripts/elf_range.py` —— 导出某个偏移区间的全部字符串（看一个模块的完整字符串表）
- `scripts/jsbin_atoms.py` —— 抽 `.js.bin` 的原子表
- `scripts/sideload-keeper.sh` / `deploy_keeper.py` —— 兜底方案
- `reference/08-sideload-persistence.md` —— 数据位置与 keeper 细节
