# PenTerm 命令历史 / 常用命令（9.3.3）

![历史面板](screenshot-history.png)
![常用面板](screenshot-favorites.png)

## 一、功能

| 功能 | 交互 |
|---|---|
| **历史命令** | 底部「历史」→ 面板列出**全部**历史（最新在前、分页 7 条/页）。**点命令 = 灌进原生键盘**（可直接改完再执行）；**点 ▶ = 直接重跑** |
| **常用命令** | 底部「常用」→ 列出收藏；点命令=载入键盘，▶=执行，**x = 删除**（9.3.5 起 x 用绝对定位钉在**行最右边**，与 ▶ 完全分开，防误触） |
| **收藏** | 底部「★」或常用面板里的「收藏当前」→ 把当前输入（或上一条）加入常用 |
| **清空** | 面板里的「清空」→ 清历史 / 清常用（同时更新磁盘文件） |
| **Up/Dn 载入命令行** | 点快捷键条的 **Up / Dn** 后，页面会把 **shell 命令行里正在编辑的内容**（去掉提示符）灌进输入框 —— 点 Up 召回上一条后，可以在这里继续改 |
| **屏幕区不再呼出键盘** | 9.3.3 起：**点终端画面不弹键盘**（那里只负责滑动翻看），只有**点下方输入框**才呼出键盘 |

![点屏幕不弹键盘](screenshot-no-keyboard-on-screen.png)

> 早先的「上一条」按钮已被历史面板取代（面板第 1 条就是最新一条，而且能编辑）。

### Up/Dn 是怎么读到命令行的

插件提供 `term.vtCursorLine(sid)`：把 **VT 网格里光标所在行**的可见字符拼成 UTF-8 串返回。
页面拿到后按提示符切一刀：

```
/userdisk # ls -la /userdisk   →  正则 /[#$>]\s+([\s\S]*)$/  →  ls -la /userdisk
```

然后写进 `this.input`，用户就能直接用键盘改。日志里能看到：

```
[term-ui] pullShellLine: "/userdisk # ls -la /userdisk" → "ls -la /userdisk"
```

（按 Up/Dn 后延迟 350ms 再读，给 shell 重画的时间。）

### ⚠️ 必须配合"清行"，否则命令会执行两遍（9.3.6 修）

**症状**：点 Up 载入 `top`，改成别的再点「发送」，实际执行的是 `toptop`（命令重复）。

**原因**：按 Up 时 **shell 自己已经把上一条命令填进了它的命令行**；我们又把它复制到输入框。
于是 shell 那行 = `top`，我们的输入框 = `top`，点「发送」时文本被**追加**到 shell 已有的行后面
→ 拼成 `toptop`。

**修法**：`pullShellLine()` 载入成功后立刻调 `clearShellLine()`，给 PTY 写
**Ctrl-A（0x01，到行首）+ Ctrl-K（0x0b，删到行尾）** 把 shell 那行清空。
用户随后在输入框里改、点发送时，shell 收到的是干净的一行，只执行一次。

**保险**：`clearShellLine()` 先查 `term.vtInfo(sid).alt`，**全屏程序（vi/less/top）里跳过清行**，
免得误删。

**验证日志**（用应用自己的 PTY 跑自检钩子）：

```
KEYTEST 召回后光标行 = "/userdisk # echo AAA-MARKER"    ← Up 后 shell 行里确实有命令（bug 根源）
[term-ui] 已清空 shell 命令行
pullShellLine: "/userdisk # echo AAA-MARKER" → "echo AAA-MARKER"
KEYTEST 清行后光标行 = "/userdisk #"                     ← 行已空
KEYTEST 输入框 = "echo AAA-MARKER"
```

屏幕上每次执行只输出一次 `AAA-MARKER`（修复前是 `AAA-MARKERecho AAA-MARKER`）。

## 二、数据存放（按需求放在 Favorite 下）

```
/userdisk/Favorite/PenTerm/history.json     命令历史（数组，最多 300 条）
/userdisk/Favorite/PenTerm/favorites.json   常用命令（数组）
```

放在这里的好处：**插电脑（MTP）或文件管理器都能看到**，可直接备份/编辑/同步。
两个文件都是纯 JSON 数组，格式：

```json
["ls -la /userdisk", "top -b -n1 | head -12"]
```

插件里的存储 API（`term.storeDir/storeLoad/storeSave/storeList`）：
- 目录**自动创建**（`/userdisk/Favorite/PenTerm`）
- 写入用「**临时文件 + rename**」，掉电不会写出半截 JSON
- 文件名只允许字母数字下划线横线（防路径穿越）
- 读文件有 4MB 兜底上限

## 三、构建

```powershell
python tools\build_terminal.py --version 9.4.0 --install
```

它按顺序做四件事：编译插件 → 笔上编译页面 → 打包 → 安装并同步 keeper 保活包。

**三个必须知道的坑**（都在这条链路上踩过）：

1. **jsfmc 需要 `LD_LIBRARY_PATH=/oem/YoudaoDictPen/output/libs:/usr/lib:/lib`**
   —— 它链接了笔上的 `libyddal_base_log.so`，不带这个路径会报
   `error while loading shared libraries`。构建脚本已内置。
2. **产物命名**：jsfmc 的 `-n` 参数要传**完整模块名**（`Component.js`），
   而输出文件必须是 `<模块名>.bin` = `Component.js.bin`。
   早先写成 `%s.js.bin` → 落成 `Component.js.js.bin` ✗，
   结果是包里同时有新旧两个文件、页面仍然用旧的那个（表现为"改了没生效"）。
3. **`/tmp` 是 tmpfs**：`jsfmc`、`touchinfo` 这些工具重启就没了。
   现在统一放 **`/userdisk/skip_re/tools/`**（持久），用时 `cp` 到 /tmp 或直接跑。

## 四、调试技巧

- **看图定位点击坐标**：注入触摸需要精确坐标，用 PIL 扫截图里的按钮颜色
  （绿 `#3fb950` = ▶/发送/常用，红 `#f85149` = 删除/清空）算出中心点，比目测准。
  给截图叠加刻度线（每 50px 一条 + 数字）再放大，能直接读出按钮 x 坐标。
- **顶栏注入点击无效**：`/dev/input` 注入的点击在 **y ≤ 25**（顶栏区）会被框架的手势层吃掉
  （日志能看到 topAppId 被切到桌面又切回）。**y ≥ 40 的注入正常**。
  所以要用注入验证顶栏功能（如「键」）时，改走**自检钩子**：页面启动时检查一个标记文件，
  命中就自动执行动作并把结果 `console.warn` 出来 —— 不依赖触摸，最可靠。
- **别用 `✕`**：框架字体里是 tofu（方框），换成 ASCII `x`（`›`/`‹`/`★`/`▶` 是可以的）。
- 面板状态在页面实例里，**重启应用不一定会重新载入 JSON**（`_storeReady` 守卫）——
  改了文件要**重启笔**或换实例才会重新读。
- 输入法是**全屏 IME**：它打开时会盖住整个屏幕，注入点击会打到键盘上；
  `miniapp_cli injectKey 4`（BACK）只清空文本、不关面板。调试面板前先确认键盘是关的。
- 改了 `showKeys`/`rows` 默认值做测试时，**两者必须配套**（`showKeys:true` 要配 `rows:ROWS_KEYS`），
  否则 12 行的输出区 + 快捷键条会超出 266px，把快捷键条挤出屏幕（只有上半截可见、点不到）。
