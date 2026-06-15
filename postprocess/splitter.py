"""调用仓库已有的 scripts/split_wiki.py 完成章节拆分。

我们不重新实现，只是用 subprocess 调用它，保证生成的 wiki 结构和现有规则一致。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def split_to_wiki(
    md_path: Path,
    *,
    wiki_root: Path,
    repo_root: Path,
    spec_name: str | None = None,
) -> None:
    script = repo_root / "scripts" / "split_wiki.py"
    if not script.exists():
        raise FileNotFoundError(f"未找到拆分脚本：{script}")
    cmd = [
        sys.executable,
        str(script),
        str(md_path),
        "--output-root",
        str(wiki_root),
        "--spec-name", ".",
    ]
    subprocess.run(cmd, check=True, cwd=str(repo_root))
