# -*- coding: utf-8 -*-
"""轨道A：确定性统计（失败事件 + 线程池分时段汇总 + 阶段性能）"""
import re
from datetime import datetime, timedelta
from collections import defaultdict

RE_RESULT_ID = re.compile(r'result_?[Ii]d\s*[:：]\s*(\d+)')  # 兼容 resultId/result_id + 中英文冒号 + 空格
RE_STAGE_OK = re.compile(r'阶段执行成功, resultId:\d+, stage:(\w+), costMs:(\d+)')
RE_TP = re.compile(r'活跃线程数=(\d+), 核心线程数=(\d+), 最大线程数=(\d+), 当前线程数=(\d+), 历史最大线程数=(\d+), 队列任务数=(\d+), 队列剩余容量=(\d+)')
RE_STAGE_FAIL = re.compile(r'阶段(\w+)失败')
# 通用失败记录："阶段执行失败, resultId:X, stage:Y, costMs:Z"（stage 取 stage:(\w+)，避免误提取"执行"）
RE_STAGE_FAIL_DETAIL = re.compile(r'阶段执行失败, resultId:\d+, stage:(\w+), costMs:\d+')
# 批阅失败落库记录中的失败阶段："失败阶段:POLISH"（兼容中英文冒号 + 空格）
RE_FAIL_STAGE = re.compile(r'失败阶段\s*[:：]\s*(\w+)')
# ERROR 但语义为成功 → 误用级别
FALSE_ERROR = re.compile(r'成功|SUCCESS|合规[:：]\s*true|完成')

# ── 服务器聚类（线程池监控按机器分组）──
_TAIL_WORDS = {"info", "error", "warn", "warning", "debug", "fatal", "trace", "log"}

def _server_of(source) -> str:
    """从来源文件名提取服务器标识：
    server1-info.log / server1.info.log / server1_error.log → "server1"
    带日期后缀也兼容：server1-info-2026-09-04.log → "server1"
    识别不出（如 info.log / error.log）→ ""（此类文件全部合并为同一组，兜底不误拆）"""
    if not source:
        return ""
    stem = re.sub(r'\.(log|txt)$', '', str(source).strip(), flags=re.I)
    # 先剥离尾部日期模式：-2026-09-04 / .2026.9.4 / -20260904
    stem = re.sub(r'[-_.]\d{4}[-_.]\d{1,2}[-_.]\d{1,2}$', '', stem)
    stem = re.sub(r'[-_.]\d{8}$', '', stem)
    parts = [p for p in re.split(r'[-_.]', stem) if p]
    while parts and parts[-1].lower() in _TAIL_WORDS:
        parts.pop()
    return "-".join(parts) if parts else ""

def extract_result_id(msg):
    m = RE_RESULT_ID.search(msg)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{16,})\b', msg)
    return m.group(1) if m else None

def collect_failures(records):
    out = {"风控异常": [], "阶段失败": defaultdict(list), "批阅失败落库": [],
           "网关分支不足": [], "疑似误用ERROR级别": 0}
    seen_stage_fail = set()
    for r in records:
        msg, is_err = r["msg"], r["level"] == "ERROR"
        # 事件判定只看 msg 首行：堆栈续行中的异常类名不算独立事件（避免风控异常误计）
        head = msg.split("\n", 1)[0]
        if "NonRetryableException" in head or head.startswith("风控异常"):
            out["风控异常"].append({"ts": r["ts"], "resultId": extract_result_id(msg),
                                    "line_no": r["line_no"], "raw": msg, "source": r.get("source")})
        if "阶段执行失败" in head or "失败，流水线终止" in head:
            rid = extract_result_id(head)
            # 通用记录取 stage:(\w+)；流水线终止记录取 阶段X失败
            if "阶段执行失败" in head:
                dm = RE_STAGE_FAIL_DETAIL.search(head)
                stage = dm.group(1) if dm else "UNKNOWN"
            else:
                sm = RE_STAGE_FAIL.search(head)
                stage = sm.group(1) if sm else "UNKNOWN"
            # 同一事件的"阶段执行失败"与"阶段X失败，流水线终止"两行只计一次（保留先出现者的 ts/line_no）
            key = (stage, rid) if rid is not None else (stage, r["line_no"])
            if key not in seen_stage_fail:
                seen_stage_fail.add(key)
                out["阶段失败"][stage].append({"ts": r["ts"], "resultId": rid,
                                               "line_no": r["line_no"], "raw": msg,
                                               "source": r.get("source")})
        if "作文批阅失败，已落库" in head:
            sm = RE_FAIL_STAGE.search(head)
            out["批阅失败落库"].append({"ts": r["ts"], "resultId": extract_result_id(msg),
                                        "line_no": r["line_no"], "raw": msg,
                                        "stage": sm.group(1) if sm else "UNKNOWN",
                                        "source": r.get("source")})
        if "并行网关成功分支数不足" in head:
            out["网关分支不足"].append({"ts": r["ts"], "resultId": extract_result_id(msg),
                                       "line_no": r["line_no"], "raw": msg,
                                       "source": r.get("source")})
        if is_err and FALSE_ERROR.search(msg):
            out["疑似误用ERROR级别"] += 1
    out["阶段失败"] = dict(out["阶段失败"])
    return out

