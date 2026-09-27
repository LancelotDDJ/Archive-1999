/* 管理后台：用户管理 / KB 状态与更新任务 / LLM 配置。 */
import { Api } from '../api.js';
import { Auth } from '../auth.js';
import { esc } from '../markdown.js';
import { playGate } from '../ui/gate.js';
import { toast } from '../ui/toast.js';
import { lineOption, mountChart, initChartsAutoResize } from '../charts.js';

const me = Auth.guard({ admin: true });
if (!me) throw new Error('unauthorized');
playGate({ once: true });
initChartsAutoResize();

const $ = s => document.querySelector(s);

function ts(t) { return t ? new Date(t * 1000).toLocaleString('zh-CN') : '—'; }

/* ---------- 用户管理 ---------- */
async function loadUsers() {
  const box = $('#usersBody');
  try {
    const { users } = await Api.request('/admin/users');
    box.innerHTML = users.map((u, i) => `
      <tr class="row-enter" style="animation-delay:${Math.min(i * 40, 240)}ms">
        <td>${u.id}</td>
        <td><b>${esc(u.username)}</b>${u.display_name && u.display_name !== u.username ? `<br><span style="color:var(--muted);font-size:11px">${esc(u.display_name)}</span>` : ''}</td>
        <td><span class="tag ${u.role === 'admin' ? 'gold' : ''}">${u.role === 'admin' ? '管理员' : '用户'}</span></td>
        <td><span class="tag ${u.status === 'active' ? 'green' : 'red'}">${u.status === 'active' ? '正常' : '禁用'}</span></td>
        <td class="job-row">${u.conv_count} 会话 / ${u.qa_count} 提问</td>
        <td class="job-row">${ts(u.last_login_at)}</td>
        <td style="white-space:nowrap">
          ${u.id === me.id ? '<span class="tag">当前账号</span>' : `
          <button class="btn-ghost" data-act="role" data-id="${u.id}" data-role="${u.role === 'admin' ? 'user' : 'admin'}">${u.role === 'admin' ? '降为用户' : '设为管理员'}</button>
          <button class="btn-ghost" data-act="status" data-id="${u.id}" data-status="${u.status === 'active' ? 'disabled' : 'active'}">${u.status === 'active' ? '禁用' : '启用'}</button>
          <button class="btn-ghost" data-act="reset" data-id="${u.id}">重置密码</button>
          <button class="btn-ghost btn-danger" data-act="del" data-id="${u.id}">删除</button>`}
        </td>
      </tr>`).join('');
    $('#userCount').textContent = users.length;
  } catch (e) {
    box.innerHTML = `<tr><td colspan="7">加载失败：${esc(e.message)}</td></tr>`;
  }
}

$('#usersBody').addEventListener('click', async e => {
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  const id = Number(b.dataset.id);
  try {
    if (b.dataset.act === 'role') {
      await Api.request(`/admin/users/${id}/role`, { method: 'PATCH', json: { role: b.dataset.role } });
      toast('角色已更新');
    } else if (b.dataset.act === 'status') {
      await Api.request(`/admin/users/${id}/status`, { method: 'PATCH', json: { status: b.dataset.status } });
      toast('状态已更新');
    } else if (b.dataset.act === 'reset') {
      const pw = prompt('为该用户设置新密码（至少 8 位，含字母与数字）：');
      if (!pw) return;
      await Api.request(`/admin/users/${id}/reset-password`, { method: 'POST', json: { new_password: pw } });
      toast('密码已重置');
    } else if (b.dataset.act === 'del') {
      if (!confirm('确认删除该用户？其会话与记录将一并删除，不可恢复。')) return;
      await Api.request(`/admin/users/${id}`, { method: 'DELETE' });
      toast('用户已删除');
    }
    loadUsers();
  } catch (err) {
    toast(err.message, { err: true });
  }
});

/* ---------- KB 状态与更新 ---------- */
async function loadKb() {
  try {
    const s = await Api.request('/kb/status');
    $('#kbState').innerHTML = `
      <dl class="kv">
        <dt>状态</dt><dd>${s.ready ? '✅ 已就绪' : '⏳ 索引构建中'}</dd>
        <dt>知识块</dt><dd>${(s.chunks || 0).toLocaleString()}</dd>
        <dt>实体</dt><dd>${(s.entities || 0).toLocaleString()} · 别名 ${(s.aliases || 0).toLocaleString()}</dd>
        <dt>索引</dt><dd>${s.index_meta?.created ? `更新于 ${s.index_meta.created}（${s.index_meta.model || ''}）` : '未构建'}</dd>
        <dt>月度更新</dt><dd>每月 1 日 03:00 自动增量更新（服务内置调度）</dd>
      </dl>`;
  } catch (e) {
    $('#kbState').textContent = '读取失败：' + e.message;
  }
}

async function loadJobs() {
  try {
    const { jobs } = await Api.request('/admin/kb/jobs');
    $('#jobsBody').innerHTML = jobs.length ? jobs.map((j, i) => `
      <tr class="row-enter" style="animation-delay:${Math.min(i * 40, 200)}ms">
        <td>#${j.id}</td>
        <td>${j.kind === 'full' ? '全量' : '增量'}</td>
        <td><span class="tag ${j.status === 'ok' ? 'green' : j.status === 'fail' ? 'red' : 'gold'}">${j.status}</span></td>
        <td>${j.trigger_by === 'schedule' ? '调度' : '手动'} · ${esc(j.actor || '')}</td>
        <td class="job-row">${ts(j.started_at)}</td>
        <td class="job-row">${ts(j.finished_at)}</td>
      </tr>`).join('') : '<tr><td colspan="6" style="color:var(--muted)">尚无更新任务</td></tr>';
  } catch (e) {
    $('#jobsBody').innerHTML = `<tr><td colspan="6">加载失败：${esc(e.message)}</td></tr>`;
  }
}

