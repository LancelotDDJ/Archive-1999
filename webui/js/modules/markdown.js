/* markdown-lite —— 1:1 移植原版渲染（金色加粗 / 引用上标 / md-table / 推断图章），
   并保留 sanitizeAnswer（思考过程与正文分离，本项目新增的安全层）。 */
export function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 思考过程与最终回答分离：渲染前移除 think 推理块与开头英文主导行。
    判定与后端 textutil 同步：拉丁词 ≥3 且拉丁字母数 > 中文字数×3
    （容忍句中夹带的少量中文术语，如 "...scent notes (香调) contain 木质..."）。 */
export function sanitizeAnswer(text) {
  if (!text) return '';
  let t = String(text);
  t = t.replace(/<think>[\s\S]*?<\/think>/gi, '');
  t = t.replace(/^\s*<think>[\s\S]*$/i, '');
  const isEnDominant = s => {
    const w = s.match(/[A-Za-z]{2,}/g) || [];
    if (w.length < 3) return false;
    const latin = w.join('').length;
    const cjk = (s.match(/[一-龥]/g) || []).length;
    return latin > cjk * 3;
  };
  const lines = t.split('\n');
  let dropped = 0;
  while (lines.length && dropped < 3) {
    const s = lines[0].trim();
    if (s && isEnDominant(s)) { lines.shift(); dropped++; }
    else break;
  }
  return lines.join('\n').replace(/^\s+/, '');
}

export function mdLite(s) {
  let h = esc(sanitizeAnswer(s));
  h = h.replace(/^(#{2,3} )/gm, "\n$1").replace(/([^\n])(#{2,3} .+)$/gm, "$1\n$2");
  h = h.replace(/\*\*(.+?)\*\*/g, "<b style='color:var(--gold)'>$1</b>");
  h = h.replace(/（推断）|\(推断\)/g, "<span class='infer'>推断</span>");
  h = h.replace(/\[(\d+)\]/g, "<sup class='cite'>[$1]</sup>");
  h = h.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^---+$/gm, "<hr>");
  h = h.replace(/^## (.+)$/gm, "<h3 style='font-size:14.5px'>$1</h3>");
  const lines = h.split("\n"), out = [], tbl = [];
  const flushTbl = () => {
    if (!tbl.length) return;
    const rows = tbl.filter(r => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
    if (rows.length) {
      let t = "<table class='md-table'>";
      rows.forEach((r, ri) => {
        const cells = r.split("|").slice(1, -1).map(c => c.trim());
        t += "<tr>" + cells.map(c => ri === 0 ? `<th>${c}</th>` : `<td>${c}</td>`).join("") + "</tr>";
      });
      t += "</table>";
      out.push(t);
    }
    tbl.length = 0;
  };
  for (const l of lines) {
    if (/^\s*\|.*\|\s*$/.test(l)) tbl.push(l);
    else { flushTbl(); out.push(l); }
  }
  flushTbl();
  h = out.join("\n");
  h = h.split(/\n/).map(l => l.trim() === "" ? "" : (l.startsWith("<h3>") || l.startsWith("<table") || l.startsWith("<") ? l : "<p>" + l + "</p>")).join("");
  return h;
}
