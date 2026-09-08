from core.html_utils import (
    add_body_attribute,
    add_body_class,
    inject_before_head_close,
    prepare_embedded_html,
)
from bot.liquidation.venus_panel_support import (
    _health_display,
    _page,
    _page_size,
    _pagination,
    _refresh_pool_tier,
    _slice,
)


def test_html_utils_keep_body_surface_and_embedded_class():
    html = "<html><head></head><body class='x'></body></html>"

    with_surface = add_body_attribute(html, "data-page-surface", "observation")

    assert 'data-page-surface="observation"' in with_surface
    assert "class='x'" in with_surface

    embedded = prepare_embedded_html(html)
    assert "body.embedded" in embedded
    assert "embedded" in embedded


def test_inject_before_head_close_inserts_markup_before_closing_tag():
    assert inject_before_head_close("<head></head>", "<meta>") == "<head><meta></head>"


def test_venus_panel_support_pagination_and_tiers():
    assert _page(0) == 1
    assert _page_size(500) == 100
    assert _pagination(1, 10, 5)["page_count"] == 1
    assert _slice([{"a": 1}, {"a": 2}], 1, 1) == [{"a": 1}]
    assert _refresh_pool_tier(1.2) == "borrow_health"
    assert _health_display(1.234, {}, {}) == "1.234"
    assert _health_display(None, {"liquidity_raw": 10}, {}) == "L 10 / S 0"
    assert _health_display(None, {}, {}) == "-"
