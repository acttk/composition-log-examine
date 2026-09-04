# -*- coding: utf-8 -*-
"""块大小自动推荐（recommend_max_chars）回归测试"""
from main import recommend_max_chars


def test_empty_body_returns_cap():
    assert recommend_max_chars(0) == 300000


def test_tiny_body_floored():
    # 极小正文（~1800 字符）→ 单块，但显示值设 50K 下限
    assert recommend_max_chars(1800) == 50000


def test_small_body_single_chunk():
    # 10 万字符 → 单块 100K（不必保留 300K）
    assert recommend_max_chars(100000) == 100000


def test_balanced_chunks_no_tiny_remainder():
    # 62 万字符：按 300K 会切成 300+300+20（零头块）；均衡推荐 3 块 × 207K
    assert recommend_max_chars(620000) == 207000


def test_just_over_cap():
    # 31 万字符：ceil(310000/300000)=2 块 → 155K × 2
    assert recommend_max_chars(310000) == 155000


def test_multiple_of_cap():
    # 90 万字符 → 3 块整 300K
    assert recommend_max_chars(900000) == 300000


def test_real_case_six_files():
    # 昨日 6 文件报告实测：2,458,729 字符 → 9 块（2,458,729/9≈273,192 → 向上取整 274K）
    assert recommend_max_chars(2458729) == 274000


def test_never_exceeds_cap():
    for n in (1, 999, 300001, 12345678):
        assert recommend_max_chars(n) <= 300000
