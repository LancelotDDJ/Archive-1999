/* NDJSON 流读取器（供问答/推演事件流）。 */
export async function readNdjson(resp, onEvent) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      try { onEvent(JSON.parse(line)); } catch {}
    }
  }
}
