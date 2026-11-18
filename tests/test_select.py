from openapi2mcp.select import Selection, select_operations
from openapi2mcp.spec import parse_spec
from conftest import SPEC


def names(result):
    return [o.tool_name for o in result.selected]


def test_default_is_get_only_and_skips_deprecated():
    r = select_operations(parse_spec(SPEC).operations, Selection())
    assert all(o.method == "GET" for o in r.selected)
    assert "oldThing" not in names(r)
    assert "createItem" not in names(r) and "deleteItem" not in names(r)


def test_methods_and_deprecated_opt_in():
    ops = parse_spec(SPEC).operations
    r = select_operations(ops, Selection(methods=["get", "post", "delete"], include_deprecated=True))
    assert {"createItem", "deleteItem", "oldThing"} <= set(names(r))


def test_tag_filters_are_case_insensitive():
    ops = parse_spec(SPEC).operations
    assert set(names(select_operations(ops, Selection(include_tags=["ITEMS"])))) == {"listItems", "getItem"}
    r = select_operations(ops, Selection(exclude_tags=["items"]))
    assert "listItems" not in names(r) and "bigList" in names(r)


def test_path_globs():
    ops = parse_spec(SPEC).operations
    assert set(names(select_operations(ops, Selection(include_paths=["/items*"])))) == {"listItems", "getItem"}
    r = select_operations(ops, Selection(exclude_paths=["/items*", "/orders/*"]))
    assert set(names(r)) == {"bigList", "emptyCall"}


def test_cap_keeps_order_and_reports_dropped():
    ops = parse_spec(SPEC).operations
    r = select_operations(ops, Selection(max_tools=2))
    assert len(r.selected) == 2 and r.candidates == 5 and len(r.dropped_by_cap) == 3
    assert names(r) == ["listItems", "getItem"]


def test_no_cap():
    r = select_operations(parse_spec(SPEC).operations, Selection(max_tools=None))
    assert not r.dropped_by_cap
