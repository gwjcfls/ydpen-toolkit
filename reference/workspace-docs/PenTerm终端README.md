# PenTerm —— 有道词典笔终端 miniapp（YDPX7-1 / X6 通用）

一个**真 VT 终端**：本地 PTY shell + SSH 客户端 + 一键在笔上起 sshd。
界面按 960×266 适配，终端画面是插件端**逐字符网格渲染成 PNG**后用 `<image>` 显示。

![界面](../../out/term/final.png)

```
┌ PenTerm │ 本地 shell · pid 2521    本地 SSH 起sshd 清屏 断开 自检 ┐
│  770   735 root  S  760m  76.0  1  0.0 /usr/bin/...            │  ← 真 top 输出，列完美对齐
│ 1457  1070 root  S  152m  15.2  2  0.0 /usr/bin/bt-manager 1   │
│ /userdisk # ▏                                                  │  ← 提示符 + 光标块
├ > [输入框]                     ★ 历史 常用 键盘 发送             ┤
├ Enter Tab ^C Up Dn Lf Rt ^D ^Z Esc PgUp PgDn                   ┤
```

## 命令历史 / 常用命令（9.3.1 起）

底部四个入口：**★ 收藏 · 历史 · 常用 · 键盘 · 发送**

| 面板 | 能力 |
|---|---|
| **历史** | 列出**全部**历史命令（最新在前、7 条/页翻页）。**点命令 → 灌进原生键盘**（可改完再执行）；**点 ▶ → 直接重跑**；「清空」清空历史 |
| **常用** | 收藏的常用命令；点命令=载入键盘，▶=执行，**x=删除**（钉在行最右侧，与 ▶ 隔开整行，防误触）；「收藏当前」把当前输入/上一条加进来 |

另外两条交互约定（9.3.3 起）：

- **点终端画面不再呼出键盘** —— 画面区只负责上下滑动翻看；要输入请点**下方输入框**。
- **点快捷键条的 Up / Dn 后，会把 shell 命令行里正在编辑的内容（去掉提示符）灌进输入框** ——
  点 Up 召回上一条后可以直接接着改。实现：插件 `term.vtCursorLine(sid)` 返回光标行文本，
  页面用 `/[#$>]\s+([\s\S]*)$/` 切掉提示符（日志：`pullShellLine: "/userdisk # ls -la …" → "ls -la …"`），然后**立刻用 Ctrl-A + Ctrl-K 把 shell 那一行清空** —— 否则按 Up 后 shell 行里已有同一条命令，点「发送」会被追加成 `toptop`（命令执行两遍）。全屏程序（vi/less）里跳过清行。

数据落在 **Favorite 下的新目录**（插电脑 MTP 就能看到、能备份）：

```
/userdisk/Favorite/PenTerm/history.json     命令历史（最多 300 条）
/userdisk/Favorite/PenTerm/favorites.json   常用命令
```

两者都是纯 JSON 数组，例如 `["df -h","top -b -n1 | head -12"]`。
写盘用「临时文件 + rename」，掉电不会写坏；写历史/收藏走插件新增的
`term.storeDir/storeLoad/storeSave` 三个 API（目录自动创建）。

### 一键构建

```powershell
python tools\build_terminal.py --version 9.4.0 --install
```

编译插件 → 笔上编译页面（jsfmc）→ 打包 → 安装 + 同步 keeper 保活包。
（注意 jsfmc 需要 `LD_LIBRARY_PATH=/oem/YoudaoDictPen/output/libs:...`，脚本已内置。）

## 为什么终端画面不走框架文字渲染

实测框架只有比例字体（`i`×61 = 241px、`M`×61 = 790px、`0`×61 = 547px），**列对不齐**，
跑 `vim`/`top` 会全乱（官方「文件管理器」的终端只是"日志式"文本，不需要对齐）。

所以本方案在**原生插件里做 VT 仿真 + 位图渲染**：

