document.addEventListener('DOMContentLoaded', () => {
  const tabButtons = document.querySelectorAll('[data-tab]');
  const panels = document.querySelectorAll('[data-panel]');
  tabButtons.forEach(button => button.addEventListener('click', () => {
    tabButtons.forEach(item => item.classList.toggle('active', item === button));
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.panel === button.dataset.tab));
  }));
  const form = document.getElementById('strategy-form');
  if (!form) return;
  const field = name => form.elements.namedItem(name);
  const num = (name, fallback = 0) => { const value = Number(field(name)?.value); return Number.isFinite(value) ? value : fallback; };
  const recalc = () => {
    const base = num('base_order_quote', 10);
    const so = num('safety_order_quote', 10);
    const count = Math.max(0, Math.min(20, Math.floor(num('max_safety_orders', 4))));
    const scale = Math.max(1, num('safety_order_volume_scale', 1.5));
    const capital = base + Array.from({length: count}, (_, i) => so * scale ** i).reduce((a, b) => a + b, 0);
    const net = num('take_profit_pct', 1) - 2 * num('fee_rate_pct_per_side', .1) - 2 * num('slippage_pct_per_side', .05);
    document.getElementById('estimate').textContent = `${capital.toLocaleString(undefined, {maximumFractionDigits: 2})} USDT`;
    const netNode = document.getElementById('net-tp'); netNode.textContent = `${net.toFixed(3)}%`; netNode.style.color = net < num('min_net_profit_pct', .3) ? 'var(--red)' : 'var(--green)';
    const errors = [];
    if (num('ema_fast', 50) >= num('ema_slow', 200)) errors.push('EMA fast must be below EMA slow.');
    if (num('max_capital_per_deal', 200) < capital) errors.push('Max capital per deal is below estimated DCA order capital.');
    if (num('max_total_allocation', 600) < num('max_capital_per_deal', 200)) errors.push('Total allocation must cover at least one maximum deal.');
    if (net < num('min_net_profit_pct', .3)) errors.push('Estimated net take profit is below the configured minimum.');
    if (field('trailing_take_profit_enabled')?.value === 'true' && num('trailing_activation_pct', 1) < num('take_profit_pct', 1)) errors.push('Trailing activation must be at least the take-profit target.');
    const box = document.getElementById('validation'); box.className = `validation ${errors.length ? 'error' : 'success'}`;
    box.textContent = errors.length ? `Review required: ${errors.join(' ')}` : 'Basic checks passed. Exchange filters, available balance, and market conditions must still be checked server-side.';
  };
  document.getElementById('preview-button').addEventListener('click', recalc);
  form.querySelectorAll('input,select').forEach(input => input.addEventListener('input', recalc));
  form.addEventListener('submit', recalc);
  recalc();
});
