/* 问答大厅 —— 渲染层 1:1 复刻原版（档案卡片消息 / F-编号会话 / 行李标签提问），
   业务层对接本项目的认证 / 会话 / 流式问答 / 推演 API。 */
import { Api } from '../api.js';
import { Auth } from '../auth.js';
import { VideoLoop } from '../theme.js';
import { esc, mdLite } from '../markdown.js';
import { playGate } from '../ui/gate.js';
import { initEmblem } from '../ui/emblem.js';
import { toast } from '../ui/toast.js';
import { initParallax } from '../ui/parallax.js';

const $ = s => document.querySelector(s);
const chatInner = $('#chatInner');
const chat = $('#chat');

/* ---------- 初始化 ---------- */
const me = Auth.guard();
if (!me) throw new Error('unauthorized');
playGate({ once: true });
initParallax();

const videoLoop = new VideoLoop($('#bgVideo'));
$('#vmModal').addEventListener('click', e => {
  const b = e.target.closest('button[data-vm]');
  if (b) videoLoop.setMode(b.dataset.vm);
});

/* ---------- 状态 ---------- */
const S = { sessionId: null, title: '', messages: [], agent: false, sim: false, sending: false, ratedKeys: new Set() };

function relTime(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return '刚刚';
  if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
  return Math.floor(d / 86400) + ' 天前';
}

function scrollBottom() {
  chat.scrollTop = chat.scrollHeight;
}

/* ---------- 徽标 / 致谢 ---------- */
initEmblem($('#emblem-btn'), { onTriple: () => $('#thanks').classList.add('show') });
$('#thanksClose').addEventListener('click', () => $('#thanks').classList.remove('show'));
$('#thanks').addEventListener('click', e => {
  if (e.target === $('#thanks')) $('#thanks').classList.remove('show');
});
addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    $('#thanks').classList.remove('show');
    $('#intro').classList.remove('show');
    closeModal();
  }
});

/* ---------- 侧栏抽屉（移动端） ---------- */
const drawer = matchMedia('(max-width: 900px)');
function setDrawer(open) {
  $('#sidebar').classList.toggle('drawer-open', open);
  $('#scrim').classList.toggle('show', open);
}
$('#foldBtn').addEventListener('click', () => {
  if (drawer.matches) setDrawer(!$('#sidebar').classList.contains('drawer-open'));
  else $('#sidebar').classList.toggle('collapsed');
});
$('#scrim').addEventListener('click', () => setDrawer(false));

/* ---------- 会话 ---------- */
async function refreshSessions() {
  try {
    const r = await Api.request('/conversations');
    const box = $('#sessionList');
    box.innerHTML = '';
    let si = 0;
    for (const s of r.conversations || []) {
      const d = document.createElement('div');
      d.className = 'sess-item' + (s.id === S.sessionId ? ' active' : '');
      d.style.animationDelay = `${Math.min(si * 40, 240)}ms`;
      si += 1;
      d.innerHTML = `<div class="t"><span class="sno">F-${esc((s.id || '').slice(0, 4))}</span> ${esc(s.title || '新对话')}</div><div class="m">${s.msg_count} 条 · ${relTime(s.updated_at)}</div><button class="del" title="删除">✕</button>`;
      d.querySelector('.del').addEventListener('click', async ev => {
        ev.stopPropagation();
        await Api.request(`/conversations/${s.id}`, { method: 'DELETE' });
        if (s.id === S.sessionId) newChat();
        refreshSessions();
      });
      d.addEventListener('click', () => { openSession(s.id); if (drawer.matches) setDrawer(false); });
      box.appendChild(d);
    }
  } catch (e) { console.warn('sessions', e); }
}

function newChat() {
  S.sessionId = null; S.title = ''; S.messages = []; S.ratedKeys = new Set();
  $('#convTitle') && ($('#convTitle').textContent = '');
  chatInner.innerHTML = WELCOME_HTML;
  bindWelcome();
  refreshSessions();
}

async function openSession(id) {
  try {
    const s = await Api.request(`/conversations/${id}`);
    S.sessionId = id;
    S.title = s.title || '';
    chatInner.innerHTML = '';
    // 历史消息整批重渲染时跳过入场动画, 避免"所有卡片同时弹入"
    chatInner.classList.add('no-anim');
    S.messages = (s.messages || []).map(m => ({ role: m.role, content: m.content, meta: m.meta || {} }));
    try {
      const rk = await Api.request(`/feedback/rated?conv_id=${encodeURIComponent(id)}`);
      S.ratedKeys = new Set(rk.keys || []);
    } catch { S.ratedKeys = new Set(); }
    for (const m of S.messages) renderStored(m);
    requestAnimationFrame(() => chatInner.classList.remove('no-anim'));
    scrollBottom();
    refreshSessions();
  } catch (e) { toast('会话读取失败', { err: true }); }
}