1. PTY 输出 → 解析 VT/ANSI → 写进 `cols×rows` 的字符网格（每格记住字符 + 前景/背景色 + 属性）
2. 用 8×16 点阵字体把网格画成 RGB 位图（自带 zlib deflate 编码成 PNG）
3. 页面用 `<image src="file://...">` 显示，每帧写一个**新文件名**（框架按 URL 缓存图片）

字体来自笔上自带的 `cmtt10.ttf`（Computer Modern Typewriter，OFL 许可的真等宽字体，
在 `/etc/miniapp/resources/latex/res/fonts/latin/optional/`），
用 `tools/mkfont.py` 在 PC 侧光栅化成点阵表（ASCII 95 字形 + 制表符/方块 30 字形）。

## 架构

| 层 | 文件 | 说明 |
|---|---|---|
| 原生插件 | `src/term.c` | PTY 会话、SSH 客户端、execSync、sshd 守护化、jsapi 注册 |
| VT + 渲染 | `src/term_vt.c` + `src/font8x16.h` + `src/zlib_min.h` | VT/ANSI 解析、网格、PNG 编码（被 term.c include） |
| 页面 | `src/terminal.js` → `index.js.bin` / `shell.js.bin` | UI：状态栏 + `<image>` 终端 + 输入行 + 快速键 + SSH 面板 |
| 入口 | `app.js.bin`（引导） | 见文末"入口契约"；目前用官方 app 的 entry 作引导 |
| 编译 | `tools/build/jsfmc`（笔上跑） | 用笔自己的 libquickjs 把 JS 编成 `.js.bin` |
| 打包 | `tools/pack_amr.py` | manifest + cert + zip，多 ABI 塞插件 |
| 字体 | `tools/mkfont.py` | TTF → 8×16 点阵 C 头文件 |

## 插件 API（`import term from 'term'`）

```js
// 会话
term.spawnShell(cwd)                        // /bin/sh -i（PTY）        -> {ok,sid,pid}
term.spawn(cmd, {cols,rows,autopassword})   // sh -c 任意命令（PTY）
term.spawnSsh(host,user,port,pw,extra)      // /bin/ssh -tt（PTY，可自动填密码）
term.write(sid, data) / term.read(sid,max) / term.alive(sid) / term.kill(sid)
term.resize(sid,cols,rows) / term.sessions()
term.execSync(cmd[, timeoutMs])             // -> {code, out}
// VT 屏幕
term.vtStart(sid, cols, rows, dir)          // 建 VT，dir 默认 /userdisk/term
term.vtFeed(sid)                            // 把 PTY 新输出喂进 VT -> {fed,dirty}
term.vtRender(sid)                          // 脏了才重绘 -> {ok,path,frame,cols,rows}
term.vtText(sid)                            // 屏幕纯文本（调试）
term.vtResize(sid,cols,rows) / term.vtInfo(sid) / term.vtWrite(sid,text)
// sshd（让别人 ssh 进笔）
term.sshdStart(port, pubkey) / term.sshdStop() / term.sshdStatus()
term.info()                                 // {uid,hostname,tools[],sessions}
```

VT 支持：C0(BS/HT/LF/CR)、`CSI A B C D E F G H f J K L M P @ S T X d m h l r s u`、
SGR(0/1/4/7/22/24/27/30-37/39/40-47/49/90-97/100-107/38;5;n/48;5;n)、
DEC 存光标、`?25` 光标、`?7` 自动换行、`?47/?1047/?1049` 备用屏（vi/vim/less 需要）、OSC 忽略、UTF-8 解码。

## 构建

