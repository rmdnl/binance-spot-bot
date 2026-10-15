(() => {
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';

  document.querySelectorAll('[data-tab]').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-tab]').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('[data-panel]').forEach(x => x.classList.remove('active'));
      button.classList.add('active');
      document.querySelector(`[data-panel="${button.dataset.tab}"]`)?.classList.add('active');
    });
  });

  document.querySelectorAll('[data-start-dca]').forEach(button => {
    button.addEventListener('click', async () => {
      const symbol = button.dataset.startDca;
      if (!confirm(`Start DCA for ${symbol}?`)) return;
      button.disabled = true;
      try {
        const body = new URLSearchParams({ csrf_token: csrf, symbol });
        const response = await fetch('/api/dca/deals/start', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'DCA start failed');
        location.reload();
      } catch (error) {
        alert(error.message);
        button.disabled = false;
      }
    });
  });

  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }

  function accountPanel(data) {
    if (!data.available) {
      return `<section class="panel" id="account"><div class="section-heading"><div><p class="eyebrow">BINANCE ACCOUNT</p><h2>Balance & PNL</h2><p class="muted">Read-only account data.</p></div></div><div class="alert" role="alert">${esc(data.error)}</div></section>`;
    }
    const balances = data.balances || [];
    const pnl = data.pnl || [];
    const trades = data.trades || [];
    const balanceRows = balances.map(x => `<article class="strategy-row"><div class="coin-icon">◈</div><div class="strategy-name"><b>${esc(x.asset)}</b><small>Free ${esc(x.free)} · Used ${esc(x.used)}</small></div><span class="state-chip">${esc(x.total)}</span></article>`).join('') || '<div class="empty-state"><b>No non-zero balances</b></div>';
    const pnlRows = pnl.map(x => `<article class="strategy-row"><div class="coin-icon">↗</div><div class="strategy-name"><b>${esc(x.symbol)}</b><small>${esc(x.quantity)} ${esc(x.base)} · mark ${esc(x.mark_price)} ${esc(x.quote)}</small></div><span class="muted">Realized ${esc(x.realized)} ${esc(x.quote)}</span><span class="muted">Unrealized ${esc(x.unrealized)} ${esc(x.quote)}</span></article>`).join('') || '<div class="empty-state"><b>No PNL rows</b></div>';
    const tradeRows = trades.map(x => `<article class="strategy-row"><div class="coin-icon">◷</div><div class="strategy-name"><b>${esc(x.symbol)}</b><small>${esc(x.side).toUpperCase()} · ${esc(x.amount)} @ ${esc(x.price)} · fee ${esc(x.fee)}${x.fee_currency ? ` ${esc(x.fee_currency)}` : ''}</small></div><span class="muted">${esc(x.cost)}</span></article>`).join('') || '<div class="empty-state"><b>No recent trades</b></div>';
    return `<section class="panel" id="account"><div class="section-heading"><div><p class="eyebrow">BINANCE ACCOUNT</p><h2>Balance & PNL</h2><p class="muted">Read-only Binance Spot account overview. No orders are created or changed here.</p></div><span class="draft-chip">READ ONLY</span></div><div class="metrics"><article class="metric"><div class="metric-head"><span>Non-zero assets</span><span>◉</span></div><h3>${balances.length}</h3><p class="muted">Spot balances</p><div class="metric-foot">● Connected</div></article><article class="metric"><div class="metric-head"><span>Realized PNL</span><span>↗</span></div><h3>Per pair</h3><p class="muted">Quote currency preserved</p><div class="metric-foot neutral">No mixed-currency total</div></article><article class="metric"><div class="metric-head"><span>Unrealized PNL</span><span>◇</span></div><h3>Per pair</h3><p class="muted">Mark vs cost basis</p><div class="metric-foot neutral">Quote currency preserved</div></article><article class="metric"><div class="metric-head"><span>Recent trades</span><span>▦</span></div><h3>${trades.length}</h3><p class="muted">Configured pairs</p><div class="metric-foot">● Read only</div></article></div><div class="strategy-list">${balanceRows}</div><div class="deal-list">${pnlRows}</div><div class="deal-list">${tradeRows}</div><p class="muted">${esc(data.note || '')}</p></section>`;
  }

  async function loadAccount() {
    const anchor = document.getElementById('overview');
    if (!anchor || document.getElementById('account')) return;
    try {
      const response = await fetch('/api/account');
      const data = await response.json().catch(() => ({available: false, error: 'Invalid account response'}));
      if (!response.ok) throw new Error(data.detail || 'Binance account unavailable');
      anchor.insertAdjacentHTML('afterend', accountPanel(data));
    } catch (error) {
      anchor.insertAdjacentHTML('afterend', accountPanel({available: false, error: error.message}));
    }
  }

  async function loadRisk() {
    const target = document.getElementById('risk-status');
    if (!target) return;
    try {
      const response = await fetch('/api/risk/status');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Risk status unavailable');
      target.innerHTML = `<b>${data.status.toUpperCase()}</b><p class="muted">Equity ${data.equity_quote} USDT · drawdown ${data.drawdown_pct}% · daily PnL ${data.daily_realized_pnl_quote} USDT</p>`;
    } catch (error) {
      target.textContent = error.message;
    }
  }

  loadAccount();
  loadRisk();
})();
