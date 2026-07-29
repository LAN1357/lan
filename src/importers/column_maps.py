"""CSV 列名映射 — 覆盖抖音/千川常见导出格式."""

# 抖音订单导出列名映射（多套，覆盖不同版本）
ORDER_COLUMN_MAPS = [
    {
        # 抖音电商罗盘标准导出
        "order_id": ["订单号", "订单ID", "order_id", "订单编号"],
        "sku_name": ["商品名称", "商品", "sku_name", "SKU名称", "宝贝名称"],
        "gmv": ["下单金额", "订单金额", "GMV", "gmv", "支付金额", "成交金额"],
        "refund_amount": ["退款金额", "退款", "refund_amount", "退货退款金额"],
        "refund_status": ["退款状态", "售后状态", "refund_status"],
        "platform_fee": ["平台服务费", "技术服务费", "扣点", "platform_fee"],
        "commission": ["达人佣金", "佣金", "commission", "带货佣金", "推广费"],
        "shipping_fee": ["运费", "快递费", "shipping_fee", "发货费用"],
        "insurance": ["运费险", "退货运费险", "insurance"],
        "settle_date": ["确认收货日期", "成交日期", "settle_date", "结算日期", "支付时间"],
        "live_session_id": ["直播场次ID", "直播间ID", "live_session_id", "场次ID"],
    },
    {
        # 抖音商家后台订单管理导出
        "order_id": ["订单编号", "子订单编号"],
        "sku_name": ["商品标题", "货品名称", "SKU"],
        "gmv": ["实付金额", "买家实付", "支付金额(元)"],
        "refund_amount": ["退款金额(元)", "已退款金额", "售后金额"],
        "refund_status": ["订单状态", "售后状态"],
        "platform_fee": ["平台扣点(元)", "服务费"],
        "commission": ["佣金(元)", "作者佣金", "达人推广费"],
        "shipping_fee": ["运费(元)", "快递成本"],
        "insurance": ["运费险(元)"],
        "settle_date": ["支付时间", "下单时间", "创建时间"],
        "live_session_id": ["关联场次", "直播ID"],
    },
]

# 千川投放报表列名映射
AD_SPEND_COLUMN_MAPS = [
    {
        "campaign_name": ["计划名称", "广告计划", "campaign_name", "推广计划"],
        "spend": ["花费", "消耗", "spend", "花费(元)", "消耗(元)"],
        "impressions": ["展示数", "曝光量", "impressions", "展示量"],
        "clicks": ["点击数", "点击量", "clicks"],
        "conversions": ["转化数", "转化量", "conversions", "成交订单数"],
        "date": ["日期", "date", "投放日期", "统计日期"],
        "live_session_id": ["直播场次ID", "关联直播", "live_session_id"],
    },
    {
        "campaign_name": ["广告组名称", "计划"],
        "spend": ["总花费", "现金消耗", "千川花费"],
        "impressions": ["展现量", "曝光次数"],
        "clicks": ["点击次数"],
        "conversions": ["成交笔数", "支付订单数", "下单数"],
        "date": ["时间", "统计时间"],
        "live_session_id": ["直播间", "场次"],
    },
]


def detect_columns(
    headers: list[str], column_maps: list[dict]
) -> tuple[dict, list[str]]:
    """自动检测 CSV 列名映射.

    遍历多套列名映射字典，对每套尝试匹配所有 headers。
    返回匹配最完整的一套映射结果 + 未匹配目标字段列表。

    Args:
        headers: CSV 第一行的列名列表
        column_maps: 多套列名映射 [{"target_field": ["别名1", "别名2"]}, ...]

    Returns:
        (mapping, unmatched)
        mapping: {"target_field": "matched_header", ...}
        unmatched: ["target_field_1", "target_field_2", ...]
    """
    headers_lower = [h.strip().lower() for h in headers]
    headers_orig = [h.strip() for h in headers]

    best_mapping: dict = {}
    best_unmatched: list[str] = []
    best_score = -1

    for cmap in column_maps:
        mapping: dict = {}
        unmatched: list[str] = []
        for target_field, aliases in cmap.items():
            found = False
            for alias in aliases:
                alias_lower = alias.lower()
                for i, hl in enumerate(headers_lower):
                    if hl == alias_lower:
                        mapping[target_field] = headers_orig[i]
                        found = True
                        break
                if found:
                    break
            if not found:
                unmatched.append(target_field)

        score = len(mapping)
        if score > best_score:
            best_score = score
            best_mapping = mapping
            best_unmatched = unmatched

    return best_mapping, best_unmatched


# 必填字段
ORDER_REQUIRED_FIELDS = ["gmv", "refund_amount", "commission"]
AD_SPEND_REQUIRED_FIELDS = ["spend", "date"]
