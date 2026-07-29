/* Track-record dashboard — read-only renderer.
 *
 * Fetches track_record.json (override with ?data=<url>) and renders it. No build
 * step, no third-party libraries: a static page reading a public, append-only
 * JSON is the whole trust model — there is no server logic that could fake data.
 */
(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const DATA_URL = params.get("data") || "track_record.json";

  const $ = (id) => document.getElementById(id);
  const fmtUsd = (n) => (n == null ? "—" : `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  const fmtPct = (n, dp = 1) => (n == null ? "—" : `${(n * 100).toFixed(dp)}%`);
  const fmtSignedUsd = (n) => (n == null ? "—" : `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(2)}`);
  const fmtDate = (iso) => (iso ? new Date(iso).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "—");

  fetch(DATA_URL, { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(`${r.status} fetching ${DATA_URL}`); return r.json(); })
    .then(render)
    .catch((err) => {
      const s = $("state");
      s.textContent = `Could not load track record: ${err.message}`;
      s.classList.add("error");
    });

  function render(d) {
    $("state").hidden = true;
    $("app").hidden = false;

    const meta = d.meta, rs = d.risk_summary, cfg = meta.risk_config;

    // Header
    $("bot-name").textContent = meta.bot_name || "BreakoutBot";
    $("strategy").textContent = meta.strategy || "";
    $("updated").textContent = `updated ${fmtDate(meta.generated_at)}`;
    const badge = $("mode-badge");
    badge.textContent = meta.mode;
    badge.className = "badge " + (meta.mode === "LIVE" ? "badge-live" : "badge-testnet");

    // Risk banner KPIs (foreground the discipline, not the return)
    $("max-dd").textContent = fmtPct(rs.max_dd_pct);
    $("risk-per-trade").textContent = cfg.risk_per_trade_usd != null ? `$${cfg.risk_per_trade_usd.toFixed(0)}` : "—";
    $("hard-stop").textContent = fmtPct(cfg.peak_dd_limit_pct, 0);
    $("throttle").textContent = fmtPct(cfg.throttle_dd_pct, 0);
    $("win-rate").textContent = rs.win_rate_pct != null ? `${rs.win_rate_pct.toFixed(0)}%` : "—";
    $("trade-count").textContent = rs.total_trades ?? "—";

    // Expectancy is the number that decides whether a system makes money — a win
    // rate on its own does not. Show it, and show the win rate it must beat.
    if (rs.expectancy_usd != null) {
      $("expectancy-kpi").hidden = false;
      $("expectancy-val").textContent = fmtSignedUsd(rs.expectancy_usd);
    }
    if (rs.breakeven_wr_pct != null) {
      $("win-rate-lbl").textContent = `win rate (needs ${rs.breakeven_wr_pct.toFixed(0)}% to break even)`;
    }

    const rr = (rs.avg_win_usd != null && rs.avg_loss_usd)
      ? `Avg win ${fmtSignedUsd(rs.avg_win_usd)} vs avg loss ${fmtSignedUsd(rs.avg_loss_usd)}`
        + (rs.payoff_ratio != null ? ` (payoff ${rs.payoff_ratio})` : "") + ". "
      : "";
    $("rr-note").textContent =
      `${rr}Every full trade risks a fixed ~$${(cfg.risk_per_trade_usd || 0).toFixed(0)} regardless of the coin's volatility — ` +
      `so no single position can blow up the account. Sizing halves at ${fmtPct(cfg.throttle_dd_pct, 0)} drawdown and the system hard-stops at ${fmtPct(cfg.peak_dd_limit_pct, 0)}.`;

    renderRiskEvents(d.risk_events || []);
    renderEquity(d.equity_curve || []);
    renderTrades(d.trades || []);
    renderBacktest(d.backtest || []);
    renderBenchmark(d.benchmark, meta);

    $("equity-sub").textContent = `${meta.mode.toLowerCase()} · start ${fmtUsd(meta.start_balance)} → now ${fmtUsd(meta.current_balance)}`;
    $("universe").textContent = `Universe (${(meta.symbols || []).length}): ${(meta.symbols || []).join(", ")}`;
  }

  function renderRiskEvents(events) {
    const ul = $("risk-events");
    if (!events.length) {
      ul.innerHTML = `<li class="empty">No circuit-breaker has fired yet — drawdown has stayed within limits. (This list fills in if/when a guard trips.)</li>`;
      return;
    }
    ul.innerHTML = events
      .slice()
      .sort((a, b) => new Date(b.ts) - new Date(a.ts))
      .map((e) => `
        <li>
          <span class="ev-ts">${fmtDate(e.ts)}</span>
          <span class="ev-tag tag-${e.type}">${e.type.replace(/_/g, " ")}</span>
          <span class="ev-detail">${escapeHtml(e.detail)}</span>
        </li>`)
      .join("");
  }

  function renderTrades(trades) {
    const tb = document.querySelector("#trades tbody");
    if (!trades.length) {
      tb.innerHTML = `<tr><td colspan="10" class="muted">No closed trades yet.</td></tr>`;
      return;
    }
    tb.innerHTML = trades
      .slice()
      .sort((a, b) => new Date(b.ts_close) - new Date(a.ts_close))
      .map((t) => {
        const cls = t.pnl >= 0 ? "pnl-pos" : "pnl-neg";
        return `<tr>
          <td>${fmtDate(t.ts_close)}</td>
          <td>${escapeHtml(t.symbol)}</td>
          <td><span class="sleeve">${t.sleeve}</span></td>
          <td>${t.regime_at_entry || "—"}</td>
          <td class="num">${t.risk_usd != null ? "$" + t.risk_usd.toFixed(0) : "—"}</td>
          <td class="num">${t.notional != null ? "$" + t.notional.toFixed(0) : "—"}</td>
          <td>${t.exit_type}</td>
          <td class="num ${cls}">${fmtSignedUsd(t.pnl)}</td>
          <td class="num">${fmtUsd(t.balance_after)}</td>
          <td class="why">${escapeHtml(t.audit_note || "")}</td>
        </tr>`;
      })
      .join("");
  }

  function renderBenchmark(b, meta) {
    // Honest baseline: bot vs buy&hold of its OWN universe (USD). Optional field —
    // if the exporter ran without --benchmark (or the fetch failed) we show nothing.
    if (!b || b.own_universe_hodl_pct == null) return;
    const el = $("benchmark-kpi");
    el.hidden = false;
    const uni = b.own_universe_hodl_pct;
    $("benchmark-val").textContent = `${uni >= 0 ? "+" : ""}${uni.toFixed(2)}%`;
    const botPct = meta.start_balance
      ? ((meta.current_balance / meta.start_balance) - 1) * 100 : null;
    const btcTxt = b.btc_usd_hodl_pct != null
      ? ` BTC/USD same period: ${b.btc_usd_hodl_pct >= 0 ? "+" : ""}${b.btc_usd_hodl_pct.toFixed(2)}%.` : "";
    $("rr-note").textContent += botPct == null ? "" :
      ` Comparison, same period & USD terms — bot: ${botPct >= 0 ? "+" : ""}${botPct.toFixed(2)}% vs own-universe HODL: ${uni >= 0 ? "+" : ""}${uni.toFixed(2)}%.${btcTxt}`;
    const method = document.getElementById("benchmark-method");
    if (method) method.hidden = false;
  }

  function renderBacktest(items) {
    const wrap = $("backtest");
    if (!items.length) { wrap.innerHTML = `<p class="muted">No backtest cards configured.</p>`; return; }
    wrap.innerHTML = items.map((b) => `
      <div class="bt-card">
        <div class="bt-period">${escapeHtml(b.period)}</div>
        <div class="bt-metrics">
          <div><span class="m-val">${b.profit_factor != null ? b.profit_factor.toFixed(2) : "—"}</span><span class="m-lbl">profit factor</span></div>
          <div><span class="m-val">${b.max_dd_pct != null ? b.max_dd_pct.toFixed(2) + "%" : "—"}</span><span class="m-lbl">max drawdown</span></div>
          ${b.win_rate_pct != null ? `<div><span class="m-val">${b.win_rate_pct.toFixed(0)}%</span><span class="m-lbl">win rate</span></div>` : ""}
        </div>
        ${b.note ? `<div class="bt-note">${escapeHtml(b.note)}</div>` : ""}
      </div>`).join("");
  }

  // ── Dependency-free equity chart (canvas) ──────────────────────────────────
  function renderEquity(curve) {
    const canvas = $("equity-chart");
    if (!curve.length) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 1000;
    const cssH = 280;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const padL = 56, padR = 12, padT = 14, padB = 26;
    const W = cssW - padL - padR, H = cssH - padT - padB;

    const xs = curve.map((p) => new Date(p.ts).getTime());
    const ys = curve.map((p) => p.balance);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    let yMin = Math.min(...ys), yMax = Math.max(...ys);
    const pad = (yMax - yMin) * 0.12 || 1;
    yMin -= pad; yMax += pad;

    const X = (t) => padL + ((t - xMin) / (xMax - xMin || 1)) * W;
    const Y = (v) => padT + (1 - (v - yMin) / (yMax - yMin || 1)) * H;

    const css = getComputedStyle(document.documentElement);
    const cBorder = css.getPropertyValue("--border").trim() || "#232c3d";
    const cGreen = css.getPropertyValue("--green").trim() || "#3fb950";
    const cMuted = css.getPropertyValue("--muted").trim() || "#8a98ab";

    // horizontal gridlines + y labels
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = yMin + ((yMax - yMin) * i) / ticks;
      const y = Y(v);
      ctx.strokeStyle = cBorder;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + W, y); ctx.stroke();
      ctx.fillStyle = cMuted;
      ctx.textAlign = "right";
      ctx.fillText("$" + v.toFixed(0), padL - 8, y);
    }

    // start-balance reference line (break-even)
    const startY = Y(ys[0]);
    ctx.strokeStyle = cMuted;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(padL, startY); ctx.lineTo(padL + W, startY); ctx.stroke();
    ctx.setLineDash([]);

    // equity line + soft fill
    ctx.beginPath();
    curve.forEach((p, i) => {
      const x = X(xs[i]), y = Y(p.balance);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = cGreen;
    ctx.lineWidth = 1.8;
    ctx.stroke();
    ctx.lineTo(X(xs[xs.length - 1]), Y(yMin));
    ctx.lineTo(X(xs[0]), Y(yMin));
    ctx.closePath();
    ctx.fillStyle = cGreen + "1a";
    ctx.fill();

    // x labels (first / last)
    ctx.fillStyle = cMuted;
    ctx.textAlign = "left";
    ctx.fillText(new Date(xMin).toISOString().slice(0, 10), padL, padT + H + 14);
    ctx.textAlign = "right";
    ctx.fillText(new Date(xMax).toISOString().slice(0, 10), padL + W, padT + H + 14);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
})();
