/* 账户管理（管理员）：列表（搜索+分页）→ 详情（注册信息+密码哈希+全部聊天数据时间线）。
   路由约定：/admin/accounts            → 列表视图
            /admin/accounts#u=<uid>    → 详情视图 */
import { Api } from '../api.js';
import { Auth } from '../auth.js';
import { esc } from '../markdown.js';
import { playGate } from '../ui/gate.js';
import { toast } from '../ui/toast.js';

const me = Auth.guard();
if (!me) throw new Error('unauthorized');
playGate({ once: true });

const $ = s => document.querySelector(s);
const S = { page: 1, pageSize: 20, search: '', uid: null, cPage: 1, cPageSize: 50, cid: '', cTotal: 0 };

function ts(t) { return t ? new Date(t * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'; }

/* ================= 列表视图 ================= */
async function loadList() {
  const q = `search=${encodeURIComponent(S.search)}&page=${S.page}&page_size=${S.pageSize}`;
  try {
    const d = await Api.request(`/admin/users/accounts?${q}`);
    const totalPages = Math.max(1, Math.ceil(d.total / d.page_size));
    S.page = Math.min(S.page, totalPages);
    $('#accTotal').textContent = d.total;
    $('#pageInfo').textContent = `第 ${S.page} / ${totalPages} 页 · 共 ${d.total} 个账户`;
    const body = $('#accBody');
    body.innerHTML = d.items.length ? d.items.map((u, i) => `
      <tr class="rowlink row-enter" style="animation-delay:${Math.min(i * 40, 240)}ms" data-uid="${u.id}">
        <td class="job-row">${u.id}</td>
        <td><b>${esc(u.username)}</b></td>
        <td>${esc(u.display_name)}</td>
        <td><span class="tag ${u.role === 'admin' ? 'gold' : ''}">${u.role === 'admin' ? '管理员' : '用户'}</span></td>
        <td><span class="tag ${u.status === 'active' ? 'green' : 'red'}">${u.status === 'active' ? '正常' : '禁用'}</span></td>
        <td class="job-row">${u.conv_count}</td>
        <td class="job-row">${u.msg_count}</td>
        <td class="job-row">${u.qa_count}</td>
        <td class="job-row">${ts(u.created_at)}</td>
        <td class="job-row">${ts(u.last_login_at)}</td>
        <td><button class="rowbtn" data-uid="${u.id}">查看详情</button></td>
      </tr>`).join('') : '<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">无匹配账户</td></tr>';
    body.querySelectorAll('tr.rowlink').forEach(tr => {
      tr.addEventListener('click', () => {
        const uid = tr.dataset.uid;
        if (location.hash === `u=${uid}`) loadDetail(Number(uid));
        else location.hash = `u=${uid}`;
      });
    });
  } catch (e) { toast('账户列表加载失败：' + e.message, { err: true }); }
}

let searchTimer = null;
$('#searchBtn').addEventListener('click', () => { S.search = $('#searchBox').value.trim(); S.page = 1; loadList(); });
$('#searchBox').addEventListener('keydown', e => { if (e.key === 'Enter') { S.search = $('#searchBox').value.trim(); S.page = 1; loadList(); } });
$('#searchBox').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.search = $('#searchBox').value.trim(); S.page = 1; loadList(); }, 400);
});
$('#pageSize').addEventListener('change', e => { S.pageSize = Number(e.target.value); S.page = 1; loadList(); });
$('#prevBtn').addEventListener('click', () => { if (S.page > 1) { S.page--; loadList(); } });
$('#nextBtn').addEventListener('click', () => { S.page++; loadList().catch(() => S.page--); });