def collect_threadpool(records, bucket_hours=2):
    pts = []
    for r in records:
        m = RE_TP.search(r["msg"])
        if m:
            pts.append({"t": datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S.%f"),
                        "active": int(m.group(1)),       # 活跃线程数 = 并发执行的任务数
                        "max_pool": int(m.group(3)),     # 配置最大线程数
                        "queue_tasks": int(m.group(6)),  # 队列任务数 = 排队等待的任务数
                        "server": _server_of(r.get("source"))})  # 服务器标识（按文件名聚类）
    if not pts:
        return {"buckets": [], "servers": [], "server_peaks": {},
                "day_peak_active": 0, "day_peak_queue_tasks": 0, "pressure_signals": []}
    # 真分桶：桶宽 bucket_hours、以各采样所在日的 00:00 为网格原点、互不重叠
    # 分桶键 = (桶起点, 服务器)：同一时段 × 每台服务器各一个桶
    buckets = defaultdict(list)
    for p in pts:
        hour = p["t"].replace(minute=0, second=0, microsecond=0)
        start = hour.replace(hour=(hour.hour // bucket_hours) * bucket_hours)
        buckets[(start, p["server"])].append(p)
    out, signals = [], []
    for (key, server), group in sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        max_pool = group[0]["max_pool"]
        b = {"start": key.strftime("%H:%M"),
             "end": (key + timedelta(hours=bucket_hours)).strftime("%H:%M"),
             "server": server,
             "avg_active": round(sum(g["active"] for g in group) / len(group), 1),
             "peak_active": max(g["active"] for g in group),
             "min_active": min(g["active"] for g in group),
             "peak_queue_tasks": max(g["queue_tasks"] for g in group),
             "samples": len(group)}
        out.append(b)
        if b["peak_active"] >= max_pool * 0.8:
            signals.append({"start": b["start"], "end": b["end"], "server": server,
                            "reason": f"并发任务峰值 {b['peak_active']} 达线程上限 {max_pool} 的 80%"})
        if b["peak_queue_tasks"] > 0:
            signals.append({"start": b["start"], "end": b["end"], "server": server,
                            "reason": f"队列出现排队，峰值 {b['peak_queue_tasks']} 个任务等待"})
    # 各服务器全天峰值（单机语义：每个采样值本就来自一台机器）
    servers = sorted({p["server"] for p in pts})
    server_peaks = {}
    for s in servers:
        sp = [p for p in pts if p["server"] == s]
        server_peaks[s] = {"peak_active": max(p["active"] for p in sp),
                           "peak_queue_tasks": max(p["queue_tasks"] for p in sp)}
    return {"buckets": out,
            "servers": servers,
            "server_peaks": server_peaks,
            "day_peak_active": max(p["active"] for p in pts),
            "day_peak_queue_tasks": max(p["queue_tasks"] for p in pts),
            "pressure_signals": signals}

def collect_processed_users(records, bucket_hours=2):
    """按“批改流水线启动”日志的 resultId 去重，统计每时段处理人数与全天去重总数。
    一条启动日志代表一个批改任务（一个人），同一 resultId 若重复打印只计一次。"""
    buckets = defaultdict(set)   # key: 桶起点 datetime -> 去重 resultId 集合
    day_ids = set()
    for r in records:
        head = r["msg"].split("\n", 1)[0]
        if "批改流水线启动" not in head:
            continue
        rid = extract_result_id(head)
        if rid is None:
            continue
        t = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S.%f")
        hour = t.replace(minute=0, second=0, microsecond=0)
        start = hour.replace(hour=(hour.hour // bucket_hours) * bucket_hours)
        buckets[start].add(rid)
        day_ids.add(rid)
    out = []
    for key in sorted(buckets):
        out.append({"start": key.strftime("%H:%M"),
                    "end": (key + timedelta(hours=bucket_hours)).strftime("%H:%M"),
                    "count": len(buckets[key])})
    return {"buckets": out, "day_total": len(day_ids)}

def stage_perf(records):
    perf = defaultdict(lambda: {"count": 0, "total_ms": 0, "max_ms": 0})
    for r in records:
        m = RE_STAGE_OK.search(r["msg"])
        if m:
            d = perf[m.group(1)]
            d["count"] += 1
            ms = int(m.group(2))
            d["total_ms"] += ms
            d["max_ms"] = max(d["max_ms"], ms)
    return {k: {"count": v["count"], "avg_ms": v["total_ms"] // v["count"], "max_ms": v["max_ms"]}
            for k, v in perf.items()}
