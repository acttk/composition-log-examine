# -*- coding: utf-8 -*-
"""FastAPI 后端：预览 / 分析 / 模版 / 配置 / 报告 API（全部 JSON 响应）"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile
from urllib.parse import quote

from core.analyzer import run_analysis
from core.chunker import chunk_body
from core.compressor import compress
from core.parser import parse_file
from core.pricing import calc_cost, lookup_price, is_subscription
from core.report import build_report
from core.stats import collect_failures, collect_threadpool, collect_processed_users

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATES_DIR = BASE_DIR / "templates"
REPORTS_DIR = BASE_DIR / "reports"
USAGE_LOG_PATH = BASE_DIR / "usage_log.json"
USAGE_LOCK = threading.Lock()

DEFAULT_CONFIG = {"provider": "custom", "base_url": "", "api_key": "", "model": "",
                  "concurrency": 3, "max_chars": 300000, "bucket_hours": 2}

app = FastAPI(title="日志智能分析工具")

# 分析任务注册表：task_id → {percent, stage, detail, done, error, result}
TASKS = {}


# ─────────────────────── 基础工具 ───────────────────────

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return cfg


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_name(name) -> None:
    """名称安全校验：禁止路径穿越（/ \ ..）"""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法名称")


def _list_templates() -> list:
    TEMPLATES_DIR.mkdir(exist_ok=True)
    out = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return out


def _load_template(name) -> dict:
    if not name:  # 未指定 → 取默认模版
        for t in _list_templates():
            if t.get("is_default"):
                return t
        raise HTTPException(400, "未指定模版且不存在默认模版")
    _check_name(name)
    p = TEMPLATES_DIR / f"{name}.json"
    if not p.is_file():
        raise HTTPException(404, f"模版不存在: {name}")
    return json.loads(p.read_text(encoding="utf-8"))


def _to_int(value, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"非法数值: {value}")


def _cleanup(paths, is_temp: bool) -> None:
    if is_temp:
        for path in paths if isinstance(paths, list) else [paths]:
            try:
                os.unlink(path)
            except OSError:
                pass


async def _resolve_input(request: Request):
    """multipart files 上传（支持多文件）→ 存临时文件；JSON body {"paths":[...]} → 本地路径。
    返回 (paths, filenames, fields, is_temp)：均为列表，保持一一对应"""
    if request.headers.get("content-type", "").startswith("multipart/"):
        form = await request.form()
        ups = [v for v in form.multi_items() if v[0] == "file" and isinstance(v[1], UploadFile)]
        if not ups:
            raise HTTPException(400, "multipart 请求需包含 file 字段")
        fields = {k: v for k, v in form.multi_items() if isinstance(v, str) and k != "file"}
        tmps, names = [], []
        for _, up in ups:
            suffix = Path(up.filename or "upload.log").suffix or ".log"
            fd, tmp = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(up.file, out)
            except Exception:
                os.unlink(tmp)
                for p in tmps:
                    os.unlink(p)
                raise
            tmps.append(tmp)
            names.append(up.filename or Path(tmp).name)
        return tmps, names, fields, True
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, '需提供 multipart file 或 JSON body {"paths":[...]}')
    fields = {k: v for k, v in body.items() if k != "paths" and k != "path"}
    ps = body.get("paths") if isinstance(body.get("paths"), list) else (
        [body["path"]] if isinstance(body.get("path"), str) else None)
    if not ps:
        raise HTTPException(400, "需提供 paths 数组")
    paths, names = [], []
    for p in ps:
        if not isinstance(p, str) or not p or not Path(p).is_file():
            raise HTTPException(400, f"文件不存在: {p}")
        paths.append(p)
        names.append(Path(p).name)
    return paths, names, fields, False


def _merge_records(paths: list, filenames: list, on_file_done=None):
    """逐文件解析后合并为单一记录流：按时间戳全局排序，次键为行号，保证稳定顺序。
    每条记录附带 source（来源文件名），供报告附录行号标注。"""
    records = []
    for path, name in zip(paths, filenames):
        rs = list(parse_file(path))
        for r in rs:
            r["source"] = name
        records.extend(rs)
        if on_file_done:
            on_file_done(name, len(rs))
    records.sort(key=lambda r: (r["ts"], r["line_no"]))
    return records


def _source_label(filenames: list) -> str:
    """来源展示：单文件 → 文件名；多文件 → a.log + b.log（N 个文件）"""
    if len(filenames) == 1:
        return filenames[0]
    return " + ".join(filenames) + f"（{len(filenames)} 个文件）"


# ─────────────────────── 预览 / 分析 ───────────────────────

def recommend_max_chars(body_chars: int, cap: int = 300000, floor: int = 50000) -> int:
    """按压缩后正文字符数推荐分块大小（字符数，向上取整到千位=整KB）。
    目标：在单块不超过 cap 的前提下均衡分块，避免最后一块只剩零头。
    例：245 万字符 → 9 块 × 274K；10 万字符 → 单块 100K。
    小正文设 50K 下限：单块装得下，下限只为避免显示 2K 这类过小值。"""
    if body_chars <= 0:
        return cap
    n = -(-body_chars // cap)                    # ceil(body/cap)：所需最少块数
    rec = -(-body_chars // n // 1000) * 1000      # ceil(body/n) 取整到千字符
    return min(max(rec, floor), cap)


@app.post("/api/preview")
def api_preview(payload=Depends(_resolve_input)):
    """只做解析+压缩，不调模型，秒级"""
    paths, filenames, fields, is_temp = payload
    try:
        records = _merge_records(paths, filenames)
        c = compress(records)
        cfg = load_config()
        recommended = recommend_max_chars(len(c["body"]))
        # auto_chunk=1：用户未手动改过块大小 → 直接用推荐值预估（块数与展示值一致）
        auto = str(fields.get("auto_chunk", "")).lower() in ("1", "true")
        max_chars = recommended if auto else _to_int(fields.get("max_chars"),
                                                     int(cfg.get("max_chars") or 300000))
        # 空 body 时 chunk_count 归零（chunk_body 对空串返回 [""]）
        chunks = chunk_body(c["body"], max_chars) if c["body"].strip() else []
        price = lookup_price(str(cfg.get("model") or ""))  # (输入¥/M, 输出¥/M) 或 None
        # 预估：全部按输入单价粗估（实际以接口返回 usage 为准）
        est_cost = round(c["est_tokens"] / 1e6 * price[0], 4) if price else None
        line_count = sum(1 for _ in open(paths[0], "rb"))
        for p in paths[1:]:
            line_count += sum(1 for _ in open(p, "rb"))
        total_size = sum(os.path.getsize(p) for p in paths)
        time_span = f"{records[0]['ts']} ~ {records[-1]['ts']}" if records else "-"
        return {"size_bytes": total_size,
                "file_count": len(paths),
                "line_count": line_count,
                "time_span": time_span,
                "est_tokens": c["est_tokens"],
                "chunk_count": len(chunks),
                "recommended_max_chars": recommended,
                "est_cost": est_cost,
                "est_seconds": len(chunks) * 90}
    finally:
        _cleanup(paths, is_temp)


@app.post("/api/compress")
def api_compress_download(payload=Depends(_resolve_input)):
    """压缩并返回可下载的压缩日志文本（带统计头）"""
    paths, filenames, fields, is_temp = payload
    try:
        records = _merge_records(paths, filenames)
        c = compress(records)
        total_size = sum(os.path.getsize(p) for p in paths)
        header = [
            f"# 压缩日志 · 来源: {_source_label(filenames)}",
            f"# 原始: {total_size:,} 字节 · {len(records):,} 条记录 → "
            f"压缩: {c['char_count']:,} 字符 · {c['template_count']} 模板组 · ≈{c['est_tokens']:,} tokens",
            f"# 生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "#",
        ]
        text = "\n".join(header) + "\n" + c["body"] + "\n"
        stem = re.sub(r'[\\/:*?"<>|]', "_", Path(filenames[0]).stem) or "log"
        suffix = f"_plus{len(paths) - 1}" if len(paths) > 1 else ""
        out_name = f"{stem}{suffix}_compressed.log"
        return Response(content=text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition":
                                 f"attachment; filename*=UTF-8''{quote(out_name)}"})
    finally:
        _cleanup(paths, is_temp)


@app.post("/api/analyze")
def api_analyze(payload=Depends(_resolve_input)):
    """启动后台分析线程，立即返回 task_id；进度经 /api/analyze/status/{task_id} 轮询"""
    paths, filenames, fields, is_temp = payload
    mode = fields.get("mode") or "stats_only"
    if mode not in ("stats_only", "full"):
        _cleanup(paths, is_temp)
        raise HTTPException(400, "mode 必须为 stats_only 或 full")
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {"percent": 1, "stage": "启动", "detail": _source_label(filenames),
                      "done": False, "error": None, "result": None}
    threading.Thread(target=_run_analysis_task,
                     args=(task_id, paths, filenames, fields, is_temp), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/analyze/status/{task_id}")
def api_analyze_status(task_id: str):
    t = TASKS.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在或已过期")
    return t


def _append_usage_log(rec: dict) -> None:
    """追加一条分析消耗记录（线程安全，保留最近 500 条）"""
    with USAGE_LOCK:
        data = []
        if USAGE_LOG_PATH.exists():
            try:
                data = json.loads(USAGE_LOG_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = []
        data.append(rec)
        USAGE_LOG_PATH.write_text(json.dumps(data[-500:], ensure_ascii=False, indent=1),
                                  encoding="utf-8")


def _run_analysis_task(task_id, paths, filenames, fields, is_temp):
    t = TASKS[task_id]
    start = time.time()
    try:
        mode = fields.get("mode") or "stats_only"
        cfg = load_config()
        bucket_hours = _to_int(fields.get("bucket_hours"), int(cfg.get("bucket_hours") or 2))
        max_chars = _to_int(fields.get("max_chars"), int(cfg.get("max_chars") or 300000))
        template = _load_template(fields.get("template"))

        def _on_file(name, cnt):
            t.update(stage="解析日志", detail=f"{name}（{cnt:,} 条）")
        t.update(percent=5, stage="解析日志", detail=_source_label(filenames))
        records = _merge_records(paths, filenames, on_file_done=_on_file)
        t.update(percent=15, stage="确定性统计")
        stats_result = collect_failures(records)
        stats_result["_threadpool"] = collect_threadpool(records, bucket_hours)
        stats_result["_processed_users"] = collect_processed_users(records, bucket_hours)
        t.update(percent=25, stage="压缩去噪")
        compress_result = compress(records)

        model_section = None
        usage_total = None
        if mode == "full":
            mode_label = "统计+模型分析"

            def on_progress(pct, detail):
                t.update(percent=min(pct, 95), stage="模型分析", detail=str(detail))
            try:
                res = run_analysis(compress_result, stats_result, template,
                                   {**cfg, "max_chars": max_chars}, on_progress=on_progress)
                model_section = res["text"]
                usage_total = res["usage"]
            except Exception as e:  # 模型失败不阻塞整次分析
                model_section = f"模型调用失败: {e}"
        else:
            mode_label = "仅统计"

        # 费用：按接口返回的真实 usage 计算
        model = str(cfg.get("model") or "")
        if usage_total and usage_total.get("calls"):
            if is_subscription(model):  # Kimi Code API 等订阅制：已含在会员月费中
                cost = (f"会员订阅（实测 {usage_total['input_tokens']:,}+"
                        f"{usage_total['output_tokens']:,} tokens，已含在月费中）")
            else:
                cost_value = calc_cost(usage_total["input_tokens"], usage_total["output_tokens"], model)
                if cost_value is not None:
                    cost = f"¥{cost_value:.4f}（实测 {usage_total['input_tokens']:,}+{usage_total['output_tokens']:,} tokens）"
                else:
                    cost = "未知单价（tokens: " \
                           f"{usage_total['input_tokens']:,}+{usage_total['output_tokens']:,}）"
        else:
            cost = "¥0.00"

        t.update(percent=97, stage="生成报告")
        date = records[0]["ts"][:10] if records else datetime.now().strftime("%Y-%m-%d")
        usage_str = None
        if usage_total and usage_total.get("calls"):
            usage_str = (f"输入 {usage_total['input_tokens']:,} + 输出 {usage_total['output_tokens']:,} tokens"
                         f" · {usage_total['calls']} 次调用")
        md = build_report(meta={"date": date, "mode": mode_label,
                                "template_name": template.get("name", "-"),
                                "cost": cost, "source": _source_label(filenames)},
                          stats_result=stats_result, compress_result=compress_result,
                          model_section=model_section, routine=compress_result["routine"],
                          usage=usage_str)
        REPORTS_DIR.mkdir(exist_ok=True)
        stem = re.sub(r'[\\/:*?"<>|]', "_", Path(filenames[0]).stem) or "report"
        suffix = f"_plus{len(paths) - 1}" if len(paths) > 1 else ""
        report_path = REPORTS_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M')}_{stem}{suffix}.md"
        report_path.write_text(md, encoding="utf-8")

        duration_s = round(time.time() - start, 1)
        cost_value = (calc_cost(usage_total["input_tokens"], usage_total["output_tokens"], model)
                      if usage_total and usage_total.get("calls") else 0)
        _append_usage_log({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "source": _source_label(filenames), "mode": mode_label, "model": model,
                           "provider": cfg.get("provider", ""), "log_date": date,
                           "file_count": len(paths),
                           "calls": (usage_total or {}).get("calls", 0),
                           "input_tokens": (usage_total or {}).get("input_tokens", 0),
                           "output_tokens": (usage_total or {}).get("output_tokens", 0),
                           "cost": cost_value,
                           "subscription": is_subscription(model),
                           "duration_s": duration_s,
                           "report": report_path.name})
        t.update(percent=100, stage="完成", done=True,
                 result={"report_path": str(report_path), "duration_s": duration_s,
                         "usage": usage_total})
    except Exception as e:
        t.update(done=True, error=str(e), stage="失败")
    finally:
        _cleanup(paths, is_temp)


@app.get("/api/usage")
def api_usage():
    """每次分析的消耗台账：tokens / 费用 / 耗时 汇总"""
    with USAGE_LOCK:
        data = []
        if USAGE_LOG_PATH.exists():
            try:
                data = json.loads(USAGE_LOG_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = []
    totals = {"count": len(data), "calls": 0, "input_tokens": 0, "output_tokens": 0,
              "cost": 0.0, "duration_s": 0.0}
    for r in data:
        totals["calls"] += r.get("calls") or 0
        totals["input_tokens"] += r.get("input_tokens") or 0
        totals["output_tokens"] += r.get("output_tokens") or 0
        totals["cost"] += r.get("cost") or 0
        totals["duration_s"] += r.get("duration_s") or 0
    return {"records": list(reversed(data)), "totals": totals,
            "price_note": "按官方接口返回的真实 usage 计费；单价表见 core/pricing.py"}


# ─────────────────────── 模版管理 ───────────────────────

@app.get("/api/templates")
def api_list_templates():
    return _list_templates()


@app.post("/api/templates")
def api_save_template(body: dict):
    """保存/更新模版（同名覆盖）；设为默认时清除其它模版的默认标记"""
    name = body.get("name")
    _check_name(name)
    data = {"name": name,
            "concern": body.get("concern", ""),
            "focus": body.get("focus", []),
            "outputFormat": body.get("outputFormat", ""),
            "extraRules": body.get("extraRules", ""),
            "is_default": bool(body.get("is_default", False))}
    if data["is_default"]:
        TEMPLATES_DIR.mkdir(exist_ok=True)
        for p in TEMPLATES_DIR.glob("*.json"):
            if p.stem == name:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if d.get("is_default"):
                d["is_default"] = False
                p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    (TEMPLATES_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.post("/api/templates/delete")
def api_delete_template(body: dict):
    name = body.get("name")
    _check_name(name)
    p = TEMPLATES_DIR / f"{name}.json"
    if not p.is_file():
        raise HTTPException(404, f"模版不存在: {name}")
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("is_default"):
        raise HTTPException(400, "默认模版不可删除")
    p.unlink()
    return {"ok": True}


# ─────────────────────── 配置管理 ───────────────────────

@app.get("/api/config")
def api_get_config():
    cfg = load_config()
    key = str(cfg.get("api_key") or "")
    return {"provider": cfg.get("provider", "custom"),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "concurrency": cfg.get("concurrency", 3),
            "max_chars": cfg.get("max_chars", 300000),
            "bucket_hours": cfg.get("bucket_hours", 2),
            "key_set": bool(key),
            "key_tail": key[-4:] if key else ""}


@app.post("/api/config")
def api_set_config(body: dict):
    cfg = load_config()
    for k in ("provider", "base_url", "model"):
        if body.get(k) is not None:
            cfg[k] = str(body[k])
    for k in ("concurrency", "max_chars", "bucket_hours"):
        if body.get(k) is not None:
            try:
                cfg[k] = int(body[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 需为整数")
    if body.get("api_key"):  # 提交的 Key 为空 → 保留原 Key
        cfg["api_key"] = body["api_key"]
    _save_config(cfg)
    return {"ok": True}


# ─────────────────────── 报告查看 ───────────────────────

@app.get("/api/reports")
def api_list_reports(date: str | None = None):
    """按修改时间倒序；date=YYYY-MM-DD 时只返回该日期生成的报告"""
    REPORTS_DIR.mkdir(exist_ok=True)
    if date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise HTTPException(400, "date 需为 YYYY-MM-DD 格式")
    entries = []
    for p in REPORTS_DIR.iterdir():
        if p.is_file():
            st = p.stat()
            mtime = datetime.fromtimestamp(st.st_mtime)
            if date and mtime.strftime("%Y-%m-%d") != date:
                continue
            entries.append((st.st_mtime, {"name": p.name, "size": st.st_size,
                                           "mtime": mtime.strftime("%Y-%m-%d %H:%M:%S")}))
    entries.sort(key=lambda e: e[0], reverse=True)
    return [e[1] for e in entries]


@app.get("/api/reports/{name}")
def api_get_report(name: str):
    _check_name(name)
    p = REPORTS_DIR / name
    if not p.is_file():
        raise HTTPException(404, f"报告不存在: {name}")
    return {"content": p.read_text(encoding="utf-8")}


@app.post("/api/reports/delete")
def api_delete_report(body: dict):
    """删除报告档案；names 为列表时批量删除"""
    names = body.get("names")
    if not isinstance(names, list) or not names:
        names = [body.get("name")]
    names = [n for n in names if isinstance(n, str) and n]
    if not names:
        raise HTTPException(400, "需提供 name 或 names")
    deleted, missing = [], []
    for name in names:
        _check_name(name)
        p = REPORTS_DIR / name
        if p.is_file():
            p.unlink()
            deleted.append(name)
        else:
            missing.append(name)
    if not deleted:
        raise HTTPException(404, f"报告不存在: {', '.join(missing)}")
    return {"ok": True, "deleted": deleted, "missing": missing}


# ─────────────────────── 静态前端（Task 7；目录不存在时容错） ───────────────────────

_web_dir = BASE_DIR / "web"
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
else:
    @app.get("/")
    def _index():
        return {"message": "web/ 前端尚未创建；API：POST /api/preview、POST /api/analyze、"
                           "GET|POST /api/templates、POST /api/templates/delete、"
                           "GET|POST /api/config、GET /api/reports"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8899)