```powershell
$root='C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb'
$adb="$root\tools\platform-tools\adb.exe"; $s='<笔IP>:5555'
$qj="C:\Users\Sever\.dsh\skills\ydpen-toolkit\assets\quickjs"

# 0) 字体点阵（只需一次；cmtt10.ttf 从笔上拉）
& $adb pull /etc/miniapp/resources/latex/res/fonts/latin/optional/cmtt10.ttf out\term\cmtt10.ttf
python tools\mkfont.py out\term\cmtt10.ttf miniapps\terminal\src\font8x16.h 15

# 1) 插件（zlib 也从笔上取，避免依赖 zlib-dev）
& $adb pull /usr/lib/libz.so.1 out\x7\libz.so.1
& "$root\tools\build\zig\zig.exe" cc -target aarch64-linux-gnu.2.29 -O2 -fPIC -shared `
  -fvisibility=hidden -I $qj miniapps\terminal\src\term.c out\x7\libquickjs.so out\x7\libz.so.1 `
  -o tools\build\libjsapi_term.so -lpthread -lutil
#    ⚠ custom_init_jsapis / custom_init_jsmodules 必须加 __attribute__((visibility("default")))

# 2) 页面
& $adb push miniapps\terminal\src\terminal.js /tmp/terminal.js
& $adb shell "/tmp/jsfmc -o /tmp/index.js.bin -n index.js /tmp/terminal.js; ^
              /tmp/jsfmc -o /tmp/shell.js.bin -n shell.js /tmp/terminal.js"
& $adb pull /tmp/index.js.bin miniapps\terminal\build\index.js.bin
& $adb pull /tmp/shell.js.bin miniapps\terminal\build\shell.js.bin

# 3) 打包 + 安装
python tools\pack_amr.py --src miniapps\terminal\build --out miniapps\dist\terminal-3.2.0.amr `
  --appid 8001999000000001 --name "终端" --version 3.2.0 `
  --lib "arm64=tools\build\libjsapi_term.so" --lib "arm64-orange=tools\build\libjsapi_term.so"
& $adb push miniapps\dist\terminal-3.2.0.amr /tmp/t.amr
& $adb shell "miniapp_cli uninstall 8001999000000001; miniapp_cli install /tmp/t.amr; sleep 3; miniapp_cli start 8001999000000001 index"
```

## 用法

- **本地 shell**：打开即 `/userdisk` 下的 `sh -i`。
- **输命令**：输入框打字 → 「发送」（等价回车）。**回车必须发 `\n`**（见下方坑）。
- **快速键**：Enter/Tab/^C/方向键/^D/^Z/Esc/PgUp/PgDn（写终端控制字符）。
- **自检**：右上角按钮，跑 `id` + `uname -a` + `top`，用来验证画面链路与对齐。
- **SSH**：点「SSH」填主机/用户/端口/密码（或密钥路径，如 `/userdisk/ssh/id_ed25519`）→ 连接。
- **从电脑 ssh 进笔**：点「起sshd」（默认 :2222）。
  ```powershell
  & $adb shell "ssh-keygen -q -t ed25519 -N '' -f /userdisk/ssh/id_pc"
  & $adb pull /userdisk/ssh/id_pc out\id_pc
  & $adb shell "cat /userdisk/ssh/id_pc.pub >> /userdisk/ssh/authorized_keys"
  ssh -i out\id_pc -p 2222 root@<笔IP>
  ```
  （`/` 是 erofs 只读，所以不能用 `/root/.ssh`；`AuthorizedKeysFile` 指到 `/userdisk`。）

## 踩坑记录（都很费时间，记下来）

1. **`\r` 不是回车**：PTY 的 termios 继承自框架进程（其 stdin 不是 tty），ICRNL 不可靠，
   而 busybox ash 的行编辑器把 `\r` 当光标控制 → 命令**只回显不执行**。
   → 插件里显式 `tcsetattr`（`ICRNL|IXON` / `OPOST|ONLCR` / `ICANON|ECHO|ISIG`），页面统一发 `\n`。
2. **框架 `<image>` 按 URL 缓存**：同名文件内容变了不会重载。
   → 每帧写**新文件名** `f<会话pid>_<序号>.png`，页面 src 变化才会重新加载。
