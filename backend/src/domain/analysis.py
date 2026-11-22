"""Match the two ledgers by event key and reconcile each ledger's signed cents."""

from collections import Counter, defaultdict
from datetime import date
import re

from .spec import CAMPUS, CANTEEN, MAX_CENTS, PARTIAL, REDEEM, REVERSE, SPEC


_MATCH = "匹配"
_MISSING_CAMPUS = "校园券账缺失"
_MISSING_CANTEEN = "食堂核销账缺失"
_DIFFERENCE = "差异"
_STATUSES = (_MATCH, _MISSING_CAMPUS, _MISSING_CANTEEN, _DIFFERENCE)
_COMPARE = (
    ("kind", "记账类型"), ("original_key", "原核销流水号"), ("zone", "校区"),
    ("merchant_group", "档口组"), ("amount_cents", "金额"),
)
_SUMMARY_COLUMNS = (
    ("campus_count", "校园券账行数"), ("canteen_count", "食堂核销账行数"),
    ("campus_cents", "校园券账净额（分）"), ("canteen_cents", "食堂核销账净额（分）"),
    ("delta_cents", "双账差额（分）"),
)
_ZONE = ("zone", "校区")
_GROUP = ("merchant_group", "档口组")


def _day(value):
    try:
        day = date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("日期须为 YYYY-MM-DD") from None
    if day != value:
        raise ValueError("日期须为 YYYY-MM-DD")
    return day


def _text(field, value):
    if isinstance(value, str):
        value = value.strip()
    if value in (None, "") and not field["required"]:
        return None
    if not isinstance(value, str) or not value or len(value) > field.get("max_length", 300):
        raise ValueError(f'{field["label"]}缺失或格式不正确')
    if field["type"] == "select" and value not in field["options"]:
        raise ValueError(f'{field["label"]}取值不受支持')
    return value


def _cents(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,13}", value.strip()):
        value = int(value)
    if type(value) is not int or not 1 <= value <= MAX_CENTS:
        raise ValueError(f"金额须为 1 至 {MAX_CENTS} 的整数分")
    return value


def _row(raw):
    if not isinstance(raw, dict):
        raise ValueError("流水须为字段对象")
    row = {}
    for field in SPEC["fields"]:
        key = field["key"]
        value = raw.get(key)
        if key == "posting_date":
            row[key] = _day(value)
        elif key == "amount_cents":
            row[key] = _cents(value)
        else:
            row[key] = _text(field, value)
    return row


def _validate_adjustments(ledger_rows):
    adjustments = defaultdict(list)
    for key, row in ledger_rows.items():
        label = f'{row["ledger"]}流水 {key}'
        original_key = row["original_key"]
        if row["kind"] == REDEEM:
            if original_key is not None:
                raise ValueError(f"{label}为核销，原核销流水号须留空")
            continue
        original = ledger_rows.get(original_key)
        if original is None or original["kind"] != REDEEM:
            raise ValueError(f"{label}须引用同账中的原核销流水")
        if row["posting_date"] < original["posting_date"]:
            raise ValueError(f"{label}早于原核销入账日")
        if (row["zone"], row["merchant_group"]) != (original["zone"], original["merchant_group"]):
            raise ValueError(f"{label}的校区和档口组须与原核销一致")
        adjustments[original_key].append(row)

    for key, entries in adjustments.items():
        original = ledger_rows[key]
        label = f'{original["ledger"]}原核销 {key}'
        partials = [row for row in entries if row["kind"] == PARTIAL]
        reversals = [row for row in entries if row["kind"] == REVERSE]
        remaining = original["amount_cents"] - sum(row["amount_cents"] for row in partials)
        if remaining <= 0:
            raise ValueError(f"{label}累计部分退扣须小于原核销金额")
        if len(reversals) > 1:
            raise ValueError(f"{label}最多允许一笔全额冲正")
        if reversals:
            reversal = reversals[0]
            if any(row["posting_date"] > reversal["posting_date"] for row in partials):
                raise ValueError(f"{label}冲正日后仍有部分退扣")
            if reversal["amount_cents"] != remaining:
                raise ValueError(f"{label}冲正金额须等于部分退扣后的剩余金额 {remaining} 分")


