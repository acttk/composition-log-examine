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

def test_parse_pipe_delimited_format(tmp_path):
    """竖线分隔 + 无毫秒格式（info-2026-09-04.log）也能正确解析"""
    p = tmp_path / "pipe.log"
    p.write_text(
        "2026-09-03 00:01:23|[AsyncResolver-bootstrap-executor-0]|INFO|c.n.d.shared.resolver.aws.ConfigClusterResolver|Resolving eureka endpoints via configuration\n"
        "2026-09-03 09:12:33|[biz-pool-1]|INFO|c.y.c.grading.GradingOrchestrator|批改流水线启动, resultId:2095318995547983872, pipeline:composition-grading\n"
        "2026-09-03 09:12:33|[biz-pool-1]|INFO|c.y.c.grading.stage.AbstractGradingStage|阶段开始执行, resultId:2095318995547983872, stage:TEXTUAL_OCR\n",
        encoding="utf-8",
    )
    recs = list(parse_file(str(p)))
    assert len(recs) == 3
    r0 = recs[0]
    assert r0["ts"] == "2026-09-03 00:01:23.000"  # 无毫秒 → 补齐 .000
    assert r0["thread"] == "AsyncResolver-bootstrap-executor-0"  # 去方括号
    assert r0["level"] == "INFO"
    assert r0["logger"] == "c.n.d.shared.resolver.aws.ConfigClusterResolver"
    assert r0["msg"] == "Resolving eureka endpoints via configuration"
    assert recs[1]["logger"] == "c.y.c.grading.GradingOrchestrator"
    assert "批改流水线启动" in recs[1]["msg"]
    assert recs[2]["line_no"] == 3
