# 日志智能分析工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地 Web 工具：上传 Java logback 文本日志 → 双轨分析（代码精确统计 + 可选模型 Map-Reduce 分析）→ Markdown 报告落盘。

**Architecture:** FastAPI 后端五个核心模块（parser→stats/compressor→chunker→analyzer）+ 单页原生 JS 前端。轨道A纯代码统计，轨道B经压缩去噪后切块调 OpenAI 兼容 API。

**Tech Stack:** Python 3.10、FastAPI、uvicorn、httpx、pytest、原生 HTML/JS（无构建）。

**Spec:** `docs/superpowers/specs/2026-09-03-log-analysis-design.md`

## Global Constraints

- 仅监听 `127.0.0.1`；API Key 只存本地 `config.json`，任何接口回显脱敏（只回 `key_set: true/false` 与尾 4 位）。
- 解析必须流式（生成器），内存占用与文件总大小无关。
- 编码容错：`open(..., encoding="utf-8", errors="replace")`。
- 切块绝不在记录中间切断；token 估算 `字符数 × 0.9`。
- 不做：JDBC 统计、历史数据库、压缩包上传、RAG。
- 工作目录：`c:\Users\11364\Desktop\log-analysis`（样例日志 `composition-ai.log` 已在其中，测试直接用它）。
- 注释与 UI 文案用中文。

---

### Task 1: 项目骨架 + parser 多行合并

**Files:**
- Create: `requirements.txt`, `core/__init__.py`, `core/parser.py`, `tests/__init__.py`, `tests/test_parser.py`
- Test fixture: `tests/sample_small.log`（从 composition-ai.log 截取 13:59:14~13:59:20 的约 100 行，需含一个多行 JSON 块）

**Interfaces:**
- Produces: `parser.parse_file(path: str) -> Iterator[dict]`，每条记录 `{"ts": str, "thread": str, "level": str, "logger": str, "msg": str, "line_no": int}`（`ts` 格式 `2026-09-03 13:59:14.826`；`msg` 含全部续行；`line_no` 为记录首行行号，从 1 起）。解析失败的行归属上一条记录的 msg。

- [ ] **Step 1: 建目录与依赖文件**

`requirements.txt`:
```
fastapi>=0.110
uvicorn>=0.29
httpx>=0.27
python-multipart>=0.0.9
pytest>=8.0
```
创建空 `core/__init__.py`、`tests/__init__.py`。

- [ ] **Step 2: 制作测试小样本**

用 Read 工具读 `composition-ai.log` 前 100 行（含第 10~82 行的多行 JSON 块），Write 到 `tests/sample_small.log`。样本必须包含：至少 1 个多行 JSON 记录、INFO/ERROR 两种级别、至少 10 条记录。

- [ ] **Step 3: 写失败测试**

`tests/test_parser.py`:
```python
from core.parser import parse_file

def test_parse_multiline_and_fields():
    recs = list(parse_file("tests/sample_small.log"))
    assert len(recs) >= 10
    r0 = recs[0]
    assert r0["ts"] == "2026-09-03 13:59:14.826"
    assert r0["thread"] == "biz-pool-20"
    assert r0["level"] == "INFO"
    assert r0["logger"] == "c.y.c.m.s.i.VolcengineModelServiceImpl"
    assert "豆包模型会话结束" in r0["msg"]
    assert r0["line_no"] == 1
    # 多行 JSON 归属：找到 AppreciationServiceImpl 那条，msg 应含续行 JSON
    appr = [r for r in recs if "AppreciationServiceImpl" in r["logger"]][0]
    assert "\n" in appr["msg"] and "excellentSentences" in appr["msg"]

def test_parser_is_generator():
    g = parse_file("tests/sample_small.log")
    assert iter(g) is g  # 生成器而非列表，验证流式
```

- [ ] **Step 4: 运行确认失败**

Run: `python -m pytest tests/test_parser.py -v` → Expected: FAIL (ModuleNotFoundError / 函数不存在)

- [ ] **Step 5: 实现 `core/parser.py`**

```python
# -*- coding: utf-8 -*-
"""流式日志解析：逐行读取 + 多行记录合并"""
import re
from typing import Iterator

# 标准日志头：时间戳 [线程] 级别 Logger - 消息
HEAD = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \[([^\]]+)\] '
    r'(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\s+(\S+) - (.*)$'
)

def parse_file(path: str) -> Iterator[dict]:
    """流式产出记录 dict；不以日志头开头的行归属上一条记录（多行载荷/堆栈）"""
    current = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            m = HEAD.match(line)
            if m:
                if current is not None:
                    yield current
                current = {"ts": m.group(1), "thread": m.group(2),
                           "level": m.group(3), "logger": m.group(4),
                           "msg": m.group(5), "line_no": line_no}
            elif current is not None:
                current["msg"] += "\n" + line
    if current is not None:
        yield current
```

- [ ] **Step 6: 运行测试通过**

Run: `python -m pytest tests/test_parser.py -v` → Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git init -b main; git add requirements.txt core tests
git commit -m "feat: 流式日志解析与多行合并"
```

---

### Task 2: stats 轨道A统计

**Files:**
- Create: `core/stats.py`, `tests/test_stats.py`

**Interfaces:**
- Consumes: Task 1 的记录 dict（ts/thread/level/logger/msg/line_no）。
- Produces:
  - `collect_failures(records: list[dict]) -> dict`，结构：`{"风控异常": [{"ts","resultId","line_no"}], "阶段失败": {stage: [{"ts","resultId","line_no"}]}, "批阅失败落库": [...], "网关分支不足": [...], "疑似误用ERROR级别": int}`
  - `collect_threadpool(records: list[dict], bucket_hours: int = 2) -> dict`，结构：`{"buckets": [{"start":"13:00","end":"15:00","avg_active","peak_active","min_active","peak_queue","samples"}], "day_peak_active", "pressure_signals": [{"start","end","reason"}]}`
  - `extract_result_id(msg: str) -> str|None`（匹配连续 ≥10 位数字或 `resultId[:：] *(xxx)`）
  - `stage_perf(records) -> dict`：`{stage: {"count", "avg_ms", "max_ms"}}`，从"阶段执行成功, ..., costMs:N"提取。

- [ ] **Step 1: 写失败测试**（用构造记录而非真实文件，便于精确断言）

`tests/test_stats.py`:
```python
from core.stats import collect_failures, collect_threadpool, extract_result_id

def rec(ts, msg, level="ERROR", logger="c.y.X", line_no=1, thread="t1"):
    return {"ts": ts, "thread": thread, "level": level, "logger": logger, "msg": msg, "line_no": line_no}

def test_extract_result_id():
    assert extract_result_id("resultId：2092912713964584960，润色") == "2092912713964584960"
    assert extract_result_id("resultId: 123, x") == "123"
    assert extract_result_id("没有id") is None

def test_failures_riskcontrol_and_stage():
    recs = [
        rec("2026-09-03 14:00:00.000", "风控异常 com.yunhen...NonRetryableException: xxx", line_no=10),
        rec("2026-09-03 14:05:00.000", "阶段EVIDENCE_EXTRACTION失败，流水线终止, resultId:2092912736634798080, 原因:xxx", line_no=20),
        rec("2026-09-03 14:06:00.000", "作文批阅成功，已落库markType=1, resultId:2092912752376020992, 失败阶段:{\"POLISH\":\"SUCCESS\"}", line_no=30),
    ]
    f = collect_failures(recs)
    assert len(f["风控异常"]) == 1 and f["风控异常"][0]["line_no"] == 10
    assert "EVIDENCE_EXTRACTION" in f["阶段失败"]
    # "批阅成功"不进失败，但计入误用级别
    assert len(f.get("批阅失败落库", [])) == 0
    assert f["疑似误用ERROR级别"] == 1

def test_failures_mark_fail():
    recs = [rec("2026-09-03 14:00:00.000", "作文批阅失败，已落库markType=-1, resultId:2092912751797207040, 失败阶段:EVIDENCE_EXTRACTION")]
    f = collect_failures(recs)
    assert len(f["批阅失败落库"]) == 1

def test_threadpool_buckets_and_signal():
    def tp(ts, active, queue_left=1000):
        return rec(ts, f"线程池监控:线程池监控[活跃线程数={active}, 核心线程数=80, 最大线程数=100, 当前线程数=72, 历史最大线程数=80, 队列任务数=0, 队列剩余容量={queue_left}]", level="INFO")
    recs = [tp("2026-09-03 13:30:00.000", 10), tp("2026-09-03 14:10:00.000", 30),
            tp("2026-09-03 14:20:00.000", 85), tp("2026-09-03 15:10:00.000", 5)]
    r = collect_threadpool(recs, bucket_hours=2)
    starts = [b["start"] for b in r["buckets"]]
    assert "14:00" in starts  # 14:00-16:00 桶
    b14 = [b for b in r["buckets"] if b["start"] == "14:00"][0]
    assert b14["samples"] == 2 and b14["peak_active"] == 85
    assert r["day_peak_active"] == 85
    # 活跃85 >= 100*0.8 → 压力信号
    assert any("14:00" in s["start"] for s in r["pressure_signals"])
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_stats.py -v` → FAIL

- [ ] **Step 3: 实现 `core/stats.py`**

```python
# -*- coding: utf-8 -*-
"""轨道A：确定性统计（失败事件 + 线程池分时段汇总 + 阶段性能）"""
import re
from datetime import datetime
from collections import defaultdict

RE_RESULT_ID = re.compile(r'resultId[:：]\s*(\d{10,})')
RE_STAGE_OK = re.compile(r'阶段执行成功, resultId:\d+, stage:(\w+), costMs:(\d+)')
RE_TP = re.compile(r'活跃线程数=(\d+), 核心线程数=(\d+), 最大线程数=(\d+), 当前线程数=(\d+), 历史最大线程数=(\d+), 队列任务数=(\d+), 队列剩余容量=(\d+)')
RE_STAGE_FAIL = re.compile(r'阶段(\w+)失败')
# ERROR 但语义为成功 → 误用级别
FALSE_ERROR = re.compile(r'成功|SUCCESS|合规[:：]\s*true|完成')

def extract_result_id(msg):
    m = RE_RESULT_ID.search(msg)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{16,})\b', msg)
    return m.group(1) if m else None

def collect_failures(records):
    out = {"风控异常": [], "阶段失败": defaultdict(list), "批阅失败落库": [],
           "网关分支不足": [], "疑似误用ERROR级别": 0}
    for r in records:
        msg, is_err = r["msg"], r["level"] == "ERROR"
        if "NonRetryableException" in msg or msg.startswith("风控异常"):
            out["风控异常"].append({"ts": r["ts"], "resultId": extract_result_id(msg), "line_no": r["line_no"]})
        if "阶段执行失败" in msg or "失败，流水线终止" in msg:
            sm = RE_STAGE_FAIL.search(msg)
            stage = sm.group(1) if sm else "UNKNOWN"
            out["阶段失败"][stage].append({"ts": r["ts"], "resultId": extract_result_id(msg), "line_no": r["line_no"]})
        if "作文批阅失败，已落库" in msg:
            out["批阅失败落库"].append({"ts": r["ts"], "resultId": extract_result_id(msg), "line_no": r["line_no"]})
        if "并行网关成功分支数不足" in msg:
            out["网关分支不足"].append({"ts": r["ts"], "resultId": extract_result_id(msg), "line_no": r["line_no"]})
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
                        "active": int(m.group(1)), "queue_left": int(m.group(7))})
    if not pts:
        return {"buckets": [], "day_peak_active": 0, "pressure_signals": []}
    pts.sort(key=lambda p: p["t"])
    bucket_sec = bucket_hours * 3600
    base = pts[0]["t"].replace(minute=0, second=0, microsecond=0)
    buckets = defaultdict(list)
    for p in pts:
        idx = int((p["t"] - base).total_seconds() // bucket_sec)
        buckets[idx].append(p)
    out, signals = [], []
    for idx, group in sorted(buckets.items()):
        start = base.timestamp() + idx * bucket_sec
        s_dt, e_dt = datetime.fromtimestamp(start), datetime.fromtimestamp(start + bucket_sec)
        b = {"start": s_dt.strftime("%H:%M"), "end": e_dt.strftime("%H:%M"),
             "avg_active": round(sum(g["active"] for g in group) / len(group), 1),
             "peak_active": max(g["active"] for g in group),
             "min_active": min(g["active"] for g in group),
             "peak_queue_left": min(g["queue_left"] for g in group),
             "samples": len(group)}
        out.append(b)
        if b["peak_active"] >= 80 or b["peak_queue_left"] <= 200:  # 100*0.8 / 1000*0.2
            reason = "活跃线程达峰值≥80" if b["peak_active"] >= 80 else "队列剩余容量过低"
            signals.append({"start": b["start"], "end": b["end"], "reason": reason})
    return {"buckets": out, "day_peak_active": max(p["active"] for p in pts),
            "pressure_signals": signals}

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
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest tests/test_stats.py -v` → 4 passed

- [ ] **Step 5: 真实样本核对**（验收锚点）

```bash
python -c "from core.parser import parse_file; from core.stats import collect_failures, collect_threadpool; recs=list(parse_file('composition-ai.log')); f=collect_failures(recs); print('风控异常:',len(f['风控异常'])); print('阶段失败:',{k:len(v) for k,v in f['阶段失败'].items()}); print('误用ERROR:',f['疑似误用ERROR级别']); tp=collect_threadpool(recs); print('线程池样本桶数:',len(tp['buckets']),'全天峰值:',tp['day_peak_active'])"
```
Expected: 风控异常 3；阶段失败含 EVIDENCE_EXTRACTION；误用 ERROR ≈ 1000+；桶数 ≥ 1。

- [ ] **Step 6: Commit**

```bash
git add core/stats.py tests/test_stats.py
git commit -m "feat: 轨道A失败统计与线程池分时段汇总"
```

---

### Task 3: compressor 压缩去噪

**Files:**
- Create: `core/compressor.py`, `tests/test_compressor.py`

**Interfaces:**
- Consumes: 记录 dict 列表。
- Produces: `compress(records: list[dict]) -> dict`：
  ```python
  {
    "body": str,            # 归并后正文：每组一行样例 + " ×N"，线程池监控不进 body
    "routine": {"模型会话": int, "阶段成功": int, "路由选择": int, "线程池监控": int},
    "template_count": int,  # 归并组数
    "char_count": int,      # body 字符数
    "est_tokens": int,      # char_count * 0.9
    "failure_details": str  # 轨道B提示词附带的失败事件明细（来自失败记录原文，最多50条）
  }
  ```

- [ ] **Step 1: 写失败测试**

`tests/test_compressor.py`:
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_compressor.py -v` → FAIL

- [ ] **Step 3: 实现 `core/compressor.py`**

```python
# -*- coding: utf-8 -*-
"""轨道B压缩：例行日志计数化 + 大载荷截断 + 模板归并"""
import re

ROUTINE_PATTERNS = [("模型会话", ("豆包模型会话", "千问模型会话")),
                    ("路由选择", ("调用方显式指定模型",)),
                    ("阶段成功", ("阶段执行成功",))]
RE_TP_LINE = "线程池监控"
RE_RESULT_ID = re.compile(r'\b\d{16,}\b')

def _mask(msg: str) -> str:
    t = re.sub(r'\d{4,}', '#', msg)
    t = re.sub(r'\b\d+\b', '#', t)
    return t[:80]

def _is_routine(r):
    for name, keys in ROUTINE_PATTERNS:
        if any(k in r["msg"] for k in keys):
            return name
    if RE_TP_LINE in r["msg"]:
        return "线程池监控"
    return None

def _is_failure(r):
    return any(k in r["msg"] for k in
               ("NonRetryableException", "阶段执行失败", "失败，流水线终止",
                "作文批阅失败", "并行网关成功分支数不足", "风控异常"))

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
    return {"body": "\n".join(body_lines), "routine": routine,
            "template_count": len(groups), "char_count": len("\n".join(body_lines)),
            "est_tokens": int(len("\n".join(body_lines)) * 0.9),
            "failure_details": "\n".join(failures[:50])}
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest tests/test_compressor.py -v` → 3 passed

- [ ] **Step 5: 真实样本核对**

```bash
python -c "from core.parser import parse_file; from core.compressor import compress; r=compress(list(parse_file('composition-ai.log'))); print('字符数:',r['char_count'],'模板组:',r['template_count'],'est_tokens:',r['est_tokens']); print('routine:',r['routine'])"
```
Expected: char_count ≈ 13~15 万；template_count ≈ 300+；模型会话 ≈ 1708；线程池监控 = 322。（设计文档实测锚点：137,629 字符 / 324 组）

- [ ] **Step 6: Commit**

```bash
git add core/compressor.py tests/test_compressor.py
git commit -m "feat: 轨道B压缩去噪(归并+截断+例行计数)"
```

---

### Task 4: chunker 切块

**Files:**
- Create: `core/chunker.py`, `tests/test_chunker.py`

**Interfaces:**
- Produces: `chunk_records(records: list[dict], max_chars: int = 300000) -> list[list[dict]]`（按记录分组，单组序列化后字符数 ≤ max_chars；超大单条记录独立成块并截断）；`est_tokens(chars: int) -> int` = `chars * 0.9`。

- [ ] **Step 1: 写失败测试**

```python
from core.chunker import chunk_records, est_tokens

def rec(i, msg="x" * 100):
    return {"ts": f"2026-09-03 13:{i//60:02d}:{i%60:02d}.000", "thread": "t", "level": "INFO",
            "logger": "a.B", "msg": msg, "line_no": i}

def test_chunk_by_record_boundary():
    recs = [rec(i) for i in range(100)]  # 每条约120字符
    chunks = chunk_records(recs, max_chars=600)
    assert all(len(c) > 0 for c in chunks)
    # 没有记录被切断：重组后总数不变
    assert sum(len(c) for c in chunks) == 100
    # 每块序列化后 ≤ 600（单条超大除外）
    for c in chunks:
        size = sum(len(r["msg"]) + 120 for r in c)
        assert size <= 600 or len(c) == 1

def test_est_tokens():
    assert est_tokens(1000) == 900
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 `core/chunker.py`**

```python
# -*- coding: utf-8 -*-
"""按记录边界切块；单条记录约按 msg+120(头部开销) 计算体积"""

def est_tokens(chars: int) -> int:
    return int(chars * 0.9)

def _size(r) -> int:
    return len(r["msg"]) + 120

def chunk_records(records, max_chars=300000):
    chunks, cur, cur_size = [], [], 0
    for r in records:
        s = _size(r)
        if cur and cur_size + s > max_chars:
            chunks.append(cur)
            cur, cur_size = [], 0
        cur.append(r)
        cur_size += s
    if cur:
        chunks.append(cur)
    return chunks
```

- [ ] **Step 4: 运行测试通过** → 2 passed

- [ ] **Step 5: Commit**

```bash
git add core/chunker.py tests/test_chunker.py
git commit -m "feat: 按记录边界切块与token估算"
```

---

### Task 5: analyzer 模型分析（Map-Reduce）

**Files:**
- Create: `core/analyzer.py`, `tests/test_analyzer.py`

**Interfaces:**
- Consumes: Task 3 的 compress 结果、Task 2 的 stats 结果、templates JSON。
- Produces:
  - `render_prompt(template: dict, stats_summary: str, routine: dict, chunk_text: str, is_final: bool) -> str`
  - `call_model(messages: list[dict], cfg: dict) -> str`（httpx POST `{base_url}/chat/completions`，30s~300s 超时，非 200 抛异常，重试由上层管）
  - `run_analysis(compress_result: dict, stats_result: dict, template: dict, cfg: dict, on_progress=None) -> str`（返回"模型分析"章节 Markdown；内部：切块→并发3 Map（`asyncio.Semaphore` + `asyncio.gather`，单块失败重试2次后插占位符"【该块分析缺失】"）→ 若摘要总量 > 30万字符则每20份二级归并 → 最终 Reduce；统计 `total_tokens_used` 存入返回 dict 的 `usage` 字段）
  - cfg: `{"base_url","api_key","model","concurrency"}` 来自 config.json。

- [ ] **Step 1: 写失败测试**（monkeypatch call_model，不发真实请求）

```python
import core.analyzer as az

def test_render_prompt_contains_template():
    tpl = {"name": "日常巡检", "concern": "失败原因", "focus": ["风控异常"],
           "outputFormat": "Markdown三节", "extraRules": "结合线程池负载"}
    p = az.render_prompt(tpl, "统计: 风控3次", {"模型会话": 10}, "chunk内容", is_final=False)
    assert "失败原因" in p and "风控异常" in p and "chunk内容" in p and "统计" in p

def test_call_model_posts_to_openai_compatible(monkeypatch):
    captured = {}
    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "模型返回"}}],
                    "usage": {"total_tokens": 123}}
    class FakeClient:
        def post(self, url, json=None, timeout=None, headers=None):
            captured.update(url=url, json=json)
            return FakeResp()
    monkeypatch.setattr(az.httpx, "Client", lambda **kw: FakeClient())
    out = az.call_model([{"role": "user", "content": "hi"}],
                        {"base_url": "https://api.x.com/v1", "api_key": "sk-1", "model": "m1"})
    assert out == "模型返回"
    assert captured["url"] == "https://api.x.com/v1/chat/completions"
    assert captured["json"]["model"] == "m1"

