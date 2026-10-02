# 07 · 从零开发 miniapp（不必用 aiot-vue-cli）

本篇是 2026-10-02 为「终端 miniapp」现挖出来的整套自制工具链与**隐式契约**。
有了它，不用官方 CLI 也能写/编/打包/上机一个 miniapp。

## 1. 三件套工具

| 工具 | 在哪 | 作用 |
|---|---|---|
| `tools\build\jsfmc` | 笔上运行（`zig cc` 交叉编译，链接笔的 `/usr/lib/libquickjs.so`） | **JS 源码 → `.js.bin`**；另有 `-g`（全局脚本）、`-d`（反查模块导出/全局） |
| `tools\pack_amr.py` | PC | 通用打包：`manifest.json` + `cert`（除 manifest/icon 外全部文件）+ zip；`--lib <abi>=<path>` 往 `libs/<abi>/` 塞插件 |
| `tools\jsfm_inspect.py` | PC | 解析 `.js.bin` 容器，打印 atom 表（找 API/模块名神器） |

**`.js.bin` 格式（实证）**：就是 `JS_WriteObject(ctx,&len,module_obj,JS_WRITE_OBJ_BYTECODE)` 的原始输出：

```
[0x01][varint 字符串数][N×(varint (字节长<<1|wide) + 内容)][字节码体]
```

字符串表 = atom 表（模块名 → import 说明符 → 标识符/字符串常量），顺序即索引。
所以**必须用笔自己的 QuickJS 编**（厂商打过补丁，上游 qjsc 不一定对得上）。

```powershell
# 笔上编译（示例）
& $adb push 你的.js /tmp/x.js
& $adb shell "/tmp/jsfmc -o /tmp/x.js.bin -n index.js /tmp/x.js"   # -n 是模块名=页面名.js
& $adb pull /tmp/x.js.bin build/index.js.bin
```

`-d` 反查能看模块导出和它设置的全局量，排查"框架到底要什么"特别好用。

## 2. 原生插件（jsapi）必须注意

1. **两个符号都要显式导出**：
   ```c
   __attribute__((visibility("default"))) void custom_init_jsapis(void);
   __attribute__((visibility("default"))) void custom_init_jsmodules(void);
   ```
   框架 dlopen 后用 `dlsym` 找这两个名字。用 `-fvisibility=hidden` 编译时**不加属性会把它们一起藏掉**，
   症状是：插件被 dlopen 了（日志有 `dynamic_load_jsapi ...`），但 `import xxx from 'xxx'` 报
   `ReferenceError: could not load module filename 'xxx'`（框架退而去 `/etc/miniapp/.../js_modules/xxx.js` 找）。
2. 模块注册照旧：`registerCModuleLoader("名字", loader)`，loader 里 `JS_NewCModule` +
   `JS_AddModuleExport(default/具名导出)`，**导出名必须和 app 的 import 一致**。
3. 不要跨线程调 JS；reader 线程只管缓冲，JS 侧轮询。
4. 起守护进程不要用 `sh -c "setsid ... &"`（会被 PTY 会话带走）→ 用 `fork + setsid + 重定向日志 + execv`。
5. OpenSSH sshd 在本机不支持 `-o UsePAM=no`（会 `Unsupported option UsePAM` 直接退出）；
   `/` 是 `erofs` 只读，host key / authorized_keys 要放 `/userdisk` 并用 `-h` / `-o AuthorizedKeysFile=` 指过去。

## 3. app 入口（app.js.bin）的隐式契约

框架对入口有 5 条硬要求，缺一个就起不来（逐条实测）：

| # | 要求 | 缺了会怎样 |
|---|---|---|
| 1 | `$falcon.__AppClazz = App`（App 继承 `$falcon.App`） | `TypeError: not a function`（launcher） |
| 2 | `App.meta = { name, version, isSingleJsBundle, pages:{页面名:'模块基名'}, options }` | `TypeError: cannot convert to object`；`pages` 写错会去加载不存在的 `xxxPage-<hash>.js` |
| 3 | `$falcon.__loadModuleDefault = m => m && m.default !== undefined ? m.default : m` | `loadPage: TypeError: not a function` |
| 4 | `$falcon.__KEYFRAMES = $falcon.__KEYFRAMES \|\| {}` | 动画相关隐患（官方 bundle 都会设） |
| 5 | `onLaunch` 里 `$falcon.useDefaultBasePageClass(BasePage)` | 页面生命周期缺失 |