async function persistSession() {
  if (!S.messages.length) return;
  try {
    const title = S.title || (S.messages[0]?.content || '新对话').slice(0, 24);
    const r = await Api.request('/conversations', {
      method: 'POST',
      json: { id: S.sessionId, title, kind: 'qa', messages: S.messages },
    });
    S.sessionId = r.id;
    S.title = title;
    refreshSessions();
  } catch (e) { console.warn('persist', e); }
}

$('#newChat').addEventListener('click', newChat);

/* ---------- 渲染（1:1 原版结构） ---------- */
const WELCOME_HTML = buildWelcome().outerHTML;
function hideWelcome() { const w = $('#welcome'); if (w) w.remove(); }

function bindWelcome() {
  const w = $('#welcome');
  if (!w) return;
  w.querySelectorAll('.quick button').forEach(b => {
    b.addEventListener('click', () => { $('#q').value = b.dataset.q; ask(); });
  });
}

function buildWelcome() {
  const wrap = document.createElement('div');
  wrap.innerHTML = `
  <div id="welcome" class="deco-corners">
    <div class="vtext">ARCHIVE</div>
    <div class="restricted-stamp">RESTRICTED<br>内部档案</div>
    <div class="oath">我们寻觅，我们援助，我们教导。</div>
    <div class="oath-sub">我们宣誓不稳定的力量将为和平与光辉使用。</div>
    <div class="title">重返未来<em>：</em>1999</div>
    <div class="sub">REVERSE : 1999 · KNOWLEDGE ARCHIVE</div>
    <div class="diamond-sep"><span class="dm"></span></div>
    <div class="modes">
      <div class="mode-card" data-idx="Ⅰ"><b>数值查询</b><span>严格模式：只答资料所载，附来源引用</span></div>
      <div class="mode-card" data-idx="Ⅱ"><b>剧情因果</b><span>推理模式：区分事实与推断，叙事分析</span></div>
      <div class="mode-card" data-idx="Ⅲ"><b>全库统计</b><span>Agent 深度分析：穷尽名单，多轮工具调用</span></div>
    </div>
    <div class="quick">
      <button data-q="卡邦克鲁是什么？">卡邦克鲁是什么</button>
      <button data-q="有多少角色闻起来是木质香调的？">木质香调角色统计</button>
      <button data-q="详细说明平衡伞是如何被开发出来的？">平衡伞的开发历程</button>
      <button data-q="芝诺参与的规模最大战斗取得了怎样的战果？">芝诺最大战果</button>
    </div>
  </div>`;
  return wrap.firstElementChild;
}

function modeTagHtml(meta) {
  const isAgent = meta.mode === 'agent';
  const modeTag = isAgent ? '🧠 Agent 深度分析 · ' + esc(meta.model || '')
    : meta.mode === 'extractive' ? '证据模式（未配置 LLM）'
    : meta.mode === 'simulate' ? '🎬 剧情推演报告'
    : (meta.mode ? 'LLM 生成 · ' + esc(meta.model || '') : '回答');
  const styleTag = meta.style === 'reason' ? ' · 推理模式（含标注推断）' : '';
  const em = meta.entity_meta;
  const entTag = (!isAgent && meta.mode !== 'simulate' && em)
    ? ` · <a href="${esc(em.url || '#')}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(em.type_name || '')}${em.type_name ? '·' : ''}${esc(em.title)}</a>` : '';
  return `<span class="mode-tag${meta.style === 'reason' ? ' reason' : ''}">${modeTag}${styleTag}${entTag}</span>`;
}

function avatarHtml(meta) {
  const isAgent = meta.mode === 'agent';
  const em = meta.entity_meta;
  return (!isAgent && meta.mode !== 'simulate' && em && em.image)
    ? `<div class="avatar-wrap"><img class="avatar" referrerpolicy="no-referrer" src="${esc(em.image)}" alt="${esc(em.title || '')}" onerror="this.parentNode.style.display='none'"></div>` : '';
}

function logHtml(steps, label) {
  if (!steps?.length) return '';
  return `<details class="srcs"><summary>${esc(label)}（${steps.length} 条，点击展开）</summary><div class="alog">${steps.map((l, j) => `<b>${j + 1}.</b> ${esc(l)}`).join('<br>')}</div></details>`;
}

