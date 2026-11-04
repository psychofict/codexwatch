/* codexwatch dashboard. Every value from the logs is set with textContent,
   never innerHTML, because session titles and project names are user text. */
(() => {
  "use strict";
  const TOKEN = window.CODEXWATCH.token;
  const $ = (s, el = document) => el.querySelector(s);
  const app = $("#app");
  let period = "30d";
  let metric = "cost";
  let state = null;
  let timer = null;

  const PERIODS = ["today", "7d", "30d", "month", "all"];
  try { const p = localStorage.getItem("codexwatch-period"); if (PERIODS.includes(p)) period = p; } catch (e) {}
  { const q = new URLSearchParams(location.search).get("period"); if (PERIODS.includes(q)) period = q; }

  // ------------------------------------------------------------ helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const svgNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs) {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }
  const tok = (n) => {
    n = Number(n || 0);
    for (const [d, u] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
      if (Math.abs(n) >= d) { const v = n / d; return (v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : v.toFixed(0)) + u; }
    }
    return String(Math.round(n));
  };
  const money = (v) => {
    if (v === null || v === undefined) return "unpriced";
    v = Number(v);
    if (v >= 10000) return "$" + Math.round(v).toLocaleString("en-US");
    if (v >= 100) return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (v >= 0.01 || v === 0) return "$" + v.toFixed(2);
    return "$" + v.toFixed(4);
  };
  const pct = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toFixed(d) + "%");
  const int = (v) => Number(v || 0).toLocaleString("en-US");
  function dur(sec) {
    if (sec === null || sec === undefined) return "–";
    sec = Math.max(0, Math.floor(sec));
    const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    if (d) return `${d}d ${hh}h`;
    if (hh) return `${hh}h ${String(m).padStart(2, "0")}m`;
    if (m) return `${m}m`;
    return `${sec}s`;
  }
  function clock(ts) {
    if (!ts) return "–";
    const d = new Date(ts * 1000), now = new Date();
    const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return t;
    if (Math.abs(d - now) < 6 * 86400e3) return d.toLocaleDateString([], { weekday: "short" }) + " " + t;
    return d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + t;
  }
  const ago = (ts) => (ts ? dur(Date.now() / 1000 - ts) + " ago" : "never");
  const level = (p) => {
    const st = state && state.settings || {};
    if (p === null || p === undefined) return "none";
    if (p >= (st.alert_pct || 90)) return "alert";
    if (p >= (st.warn_pct || 70)) return "warn";
    return "ok";
  };
  const track = (p, cls = "", lv = null) => {
    const i = h("i");
    requestAnimationFrame(() => requestAnimationFrame(() => { i.style.transform = `scaleX(${Math.max(0, Math.min(100, p || 0)) / 100})`; }));
    return h("div", { class: "track " + cls, role: "presentation", "data-level": lv }, i);
  };
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("on");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => t.classList.remove("on"), 3600);
  }
  const arrow = () => h("span", { class: "ic", "aria-hidden": "true" }, "→");

  // --------------------------------------------------------------- data
  async function load(quiet) {
    try {
      const r = await fetch(`api/state?period=${encodeURIComponent(period)}`, { headers: { "X-Codexwatch-Token": TOKEN } });
      if (!r.ok) throw new Error((await r.json()).error || r.statusText);
      const text = await r.text();
      if (text === load.last) return;  // nothing new; leave the page and its focus alone
      load.last = text;
      state = JSON.parse(text);
      render();
    } catch (e) {
      if (!quiet) app.replaceChildren(h("div", { class: "wrap empty" }, h("h1", null, "Could not read the dashboard data"),
        h("p", null, String(e.message || e)), h("p", null, "Check that codexwatch dashboard is still running in its terminal, then reload this page.")));
    }
  }
  function schedule() {
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden) load(true); }, 15000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(true); });

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "X-Codexwatch-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    return r.json();
  }

  // ------------------------------------------------------------- render
  function render() {
    const st = state;
    const who = $("#who");
    const plan = st.plan && st.plan.name;
    who.replaceChildren(h("span", { class: "dot" }), h("span", null,
      [st.live_account ? `Live: ${st.live_account}` : null, plan, `read ${ago(st.headline.read_at)}`].filter(Boolean).join(" · ")));
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    const nodes = [h("h1", { class: "sr" }, "codexwatch dashboard"), hero(st), livery(st), totals(st), split(st), quota(st), sessions(st), trio(st), footer(st)];
    app.replaceChildren(...nodes.filter(Boolean));
    if (focused) { const el = app.querySelector(`[data-key="${focused}"]`); if (el) el.focus(); }
    reveal();
  }

  function reveal() {
    const items = app.querySelectorAll(".rise:not(.in)");
    if (!("IntersectionObserver" in window)) { items.forEach((x) => x.classList.add("in")); return; }
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { rootMargin: "0px 0px -8% 0px" });
    items.forEach((x) => io.observe(x));
    if (reveal.done) items.forEach((x) => x.classList.add("in"));
    reveal.done = true;
  }

  function gauge(name, w) {
    if (!w) return h("div", { class: "gauge" }, h("div", { class: "label" }, name), h("div", { class: "fig" }, "–"), h("p", { class: "body" }, "No reading yet. It appears after the next Codex request."));
    const p = w.pct || 0;
    const lv = level(p);
    const facts = [];
    if (w.was !== null && w.was !== undefined) {
      facts.push(h("div", null, h("span", { class: "label" }, "Reset"), h("b", null, "Window reset"), h("small", null, `was ${pct(w.was)} at the last reading`)));
    } else {
      facts.push(h("div", null, h("span", { class: "label" }, "Resets"), h("b", null, clock(w.resets_at)), h("small", null, `in ${dur(w.resets_in_s)}`)));
      facts.push(h("div", null, h("span", { class: "label" }, w.full_at ? "Full at" : "Burn"),
        h("b", { class: w.full_at ? "red" : "" }, w.full_at ? clock(w.full_at) : (w.burn_pct_per_h ? `${w.burn_pct_per_h.toFixed(1)} pts/h` : "Idle")),
        h("small", null, w.full_at ? `at ${w.burn_pct_per_h.toFixed(1)} points an hour` : (w.burn_pct_per_h ? "over the last hour" : "no rise in the last hour"))));
      const sr = w.since_reset || {};
      facts.push(h("div", null, h("span", { class: "label" }, "Since reset"), h("b", null, money(sr.plan_cost)), h("small", null, `${tok(sr.tokens)} tokens here`)));
    }
    return h("div", { class: "gauge rise", "data-level": lv },
      h("div", { class: "head" }, h("span", { class: "label" }, name), lv !== "ok" ? h("span", { class: "chip " + (lv === "alert" ? "red" : "") }, lv === "alert" ? "Near the limit" : "Filling up") : null),
      h("div", { class: "fig", "aria-label": `${name} ${pct(p)} used` }, String(Math.round(p)), h("sup", null, "%")),
      track(p),
      h("div", { class: "ticks", "aria-hidden": "true" }, h("span", null, "0"), h("span", null, "25"), h("span", null, "50"), h("span", null, "75"), h("span", null, "100")),
      h("div", { class: "facts" }, facts));
  }

  function hero(st) {
    const hd = st.headline;
    const sh = hd.short, lg = hd.long;
    const nm = (w, d) => (w ? (w.label === "5h" ? "5-hour window" : w.label === "week" ? "Weekly window" : `${w.label} window`) : d);
    const pills = [h("span", { class: "label" }, "Plan limits")];
    if (st.plan && st.plan.name) pills.push(h("span", { class: "pill" }, st.plan.name));
    if (st.live_account) pills.push(h("span", { class: "pill live" }, st.live_account));
    pills.push(h("span", { class: "muted", style: "font-size:12px" }, hd.read_at ? `From the logs, read ${ago(hd.read_at)}` : "No readings yet"));

    const minor = [];
    for (const lim of st.limits || []) {
      // Other limits (a model's own cap) only while they are as fresh as the main one.
      if (lim.limit_id === "codex" || (hd.read_at && lim.read_at < hd.read_at - 6 * 3600)) continue;
      for (const w of lim.windows) {
        minor.push(h("div", { class: "row" }, h("span", null, `${lim.name || lim.limit_id} · ${w.label}`), track(w.pct, "", level(w.pct)), h("span", { class: "num", style: "text-align:right" }, pct(w.pct)),
          h("span", { class: "rs muted" }, w.resets_at ? `resets ${clock(w.resets_at)}` : "not started")));
      }
    }
    const accts = (st.accounts || []).map((a) => {
      const best = st.next_account === a.name;
      const bar = (lbl, w) => h("div", null, h("span", null, lbl), track(w ? w.pct : 0, "", w ? level(w.pct) : null), h("span", { style: "text-align:right" }, w ? pct(w.pct) : "–"));
      return h("div", { class: "acct" + (a.active ? " on" : "") + (best ? " best" : ""), title: a.fetched_at ? `read ${ago(a.fetched_at)}` : "no reading" },
        h("div", { class: "n" }, h("span", null, a.name), h("em", null, a.active ? "live" : best ? "most room" : a.plan || "")),
        h("div", { class: "bars" }, bar("5h", a.short), bar("wk", a.long)),
        h("small", null, a.fetched_at ? `read ${ago(a.fetched_at)}` : "never read"));
    });
    const actions = [];
    if (st.meta.codexhop && accts.length) {
      actions.push(h("button", { class: "btn small ghost", "data-key": "read-accounts", onclick: refreshAccounts }, "Read every account", arrow()));
      const live = sh ? sh.pct : 0;
      if (st.next_account && level(live) !== "ok") {
        actions.push(h("button", { class: "btn small primary", "data-key": "hop", onclick: () => hopTo(st.next_account) }, `Hop to ${st.next_account}`, arrow()));
      }
    }
    return h("section", { class: "hero" }, h("div", { class: "wrap" },
      h("div", { class: "eyebrow" }, pills),
      h("div", { class: "gauges" }, gauge(nm(sh, "5-hour window"), sh), gauge(nm(lg, "Weekly window"), lg)),
      minor.length ? h("div", { class: "minor" }, minor) : null,
      accts.length ? h("div", { class: "accounts rise" }, accts, h("div", { style: "display:flex;flex-direction:column;gap:10px;justify-content:center" }, actions)) : null));
  }

  async function refreshAccounts(ev) {
    const b = ev.currentTarget;
    b.disabled = true;
    toast("Asking codexhop for every account's reading…");
    try { const r = await post("api/refresh-accounts"); toast(r.ok ? "Every account read." : `codexhop: ${r.message}`); await load(true); } finally { b.disabled = false; }
  }
  async function hopTo(name) {
    if (!confirm(`Hop the live Codex login to ${name}? New codex sessions start as ${name}; open ones keep their login until it renews.`)) return;
    const r = await post("api/hop", { name });
    toast(r.ok ? `Now on ${name}.` : `Hop failed: ${r.message}`);
    load(true);
  }

  function livery(st) {
    const pv = st.plan_value || {};
    const v = pv.api_value_30d || 0;
    const mult = pv.multiple;
    const big = mult ? h("div", { class: "big" }, mult >= 10 ? Math.round(mult) : mult.toFixed(1), h("small", null, "×")) : h("div", { class: "big" }, money(v));
    const text = mult
      ? h("p", null, `${money(v)} of API usage in the last 30 days, on a ${money(pv.plan_usd).replace(".00", "")} plan.`,
          h("span", null, "API-equivalent value at OpenAI's published prices. Your plan bills a flat monthly fee."))
      : h("p", null, "of API usage in the last 30 days.",
          h("span", null, "Set your plan price with codexwatch config set plan_price N to see what your plan returns."));
    return h("section", { class: "livery" }, h("div", { class: "wrap" }, big, text,
      h("button", { class: "btn", "data-key": "share", onclick: openShare }, "Share card", arrow())));
  }

  function totals(st) {
    const p = st.period;
    const cells = [
      ["API value", money(p.cost), p.unpriced_tokens ? `${tok(p.unpriced_tokens)} tokens unpriced` : `${int(p.sessions)} sessions`, true],
      ["Tokens", tok(p.total), `${tok(p.input)} in · ${tok(p.output)} out · ${tok(p.reasoning)} reasoning`],
      ["Requests", int(p.requests), p.requests ? `${tok(p.total / p.requests)} tokens each on average` : "no requests"],
      ["Cache hit", p.input ? pct(p.cache_hit * 100, 1) : "–", p.input ? `${tok(p.cached)} of ${tok(p.input)} input tokens from cache` : ""],
    ];
    const head = h("div", { class: "sechead" }, h("div", null, h("div", { class: "label" }, p.label), h("h2", null, "Where it went")),
      h("div", { class: "tabs toggle", role: "group", "aria-label": "Chart measure" },
        h("button", { "aria-pressed": String(metric === "cost"), "data-key": "m-cost", onclick: () => { metric = "cost"; render(); } }, "Value"),
        h("button", { "aria-pressed": String(metric === "total"), "data-key": "m-total", onclick: () => { metric = "total"; render(); } }, "Tokens")));
    return h("section", { class: "band" }, h("div", { class: "wrap" }, head,
      h("div", { class: "specs rise" }, cells.map(([k, v, sub, red]) => h("div", { class: "spec" }, h("div", { class: "label" }, k), h("div", { class: "v" + (red ? " red" : "") }, v), h("small", null, sub)))),
      chart(st)));
  }

  function chart(st) {
    const days = (period === "all" ? st.daily : st.daily.slice(-30));
    const table = h("div", { class: "sr" }, h("table", null, h("caption", null, "API value and tokens per day"),
      h("thead", null, h("tr", null, h("th", null, "Day"), h("th", null, "API value"), h("th", null, "Tokens"))),
      h("tbody", null, days.map((d) => h("tr", null, h("td", null, d.key), h("td", null, money(d.cost)), h("td", null, tok(d.total)))))));
    const wrap = h("div", { class: "chart rise", "aria-hidden": "true" });
    const outer = h("div", null, table, wrap);
    // Drawn at the container's real width so axis text stays at its true size.
    requestAnimationFrame(() => drawChart(wrap, st));
    chart.last = () => drawChart(wrap, st);
    return outer;
  }
  let resizeT = null;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => chart.last && chart.last(), 120); });
  function drawChart(wrap, st) {
    const days = (period === "all" ? st.daily : st.daily.slice(-30));
    const W = Math.max(280, Math.round(wrap.clientWidth || 1000));
    const H = W < 600 ? 220 : 280, pad = { l: 44, r: 0, t: 12, b: 26 };
    const vals = days.map((d) => (metric === "cost" ? d.cost : d.total) || 0);
    const max = Math.max(...vals, metric === "cost" ? 1 : 1000);
    const nice = niceMax(max);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": `Daily ${metric === "cost" ? "API value" : "tokens"}` });
    // Axis labels are HTML laid over the plot, so they wrap, scale and measure like any other text.
    const g = s("g", { class: "grid" }), labels = [];
    const label = (text, x, y, align) => labels.push(h("span", { class: "ax " + align, style: `left:${x}px;top:${y}px` }, text));
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (H - pad.t - pad.b) * (1 - i / 4);
      g.append(s("line", { x1: pad.l, x2: W, y1: y, y2: y }));
      label(metric === "cost" ? money(nice * i / 4).replace(".00", "") : tok(nice * i / 4), 0, y - 7, "start");
    }
    svg.append(g);
    const n = days.length, bw = (W - pad.l) / n, gap = Math.max(2, bw * 0.28);
    const today = days.length ? days[days.length - 1].key : "";
    days.forEach((d, i) => {
      const v = vals[i];
      const bh = v ? Math.max(2, (H - pad.t - pad.b) * v / nice) : 0;
      const x = pad.l + i * bw + gap / 2;
      const r = s("rect", { class: "b" + (d.key === today ? " today" : ""), x, y: H - pad.b - bh, width: Math.max(1, bw - gap), height: bh });
      svg.append(r);
      const hit = s("rect", { class: "hit", x: pad.l + i * bw, y: pad.t, width: bw, height: H - pad.t - pad.b });
      hit.addEventListener("pointerenter", (e) => { r.classList.add("on"); tipDay(e, d); });
      hit.addEventListener("pointermove", (e) => moveTip(e));
      hit.addEventListener("pointerleave", () => { r.classList.remove("on"); hideTip(); });
      svg.append(hit);
      const every = Math.ceil(n / (W < 600 ? 4 : 8));
      if ((i % every === 0 && n - 1 - i >= every * 0.75) || i === n - 1) {
        const when = new Date(d.key + "T00:00:00").toLocaleDateString([], { day: "numeric", month: "short" });
        label(when, i === n - 1 ? W : pad.l + i * bw + bw / 2, H - 16, i === n - 1 ? "end" : "middle");
      }
    });
    wrap.replaceChildren(svg, ...labels);
  }
  function niceMax(v) {
    const p = Math.pow(10, Math.floor(Math.log10(v || 1)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }
  const tip = $("#tip");
  function tipDay(e, d) {
    tip.replaceChildren(h("span", { class: "k" }, new Date(d.key + "T00:00:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })),
      h("b", null, metric === "cost" ? money(d.cost) : tok(d.total)),
      h("div", null, metric === "cost" ? `${tok(d.total)} tokens` : money(d.cost)),
      h("div", { class: "k" }, `${int(d.requests)} requests · ${int(d.sessions)} sessions`));
    tip.classList.add("on");
    moveTip(e);
  }
  function tipText(e, title, lines) {
    tip.replaceChildren(h("span", { class: "k" }, title), ...lines.map((l, i) => (i === 0 ? h("b", null, l) : h("div", null, l))));
    tip.classList.add("on");
    moveTip(e);
  }
  function moveTip(e) {
    const r = tip.getBoundingClientRect();
    let x = e.clientX + 16 + window.scrollX, y = e.clientY - r.height - 12 + window.scrollY;
    if (x + r.width > window.scrollX + document.documentElement.clientWidth - 8) x = e.clientX - r.width - 16 + window.scrollX;
    if (y < window.scrollY + 70) y = e.clientY + 18 + window.scrollY;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { tip.classList.remove("on"); }

  function rank(title, rows, keyName) {
    const total = rows.reduce((a, r) => a + (r.cost || 0), 0) || 1;
    return h("div", { class: "rise" }, h("div", { class: "label", style: "margin-bottom:12px" }, title),
      rows.length ? h("ul", { class: "rank" }, rows.map((r) => h("li", null,
        h("span", { class: "nm", title: r.key }, String(r.key)),
        h("span", { class: "val" }, money(r.cost) + (r.unpriced_tokens ? "*" : "")),
        h("div", { class: "meta" }, track((r.cost || 0) / total * 100), h("span", null, `${tok(r.total)} tokens · ${pct((r.cost || 0) / total * 100)}`))))) : h("p", { class: "muted" }, `No ${keyName} in this period.`));
  }
  function split(st) {
    const b = st.breakdown;
    return h("section", { class: "band" }, h("div", { class: "wrap" },
      h("div", { class: "split" }, rank("Models", b.model, "models"), rank("Projects", b.project, "projects"))));
  }

  function quota(st) {
    const q = st.quota || {};
    const wins = q.windows || [];
    const week = wins.find((w) => w.label === "week" && w.usd_per_pct) || wins.find((w) => w.usd_per_pct);
    const typical = week ? week.typical_usd_per_pct : null;
    const left = h("div", { class: "rate rise" },
      h("div", { class: "label" }, week ? `1% of your ${week.label === "week" ? "weekly" : week.label} window` : "1% of your plan"),
      h("div", { class: "v" }, week ? money(week.usd_per_pct) : "–"),
      h("p", null, week ? `of API usage, measured over the ${week.plan_name ? week.plan_name + " " : ""}window that started ${clock(week.starts_at)}.` + (typical && Math.abs(typical - week.usd_per_pct) > 0.01 ? ` Your ${week.plan_name || ""} windows of this length have averaged ${money(typical)}.` : "")
        : "Needs a window that has moved at least 2 points. It fills in as you use Codex."),
      (q.jumps || []).length
        ? h("div", { class: "note" }, h("b", null, `${q.jumps.length} rise${q.jumps.length > 1 ? "s" : ""} these logs cannot explain. `),
            `The newest: ${q.jumps[0].label} window ${pct(q.jumps[0].from_pct)} → ${pct(q.jumps[0].to_pct)} at ${clock(q.jumps[0].ts)}, where local usage accounts for about ${Math.round(q.jumps[0].explained_pct)} point${Math.round(q.jumps[0].explained_pct) === 1 ? "" : "s"}. Usage on another machine, in ChatGPT or in cloud tasks counts against the same plan.`)
        : h("div", { class: "note ok" }, h("b", null, "Every rise is accounted for. "), "Each move in your plan windows matches usage in these logs."));
    const showAcct = wins.some((w) => w.account);
    const rows = wins.slice(0, 8).map((w) => h("tr", null,
      h("td", { "data-label": "Window" }, w.label), h("td", { "data-label": "Plan" }, w.plan_name || w.plan || "–"),
      showAcct ? h("td", { "data-label": "Account" }, w.account || "–") : null, h("td", { "data-label": "Started" }, clock(w.starts_at)),
      h("td", { class: "r", "data-label": "Used" }, pct(w.max_pct)), h("td", { class: "r", "data-label": "API value" }, money(w.plan_cost)),
      h("td", { class: "r", "data-label": "Tokens" }, tok(w.tokens)),
      h("td", { class: "r", "data-label": "Per 1%" }, w.usd_per_pct ? money(w.usd_per_pct) : "–")));
    const table = h("div", { class: "tablewrap rise" }, h("table", { class: "rtable" },
      h("thead", null, h("tr", null, ["Window", "Plan", showAcct ? "Account" : null, "Started", "Used", "API value", "Tokens", "Per 1%"].filter(Boolean).map((t, i, all) => h("th", { class: i >= all.length - 4 ? "r" : "" }, t)))),
      h("tbody", null, rows.length ? rows : h("tr", null, h("td", { colspan: 8, class: "muted" }, "No plan windows in the logs yet.")))));
    return h("section", { class: "band" }, h("div", { class: "wrap" },
      h("div", { class: "sechead" }, h("div", null, h("div", { class: "label" }, "Quota forensics"), h("h2", null, "What a percent of your plan costs"),
        h("p", { class: "lede" }, "Codex logs your plan's reading after every response, so each point a window moves can be set against the tokens that moved it. Fast mode counts at 2.5×, the rate it uses limits."))),
      h("div", { class: "quota" }, left, table)));
  }

  function sessions(st) {
    const rows = (st.sessions || []).map((r) => h("tr", { class: r.subagent ? "sub" : "" },
      h("td", { class: "t" }, (r.subagent ? "↳ " + (r.nickname || "subagent") + ": " : "") + (r.title || r.session_id.slice(0, 8)),
        h("span", { class: "sub" }, [r.project, r.branch, r.client].filter(Boolean).join(" · "))),
      h("td", { class: "m", "data-label": "Model" }, (r.models || []).filter(Boolean).join(", ")),
      h("td", { class: "r", "data-label": "Turns" }, int(r.turns)), h("td", { class: "r", "data-label": "Tokens" }, tok(r.total)),
      h("td", { class: "r", "data-label": "API value" }, money(r.cost)),
      h("td", { class: "r", "data-label": "Compacted" }, r.compactions ? String(r.compactions) : "–"),
      h("td", { class: "r", "data-label": "Last" }, clock(r.last))));
    return h("section", { class: "band" }, h("div", { class: "wrap" },
      h("div", { class: "sechead" }, h("div", null, h("div", { class: "label" }, "Sessions"), h("h2", null, "Recent work"))),
      h("div", { class: "tablewrap rise" }, h("table", { class: "rtable sessions" },
        h("thead", null, h("tr", null, ["Session", "Model", "Turns", "Tokens", "API value", "Compacted", "Last"].map((t, i) => h("th", { class: i >= 2 ? "r" : "" }, t)))),
        h("tbody", null, rows.length ? rows : h("tr", null, h("td", { colspan: 7, class: "muted" }, "No sessions in this period.")))))));
  }

  function trio(st) {
    const grid = st.heatmap || [];
    const top = Math.max(1, ...grid.flat());
    const heat = h("div", { class: "heat", role: "img", "aria-label": "Tokens by weekday and hour" });
    heat.append(h("span", { class: "h" }));
    for (let hr = 0; hr < 24; hr++) heat.append(h("span", { class: "h" }, hr % 6 === 0 ? String(hr).padStart(2, "0") : ""));
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach((d, i) => {
      heat.append(h("span", { class: "d" }, d));
      (grid[i] || []).forEach((v, hr) => {
        const k = v ? 1 + Math.min(3, Math.floor(v / top * 4)) : 0;
        const c = h("span", { style: `background:var(--heat-${k})` });
        c.addEventListener("pointerenter", (e) => tipText(e, `${d} ${String(hr).padStart(2, "0")}:00`, [tok(v) + " tokens"]));
        c.addEventListener("pointermove", moveTip);
        c.addEventListener("pointerleave", hideTip);
        heat.append(c);
      });
    });
    const key = h("div", { class: "heatkey", "aria-hidden": "true" }, "Less", [0, 1, 2, 3, 4].map((k) => h("i", { style: `background:var(--heat-${k})` })), "More");
    let best = null;
    grid.forEach((row, d) => (row || []).forEach((v, hr) => { if (v && (!best || v > best.v)) best = { v, d, hr }; }));
    const days7 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    const note = h("p", { class: "heatnote" }, best ? `Busiest hour: ${days7[best.d]}s at ${String(best.hr).padStart(2, "0")}:00, ${tok(best.v)} tokens.` : "No usage in this period.");
    const tools = (st.tools || []).slice(0, 9);
    const b = st.breakdown;
    const kv = (rows, f) => h("ul", { class: "kv" }, rows.map((r) => h("li", null, h("span", null, f(r)[0]), h("span", null, f(r)[1]))));
    const lat = (st.latency || []).slice(0, 4);
    return h("section", { class: "band" }, h("div", { class: "wrap" },
      h("div", { class: "trio" },
        h("div", { class: "rise" }, h("div", { class: "label", style: "margin-bottom:16px" }, "When you work"), heat, key, note),
        h("div", { class: "rise" }, h("div", { class: "label", style: "margin-bottom:12px" }, "Tool calls"),
          tools.length ? kv(tools, (r) => [r.name, int(r.calls)]) : h("p", { class: "muted" }, "No tool calls in this period.")),
        h("div", { class: "rise" }, h("div", { class: "label", style: "margin-bottom:12px" }, "How it ran"),
          kv([...b.client.map((r) => ["Surface: " + r.key, money(r.cost)]), ...b.tier.map((r) => ["Tier: " + (r.key === "priority" ? "fast" : r.key), money(r.cost)]),
            ...b.agent.map((r) => [r.key === "subagent" ? "Subagents" : "Main sessions", money(r.cost)]),
            ...lat.map((r) => [`Median turn, ${r.model}`, dur(r.median_s)])], (x) => x)))));
  }

  function footer(st) {
    return h("footer", null, h("div", { class: "wrap" },
      h("p", null, "Dollar figures are API-equivalent estimates at OpenAI's published prices; ChatGPT plans bill a flat fee. Plan readings come from your Codex logs. codexwatch reads no login and sends nothing anywhere."),
      h("p", null, `codexwatch ${st.meta.version} · ${st.meta.sources} source${st.meta.sources === 1 ? "" : "s"} · scanned ${ago(st.meta.last_scan)}`)));
  }

  // --------------------------------------------------------------- share
  const dlg = $("#share");
  function cardURL() {
    const pr = $("#card-projects").checked ? "1" : "0";
    const th = $("#card-light").checked ? "light" : "dark";
    return `api/card.svg?period=30d&projects=${pr}&theme=${th}&t=${encodeURIComponent(TOKEN)}&n=${Date.now()}`;
  }
  function showCard() {
    const img = h("img", { class: "cardimg", width: 1200, height: 630, alt: "Share card: your Codex usage over the last 30 days", src: cardURL() });
    $("#cardslot").replaceChildren(img);
  }
  function openShare() { showCard(); dlg.showModal(); }
  $("#share-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  $("#card-projects").addEventListener("change", showCard);
  $("#card-light").addEventListener("change", showCard);
  async function fetchCard() { return (await fetch(cardURL())).text(); }
  function save(blob, name) {
    const a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  $("#dl-svg").addEventListener("click", async () => save(new Blob([await fetchCard()], { type: "image/svg+xml" }), "codexwatch-card.svg"));
  $("#dl-png").addEventListener("click", async () => {
    const svg = await fetchCard();
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = 2400; c.height = 1260;
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((b) => save(b, "codexwatch-card.png"), "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  });

  // ------------------------------------------------------- tabs, theme
  function syncTabs() { document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.p === period))); }
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
    period = b.dataset.p;
    try { localStorage.setItem("codexwatch-period", period); } catch (e) {}
    const u = new URL(location.href); u.searchParams.set("period", period); history.replaceState(null, "", u);
    syncTabs();
    load();
  }));
  const themes = ["system", "dark", "light"];
  function applyTheme(t) {
    if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    $("#theme").title = "Theme: " + t;
    try { if (t === "system") localStorage.removeItem("codexwatch-theme"); else localStorage.setItem("codexwatch-theme", t); } catch (e) {}
  }
  $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || "system";
    applyTheme(themes[(themes.indexOf(cur) + 1) % themes.length]);
  });
  syncTabs();
  load();
  schedule();
})();