3. **本地图片必须带 `file://`**：否则 `WXImage::onLoad failed, src=/userdisk/...`。
4. **插件符号必须显式导出**：`-fvisibility=hidden` 会把 `custom_init_jsapis`/`custom_init_jsmodules`
   一起藏掉，症状是插件被 dlopen 了但 `import term from 'term'` 报
   `could not load module filename 'term'`（框架退回 `/etc/miniapp/.../js_modules/term.js` 找）。
5. **zlib 头文件缺失**：zig 的 sysroot 没有 `zlib.h`，用 `src/zlib_min.h` 自己声明，
   链接笔上的 `libz.so.1`。
6. **图片不能太大**：插件每帧渲染 960×176 RGB + Up 滤波 + zlib level 3，一帧 ~3-10KB。
7. **一个进程里可能有多个页面实例**（install 自动启动 + 手动 start），
   各自 spawn 一个 shell、各自写帧文件；帧名带会话 pid 后互不干扰，
   页面 `killOld()` 也会清掉旧会话。

## 入口契约（当前状态与结论）

框架对 `app.js.bin` 有隐式契约，**这些在用户源码里看不到 —— 它们是 aiot-vue-cli 打包时注入的胶水**
（PenBili 的 `src/app.js` 只有 `class App extends $falcon.App` + `export default App`，
但它的 `app.js.bin` 里却有 `__AppClazz/__loadModuleDefault/__KEYFRAMES/__pages` 这些 atom）。

已实测确认的 5 条：

| # | 要求 | 缺了会怎样 |
|---|---|---|
| 1 | `$falcon.__AppClazz = App` | `TypeError: not a function`（launcher） |
| 2 | `App.meta = {name,version,isSingleJsBundle,pages,options}`，且 **`pages` 的值是不带扩展名的模块基名**（`{index:'index'}`） | `TypeError: cannot convert to object`；写成 `'./index.js'` 会去找不存在的 `xxxPage-<hash>.js` |
| 3 | `$falcon.__loadModuleDefault = m => (m && m.default !== undefined) ? m.default : m` | `loadPage: TypeError: not a function` |
| 4 | `$falcon.__KEYFRAMES = {}` | 动画隐患（官方 bundle 都会设） |
| 5 | `onLaunch 里 $falcon.useDefaultBasePageClass(BasePage)` | 页面生命周期缺失 |

**页面文件**：就是一个 Vue options 对象（`export default {name,data,methods,render(h)}`），
**页面生命周期钩子必须写进 `methods`**（框架基类用 `this.$root.onShow()` 转发；
写成顶层 `onShow` 时 `this` 是"页面实例"而非 Vue 实例，`this.someMethod()` → `not a function`，页面 1 秒后被销毁）。

**还没对穿的最后一环**：自研 entry 下页面生命周期会跑（`BasePage.onLoad`/`beforeVueInstantiate`/`onShow` 都触发、
`$root` 是 object），但**页面组件不 mount**（`render()` 不被调用）。
官方 entry 下同一份页面文件能正常渲染，所以差异在 entry 提供的胶水里；
`$falcon.Page.prototype` 在两种 entry 下内容不同（官方 entry 下多了 `beforeVueInstantiate/sleep`、
少了 `setRootComponent`），说明胶水还动了 Page 类。
下一步建议：用 `jsfmc -d` 把官方 `app.js.bin` 在打了框架桩的环境里执行并 dump 它对 `$falcon`/`globalThis` 的所有写入。

**因此当前发布包用官方 app 的 `app.js.bin` 作引导 entry**（页面名 `index` / `shell` 都是我的页面），
功能完全可用；自研 entry 打通后即可替换。

## 待办

