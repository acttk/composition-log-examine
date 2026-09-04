# -*- coding: utf-8 -*-
"""流式日志解析：逐行读取 + 多行记录合并"""
import re
from typing import Iterator

# 格式一：空格分隔（带毫秒）— 时间戳.fff [线程] 级别 Logger - 消息
HEAD = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \[([^\]]+)\] '
    r'(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\s+(\S+) - (.*)$'
)

# 格式二：竖线分隔（无毫秒）— 时间戳|[线程]|级别|Logger|消息
HEAD_PIPE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\|\[([^\]]*)\]\|'
    r'(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\|([^|]*)\|(.*)$'
)

def parse_file(path: str) -> Iterator[dict]:
    """流式产出记录 dict；不以日志头开头的行归属上一条记录（多行载荷/堆栈）。

    兼容两种格式的空格分隔与竖线分隔日志，时间戳统一补齐毫秒，
    使下游 collect_threadpool 的 strptime("%Y-%m-%d %H:%M:%S.%f") 正常工作。
    """
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
                continue
            m = HEAD_PIPE.match(line)
            if m:
                if current is not None:
                    yield current
                current = {"ts": m.group(1) + ".000", "thread": m.group(2),
                           "level": m.group(3), "logger": m.group(4),
                           "msg": m.group(5), "line_no": line_no}
            elif current is not None:
                current["msg"] += "\n" + line
    if current is not None:
        yield current
