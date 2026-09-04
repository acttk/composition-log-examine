from core.chunker import chunk_body, est_tokens

def test_chunk_by_line_boundary():
    lines = [f"2026-09-03 13:59:{i:02d}.000 [t] INFO a.B - 消息{i}" + "x" * 90 for i in range(100)]
    body = "\n".join(lines)
    chunks = chunk_body(body, max_chars=600)
    assert len(chunks) >= 2
    # 没有行被切断：每块都是完整行的拼接
    for c in chunks:
        cl = c.split("\n")
        assert all(l in lines for l in cl)
    # 重组后行数守恒（超长行截断不在此场景触发）
    assert sum(len(c.split("\n")) for c in chunks) == 100

def test_chunk_single_huge_line():
    body = "超长行" + "y" * 500000
    chunks = chunk_body(body, max_chars=300000)
    assert len(chunks) == 1
    assert len(chunks[0]) <= 300000
    assert chunks[0].endswith("...[超长行截断]")

def test_chunk_no_split_when_small():
    body = "\n".join(f"line{i}" for i in range(10))
    assert chunk_body(body, max_chars=300000) == [body]

def test_est_tokens():
    assert est_tokens(1000) == 900
    assert est_tokens(0) == 0
