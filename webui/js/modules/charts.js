/* ECharts 主题工厂：从 CSS 设计令牌取色，保证图表与界面同风格。 */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function themePalette() {
  return {
    gold: cssVar('--gold') || '#9c7c42',
    goldDim: cssVar('--gold-dim') || '#7d6337',
    text: cssVar('--text') || '#23231f',
    muted: cssVar('--muted') || '#847f70',
    line: cssVar('--line') || '#c4c1b4',
    panel: cssVar('--panel') || '#f4f3ee',
    accent: cssVar('--accent') || '#b03a2e',
    soul: cssVar('--soul') || '#2e5a4e',
    series: [
      cssVar('--gold') || '#9c7c42',
      cssVar('--soul') || '#2e5a4e',
      cssVar('--accent') || '#b03a2e',
      cssVar('--afflatus-star') || '#0073ff',
      cssVar('--afflatus-spirit') || '#cf5de8',
      cssVar('--afflatus-mineral') || '#b37432',
      cssVar('--afflatus-plant') || '#1fde5a',
      cssVar('--afflatus-intelligence') || '#ffd300',
      cssVar('--afflatus-beast') || '#f25050',
    ],
  };
}

export function baseOption() {
  const p = themePalette();
  return {
    color: p.series,
    textStyle: { fontFamily: '"Noto Serif SC", serif', color: p.text },
    tooltip: {
      backgroundColor: p.panel,
      borderColor: p.line,
      textStyle: { color: p.text, fontSize: 12 },
    },
  };
}

export function pieOption(title, data) {
  const p = themePalette();
  return Object.assign(baseOption(), {
    title: { text: title, left: 'center', top: 6,
      textStyle: { color: p.gold, fontSize: 14, fontWeight: 700 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}（{d}%）' },
    legend: { bottom: 4, textStyle: { color: p.muted, fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['38%', '66%'], center: ['50%', '54%'],
      itemStyle: { borderColor: p.panel, borderWidth: 2 },
      label: { color: p.text, fontSize: 11, formatter: '{b}\n{c}' },
      data,
    }],
  });
}

export function barOption(title, cats, values, { horizontal = false } = {}) {
  const p = themePalette();
  const ax = { type: 'category', data: cats,
    axisLabel: { color: p.muted, fontSize: 11 },
    axisLine: { lineStyle: { color: p.line } } };
  const valAx = { type: 'value',
    axisLabel: { color: p.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: p.line, opacity: .4 } } };
  return Object.assign(baseOption(), {
    title: { text: title, left: 'center', top: 6,
      textStyle: { color: p.gold, fontSize: 14, fontWeight: 700 } },
    grid: { left: 50, right: 20, top: 46, bottom: 40, containLabel: true },
    xAxis: horizontal ? valAx : ax,
    yAxis: horizontal ? ax : valAx,
    series: [{
      type: 'bar', data: values, barMaxWidth: 34,
      itemStyle: {
        borderRadius: [2, 2, 0, 0],
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: p.gold },
            { offset: 1, color: p.goldDim },
          ],
        },
      },
    }],
  });
}

export function lineOption(title, cats, seriesList) {
  const p = themePalette();
  return Object.assign(baseOption(), {
    title: { text: title, left: 'center', top: 6,
      textStyle: { color: p.gold, fontSize: 14, fontWeight: 700 } },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 4, textStyle: { color: p.muted, fontSize: 11 } },
    grid: { left: 50, right: 24, top: 46, bottom: 56, containLabel: true },
    xAxis: { type: 'category', data: cats, boundaryGap: false,
      axisLabel: { color: p.muted, fontSize: 10 },
      axisLine: { lineStyle: { color: p.line } } },
    yAxis: { type: 'value',
      axisLabel: { color: p.muted, fontSize: 11 },
      splitLine: { lineStyle: { color: p.line, opacity: .4 } } },
    series: seriesList.map(s => Object.assign({
      type: 'line', smooth: true, symbolSize: 5,
      areaStyle: { opacity: .12 }, lineStyle: { width: 2 },
    }, s)),
  });
}

const _charts = new Map();
export function mountChart(el, option) {
  if (!window.echarts || !el) return null;
  let chart = _charts.get(el);
  if (!chart) {
    chart = echarts.init(el, null, { renderer: 'canvas' });
    _charts.set(el, chart);
  }
  chart.setOption(option, true);
  return chart;
}

export function initChartsAutoResize() {
  window.addEventListener('resize', () => {
    for (const c of _charts.values()) c.resize();
  });
  document.addEventListener('themechange', () => {
    document.dispatchEvent(new CustomEvent('charts:restyle'));
  });
}
