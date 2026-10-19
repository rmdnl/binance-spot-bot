(() => {
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
  const esc = v => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  async function json(url, options={}) { const r=await fetch(url,options); const d=await r.json(); if(!r.ok) throw new Error(d.detail||d.error||`HTTP ${r.status}`); return d; }
  function metric(id, value, note) {
    const valueEl = document.getElementById(id);
    const noteEl = document.getElementById(id + '-note');
    if (valueEl) valueEl.textContent = value;
    if (noteEl && note !== undefined) noteEl.textContent = note;
  }
  function usdt(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 'Unavailable';
    return new Intl.NumberFormat('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 4}).format(n) + ' USDT';
  }
  function renderMetrics(m) {
    if (!m) {
      metric('metric-equity', 'Unavailable', 'Could not calculate account equity');
      metric('metric-coverage', 'Unavailable', 'Metrics are not available');
      metric('metric-realized', 'Unavailable', 'Could not load realized PNL');
      metric('metric-unrealized', 'Unavailable', 'Could not load unrealized PNL');
      return;
    }
    metric('metric-equity', (m.equity_complete ? '' : '~ ') + usdt(m.equity_usdt),
      m.equity_complete ? 'Estimated from exchange balances at current market prices' : 'Partial estimate. Some assets have no USDT market price.');
    metric('metric-coverage', m.equity_complete ? 'Complete' : 'Partial',
      m.unpriced_assets?.length ? 'Unpriced: ' + m.unpriced_assets.join(', ') : 'All non-zero balances have a USDT valuation');
    metric('metric-realized', (m.realized_pnl_complete ? '' : '~ ') + usdt(m.realized_pnl_usdt),
      m.realized_pnl_complete ? 'Ledger PNL uses current quote-to-USDT prices; third-token fees are valued at ingestion, not historical FX' : 'Partial: unpriced quote currency or third-token fee valuation');
    metric('metric-unrealized', (m.unrealized_pnl_complete ? '' : '~ ') + usdt(m.unrealized_pnl_usdt),
      m.unrealized_pnl_complete ? 'Open inventory marked to current market prices' : 'Some open positions could not be valued');
  }
  async function refreshRisk(){ const el=document.getElementById('risk-status'); if(!el)return; try{const d=await json('/api/risk/status'); const equity=d.equity_quote==null?'Unavailable':usdt(d.equity_quote); const drawdown=d.drawdown_pct==null?'Unavailable':`${esc(d.drawdown_pct)}%`; const daily=d.daily_realized_pnl_quote==null?'Unavailable':usdt(d.daily_realized_pnl_quote); el.innerHTML=`<b>${esc(d.status.toUpperCase())}</b><p class="muted">Equity ${esc(equity)} · Drawdown ${drawdown} · Daily PNL ${esc(daily)}</p>${d.metrics_error?`<p class="muted">Risk valuation unavailable: ${esc(d.metrics_error)}</p>`:''}`;}catch(e){el.textContent=e.message;} }
  async function loadAccount(){
    const anchor=document.getElementById('overview');
    if(!anchor)return;
    try {
      const d=await json('/api/account');
      renderMetrics(d.available ? d.metrics : null);
      if (d.available && !d.metrics && d.metrics_error) {
        metric('metric-equity', 'Unavailable', 'Valuation failed: ' + d.metrics_error);
      }
      const section=document.createElement('section');
      section.className='panel';
      section.id='account';
      if(!d.available){
        section.innerHTML=`<div class="section-heading"><div><p class="eyebrow">BINANCE ACCOUNT</p><h2>Balance & PNL</h2></div></div><div class="alert">${esc(d.error)}</div>`;
      } else {
        const balances=d.balances||[], trades=d.trades||[];
        section.innerHTML=`<div class="section-heading"><div><p class="eyebrow">BINANCE ACCOUNT</p><h2>Balance & Recent Trades</h2><p class="muted">Read-only exchange snapshot. Equity and PNL are valued in USDT; unsupported assets are flagged rather than silently valued at zero.</p></div><span class="draft-chip">READ ONLY</span></div><div class="strategy-list">${balances.map(x=>`<article class="strategy-row"><div class="coin-icon">◈</div><div class="strategy-name"><b>${esc(x.asset)}</b><small>Free ${esc(x.free)} · Used ${esc(x.used)}</small></div><span class="state-chip">${esc(x.total)}</span></article>`).join('')||'<div class="empty-state"><b>No non-zero balances</b></div>'}</div><div class="deal-list">${trades.map(x=>`<article class="strategy-row"><div class="coin-icon">◷</div><div class="strategy-name"><b>${esc(x.symbol)}</b><small>${esc(x.side).toUpperCase()} · ${esc(x.amount)} @ ${esc(x.price)}</small></div><span class="muted">${esc(x.cost)}</span></article>`).join('')||'<div class="empty-state"><b>No recent trades</b></div>'}</div>`;
      }
      if (d.available && d.trades_error) {
        const warning = document.createElement('p');
        warning.className = 'muted';
        warning.textContent = 'Recent trades unavailable: ' + d.trades_error;
        section.appendChild(warning);
      }
      document.getElementById('account')?.remove();
      anchor.after(section);
    } catch(e) {
      renderMetrics(null);
      console.error(e);
    }
  }
  document.querySelectorAll('form[data-csrf]').forEach(f=>{f.addEventListener('submit',e=>{if(!f.querySelector('input[name="csrf_token"]')){const i=document.createElement('input');i.type='hidden';i.name='csrf_token';i.value=csrf;f.appendChild(i);}})});
  const gridForm = document.getElementById('grid-create-form');
  const gridMessage = document.getElementById('grid-message');
  const gridBots = document.getElementById('grid-bots');
  const gridPreview = document.getElementById('grid-preview-result');
  function showGridMessage(message, isError=false) {
    if (!gridMessage) return;
    gridMessage.textContent = message;
    gridMessage.className = isError ? 'alert' : 'muted';
  }
  function gridFormValues() {
    return Object.fromEntries(new FormData(gridForm).entries());
  }
  async function previewGrid() {
    if (!gridForm) return;
    const v = gridFormValues();
    const q = new URLSearchParams({
      symbol: v.symbol, lower_price: v.lower_price, upper_price: v.upper_price,
      grids: v.grids, grid_type: v.grid_type
    });
    try {
      const d = await json('/api/grid/preview?' + q.toString());
      gridPreview.innerHTML = '<p class="muted">Step: ' + esc(d.step_pct) +
        '% · ' + esc(d.levels.length) + ' levels</p><div class="strategy-list">' +
        d.levels.map(level => '<article class="strategy-row"><div class="strategy-name"><b>Level ' +
          esc(level.index) + '</b></div><span class="state-chip">' + esc(level.price) +
          '</span></article>').join('') + '</div>';
      showGridMessage('Grid preview ready. Review the levels before creating it.');
    } catch (e) { showGridMessage(e.message, true); }
  }
  async function refreshGridBots() {
    if (!gridBots) return;
    try {
      const bots = await json('/api/grid/bots');
      if (!bots.length) {
        gridBots.innerHTML = '<div class="empty-state"><b>No grid instances yet</b><p>Create a grid above, preview its levels, then start it when ready.</p></div>';
        return;
      }
      const cards = await Promise.all(bots.map(async bot => {
        let detail = null;
        try { detail = await json('/api/grid/bots/' + encodeURIComponent(bot.id)); } catch (_) {}
        const price = detail?.current_price ? 'Price ' + detail.current_price : (detail?.price_error ? 'Price unavailable' : 'Price loading');
        const trigger = bot.trigger_price ? ' · Downward trigger ' + bot.trigger_price : '';
        const nearest = detail?.nearest_level ? ' · Nearest level ' + detail.nearest_level.price +
          ' (' + Number(detail.nearest_level.distance_pct).toFixed(3) + '% away)' : '';
        let actions = '';
        if (bot.status === 'draft') actions = '<button class="button primary" data-grid-action="start" data-grid-id="' + esc(bot.id) + '">Start</button>';
        if (bot.status === 'armed') actions = '<button class="button secondary" data-grid-action="stop" data-grid-id="' + esc(bot.id) + '">Stop waiting</button>';
        if (bot.status === 'running') actions = '<button class="button secondary" data-grid-action="pause" data-grid-id="' + esc(bot.id) + '">Pause</button><button class="button secondary" data-grid-action="stop" data-grid-id="' + esc(bot.id) + '">Stop</button>';
        if (bot.status === 'paused') actions = '<button class="button primary" data-grid-action="resume" data-grid-id="' + esc(bot.id) + '">Resume</button><button class="button secondary" data-grid-action="stop" data-grid-id="' + esc(bot.id) + '">Stop</button>';
        if (bot.status === 'error') actions = '<button class="button secondary" data-grid-action="stop" data-grid-id="' + esc(bot.id) + '">Stop</button>';
        return '<article class="strategy-row"><div class="strategy-name"><b>' + esc(bot.symbol) + ' · #' + esc(bot.id) +
          '</b><small>' + esc(bot.environment.toUpperCase()) + ' · ' + esc(bot.lower_price) + ' to ' +
          esc(bot.upper_price) + ' · ' + esc(bot.grids) + ' levels · ' + esc(bot.capital_quote) +
          ' quote capital' + esc(trigger) + '</small><small>' + esc(price + nearest) + ' · ' + esc(bot.open_orders.length) +
          ' open orders</small></div><span class="state-chip">' + esc(bot.status.toUpperCase()) +
          '</span><div class="top-actions">' + actions + '</div></article>';
      }));
      gridBots.innerHTML = cards.join('');
    } catch (e) {
      gridBots.innerHTML = '<div class="alert">' + esc(e.message) + '</div>';
    }
  }
  document.getElementById('grid-preview')?.addEventListener('click', previewGrid);
  document.getElementById('grid-refresh')?.addEventListener('click', refreshGridBots);
  gridForm?.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      const d = await json('/api/grid/bots', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams(new FormData(gridForm))
      });
      showGridMessage('Grid #' + d.id + ' created in DRAFT. Preview levels and trigger, then press Start to arm or activate it.');
      gridPreview.innerHTML = '';
      await refreshGridBots();
    } catch (err) { showGridMessage(err.message, true); }
  });
  gridBots?.addEventListener('click', async e => {
    const button = e.target.closest('[data-grid-action]');
    if (!button) return;
    const id = button.dataset.gridId;
    const action = button.dataset.gridAction;
    if (action === 'stop' && !window.confirm('Stop this grid and cancel its open orders?')) return;
    button.disabled = true;
    showGridMessage('Sending ' + action + ' request for grid #' + id + '…');
    try {
      const d = await json('/api/grid/bots/' + encodeURIComponent(id) + '/' + action, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({csrf_token: csrf})
      });
      showGridMessage('Grid #' + id + ': ' + d.status + (d.status === 'armed' ? ' and waiting for price to fall to ' + (d.trigger_price || 'trigger') + '.' : '.') + ' Orders submitted: ' +
        (d.submitted ?? 'n/a') + ', canceled: ' + (d.canceled ?? 'n/a') + '.');
      await refreshGridBots();
    } catch (err) {
      showGridMessage(err.message, true);
      button.disabled = false;
    }
  });

  const manualForm = document.getElementById('manual-spot-form');
  const manualMessage = document.getElementById('manual-message');
  const manualType = document.getElementById('manual-order-type');
  const manualPrice = document.getElementById('manual-price');
  const manualSide = document.getElementById('manual-side');
  function syncManualFields() {
    const market = manualType?.value === 'market';
    if (manualPrice) { manualPrice.disabled = market; manualPrice.required = !market; if (market) manualPrice.value = ''; }
    const sell = manualSide?.value === 'sell';
    for (const field of [document.getElementById('manual-sl'), document.getElementById('manual-tp')]) {
      if (field) { field.disabled = sell; if (sell) field.value = ''; }
    }
  }
  manualType?.addEventListener('change', syncManualFields);
  manualSide?.addEventListener('change', syncManualFields);
  syncManualFields();
  manualForm?.addEventListener('submit', async e => {
    e.preventDefault();
    const button = document.getElementById('manual-submit');
    if (button) button.disabled = true;
    if (manualMessage) { manualMessage.className = 'muted'; manualMessage.textContent = 'Validating and submitting order…'; }
    try {
      const d = await json('/api/spot/orders', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams(new FormData(manualForm))
      });
      if (manualMessage) {
        manualMessage.className = 'muted';
        manualMessage.textContent = 'Order #' + d.id + ' status ' + d.status +
          (d.protection ? '. SL/TP protection #' + d.protection.id + ' saved (' + d.protection.status + ').' : '.') +
          (d.error ? ' ' + d.error : '');
      }
      await refreshSpotPanels();
    } catch (err) {
      if (manualMessage) { manualMessage.className = 'alert'; manualMessage.textContent = err.message; }
    } finally { if (button) button.disabled = false; }
  });
  async function refreshSpotPanels() {
    const protectionEl = document.getElementById('spot-protections');
    const ordersEl = document.getElementById('spot-orders');
    if (!protectionEl || !ordersEl) return;
    try {
      const [protections, orders] = await Promise.all([
        json('/api/spot/protections'), json('/api/orders')
      ]);
      protectionEl.innerHTML = protections.length ? protections.map(p => {
        const levels = ['SL ' + (p.stop_loss_price || 'off'), 'TP ' + (p.take_profit_price || 'off')].join(' · ');
        const price = p.last_price ? 'Last ' + p.last_price : 'Price pending';
        const detail = levels + ' · Qty ' + p.protected_quantity + ' · ' + price +
          (p.trigger_reason ? ' · Triggered: ' + p.trigger_reason.replace('_',' ') : '') +
          (p.exit_order_id ? ' · Exit order #' + p.exit_order_id : '');
        return '<article class="strategy-row"><div class="coin-icon">⛨</div><div class="strategy-name"><b>' + esc(p.symbol) + ' · Protection #' + esc(p.id) + '</b><small>Entry #' + esc(p.entry_order_id) + ' · ' + esc(p.environment.toUpperCase()) + ' · ' + esc(detail) + '</small>' +
          (p.last_error ? '<small class="protection-error">' + esc(p.last_error) + '</small>' : '') +
          (p.status === 'waiting_entry' ? '<small>Protection starts after the first fill; a partial entry remainder is canceled if SL/TP triggers.</small>' : '') +
          '</div><span class="state-chip">' + esc(p.status.toUpperCase()) + '</span></article>';
      }).join('') : '<div class="empty-state"><b>No manual SL/TP rules</b><p>Attach stop loss or take profit when submitting a BUY order.</p></div>';
      ordersEl.innerHTML = orders.length ? orders.map(o => {
        const active = ['new','open','partially_filled'].includes(o.status);
        const actions = active ? '<button class="button secondary" data-spot-action="reconcile" data-spot-id="' + esc(o.id) + '">Sync</button><button class="button secondary" data-spot-action="cancel" data-spot-id="' + esc(o.id) + '">Cancel</button>' : '';
        return '<article class="strategy-row"><div class="coin-icon">▤</div><div class="strategy-name"><b>#' + esc(o.id) + ' · ' + esc(o.symbol) + ' · ' + esc(o.side.toUpperCase()) + '</b><small>' + esc(o.environment.toUpperCase()) + ' · ' + esc(o.type.toUpperCase()) + ' · Qty ' + esc(o.filled) + '/' + esc(o.amount) + ' · Price ' + esc(o.price || o.average_price || 'market') + (o.exchange_order_id ? ' · Exchange #' + esc(o.exchange_order_id) : '') + '</small>' + (o.error ? '<small class="protection-error">' + esc(o.error) + '</small>' : '') + '</div><span class="state-chip">' + esc(o.status.toUpperCase()) + '</span><div class="top-actions">' + actions + '</div></article>';
      }).join('') : '<div class="empty-state"><b>No Spot orders yet</b><p>Submitted manual and grid orders will appear here.</p></div>';
    } catch (err) {
      protectionEl.innerHTML = '<div class="alert">' + esc(err.message) + '</div>';
      ordersEl.innerHTML = '<div class="alert">Could not load order ledger.</div>';
    }
  }
  document.getElementById('spot-refresh')?.addEventListener('click', refreshSpotPanels);
  document.getElementById('spot-orders')?.addEventListener('click', async e => {
    const button = e.target.closest('[data-spot-action]');
    if (!button) return;
    const id = button.dataset.spotId;
    const action = button.dataset.spotAction;
    if (action === 'cancel' && !window.confirm('Cancel Spot order #' + id + '? Any already-filled quantity remains in your account.')) return;
    button.disabled = true;
    try {
      await json('/api/spot/orders/' + encodeURIComponent(id) + '/' + action, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({csrf_token: csrf})
      });
      await refreshSpotPanels();
    } catch (err) {
      if (manualMessage) { manualMessage.className = 'alert'; manualMessage.textContent = err.message; }
      button.disabled = false;
    }
  });

  refreshGridBots();
  if (gridBots) setInterval(refreshGridBots, 5000);
  loadAccount(); refreshRisk(); refreshSpotPanels();
  setInterval(refreshSpotPanels, 5000);
})();