**页面清单**：`App.meta.pages` 的值是**不带扩展名的模块基名**（官方 bundle 用 `'./'+name+'.js'` 拼；
实证 `{ index: 'index' }` 能让框架加载 `<slot>/index.js.bin`）。`$falcon.__pages`（框架内部量）**不需要**设。

**基类页面**：`$falcon.Page` 子类，实现 `onLoad/onNewOptions/onShow/onHide/onUnload/beforeVueInstantiate/release`；
框架调 `beforeVueInstantiate(Vue)`，`$falcon.Page.prototype` 里**没有**这个方法（是留给基类实现的钩子）；
`$falcon.Page.prototype` 有 `onLoad/onShow/setRootComponent`。
`onShow` 里要转发 `this.$root.onShow()`（`$root` 才是 Vue 实例）。

**页面文件**：就是一个 Vue options 对象（`export default { name, data(), methods, render(h) }`），
**明文 `.js` 也能被加载**（框架对页面名会先试 `.js.bin` 再试 `.js`），但**入口 app.js 只认 `.js.bin`**。

> 页面生命周期钩子（`onShow/onHide/onUnload`）**必须写在 `methods` 里**：
> 框架的基类页面用 `this.$root.onShow()` 转发到 Vue 实例；
> 写成顶层 `onShow` 时 `this` 是"页面实例"而非 Vue 实例，`this.someMethod()` → `not a function`，
> 页面会被销毁（现象：启动 1 秒后弹回桌面）。

## 4. UI 可用词汇（从 `js-framework.min.bin` 挖出来的）

- 框架：**Weex + Vue 2.6.12**，全局 `$falcon`，JS framework 版本 `0.0.32`
- 标签（组件）：`view/div/text/span/img/image/input/textarea/switch/select/slider/indicator/`
  `canvas/list/cell/header/loading/refresh/scrollable/scroller/video/web/richtext/transition/`
  `transition-group/marquee/countdown/embed/a/link/meta/svg/...`
- 生命周期：App 是 `onLaunch/onShow/onHide/onDestroy`；Page 是 `onLoad/onNewOptions/onShow/onHide/onUnload`
- 事件：`click`、`longpress`、`touchstart/move/end`、`pan*`、`swipe`
- 样式：Weex 风格 flex（默认 `flexDirection: column`），`px` / `vh` / `vw` 单位，camelCase 属性名
- 框架注册的 JS 模块（`import x from '名字'` 可用）：
  `fs http crypto path storage net network process zip sqlite3 nvue miniapp pm updater util wpa $am $input $service_proxy`
- 页面里能拿到：`this.$root`(Vue) `this.$page.finish()` `$workspace` `$appid` `$falcon.navTo`

## 5. 其它实测坑

- `miniapp_cli start <appid> --page` 会把 `--page` 当页面名（框架去找 `--page.js.bin`）→ 写 `page` 不带横线。
- 安装落点：`/userdisk/secondary/miniapp/data/mini_app/pkg/<appid>/a/`（另有 `/userdata/miniapp`、`/userdisk/miniapp` 两个根）。
- `libs/<abi>` 的 ABI 目录随机型：官方包用 `libs/arm64-orange`；没有该目录时框架会回退到 `libs/arm64`。
- 框架提取 `.so` 时会重写元数据（体积变化，如 109768 → 197880），核对 `strings` 里的功能符号即可。
- 截图为空（1667B）= 屏幕息屏/框架暂停渲染：先 `echo 200 > /sys/class/backlight/backlight/brightness`
  再注入一次触摸，然后再 `miniapp_cli capture`。
- PowerShell 5.1 读无 BOM 的 UTF-8 `.ps1` 会乱码报语法错 → 存 UTF-8 with BOM，或把逻辑内联执行。

## 6. 终端类 UI：为什么要"原生渲染 + `<image>`"（2026-10-02 实证）

框架只有比例字体（实测 `i`×61=241px、`M`×61=790px），**纯文本行做不出等宽对齐**，
跑 `vim`/`top` 会错位。可行方案：**在插件里做 VT 仿真 + 位图字体渲染成 PNG，页面用 `<image>` 显示**：