function srcHtml(sources) {
  if (!sources || !sources.length) return '';
  let h = `<details class='srcs'><summary>📎 引用来源（${sources.length} 条，点击展开）</summary>`;
  for (const s of sources) {
    const link = s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.page)}</a>` : esc(s.page);
    h += `<div class="src-item"><span class="sn">[${s.n}]</span>${link} · ${esc(s.section)} <span style="opacity:.6">score ${s.score}</span><div class="snippet">${esc(s.snippet || '')}</div></div>`;
  }
  return h + '</details>';
}

function botFinalHtml(meta, content) {
  const isPlain = meta.mode === 'agent' || meta.mode === 'simulate';
  const stepLabel = meta.mode === 'simulate' ? '📋 推演过程' : '📋 Agent 工作记录';
  return `<span class="vtag">ARCHIVE</span>${modeTagHtml(meta)}${logHtml(meta.steps, stepLabel)}`
    + `<div class="streaming-wrap">${avatarHtml(meta)}<div style="flex:1;min-width:0"><div class="streaming">${mdLite(content)}</div></div></div>`
    + (isPlain ? '' : srcHtml(meta.sources));
}

function renderStored(m) {
  hideWelcome();
  if (m.role === 'user') {
    const d = document.createElement('div');
    d.className = 'msg user';
    d.innerHTML = esc(m.content);
    chatInner.appendChild(d);
    return;
  }
  const meta = m.meta || {};
  const d = document.createElement('div');
  d.className = 'msg bot';
  d.innerHTML = botFinalHtml(meta, m.content || '');
  if (meta.report_path) {
    d.insertAdjacentHTML('beforeend',
      `<p style="font-size:11px;color:var(--muted)">报告已存档：${esc(meta.report_path)}</p>`);
  }
  chatInner.appendChild(d);
  if (meta.answer_key) {
    d.insertAdjacentHTML('beforeend', ratingRowHtml(S.sessionId, meta.answer_key,
      (m.content || '').slice(0, 200), S.ratedKeys.has(meta.answer_key)));
    bindRateRow(d.lastElementChild, () => S.sessionId, meta.answer_key,
      (m.content || '').slice(0, 200));
  }
}

/* ---------- 回答评价 ---------- */
function ratingRowHtml(convId, key, snippet, rated) {
  if (rated) return '<div class="rate-row rated">感谢您的反馈</div>';
  return `<div class="rate-row">
    <span class="rate-ask">这条回答对您有帮助吗？</span>
    <button class="rate-btn" data-r="1">好评</button>
    <button class="rate-btn" data-r="-1">差评</button>
  </div>`;
}

function bindRateRow(row, convIdFn, key, snippet) {
  row.querySelectorAll('.rate-btn').forEach(b => {
    b.addEventListener('click', () => {
      if (row.classList.contains('rated')) return;
      const r = b.dataset.r;
      row.innerHTML = `<div class="rate-panel">
        <textarea class="rate-text" maxlength="1000" rows="2"
          placeholder="补充说明（可选）…"></textarea>
        <div class="rate-actions">
          <button class="rate-submit">提交反馈</button>
          <button class="rate-skip">跳过</button>
        </div></div>`;
      row.querySelector('.rate-submit').addEventListener('click', () =>
        sendRate(row, convIdFn(), key, snippet, Number(r),
                 row.querySelector('.rate-text').value));
      row.querySelector('.rate-skip').addEventListener('click', () =>
        sendRate(row, convIdFn(), key, snippet, Number(r), ''));
    });
  });
}

async function sendRate(row, convId, key, snippet, rating, content) {
  if (!convId) { toast('会话尚未保存，请稍后再试', { err: true }); return; }
  try {
    await Api.request('/feedback', {
      method: 'POST',
      json: { kind: 'answer', conv_id: convId, answer_key: key,
              answer_snippet: snippet, rating, content: content || '' },
    });
    row.innerHTML = '感谢您的反馈';
    row.classList.add('rated');
    S.ratedKeys.add(key);
  } catch (e) {
    if (String(e.message).includes('已评价')) {
      row.innerHTML = '感谢您的反馈';
      row.classList.add('rated');
      S.ratedKeys.add(key);
    } else {
      toast('提交失败：' + e.message, { err: true });
    }
  }
}

function botShell() {
  const d = document.createElement('div');
  d.className = 'msg bot';
  d.innerHTML = `<span class="vtag">ARCHIVE</span><div class="typing"><i></i><i></i><i></i></div>`;
  chatInner.appendChild(d);
  scrollBottom();
  return d;
}

/* ---------- 提问（流式） ---------- */
async function ask() {
  if (S.sending) return;
  const raw = $('#q').value.trim();
  if (!raw) return;
  S.sending = true;
  $('#send').disabled = true;
  hideWelcome();
  $('#q').value = '';
  const isSim = S.sim;
  const q = isSim ? `【剧情推演】${raw}` : raw;

  const ud = document.createElement('div');
  ud.className = 'msg user';
  ud.innerHTML = esc(q);
  chatInner.appendChild(ud);
  S.messages.push({ role: 'user', content: q, meta: {} });

  const shell = botShell();
  let fullText = '', meta = {}, agentLog = [], simLog = [], simCards = '';
  let live = '';

  // 组装多轮历史（最近 3 轮 user/assistant 对）
  const histPairs = [];
  for (let i = S.messages.length - 2; i >= 0 && histPairs.length < 3; i--) {
    const m = S.messages[i];
    if (m.role === 'assistant') histPairs.unshift({ q: '', a: m.content.slice(0, 400) });
    else if (m.role === 'user') {
      if (histPairs.length && histPairs[0].q === '') histPairs[0].q = m.content;
      else histPairs.unshift({ q: m.content, a: '' });
    }
  }

  const applyLive = () => {
    shell.innerHTML = `<span class="vtag">ARCHIVE</span>${meta.mode || meta.model ? modeTagHtml(meta) : ''}`
      + `<div class="agent-live">${live}</div>`
      + `<div class="streaming-wrap"><div style="flex:1;min-width:0"><div class="streaming">${mdLite(fullText)}</div></div></div>`;
    scrollBottom();
  };

  try {
    const path = isSim ? '/qa/simulate' : '/qa/ask';
    const body = isSim
      ? { theme: raw, turns: 3, conversation_id: S.sessionId }
      : { question: raw, top_k: 8, history: histPairs.filter(h => h.q),
          agent: S.agent ? true : null, conversation_id: S.sessionId };
    await Api.stream(path, body, ev => {
      const t = ev.type;
      if (t === 'agent_mode') {
        live += `<div class="step">进入 Agent 深度分析模式</div>`;
        applyLive();
      } else if (t === 'agent') {
        agentLog.push(ev.note || ev.tool || '');
        live += `<div class="step">${esc(ev.note || ev.tool || '')}</div>`;
        applyLive();
      } else if (t === 'meta') {
        meta = ev;
        applyLive();
      } else if (t === 'delta') {
        fullText += ev.text || '';
        applyLive();
      } else if (t === 'phase') {
        simLog.push(`【阶段】${ev.note || ev.phase}`);
        live += `<div class="step">${esc(ev.note || ev.phase || '')}</div>`;
        applyLive();
      } else if (t === 'collect_step') {
        simLog.push(ev.note || '');
        live += `<div class="step">${esc(ev.note || '')}</div>`;
        applyLive();
      } else if (t === 'worldview') {
        const wv = ev.data || {};
        const names = (wv.agents || []).map(a => a.name).join('、');
        simLog.push(`世界观构建完成：${names}`);
        live += `<div class="step">角色卡：${esc(names)}</div>`;
        applyLive();
      } else if (t === 'turn_start') {
        simLog.push(`—— 回合 ${ev.turn} ——`);
        simCards += `<div class="narrator-card" style="text-align:center;color:var(--gold)">—— 回合 ${ev.turn} ——</div>`;
        applyLive();
      } else if (t === 'action') {
        simLog.push(`${ev.agent}：${(ev.action || '').slice(0, 80)}`);
        simCards += `<div class="action-card"><div class="who">${esc(ev.agent)}</div>${esc(ev.action || '')}<div class="why">${esc(ev.reasoning || '')}</div></div>`;
        applyLive();
      } else if (t === 'narrator') {
        simLog.push(`叙述者：${(ev.content || '').slice(0, 80)}`);
        simCards += `<div class="narrator-card">${esc(ev.content || '')}</div>`;
        applyLive();
      } else if (t === 'branch') {
        simLog.push(`分支（${ev.data?.stance}）：${ev.data?.title || ''}`);
        simCards += `<div class="branch-item"><b>${esc(ev.data?.stance || '')}</b> · ${esc(ev.data?.title || '')} <span class="score">（${esc(String(ev.data?.score ?? 3))}/5）</span><div class="why">${esc(ev.data?.reasoning || '')}</div></div>`;
        applyLive();
      } else if (t === 'story_section') {
        fullText += `\n\n---\n\n## ${ev.section}\n\n`;
        applyLive();
      } else if (t === 'error') {
        fullText += `\n\n⚠ ${ev.error || '未知错误'}`;
        applyLive();
      }
    });
  } catch (e) {
    fullText += `\n\n⚠ 网络异常：${e.message}`;
  } finally {
    S.sending = false;
    $('#send').disabled = false;
  }

  const answerKey = 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const finalMeta = {
    mode: isSim ? 'simulate' : (meta.mode || (S.agent ? 'agent' : '')),
    model: meta.model || '', style: meta.style || '',
    sources: meta.sources || [], entity_meta: meta.entity_meta || null,
    steps: isSim ? simLog : agentLog,
    report_path: meta.report_path || null,
    answer_key: answerKey,
  };
  shell.innerHTML = botFinalHtml(finalMeta, fullText)
    + (isSim ? `<div class="sim-cards">${simCards}</div>` : '')
    + (finalMeta.report_path ? `<p style="font-size:11px;color:var(--muted)">报告已存档：${esc(finalMeta.report_path)}</p>` : '')
    + ratingRowHtml(S.sessionId, answerKey, fullText.slice(0, 200), false);
  bindRateRow(shell.lastElementChild, () => S.sessionId, answerKey, fullText.slice(0, 200));
  S.messages.push({ role: 'assistant', content: fullText, meta: finalMeta });
  scrollBottom();
  persistSession();
}

