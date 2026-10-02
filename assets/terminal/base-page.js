import Component from './Component.js';
// BasePage.js —— 页面基类（独立块文件，官方 app 里对应 BasePage-<hash>.js）
// 形态与 PenBili 的 src/base-page.js 一致（真机验证过的写法）：
// 跟踪 $falcon.on token / timeout / interval，在 onUnload 的 finally 里释放；
// onShow/onHide/onUnload 把生命周期转发给 $root（Vue 实例）。
// 加了 try/catch 加固，避免某一步抛错导致整页不挂载。

class ResourcePage extends $falcon.Page {
  constructor() {
    super();
    this.falconOnTokens = [];
    this.timeoutTokens = new Set();
    this.intervalTokens = new Set();
  }

  on(name, callback) {
    const token = $falcon.on(name, callback);
    this.falconOnTokens.push([token, name]);
    return token;
  }

  off(name, callbackOrToken) {
    $falcon.off(name, callbackOrToken);
    this.falconOnTokens = this.falconOnTokens.filter((item) => item[0] !== callbackOrToken);
  }

  setTimeout(callback, delay) {
    const token = setTimeout(() => {
      this.timeoutTokens.delete(token);
      callback();
    }, delay);
    this.timeoutTokens.add(token);
    return token;
  }

  clearTimeout(token) {
    clearTimeout(token);
    this.timeoutTokens.delete(token);
  }

  setInterval(callback, delay) {
    const token = setInterval(callback, delay);
    this.intervalTokens.add(token);
    return token;
  }

  clearInterval(token) {
    clearInterval(token);
    this.intervalTokens.delete(token);
  }

  sleep(delay) {
    return new Promise((resolve) => this.setTimeout(resolve, delay));
  }

  release() {
    this.falconOnTokens.forEach((item) => $falcon.off(item[1], item[0]));
    this.falconOnTokens = [];
    this.timeoutTokens.forEach((token) => clearTimeout(token));
    this.intervalTokens.forEach((token) => clearInterval(token));
    this.timeoutTokens.clear();
    this.intervalTokens.clear();
  }
}

function attachRoot(page) {
  try {
    if (page && typeof page.setRootComponentOptions === 'function') {
      page.setRootComponentOptions(Component);
      try { console.warn('[term-ui] 基类挂载: setRootComponentOptions 已调用'); } catch (e) {}
      return true;
    }
  } catch (e) { try { console.warn('[term-ui] setRootComponentOptions 抛错 ' + e); } catch (e2) {} }
  try {
    if (page && typeof page.setRootComponent === 'function') {
      page.setRootComponent(Component);
      try { console.warn('[term-ui] 基类挂载: setRootComponent 已调用'); } catch (e) {}
      return true;
    }
  } catch (e) { try { console.warn('[term-ui] setRootComponent 抛错 ' + e); } catch (e2) {} }
  return false;
}

export class BasePage extends ResourcePage {
  onLoad(options) {
    try { super.onLoad(options); } catch (e) { try { console.warn('[BasePage] onLoad super 抛错 ' + e); } catch (e2) {} }
    this.options = options || {};
    try { console.warn('[term-ui] BasePage.onLoad 到'); } catch (e) {}
    attachRoot(this);
  }

  onNewOptions(options) {
    try { super.onNewOptions(options); } catch (e) {}
    this.options = options || {};
    attachRoot(this);
  }

  onShow() {
    try { super.onShow(); } catch (e) {}
    if (this.$root && this.$root.onShow) this.$root.onShow();
  }

  onHide() {
    try { super.onHide(); } catch (e) {}
    if (this.$root && this.$root.onHide) this.$root.onHide();
  }

  onUnload() {
    try {
      try { super.onUnload(); } catch (e) {}
      if (this.$root && this.$root.onUnload) this.$root.onUnload();
    } finally {
      this.release();
    }
  }

  beforeVueInstantiate(Vue) {
    if (super.beforeVueInstantiate) super.beforeVueInstantiate(Vue);
    Vue.prototype.$workspace = globalThis.$workspace;
    Vue.prototype.$appid = globalThis.$appid;
  }
}

export default BasePage;
