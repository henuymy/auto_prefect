from services.compare_service import compare_tables


def test_compare_tables_same():
    table = [
        ["name", "count"],
        ["A", 1],
        ["B", "2"],
    ]

    result = compare_tables(table, table)

    assert result.result == "same"
    assert result.new_row_count == 2
    assert result.old_row_count == 2


def test_compare_tables_changed_without_key():
    old_table = [
        ["name", "count"],
        ["A", 1],
    ]
    new_table = [
        ["name", "count"],
        ["A", 2],
        ["B", 1],
    ]

    result = compare_tables(new_table, old_table)

    assert result.result == "changed"
    assert result.diff_summary["added_rows"] == 1
    assert result.diff_summary["changed_rows"] == 2


def test_compare_tables_invalid_header_mismatch():
    old_table = [
        ["name", "count"],
        ["A", 1],
    ]
    new_table = [
        ["name", "total"],
        ["A", 1],
    ]

    result = compare_tables(new_table, old_table)

    assert result.result == "invalid"
    assert result.message == "新报表和模板旧数据字段不一致"
