# -*- coding: utf-8 -*-
"""对压缩正文按行（记录边界）切块；绝不在行中间切断"""

def est_tokens(chars: int) -> int:
    """中文密度 token 估算：字符数 × 0.9"""
    return int(chars * 0.9)

def chunk_body(body: str, max_chars: int = 300000) -> list[str]:
    """按行切块；单行超限时独立成块并尾部截断"""
    chunks, cur, size = [], [], 0
    for line in body.split("\n"):
        if len(line) > max_chars:
            if cur:
                chunks.append("\n".join(cur))
                cur, size = [], 0
            chunks.append(line[:max_chars - 14] + "...[超长行截断]")
            continue
        if cur and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks
