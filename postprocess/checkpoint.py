"""断点续跑 checkpoint。

每个 job 的 work_dir 下维护一个 `checkpoint.json`，记录各 LLM 步骤的已处理 item。
LLM 函数在处理每个 item 前先查 checkpoint：已存在则跳过调用，直接用缓存结果。

用法：
    from .checkpoint import Checkpoint

    ck = Checkpoint(work_dir / "checkpoint.json")
    for item in items:
        key = f"{item.page}:{item.line_index}"
        if ck.is_done(step, key):
            cached = ck.get_result(step, key)
            apply(cached)
            continue
        result = call_llm(...)
        ck.mark_done(step, key, result=result)
"""

from __future__ import annotations

import json
from pathlib import Path


class Checkpoint:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_done(self, step: str, key: str) -> bool:
        return key in self.data.get(step, {}).get("done", {})

    def get_result(self, step: str, key: str):
        return self.data.get(step, {}).get("done", {}).get(key)

    def mark_done(self, step: str, key: str, result=None):
        self.data.setdefault(step, {}).setdefault("done", {})
        self.data[step]["done"][key] = result
        self._save()

    def set_counter(self, step: str, counter_name: str, value):
        self.data.setdefault(step, {})[counter_name] = value
        self._save()

    def get_counter(self, step: str, counter_name: str, default=0):
        return self.data.get(step, {}).get(counter_name, default)

    def step_done_count(self, step: str) -> int:
        return len(self.data.get(step, {}).get("done", {}))
