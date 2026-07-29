"""算账引擎指标测试."""
import pytest
from src.engine.metrics import compute_metrics, compute_period_summary, ROIMetrics, _safe_div


def make_order(sku="测试商品", gmv=1000.0, refund=100.0,
               platform_fee=50.0, commission=100.0,
               shipping=10.0, insurance=2.0):
    return dict(sku_name=sku, gmv=gmv, refund_amount=refund,
                platform_fee=platform_fee, commission=commission,
                shipping_fee=shipping, insurance=insurance)


def make_cost(sku="测试商品", cost_per_unit=300.0, gift_pct=0.02,
              warehouse=3.0, labor_pct=0.05, tax_rate=0.13):
    return dict(sku_name=sku, cost_per_unit=cost_per_unit,
                gift_cost_pct=gift_pct, warehouse_cost_per_order=warehouse,
                labor_pct=labor_pct, tax_rate=tax_rate)


class TestSafeDiv:
    def test_normal_division(self):
        assert _safe_div(10.0, 2.0) == 5.0

    def test_zero_denominator_returns_none(self):
        assert _safe_div(10.0, 0.0) is None

    def test_near_zero_denominator_returns_none(self):
        assert _safe_div(10.0, 1e-11) is None


class TestComputeMetrics:
    def test_normal_case(self):
        """正常盈亏场景：收入 > 成本，应有正净利润."""
        orders = [make_order()]
        ad_spends = [{"spend": 200.0}]
        cost_configs = {"测试商品": make_cost()}

        result = compute_metrics(
            orders=orders,
            ad_spends=ad_spends,
            cost_configs=cost_configs,
            period_type="day",
            period_value="2026-07-01",
        )

        # 验证核心计算链
        assert result.gmv == 1000.0
        assert result.refund_amount == 100.0
        assert result.net_gmv == 900.0
        assert result.platform_fee == 50.0
        assert result.commission == 100.0
        assert result.net_revenue == 750.0
        assert result.product_cost == 300.0
        assert result.ad_spend == 200.0
        assert result.shipping_cost == 12.0
        assert result.gift_cost == 20.0   # 1000 * 0.02
        assert result.warehouse_cost == 3.0
        assert result.labor_cost == 37.5  # 750 * 0.05

        # 税前毛利 = 750 - 300 - 200 - 12 - 20 - 3 - 37.5 = 177.5
        assert result.pre_tax_profit == pytest.approx(177.5)
        assert result.tax == pytest.approx(23.075)   # 177.5 * 0.13
        assert result.net_profit == pytest.approx(154.425)

    def test_empty_orders(self):
        """空订单列表应返回全零指标."""
        result = compute_metrics(orders=[], ad_spends=[], cost_configs={})
        assert result.gmv == 0.0
        assert result.net_revenue == 0.0
        assert result.net_profit == 0.0
        assert result.marketing_roi is None
        assert result.real_roi is None
        assert result.net_margin is None
        assert result.refund_rate is None
        assert result.sku_breakdown == []

    def test_zero_ad_spend_roi_is_none(self):
        """投放费用为 0 时 marketing_roi 应为 None."""
        orders = [make_order()]
        ad_spends = [{"spend": 0.0}]
        cost_configs = {"测试商品": make_cost()}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.ad_spend == 0.0
        assert result.marketing_roi is None

    def test_default_cost_fallback(self):
        """SKU 无成本配置时应使用 default_cost 并触发警告."""
        orders = [make_order(sku="未知SKU")]
        ad_spends = [{"spend": 100.0}]
        cost_configs = {}  # 无任何配置
        default = make_cost(cost_per_unit=200.0, gift_pct=0.01)

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, default_cost=default,
        )
        assert "未知SKU" in result.warnings[0]
        assert result.product_cost == 200.0
        assert result.gift_cost == 10.0  # 1000 * 0.01

    def test_pre_tax_loss_zero_tax(self):
        """税前亏损时税费应为 0."""
        orders = [make_order(gmv=100.0, refund=90.0,
                             platform_fee=5.0, commission=10.0)]
        # net_revenue = 100 - 90 - 5 - 10 = -5
        ad_spends = [{"spend": 100.0}]
        cost_configs = {"测试商品": make_cost(cost_per_unit=500.0)}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.pre_tax_profit < 0
        assert result.tax == 0.0

    def test_ratio_fields_returned(self):
        """比率字段应正确计算."""
        orders = [make_order()]
        ad_spends = [{"spend": 200.0}]
        cost_configs = {"测试商品": make_cost()}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.refund_rate == 0.1  # 100 / 1000
        assert result.ad_spend_ratio == pytest.approx(200.0 / 750.0)
        assert result.net_margin is not None
        assert result.real_roi is not None

    def test_sku_breakdown_included(self):
        """应包含单品拆解明细，含按 GMV 占比分摊的 ad_spend."""
        orders = [make_order(sku="SKU-A"), make_order(sku="SKU-B", gmv=500.0)]
        ad_spends = [{"spend": 100.0}]
        cost_configs = {
            "SKU-A": make_cost(sku="SKU-A"),
            "SKU-B": make_cost(sku="SKU-B", cost_per_unit=150.0),
        }

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert len(result.sku_breakdown) == 2
        sku_names = [s["sku_name"] for s in result.sku_breakdown]
        assert "SKU-A" in sku_names
        assert "SKU-B" in sku_names

        # 总 GMV=1500, ad_spend=100
        # SKU-A gmv=1000, ad_spend 分摊 = 100 * 1000/1500 ≈ 66.67
        # SKU-B gmv=500,  ad_spend 分摊 = 100 * 500/1500 ≈ 33.33
        sku_a = next(s for s in result.sku_breakdown if s["sku_name"] == "SKU-A")
        sku_b = next(s for s in result.sku_breakdown if s["sku_name"] == "SKU-B")
        assert "ad_spend" in sku_a
        assert "ad_spend" in sku_b
        assert sku_a["ad_spend"] == pytest.approx(66.67, abs=0.01)
        assert sku_b["ad_spend"] == pytest.approx(33.33, abs=0.01)

        # SKU net_profit 应扣除分摊的 ad_spend
        # SKU-A: net_revenue=750, costs=300+12+20+3+37.5=372.5, pre_tax=377.5
        #        tax=377.5*0.13=49.075, profit_before_ad=328.425
        #        profit_with_ad = 328.425 - 66.67 ≈ 261.76
        assert sku_a["net_profit"] == pytest.approx(261.76, abs=0.01)

        # SKU-B: net_revenue=250, costs=150+12+10+3+12.5=187.5, pre_tax=62.5
        #        tax=62.5*0.13=8.125, profit_before_ad=54.375
        #        profit_with_ad = 54.375 - 33.33 ≈ 21.04
        assert sku_b["net_profit"] == pytest.approx(21.04, abs=0.01)

        # 验证净利率存在且合理
        assert sku_a["net_margin"] is not None
        assert sku_b["net_margin"] is not None

    def test_to_dict_rounds_floats(self):
        """to_dict 应将浮点数四舍五入到 2 位小数."""
        result = compute_metrics(orders=[], ad_spends=[], cost_configs={})
        d = result.to_dict()
        assert d["gmv"] == 0.0
        assert d["marketing_roi"] is None
        assert isinstance(d["period_type"], str)

    def test_multiple_sku_weighted_average(self):
        """多 SKU 时 labor_pct 和 tax_rate 使用简单平均."""
        orders = [
            make_order(sku="A", gmv=1000.0),
            make_order(sku="B", gmv=500.0),
        ]
        ad_spends = [{"spend": 200.0}]
        cost_configs = {
            "A": make_cost(sku="A", labor_pct=0.05, tax_rate=0.13),
            "B": make_cost(sku="B", labor_pct=0.10, tax_rate=0.06),
        }

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        # avg_labor_pct = (0.05 + 0.10) / 2 = 0.075
        assert result.labor_cost == pytest.approx(result.net_revenue * 0.075)
        # avg_tax_rate = (0.13 + 0.06) / 2 = 0.095
        expected_tax = max(0.0, result.pre_tax_profit * 0.095)
        assert result.tax == pytest.approx(expected_tax)

    def test_sku_breakdown_zero_gmv_even_split(self):
        """GMV 为零时 ad_spend 应均摊到各 SKU."""
        orders = [
            dict(sku_name="A", gmv=0.0, refund_amount=0.0,
                 platform_fee=0.0, commission=0.0,
                 shipping_fee=0.0, insurance=0.0),
            dict(sku_name="B", gmv=0.0, refund_amount=0.0,
                 platform_fee=0.0, commission=0.0,
                 shipping_fee=0.0, insurance=0.0),
        ]
        ad_spends = [{"spend": 100.0}]
        cost_configs = {
            "A": make_cost(sku="A"),
            "B": make_cost(sku="B"),
        }

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.gmv == 0.0
        assert len(result.sku_breakdown) == 2
        # 100 / 2 = 50 each
        for item in result.sku_breakdown:
            assert item["ad_spend"] == 50.0

    def test_sku_net_margin_none_when_zero_denom(self):
        """SKU net_revenue 为 0 时 net_margin 应为 None."""
        orders = [
            dict(sku_name="商品A", gmv=0.0, refund_amount=0.0,
                 platform_fee=0.0, commission=0.0,
                 shipping_fee=0.0, insurance=0.0),
        ]
        ad_spends = []
        cost_configs = {"商品A": make_cost(sku="商品A")}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert len(result.sku_breakdown) == 1
        assert result.sku_breakdown[0]["net_margin"] is None
        assert result.sku_breakdown[0]["net_revenue"] == 0.0