def test_run_analysis_map_reduce(monkeypatch):
    monkeypatch.setattr(az, "call_model", lambda msgs, cfg: f"摘要({len(msgs[0]['content'])})")
    comp = {"body": "行1\n" * 100, "routine": {}, "template_count": 1,
            "char_count": 500, "est_tokens": 450, "failure_details": ""}
    out = az.run_analysis(comp, {"风控异常": []}, {"name": "t", "concern": "c",
                          "focus": [], "outputFormat": "md", "extraRules": ""},
                          {"base_url": "u", "api_key": "k", "model": "m", "concurrency": 2})
    assert "摘要(" in out  # Map与Reduce均调用了桩
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 `core/analyzer.py`**

```python
# -*- coding: utf-8 -*-
"""轨道B：Map-Reduce 模型分析（OpenAI 兼容 API）"""
import asyncio
import httpx

RETRY = 2
CONCURRENCY = 3

def render_prompt(template, stats_summary, routine, chunk_text, is_final):
    role = ("你是汇总者：以下是多个日志分块的中间摘要与统计信息，产出最终分析。" if is_final
            else "你是日志分析员：分析以下日志分块，输出中间摘要。")
    return f"""{role}

【分析模版】
- 名称：{template.get('name', '')}
- 关注方向：{template.get('concern', '')}
- 关注点：{'、'.join(template.get('focus', []))}
- 输出格式要求：{template.get('outputFormat', '')}
- 附加规则：{template.get('extraRules', '')}

【代码统计结果（精确，可直接引用）】
{stats_summary}

【例行日志计数】
{routine}

【{'中间摘要集合' if is_final else '日志分块内容'}】
{chunk_text}"""

def call_model(messages, cfg):
    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={"model": cfg["model"], "messages": messages, "temperature": 0.3})
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

def _stats_to_summary(stats_result):
    lines = []
    for k in ("风控异常", "批阅失败落库", "网关分支不足"):
        lines.append(f"{k}: {len(stats_result.get(k, []))} 次")
    for stage, items in stats_result.get("阶段失败", {}).items():
        lines.append(f"阶段失败-{stage}: {len(items)} 次")
    tp = stats_result.get("_threadpool", {})
    if tp:
        lines.append(f"线程池全天峰值活跃: {tp.get('day_peak_active')}")
    return "\n".join(lines)

def _chunk_text_by_chars(body, max_chars=300000):
    lines, chunks, size = body.splitlines(), [], 0
    cur = []
    for ln in lines:
        if cur and size + len(ln) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks

def run_analysis(compress_result, stats_result, template, cfg, on_progress=None):
    chunks = _chunk_text_by_chars(compress_result["body"])
    stats_summary = _stats_to_summary(stats_result) + "\n失败明细:\n" + (compress_result.get("failure_details") or "无")
    routine = "\n".join(f"{k}: {v} 次" for k, v in compress_result["routine"].items())

    async def _map():
        sem = asyncio.Semaphore(cfg.get("concurrency", CONCURRENCY))
        async def one(i, text):
            async with sem:
                prompt = render_prompt(template, stats_summary, routine, f"[分块 {i+1}/{len(chunks)}]\n{text}", is_final=False)
                for attempt in range(1 + RETRY):
                    try:
                        return await asyncio.to_thread(call_model, [{"role": "user", "content": prompt}], cfg)
                    except Exception:
                        if attempt == RETRY:
                            return "【该块分析缺失】"
                        await asyncio.sleep(2)
        return await asyncio.gather(*[one(i, t) for i, t in enumerate(chunks)])

    async def _main():
        summaries = await _map()
        text = "\n\n".join(f"### 分块{i+1}摘要\n{s}" for i, s in enumerate(summaries))
        while len(text) > 300000:  # 二级归并，每20份一批
            parts = [text[i:i+300000] for i in range(0, len(text), 300000)]
            text = "\n\n".join(await asyncio.gather(*[
                asyncio.to_thread(call_model,
                    [{"role": "user", "content": render_prompt(template, stats_summary, routine,
                                                               f"[中间摘要 {j+1}/{len(parts)}]\n{p}", is_final=True)}], cfg)
                for j, p in enumerate(parts)]))
        prompt = render_prompt(template, stats_summary, routine, text, is_final=True)
        return await asyncio.to_thread(call_model, [{"role": "user", "content": prompt}], cfg)

    return asyncio.run(_main())
```