def validate_rows(rows):
    """Normalize ledger cells and validate each adjustment against its source ledger."""
    ledgers = {CAMPUS: {}, CANTEEN: {}}
    validated = []
    for index, raw in enumerate(rows, 1):
        try:
            row = _row(raw)
            key = row["transaction_key"]
            ledger_rows = ledgers[row["ledger"]]
            if key in ledger_rows:
                raise ValueError(f'{row["ledger"]}流水号 {key} 重复')
            ledger_rows[key] = row
            validated.append(row)
        except ValueError as error:
            raise ValueError(f"明细第 {index} 行 · {error}") from None
    if not validated:
        raise ValueError("至少需要一行餐券流水")
    for ledger_rows in ledgers.values():
        _validate_adjustments(ledger_rows)
    return validated


def _signed(row):
    return row["amount_cents"] if row["kind"] == REDEEM else -row["amount_cents"]


def _drilldown(campus, canteen, posting_days):
    entries = []
    for key in sorted(campus.keys() | canteen.keys()):
        left, right = campus.get(key), canteen.get(key)
        if left is None or right is None:
            ledger = CAMPUS if left is None else CANTEEN
            status = _MISSING_CAMPUS if left is None else _MISSING_CANTEEN
            other_day = posting_days.get((ledger, key))
            reason = f"{ledger}入账日为 {other_day}" if other_day else f"请查{ledger}是否漏传"
        else:
            differences = [label for field, label in _COMPARE if left[field] != right[field]]
            status = _DIFFERENCE if differences else _MATCH
            reason = "、".join(differences)
        entry = {"transaction_key": key, "status": status, "reason": reason}
        for prefix, row in (("campus", left), ("canteen", right)):
            for field, _ in _COMPARE[:-1]:
                entry[f"{prefix}_{field}"] = row[field] if row else None
            entry[f"{prefix}_cents"] = _signed(row) if row else None
        entry["delta_cents"] = (entry["campus_cents"] or 0) - (entry["canteen_cents"] or 0)
        entries.append(entry)
    return entries


def _summaries(rows, keys):
    groups = defaultdict(Counter)
    for row in rows:
        total = groups[tuple(row[key] for key in keys)]
        prefix = "campus" if row["ledger"] == CAMPUS else "canteen"
        total[f"{prefix}_count"] += 1
        total[f"{prefix}_cents"] += _signed(row)
    return [dict(zip(keys, values), **{key: totals[key] for key, _ in _SUMMARY_COLUMNS[:-1]},
                 delta_cents=totals["campus_cents"] - totals["canteen_cents"])
            for values, totals in sorted(groups.items())]


