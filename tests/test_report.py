from core.report import build_report

def test_build_report_stats_only():
    stats = {"风控异常": [{"ts": "2026-09-03 14:00:00.000", "resultId": "123", "line_no": 10,
                          "raw": "风控异常 com.yunhen...NonRetryableException: xxx"}],
             "阶段失败": {"EVIDENCE_EXTRACTION": [{"ts": "t", "resultId": "456", "line_no": 20,
                                                   "raw": "阶段执行失败, resultId:456, stage:EVIDENCE_EXTRACTION"}]},
             "批阅失败落库": [{"ts": "t", "resultId": "789", "line_no": 30, "stage": "POLISH",
                             "raw": "作文批阅失败，已落库markType=-1, resultId:789, 失败阶段:POLISH"},
                            {"ts": "t2", "resultId": "999", "line_no": 40, "stage": "POLISH",
                             "raw": "作文批阅失败，已落库markType=-1, resultId:999, 失败阶段:POLISH"}],
             "网关分支不足": [], "疑似误用ERROR级别": 5,
             "_threadpool": {"buckets": [{"start": "14:00", "end": "16:00", "avg_active": 30.0,
                              "peak_active": 72, "min_active": 10, "peak_queue_tasks": 3,
                              "samples": 100}], "day_peak_active": 72, "day_peak_queue_tasks": 3,
                "pressure_signals": []},
             "_processed_users": {"buckets": [{"start": "14:00", "end": "16:00", "count": 42}],
                                   "day_total": 564}}
    md = build_report(meta={"date": "2026-09-03", "mode": "仅统计", "template_name": "-",
                            "cost": "¥0.00", "source": "a.log"},
                      stats_result=stats, compress_result=None, model_section=None,
                      routine={"模型会话": 1708, "阶段成功": 943, "路由选择": 833, "线程池监控": 322}, usage=None)
    assert "# 2026-09-03 日志分析报告" in md
    assert "风控异常" in md and "1" in md
    # 批阅失败落库按失败阶段分组（摘要表 + 附录小节）
    assert "批阅失败落库-POLISH" in md
    assert "批阅失败落库-POLISH（2 次）" in md
    assert "14:00" in md and "72" in md
    assert "处理人数" in md and "564" in md and "42" in md
    assert "模型会话" in md
    assert "模型分析" not in md  # 仅统计模式不含该节
    # 附录明细行携带原始报错负载（供前端「详情」按钮弹出）
    assert "data-detail:" in md and "NonRetryableException" in md

def test_build_report_full_mode():
    stats = {"风控异常": [], "阶段失败": {}, "批阅失败落库": [], "网关分支不足": [],
             "疑似误用ERROR级别": 0,
             "_threadpool": {"buckets": [], "day_peak_active": 0, "day_peak_queue_tasks": 0, "pressure_signals": []}}
    md = build_report(meta={"date": "2026-09-03", "mode": "统计+模型分析", "template_name": "日常巡检",
                            "cost": "¥0.32", "source": "a.log"},
                      stats_result=stats, compress_result={"routine": {}}, model_section="【结论】一切正常",
                      routine={"模型会话": 1, "阶段成功": 1, "路由选择": 1, "线程池监控": 1}, usage=None)
    assert "## 二、模型分析" in md and "一切正常" in md

def test_build_report_multi_server_threadpool():
    stats = {"风控异常": [], "阶段失败": {}, "批阅失败落库": [], "网关分支不足": [],
             "疑似误用ERROR级别": 0,
             "_threadpool": {
                 "buckets": [
                     {"start": "14:00", "end": "16:00", "server": "server1", "avg_active": 30.0,
                      "peak_active": 85, "min_active": 10, "peak_queue_tasks": 12, "samples": 100},
                     {"start": "14:00", "end": "16:00", "server": "server2", "avg_active": 20.0,
                      "peak_active": 40, "min_active": 5, "peak_queue_tasks": 0, "samples": 90}],
                 "servers": ["server1", "server2"],
                 "server_peaks": {"server1": {"peak_active": 85, "peak_queue_tasks": 12},
                                  "server2": {"peak_active": 40, "peak_queue_tasks": 0}},
                 "day_peak_active": 85, "day_peak_queue_tasks": 12,
                 "pressure_signals": [{"start": "14:00", "end": "16:00", "server": "server1",
                                       "reason": "并发任务峰值 85 达线程上限 100 的 80%"}]},
             "_processed_users": {"buckets": [], "day_total": 0}}
    md = build_report(meta={"date": "2026-09-03", "mode": "仅统计", "template_name": "-",
                            "cost": "¥0.00", "source": "s1.log + s2.log"},
                      stats_result=stats, compress_result=None, model_section=None,
                      routine={}, usage=None)
    # 多服务器：按机器分表 + 各自峰值 + 全局峰值 + 信号带服务器前缀
    assert "#### 服务器 server1" in md and "#### 服务器 server2" in md
    assert "全局峰值" in md
    assert "[server1] " in md
    # 单表模式的「全天峰值」行不应出现在多服务器模式下（各表用自己的服务器峰值）
    assert md.count("全天峰值") == 0

def test_build_report_single_server_keeps_format():
    """单服务器（source 未识别 / 单文件）保持原有单表格式"""
    stats = {"风控异常": [], "阶段失败": {}, "批阅失败落库": [], "网关分支不足": [],
             "疑似误用ERROR级别": 0,
             "_threadpool": {"buckets": [{"start": "14:00", "end": "16:00", "server": "",
                                          "avg_active": 30.0, "peak_active": 72, "min_active": 10,
                                          "peak_queue_tasks": 3, "samples": 100}],
                             "servers": [""], "server_peaks": {},
                             "day_peak_active": 72, "day_peak_queue_tasks": 3,
                             "pressure_signals": []},
             "_processed_users": {"buckets": [], "day_total": 0}}
    md = build_report(meta={"date": "2026-09-03", "mode": "仅统计", "template_name": "-",
                            "cost": "¥0.00", "source": "a.log"},
                      stats_result=stats, compress_result=None, model_section=None,
                      routine={}, usage=None)
    assert "#### 服务器" not in md      # 不分表
    assert "全天峰值" in md              # 保留原全天峰值行
    assert "全局峰值" not in md