- [ ] **Step 4: 运行测试通过**

Run: `python -m pytest tests/test_analyzer.py -v` → 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/analyzer.py tests/test_analyzer.py
git commit -m "feat: Map-Reduce模型分析管线"
```

---

### Task 6: 报告生成 + FastAPI 后端

**Files:**
- Create: `main.py`, `core/report.py`, `config.json`, `templates/日常巡检.json`, `tests/test_report.py`
- Modify: 无

**Interfaces:**
- Consumes: Task 2/3/5 全部产出。
- Produces:
  - `report.build_report(meta: dict, stats_result: dict, compress_result: dict|None, model_section: str|None, routine: dict, usage) -> str`（Markdown，结构见设计文档第10节：头部元信息→核心指标(失败表+线程池表+例行计数)→模型分析→附录失败明细带行号）
  - HTTP API（全部 JSON）：
    - `POST /api/preview`（multipart file 或 `{path}`）→ `{size_bytes, line_count, time_span, est_tokens, chunk_count, est_cost, est_seconds}`（秒级：只做解析+压缩，不调模型）
    - `POST /api/analyze`（multipart file 或 `{path}` + `mode: "stats_only"|"full"` + `template` + `bucket_hours` + `max_chars`）→ `{report_path}`（服务端落盘 reports/，文件名 `{YYYY-MM-DD_HHMM}_{原名}.md`）
    - `GET /api/templates` / `POST /api/templates`（增删改查，落盘 templates/）
    - `GET /api/config` / `POST /api/config`（回显时 api_key 脱敏为 `sk-****尾4位`，`key_set: true`）
    - `GET /api/reports`（列 reports/ 下文件，按日期倒序）；`GET /api/reports/{name}`（返回 Markdown 内容）
    - 静态文件挂载 `/` → `web/index.html`
  - `config.json` 初始内容：`{"provider":"custom","base_url":"","api_key":"","model":"","concurrency":3,"max_chars":300000,"bucket_hours":2}`；预置厂商下拉数据硬编码在 index.html（火山方舟 `https://ark.cn-beijing.volces.com/api/v3`、Kimi `https://api.moonshot.cn/v1`、DeepSeek `https://api.deepseek.com/v1`、智谱 `https://open.bigmodel.cn/api/paas/v4`）。
  - 成本估算：`est_tokens/1e6 × 单价(默认 ¥4/百万)`。
  - `templates/日常巡检.json`：内容即设计文档第 8 节的 JSON。

