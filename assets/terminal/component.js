try { console.warn('[term-ui] Component evaluated'); } catch (e) {}
// terminal.js —— 终端 miniapp 主页面（VT 位图 + 系统键盘 + 滚动回看）
//
// 关键设计：
//  1) 终端画面不用框架文字渲染（框架只有比例字体，列对不齐），而是插件把 VT 网格画成 PNG，
//     页面用 <image src="file://..."> 显示，每帧一个新文件名（框架按 URL 缓存图片）。
//  2) 输入用**系统输入法**：import globalModule from 'global' → new globalModule.Global()
//     → startTextEdit(JSON) 拉起笔的键盘；结果从 m.textEditFinished.on(...) /
//     $falcon.on('textEditFinished', ...) 双通道回来（多固件形态兼容，见 PenBili services/input.js）。
//  3) 输出区可上下滑动回看：滑动/上翻/下翻 → term.vtScroll()，有新输出自动回到底部。
//  4) 快捷键条默认折叠，点顶栏「⌨ 键」展开，避免把输出区挤小。

import term from 'term';
import globalModule from 'global';

// 32 位 hex / 36 位 uuid 是会话 id，不是输入内容
function isIdLike(s) {
  return typeof s === 'string' && (/^[0-9a-f]{32}$/i.test(s) || /^[0-9a-f-]{36}$/i.test(s));
}

var DIR = '/userdisk/term';
var COLS = 120;           // 120×8px = 960px
var ROWS_KEYS = 9;        // 展开快捷键时两行共 52px：28+144+34+52 = 258 ✓
var ROWS_PLAIN = 12;      // 折叠时：12×16 = 192px