$('#btnIncremental').addEventListener('click', async () => {
  try {
    const r = await Api.request('/admin/kb/update', { method: 'POST', json: { kind: 'incremental' } });
    if (r.accepted) { toast('增量更新任务已启动'); setTimeout(loadJobs, 1500); }
    else toast(r.reason || '任务被拒绝', { err: true });
  } catch (e) { toast(e.message, { err: true }); }
});
$('#btnFull').addEventListener('click', async () => {
  if (!confirm('全量更新将重新抓取整个 wiki（耗时数小时），确认继续？')) return;
  try {
    const r = await Api.request('/admin/kb/update', { method: 'POST', json: { kind: 'full' } });
    if (r.accepted) { toast('全量更新任务已启动'); setTimeout(loadJobs, 1500); }
    else toast(r.reason || '任务被拒绝', { err: true });
  } catch (e) { toast(e.message, { err: true }); }
});

/* ---------- 全局活跃 ---------- */
async function loadActivity() {
  try {
    const a = await Api.request('/admin/stats/qa-activity?days=30');
    const days = a.series || [];
    mountChart($('#chGlobal'), lineOption('全站问答活跃（近 30 日）',
      days.map(d => d.day.slice(5)),
      [
        { name: '提问数', data: days.map(d => d.total) },
        { name: '活跃用户', data: days.map(d => d.users || 0) },
      ]));
    $('#recentBody').innerHTML = (a.recent || []).map((q, i) => `
      <tr class="row-enter" style="animation-delay:${Math.min(i * 30, 200)}ms"><td class="job-row">${ts(q.created_at)}</td>
        <td>${esc(q.username)}</td>
        <td>${esc(q.question.slice(0, 60))}</td>
        <td><span class="tag">${esc(q.mode || '')}</span></td>
        <td class="job-row">${q.latency_ms}ms${q.cached ? ' · 缓存' : ''}</td></tr>`).join('')
      || '<tr><td colspan="5" style="color:var(--muted)">暂无记录</td></tr>';
  } catch (e) {
    console.warn(e);
  }
}

/* ---------- LLM 配置 ---------- */
async function loadLlm() {
  try {
    const c = await Api.request('/admin/llm/config');
    $('#f_provider').value = c.provider || 'auto';
    $('#f_base').value = c.base_url || '';
    $('#f_model').value = c.model || '';
    $('#f_key').placeholder = c.has_key ? `已保存（${c.api_key_masked}），输入以更换` : 'sk-…';
  } catch (e) { toast(e.message, { err: true }); }
}
$('#llmSave').addEventListener('click', async () => {
  const body = {
    provider: $('#f_provider').value,
    base_url: $('#f_base').value.trim(),
    model: $('#f_model').value.trim(),
  };
  const k = $('#f_key').value.trim();
  if (k) body.api_key = k;
  try {
    await Api.request('/admin/llm/config', { method: 'POST', json: body });
    $('#f_key').value = '';
    toast('LLM 配置已保存');
    loadLlm();
  } catch (e) { toast(e.message, { err: true }); }
});
$('#llmClear').addEventListener('click', async () => {
  try {
    await Api.request('/admin/llm/config', { method: 'POST', json: { clear_key: true } });
    toast('API Key 已清空（将使用证据模式）');
    loadLlm();
  } catch (e) { toast(e.message, { err: true }); }
});

async function loadFeedback() {
  const body = $('#fbkBody');
  if (!body) return;
  try {
    const kind = $('#fbkKind').value;
    const from = $('#fbkFrom').value;
    const to = $('#fbkTo').value;
    const q = new URLSearchParams();
    if (kind) q.set('kind', kind);
    if (from) q.set('from', from);
    if (to) q.set('to', to);
    const { items } = await Api.request('/feedback/admin?' + q.toString());
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6">暂无反馈记录</td></tr>';
      return;
    }
    body.innerHTML = items.map((f, i) => `
      <tr class="row-enter" style="animation-delay:${Math.min(i * 30, 200)}ms">
        <td class="job-row">${ts(f.created_at)}</td>
        <td>${f.kind_label}</td>
        <td>${esc(f.username)}</td>
        <td>${f.kind === 'answer'
          ? `<span class="${f.rating === 1 ? 'fbk-rating-pos' : 'fbk-rating-neg'}">${f.rating_label}</span>`
          : '—'}</td>
        <td class="fbk-snippet">${esc(f.content || f.answer_snippet || '（无补充说明）')}</td>
        <td class="fbk-conv">${f.conv_id ? esc(f.conv_id.slice(0, 10)) + '…' : '—'}</td>
      </tr>`).join('');
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6">加载失败：${esc(e.message)}</td></tr>`;
  }
}

loadUsers(); loadKb(); loadJobs(); loadActivity(); loadLlm(); loadFeedback();
setInterval(loadJobs, 15000);
$('#fbkQuery').addEventListener('click', loadFeedback);