- [ ] **Step 1: 写 report 失败测试**

```python
from core.report import build_report

def test_build_report_stats_only():
    stats = {"风控异常": [{"ts": "2026-09-03 14:00:00.000", "resultId": "123", "line_no": 10}],
             "阶段失败": {"EVIDENCE_EXTRACTION": [{"ts": "t", "resultId": "456", "line_no": 20}]},
             "批阅失败落库": [], "网关分支不足": [], "疑似误用ERROR级别": 5,
             "_threadpool": {"buckets": [{"start": "14:00", "end": "16:00", "avg_active": 30.0,
                              "peak_active": 72, "min_active": 10, "peak_queue_left": 1000,
                              "samples": 100}], "day_peak_active": 72, "pressure_signals": []}}
    md = build_report(meta={"date": "2026-09-03", "mode": "仅统计", "template_name": "-",
                            "cost": "¥0.00", "source": "a.log"},
                      stats_result=stats, compress_result=None, model_section=None,
                      routine={"模型会话": 1708, "阶段成功": 943, "路由选择": 833, "线程池监控": 322}, usage=None)
    assert "# 2026-09-03 日志分析报告" in md
    assert "风控异常" in md and "1" in md
    assert "14:00" in md and "72" in md
    assert "模型会话" in md
    assert "模型分析" not in md  # 仅统计模式不含该节
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 `core/report.py`**（按测试与设计第10节结构写 Markdown 表格；线程池表列：时段/均值/峰值/谷值/队列剩余峰值/采样数；失败表列：类型/次数/样例resultId）

- [ ] **Step 4: 测试通过后实现 `main.py`**

要点：FastAPI app；`/api/preview` 保存上传文件到临时目录（或直接读 path）→ parse → compress → 返回预估；`/api/analyze` 根据 mode 决定是否调 `run_analysis`；报告写 `reports/`；`app.mount("/", StaticFiles(directory="web", html=True))`；`uvicorn.run(app, host="127.0.0.1", port=8899)`。

- [ ] **Step 5: 手动冒烟**

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8899 &
curl -s -X POST http://127.0.0.1:8899/api/preview -F "file=@composition-ai.log"
curl -s -X POST http://127.0.0.1:8899/api/analyze -F "file=@composition-ai.log" -F "mode=stats_only" -F "template=日常巡检"
```
Expected: preview 返回 est_tokens ≈ 12 万、chunk_count = 1；analyze 返回 report_path，文件存在且包含风控异常 3 次。

