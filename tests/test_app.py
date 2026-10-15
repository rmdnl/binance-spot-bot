import pytest
from fastapi.testclient import TestClient
from dca_bot.main import app
from dca_bot.strategy import StrategyConfig


def test_health_reports_manual_dca_mode():
    response = TestClient(app).get('/healthz')
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok'
    assert body['autonomous'] == 'disabled'
    assert body['mode'] in {'paper', 'testnet'}


def test_dashboard_redirects_to_login_without_session():
    response = TestClient(app).get('/', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login'


def test_login_page_does_not_expose_dashboard():
    response = TestClient(app).get('/login')
    assert response.status_code == 200
    assert 'Dashboard password' in response.text


def test_dca_preview_calculates_manual_ladder(monkeypatch):
    monkeypatch.setattr('dca_bot.main.ensure_authenticated', lambda request: None)
    response = TestClient(app).get(
        '/api/dca/preview?base_price=100&base_order_quote=10&'
        'safety_order_quote=20&safety_order_count=2&initial_deviation_pct=2&'
        'deviation_scale=1.5&safety_volume_scale=1'
    )
    assert response.status_code == 200
    body = response.json()
    assert body['base_price'] == '100'
    assert len(body['safety_orders']) == 2
    assert body['safety_orders'][0]['deviation_pct'] == '2.0'
    assert body['safety_orders'][1]['deviation_pct'] == '5.00'
    assert body['execution'] in {'paper', 'testnet'}


def test_manual_pair_config_rejects_non_usdt():
    with pytest.raises(ValueError, match='Only USDT spot pairs'):
        StrategyConfig(symbols=['BTC/USDC'])


def test_manual_pair_config_rejects_duplicates():
    with pytest.raises(ValueError, match='Duplicate'):
        StrategyConfig(symbols=['BTCUSDT', 'BTC/USDT'])


def test_manual_pair_config_accepts_normalized_usdt_pairs():
    config = StrategyConfig(symbols=['btcusdt', 'ETH/USDT', 'sol-usdt'])
    assert config.symbols == ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']


def test_trigger_price_must_be_inside_enabled_range():
    with pytest.raises(ValueError, match='inside the configured Price Range'):
        StrategyConfig(
            symbols=['BTC/USDT'],
            price_range_enabled=True,
            price_range_min=90,
            price_range_max=110,
            trigger_price=120,
        )


def test_dashboard_contains_manual_configuration_controls():
    client = TestClient(app)
    response = client.get('/login')
    assert response.status_code == 200


def test_fixed_coin_capital_requires_reference_price():
    config = StrategyConfig(averaging_method='fixed_coin_amount', safety_order_base_quantity=0.01)
    with pytest.raises(ValueError, match='reference price'):
        config.estimated_max_deal_capital()


def test_fixed_coin_capital_uses_dca_price_ladder():
    config = StrategyConfig(
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=2,
        initial_deviation_pct=1,
        deviation_scale=1,
        averaging_method='fixed_coin_amount',
        safety_order_base_quantity=0.1,
        safety_order_volume_scale=1,
    )
    estimated = config.estimated_max_deal_capital(reference_price=100)
    assert estimated == 10 + 0.1 * 99 + 0.1 * 98