1. VT/ANSI 解析 → `cols×rows` 字符网格（字符 + 前后景色 + 属性）
2. 8×16 点阵字体画成 RGB 位图 → 自己写 PNG（`compress2` + Up 滤波）
3. 页面 `<image src="file://...">`；**每帧写新文件名**（框架按 URL 缓存图片），
   文件名带会话 pid，避免同进程多个页面实例互踩

字体来源：笔自带 `cmtt10.ttf`（OFL 真等宽，`/etc/miniapp/resources/latex/res/fonts/latin/optional/`），
用 `scripts/mkfont.py` 在 PC 侧光栅化成点阵头文件（ASCII + 程序化绘制的制表符/方块字符）。

配套坑位：
- **zlib 头文件**：zig sysroot 没有 `zlib.h` → 用 `zlib_min.h` 自己声明，链接笔上 `/usr/lib/libz.so.1`
- **PTY termios 必须显式设置**：框架进程 stdin 不是 tty，继承来的设置不可靠；
  busybox ash 行编辑把 `\r` 当光标控制，**命令只回显不执行** → 设 `ICRNL|OPOST|ONLCR|ICANON|ECHO|ISIG`，页面发 `\n`
- **本地图片必须 `file://` 前缀**，否则 `WXImage::onLoad failed`
- 帧图别照原尺寸死写：960×176 RGB + Up 滤波 + level 3 ≈ 3-10KB/帧，够用
- 终端图片区域用 `<image>` 的行内样式定死宽高（`960px×176px`），`resize: stretch`

## 7. app 入口契约的边界（未完全打通，供接手）

自研 entry 能跑到：App 构造 → `onLaunch` → 页面基类 `onLoad`/`beforeVueInstantiate`/`onShow`、
`$root` 是 object；但**页面组件不 mount**（`render()` 不调用）。
官方 entry 下同一页面文件正常渲染 ⇒ 差异在 CLI 注入的胶水里。
线索：官方 bundle 的 atom 表含 `__pages` + `'./'` + `'.js'`（说明胶水自己 require 页面模块并注册），
且两种 entry 下 `$falcon.Page.prototype` 内容不同（官方多 `beforeVueInstantiate/sleep`，少 `setRootComponent`）。
建议下一步：给 `jsfmc` 加一个"框架桩 + 执行 + dump 所有对 `$falcon`/`globalThis` 的写入"的模式，
把官方 `app.js.bin` 跑一遍，照抄胶水。

## 8. 自研 entry 追踪：结论与唯一未解点（2026-10-02 深夜）

### 8.1 官方胶水的确切行为（**在真框架环境里追踪出来的**）

方法（比独立进程桩靠谱得多，因为官方字节码比共享库新，独立进程读不了）：

1. 把官方 `app.js.bin` 复制一份**改名** `glue.js.bin`（改名才能绕过框架的模块缓存、真正再执行一次）；
2. 加一个**先求值**的快照模块 `snap.js`（`import './snap.js'` 写在 `import './glue.js'` 之前，
   利用 ESM 依赖按顺序求值），把 `$falcon`/`globalThis` 的现状存起来；
3. 自己的页面模块里 `import './glue.js'` → 执行官方胶水 → diff，用 `console.warn` 打出来。

拿到的官方 `App.meta` 原样：

```json
{"pages":{"index":"pages/index/index.vue","shell":"pages/index/shell.vue","page":"pages/page/page.vue", ...},
 "options":{"style":{"lessPaths":["styles"]}},
 "name":"文件管理器","version":"1.2.0","isSingleJsBundle":false}
```

- `meta.pages` 的值是 **`.vue` 源码路径**（不是模块基名）
- `$falcon.__AppClazz` = function，statics = `length,name,prototype,meta`（meta 是**直接挂的静态属性**）
- `$falcon.__loadModuleDefault` = function；`$falcon.__KEYFRAMES` = object
- **`$falcon.__pages` = undefined**（官方不用它）
- `$falcon.$app.$meta` = 上面的 meta **+ 框架追加的 `{appId, appPath}`**

### 8.2 自研 entry 已能跑到哪一步

用官方契约写的自研 entry（`miniapps/terminal/src/app.js`，见 `dist/terminal-6.0.0.amr`）：

