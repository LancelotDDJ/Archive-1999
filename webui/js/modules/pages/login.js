/* 登录 / 注册页逻辑。 */
import { Api } from '../api.js';
import { Auth } from '../auth.js';
import { playGate } from '../ui/gate.js';

playGate({ once: true });

// 已登录则直接进入大厅
if (Auth.accessToken() && Auth.user()) {
  location.href = '/';
}

const $ = s => document.querySelector(s);
let mode = 'login';

function setMode(m) {
  mode = m;
  $('#tabLogin').classList.toggle('on', m === 'login');
  $('#tabRegister').classList.toggle('on', m === 'register');
  $('#displayNameRow').style.display = m === 'register' ? '' : 'none';
  $('#submitBtn').textContent = m === 'login' ? '进 入 档 案 馆' : '建 立 档 案';
  $('#err').textContent = '';
  $('#pwHint').style.display = m === 'register' ? '' : 'none';
}

$('#tabLogin').addEventListener('click', () => setMode('login'));
$('#tabRegister').addEventListener('click', () => setMode('register'));

async function submit() {
  const username = $('#username').value.trim();
  const password = $('#password').value;
  const display = $('#displayName').value.trim();
  const err = $('#err');
  err.textContent = '';
  if (!username || !password) {
    err.textContent = '请填写用户名与密码';
    return;
  }
  const btn = $('#submitBtn');
  btn.disabled = true;
  try {
    const data = mode === 'login'
      ? await Api.request('/auth/login', { method: 'POST', json: { username, password } })
      : await Api.request('/auth/register', {
          method: 'POST',
          json: { username, password, display_name: display || undefined },
        });
    Auth.save(data);
    if (data.user?.must_change_pwd) {
      sessionStorage.setItem('a1999.must_change', '1');
    }
    location.href = '/';
  } catch (e) {
    err.textContent = e.message || '操作失败，请重试';
  } finally {
    btn.disabled = false;
  }
}

$('#submitBtn').addEventListener('click', submit);
document.querySelectorAll('.login-card input').forEach(el => {
  el.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
});
setMode('login');
