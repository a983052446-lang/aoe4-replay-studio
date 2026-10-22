// 后台：录制开关、注入采集脚本、缓冲动作、转发给本地工作台或导出文件。
const STATE = {
  recording: false,
  startedAt: 0,
  actions: [],
  nativePort: null,
  injected: new Set(),
  tabId: null,          // 只录一个标签页：别的标签页的动作混进来会被回放到错误的页面
};
// 端口不能写死：服务默认 8787，但被占用时会顺延（见 run.py 的 find_free_port），
// 用户在设置里也可能改成别的值。所以先探 /api/ping，探到就记进 storage。
const PORT_RANGE = Array.from({ length: 12 }, (_, i) => 8787 + i);
const PROBE_TIMEOUT = 400;         // 每个候选端口最多等这么久（关着的端口可能不是立刻 refused）
let ENDPOINT = null;
let DISCOVERING = null;

async function pingPort(port) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT);
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/ping`, { cache: 'no-store', signal: ctrl.signal });
    if (!r.ok) return null;
    const j = await r.json().catch(() => null);
    return j && j.app === 'aoe4-replay-studio' ? port : null;
  } catch (_) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function discover() {
  const { savedPort } = await chrome.storage.local.get('savedPort');
  const ports = savedPort && PORT_RANGE.includes(savedPort)
    ? [savedPort, ...PORT_RANGE.filter((p) => p !== savedPort)]
    : PORT_RANGE;
  for (const p of ports) {
    const hit = await pingPort(p);
    if (hit) {
      ENDPOINT = `http://127.0.0.1:${hit}/api/recorder/ingest`;
      chrome.storage.local.set({ savedPort: hit });
      return ENDPOINT;
    }
  }
  return null;
}

function endpoint() {
  if (ENDPOINT) return Promise.resolve(ENDPOINT);
  if (!DISCOVERING) {
    // 并发调用共用同一次探测，别让几个调用各扫一遍端口
    DISCOVERING = discover().finally(() => { DISCOVERING = null; });
  }
  return DISCOVERING;
}

// MV3 的 service worker 随时可能被挂起，回来时 STATE 是空的。
// 所有入口都先等这个 promise，否则「刚点开始录制就切页」那一下会看到
// recording=false，注入直接跳过 —— 录制开头就这么丢了。
const READY = new Promise((resolve) => {
  chrome.storage.local.get(['recording', 'startedAt', 'actions', 'tabId'], (v) => {
    STATE.recording = !!v.recording;
    STATE.startedAt = v.startedAt || 0;
    STATE.actions = Array.isArray(v.actions) ? v.actions : [];
    STATE.tabId = v.tabId || null;
    updateBadge();
    resolve();
  });
});

function updateBadge() {
  if (STATE.recording) {
    chrome.action.setBadgeText({ text: 'REC' });
    chrome.action.setBadgeBackgroundColor({ color: '#c25555' });
  } else {
    chrome.action.setBadgeText({ text: '' });
  }
}

function persist() {
  chrome.storage.local.set({
    recording: STATE.recording,
    startedAt: STATE.startedAt,
    tabId: STATE.tabId,
    actions: STATE.actions.slice(-2000),
  });
}

function dispatchToNative(payload) {
  if (!STATE.nativePort) {
    try {
      STATE.nativePort = chrome.runtime.connectNative('com.lcs.aoe4recorder');
      STATE.nativePort.onDisconnect.addListener(() => {
        console.warn('native host disconnected:', chrome.runtime.lastError && chrome.runtime.lastError.message);
        STATE.nativePort = null;
      });
    } catch (e) {
      STATE.nativePort = null;
    }
  }
  if (STATE.nativePort) {
    try { STATE.nativePort.postMessage(payload); return true; } catch (e) { STATE.nativePort = null; }
  }
  return false;
}

async function ingest(payload) {
  const ep = await endpoint();
  if (!ep) return dispatchToNative(payload);
  try {
    const r = await fetch(ep, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    // 4xx/5xx 不能当成功：老代码只看 fetch 有没有抛异常，
    // 结果服务端拒了也当作已入库，动作就这么没了。
    if (r.ok) return true;
    ENDPOINT = null;   // 端口变了或服务重启了，下次重新探
  } catch (e) {
    ENDPOINT = null;
  }
  return dispatchToNative(payload);
}

async function inject(tabId) {
  if (STATE.injected.has(tabId)) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['capture.js'] });
    // 网络嗅探必须进 MAIN world，否则包到的 window.fetch 不是页面那份（等于没包）
    await chrome.scripting.executeScript({ target: { tabId }, files: ['capture_net.js'], world: 'MAIN' });
    STATE.injected.add(tabId);
  } catch (e) {
    // 受限页面（chrome://、Web Store 等）直接跳过
  }
}

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  await READY;
  if (!STATE.recording || info.status !== 'complete' || !tab || !tab.url) return;
  if (STATE.tabId && tabId !== STATE.tabId) return;
  if (!/^https?:/.test(tab.url)) return;
  await inject(tabId);
});

chrome.action.onClicked.addListener(async (tab) => {
  await READY;
  STATE.recording = !STATE.recording;
  if (STATE.recording) {
    STATE.startedAt = Date.now();
    STATE.actions = [];
    STATE.tabId = (tab && tab.id) || null;
    if (tab && tab.id) await inject(tab.id);
  } else {
    persist();
    const payload = {
      kind: 'lcs-action-script',
      name: 'chrome-' + new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-'),
      created: new Date().toISOString(),
      actions: STATE.actions,
    };
    await ingest(payload);
    // 同时留一份本地文件：manifest 里必须有 "downloads"，否则 chrome.downloads 是
    // undefined，这里会静默失败（老版本就是这样，导出从来没成过）。
    if (chrome.downloads) {
      const blob = new Blob([JSON.stringify(payload, null, 1)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      try {
        await chrome.downloads.download({ url, filename: 'aoe4-actions-' + Date.now() + '.json' });
      } catch (e) {
        console.warn('导出文件失败:', e && e.message);
      } finally {
        setTimeout(() => URL.revokeObjectURL(url), 30000);
      }
    }
  }
  updateBadge();
  persist();
});

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg && msg.type === 'lcs-event') {
    if (!STATE.recording) return;
    // 只收录制那个标签页的事件：多标签混进来，回放时点不到正确的位置
    if (STATE.tabId && sender && sender.tab && sender.tab.id !== STATE.tabId) return;
    const ev = msg.event;
    if (!ev || typeof ev !== 'object') return;
    ev.t = Date.now() - STATE.startedAt;
    const prev = STATE.actions.length ? STATE.actions[STATE.actions.length - 1].t : 0;
    ev.delay = Math.max(0, ev.t - prev);
    STATE.actions.push(ev);
    if (STATE.actions.length % 10 === 0) persist();
    return;
  }
  if (msg && msg.type === 'lcs-export') {
    reply({ recording: STATE.recording, count: STATE.actions.length });
  }
});

chrome.runtime.onInstalled.addListener(() => {
  READY.then(() => updateBadge());
});
