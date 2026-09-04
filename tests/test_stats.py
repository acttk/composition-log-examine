from core.stats import collect_failures, collect_threadpool, collect_processed_users, extract_result_id, _server_of

def rec(ts, msg, level="ERROR", logger="c.y.X", line_no=1, thread="t1", source=None):
    r = {"ts": ts, "thread": thread, "level": level, "logger": logger, "msg": msg, "line_no": line_no}
    if source is not None:
        r["source"] = source
    return r

def test_extract_result_id():
    assert extract_result_id("resultId：2092912713964584960，润色") == "2092912713964584960"
    assert extract_result_id("resultId: 123, x") == "123"
    assert extract_result_id("result_id: 456, x") == "456"  # 下划线形式
    assert extract_result_id("result_id：789，x") == "789"     # 下划线 + 中文冒号
    assert extract_result_id("没有id") is None

def test_failures_riskcontrol_and_stage():
    recs = [
        rec("2026-09-03 14:00:00.000", "风控异常 com.yunhen...NonRetryableException: xxx", line_no=10),
        rec("2026-09-03 14:05:00.000", "阶段EVIDENCE_EXTRACTION失败，流水线终止, resultId:2092912736634798080, 原因:xxx", line_no=20),
        rec("2026-09-03 14:05:30.000", "阶段执行失败, resultId:2092912736634798080, stage:EVIDENCE_EXTRACTION, costMs:1234", line_no=25),
        rec("2026-09-03 14:06:00.000", "作文批阅成功，已落库markType=1, resultId:2092912752376020992, 失败阶段:{\"POLISH\":\"SUCCESS\"}", line_no=30),
    ]
    f = collect_failures(recs)
    assert len(f["风控异常"]) == 1 and f["风控异常"][0]["line_no"] == 10
    assert "EVIDENCE_EXTRACTION" in f["阶段失败"]
    # 同一事件的两行（流水线终止 + 阶段执行失败）只计一次，且 stage 不误提取为"执行"
    assert len(f["阶段失败"]["EVIDENCE_EXTRACTION"]) == 1
    assert "执行" not in f["阶段失败"]
    # "批阅成功"不进失败，但计入误用级别
    assert len(f.get("批阅失败落库", [])) == 0
    assert f["疑似误用ERROR级别"] == 1

def test_failures_mark_fail():
    recs = [rec("2026-09-03 14:00:00.000", "作文批阅失败，已落库markType=-1, resultId:2092912751797207040, 失败阶段:EVIDENCE_EXTRACTION")]
    f = collect_failures(recs)
    assert len(f["批阅失败落库"]) == 1
    assert f["批阅失败落库"][0]["stage"] == "EVIDENCE_EXTRACTION"

def test_failures_mark_fail_stage_grouped():
    # 不同失败阶段各成一组；中文冒号 / 无阶段记录兼容
    recs = [
        rec("2026-09-03 14:00:00.000", "作文批阅失败，已落库markType=-1, resultId:111, 失败阶段:POLISH", line_no=1),
        rec("2026-09-03 14:01:00.000", "作文批阅失败，已落库markType=-1, resultId:222, 失败阶段:POLISH", line_no=2),
        rec("2026-09-03 14:02:00.000", "作文批阅失败，已落库markType=-1, resultId:333, 失败阶段：REVIEW_AND_POLISH", line_no=3),
        rec("2026-09-03 14:03:00.000", "作文批阅失败，已落库markType=-1, resultId:444", line_no=4),  # 无失败阶段
    ]
    f = collect_failures(recs)
    by_stage = {}
    for it in f["批阅失败落库"]:
        by_stage.setdefault(it["stage"], []).append(it)
    assert set(by_stage) == {"POLISH", "REVIEW_AND_POLISH", "UNKNOWN"}
    assert len(by_stage["POLISH"]) == 2
    assert by_stage["REVIEW_AND_POLISH"][0]["resultId"] == "333"

