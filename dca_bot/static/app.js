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

  loadRisk();
})();