export default {
  name: 'index',

  data() {
    return {
      frame: '',
      status: '未连接',
      hint: '',
      input: '',
      sid: 0,
      rows: ROWS_PLAIN,
      showKeys: false,
      hist: [],              // 全部历史命令（落盘在 /userdisk/Favorite/PenTerm/history.json）
      favs: [],              // 常用命令（落盘 favorites.json）
      panel: '',             // '' | 'hist' | 'fav' —— 打开的面板
      page: 0,               // 面板当前页（0 = 最新的一页）
      pageRows: 7,           // 面板每页显示条数
      lastCmd: '',
      showSsh: false,
      sshHost: '',
      sshUser: 'root',
      sshPort: '22',
      sshPass: '',
      sshKey: '',
      keys: [
        { t: 'Enter', d: '\n' },
        { t: 'Tab', d: '\t' },
        { t: '^C', d: '\u0003' },
        { t: '^D', d: '\u0004' },
        { t: '^Z', d: '\u001a' },
        { t: 'Esc', d: '\u001b' },
        { t: 'Up', d: '\u001b[A' },
        { t: 'Dn', d: '\u001b[B' },
        { t: 'Lf', d: '\u001b[D' },
        { t: 'Rt', d: '\u001b[C' },
        { t: 'PgUp', d: '\u001b[5~' },
        { t: 'PgDn', d: '\u001b[6~' },
      ],
    };
  },

  methods: {
    // ---------- 生命周期（必须写在 methods 里，基类页面用 $root.onShow() 转发）----------
    onShow() {
      console.warn('[term-ui] onShow');
      if (!this._timer) this._timer = setInterval(() => this.tick(), 260);
      if (!this.sid) this.startLocal();
      this.subscribeInput();
      this.storeInit();
    },
    onHide() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
    },
    onUnload() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
    },

    // ---------- 会话 ----------
    killOld() {
      try {
        var olds = term.sessions() || [];
        for (var i = 0; i < olds.length; i++) {
          if (olds[i].sid !== this.sid) term.kill(olds[i].sid);
        }
      } catch (e) {}
    },

    layout() {
      // 折叠/展开快捷键条时改 VT 行数；窗口变化会触发 SIGWINCH，top/vi 会自己重排
      var rows = this.showKeys ? ROWS_KEYS : ROWS_PLAIN;
      this.rows = rows;
      if (this.sid) {
        try { term.vtResize(this.sid, COLS, rows); term.resize(this.sid, COLS, rows); } catch (e) {}
      }
      this.tick(true);
    },

    attach(r, label) {
      this.sid = r.sid;
      var vt = term.vtStart(this.sid, COLS, this.rows, DIR);
      term.resize(this.sid, COLS, this.rows);
      this.status = label + ' · pid ' + r.pid;
      this.hint = '';
      this.tick(true);
      console.warn('[term-ui] attach ' + label + ' sid=' + r.sid + ' vt=' + JSON.stringify(vt));
    },

    startLocal() {
      this.killOld();
      this.sid = 0;
      var r = term.spawnShell('/userdisk');
      if (r && r.ok) this.attach(r, '本地 shell');
      else this.status = '启动失败: ' + JSON.stringify(r);
    },

    connectSsh() {
      var host = (this.sshHost || '').trim();
      if (!host) { this.status = '请填写主机'; return; }
      this.killOld();
      this.sid = 0;
      var port = parseInt(this.sshPort || '22', 10);
      var user = (this.sshUser || 'root').trim();
      var extra = this.sshKey ? '-i' + this.sshKey.trim() : '';
      var cmd = '/bin/ssh -tt -p ' + port + ' -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' +
                (extra ? ' ' + extra : '') + ' ' + user + '@' + host;
      var r = term.spawn(cmd, { cols: COLS, rows: this.rows, autopassword: this.sshPass || null });
      if (r && r.ok) { this.showSsh = false; this.attach(r, 'SSH ' + user + '@' + host + ':' + port); }
      else this.status = 'SSH 启动失败: ' + JSON.stringify(r);
    },

    // ---------- 轮询渲染 ----------
    tick(force) {
      if (!this.sid) return;
      try {
        var f = term.vtFeed(this.sid);
        if ((f && f.dirty) || force) {
          // <image> 异步加载：上一帧没加载完就不发下一帧，避免排队/显示落后一两帧
          if (force || !this._imgLoading) {
            var img = term.vtRender(this.sid);
            if (img && img.ok && img.path) {
              this.frame = img.path;
              this._imgLoading = true;
              var self = this;
              if (this._imgTimer) clearTimeout(this._imgTimer);
              this._imgTimer = setTimeout(function () { self._imgLoading = false; self._imgTimer = null; }, 1500);
            }
          }
        }
        var h = term.vtHistory(this.sid);
        if (h && h.view > 0) this.hint = '已回看 ' + h.view + ' 行 · 点此回到最新';
        else if (this.hint) this.hint = '';
      } catch (e) {
        console.warn('[term-ui] tick 异常 ' + e);
      }
    },

    imgLoaded() {
      this._imgLoading = false;
      if (this._imgTimer) { clearTimeout(this._imgTimer); this._imgTimer = null; }
    },

    // ---------- 输出区回看 ----------
    scrollView(delta) {
      if (!this.sid) return;
      try {
        var r = term.vtScroll(this.sid, delta);
        this.hint = (r && r.view > 0) ? ('已回看 ' + r.view + ' 行 · 点此回到最新') : '';
      } catch (e) {}
      this.tick(true);
    },
    toBottom() {
      if (!this.sid) return;
      try { term.vtScroll(this.sid, -999999); } catch (e) {}
      this.hint = '';
      this.tick(true);
    },
    onSwipe(e) {
      var d = e && e.direction;
      if (d === 'up') this.scrollView(3);          // 手指上滑 = 往后翻
      else if (d === 'down') this.scrollView(-3);
    },

    // 触摸取 Y 坐标（不同实现给的字段不一样，挨个兜）
    evY(e) {
      try {
        var l = (e && (e.touches || e.changedTouches)) || null;
        if (l && l.length) {
          var t0 = l[0];
          if (typeof t0.pageY === 'number') return t0.pageY;
          if (typeof t0.screenY === 'number') return t0.screenY;
          if (typeof t0.y === 'number') return t0.y;
        }
        if (e && typeof e.pageY === 'number') return e.pageY;
      } catch (err) {}
      return 0;
    },
    onTouchStart(e) { this._ty = this.evY(e); this._tdy = 0; },
    onTouchMove(e) {
      var y = this.evY(e);
      if (this._ty === undefined || this._ty === null) { this._ty = y; return; }
      this._tdy = y - this._ty;
    },
    onTouchEnd(e) {
      var d = this._tdy || 0;
      this._ty = null; this._tdy = 0;
      if (d > 26) { this._suppressClick = true; this.scrollView(3); }        // 手指下滑 = 往旧内容翻
      else if (d < -26) { this._suppressClick = true; this.scrollView(-3); } // 手指上滑 = 往新内容翻
    },

    // ---------- 输入：系统键盘（global 模块）----------
    subscribeInput() {
      if (this._kbSub) return;
      this._kbSub = true;
      var self = this;
      try {
        if (!this._gm) this._gm = new globalModule.Global();
      } catch (e) {
        console.warn('[term-ui] global 模块不可用: ' + e);
        return;
      }

      try {
        this._gm.textEditFinished.on(function () { self.onEditFinished(Array.prototype.slice.call(arguments)); });
        console.warn('[term-ui] 已订阅 textEditFinished');
      } catch (e) { console.warn('[term-ui] 订阅 textEditFinished 失败: ' + e); }
      try {
        this._gm.apolloTextEditClosed.on(function () { console.warn('[term-ui] 面板关闭(textEditClosed)'); });
      } catch (e) {}
      try {
        $falcon.on('textEditFinished', function () { self.onEditFinished(Array.prototype.slice.call(arguments)); });
        console.warn('[term-ui] 已订阅总线 textEditFinished');
      } catch (e) {}      try {
        $falcon.on('textEditFinished', function () { self.onEditFinished(Array.prototype.slice.call(arguments)); });
        console.warn('[term-ui] 总线回调订阅成功');
      } catch (e) { console.warn('[term-ui] 总线回调订阅失败: ' + e); }
    },

    openKeyboard(prefill) {
      if (this._suppressClick) { this._suppressClick = false; return; }   // 刚才是在滑动，不是点击
      if (!this.sid) return;
      this.subscribeInput();
      if (!this._gm) { this.status = '系统键盘不可用'; return; }
      var seed = (typeof prefill === 'string') ? prefill : (this.input || '');
      try {
        var uuid = this._gm.startTextEdit(JSON.stringify({
          text: seed,
          placeholder: '输入命令或文字',
          maxlength: 4096,
          autofocus: true,
          enterButtonText: '执行',
          placeholderColor: '#666666',
        }));
        if (uuid && typeof uuid === 'object') uuid = uuid.uuid || uuid.value || '';
        this._kbUuid = String(uuid || '');
        console.warn('[term-ui] startTextEdit uuid=' + this._kbUuid.slice(0, 8));
      } catch (e) {
        console.warn('[term-ui] startTextEdit 失败: ' + e);
        this.status = '拉起键盘失败';
      }
    },

    openKeyboardFor(key) {
      // SSH 表单字段也走系统键盘：先把当前值填进去，确认后写回该字段
      this._kbTarget = key;
      var self = this;
      this.input = this[key] || '';
      this.openKeyboard();
    },

    // 回调形态很多（字符串/对象/JSON 串/布尔），统一吸收
    onEditFinished(args) {
      var acc = { text: '', confirmed: false, canceled: false, uuid: '' };
      var absorb = function (x, depth) {
        if (depth > 3 || x === undefined || x === null) return;
        if (typeof x === 'string') {
          var s = x.trim();
          if (!s) return;
          if (s.charAt(0) === '{' || s.charAt(0) === '[') {
            try { absorb(JSON.parse(s), depth + 1); return; } catch (e) {}
          }
          if (isIdLike(s)) { if (!acc.uuid) acc.uuid = s; return; }
          if (s.length > acc.text.length) acc.text = s;
          return;
        }
        if (typeof x === 'boolean') { if (x) acc.confirmed = true; return; }
        if (typeof x === 'object') {
          if (x.editConfirmed === true || x.confirmed === true) acc.confirmed = true;
          if (x.editCanceled === true || x.canceled === true) acc.canceled = true;
          var t = x.text !== undefined ? x.text
                : x.value !== undefined ? x.value
                : x.editText !== undefined ? x.editText
                : x.contents;
          if (typeof t === 'string' && !isIdLike(t) && t.length >= acc.text.length) acc.text = t;
          var u = x.uuid || x.calledTaskUuid || x.taskUuid;
          if (typeof u === 'string' && u) acc.uuid = u;
          if (typeof x.data === 'string') absorb(x.data, depth + 1);
        }
      };
      try {
        var raw = [];
        for (var j = 0; j < args.length; j++) {
          var a = args[j];
          if (a === null || a === undefined) { raw.push(String(a)); continue; }
          if (typeof a === 'object') {
            var ks = [];
            try {
              var names = Object.keys(a);
              for (var q = 0; q < names.length; q++) {
                var v = a[names[q]];
                ks.push(names[q] + ':' + typeof v + (typeof v === 'string' ? '(' + v.length + ')"' + v.slice(0, 24) + '"' : ''));
              }
            } catch (e2) { ks.push('枚举失败'); }
            raw.push('{' + ks.join(',') + '}');
          } else {
            raw.push(typeof a + '=' + String(a).slice(0, 40));
          }
        }
        console.warn('[term-ui] 原始回调 cfg=' + args.length + ' :: ' + raw.join(' | '));
      } catch (e) { console.warn('[term-ui] 原始回调打印失败 ' + e); }      for (var i = 0; i < args.length; i++) absorb(args[i], 0);
      console.warn('[term-ui] textEditFinished confirmed=' + acc.confirmed +
                   ' canceled=' + acc.canceled + ' len=' + acc.text.length);

      var self = this;
      setTimeout(function () {
        try { if (self._gm) self._gm.closeTextEdit(acc.uuid || self._kbUuid); } catch (e) {}
      }, 80);

      if (acc.canceled && !acc.confirmed) return;
      if (!acc.text && !acc.confirmed) return;

      if (this._kbTarget) {
        var k = this._kbTarget;
        this._kbTarget = null;
        this[k] = acc.text;
        this.input = '';
        console.warn('[term-ui] 表单字段 ' + k + ' 已填 len=' + acc.text.length);
        return;
      }
      this.input = acc.text;
      this.send();
    },

    // ---------- 历史 / 常用命令：持久化在 /userdisk/Favorite/PenTerm/ ----------
    storeInit() {
      if (this._storeReady) return;
      this._storeReady = true;
      try {
        var raw = term.storeLoad('history');
        if (raw) { var a = JSON.parse(raw); if (a && a.length) this.hist = a; }
      } catch (e) { console.warn('[term-ui] 读历史失败: ' + e); }
      try {
        var raw2 = term.storeLoad('favorites');
        if (raw2) { var b = JSON.parse(raw2); if (b && b.length) this.favs = b; }
      } catch (e) { console.warn('[term-ui] 读常用失败: ' + e); }
      var dir = '';
      try { dir = term.storeDir(); } catch (e) {}
      console.warn('[term-ui] 已载入 历史=' + this.hist.length + ' 常用=' + this.favs.length + ' 目录=' + dir);
    },
    saveHist() {
      try { term.storeSave('history', JSON.stringify(this.hist.slice(-300))); } catch (e) {}
    },
    saveFavs() {
      try { term.storeSave('favorites', JSON.stringify(this.favs)); } catch (e) {}
    },

    // ---------- 面板：历史 / 常用 ----------
    togglePanel(p) {
      this.panel = (this.panel === p) ? '' : p;
      this.page = 0;
      if (this.panel === 'fav' && !this.favs.length) this.status = '常用还是空的：输入命令后点「收藏」';
    },
    closePanel() { this.panel = ''; },
    // 把列表摊平成"第 0 条 = 最新"的数组
    panelList() {
      var src = (this.panel === 'fav') ? (this.favs || []) : (this.hist || []);
      var out = [];
      for (var i = src.length - 1; i >= 0; i--) out.push({ i: i, cmd: String(src[i]) });
      return out;
    },
    pageTotal() {
      return Math.max(1, Math.ceil(this.panelList().length / this.pageRows));
    },
    pageItems() {
      var all = this.panelList();
      var start = this.page * this.pageRows;
      return all.slice(start, start + this.pageRows);
    },
    pageNext() { if (this.page + 1 < this.pageTotal()) this.page++; },
    pagePrev() { if (this.page > 0) this.page--; },
    // 点某条 = 灌进原生键盘 → 可以改完再执行（这就是"查看并编辑全部历史"）
    editCmd(cmd) {
      this.input = cmd;
      this.panel = '';
      this.openKeyboard(cmd);
    },
    // ▶ = 直接重跑
    runCmd(cmd) {
      this.input = cmd;
      this.panel = '';
      this.send();
    },
    addFav() {
      var c = this.input || this.lastCmd || '';
      if (!c) { this.status = '没有可收藏的命令（先输入或跑一条）'; return; }
      if (this.favs.indexOf(c) >= 0) { this.status = '已在常用里'; return; }
      this.favs.push(c);
      this.saveFavs();
      this.status = '已收藏 ' + String(c).slice(0, 24);
    },
    delFav(i) {
      this.favs.splice(i, 1);
      this.saveFavs();
      this.status = '已删除该常用';
    },
    clearHist() {
      this.hist = [];
      this.saveHist();
      this.page = 0;
      this.status = '历史已清空（文件也已更新）';
    },
    clearFavs() {
      this.favs = [];
      this.saveFavs();
      this.page = 0;
      this.status = '常用已清空';
    },

    // ---------- 发送 ----------
    send() {
      if (!this.sid) return;
      var cmd = this.input || '';
      if (cmd) {
        var h = this.hist || (this.hist = []);
        if (!h.length || h[h.length - 1] !== cmd) { h.push(cmd); this.saveHist(); }
        if (h.length > 300) h.shift();
        this.lastCmd = cmd;
        this.page = 0;
      }
      term.write(this.sid, cmd + '\n');
      this.input = '';
      this.toBottom();
      this.tick();
    },

    // 上一条命令的"快捷回填"已被「历史」面板取代（面板第 1 条就是最新一条，可编辑）
    sendKey(d) {
      if (!this.sid) return;
      term.write(this.sid, d);
      // Up/Dn 是 shell 的历史召回：等它重画完，把"命令行里正在编辑的内容"捞进输入框
      if (d === '\u001b[A' || d === '\u001b[B') {
        setTimeout(() => this.pullShellLine(), 350);
      }
      this.tick();
    },

    // 读终端光标所在行（即 shell 的命令行），去掉提示符后灌进输入框，
    // 然后**把 shell 那一行清掉** —— 否则用户改完点「发送」时，文本会被追加到
    // shell 已有的那行后面，变成 toptop 这种"执行两遍"的 bug。
    pullShellLine() {
      if (!this.sid) return;
      try {
        var line = term.vtCursorLine(this.sid) || '';
        var m = line.match(/[#$>]\s+([\s\S]*)$/);
        var cmd = (m ? m[1] : line).replace(/\s+$/, '');
        if (cmd) {
          this.input = cmd;
          this.status = '已从命令行载入：' + String(cmd).slice(0, 22);
          this.clearShellLine();
        } else {
          this.status = '命令行当前是空的';
        }
        console.warn('[term-ui] pullShellLine: ' + JSON.stringify(line) + ' → ' + JSON.stringify(cmd));
      } catch (e) {
        console.warn('[term-ui] 读命令行失败: ' + e);
      }
    },

    // 清空 shell 的命令行（Ctrl-A 到行首 + Ctrl-K 删到行尾）。
    // 全屏程序（vi/less/top）里不动它，避免误删。
    clearShellLine() {
      if (!this.sid) return;
      try {
        var info = term.vtInfo(this.sid);
        if (info && info.alt) { console.warn('[term-ui] 全屏程序中，跳过清行'); return; }
      } catch (e) {}
      term.write(this.sid, '\u0001\u000b');
      console.warn('[term-ui] 已清空 shell 命令行');
    },
    clearScreen() {
      if (this.sid) term.write(this.sid, 'clear\n');
      this.tick();
    },
    closeSession() {
      if (this.sid) { term.kill(this.sid); this.sid = 0; }
      this.status = '未连接';
      this.hint = '';
    },
    selfTest() {
      if (!this.sid) return;
      term.write(this.sid, 'clear; echo "== PenTerm 自检 =="; echo "中文渲染：你好，有道词典笔！"; echo "标点：，。、；：（）《》【】"; ls -la /userdisk/中文测试 2>/dev/null; id; uname -a; echo; top -b -n1 | head -8\n');
      this.toBottom();
      this.tick();
    },
    startSshd() {
      var r = term.sshdStart(2222, '');
      this.status = 'sshd :2222 ' + (r && r.ok ? '已启动' : '失败');
    },
    toggleSsh() { this.showSsh = !this.showSsh; },
    toggleKeys() { this.showKeys = !this.showKeys; this.layout(); },
    onType(e) { this.input = (e && e.value !== undefined) ? e.value : ''; },
    quit() { if (this.$page && this.$page.finish) this.$page.finish(); },
  },

  render(h) {
    var root = { flexDirection: 'column', backgroundColor: '#0b0f14', width: '960px', height: '266px' };

    // ---- 顶栏 ----
    var bar = h('div', {
      staticStyle: {
        flexDirection: 'row', height: '28px', alignItems: 'center',
        backgroundColor: '#161b22', paddingLeft: '8px', paddingRight: '8px',
      },
    }, [
      h('text', { staticStyle: { color: '#7ee787', fontSize: '16px', marginRight: '10px' } }, 'PenTerm'),
      h('text', { staticStyle: { color: '#8b949e', fontSize: '13px', flex: 1 } }, this.status),
      h('text', { staticStyle: { color: '#58a6ff', fontSize: '14px', marginRight: '10px' }, on: { click: () => this.startLocal() } }, '本地'),
      h('text', { staticStyle: { color: '#d29922', fontSize: '14px', marginRight: '10px' }, on: { click: () => this.toggleSsh() } }, 'SSH'),
      h('text', { staticStyle: { color: '#f778ba', fontSize: '14px', marginRight: '10px' }, on: { click: () => this.startSshd() } }, 'sshd'),
      h('text', { staticStyle: { color: '#8b949e', fontSize: '14px', marginRight: '10px' }, on: { click: () => this.clearScreen() } }, '清屏'),
      h('text', { staticStyle: { color: '#f85149', fontSize: '14px', marginRight: '10px' }, on: { click: () => this.closeSession() } }, '断开'),
      h('text', { staticStyle: { color: '#c9d1d9', fontSize: '14px', marginRight: '10px' }, on: { click: () => this.selfTest() } }, '自检'),
      h('text', {
        staticStyle: {
          color: this.showKeys ? '#0b0f14' : '#c9d1d9', fontSize: '14px',
          backgroundColor: this.showKeys ? '#58a6ff' : '#30363d',
          paddingLeft: '8px', paddingRight: '8px', paddingTop: '2px', paddingBottom: '2px', borderRadius: '6px',
        },
        on: { click: () => this.toggleKeys() },
      }, '键'),
    ]);

    // ---- 终端画面（点一下调键盘、上下滑动翻页）----
    var imgH = (this.rows * 16) + 'px';
    // 按需求：终端画面区**不再**呼出键盘（只能点下方输入框），这里只保留滑动翻看
    var screen = h('div', {
      staticStyle: { width: '960px', height: imgH, backgroundColor: '#0b0f14', position: 'relative' },
      on: {
        swipe: this.onSwipe,
        touchstart: this.onTouchStart,
        touchmove: this.onTouchMove,
        touchend: this.onTouchEnd,
      },
    }, [
      this.frame
        ? h('image', {
            staticStyle: { width: '960px', height: imgH },
            attrs: { src: 'file://' + this.frame, resize: 'stretch' },
            on: { load: () => this.imgLoaded(), error: () => this.imgLoaded() },
          })
        : h('div', {
            staticStyle: { width: '960px', height: imgH, alignItems: 'center', justifyContent: 'center' },
          }, [h('text', { staticStyle: { color: '#484f58', fontSize: '16px' } }, '点这里输入 / 终端未连接')]),
      this.hint
        ? h('text', {
            staticStyle: {
              position: 'absolute', right: '8px', bottom: '6px',
              color: '#0b0f14', fontSize: '13px', backgroundColor: '#d29922',
              paddingLeft: '8px', paddingRight: '8px', paddingTop: '2px', paddingBottom: '2px', borderRadius: '6px',
            },
            on: { click: () => this.toBottom() },
          }, this.hint)
        : h('text', { staticStyle: { position: 'absolute', right: '8px', bottom: '6px', color: '#484f58', fontSize: '12px' } }, '上滑翻看 · 点下方输入框输入'),
    ]);

    // ---- 历史 / 常用 面板（占满输出区，可翻页；点条目=载入键盘编辑，▶=直接执行）----
    var panelView = null;
    if (this.panel) {
      var isFav = (this.panel === 'fav');
      var items = this.pageItems();
      var btn = (txt, color, bg, fn, key) => h('text', {
        key: key,
        staticStyle: {
          color: color, fontSize: '14px', backgroundColor: bg,
          paddingLeft: '8px', paddingRight: '8px', paddingTop: '2px', paddingBottom: '2px',
          borderRadius: '5px', marginRight: '8px',
        },
        on: { click: fn },
      }, txt);

      var rows = items.map((it, idx) => h('div', {
        key: 'r' + idx,
        staticStyle: { width: '960px', position: 'relative', flexDirection: 'row', height: '20px', alignItems: 'center', paddingLeft: '8px', paddingRight: '8px' },
      }, [
        h('text', { staticStyle: { color: '#484f58', fontSize: '12px', width: '34px' } }, '#' + (it.i + 1)),
        h('text', {
          staticStyle: { flex: 1, color: isFav ? '#7ee787' : '#c9d1d9', fontSize: '14px' },
          on: { click: () => this.editCmd(it.cmd) },
        }, String(it.cmd).slice(0, 46)),
        h('text', {
          staticStyle: { color: '#0b0f14', fontSize: '13px', backgroundColor: '#3fb950', paddingLeft: '7px', paddingRight: '7px', borderRadius: '5px', marginLeft: '6px' },
          on: { click: () => this.runCmd(it.cmd) },
        }, '▶'),
        isFav ? h('text', {
          staticStyle: {
            position: 'absolute', right: '12px', top: '2px',
            color: '#f85149', fontSize: '13px', paddingLeft: '8px', paddingRight: '8px',
          },
          on: { click: () => this.delFav(it.i) },
        }, 'x') : h('text', { staticStyle: { width: '0px' } }, ''),
      ]));

      panelView = h('div', {
        staticStyle: { width: '960px', height: imgH, backgroundColor: '#0b0f14' },
      }, [
        h('div', {
          staticStyle: { flexDirection: 'row', height: '26px', alignItems: 'center', backgroundColor: '#161b22', paddingLeft: '8px', paddingRight: '8px' },
        }, [
          h('text', { staticStyle: { color: isFav ? '#7ee787' : '#58a6ff', fontSize: '15px', flex: 1 } },
            (isFav ? '常用命令' : '历史命令') + '  ' + (this.page + 1) + '/' + this.pageTotal() +
            '  共 ' + this.panelList().length + ' 条'),
          isFav ? btn('收藏当前', '#0b0f14', '#3fb950', () => this.addFav(), 'b1') : null,
          btn('清空', '#f85149', '#21262d', () => (isFav ? this.clearFavs() : this.clearHist()), 'b2'),
          btn('关闭', '#c9d1d9', '#30363d', () => this.closePanel(), 'b3'),
        ]),
        h('div', { staticStyle: { flex: 1 } }, rows.length ? rows : [
          h('text', { staticStyle: { color: '#484f58', fontSize: '14px', paddingLeft: '12px', paddingTop: '8px' } },
            isFav ? '还没有常用命令：输入命令后点顶栏「收藏当前」' : '还没有历史命令'),
        ]),
        h('div', {
          staticStyle: { flexDirection: 'row', height: '24px', alignItems: 'center', backgroundColor: '#0d1117', paddingLeft: '8px', paddingRight: '8px' },
        }, [
          btn('‹ 上页', '#c9d1d9', '#21262d', () => this.pagePrev(), 'p1'),
          btn('下页 ›', '#c9d1d9', '#21262d', () => this.pageNext(), 'p2'),
          h('text', { staticStyle: { color: '#484f58', fontSize: '13px' } }, '点命令=载入键盘编辑 · ▶=直接执行'),
        ]),
      ]);
    }

    // ---- 输入行 ----
    var inputRow = h('div', {
      staticStyle: { flexDirection: 'row', height: '34px', alignItems: 'center', backgroundColor: '#161b22', paddingLeft: '8px', paddingRight: '6px' },
    }, [
      h('text', { staticStyle: { color: '#58a6ff', fontSize: '16px', marginRight: '6px' } }, '>'),
      h('text', {
        staticStyle: { flex: 1, color: this.input ? '#e6edf3' : '#484f58', fontSize: '15px' },
        on: { click: () => this.openKeyboard() },
      }, this.input ? this.input : (this.lastCmd ? ('点这里输入…（上一条：' + String(this.lastCmd).slice(0, 30) + '）') : '点这里调出键盘输入…')),
      h('text', {
        staticStyle: { color: '#d29922', fontSize: '15px', backgroundColor: '#30363d', paddingLeft: '9px', paddingRight: '9px', paddingTop: '3px', paddingBottom: '3px', borderRadius: '6px', marginRight: '7px' },
        on: { click: () => this.addFav() },
      }, '★'),
      h('text', {
        staticStyle: { color: this.panel === 'hist' ? '#0b0f14' : '#c9d1d9', fontSize: '15px', backgroundColor: this.panel === 'hist' ? '#58a6ff' : '#30363d', paddingLeft: '9px', paddingRight: '9px', paddingTop: '3px', paddingBottom: '3px', borderRadius: '6px', marginRight: '7px' },
        on: { click: () => this.togglePanel('hist') },
      }, '历史'),
      h('text', {
        staticStyle: { color: this.panel === 'fav' ? '#0b0f14' : '#c9d1d9', fontSize: '15px', backgroundColor: this.panel === 'fav' ? '#3fb950' : '#30363d', paddingLeft: '9px', paddingRight: '9px', paddingTop: '3px', paddingBottom: '3px', borderRadius: '6px', marginRight: '7px' },
        on: { click: () => this.togglePanel('fav') },
      }, '常用'),
      h('text', {
        staticStyle: { color: '#0b0f14', fontSize: '15px', backgroundColor: '#58a6ff', paddingLeft: '9px', paddingRight: '9px', paddingTop: '3px', paddingBottom: '3px', borderRadius: '6px', marginRight: '7px' },
        on: { click: () => this.openKeyboard() },
      }, '键盘'),
      h('text', {
        staticStyle: { color: '#0b0f14', fontSize: '15px', backgroundColor: '#3fb950', paddingLeft: '9px', paddingRight: '9px', paddingTop: '3px', paddingBottom: '3px', borderRadius: '6px' },
        on: { click: () => this.send() },
      }, '发送'),
    ]);

    var children = [bar, panelView || screen, inputRow];

    // ---- 快捷键条（默认折叠，避免挤占输出区）----
    if (this.showKeys) {
      children.push(h('div', {
        staticStyle: { flexDirection: 'row', height: '26px', alignItems: 'center', backgroundColor: '#0d1117', paddingLeft: '4px' },
      }, this.keys.map((k, i) => h('text', {
        key: 'k' + i,
        staticStyle: {
          color: '#8b949e', fontSize: '13px', marginRight: '6px',
          paddingTop: '1px', paddingBottom: '1px', paddingLeft: '5px', paddingRight: '5px',
          backgroundColor: '#21262d', borderRadius: '5px',
        },
        on: { click: () => this.sendKey(k.d) },
      }, k.t))));
      children.push(h('div', {
        staticStyle: { flexDirection: 'row', height: '26px', alignItems: 'center', backgroundColor: '#0d1117', paddingLeft: '4px' },
      }, [
        h('text', { staticStyle: { color: '#d29922', fontSize: '13px', marginRight: '8px', paddingLeft: '6px', paddingRight: '6px', backgroundColor: '#21262d', borderRadius: '5px' }, on: { click: () => this.scrollView(3) } }, '上翻'),
        h('text', { staticStyle: { color: '#d29922', fontSize: '13px', marginRight: '8px', paddingLeft: '6px', paddingRight: '6px', backgroundColor: '#21262d', borderRadius: '5px' }, on: { click: () => this.scrollView(-3) } }, '下翻'),
        h('text', { staticStyle: { color: '#3fb950', fontSize: '13px', paddingLeft: '6px', paddingRight: '6px', backgroundColor: '#21262d', borderRadius: '5px' }, on: { click: () => this.toBottom() } }, '回到最新'),
      ]));
    }

    // ---- SSH 面板 ----
    if (this.showSsh) {
      var field = (label, key, ph) => h('div', {
        staticStyle: { flexDirection: 'row', alignItems: 'center', marginBottom: '5px' },
      }, [
        h('text', { staticStyle: { color: '#8b949e', fontSize: '14px', width: '64px' } }, label),
        h('text', {
          staticStyle: { flex: 1, color: this[key] ? '#e6edf3' : '#484f58', fontSize: '14px', height: '28px', backgroundColor: '#0d1117' },
          on: { click: () => this.openKeyboardFor(key) },
        }, this[key] ? this[key] : ph),
      ]);
      children.push(h('div', {
        staticStyle: {
          position: 'absolute', top: '30px', left: '140px', width: '680px',
          backgroundColor: '#161b22', paddingTop: '8px', paddingLeft: '12px', paddingRight: '12px', paddingBottom: '8px',
          borderWidth: '1px', borderColor: '#30363d', borderRadius: '8px',
        },
      }, [
        h('text', { staticStyle: { color: '#d29922', fontSize: '15px', marginBottom: '6px' } }, 'SSH 登录（点字段用键盘输入）'),
        field('主机', 'sshHost', '192.168.1.10'),
        field('用户', 'sshUser', 'root'),
        field('端口', 'sshPort', '22'),
        field('密码', 'sshPass', '留空则用密钥'),
        field('密钥', 'sshKey', '/userdisk/ssh/id_ed25519'),
        h('div', { staticStyle: { flexDirection: 'row', marginTop: '4px' } }, [
          h('text', { staticStyle: { color: '#3fb950', fontSize: '15px', marginRight: '16px' }, on: { click: () => this.connectSsh() } }, '连接'),
          h('text', { staticStyle: { color: '#8b949e', fontSize: '15px' }, on: { click: () => this.toggleSsh() } }, '取消'),
        ]),
      ]));
    }

    return h('div', { staticStyle: root }, children);
  },
};
