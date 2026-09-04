# -*- coding: utf-8 -*-
from core.pricing import lookup_price, calc_cost, is_subscription


def test_kimi_code_subscription_models():
    # Kimi Code API 官方 4 个模型 ID：订阅制，单价 0（已含在会员月费）
    for m in ("k3", "k3-256k", "kimi-for-coding", "kimi-for-coding-highspeed"):
        assert lookup_price(m) == (0, 0), m
        assert is_subscription(m), m
        assert calc_cost(100000, 20000, m) == 0.0


def test_kimi_code_not_matching_open_platform_names():
    # 开放平台按量计费模型不被订阅制规则误伤
    assert not is_subscription("kimi-k2-0905-preview")
    assert lookup_price("kimi-k2-0905-preview") == (4, 16)


def test_unknown_model():
    assert lookup_price("totally-unknown") is None
    assert not is_subscription("totally-unknown")


def test_pay_per_use_calc():
    # 1M 输入 + 100k 输出，K2 单价 4/16 → 4 + 1.6 = 5.6
    assert abs(calc_cost(1_000_000, 100_000, "kimi-k2-0905-preview") - 5.6) < 1e-9
