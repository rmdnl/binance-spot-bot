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
      m.realized_pnl_complete ? 'Closed fills from the realized PNL ledger' : 'Some quote currencies could not be converted to USDT');
    metric('metric-unrealized', (m.unrealized_pnl_complete ? '' : '~ ') + usdt(m.unrealized_pnl_usdt),
      m.unrealized_pnl_complete ? 'Open inventory marked to current market prices' : 'Some open positions could not be valued');
  }
  async function refreshRisk(){ const el=document.getElementById('risk-status'); if(!el)return; try{const d=await json('/api/risk/status');el.innerHTML=`<b>${esc(d.status.toUpperCase())}</b><p class="muted">Drawdown ${esc(d.drawdown_pct)}% · Daily PNL ${esc(d.daily_realized_pnl_quote)} USDT</p>`;}catch(e){el.textContent=e.message;} }
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
  loadAccount(); refreshRisk();
})();
