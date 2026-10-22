// 页面主世界（MAIN world）的网络嗅探。
//
// 为什么单独一个文件：chrome.scripting.executeScript 默认注入 ISOLATED world，
// 在那里包 window.fetch 抓不到页面自己的调用（两个独立的 JS 环境）。这个文件用
// world:'MAIN' 注入，包完把结果 postMessage 给隔离世界，再由 capture.js 转给后台。
// 只上报 URL + 方法，不碰请求头、不碰请求体。
(() => {
  if (window.__lcsNetHooked) return;
  window.__lcsNetHooked = true;

  const post = (url, method) => {
    try {
      window.postMessage({ __lcsNet: true, url: String(url).slice(0, 300), method: method || 'GET' }, '*');
    } catch (_) {}
  };

  const of = window.fetch;
  if (typeof of === 'function') {
    window.fetch = function (input, init) {
      try {
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        post(url, (init && init.method) || (input && input.method) || 'GET');
      } catch (_) {}
      return of.apply(this, arguments);
    };
  }

  const XO = window.XMLHttpRequest;
  if (XO && XO.prototype) {
    const open = XO.prototype.open;
    XO.prototype.open = function (method, url) {
      try { post(url, method); } catch (_) {}
      return open.apply(this, arguments);
    };
  }
})();