def _margin_matches(cells, margins, dimension):
    keys = [key for key, _ in _SUMMARY_COLUMNS]
    sums = defaultdict(Counter)
    for cell in cells:
        for key in keys:
            sums[cell[dimension]][key] += cell[key]
    expected = {row[dimension]: {key: row[key] for key in keys} for row in margins}
    return sums == expected


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def analyze(rows, normalized_params):
    """Return the standard batch report for the selected posting date."""
    if not isinstance(normalized_params, dict) or normalized_params.keys() - {"posting_date"}:
        raise ValueError("日终清分仅支持结算日参数")
    day = _day(normalized_params.get("posting_date", SPEC["params"][0]["default"]))
    validated = validate_rows(rows)
    posting_days = {(row["ledger"], row["transaction_key"]): row["posting_date"] for row in validated}
    selected = [row for row in validated if row["posting_date"] == day]
    campus = {row["transaction_key"]: row for row in selected if row["ledger"] == CAMPUS}
    canteen = {row["transaction_key"]: row for row in selected if row["ledger"] == CANTEEN}
    drilldown = _drilldown(campus, canteen, posting_days)
    counts = Counter(row["status"] for row in drilldown)
    missing = counts[_MISSING_CAMPUS] + counts[_MISSING_CANTEEN]
    differences = counts[_DIFFERENCE]

    cells = _summaries(selected, ("zone", "merchant_group"))
    zones = _summaries(selected, ("zone",))
    groups = _summaries(selected, ("merchant_group",))
    campus_cents = sum(map(_signed, campus.values()))
    canteen_cents = sum(map(_signed, canteen.values()))
    totals = {"campus_count": len(campus), "canteen_count": len(canteen), "campus_cents": campus_cents,
              "canteen_cents": canteen_cents, "delta_cents": campus_cents - canteen_cents}
    crossfoot = (all(sum(cell[key] for cell in cells) == value for key, value in totals.items())
                 and _margin_matches(cells, zones, "zone") and _margin_matches(cells, groups, "merchant_group"))
    checks = (
        ("当日入账", bool(selected), f"当日共 {len(selected)} 行。"),
        ("双账齐全", missing == 0, f"{missing} 笔流水只出现在一侧。"),
        ("逐笔一致", differences == 0, f"{differences} 笔双边流水存在差异。"),
        ("交叉勾稽", crossfoot, "核对校区小计、档口组小计及全账行数与净额。"),
    )
    ready = all(passed for _, passed, _ in checks)
    gate = "通过" if ready else "阻断"
    check_rows = [{"check": label, "status": "通过" if passed else "阻断", "detail": detail}
                  for label, passed, detail in checks]
    check_rows.append({"check": "结算放行", "status": gate, "detail": "所有检查通过后可确认入账。"})
    guidance = "核对小计后可将清分报告交给财务入账。"
    if not ready:
        guidance = "请按明细中的流水号追查缺失、差异或跨日补传，订正后沿用本结算日重跑。"
    if not selected:
        guidance = "当前结算日没有入账流水，请核对日期及双账导出范围后重跑。"
    entry_columns = (
        ("transaction_key", "流水号"), ("status", "核对状态"), ("reason", "追查字段或原因"),
        *((f"{prefix}_{key}", f"{name} · {label}")
          for prefix, name in (("campus", CAMPUS), ("canteen", CANTEEN)) for key, label in _COMPARE[:-1]),
        *_SUMMARY_COLUMNS[2:],
    )
    return {
        "state": "ok" if ready else "warning",
        "headline": f"{day} 结算检查{gate}，{counts[_MATCH]} 笔匹配、{missing} 笔单边缺失、{differences} 笔差异。",
        "note": "按入账日匹配，冲正与退扣按负分记账。差额为校园券账减食堂核销账。",
        "metrics": [
            {"label": "校园券账净额（分）", "value": campus_cents, "detail": f"{len(campus)} 行"},
            {"label": "食堂核销账净额（分）", "value": canteen_cents, "detail": f"{len(canteen)} 行"},
            {"label": "双账差额（分）", "value": totals["delta_cents"], "detail": "逐笔异常影响结算放行"},
        ],
        "tables": [
            _table("结算检查", (("check", "检查项"), ("status", "结果"), ("detail", "说明")), check_rows),
            _table("流水核对明细", entry_columns, drilldown),
            _table("校区 × 档口组勾稽", (_ZONE, _GROUP) + _SUMMARY_COLUMNS, cells),
            _table("校区小计", (_ZONE,) + _SUMMARY_COLUMNS, zones),
            _table("档口组小计", (_GROUP,) + _SUMMARY_COLUMNS, groups),
            _table("双账合计", _SUMMARY_COLUMNS, [totals]),
        ],
        "charts": [{"title": "流水核对状态", "kind": "bar",
                    "series": [{"label": status, "value": counts[status]} for status in _STATUSES]}],
        "sections": [{"title": "财务复核", "text": guidance}],
    }
