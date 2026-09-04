# -*- coding: utf-8 -*-
"""多文件合并解析回归测试"""
from core.report import build_report
from main import _merge_records, _source_label


def test_merge_records_multi_file(tmp_path):
    a = tmp_path / "info.log"
    a.write_text(
        "2026-09-03 14:00:00.000 [t1] INFO  c.y.A - 批改流水线启动, resultId:111\n"
        "2026-09-03 15:00:00.000 [t1] INFO  c.y.A - 批改流水线启动, resultId:222\n",
        encoding="utf-8")
    b = tmp_path / "error.log"
    b.write_text(
        "2026-09-03 14:30:00.000 [t2] ERROR c.y.B - 风控异常 NonRetryableException: x, resultId:111\n",
        encoding="utf-8")
    recs = _merge_records([str(a), str(b)], ["info.log", "error.log"])
    # 合并后按时间全局排序：14:00(info) → 14:30(error) → 15:00(info)
    assert [r["source"] for r in recs] == ["info.log", "error.log", "info.log"]
    assert [r["ts"] for r in recs] == ["2026-09-03 14:00:00.000",
                                        "2026-09-03 14:30:00.000",
                                        "2026-09-03 15:00:00.000"]
    # 每条记录带来源标注
    assert all("source" in r for r in recs)


def test_merge_records_single_file_unchanged(tmp_path):
    a = tmp_path / "only.log"
    a.write_text("2026-09-03 14:00:00.000 [t1] INFO  c.y.A - hello\n", encoding="utf-8")
    recs = _merge_records([str(a)], ["only.log"])
    assert len(recs) == 1
    assert recs[0]["source"] == "only.log"


def test_source_label():
    assert _source_label(["a.log"]) == "a.log"
    assert _source_label(["info.log", "error.log"]) == "info.log + error.log（2 个文件）"


def test_report_line_ref_with_source():
    stats = {"风控异常": [{"ts": "t", "resultId": "1", "line_no": 9, "source": "error.log",
                          "raw": "x"}],
             "阶段失败": {}, "批阅失败落库": [], "网关分支不足": [], "疑似误用ERROR级别": 0,
             "_threadpool": {"buckets": [], "day_peak_active": 0,
                             "day_peak_queue_tasks": 0, "pressure_signals": []},
             "_processed_users": {"buckets": [], "day_total": 0}}
    md = build_report(meta={"date": "d", "mode": "m", "template_name": "-",
                            "cost": "c", "source": "info.log + error.log（2 个文件）"},
                      stats_result=stats, compress_result=None, model_section=None, routine={})
    # 附录行号标注来源文件
    assert "error.log:L9" in md
    # 详情负载携带 source（HTML 转义后的 JSON）
    assert "&quot;source&quot;: &quot;error.log&quot;" in md
