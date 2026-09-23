/* 视差层：鼠标驱动的空间纵深（触屏设备自动禁用）。 */
export function initParallax() {
  if (matchMedia('(hover: none)').matches) return;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const far = document.getElementById('pxFar');
  const mid = document.getElementById('pxMid');
  if (!far && !mid) return;
  let raf = null, tx = 0, ty = 0;
  addEventListener('mousemove', e => {
    tx = (e.clientX / innerWidth - .5) * 2;
    ty = (e.clientY / innerHeight - .5) * 2;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = null;
      if (far) far.style.transform = `translate(${tx * -10}px, ${ty * -6}px)`;
      if (mid) mid.style.transform = `translate(${tx * -22}px, ${ty * -12}px)`;
    });
  }, { passive: true });
}
