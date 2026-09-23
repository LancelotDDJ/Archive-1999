/* 背景视频播放控制（主题已固定为基金会制式，不再提供切换）。

   三种模式（默认 1×）：
   - 1×：完整播放时长 60 秒（源片恰好 60s，播放速率 1.0）
   - 2×：约 30 秒播完（速率 2.0）
   - sync：不播放视频——按当前真实时刻定位到视频中对应的一帧，
     暂停在该帧作为静态背景，每分钟随时间变化切换帧画面。
   模式持久化到 localStorage，切换立即生效。 */
const K_VMODE = 'a1999.vmode';
const LOOP_SECONDS = 60;                 // 1× 模式的完整播放时长

export class VideoLoop {
  constructor(video) {
    this.video = video;
    this.mode = localStorage.getItem(K_VMODE) || '1x';
    if (!['1x', '2x', 'sync'].includes(this.mode)) this.mode = '1x';
    this._syncTimer = null;
    video.addEventListener('loadedmetadata', () => this._apply());
    this.setMode(this.mode, { silent: true });
  }

  setMode(mode, { silent = false } = {}) {
    if (!['1x', '2x', 'sync'].includes(mode)) mode = '1x';
    this.mode = mode;
    localStorage.setItem(K_VMODE, mode);
    this._apply();
    this._reflect();
    if (!silent) this.onModeChange?.(mode);
  }

  _apply() {
    const v = this.video;
    if (!v) return;
    clearInterval(this._syncTimer);
    this._syncTimer = null;
    if (this.mode === 'sync') {
      /* 同步 = 静态帧背景：暂停播放，按真实时刻定位帧，每分钟切换 */
      const seek = () => {
        const d = v.duration;
        if (!d) return;
        const now = new Date();
        const minutes = now.getHours() * 60 + now.getMinutes();
        try { v.pause(); v.currentTime = (minutes / 1440) * d; } catch {}
      };
      seek();
      v.pause();
      this._syncTimer = setInterval(seek, 60_000);
      return;
    }
    /* 播放模式 */
    const d = v.duration || LOOP_SECONDS;
    v.playbackRate = this.mode === '2x'
      ? Math.min(Math.max(d / LOOP_SECONDS * 2, 0.1), 16)
      : Math.min(Math.max(d / LOOP_SECONDS, 0.1), 16);
    v.play().catch(() => {});
  }

  _reflect() {
    document.querySelectorAll('#vmModal button[data-vm]').forEach(b => {
      b.classList.toggle('on', b.dataset.vm === this.mode);
    });
  }
}