/* ================= 详情视图 ================= */
async function loadDetail(uid) {
  $('#viewList').style.display = 'none';
  $('#viewDetail').style.display = '';
  try {
    const d = await Api.request(`/admin/users/accounts/${uid}`);
    S.uid = uid;
    $('#dTitle').textContent = `${d.display_name}（${d.username}）`;
    const rows = [
      ['账户 ID', String(d.id)],
      ['用户名', d.username],
      ['显示名', d.display_name],
      ['角色', d.role === 'admin' ? '管理员' : '普通用户'],
      ['状态', d.status === 'active' ? '正常' : '已禁用'],
      ['注册时间', ts(d.created_at)],
      ['最近登录', ts(d.last_login_at)],
      ['会话 / 消息 / 提问', `${d.conv_count} / ${d.msg_count} / ${d.qa_count}`],
    ];
    $('#dInfo').innerHTML = rows.map(([k, v]) =>
      `<div class="item"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
    $('#dHash').textContent = d.password.hash;
    $('#dHashNote').innerHTML =
      `算法：<b>${esc(d.password.algorithm)}</b> · 单向哈希<b>不可还原</b>为原始密码，仅用于后台核对。` +
      `如需重新获得该账户的访问权，请到「后台总览 → 用户管理」使用<b>重置密码</b>。`;

    // 会话过滤下拉
    const sel = $('#convFilter');
    sel.innerHTML = `<option value="">全部会话（${(d.conversations || []).length} 个）</option>` +
      (d.conversations || []).map(c =>
        `<option value="${esc(c.id)}">${esc(c.title || '新对话')}（${c.msg_count} 条 · ${esc(c.kind === 'story' ? '推演' : '问答')}）</option>`).join('');
    S.cid = ''; S.cPage = 1;
    loadChats();
  } catch (e) {
    toast('账户详情加载失败：' + e.message, { err: true });
    location.hash = '';
  }
}

async function loadChats() {
  if (!S.uid) return;
  const q = `page=${S.cPage}&page_size=${S.cPageSize}` + (S.cid ? `&cid=${encodeURIComponent(S.cid)}` : '');
  try {
    const d = await Api.request(`/admin/users/accounts/${S.uid}/chats?${q}`);
    S.cTotal = d.total;
    const totalPages = Math.max(1, Math.ceil(d.total / d.page_size));
    $('#cTotal').textContent = d.total;
    $('#cPageInfo').textContent = `第 ${S.cPage} / ${totalPages} 页（时间正序）`;
    const box = $('#chatList');
    if (!d.items.length) {
      box.innerHTML = '<div class="empty" style="padding:24px;text-align:center;color:var(--muted);font-size:12.5px">该账户/会话暂无聊天数据</div>';
      return;
    }
    let html = '', lastDay = '', bi = 0;
    const fullTexts = new Map();          // 长消息全文（展开/折叠切换用）
    for (const m of d.items) {
      const day = new Date(m.time * 1000).toLocaleDateString('zh-CN');
      if (day !== lastDay) { html += `<div class="day-sep">${day}</div>`; lastDay = day; }
      const long = m.content.length > 260;
      if (long) fullTexts.set(m.id, m.content);
      const shown = long ? m.content.slice(0, 260) + '…' : m.content;
      const delay = ` style="animation-delay:${Math.min(bi * 40, 240)}ms"`;
      bi += 1;
      html += `<div class="bubble ${esc(m.role)}"${delay}>
        <div class="meta">
          <span class="who">${esc(m.sender)}</span>
          <span>${new Date(m.time * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</span>
          <span class="conv" title="${esc(m.conv_title)}">${esc(m.conv_title || '会话')} · ${m.conv_kind === 'story' ? '推演' : '问答'}</span>
        </div>
        <div class="content${long ? ' folded' : ''}" data-mid="${m.id}">${esc(shown)}</div>
        ${long ? `<div class="more" data-mid="${m.id}">展开全文（共 ${m.content.length} 字）</div>` : ''}
      </div>`;
    }
    box.innerHTML = html;
    /* 事件委托：展开 = 回填全文；折叠 = 恢复摘要与按钮文案 */
    box.onclick = e => {
      const more = e.target.closest('.more');
      if (!more) return;
      const id = Number(more.dataset.mid);
      const c = box.querySelector(`.content[data-mid="${id}"]`);
      if (!c) return;
      const full = fullTexts.get(id);
      const nowFolded = c.classList.toggle('folded');
      if (nowFolded) {
        c.textContent = full.slice(0, 260) + '…';
        more.textContent = `展开全文（共 ${full.length} 字）`;
      } else {
        c.textContent = full;
        more.textContent = '收起';
      }
    };
  } catch (e) { toast('聊天数据加载失败：' + e.message, { err: true }); }
}

$('#convFilter').addEventListener('change', e => { S.cid = e.target.value; S.cPage = 1; loadChats(); });
$('#cPrev').addEventListener('click', () => { if (S.cPage > 1) { S.cPage--; loadChats(); } });
$('#cNext').addEventListener('click', () => { S.cPage++; loadChats().catch(() => S.cPage--); });
$('#backBtn').addEventListener('click', () => { location.hash = ''; });

/* ================= 视图路由 ================= */
function route() {
  const m = location.hash.match(/u=(\d+)/);
  if (m) loadDetail(Number(m[1]));
  else {
    S.uid = null;
    $('#viewDetail').style.display = 'none';
    $('#viewList').style.display = '';
    loadList();
  }
}
addEventListener('hashchange', route);
route();
