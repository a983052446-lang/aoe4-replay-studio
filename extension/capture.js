// 页面侧采集：只上报结构性动作（坐标、按键、元素描述、请求元信息），不读取页面正文。
//
// 注意：这个文件默认跑在 ISOLATED world（和页面共享 DOM、不共享 JS 环境）。
// 所以：
//   * 这里的 addEventListener 能抓到真实事件 —— 没问题
//   * 这里包的 window.fetch 抓到的是「扩展自己那份 window」，页面的调用根本走不到 —— 白包
// 真实的网络调用由 capture_net.js（MAIN world）负责，用 postMessage 递进来。
(() => {
  if (window.__lcsExtInstalled) return 'already';
  window.__lcsExtInstalled = true;

  const desc = (el) => {
    if (!el) return '';
    const t = (el.innerText || el.getAttribute('aria-label') || el.title || el.value || '').trim();
    return (el.tagName || '').toLowerCase() + (el.id ? '#' + el.id : '') + (t ? ' · ' + t.slice(0, 40) : '');
  };
  const norm = (x, y) => [+(x / Math.max(1, innerWidth)).toFixed(4), +(y / Math.max(1, innerHeight)).toFixed(4)];
  const cssPath = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < 5) {
      let s = cur.tagName.toLowerCase();
      if (cur.id) { s += '#' + cur.id; parts.unshift(s); break; }
      const cls = (cur.className && typeof cur.className === 'string') ? cur.className.trim().split(/\s+/)[0] : '';
      if (cls) s += '.' + cls;
      parts.unshift(s); cur = cur.parentElement; depth++;
    }
    return parts.join(' > ');
  };
  const send = (e) => {
    e.ts = Date.now();
    e.url = location.href;
    e.viewport = { w: innerWidth, h: innerHeight, dpr: devicePixelRatio };
    try { chrome.runtime.sendMessage({ type: 'lcs-event', event: e }); } catch (_) {}
  };

  // 密码 / 支付 / 一次性验证码这类字段：键名和值一律不记录。
  // 动作录制器没有理由拿到明文口令，录下来还会进 chrome.storage、本地 HTTP 和导出 JSON。
  const SENSITIVE_TYPE = /^(password)$/i;
  const SENSITIVE_AUTOCOMPLETE = /(password|cc-number|cc-csc|cc-exp|one-time-code|current-password|new-password)/i;
  const isSensitive = (el) => {
    if (!el || el.nodeType !== 1) return false;
    if (SENSITIVE_TYPE.test(el.type || '')) return true;
    const ac = (el.getAttribute && el.getAttribute('autocomplete')) || '';
    if (SENSITIVE_AUTOCOMPLETE.test(ac)) return true;
    // 有些站点用 contenteditable + aria 做密码框
    const name = `${el.name || ''} ${el.id || ''} ${el.getAttribute && (el.getAttribute('aria-label') || '')}`;
    return /pass(word)?|pwd|otp|验证码|密码/i.test(name);
  };

  addEventListener('mousedown', (ev) => {
    // 点密码框本身不算敏感（坐标+选择器而已），但别把框里的值写进 target 描述
    const t = ev.target;
    const [nx, ny] = norm(ev.clientX, ev.clientY);
    send({
      kind: 'click', button: ev.button, x: ev.clientX, y: ev.clientY, nx, ny,
      target: isSensitive(t) ? `${(t.tagName || '').toLowerCase()} · <sensitive>` : desc(t),
      selector: cssPath(t),
    });
  }, true);

  addEventListener('keydown', (ev) => {
    const t = ev.target;
    const sensitive = isSensitive(t);
    send({
      kind: 'key',
      key: sensitive ? '<sensitive>' : ev.key,
      code: sensitive ? '<sensitive>' : ev.code,
      ctrl: ev.ctrlKey, shift: ev.shiftKey, alt: ev.altKey, meta: ev.metaKey,
      target: sensitive ? '<sensitive>' : desc(t),
    });
  }, true);

  addEventListener('input', (ev) => {
    const t = ev.target;
    if (!t || typeof t.value !== 'string') return;
    if (isSensitive(t)) {
      send({ kind: 'input', value: '<sensitive>', selector: cssPath(t), target: '<sensitive>' });
      return;
    }
    send({ kind: 'input', value: t.value.slice(0, 200), selector: cssPath(t), target: desc(t) });
  }, true);

  let tick = 0;
  addEventListener('scroll', () => {
    const now = Date.now();
    if (now - tick < 120) return;
    tick = now;
    send({ kind: 'scroll', x: scrollX, y: scrollY });
  }, { passive: true });

  // MAIN world 的嗅探脚本通过 postMessage 递进来的网络事件
  addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.__lcsNet !== true) return;
    send({ kind: 'fetch', url: String(d.url || '').slice(0, 300), method: d.method || 'GET' });
  }, false);

  return 'installed';
})();