- 打通自研 entry（见上）。
- VT 补齐：`ESC[?...h/l` 更多模式、双宽字符（CJK 目前画占位框）、鼠标上报、滚动回看（scrollback）。
- 手势滚动：滑动终端图片时把方向键/PgUp/PgDn 写进 PTY。
- SSH 面板输入偶发需要点两次才聚焦。
- **帧刷新与图片加载的速率匹配**：目前 220ms 一帧，图片是异步加载的，
  截图/屏幕上偶尔会看到比 VT 实际内容落后一两帧。可把间隔放宽到 300~400ms，
  或改为"上一帧加载完成后才发下一帧"（`<image>` 的 `load` 事件）。
- **子进程回收**：`js_kill`/会话结束时不 `waitpid`，长时间使用会留下 zombie `sh -i`
  （`ps` 里能看到、占 0 内存，无害）。可在 reader 线程退出时补一次 `waitpid(..., WNOHANG)`。


## 4.0.0 状态（键盘 / 滚动 / 布局）

- **系统键盘已拉起** ✔：`import globalModule from 'global'` → `new globalModule.Global()` →
  `startTextEdit(JSON)`（`enterButtonText:'执行'`）实测弹出笔的输入法（截图 `out/term/kb1.png`），
  合成点击能往输入框里打字 ✔。
- **盲区（下一轮）**：`执行` 后**回调没到我的 handler**——`this._gm.textEditFinished.on(...)` 与
  `$falcon.on('textEditFinished', ...)` 都订阅成功（日志确认）但都不触发。面板关闭后页面会重新
  `onShow`，所以可用**面板关闭 + 轮询取值**兜底：调用 `Global` 实例上的
  `getTextEditValue/getTextEditContent/getTextEditText/getCurrentTextEditValue/getEditText/getContent/getText/getValue`
  （PenBili `services/input.js::_probeText` 列出的候选）把文本读回来。这是下一步第一件事。
- **滚动回看** ✔ 已实现：VT 侧 `VT_HIST=600` 行环形历史 + `view` 偏移，`vtFeed` 有新输出自动回底；
  插件 API `term.vtScroll(sid, delta)` / `term.vtHistory(sid)`；页面用**上滑/下滑手势** +
  快捷键条里的「上翻/下翻/回到最新」，回看时右下角显示「已回看 N 行 · 点此回到最新」。
- **布局不再被按键挤压** ✔：快捷键条默认折叠（VT 12 行 = 192px），点顶栏「键」展开
  （VT 11 行 = 176px，`vtResize`+`TIOCSWINSZ` 会让 top/vi 自己重排）；输入行走系统键盘，
  不用 `<input>`（笔上 `<input>` 不会自动弹输入法）。

## 键盘调试结论 + "换新包不生效"的坑（2026-10-02 实测）

**键盘回调其实一直是通的**：日志里 `textEditFinished confirmed=true canceled=false len=32`
—— 32 就是**会话 UUID 的长度**。面板回调把 uuid 放在 `text` 字段里（对象形态），
而"取最长字符串当输入内容"的启发式让 uuid 顶掉了真实输入，于是 shell 收到的是
`d1cc42e6…: not found`。修法：加 `isIdLike()`（`/^[0-9a-f]{32}$/i` 或 36 位带横线），
**字符串分支和对象分支都要过滤**（PenBili `services/input.js` 就是这么干的）。

`Global` 实例的完整成员已 dump 过，可用的相关 API：
`startTextEdit` / `closeTextEdit` / `clearTextEditContent` / `sendTextEditFinishedSignal` /
`startTextMemory` + `textMemoryFinished`；信号 `textEditFinished` / `apolloTextEditClosed` /
`inputTypeChanged` / `imPanelVisbileChanged`；**没有取文本的 getter**（所以必须靠回调，不能轮询）。
另有 `execShell` / `showToast` / `captureAndSaveToPath` / `restartMiniapp` / `getBrightness` /
`screenOnOff` / `getEnvValue` 等一堆能用的原生能力。

