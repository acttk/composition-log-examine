from core.compressor import compress

def rec(ts, level, logger, msg, line_no=1):
    return {"ts": ts, "thread": "t", "level": level, "logger": logger, "msg": msg, "line_no": line_no}

def test_routine_counted_not_in_body():
    recs = [
        rec("2026-09-03 13:59:14.826", "INFO", "a.B", "豆包模型会话开始（流式）"),
        rec("2026-09-03 13:59:15.826", "INFO", "a.B", "豆包模型会话结束（流式）, requestId:0217884"),
        rec("2026-09-03 13:59:16.826", "INFO", "a.C", "线程池监控:线程池监控[活跃线程数=1, 核心线程数=80, 最大线程数=100, 当前线程数=72, 历史最大线程数=80, 队列任务数=0, 队列剩余容量=1000]"),
    ]
    r = compress(recs)
    assert r["routine"]["模型会话"] == 2
    assert r["routine"]["线程池监控"] == 1
    assert "豆包模型会话" not in r["body"]
    assert "线程池监控" not in r["body"]

def test_template_merge():
    recs = [rec(f"2026-09-03 13:59:{s:02d}.000", "ERROR", "a.D",
                f"Lua——ofOther参数:homeworkKey=result:grading:progress:123,done", line_no=i)
            for i, s in enumerate(range(10))]
    r = compress(recs)
    assert "Lua——ofOther参数" in r["body"]
    assert "× 10" in r["body"] or "×10" in r["body"]
    assert r["template_count"] == 1

def test_long_payload_truncated():
    recs = [rec("2026-09-03 13:59:17.290", "INFO", "a.E", "载荷:" + "x" * 500)]
    r = compress(recs)
    assert len([l for l in r["body"].splitlines() if l.startswith("2026-")][0]) < 280
    assert "[载荷截断]" in r["body"]

def test_first_line_only_matching():
    # 控制器补充说明：例行/失败判定只看 msg 首行（堆栈续行中的关键词不算独立事件）
    recs = [
        # 堆栈续行含 NonRetryableException，首行无失败关键词 → 不进 failure_details
        rec("2026-09-03 14:00:00.000", "ERROR", "a.F",
            "处理请求异常\ncom.xxx.NonRetryableException: boom"),
        # 多行载荷续行含例行关键词，首行无关键词 → 不计数，记录进 body
        rec("2026-09-03 14:00:01.000", "INFO", "a.G",
            "收到请求载荷\n{\"echo\": \"豆包模型会话开始\"}"),
        # 首行含失败关键词 → 进 failure_details
        rec("2026-09-03 14:00:02.000", "ERROR", "a.H",
            "阶段执行失败, resultId:123, stage:grading, costMs:5\nstack..."),
    ]
    r = compress(recs)
    assert r["routine"]["模型会话"] == 0
    assert "NonRetryableException" not in r["failure_details"]
    assert "阶段执行失败" in r["failure_details"]
    assert "收到请求载荷" in r["body"]
