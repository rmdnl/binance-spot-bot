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
    const box = document.getElementById('validation');
    box.className = `validation ${errors.length ? 'error' : 'success'}`;
    box.textContent = errors.length ? `Review required: ${errors.join(' ')}` : 'Basic checks passed. Exchange filters, available balance, and market conditions must still be checked server-side.';
  };

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

  document.getElementById('preview-button').addEventListener('click', recalc);
  form.querySelectorAll('input,select').forEach(input => input.addEventListener('input', recalc));
  form.addEventListener('submit', recalc);
  recalc();
});
