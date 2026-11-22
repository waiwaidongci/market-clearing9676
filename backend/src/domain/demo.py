"""青禾大学双账明细，含隔日冲正、退扣及需追查的流水。"""

from .spec import CAMPUS, CANTEEN, DEMO_DAY, PARTIAL, PRIOR_DAY, REDEEM, REVERSE


def demo_rows():
    rows = []
    entries = (
        # 流水号、入账日、校区、档口组、类型、原核销、校园分、食堂分
        ("C001", PRIOR_DAY, "东校区", "自营档口", REDEEM, None, 1200, 1200),
        ("C002", PRIOR_DAY, "西校区", "合作档口", REDEEM, None, 900, 900),
        ("C003", PRIOR_DAY, "东校区", "合作档口", REDEEM, None, 800, 800),
        ("C004", DEMO_DAY, "东校区", "自营档口", REDEEM, None, 1550, 1550),
        ("C005", DEMO_DAY, "西校区", "合作档口", REDEEM, None, 1250, 1200),
        ("C006", DEMO_DAY, "东校区", "合作档口", REDEEM, None, 640, None),
        ("C007", DEMO_DAY, "西校区", "自营档口", REDEEM, None, None, 730),
        ("C008", DEMO_DAY, "东校区", "合作档口", REDEEM, None, 680, 680),
        ("R003", DEMO_DAY, "东校区", "合作档口", REVERSE, "C003", 800, 800),
        ("P001", DEMO_DAY, "东校区", "自营档口", PARTIAL, "C001", 150, 150),
        ("P002", DEMO_DAY, "西校区", "合作档口", PARTIAL, "C002", 100, 130),
    )
    for key, day, zone, group, kind, original, campus, canteen in entries:
        for ledger, cents in ((CAMPUS, campus), (CANTEEN, canteen)):
            if cents is not None:
                rows.append({
                    "ledger": ledger, "transaction_key": key, "posting_date": day,
                    "zone": "西校区" if key == "C008" and ledger == CANTEEN else zone,
                    "merchant_group": group, "kind": kind, "original_key": original, "amount_cents": cents,
                })
    return rows
