# -*- coding: utf-8 -*-
"""Markdown 报告生成（设计文档 §10）"""
import html as _html
import json


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _bucket_hours(buckets: list) -> int:
    """由首桶起止时间推算桶粒度（小时）；无桶时默认 2"""
    if not buckets:
        return 2
    start = _minutes(buckets[0]["start"])
    end = _minutes(buckets[0]["end"])
    if end <= start:  # 跨零点桶（如 22:00-00:00）
        end += 24 * 60
    return max(1, round((end - start) / 60))


def _sample(items: list) -> str:
    """失败表样例列：首条 resultId@行号；无则 -"""
    if not items:
        return "-"
    it = items[0]
    rid, ln = it.get("resultId"), it.get("line_no")
    where = f"@{it['source']}:{ln}" if it.get("source") else f"@{ln}"
    return f"{rid}{where}" if rid is not None else f"L{ln}"


def _line_ref(it: dict) -> str:
    """附录明细行号：多文件时标注来源（file:L123），单文件保持 L123"""
    ln = it["line_no"]
    src = it.get("source")
    return f"{src}:L{ln}" if src else f"L{ln}"


def _group_stage(items: list) -> dict:
    """批阅失败落库按失败阶段分组（无阶段 → UNKNOWN）"""
    g = {}
    for it in items:
        g.setdefault(it.get("stage") or "UNKNOWN", []).append(it)
    return g


def _detail_payload(it: dict, name: str) -> str:
    """附录明细行的原始报错负载：HTML 转义 + JSON，嵌入 <!--data-detail--> 注释。
    Markdown 渲染器忽略注释，前端提取后弹出详情。"""
    payload = {"type": name, "ts": it.get("ts"), "resultId": it.get("resultId"),
               "line_no": it.get("line_no"), "stage": it.get("stage"),
               "source": it.get("source"), "raw": it.get("raw") or ""}
    return "<!--data-detail:" + _html.escape(json.dumps(payload, ensure_ascii=False)) + "-->"


def _tp_table(L: list, buckets: list, peak_active=0, peak_queue=0, peak_label="全天峰值") -> None:
    """渲染一张线程池分时段负载表；peak 参数提供时追加峰值行（多服务器时标签为本机峰值）"""
    L.append("| 时段 | 并发任务均值 | 并发任务峰值 | 并发任务谷值 | 排队等待峰值 | 采样数 |")
    L.append("|---|---|---|---|---|---|")
    for b in buckets:
        L.append(f"| {b['start']}-{b['end']} | {b['avg_active']} | {b['peak_active']} | "
                 f"{b['min_active']} | {b['peak_queue_tasks']} | {b['samples']} |")
    if peak_active or peak_queue:
        L.append(f"| **{peak_label}** | — | **{peak_active}** | — | **{peak_queue}** | — |")
    L.append("")


