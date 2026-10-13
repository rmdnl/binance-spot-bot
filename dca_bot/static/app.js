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
    const capital = base + Array.from({length: count}, (_, i) => so * scale ** i).reduce((a, b) => a + b, 0);
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
      safety_volume_scale: String(num('safety_order_volume_scale', 1.5))
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

  document.querySelectorAll('[data-edit]').forEach(button => button.addEventListener('click', () => {
    let item;
    try { item = JSON.parse(button.getAttribute('data-edit')); } catch { return; }
    const config = item.config || {};
    setField('strategy_id', item.id);
    setField('name', config.name || item.name);
    setField('symbols', (config.symbols || item.symbols || []).join(', '));
    [
      'base_order_quote','safety_order_quote','max_safety_orders','take_profit_pct',
      'initial_deviation_pct','max_active_deals','order_size_mode','base_order_balance_pct',
      'safety_order_volume_scale','deviation_scale','trailing_activation_pct',
      'trailing_callback_pct','stop_loss_pct','entry_filter','timeframe','ema_fast',
      'ema_slow','rsi_period','rsi_max_entry','adx_min','atr_period','cooldown_seconds',
      'order_timeout_seconds','max_capital_per_deal','max_total_allocation','reserve_balance',
      'daily_loss_limit_quote','max_equity_drawdown_pct','min_net_profit_pct',
      'fee_rate_pct_per_side','slippage_pct_per_side','retry_limit','reconciliation_seconds','order_type'
    ].forEach(name => setField(name, config[name]));
    ['trailing_take_profit_enabled','stop_loss_enabled'].forEach(name => setField(name, config[name]));
    activateTab('basic');
    recalc();
    document.getElementById('strategy-editor').scrollIntoView({behavior:'smooth', block:'start'});
  }));

  document.getElementById('preview-button').addEventListener('click', () => { recalc(); refreshLadder(); });
  form.querySelectorAll('input,select').forEach(input => input.addEventListener('input', recalc));
  form.addEventListener('submit', recalc);
  recalc();
});
