/* 开门动画：进入设施仪式感。仅在会话中首次加载播放。 */
export function playGate({ once = true, duration = 1350 } = {}) {
  const gate = document.getElementById('gate');
  if (!gate) return;
  if (once && sessionStorage.getItem('a1999.gate_done')) {
    gate.classList.add('done');
    return;
  }
  requestAnimationFrame(() => {
    setTimeout(() => gate.classList.add('open'), 350);
    setTimeout(() => {
      gate.classList.add('done');
      sessionStorage.setItem('a1999.gate_done', '1');
    }, duration + 350);
  });
}
