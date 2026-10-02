import PageIndex from './index.js';
import PageShell from './shell.js';
import Component from './Component.js';
import { BasePage } from './BasePage.js';

function __loadModuleDefault(m) { return (m && typeof m === 'object' && m.default !== undefined) ? m.default : m; }
globalThis.__loadModuleDefault = __loadModuleDefault;
function tlog(m) { try { console.warn('[term] A ' + m); } catch (e) {} }
var __pages = { index: Component, shell: Component };
try { console.warn('[term] __pages 类型: index=' + typeof __pages.index + ' shell=' + typeof __pages.shell); } catch (e) {}
class App extends $falcon.App {
  constructor() { super(); tlog('ctor'); }
  onLaunch(o) { super.onLaunch(o || {}); this.setViewPort(960); $falcon.useDefaultBasePageClass(BasePage); tlog('onLaunch ok'); }
  onShow() { super.onShow(); tlog('onShow'); }
  onHide() { super.onHide(); tlog('onHide'); }
  onDestroy() { super.onDestroy(); tlog('onDestroy'); }
}
App.meta = {
  name: '终端', version: '8.3.0', isSingleJsBundle: false,
  pages: { index: 'pages/index/index.vue', shell: 'pages/index/shell.vue' },
  options: { style: { lessPaths: ['styles'] } },
};
$falcon.__pages = __pages;
$falcon.__loadModuleDefault = __loadModuleDefault;
$falcon.__KEYFRAMES = $falcon.__KEYFRAMES || {};
$falcon.__AppClazz = App;
try { console.warn('[term] 页面类: ' + (PageIndex && PageIndex.name) + ' / ' + (PageShell && PageShell.name)); } catch (e) {}
tlog('registered');
export default App;