**⚠ 换新包后在笔上不生效的坑**：`miniapp_cli uninstall/install` 之后，**旧 app 实例可能还活着**
（`AppLifecycle->appResumed` 里版本号还是旧的），页面跑的是旧代码，会让人误判"改了没用"。
可靠的换包姿势：
```
miniapp_cli uninstall <appid>
# 等日志出现 appDestroyed（或 sleep 3）
miniapp_cli install /tmp/x.amr
sleep 4
miniapp_cli start <appid> --index      # 注意是 --index（帮助里的用法）
# 校验：grep 'appResumed' 日志，确认 getVersion() 是新版本
```

### ⚠⚠ 最坑的一个：`--index` 让新代码"完全没生效"

`miniapp_cli` 的帮助写的是 `start {appId} --{page}`，照抄成
`miniapp_cli start <appid> --index` 的结果是：框架把页面名当成字面量 **`--index`**，
去找 `<slot>/--index.js` → 日志 `read jsfm file failed: .../b//--index.js`，
**页面从未加载**，屏幕上一直是上一个还活着的旧实例（`appResumed` 里版本号没变）。
现象就是"改了代码、装了包，界面上没有任何变化"。
**正确写法：`miniapp_cli start <appid> index`（不带横线）。**

搭配前面的实例残留问题，完整的"确认新版本真的在跑"流程：
1. `miniapp_cli uninstall <appid>` → 等日志出现 `appDestroyed`
2. `miniapp_cli install /tmp/x.amr` → `sleep 4`
3. `miniapp_cli start <appid> index`（**无横线**）
4. 校验 `grep appResumed` 的 `getVersion()` 是新版本，且没有 `read jsfm file failed`

## 🎉 9.0.0：完全自研 entry 打通（不再借用官方 app.js.bin）

**最终配方**（每一步都是实机验证过的，缺一不可）：

### 1) 三个文件的分工

| 文件 | 内容 |
|---|---|
| `app.js` → `app.js.bin` | 入口：**import 页面模块** + import 基类；设 `$falcon.__AppClazz` / `__loadModuleDefault` / `__KEYFRAMES`；`App.meta`；`onLaunch` 里 `setViewPort(960)` + `useDefaultBasePageClass(BasePage)` |
| `base-page.js` → `BasePage.js.bin` | 基类页面（extends `$falcon.Page`）；**`onLoad` 里 `this.setRootComponentOptions(Component)`** ← 挂载就靠这一句 |
| `component.js` → `Component.js.bin` | 纯 Vue options 对象（UI），`import term from 'term'` 等都在这里 |
| `page-index.js` → `index.js.bin` / `shell.js.bin` | 很薄的页面模块：`class PageTerminal extends BasePage {...} export default PageTerminal;` |

### 2) 入口必须 import 页面模块 —— 这是"框架知道有页面"的开关

```js
import './index.js';      // ← 少了这行，框架永远不会去求值页面模块
import './shell.js';
import { BasePage } from './BasePage.js';
```

实测：加上这两行后日志立刻出现页面模块被求值；不加则连求值都没有。
这解释了官方 bundle 里那两个 atom：`'./'` + `'.js'`（胶水是静态 import 每个页面模块的）。

### 3) ★ 挂载调用必须写在**基类页面**里，不是页面类里

```
[term-ui] BasePage.onLoad 到
[term-ui] 基类挂载: setRootComponentOptions 已调用     ← 这一句一出现，页面就挂上了
[term-ui] onShow / attach 本地 shell ...
```

**框架实例化的是"基类页面"**（`useDefaultBasePageClass` 传进去的那个类），
页面模块 default 导出的那个类**不会被实例化**（`PageLifecycle->onStart` 有日志，但页面类的 `onLoad` 不执行）。
所以把 `setRootComponentOptions(Component)` 放在基类 `onLoad` 里才行 —— 这是我卡最久的一步。
（框架 Page 基类有两个挂载方法：`setRootComponent(组件类)` / `setRootComponentOptions(options 对象)`，
普通 Vue options 用后者。）

