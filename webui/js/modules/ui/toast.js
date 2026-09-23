/* 轻提示 toast。 */
function box() {
  let b = document.getElementById('toasts');
  if (!b) {
    b = document.createElement('div');
    b.id = 'toasts';
    document.body.appendChild(b);
  }
  return b;
}

export function toast(msg, { title = '提示', err = false, ms = 3200 } = {}) {
  const el = document.createElement('div');
  el.className = 'toast' + (err ? ' err' : '');
  el.innerHTML = `<div class="tt"></div><div class="mm"></div>`;
  el.querySelector('.tt').textContent = title;
  el.querySelector('.mm').textContent = msg;
  box().appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .3s';
    setTimeout(() => el.remove(), 320);
  }, ms);
}