def build_report(meta: dict, stats_result: dict, compress_result: dict | None,
                 model_section: str | None, routine: dict, usage=None) -> str:
    L = []
    L.append(f"# {meta['date']} 日志分析报告")
    L.append(f"> 模式：{meta['mode']} | 模版：{meta['template_name']} | 消耗：{meta['cost']}")
    if meta.get("source"):
        L.append(f"> 来源：{meta['source']}")
    if compress_result:
        L.append(f"> 压缩：{compress_result.get('char_count', 0):,} 字符 / "
                 f"{compress_result.get('template_count', 0)} 模板组 / "
                 f"≈{compress_result.get('est_tokens', 0):,} tokens")
    L.append("")

    # ── 一、核心指标 ──
    L.append("## 一、核心指标（代码统计，精确）")
    L.append("### 失败统计")
    L.append("| 类型 | 次数 | 样例(resultId@行号) |")
    L.append("|---|---|---|")
    fk = stats_result.get("风控异常", [])
    L.append(f"| 风控异常 | {len(fk)} | {_sample(fk)} |")
    for stage, items in stats_result.get("阶段失败", {}).items():
        L.append(f"| 阶段失败-{stage} | {len(items)} | {_sample(items)} |")
    # 批阅失败落库按失败阶段细分（如 EVIDENCE_EXTRACTION / POLISH / REVIEW_AND_POLISH）
    pg = stats_result.get("批阅失败落库", [])
    if pg:
        for stage, items in _group_stage(pg).items():
            L.append(f"| 批阅失败落库-{stage} | {len(items)} | {_sample(items)} |")
    else:
        L.append("| 批阅失败落库 | 0 | - |")
    for key in ("网关分支不足",):
        items = stats_result.get(key, [])
        L.append(f"| {key} | {len(items)} | {_sample(items)} |")
    L.append(f"| 疑似误用ERROR级别 | {stats_result.get('疑似误用ERROR级别', 0)} | - |")
    L.append("")

    tp = stats_result.get("_threadpool") or {}
    buckets = tp.get("buckets", [])
    servers = tp.get("servers") or []
    L.append(f"### 线程池分时段负载（桶粒度 {_bucket_hours(buckets)} 小时）")
    if len(servers) > 1:
        # 多服务器：按机器分表，各自独立峰值
        for s in servers:
            sb = [b for b in buckets if b.get("server") == s]
            sp = tp.get("server_peaks", {}).get(s, {})
            L.append(f"#### 服务器 {s}")
            _tp_table(L, sb, sp.get("peak_active", 0), sp.get("peak_queue_tasks", 0),
                      peak_label="本机峰值")
        L.append(f"**全局峰值**（各服务器最高）：并发任务 **{tp.get('day_peak_active', 0)}** · "
                 f"排队等待 **{tp.get('day_peak_queue_tasks', 0)}**")
        L.append("")
    else:
        # 单服务器（或未识别）：保持原有单表格式
        _tp_table(L, buckets, tp.get("day_peak_active", 0), tp.get("day_peak_queue_tasks", 0))
    multi = len(servers) > 1
    for sig in tp.get("pressure_signals", []):
        prefix = f"[{sig.get('server')}] " if multi and sig.get("server") else ""
        L.append(f"⚠️ 压力信号 {prefix}{sig['start']}-{sig['end']}：{sig['reason']}")
    if tp.get("pressure_signals"):
        L.append("")

    pu = stats_result.get("_processed_users") or {}
    pu_buckets = pu.get("buckets", [])
    L.append(f"### 处理人数统计（按 resultId 去重，桶粒度 {_bucket_hours(pu_buckets)} 小时）")
    if pu_buckets:
        L.append("| 时段 | 处理人数 |")
        L.append("|---|---|")
        for b in pu_buckets:
            L.append(f"| {b['start']}-{b['end']} | {b['count']} |")
    L.append(f"| **全天合计** | **{pu.get('day_total', 0)}** |")
    L.append("")

    L.append("### 例行日志计数")
    L.append("| 类别 | 次数 |")
    L.append("|---|---|")
    for k, v in routine.items():
        L.append(f"| {k} | {v} |")
    L.append("")

    # ── 二、模型分析（model_section 为 None 时整节省略） ──
    if model_section is not None:
        L.append("## 二、模型分析（按模版）")
        if usage is not None:
            L.append(f"> 模型 token 消耗：{usage}")
            L.append("")
        L.append(str(model_section))
        L.append("")

    # ── 三、附录 ──
    L.append("## 三、附录：失败事件明细（带行号）")
    blocks = []
    if fk:
        blocks.append(("风控异常", fk))
    for stage, items in stats_result.get("阶段失败", {}).items():
        blocks.append((f"阶段失败-{stage}", items))
    # 批阅失败落库按失败阶段分组展示
    for stage, items in _group_stage(stats_result.get("批阅失败落库", [])).items():
        blocks.append((f"批阅失败落库-{stage}", items))
    for key in ("网关分支不足",):
        items = stats_result.get(key, [])
        if items:
            blocks.append((key, items))
    if not blocks:
        L.append("（无失败事件）")
    for name, items in blocks:
        L.append("")
        L.append(f"#### {name}（{len(items)} 次）")
        for it in items:
            rid = it.get("resultId")
            L.append(f"- {_line_ref(it)} {it['ts']}" + (f" {rid}" if rid is not None else "")
                     + " " + _detail_payload(it, name))
    return "\n".join(L) + "\n"
