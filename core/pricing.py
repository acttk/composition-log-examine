# -*- coding: utf-8 -*-
"""模型计价：按官方公开单价（元/百万 tokens，输入/输出）× 接口返回的真实 usage 计费。
单价随官方调整可能变动，可在此表维护；未收录模型返回 None（只展示 tokens，不计价）。"""
import re

# (模型名正则, 输入¥/M, 输出¥/M)  —— 来源：各厂商官网价格页 2026-09
PRICE_TABLE = [
    # ── Kimi Code API（api.kimi.com/coding/v1）：会员订阅制，按月付费不按 token 计费 ──
    # usage 仍会统计展示，费用记 ¥0（已含在月费中）
    (r"^k3-256k$", 0, 0),
    (r"^k3$", 0, 0),
    (r"^kimi-for-coding-highspeed$", 0, 0),
    (r"^kimi-for-coding$", 0, 0),
    # ── Moonshot 开放平台（api.moonshot.cn/v1）：按量付费 ──
    (r"kimi-k2|kimi-latest", 4, 16),            # Kimi K2 系列
    (r"moonshot-v1-8k", 12, 12),
    (r"moonshot-v1-32k", 24, 24),
    (r"moonshot-v1-128k", 60, 60),
    (r"deepseek-chat", 2, 8),                   # DeepSeek V3
    (r"deepseek-reasoner", 4, 16),              # DeepSeek R1
    (r"doubao-seed-1\.6", 0.8, 2),
    (r"doubao-1\.5-pro", 0.8, 2),
    (r"doubao", 0.8, 2),                        # 兜底：豆包通用档
    (r"glm-4\.5", 2, 8),                        # 智谱 GLM-4.5
    (r"glm-4-air", 0.8, 2),
    (r"glm-4", 14, 14),                         # GLM-4 旧版
    (r"qwen3|max|qwen-max", 6, 24),             # 通义 qwen-max 档
    (r"qwen-plus", 0.8, 2),
    (r"qwen-turbo|qwen3-turbo", 0.3, 0.6),
]

# 订阅制模型（Kimi Code API 等）：费用显示为「已含在会员订阅中」而非 ¥0
SUBSCRIPTION_MODELS = re.compile(r"^(k3|k3-256k|kimi-for-coding|kimi-for-coding-highspeed)$")


def is_subscription(model: str) -> bool:
    return bool(model) and bool(SUBSCRIPTION_MODELS.match(model.strip()))


def lookup_price(model: str):
    """返回 (输入¥/M, 输出¥/M)；未收录返回 None"""
    if not model:
        return None
    for pattern, pin, pout in PRICE_TABLE:
        if re.search(pattern, model, re.IGNORECASE):
            return pin, pout
    return None


def calc_cost(input_tokens: int, output_tokens: int, model: str):
    """按真实 usage 计算费用（元）；未知单价返回 None"""
    price = lookup_price(model)
    if price is None:
        return None
    pin, pout = price
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
