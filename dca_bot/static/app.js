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

  function money(value, currency) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return new Intl.NumberFormat(currency === 'IDR' ? 'id-ID' : 'en-US', {
      style: 'currency',
      currency,
      maximumFractionDigits: currency === 'IDR' ? 0 : 2,
    }).format(number);
  }

  async function fetchJson(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(url, {...options, signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.msg || data.detail || `HTTP ${response.status}`);
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function calculateTotalEquity(balances) {
    if (!balances.length) return {usd: 0, idr: null, rate: null, valued: 0, total: 0};

    const prices = await fetchJson('https://testnet.binance.vision/api/v3/ticker/price');
    const priceMap = new Map((Array.isArray(prices) ? prices : []).map(x => [String(x.symbol).toUpperCase(), Number(x.price)]));

    const directUsd = asset => {
      const value = priceMap.get(`${asset}USDT`);
      return Number.isFinite(value) && value > 0 ? value : null;
    };

    const via = (asset, bridge, bridgeUsd) => {
      const value = priceMap.get(`${asset}${bridge}`);
      return Number.isFinite(value) && value > 0 && Number.isFinite(bridgeUsd) ? value * bridgeUsd : null;
    };

    const assetUsd = asset => {
      const a = String(asset).toUpperCase();
      if (a === 'USDT' || a === 'USD' || a === 'USDC') return 1;
      const direct = directUsd(a);
      if (direct !== null) return direct;
      const btcUsd = directUsd('BTC');
      if (btcUsd !== null) {
        const value = via(a, 'BTC', btcUsd);
        if (value !== null) return value;
      }
      const ethUsd = directUsd('ETH');
      if (ethUsd !== null) {
        const value = via(a, 'ETH', ethUsd);
        if (value !== null) return value;
      }
      return null;
    };

    let usd = 0;
    let valued = 0;
    for (const row of balances) {
      const total = Number(row.total);
      if (!Number.isFinite(total) || total <= 0) continue;
      const unitUsd = assetUsd(row.asset);
      if (unitUsd === null) continue;
      usd += total * unitUsd;
      valued += 1;
    }

    let idr = null;
    let rate = null;
    try {
      const fx = await fetchJson('https://open.er-api.com/v6/latest/USD');
      const candidate = Number(fx?.rates?.IDR);
      if (Number.isFinite(candidate) && candidate > 0) {
        rate = candidate;
        idr = usd * rate;
      }
    } catch (_) {
      // USD equity remains available if the external FX service is unavailable.
    }

    return {usd, idr, rate, valued, total: balances.length};
  }

  function equityPanel(valuation) {
    const idrText = valuation.idr === null ? 'IDR rate unavailable' : money(valuation.idr, 'IDR');
    const rateText = valuation.rate === null ? 'USD/IDR unavailable' : `1 USD ≈ ${new Intl.NumberFormat('id-ID').format(valuation.rate)} IDR`;
    return `<section class="panel" id="equity"><div class="section-heading"><div><p class="eyebrow">PORTFOLIO VALUATION</p><h2>Total Equity</h2><p class="muted">All non-zero Spot balances valued into a common display currency. Native quote buckets remain unchanged for accounting and risk.</p></div><span class="draft-chip">READ ONLY</span></div><div class="metrics"><article class="metric"><div class="metric-head"><span>Total Equity · USD</span><span>$</span></div><h3>${esc(money(valuation.usd, 'USD'))}</h3><p class="muted">Portfolio market value</p><div class="metric-foot">● Live Spot valuation</div></article><article class="metric"><div class="metric-head"><span>Total Equity · IDR</span><span>Rp</span></div><h3>${esc(idrText)}</h3><p class="muted">Indonesian Rupiah display</p><div class="metric-foot neutral">${esc(rateText)}</div></article><article class="metric"><div class="metric-head"><span>Valued assets</span><span>◉</span></div><h3>${valuation.valued} <small>/ ${valuation.total}</small></h3><p class="muted">Balances with a market path</p><div class="metric-foot neutral">Unpriced assets excluded</div></article></div></section>`;
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
      if (!data.available) {
        anchor.insertAdjacentHTML('afterend', accountPanel(data));
        return;
      }
      const balances = data.balances || [];
      try {
        const valuation = await calculateTotalEquity(balances);
        anchor.insertAdjacentHTML('afterend', equityPanel(valuation));
      } catch (_) {
        anchor.insertAdjacentHTML('afterend', equityPanel({usd: 0, idr: null, rate: null, valued: 0, total: balances.length}));
      }
      const equity = document.getElementById('equity');
      if (equity) equity.insertAdjacentHTML('afterend', accountPanel(data));
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
