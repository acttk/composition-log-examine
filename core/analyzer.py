# -*- coding: utf-8 -*-
"""轨道B：Map-Reduce 模型分析（OpenAI 兼容 API）"""
import asyncio
import httpx

from core.chunker import chunk_body

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
    client = httpx.Client(timeout=300)
    resp = client.post(
        f"{cfg['base_url'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['api_key']}"},
        json={"model": cfg["model"], "messages": messages})
    if resp.status_code != 200:
        raise RuntimeError(f"模型接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    # 官方接口返回的真实用量：输入/输出 tokens（计费依据）
    u = data.get("usage") or {}
    usage = {"input": int(u.get("prompt_tokens") or 0),
             "output": int(u.get("completion_tokens") or 0)}
    return content, usage

def _stats_to_summary(stats_result):
    lines = []
    for k in ("风控异常", "批阅失败落库", "网关分支不足"):
        lines.append(f"{k}: {len(stats_result.get(k, []))} 次")
    for stage, items in stats_result.get("阶段失败", {}).items():
        lines.append(f"阶段失败-{stage}: {len(items)} 次")
    tp = stats_result.get("_threadpool", {})
    if tp:
        lines.append(f"线程池全天并发任务峰值: {tp.get('day_peak_active')}")
        lines.append(f"线程池全天排队等待峰值: {tp.get('day_peak_queue_tasks')}")
        peaks = tp.get("server_peaks") or {}
        if len(peaks) > 1:
            per = ", ".join(f"{s}: 并发{v['peak_active']}/排队{v['peak_queue_tasks']}"
                             for s, v in peaks.items())
            lines.append(f"线程池各服务器全天峰值: {per}")
    pu = stats_result.get("_processed_users", {})
    if pu:
        lines.append(f"当天处理人数: {pu.get('day_total')}")
    return "\n".join(lines)

def run_analysis(compress_result, stats_result, template, cfg, on_progress=None):
    """返回 {"text": 最终分析文本, "usage": {"calls","input_tokens","output_tokens"}}。
    on_progress(percent, detail) 供外部展示进度（map 阶段 30→80）。"""
    max_chars = cfg.get("max_chars") or 300000
    chunks = chunk_body(compress_result["body"], max_chars)
    stats_summary = _stats_to_summary(stats_result) + "\n失败明细:\n" + (compress_result.get("failure_details") or "无")
    routine = "\n".join(f"{k}: {v} 次" for k, v in compress_result["routine"].items())
    total = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def _prog(pct, detail):
        if on_progress:
            try:
                on_progress(pct, detail)
            except Exception:
                pass

    async def _map():
        sem = asyncio.Semaphore(cfg.get("concurrency", CONCURRENCY))
        done = [0]

        async def one(i, text):
            async with sem:
                prompt = render_prompt(template, stats_summary, routine, f"[分块 {i+1}/{len(chunks)}]\n{text}", is_final=False)
                for attempt in range(1 + RETRY):
                    try:
                        content, usage = await asyncio.to_thread(call_model, [{"role": "user", "content": prompt}], cfg)
                        total["calls"] += 1
                        total["input_tokens"] += usage["input"]
                        total["output_tokens"] += usage["output"]
                        done[0] += 1
                        _prog(30 + int(done[0] / len(chunks) * 50),
                              f"模型分块分析 {done[0]}/{len(chunks)}（并发 {cfg.get('concurrency', CONCURRENCY)}）")
                        return content
                    except Exception:
                        if attempt == RETRY:
                            return "【该块分析缺失】"
                        await asyncio.sleep(2)
        return await asyncio.gather(*[one(i, t) for i, t in enumerate(chunks)])

    async def _call_and_track(prompt):
        content, usage = await asyncio.to_thread(call_model, [{"role": "user", "content": prompt}], cfg)
        total["calls"] += 1
        total["input_tokens"] += usage["input"]
        total["output_tokens"] += usage["output"]
        return content

    async def _main():
        summaries = await _map()
        text = "\n\n".join(f"### 分块{i+1}摘要\n{s}" for i, s in enumerate(summaries))
        while len(text) > max_chars:  # 二级归并
            parts = [text[i:i+max_chars] for i in range(0, len(text), max_chars)]
            _prog(85, f"中间摘要归并（{len(parts)} 组）")
            text = "\n\n".join(await asyncio.gather(*[
                _call_and_track(render_prompt(template, stats_summary, routine,
                                               f"[中间摘要 {j+1}/{len(parts)}]\n{p}", is_final=True))
                for j, p in enumerate(parts)]))
        _prog(90, "最终汇总中")
        return await _call_and_track(render_prompt(template, stats_summary, routine, text, is_final=True))

    text = asyncio.run(_main())
    return {"text": text, "usage": total}
