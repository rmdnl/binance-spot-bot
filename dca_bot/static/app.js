document.addEventListener('DOMContentLoaded', () => {
  const tabButtons = document.querySelectorAll('[data-tab]');
  const panels = document.querySelectorAll('[data-panel]');
  const activateTab = (name) => {
    tabButtons.forEach(item => item.classList.toggle('active', item.dataset.tab === name));
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.panel === name));
  };
  tabButtons.forEach(button => button.addEventListener('click', () => activateTab(button.dataset.tab)));
  document.querySelectorAll('a[href="#risk"]').forEach(link => {
    link.addEventListener('click', () => activateTab('risk'));
  });

  const form = document.getElementById('strategy-form');
  if (!form) return;
  const field = name => form.elements.namedItem(name);
  const num = (name, fallback = 0) => {
    const value = Number(field(name)?.value);
    return Number.isFinite(value) ? value : fallback;
  };
  const setField = (name, value) => {
    const element = field(name);
    if (element && value !== undefined && value !== null) element.value = String(value);
  };
  const validation = document.getElementById('validation');
  const previewNode = document.getElementById('ladder-preview');

  const recalc = () => {
    const base = num('base_order_quote', 10);
    const so = num('safety_order_quote', 10);
    const count = Math.max(0, Math.min(20, Math.floor(num('max_safety_orders', 4))));
    const scale = Math.max(1, num('safety_order_volume_scale', 1.5));
    let capital = base + Array.from({length: count}, (_, i) => so * scale ** i).reduce((a, b) => a + b, 0);
    const referencePrice = num('reference_price', 0);
    if (field('averaging_method')?.value === 'fixed_coin_amount' && referencePrice > 0) {
      let cumulativeDeviation = 0;
      capital = base;
      for (let i = 0; i < count; i += 1) {
        cumulativeDeviation += num('initial_deviation_pct', 1) * Math.max(1, num('deviation_scale', 1.5)) ** i;
        const volumeOffset = field('dca_mode')?.value === 'order_averaging' ? 1 : 0;
        const orderPrice = referencePrice * (1 - cumulativeDeviation / 100);
        if (orderPrice <= 0) { capital = Number.POSITIVE_INFINITY; break; }
        capital += num('safety_order_base_quantity', 0.001) * scale ** (i + volumeOffset) * orderPrice;
      }
    }
    const net = num('take_profit_pct', 1) - 2 * num('fee_rate_pct_per_side', .1) - 2 * num('slippage_pct_per_side', .05);
    document.getElementById('estimate').textContent = `${capital.toLocaleString(undefined, {maximumFractionDigits: 2})} USDT`;
    const netNode = document.getElementById('net-tp');
    netNode.textContent = `${net.toFixed(3)}%`;
    netNode.style.color = net < num('min_net_profit_pct', .3) ? 'var(--red)' : 'var(--green)';
    const errors = [];
    if (num('ema_fast', 50) >= num('ema_slow', 200)) errors.push('EMA fast must be below EMA slow.');
    if (num('max_capital_per_deal', 200) < capital) errors.push('Max capital per deal is below estimated DCA order capital.');
    if (num('max_capital_per_deal', 200) * num('max_active_deals', 3) > num('max_total_allocation', 600)) errors.push('Per-deal cap multiplied by active deals exceeds total allocation cap.');
    if (net < num('min_net_profit_pct', .3)) errors.push('Estimated net take profit is below the configured minimum.');
    if (field('trailing_take_profit_enabled')?.value === 'true' && num('trailing_activation_pct', 1) < num('take_profit_pct', 1)) errors.push('Trailing activation must be at least the take-profit target.');
    validation.className = `validation ${errors.length ? 'error' : 'success'}`;
    validation.textContent = errors.length ? `Review required: ${errors.join(' ')}` : 'Basic checks passed. Exchange filters, available balance, and market conditions must still be checked server-side.';
  };

  const makeCell = (tag, text) => {
    const cell = document.createElement(tag);
    cell.textContent = text;
    return cell;
  };
  const renderLadder = data => {
    document.getElementById('estimate').textContent = `${Number(data.total_quote_required).toLocaleString(undefined, {maximumFractionDigits: 4})} USDT`;
    previewNode.replaceChildren();
    const title = document.createElement('div');
    title.className = 'ladder-title';
    title.textContent = `DCA ladder · deepest order -${Number(data.deepest_deviation_pct).toFixed(2)}%`;
    previewNode.append(title);
    const stats = document.createElement('div');
    stats.className = 'ladder-stats';
    [
      ['Total capital', `${Number(data.total_quote_required).toFixed(4)} USDT`],
      ['Average entry', Number(data.average_entry_price).toPrecision(8)],
      ['Base quantity', Number(data.total_base_quantity).toPrecision(8)]
    ].forEach(([label, value]) => {
      const item = document.createElement('div');
      item.append(makeCell('span', label), makeCell('b', value));
      stats.append(item);
    });
    previewNode.append(stats);
    const table = document.createElement('table');
    table.className = 'ladder-table';
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    ['Order', 'Deviation', 'Limit price', 'Quote size'].forEach(value => header.append(makeCell('th', value)));
    head.append(header);
    table.append(head);
    const body = document.createElement('tbody');
    const baseRow = document.createElement('tr');
    ['Base', '0.00%', Number(data.base_price).toPrecision(8), `${Number(data.base_order_quote).toFixed(4)} USDT`].forEach(value => baseRow.append(makeCell('td', value)));
    body.append(baseRow);
    data.safety_orders.forEach(order => {
      const row = document.createElement('tr');
      [`Safety ${order.index}`, `-${Number(order.deviation_pct).toFixed(2)}%`, Number(order.price).toPrecision(8), `${Number(order.quote_amount).toFixed(4)} USDT`].forEach(value => row.append(makeCell('td', value)));
      body.append(row);
    });
    table.append(body);
    previewNode.append(table);
    const note = document.createElement('p');
    note.className = 'muted tiny';
    note.textContent = 'Estimate excludes fees, slippage, exchange precision/minimum-notional rules and order fills. No orders were placed.';
    previewNode.append(note);
  };

  const refreshLadder = async () => {
    const price = document.getElementById('reference_price').value.trim();
    if (!price || Number(price) <= 0) {
      previewNode.textContent = 'Enter or fetch a reference price to calculate the DCA ladder.';
      return;
    }
    const params = new URLSearchParams({
      base_price: price,
      base_order_quote: String(num('base_order_quote', 10)),
      safety_order_quote: String(num('safety_order_quote', 10)),
      safety_order_count: String(Math.floor(num('max_safety_orders', 4))),
      initial_deviation_pct: String(num('initial_deviation_pct', 1)),
      deviation_scale: String(num('deviation_scale', 1.5)),
      safety_volume_scale: String(num('safety_order_volume_scale', 1.5)),
      averaging_method: field('averaging_method')?.value || 'fixed_order_value',
      safety_order_base_quantity: String(num('safety_order_base_quantity', 0.001)),
      dca_mode: field('dca_mode')?.value || 'position_averaging'
    });
    previewNode.textContent = 'Calculating ladder…';
    try {
      const response = await fetch(`/api/dca/preview?${params}`, {headers: {'Accept': 'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not calculate ladder');
      renderLadder(data);
    } catch (error) {
      previewNode.textContent = error.message || 'Could not calculate ladder.';
    }
  };

  document.getElementById('fetch-price-button').addEventListener('click', async () => {
    const firstSymbol = document.getElementById('symbols').value.split(',')[0].trim();
    const button = document.getElementById('fetch-price-button');
    button.disabled = true;
    button.textContent = 'Fetching…';
    try {
      const response = await fetch(`/api/market/ticker?symbol=${encodeURIComponent(firstSymbol)}`, {headers: {'Accept': 'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not fetch Spot price');
      document.getElementById('reference_price').value = data.last;
      recalc();
      await refreshLadder();
    } catch (error) {
      previewNode.textContent = error.message || 'Could not fetch Spot price.';
    } finally {
      button.disabled = false;
      button.textContent = 'Fetch Spot price ↻';
    }
  });

  const csrfToken = () => document.querySelector('#strategy-form input[name="csrf_token"]')?.value || '';
  const paperList = document.getElementById('paper-deals-list');
  const paperCell = (tag, text, className) => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const refreshPaperDeals = async (tick = true) => {
    if (!paperList) return;
    paperList.textContent = 'Loading paper deals…';
    try {
      if (tick) {
        const tickResponse = await fetch('/api/paper/deals/tick', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
          body: new URLSearchParams({csrf_token: csrfToken()})
        });
        if (!tickResponse.ok) {
          const err = await tickResponse.json();
          throw new Error(err.detail || 'Could not refresh Spot prices');
        }
      }
      const response = await fetch('/api/paper/deals', {headers: {'Accept': 'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load paper deals');
      paperList.replaceChildren();
      const activeCount = data.deals.filter(deal => deal.status === 'active').length;
      const countChip = document.querySelector('#deals .section-heading .count-chip');
      if (countChip) countChip.textContent = `${activeCount} active`;
      if (!data.deals.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.append(paperCell('span', '◷'), paperCell('b', 'No paper deals'), paperCell('p', 'Start a paper deal from a saved strategy profile.'));
        paperList.append(empty);
        return;
      }
      data.deals.forEach(deal => {
        const card = document.createElement('article');
        card.className = 'paper-deal-card';
        const head = document.createElement('div');
        head.className = 'paper-deal-head';
        head.append(paperCell('b', deal.symbol), paperCell('span', deal.status.toUpperCase(), `state-chip ${deal.status === 'active' ? 'paper-active' : ''}`));
        card.append(head);
        const stats = document.createElement('div');
        stats.className = 'paper-deal-stats';
        [
          ['Average entry', Number(deal.average_entry_price).toPrecision(8)],
          ['Last price', Number(deal.last_price).toPrecision(8)],
          ['Capital used', `${Number(deal.quote_spent).toFixed(4)} USDT`],
          ['Unrealized PnL', `${Number(deal.unrealized_pnl_quote).toFixed(4)} USDT`],
          ['Safety orders', `${deal.safety_orders_filled}/${deal.safety_orders_total}`],
          ['Realized PnL', `${Number(deal.realized_pnl_quote).toFixed(4)} USDT`]
        ].forEach(([label, value]) => {
          const item = document.createElement('div');
          item.append(paperCell('span', label), paperCell('b', value));
          stats.append(item);
        });
        card.append(stats);
        if (deal.close_reason) card.append(paperCell('p', `Closed: ${deal.close_reason}`, 'muted tiny'));
        paperList.append(card);
      });
    } catch (error) {
      paperList.textContent = error.message || 'Could not refresh paper deals.';
    }
  };

  const riskList = document.getElementById('risk-status-list');
  const refreshRiskStatus = async () => {
    if (!riskList) return;
    riskList.textContent = 'Checking paper risk guards…';
    try {
      const response = await fetch('/api/risk/status', {headers: {'Accept': 'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Risk status unavailable');
      riskList.replaceChildren();
      if (!data.strategies.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.append(
          paperCell('span', '◇'),
          paperCell('b', 'No saved strategies'),
          paperCell('p', 'Save a strategy to initialize its paper risk guards.')
        );
        riskList.append(empty);
        return;
      }
      data.strategies.forEach(item => {
        const card = document.createElement('article');
        card.className = 'paper-deal-card';
        const head = document.createElement('div');
        head.className = 'paper-deal-head';
        const stateLabel = item.status === 'clear' ? 'ENTRY GUARDS CLEAR' : item.status === 'blocked' ? 'ENTRIES BLOCKED' : 'RISK STATUS UNKNOWN';
        head.append(
          paperCell('b', item.name),
          paperCell('span', stateLabel, `state-chip ${item.status === 'clear' ? 'paper-active' : ''}`)
        );
        card.append(head);
        if (item.status !== 'error') {
          const stats = document.createElement('div');
          stats.className = 'paper-deal-stats';
          [
            ['Virtual equity', `${Number(item.equity_quote).toFixed(2)} USDT`],
            ['Peak equity', `${Number(item.peak_equity_quote).toFixed(2)} USDT`],
            ['Drawdown', `${Number(item.drawdown_pct).toFixed(3)}%`],
            ['Daily realized PnL (UTC)', `${Number(item.daily_realized_pnl_quote).toFixed(4)} USDT`]
          ].forEach(([label, value]) => {
            const cell = document.createElement('div');
            cell.append(paperCell('span', label), paperCell('b', value));
            stats.append(cell);
          });
          card.append(stats);
        }
        if (item.reason) card.append(paperCell('p', item.reason, 'muted tiny'));
        riskList.append(card);
      });
      if (!data.entries_enabled) {
        riskList.prepend(paperCell('p', 'At least one strategy is blocked or its risk state could not be verified. Entry must remain blocked for that strategy.', 'validation error'));
      }
    } catch (error) {
      riskList.replaceChildren(paperCell('p', error.message || 'Risk status unavailable. Treat new entries as blocked until risk checks recover.', 'validation error'));
    }
  };

  document.getElementById('refresh-risk-status')?.addEventListener('click', refreshRiskStatus);
  refreshRiskStatus();

  document.querySelectorAll('[data-start-paper]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    const original = button.textContent;
    button.textContent = 'Starting…';
    try {
      const response = await fetch('/api/paper/deals/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
        body: new URLSearchParams({
          csrf_token: csrfToken(),
          strategy_id: button.dataset.startPaper,
          symbol: button.dataset.symbol
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not start paper deal');
      activateTab('basic');
      document.getElementById('deals').scrollIntoView({behavior:'smooth', block:'start'});
      await refreshPaperDeals(false);
    } catch (error) {
      validation.className = 'validation error';
      validation.textContent = error.message || 'Could not start paper deal.';
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }));

  document.getElementById('refresh-paper-deals')?.addEventListener('click', () => refreshPaperDeals(true));
  refreshPaperDeals(false);

  document.querySelectorAll('[data-edit]').forEach(button => button.addEventListener('click', () => {
    let item;
    try { item = JSON.parse(button.getAttribute('data-edit')); } catch { return; }
    const config = item.config || {};
    const autoPaperCheckbox = field('auto_paper_enabled');
    if (autoPaperCheckbox) autoPaperCheckbox.checked = Boolean(item.enabled && config.start_condition === 'autonomous');
    setField('strategy_id', item.id);
    setField('name', config.name || item.name);
    setField('symbols', (config.symbols || item.symbols || []).join(', '));
    [
      'base_order_quote','safety_order_quote','max_safety_orders','take_profit_pct','take_profit_basis',
      'dca_mode','averaging_method','safety_order_base_quantity','start_condition','stop_loss_basis',
      'breakeven_activation_pct','breakeven_offset_pct','max_deal_duration_hours',
      'initial_deviation_pct','max_active_deals','order_size_mode','base_order_balance_pct',
      'safety_order_volume_scale','deviation_scale','trailing_activation_pct',
      'trailing_callback_pct','stop_loss_pct','entry_filter','timeframe','ema_fast',
      'ema_slow','rsi_period','rsi_max_entry','adx_min','atr_period','cooldown_seconds',
      'order_timeout_seconds','max_capital_per_deal','max_total_allocation','reserve_balance',
      'daily_loss_limit_quote','max_equity_drawdown_pct','min_net_profit_pct',
      'fee_rate_pct_per_side','slippage_pct_per_side','retry_limit','reconciliation_seconds','order_type'
    ].forEach(name => setField(name, config[name]));
    ['trailing_take_profit_enabled','stop_loss_enabled','move_stop_to_breakeven_enabled','reinvest_profit'].forEach(name => setField(name, config[name]));
    activateTab('basic');
    recalc();
    refreshLadder();
    document.getElementById('strategy-editor').scrollIntoView({behavior:'smooth', block:'start'});
  }));

  document.getElementById('preview-button').addEventListener('click', () => { recalc(); refreshLadder(); });
  form.querySelectorAll('input,select').forEach(input => input.addEventListener('input', recalc));
  form.addEventListener('submit', recalc);
  recalc();
});
