import re

from web.control_panel import app
from web.control_panel import TEMPLATE_PATH
from web.control_panel import LIQUIDATION_TEMPLATE_PATH
from web.control_panel import OPPORTUNITY_HEALTH_TEMPLATE_PATH


def test_exchange_matrix_page_is_standalone():
    client = app.test_client()

    response = client.get("/exchange-matrix")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "兑换矩阵复盘" in body
    assert "5×5 兑换路径矩阵" in body
    assert "/api/velocity-timepoints" in body
    assert "/api/velocity-summary" in body


def test_control_and_liquidation_pages_link_exchange_matrix_without_inline_matrix():
    client = app.test_client()

    control_body = client.get("/").get_data(as_text=True)
    liquidation_body = client.get("/liquidation").get_data(as_text=True)

    assert "/exchange-matrix" in control_body
    assert "/exchange-matrix" in liquidation_body
    assert "5×5 兑换胜率汇总" not in control_body
    assert "5×5 兑换路径矩阵" not in control_body
    assert "refreshLiquidationSettings(" not in control_body


def test_opportunity_health_page_is_standalone():
    client = app.test_client()

    response = client.get("/opportunity-health")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "机会健康诊断" in body
    assert "全量机会健康度列表" in body
    assert "/api/opportunity-health" in body


def test_control_page_links_opportunity_health_without_inline_table():
    body = app.test_client().get("/").get_data(as_text=True)

    assert "/opportunity-health" in body
    assert "全量机会健康度列表" not in body
    assert "opportunityHealthBody" not in body


def test_control_page_keeps_liquidation_account_health_as_primary_table():
    body = app.test_client().get("/").get_data(as_text=True)

    assert "Aave 清算总览" in body
    assert "全体清算账户健康度" in body
    assert "liquidationHealthBody" in body
    assert "/api/liquidation-health" in body
    assert body.index("全体清算账户健康度") < body.index("机会总数")
    assert "/liquidation/account?account=" in body


def test_liquidation_page_accepts_account_query_from_overview():
    body = app.test_client().get("/liquidation/account").get_data(as_text=True)

    assert 'new URLSearchParams(location.search).get("account")' in body
    assert "refreshAccount(accountParam)" in body


def test_liquidation_monitor_separates_account_pool_from_dynamic_health():
    body = app.test_client().get("/liquidation").get_data(as_text=True)

    assert "账户池" in body
    assert "动态借贷健康度" in body
    assert "/api/liquidation/accounts" in body
    assert "Aave 单账户清算分析" not in body


def test_liquidation_account_page_is_standalone():
    body = app.test_client().get("/liquidation/account").get_data(as_text=True)

    assert "Aave 单账户清算分析" in body
    assert "/api/liquidation/account?account=" in body


def test_control_panel_script_references_existing_dom_ids():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    declared_ids = set(re.findall(r'id="([^"]+)"', template))
    referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', template))

    assert referenced_ids <= declared_ids


def test_opportunity_health_script_references_existing_dom_ids():
    template = OPPORTUNITY_HEALTH_TEMPLATE_PATH.read_text(encoding="utf-8")
    declared_ids = set(re.findall(r'id="([^"]+)"', template))
    referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', template))

    assert referenced_ids <= declared_ids


def test_liquidation_panel_script_references_existing_dom_ids():
    template = LIQUIDATION_TEMPLATE_PATH.read_text(encoding="utf-8")
    declared_ids = set(re.findall(r'id="([^"]+)"', template))
    referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', template))

    assert referenced_ids <= declared_ids
