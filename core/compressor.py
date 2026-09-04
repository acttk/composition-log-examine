# -*- coding: utf-8 -*-
"""轨道B压缩：例行日志计数化 + 大载荷截断 + 模板归并"""
import re

ROUTINE_PATTERNS = [("模型会话", ("豆包模型会话", "千问模型会话")),
                    ("路由选择", ("调用方显式指定模型",)),
                    ("阶段成功", ("阶段执行成功",))]
RE_TP_LINE = "线程池监控"
FAILURE_KEYS = ("NonRetryableException", "阶段执行失败", "失败，流水线终止",
                "作文批阅失败", "并行网关成功分支数不足", "风控异常")


def _mask(msg: str) -> str:
    t = re.sub(r'\d{4,}', '#', msg)
    t = re.sub(r'\b\d+\b', '#', t)
    return t[:80]


def _first_line(msg: str) -> str:
    # 控制器补充：例行/失败判定只看首行，多行堆栈/载荷续行中的关键词不算独立事件
    return msg.split("\n", 1)[0]


def _is_routine(r):
    head = _first_line(r["msg"])
    for name, keys in ROUTINE_PATTERNS:
        if any(k in head for k in keys):
            return name
    if RE_TP_LINE in head:
        return "线程池监控"
    return None


def _is_failure(r):
    head = _first_line(r["msg"])
    return any(k in head for k in FAILURE_KEYS)


def compress(records):
    routine = {"模型会话": 0, "阶段成功": 0, "路由选择": 0, "线程池监控": 0}
    groups = {}       # key -> {"count", "line"}
    failures = []
    for r in records:
        kind = _is_routine(r)
        if kind:
            routine[kind] += 1
            continue
        if _is_failure(r):
            failures.append(f"L{r['line_no']} [{r['level']}] {r['msg'][:200]}")
        msg = r["msg"] if len(r["msg"]) <= 200 else r["msg"][:200] + " ...[载荷截断]"
        key = (r["level"], r["logger"], _mask(msg))
        if key not in groups:
            groups[key] = {"count": 0,
                           "line": f"{r['ts']} [{r['thread']}] {r['level']} {r['logger']} - {msg}"}
        groups[key]["count"] += 1
    body_lines = [f"{g['line']} × {g['count']}" if g["count"] > 1 else g["line"]
                  for g in groups.values()]
    body = "\n".join(body_lines)
    return {"body": body, "routine": routine,
            "template_count": len(groups), "char_count": len(body),
            "est_tokens": int(len(body) * 0.9),
            "failure_details": "\n".join(failures[:50])}
