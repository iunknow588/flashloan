from web.control_panel import app


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