class TestComputePeriodSummary:
    def test_daily_summary(self):
        orders = [
            {"sku_name": "商品A", "gmv": 1000.0, "refund_amount": 0.0,
             "platform_fee": 50.0, "commission": 100.0,
             "shipping_fee": 10.0, "insurance": 2.0,
             "settle_date": "2026-07-01"},
            {"sku_name": "商品A", "gmv": 500.0, "refund_amount": 0.0,
             "platform_fee": 25.0, "commission": 50.0,
             "shipping_fee": 5.0, "insurance": 1.0,
             "settle_date": "2026-07-02"},
        ]
        ad_spends = [
            {"spend": 100.0, "date": "2026-07-01"},
            {"spend": 50.0, "date": "2026-07-02"},
        ]
        cost_configs = {"商品A": make_cost(sku="商品A")}

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="day",
        )
        assert len(results) == 2
        assert results[0].period_value == "2026-07-01"
        assert results[1].period_value == "2026-07-02"
        assert results[0].gmv == 1000.0
        assert results[1].gmv == 500.0

    def test_daily_summary_forwards_default_cost(self):
        """compute_period_summary 应将 default_cost 转发到 compute_metrics."""
        orders = [
            {"sku_name": "未知SKU", "gmv": 500.0, "refund_amount": 0.0,
             "platform_fee": 0.0, "commission": 0.0,
             "shipping_fee": 0.0, "insurance": 0.0,
             "settle_date": "2026-07-01"},
        ]
        ad_spends = [{"spend": 50.0, "date": "2026-07-01"}]
        cost_configs = {}  # 无匹配，全靠 default_cost
        default = make_cost(cost_per_unit=100.0, gift_pct=0.0)

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="day",
            default_cost=default,
        )
        assert len(results) == 1
        assert "未知SKU" in results[0].warnings[0]
        assert results[0].product_cost == 100.0

    def test_weekly_summary(self):
        orders = [
            {"sku_name": "商品A", "gmv": 1000.0, "refund_amount": 0.0,
             "platform_fee": 0.0, "commission": 0.0,
             "shipping_fee": 0.0, "insurance": 0.0,
             "settle_date": "2026-07-06"},  # Monday of W28 2026
        ]
        ad_spends = []
        cost_configs = {"商品A": make_cost(sku="商品A")}

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="week",
        )
        assert len(results) == 1
        # 2026-07-06 is a Monday, ISO week 28 of 2026
        assert results[0].period_value == "2026-W28"

    def test_monthly_summary(self):
        orders = [
            {"sku_name": "商品A", "gmv": 1000.0, "refund_amount": 0.0,
             "platform_fee": 0.0, "commission": 0.0,
             "shipping_fee": 0.0, "insurance": 0.0,
             "settle_date": "2026-07-15"},
        ]
        ad_spends = []
        cost_configs = {"商品A": make_cost(sku="商品A")}

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="month",
        )
        assert len(results) == 1
        assert results[0].period_value == "2026-07"
