// glueboot.js —— 框架桩：模拟官方 app.js.bin 需要的运行环境，并记录它的每一次写入
// 由 gluetrace 在官方 bundle 之前执行；之后调用 __snapshot() / __report() 出报告。
(function () {
  var T = globalThis.__trace;
  var sets = [];      // $falcon 上的写入（按顺序）
  var calls = [];     // 被调用的框架方法
  var metaVal;        // App.meta 的内容（关键！页面清单在这里）
  var appClazz;       // $falcon.__AppClazz
  var basePage;       // useDefaultBasePageClass 给的基类
  var keyframes;      // $falcon.__KEYFRAMES

  function jlog(tag, detail) { try { T(tag, detail === undefined ? '' : String(detail)); } catch (e) {} }

  function preview(v) {
    try {
      if (v === undefined) return 'undefined';
      if (v === null) return 'null';
      var t = typeof v;
      if (t === 'string') return 'str(' + v.length + ')"' + v.slice(0, 60) + '"';
      if (t === 'number' || t === 'boolean') return t + ' ' + String(v);
      if (t === 'function') return 'fn/' + (v.name || '?') + '/' + v.length;
      if (t === 'object') {
        var ks = Object.keys(v);
        return 'obj{' + ks.slice(0, 14).join(',') + (ks.length > 14 ? ',...' : '') + '}';
      }
      return t;
    } catch (e) { return 'ERR'; }
  }

  /* ---- 框架 App / Page 桩 ---- */
  var AppBase = class AppBase {
    constructor() { jlog('$falcon.App.ctor', ''); }
    onLaunch(o) { jlog('App.onLaunch', preview(o)); }
    onShow() { jlog('App.onShow', ''); }
    onHide() { jlog('App.onHide', ''); }
    onDestroy() { jlog('App.onDestroy', ''); }
    setViewPort(w) { calls.push('setViewPort(' + w + ')'); jlog('App.setViewPort', String(w)); }
    static set meta(v) { metaVal = v; jlog('App.meta 被赋值', preview(v)); }
    static get meta() { return metaVal; }
  };

  var PageBase = class PageBase {
    constructor() { jlog('$falcon.Page.ctor', ''); }
    onLoad() {} onNewOptions() {} onShow() {} onHide() {} onUnload() {}
    beforeVueInstantiate() { calls.push('beforeVueInstantiate'); }
    setRootComponent() { calls.push('setRootComponent'); }
    sleep() { return Promise.resolve(); }
  };

  var base = {
    App: AppBase, Page: PageBase,
    JSAPI: {}, jsapi: {}, env: { NODE_ENV: 'production' }, util: {},
    eventMap: {}, _modules: {}, _pageMap: {}, _serviceMap: {}, _uniqueId: 0,
    $_appInfo: { appId: 'test', appPath: '/tmp' },
    $workspace: '/tmp/ws', $dataDir: '/tmp/data', $appid: 'test',
    __NAVIGATOR: {}, __JSAPI: {},
    on: function (n) { calls.push('on(' + n + ')'); return 1; },
    off: function () {}, trigger: function () {}, emit: function () {},
    navTo: function () { calls.push('navTo'); },
    closeApp: function () {}, closePageByName: function () {}, closePageById: function () {},
    $getTopApp: function () { return null; },
    useDefaultBasePageClass: function (cls) {
      basePage = cls;
      calls.push('useDefaultBasePageClass(' + (cls && cls.name) + ')');
      jlog('useDefaultBasePageClass', cls && cls.name);
    },
  };

  var proxy = new Proxy(base, {
    get: function (t, k) {
      if (!(k in t)) jlog('$falcon.GET 缺失字段', String(k));
      return t[k];
    },
    set: function (t, k, v) {
      sets.push(String(k) + ' = ' + preview(v));
      jlog('$falcon.SET', String(k) + ' = ' + preview(v));
      if (k === '__AppClazz') appClazz = v;
      if (k === '__KEYFRAMES') keyframes = v;
      t[k] = v;
      return true;
    },
  });

  globalThis.$falcon = proxy;
  globalThis.$_Falcon = proxy;

  /* ---- 模块加载器桩：看官方 bundle 到底 require 了哪些页面/模块 ---- */
  globalThis.__MODULELOADER = new Proxy({}, {
    get: function (t, k) {
      if (k === '__proto__' || k === 'constructor' || k === 'then') return t[k];
      if (!t[k]) {
        t[k] = function () {
          var a = Array.prototype.slice.call(arguments).map(preview).join(' , ');
          jlog('MODULELOADER.' + String(k), a);
          return { ok: true, default: {}, keys: function () { return []; } };
        };
      }
      return t[k];
    },
  });

  globalThis.window = globalThis;
  globalThis.process = { env: { NODE_ENV: 'production' } };
  globalThis.requestAnimationFrame = function () { return 1; };
  globalThis.cancelAnimationFrame = function () {};

  /* ---- 记录 bundle 新加了哪些全局量 ---- */
  var before = {};
  Object.getOwnPropertyNames(globalThis).forEach(function (n) { before[n] = 1; });
  globalThis.__added = [];
  globalThis.__snapshot = function () {
    var out = [];
    Object.getOwnPropertyNames(globalThis).forEach(function (n) { if (!before[n]) out.push(n); });
    globalThis.__added = out;
  };

  /* ---- 报告 ---- */
  globalThis.__report = function () {
    jlog('=== $falcon 写入序列 ===', sets.length + ' 次');
    for (var i = 0; i < sets.length; i++) jlog('SET#' + i, sets[i]);
    jlog('=== 调用过的框架方法 ===', calls.join(' ; '));
    jlog('=== App.meta ===', '');
    if (metaVal) {
      jlog('  meta.keys', Object.keys(metaVal).join(','));
      if (metaVal.pages) {
        var p = metaVal.pages, ks = Object.keys(p);
        jlog('  meta.pages.keys', ks.join(','));
        for (var j = 0; j < Math.min(ks.length, 8); j++) jlog('    pages[' + ks[j] + ']', preview(p[ks[j]]));
      } else {
        jlog('  meta.pages', String(metaVal.pages));
      }
      jlog('  meta.options', preview(metaVal.options));
      jlog('  meta 其它字段', Object.keys(metaVal).filter(function (k) { return k !== 'pages' && k !== 'options'; })
        .map(function (k) { return k + '=' + preview(metaVal[k]); }).join(' ; '));
    } else {
      jlog('  (App.meta 未被赋值)', '');
    }
    jlog('=== App 类原型 ===', appClazz ? (appClazz.name + ' : ' + Object.getOwnPropertyNames(appClazz.prototype || {}).join(',')) : 'n/a');
    jlog('=== 基类页面原型 ===', basePage ? Object.getOwnPropertyNames(basePage.prototype || {}).join(',') : 'n/a');
    jlog('=== __KEYFRAMES ===', preview(keyframes));
    jlog('=== bundle 新增的全局量 ===', (globalThis.__added || []).join(','));
    jlog('=== 报告结束 ===', '');
  };

  jlog('桩就绪', 'globalThis 属性数=' + Object.getOwnPropertyNames(globalThis).length);
})();
