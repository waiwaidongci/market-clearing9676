"""校园食堂餐券日终清分配置。"""

CAMPUS = "校园券账"
CANTEEN = "食堂核销账"
REDEEM = "核销"
PARTIAL = "部分退扣"
REVERSE = "全额冲正"
MAX_CENTS = 1_000_000_000_000
DEMO_DAY = "2026-11-20"
PRIOR_DAY = "2026-11-19"

SPEC = {
    "title": "校园食堂餐券双账结算",
    "short_name": "market-clearing",
    "subtitle": "按流水核对餐券核销与退扣，复核各校区和档口组的应结金额。",
    "organization": "青禾大学后勤餐饮中心，每日与东西两个校区的自营及合作档口结算餐券。",
    "workflow": (
        "校园卡中心导出餐券账，食堂收银系统导出核销账。后勤结算员原先用表格查流水并统计档口应结金额，"
        "现在将两份账合并为带来源列的 CSV，由系统逐笔核对。食堂值班员按流水回查原单，财务复核员"
        "使用勾稽表确认涉账校区和档口组后安排划款。"
    ),
    "pains": [
        "收银离线补传会导致一侧暂缺流水，汇总金额看不出缺的是哪张餐券。",
        "隔日退扣与整笔冲正需要追到原核销流水，单看金额很难发现扣错原单。",
        "两笔差额可能相互抵消，同额挂错校区也会影响档口结算。",
    ],
    "steps": [
        "11 月 20 日 20:30，合并校园卡中心和食堂收银记录，生成「青禾大学餐券双账_20261119-20.csv」。",
        "在「双账明细」导入 20 行，保留 11 月 19 日的三笔核销双账供隔日退扣回查。",
        "在「日终清分」选择 11 月 20 日，核对当天两账共 14 行，覆盖 8 个流水号。",
        "在「清分结果」查看 3 笔匹配、2 笔单边缺失和 3 笔差异，并核对两账净额 3070 分和 3080 分。",
        "在「结算复核」按流水 C006 查补传，按 C008 核对校区归属，按 P002 核对退扣单据。",
        "改查 11 月 19 日，确认三笔原核销全部匹配，两账均为 2900 分，结算检查通过。",
        "在「结算交付」下载清分报告，将检查通过的批次交给财务。",
    ],
    "methodology": [
        "同一来源内流水号唯一，同一流水号可在两个来源各出现一次。按入账日选取流水，比较类型、原核销号、校区、档口组和金额。",
        "输入金额为大于零的整数分。净额等于核销金额减去部分退扣及全额冲正。隔日调整须在同一账内引用原核销，校区和档口组沿用原单。",
        "同一原单累计部分退扣须小于原金额。全额冲正最多一笔，金额等于扣除截至冲正日的部分退扣后所余金额，冲正日后禁止再退扣。",
        "各账按自身校区与档口组归集，交叉表分别与原始行计算的校区、档口组和全账合计勾稽。差额统一为校园券账减食堂核销账。",
        "存在入账记录、双账无缺失和差异且勾稽成立时才通过结算检查。单笔异常即使在汇总中抵消，仍会阻断结算。",
    ],
    "sample_name": "青禾大学餐券双账_20261119-20.csv",
    "default_dataset": "青禾大学 11 月 19–20 日餐券双账",
    "entity": "餐券流水",
    "run": "日终清分",
    "decision": "结算复核",
    "action": "提交复核决定",
    "modes": ["确认入账", "退回补账", "暂缓结算"],
    "pages": ["结算总览", "双账明细", "日终清分", "清分结果", "结算复核", "结算交付"],
    "fields": [
        {"key": "ledger", "label": "记账来源", "type": "select", "required": True, "options": [CAMPUS, CANTEEN]},
        {"key": "transaction_key", "label": "流水号", "type": "string", "required": True, "max_length": 100},
        {"key": "posting_date", "label": "入账日", "type": "date", "required": True},
        {"key": "zone", "label": "校区", "type": "string", "required": True, "max_length": 80},
        {"key": "merchant_group", "label": "档口组", "type": "string", "required": True, "max_length": 80},
        {"key": "kind", "label": "记账类型", "type": "select", "required": True, "options": [REDEEM, PARTIAL, REVERSE]},
        {"key": "original_key", "label": "原核销流水号", "type": "string", "required": False, "max_length": 100},
        {"key": "amount_cents", "label": "金额（分）", "type": "int", "required": True, "min": 1, "max": MAX_CENTS},
    ],
    "params": [
        {"key": "posting_date", "label": "结算日", "type": "date", "required": True, "default": DEMO_DAY,
         "help": "按入账日清分。当天有冲正或退扣时，导入明细须包含被引用的历史核销。"},
    ],
    "fixture": {"posting_date": PRIOR_DAY},
    "axes": "横轴为逐笔核对状态，纵轴为流水号个数。",
}
