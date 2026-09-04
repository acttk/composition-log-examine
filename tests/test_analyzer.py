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
                    "usage": {"prompt_tokens": 100, "completion_tokens": 23}}
    class FakeClient:
        def post(self, url, json=None, timeout=None, headers=None):
            captured.update(url=url, json=json)
            return FakeResp()
    monkeypatch.setattr(az.httpx, "Client", lambda **kw: FakeClient())
    out, usage = az.call_model([{"role": "user", "content": "hi"}],
                               {"base_url": "https://api.x.com/v1", "api_key": "sk-1", "model": "m1"})
    assert out == "模型返回"
    assert usage == {"input": 100, "output": 23}  # 提取接口返回的真实用量
    assert captured["url"] == "https://api.x.com/v1/chat/completions"
    assert captured["json"]["model"] == "m1"

def test_run_analysis_map_reduce(monkeypatch):
    def fake_call(msgs, cfg):
        return f"摘要({len(msgs[0]['content'])})", {"input": 10, "output": 5}
    monkeypatch.setattr(az, "call_model", fake_call)
    comp = {"body": "行1\n" * 100, "routine": {}, "template_count": 1,
            "char_count": 500, "est_tokens": 450, "failure_details": ""}
    out = az.run_analysis(comp, {"风控异常": []}, {"name": "t", "concern": "c",
                          "focus": [], "outputFormat": "md", "extraRules": ""},
                          {"base_url": "u", "api_key": "k", "model": "m", "concurrency": 2})
    assert "摘要(" in out["text"]  # Map 与 Reduce 均调用了桩
    assert out["usage"]["input_tokens"] == 10 * 2   # 1 map（body 一块）+ 1 final
    assert out["usage"]["output_tokens"] == 5 * 2
    assert out["usage"]["calls"] == 2

def test_run_analysis_progress_callback(monkeypatch):
    events = []
    def fake_call(msgs, cfg):
        return "内容", {"input": 1, "output": 1}
    monkeypatch.setattr(az, "call_model", fake_call)
    comp = {"body": "行1\n" * 100, "routine": {}, "template_count": 1,
            "char_count": 500, "est_tokens": 450, "failure_details": ""}
    az.run_analysis(comp, {"风控异常": []}, {"name": "t", "concern": "c",
                    "focus": [], "outputFormat": "md", "extraRules": ""},
                    {"base_url": "u", "api_key": "k", "model": "m", "concurrency": 1},
                    on_progress=lambda pct, detail: events.append((pct, detail)))
    assert events  # 进度回调有事件
    assert any("最终汇总中" in e[1] for e in events)
