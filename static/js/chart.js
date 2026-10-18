// Chart wrappers around TradingView Lightweight Charts (loaded as a global from /vendor/).
import { cssVar } from './util.js';

const LWC = () => window.LightweightCharts;
const IST = 19800;   // +05:30 in seconds: the chart axis shows UTC, so times are shifted to read as India time

function baseOptions() {
  return {
    autoSize: true,
    // Pin the locale. By default the library reads navigator.language, and a browser that
    // reports an odd tag (for example "en-US@posix") makes it throw and draw an empty chart.
    localization: { locale: 'en-US' },
    layout: {
      background: { type: 'solid', color: cssVar('--chart-bg') },
      textColor: cssVar('--ink-2'),
      fontFamily: cssVar('--font') || 'sans-serif',
      fontSize: 12,
    },
    grid: {
      vertLines: { color: cssVar('--grid') },
      horzLines: { color: cssVar('--grid') },
    },
    rightPriceScale: { borderColor: cssVar('--line'), scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: cssVar('--line'), timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: {
      mode: LWC().CrosshairMode.Normal,
      vertLine: { color: cssVar('--ink-3'), labelBackgroundColor: cssVar('--ink') },
      horzLine: { color: cssVar('--ink-3'), labelBackgroundColor: cssVar('--ink') },
    },
  };
}

function candleOptions(market) {
  const dec = 2;
  return {
    upColor: cssVar('--chart-up'), downColor: cssVar('--chart-down'),
    wickUpColor: cssVar('--chart-up'), wickDownColor: cssVar('--chart-down'),
    borderVisible: false,
    priceFormat: { type: 'price', precision: dec, minMove: Math.pow(10, -dec) },
  };
}

export function createPriceChart(container) {
  const chart = LWC().createChart(container, baseOptions());
  const series = chart.addSeries(LWC().CandlestickSeries, candleOptions(null));
  let current = null;       // the market the series is formatted for
  let tfSeconds = 900;
  let last = null;          // last candle (mutated by live ticks)
  let lines = new Map();    // key -> price line handle
  let lineSpec = new Map(); // key -> JSON of last applied options (to avoid churn)

  function toBar(r) { return { time: Math.floor(r[0]) + IST, open: r[1], high: r[2], low: r[3], close: r[4] }; }

  return {
    setCandles(rows, market, tfSec) {
      current = market;
      tfSeconds = tfSec;
      series.applyOptions(candleOptions(market));
      const bars = [];
      let prev = -1;
      for (const r of rows) {
        const b = toBar(r);
        if (b.time > prev) { bars.push(b); prev = b.time; }
      }
      series.setData(bars);
      last = bars.length ? { ...bars[bars.length - 1] } : null;
      chart.timeScale().fitContent();
      chart.timeScale().scrollToPosition(6, false);
    },

    // Feed a live price into the newest candle (opens a new one when the period rolls over).
    tick(p) {
      if (!last || !Number.isFinite(p)) return;
      const now = Math.floor(Date.now() / 1000) + IST;
      const bucket = last.time + Math.floor((now - last.time) / tfSeconds) * tfSeconds;
      if (bucket > last.time) {
        last = { time: bucket, open: last.close, high: Math.max(last.close, p), low: Math.min(last.close, p), close: p };
      } else {
        last.close = p;
        last.high = Math.max(last.high, p);
        last.low = Math.min(last.low, p);
      }
      series.update(last);
    },

    // lines: [{ key, price, color, title, style: 'solid'|'dashed'|'dotted' }]
    setLines(spec) {
      const wanted = new Set(spec.map((l) => l.key));
      for (const [key, handle] of lines) {
        if (!wanted.has(key)) { series.removePriceLine(handle); lines.delete(key); lineSpec.delete(key); }
      }
      const styles = { solid: LWC().LineStyle.Solid, dashed: LWC().LineStyle.Dashed, dotted: LWC().LineStyle.Dotted };
      for (const l of spec) {
        const opts = {
          price: l.price, color: l.color, lineWidth: l.width || 1,
          lineStyle: styles[l.style || 'solid'], axisLabelVisible: true, title: l.title || '',
        };
        const sig = JSON.stringify(opts);
        if (lines.has(l.key)) {
          if (lineSpec.get(l.key) !== sig) { lines.get(l.key).applyOptions(opts); lineSpec.set(l.key, sig); }
        } else {
          lines.set(l.key, series.createPriceLine(opts));
          lineSpec.set(l.key, sig);
        }
      }
    },

    // cb(price, { x, y }) when the user clicks a spot on the chart (y is the pixel row).
    onClick(cb) {
      chart.subscribeClick((param) => {
        if (!param.point) return;
        const p = series.coordinateToPrice(param.point.y);
        if (p === null || p === undefined || !Number.isFinite(p)) return;
        cb(p, param.point);
      });
    },

    applyTheme() {
      chart.applyOptions(baseOptions());
      series.applyOptions(candleOptions(current));
    },

    destroy() { chart.remove(); },
  };
}

// Equity curve: green above the starting balance, red below it.
export function createEquityChart(container, points, startBalance) {
  const chart = LWC().createChart(container, { ...baseOptions(), rightPriceScale: { borderColor: cssVar('--line'), scaleMargins: { top: 0.15, bottom: 0.15 } } });
  const series = chart.addSeries(LWC().BaselineSeries, {
    baseValue: { type: 'price', price: startBalance },
    topLineColor: cssVar('--chart-up'), bottomLineColor: cssVar('--chart-down'),
    topFillColor1: cssVar('--chart-up-fill'), topFillColor2: 'rgba(0,0,0,0)',
    bottomFillColor1: 'rgba(0,0,0,0)', bottomFillColor2: cssVar('--chart-down-fill'),
    lineWidth: 2,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  });
  const data = [];
  let prev = -1;
  for (const [ts, eq] of points) {
    const t = Math.floor(ts);
    if (t > prev) { data.push({ time: t, value: eq }); prev = t; }
    else if (data.length) data[data.length - 1].value = eq;
  }
  series.setData(data);
  series.createPriceLine({ price: startBalance, color: cssVar('--ink-3'), lineWidth: 1, lineStyle: LWC().LineStyle.Dashed, axisLabelVisible: true, title: 'Start' });
  chart.timeScale().fitContent();
  return {
    // cb({ time, value }) while the pointer is over the chart, cb(null) when it leaves.
    onHover(cb) {
      chart.subscribeCrosshairMove((param) => {
        const d = param.time !== undefined ? param.seriesData.get(series) : null;
        cb(d && d.value !== undefined ? { time: param.time, value: d.value } : null);
      });
    },
    applyTheme() { chart.applyOptions(baseOptions()); },
    destroy() { chart.remove(); },
  };
}