```
[term] A registered → ctor → onLaunch ok → BasePage.onLoad → beforeVueInstantiate → onShow（root=object）
appResumed ... getVersion()=6.0.0      ← 新代码确实在跑
```

**但页面组件不挂载**：`[term-ui]`（页面自己的方法）一行都不执行，屏幕全黑（`screen_on=1`）。

### 8.3 已排除的原因

| 假设 | 结果 |
|---|---|
| `meta.pages` 形态不对（基名 vs .vue 路径） | ✗ 改成官方 `.vue` 路径形态后状态不变 |
| `$falcon.__pages` 是页面注册表 | ✗ `{index:'index'}` 与 `{index:'./index.js'}` 两种形状都不挂载 |
| 框架找不到页面文件 | ✗ 框架**根本没尝试加载任何页面文件**（该 app 启动前后无 `fpath/stat fomailed/jsfm` 记录） |
| `__MODULELOADER` 是动态加载器 | ✗ 实测它是**普通对象**：`own=[]`，原型只有 `Object.prototype` 的方法，**没有 load/require/import** |
| 旧实例残留导致误判 | 已用**全新 appid** 排除（`miniapp_cli` 没有 stop，旧实例会一直活着） |

同期还确认：`$falcon._modules`/`_pageMap` 都是空对象；`$falcon.jsapi` 里是 `http.*` 与 `storage.*`。

### 8.4 唯一还没验证的线索

官方 app 的模块图里会 `import '<名字>-<hash>.js'`（实测日志 `read jsfm file failed: .../lookupWordNative-142d00bf.js.js`），
桌面 app 的包里也有 `<页面名>Page-<hash>.js.bin`（如 `indexPage-93295cbd.js.bin`）。
推测 **aiot-vue-cli 会为每个页面生成一个 `<页面名>Page-<hash>.js` 包装块**，框架的页面加载链可能以这个包装块为入口。
下一步：把官方桌面 app 的 `indexPage-93295cbd.js.bin` 拿到桩环境里跑（或用框架内追踪法 `import` 它），
看它导出什么、以及它是怎么把真正的页面组件接上去的。

### 8.5 `<页面名>Page-<hash>.js` 包装块的真面目（2026-10-02 再探）

拿官方「词典」app 的 `dictPage-4bda502c.js.bin` 看 atom 表，它是**打包器切出来的页面入口块**：

```
0  'dictPage-4bda502c.js'
1  './index-3244a59e.js'
2  './title-2b9bf3c1.js'
3  './dictTitle-570c844e.js'
...
36 './BasePage-c810e086.js'
...
```

即：官方 app 是**多文件代码分割打包**，每个页面入口块用 `./<模块名>-<hash>.js` 引一堆分块
（其中还有 **`BasePage-<hash>.js`** —— 应用自己的基类页面，来自 `src/base-page.js`）。
所以：
- `indexPage-93295cbd.js` = index 页面的入口块；
- `BasePage-c810e086.js` = 基类页面块（官方 app 必备，和 PenBili 的 `src/base-page.js` 对应）。

**对自研 entry 的启示**：官方 entry 之所以能挂载页面，很可能是它把
「页面入口块 + 基类页面块」都以**分块模块名**引进来注册；而我的包是"页面名=文件名"（`index.js.bin`）
的单文件形态。下一步实验就两条，都很便宜：
1. `meta.pages = { index: 'index' }`（模块基名，实测框架**会**去加载 `index.js.bin`、生命周期能跑）
   **并且**把基类页面单独做成一个块文件（如 `BasePage.js.bin`），让页面模块 `import './BasePage.js'`；
2. 或者照官方命名把页面文件命名成 `<页面名>Page-<hash>.js.bin` 并在 `meta.pages` 里用这个名字。

## 9. 自研 entry 攻坚记录（2026-10-03 凌晨，第二轮）

### 9.1 追踪方法的关键修正（之前的 diff 是无效的！）

第一轮我把官方 `app.js.bin` 当 **entry** 用，然后再 import 一份改名副本去 diff —— diff 永远是空的，
因为 entry 早就把那些键写好了。**正确做法：用自己的 entry，把官方 bundle 当普通模块 import**：

```js
// app.js（我的 entry）
import './snap.js';      // 先求值：快照 $falcon 现有键
import './glue.js';      // 再执行官方胶水（改名副本，绕过模块缓存）
report();                // 立刻 diff，然后照常立自己的 App
```

