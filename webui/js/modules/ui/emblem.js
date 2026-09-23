/* 徽标交互：单击重播微光，三连击弹出致谢。 */
export function initEmblem(btn, { onTriple } = {}) {
  if (!btn) return;
  let clicks = 0, timer = null;
  btn.addEventListener('click', () => {
    clicks += 1;
    btn.classList.remove('flash');
    void btn.offsetWidth;          // 重触动画
    btn.classList.add('flash');
    clearTimeout(timer);
    timer = setTimeout(() => { clicks = 0; }, 620);
    if (clicks >= 3) {
      clicks = 0;
      clearTimeout(timer);
      onTriple?.();
    }
  });
}
