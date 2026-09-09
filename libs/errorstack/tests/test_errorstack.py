from libs.errorstack import ErrorStack, ModuleError, ResultStamp, stack_from_legacy


def test_result_stamp_serializes_without_registry():
    stamp = ModuleError(
        module="app-b",
        code=0,
        message="loaded",
    )
    data = stamp.as_dict()
    assert data == {"module": "app-b", "code": 0, "message": "loaded"}
    assert ResultStamp is ModuleError


def test_stack_pushes_any_module_owned_error_codes():
    stack = ErrorStack(trace_id="app-1")
    stack.push(ModuleError(module="app-b", code=715, message="database timeout"))
    assert stack.errors[0].code == 715
    assert stack.as_list()[0] == {"module": "app-b", "code": 715, "message": "database timeout"}


def test_stack_preserves_push_order_and_does_not_parse():
    stack = ErrorStack(trace_id="route")
    stack.push(ModuleError(module="dex.hop1", code=0, message="ok, not an error"))
    stack.push(ModuleError(module="dex.hop2", code=91, message="hop 2 pool not ready"))
    assert [item["module"] for item in stack.as_list()] == ["dex.hop2"]
    assert stack.as_dict()["traceId"] == "route"
    assert len(stack.as_dict()["errorStack"]) == 1
    assert "not an error" not in stack.as_dict()["errorStack"][0]["message"]


def test_mark_ok_replaces_child_ok_with_single_boundary_element():
    stack = ErrorStack(trace_id="route")
    stack.push(ModuleError(module="dex.hop1", code=0, message="hop 1 ok"))
    stack.push(ModuleError(module="dex.hop2", code=0, message="hop 2 ok"))
    stack.mark_ok(ModuleError(module="dex.route", code=0, message="OK"))

    assert stack.as_list() == [{"module": "dex.route", "code": 0, "message": "OK"}]


def test_mark_ok_does_not_hide_existing_child_error():
    stack = ErrorStack()
    stack.push(ModuleError(module="dex.hop1", code=91, message="hop 1 pool not ready"))
    stack.mark_ok(ModuleError(module="dex.route", code=0, message="OK"))

    assert stack.as_list() == [{"module": "dex.hop1", "code": 91, "message": "hop 1 pool not ready"}]


def test_adopt_children_adopts_errors_and_replaces_ok_with_boundary_ok():
    child_error = ErrorStack()
    child_error.push(ModuleError(module="dex.hop1", code=91, message="pool missing"))
    child_ok = ErrorStack()
    child_ok.push(ModuleError(module="dex.hop2", code=0, message="ok"))

    stack = ErrorStack()
    stack.adopt_children([child_ok], ok_element=ModuleError(module="dex.route", code=0, message="OK"))
    assert stack.as_list() == [{"module": "dex.route", "code": 0, "message": "OK"}]

    stack.adopt_children([child_error, child_ok])
    assert stack.as_list() == [{"module": "dex.hop1", "code": 91, "message": "pool missing"}]


def test_stack_does_not_import_project_packages():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "error_stack.py"
    text = source.read_text(encoding="utf-8")
    assert "import core" not in text
    assert "from core" not in text


def test_stack_from_legacy_accepts_json_rows():
    payload = {
        "errorStack": [
            {"module": "app", "code": 1, "message": "bad"}
        ]
    }
    stack = stack_from_legacy(payload)
    assert stack.errors[0].module == "app"
    assert stack.errors[0].message == "bad"


def test_stack_from_legacy_downgrades_old_complex_stamp():
    payload = {
        "errorStack": [
            {"source": "dex", "node_id": "hop_1", "node_index": 1, "code": 90, "error_key": "DEX.HOP", "message": "bad hop"}
        ]
    }
    stack = stack_from_legacy(payload)
    assert stack.errors[0].module == "dex"
    assert stack.errors[0].code == 90
    assert stack.errors[0].message == "bad hop"


def test_stack_omits_ok_rows_when_loaded_from_legacy():
    payload = {
        "errorStack": [
            {"module": "app", "code": 0, "message": "fine"},
            {"module": "app", "code": 1, "message": "bad"},
        ]
    }
    stack = stack_from_legacy(payload)
    assert stack.as_list() == [{"module": "app", "code": 1, "message": "bad"}]


def test_mask_internal_and_finalize_clear_ok_stamps():
    stack = ErrorStack(trace_id="scope-1")
    stack.push(ModuleError(module="internal.detail", code=0, message="ok"))
    stack.push(ModuleError(module="internal.detail", code=0, message="also ok"))
    payload = stack.finalize()
    assert payload == {
        "traceId": "scope-1",
        "errorStack": [{"module": "internal.detail", "code": 0, "message": "also ok"}],
    }


def test_mask_internal_keeps_only_errors():
    stack = ErrorStack()
    stack.push(ModuleError(module="internal.detail", code=0, message="ok"))
    stack.push(ModuleError(module="dex.hop3", code=500, message="not enough liquidity"))
    stack.mask_internal()
    assert stack.as_list() == [{"module": "dex.hop3", "code": 500, "message": "not enough liquidity"}]