### 4) `App.meta` 的形态（官方 glue 原样）

```js
App.meta = {
  name: '终端', version: '9.0.0', isSingleJsBundle: false,
  pages: { index: 'pages/index/index.vue', shell: 'pages/index/shell.vue' },   // 值是 .vue 源码路径
  options: { style: { lessPaths: ['styles'] } },
};
```

### 5) 官方 glue 的完整行为（实测 diff，就这么多）

```
$falcon.__AppClazz = App
$falcon.__loadModuleDefault = fn
$falcon.__KEYFRAMES = {0:...,1:...,...}        // 只有这三个键
App.meta = {pages, options, name, version, isSingleJsBundle}
onLaunch: setViewPort(960); useDefaultBasePageClass(BasePage)
```
官方 glue 模块的**命名空间是空的**（连 default 都没有）；`$falcon.__pages` 官方也不用。

### 6) 页面的生命周期（踩过的坑，仍然成立）

- 页面自己的 `onShow/onHide/onUnload` 要写在 Vue `methods` 里（基类用 `$root.onShow()` 转发）
- 输入回车发 `\n`（不是 `\r`）
- 图片必须 `file://` 前缀、每帧换文件名
- 换包必用 `uninstall`（等 `appDestroyed`）+ `install` + `start <appid> index`（**不带 `--`**），并用 `appResumed` 的版本号校验

## 9.1.0：中文支持（CJK 双宽字符）

![中文渲染](out/term/cjk2.png)

### 两件事

**(1) CJK 点阵字库**（`tools/mkcjk.py` → `src/font_cjk.h`，6938 字形 / ~230KB）

- 从**笔自带的 `HarmonyOS_Sans_SC_Regular.ttf`**（`/etc/miniapp/resources/fonts/`）光栅化，字型与系统一致
- 字集：**GB2312 一级+二级全部汉字**（按 GB2312 编码枚举解码得到，不需要外部字表）+ CJK 标点(U+3000-303F) + 全角(U+FF01-FF60) + 常用符号
- 16×16 单色点阵（每行 2 字节），二分查找（`g_cjk_cp[]` 升序）
- 覆盖率：终端里出现的中文文件名/命令输出都能显示；缺字画宽占位框

**(2) VT 支持双宽字符**（一个汉字占两列，这是终端规范）

- `vt_cell_t` 增加 `wide` 标记：`0` 单宽 / `1` 宽字符头 / `2` 宽字符尾
- `vt_is_wide(cp)`：CJK 统一表意、韩文、全角、CJK 标点等区间
- `vt_putc` 写宽字符时占两格、光标前进 2 列；换行边界判断用 `cx + w > cols`
- `vt_fix_wide()`：覆盖/擦除某一格时把它的**伙伴格一起清掉**，否则会留半截字（滚屏、`ED`/`EL`、换行都要用）
- 渲染：宽字符用 16×16 画进相邻两个 8×16 单元（2 字节/行），右半格 `wide==2` 跳过（由头格一起画）
- 光标：落在右半格时回退到头格，并且光标块画 16 像素宽（整字反白）
- `vtText`（调试用屏幕文本 dump）跳过右半格，避免多出空字符

### 实测

```
== PenTerm 自检 ==
中文渲染：你好，有道词典笔！
标点：，。、；：（）《》【】
-rw-r--r--  1 root root    7 Oct  2 21:48 文件.txt     ← 中文文件名 + 列对齐
```

包体积：插件 140KB → **375KB**（含字库），amr 136KB → **462KB**（笔上磁盘充足，无压力）。

### 新增/更新文件

- `tools/mkcjk.py`（新）：CJK 点阵生成器
- `src/font_cjk.h`（新，生成物）：6938 字形
- `src/term_vt.c`：双宽字符 + CJK 渲染
- `src/component.js`：自检命令加入中文验证