这样才拿到**官方 glue 的完整写入清单**（实测）：

```
[glue] 快照 falcon=21 keys=eventMap,_uniqueId,...,$getTopApp
[glue] 新增键: __AppClazz=fn | __loadModuleDefault=fn | __KEYFRAMES=obj{0..6}
[glue] 变化的键: (空)
[glue] __pages = undefined
[glue] 官方 glue 模块命名空间 keys= (空！连 default 都没有)
```

结论：**胶水只做这几件事** —— `$falcon.__AppClazz`、`$falcon.__loadModuleDefault`、`$falcon.__KEYFRAMES`、
`App.meta = {pages:{页面名:'.vue 源码路径'}, options:{style:{lessPaths}}, name, version, isSingleJsBundle}`、
`onLaunch` 里 `useDefaultBasePageClass(BasePage)`。这些我全都复刻了。

### 9.2 新挖到两个关键接口（这轮最大收获）

**(1) 框架 Page 基类有两个挂载方法**（`$falcon.Page` 原型实测）：

```
setRootComponent, setRootComponentOptions, onLoad, onNewOptions, onShow, onHide, onUnload, finish
```

- **普通 Vue options 对象要用 `setRootComponentOptions(Component)`**；
- `setRootComponent` 留给"Vue 组件类"。
（PenBili 的页面用的是 `setRootComponent(IndexComponent)`，但它 import 的 `.vue` 是 vue-loader 产物，
两种取值的差别还需再确认——我两种都试了，见 9.4。）

**(2) entry 必须 import 页面模块，框架才会去求值/注册它们**（实测决定性）：

```js
// app.js
import './index.js';    // ← 少了这两行，页面模块永远不会被求值
import './shell.js';
```

加上这两行后，日志立刻出现 `[term-ui] page module evaluated`（页面模块被真正求值）——
这解释了官方 bundle 里为什么会有 `'./'` + `'.js'` 这两个 atom：**胶水是静态 import 了每个页面模块的**。
官方 App 类的原型链也顺便摸清了（三层：应用层 → 框架 App 层 `$nextTick/$mvvmEnv/$data/onError/finish/setViewPort`
→ 事件/注册层 `init/register/unRegister/trigger/on/off`）。

### 9.3 官方 app 的页面模块形态（PenBili 源码实证）

```js
// pages/index/index.js
import IndexComponent from './index.vue';
import { BasePage } from '../../base-page.js';
class PageIndex extends BasePage {
  onLoad(options) { super.onLoad(options); this.setRootComponent(IndexComponent); }
  onNewOptions(o) { super.onNewOptions(o); }
}
export default PageIndex;          // 页面模块 default 导出的是"类"，不是 Vue options 对象
```

`app.json`（CLI 的页面配置）里 `pages` 的值是**页面模块路径**（`pages/index/index.js`），
而官方 amr 的 `manifest.json` **没有 pages 字段**（只有 appName/version/appid/icon/quickjs/cert）。

### 9.4 目前卡住的最后一步

复刻到"entry import 页面模块 + `__pages` 表 + 类式页面 + setRootComponentOptions"之后：

- ✔ 页面模块、Component 模块**都被求值**（日志确认）
- ✔ App 构造 / onLaunch / onShow 正常，`$falcon` 状态与官方完全一致
- ✗ **框架仍不创建页面实例**（`setRootComponentOptions 已调用` 从未出现），屏幕全黑
- ✗ 框架也没有任何 `fpath / stat failed` 之类"找页面文件"的记录

已穷尽排除：`meta.pages` 三种取值形态（`.vue` 路径 / 模块基名 / 模块路径）、`$falcon.__pages` 三种取值
（字符串 / 页面类 / 组件对象）、manifest 字段（与官方逐字一致，含 `quickjs.version`）、
`__MODULELOADER`（是空对象，无加载能力）、安装槽位残留（已用全新 appid 排除）。

**唯一还没试的方向**：官方包里那些**哈希分块**（`index-c39e9ced.js.bin`、`v-275aa2e2.js.bin`）——
FM 的 entry 静态 import 了它们；也许页面"入口块"（`<页面名>Page-<hash>.js`）才是框架认识的页面单元。
下一步：把自己的页面也做成"入口块 + 组件块"两级结构，并让 entry 只 import 入口块。