$('#send').addEventListener('click', ask);
$('#q').addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });

/* ---------- 模式开关 ---------- */
$('#agentBtn').addEventListener('click', () => {
  S.agent = !S.agent;
  if (S.agent) S.sim = false;
  $('#agentBtn').classList.toggle('active', S.agent);
  $('#simBtn').classList.toggle('active', S.sim);
});
$('#simBtn').addEventListener('click', () => {
  S.sim = !S.sim;
  if (S.sim) S.agent = false;
  $('#simBtn').classList.toggle('active', S.sim);
  $('#agentBtn').classList.toggle('active', S.agent);
});

/* ---------- 设置弹窗 ---------- */
function openModal() { $('#modal').classList.add('show'); }
function closeModal() { $('#modal').classList.remove('show'); }
$('#settingsBtn').addEventListener('click', openModal);
$('#modalClose').addEventListener('click', closeModal);
$('#modal').addEventListener('click', e => { if (e.target === $('#modal')) closeModal(); });

/* ---------- 项目简介弹窗 ---------- */
const intro = $('#intro');
$('#introBtn').addEventListener('click', () => intro.classList.add('show'));
$('#introClose').addEventListener('click', () => intro.classList.remove('show'));
intro.addEventListener('click', e => { if (e.target === intro) intro.classList.remove('show'); });

