#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path


# 这个脚本按“文件物理行数”做粗略对比，不追求逐行完全一致。
# 在当前 wiki 拆分规则下，拆分后非 index 文件的总行数通常会比源文件少，
# 这是正常现象，常见原因包括：
# 1. 页码标记（如 <!-- 7 -->）会在拆分正文时被移除；
# 2. 个别噪声行会被清理，例如 OCR 残留的页眉/页脚短词；
# 3. 连续空行会被压缩；
# 4. 统计时排除了所有 -index.md 文件。
#
# 经验上，如果差值只是小幅到中等幅度减少，通常可以先视为正常清洗结果。
# 以规范类文档为例，拆分后总行数比源文件少约 5% 到 20% 都可能合理。
# 如果减少幅度明显超过这个范围，或出现反向大幅增加，则应进一步检查：
# 1. 是否误删了正文条款；
# 2. 是否错误跳过了某些章节；
# 3. 是否因为分页重叠、标题补回等机制导致重复过多。

def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def find_output_root(source: Path, output_root: Path, spec_name: str | None = None) -> Path:
    spec_root = output_root / (spec_name or source.stem)
    if not spec_root.exists():
        raise FileNotFoundError(f"未找到拆分输出目录: {spec_root}")
    return spec_root


def iter_non_index_files(spec_root: Path) -> list[Path]:
    return sorted(
        path
        for path in spec_root.rglob("*.md")
        if not path.name.endswith("-index.md")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare source line count with split non-index markdown files.")
    parser.add_argument("source", help="source markdown file")
    parser.add_argument("--output-root", default="wiki", help="wiki output root directory")
    parser.add_argument("--spec-name", default=None, help="wiki directory name (defaults to source file stem)")
    parser.add_argument("--details", action="store_true", help="show each non-index file line count")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    output_root = Path(args.output_root).resolve()
    spec_root = find_output_root(source, output_root, args.spec_name)

    source_lines = count_lines(source)
    body_files = iter_non_index_files(spec_root)
    split_total_lines = sum(count_lines(path) for path in body_files)
    diff = split_total_lines - source_lines
    ratio = (split_total_lines / source_lines) if source_lines else 0

    print(f"源文件: {source}")
    print(f"拆分目录: {spec_root}")
    print(f"源文件总行数: {source_lines}")
    print(f"拆分后非index文件数: {len(body_files)}")
    print(f"拆分后非index文件总行数: {split_total_lines}")
    print(f"行数差值(拆分后-源文件): {diff}")
    print(f"行数比值(拆分后/源文件): {ratio:.4f}")

    if args.details:
        print("")
        print("各文件行数:")
        for path in body_files:
            print(f"{path.relative_to(spec_root)}\t{count_lines(path)}")


if __name__ == "__main__":
    main()