def test_threadpool_buckets_and_signal():
    def tp(ts, active, queue_tasks=0):
        return rec(ts, f"线程池监控:线程池监控[活跃线程数={active}, 核心线程数=80, 最大线程数=100, 当前线程数=72, 历史最大线程数=80, 队列任务数={queue_tasks}, 队列剩余容量=1000]", level="INFO")
    recs = [tp("2026-09-03 13:30:00.000", 10),
            tp("2026-09-03 14:10:00.000", 30, queue_tasks=5),
            tp("2026-09-03 14:20:00.000", 85, queue_tasks=12),
            tp("2026-09-03 16:10:00.000", 5)]
    r = collect_threadpool(recs, bucket_hours=2)
    starts = [b["start"] for b in r["buckets"]]
    # 真分桶：以当日 00:00 为网格原点的 bucket_hours 宽不重叠桶（13:30→12:00-14:00，16:10→16:00-18:00）
    assert starts == ["12:00", "14:00", "16:00"]
    b14 = [b for b in r["buckets"] if b["start"] == "14:00"][0]
    assert b14["samples"] == 2
    assert b14["peak_active"] == 85             # 并发任务峰值
    assert b14["peak_queue_tasks"] == 12        # 排队等待峰值
    assert r["day_peak_active"] == 85
    assert r["day_peak_queue_tasks"] == 12
    # 并发 85 >= 100*0.8 → 线程紧张信号；排队 12 > 0 → 排队信号
    assert any("14:00" in s["start"] and "并发" in s["reason"] for s in r["pressure_signals"])
    assert any("14:00" in s["start"] and "排队" in s["reason"] for s in r["pressure_signals"])

def test_server_of_naming():
    # 常规约定：机器名-info.log / 机器名-error.log
    assert _server_of("server1-info.log") == "server1"
    assert _server_of("server1-error.log") == "server1"     # 与 info 归为同一台
    assert _server_of("server2.info.log") == "server2"      # 点分隔
    assert _server_of("server3_error.log") == "server3"      # 下划线
    assert _server_of("server1-info-2026-09-04.log") == "server1"  # 带日期后缀
    assert _server_of("server1.info.20260904.log") == "server1"    # 紧凑日期
    # 识别不出机器标识 → 空串（兜底合并为一组，不误拆）
    assert _server_of("info.log") == ""
    assert _server_of("error.log") == ""
    assert _server_of("info-2026-09-04.log") == ""
    assert _server_of("") == ""
    assert _server_of(None) == ""
    # 纯机器名单文件（无 info/error 字样）
    assert _server_of("machine-a.log") == "machine-a"

def test_threadpool_multi_server():
    def tp(ts, active, queue_tasks=0, source=None):
        return rec(ts, f"线程池监控:线程池监控[活跃线程数={active}, 核心线程数=80, 最大线程数=100, 当前线程数=72, 历史最大线程数=80, 队列任务数={queue_tasks}, 队列剩余容量=1000]",
                  level="INFO", source=source)
    # 两台服务器 + 一台无法识别（info.log 兜底组）
    recs = [tp("2026-09-03 14:10:00.000", 30, source="server1-info.log"),
            tp("2026-09-03 14:20:00.000", 85, source="server1-error.log"),  # 与 server1-info 同组
            tp("2026-09-03 14:15:00.000", 40, source="server2-info.log"),
            tp("2026-09-03 14:25:00.000", 5, source="info.log")]
    r = collect_threadpool(recs, bucket_hours=2)
    # server1 的 info+error 聚为一组；共 3 组
    assert r["servers"] == ["", "server1", "server2"]
    b14 = {b["server"]: b for b in r["buckets"]}
    assert b14["server1"]["samples"] == 2            # 两个文件的采样合入同一桶
    assert b14["server1"]["peak_active"] == 85
    assert b14["server2"]["peak_active"] == 40
    assert b14[""]["peak_active"] == 5               # 兜底组
    # 各服务器峰值独立；全局峰值 = 最忙那台
    assert r["server_peaks"]["server1"]["peak_active"] == 85
    assert r["server_peaks"]["server2"]["peak_active"] == 40
    assert r["day_peak_active"] == 85
    # 压力信号带服务器标识
    assert any(s.get("server") == "server1" and "并发" in s["reason"] for s in r["pressure_signals"])

def test_processed_users():
    def start(ts, rid):
        return rec(ts, f"批改流水线启动, resultId:{rid}, pipeline:composition-grading", level="INFO")
    recs = [start("2026-09-03 09:10:00.000", "1001"),
            start("2026-09-03 09:20:00.000", "1002"),
            start("2026-09-03 09:30:00.000", "1001"),  # 同 resultId 重复 → 去重
            start("2026-09-03 11:10:00.000", "1003"),
            rec("2026-09-03 09:15:00.000", "阶段执行成功, resultId:9999, stage:X, costMs:10", level="INFO")]
    r = collect_processed_users(recs, bucket_hours=2)
    assert r["day_total"] == 3                      # 1001/1002/1003
    b9 = [b for b in r["buckets"] if b["start"] == "08:00"][0]  # 09:10～09:30 → 08:00-10:00
    assert b9["count"] == 2
    assert [b["start"] for b in r["buckets"]] == ["08:00", "10:00"]  # 11:10 → 10:00-12:00