/* ---------- 用户反馈弹窗 ---------- */
const fbk = $('#fbkModal');
$('#fbkBtn').addEventListener('click', () => {
  $('#fbkForm').style.display = '';
  $('#fbkThanks').style.display = 'none';
  $('#fbkText').value = '';
  fbk.classList.add('show');
});
$('#fbkClose').addEventListener('click', () => fbk.classList.remove('show'));
fbk.addEventListener('click', e => { if (e.target === fbk) fbk.classList.remove('show'); });
$('#fbkSubmit').addEventListener('click', async () => {
  const text = $('#fbkText').value.trim();
  if (!text) { toast('请先填写反馈内容', { err: true }); return; }
  try {
    await Api.request('/feedback', { method: 'POST', json: { kind: 'general', content: text } });
    $('#fbkForm').style.display = 'none';
    $('#fbkThanks').style.display = '';
    setTimeout(() => fbk.classList.remove('show'), 1500);
  } catch (e) { toast('提交失败：' + e.message, { err: true }); }
});

/* ---------- 登出 / 用户信息 ---------- */
$('#logoutBtn').addEventListener('click', () => Auth.logout());
if (sessionStorage.getItem('a1999.must_change') || me.must_change_pwd) {
  toast('首次登录请尽快修改初始密码', { title: '安全提示', ms: 6000 });
  sessionStorage.removeItem('a1999.must_change');
}
(function fillUser() {
  const who = $('#sideUserWho');
  if (who) {
    who.querySelector('.n').textContent = me.display_name || me.username;
    who.querySelector('.r').textContent = me.role === 'admin' ? '管理员' : '调查员';
  }
  if (Auth.isAdmin()) {
    const nav = $('#adminNavItem');
    if (nav) nav.style.display = '';
  }
})();

/* ---------- 启动 ---------- */
chatInner.innerHTML = WELCOME_HTML;
bindWelcome();
refreshSessions();