- [ ] **Step 6: Commit**

```bash
git add main.py core/report.py config.json templates/ tests/test_report.py
git commit -m "feat: FastAPI后端与报告生成"
```

---

### Task 7: 前端单页

**Files:**
- Create: `web/index.html`（单文件，原生 HTML/CSS/JS，无构建）

**Interfaces:**
- Consumes: Task 6 的全部 API。
- Produces: 三区块页面（上传配置/预估确认/结果展示）+ 设置弹窗 + 模版编辑弹窗。

- [ ] **Step 1: 实现页面**（一个文件包含：file input + 本地路径输入、模版下拉（GET /api/templates 填充）、模式单选（仅统计/统计+模型）、时间桶 select(1/2/3h)、块大小 number(默认300)；上传后 fetch /api/preview 渲染预估值和"开始分析"按钮；分析中显示 loading（fetch 流式轮询或转圈即可）；完成后 fetch 报告 Markdown 用简易 md 渲染（`<pre>` 或引入 marked CDN）展示；历史列表 GET /api/reports 渲染可点击）

- [ ] **Step 2: 浏览器手工验证**（OpenPreview 或用户自开 `http://127.0.0.1:8899`）

流程走通：上传 composition-ai.log → 看到预估（~12万tokens/1块/成本<¥1）→ 仅统计模式 → 报告渲染含失败表与线程池表 → 历史列表出现该报告。

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat: 单页前端(上传/预估/报告/历史)"
```

---

### Task 8: 端到端验收（真实模型）

**Files:**
- Modify: 无（验证性任务）

- [ ] **Step 1: 配置真实模型**：用户在设置弹窗填任一厂商 BaseURL/Key/模型名（或编辑 config.json）。

- [ ] **Step 2: 完整模式跑 composition-ai.log**：确认报告"模型分析"节按模版输出【结论】【异常明细】【建议】三节；消耗 < ¥1；总耗时 < 3 分钟。

- [ ] **Step 3: 验收清单逐项打勾**（对照设计文档第 12 节）：
  - 风控异常 3、EVIDENCE_EXTRACTION 3、线程池 322 条分桶正确
  - 压缩 8.03MB → ~13~15 万字符
  - 仅统计秒出；完整模式单块成本 < ¥1
  - 前端全流程（上传→预估→确认→报告→历史）
  - API Key 页面回显为 `sk-****`

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "chore: 端到端验收通过"
```
