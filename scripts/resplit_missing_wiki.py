#!/usr/bin/env python3
"""对比 parse_structure 与 wiki 目录，重拆缺失章并同步知识库。"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import split_wiki as sw  # noqa: E402

TOC = re.compile(r"[…\.]{2,}\s*\(?\d+\)?\s*$")
H1 = re.compile(r"^#(?!#)\s+(.+?)\s*$")


def chapter_dir_names(lines: list[str]) -> list[str]:
    try:
        chapters = sw.parse_structure(lines)
    except ValueError:
        return []
    names: list[str] = []
    for ch in chapters:
        if sw.is_appendix_title(ch.title):
            names.append(sw.slug_title(ch.title))
        elif sw.chapter_number(ch.title):
            names.append(sw.slug_title(ch.title))
    return names


def wiki_dir_names(wiki: Path) -> set[str]:
    if not wiki.is_dir():
        return set()
    return {p.name for p in wiki.iterdir() if p.is_dir()}


def resplit(md: Path) -> None:
    wiki = md.parent / "wiki"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "split_wiki.py"),
            str(md),
            "--output-root",
            str(wiki),
            "--spec-name",
            ".",
        ],
        check=True,
        cwd=str(REPO),
    )


def sync_kb(name: str) -> None:
    kb = REPO / "知识库以及文档"
    shutil.copy2(REPO / "output" / name / "05-final.md", kb / f"{name}.md")
    dest = kb / "wiki" / name
    src = REPO / "output" / name / "wiki"
    if dest.exists():
        shutil.rmtree(dest)
    if src.is_dir():
        shutil.copytree(src, dest)


def apply_targeted_fixes(name: str, lines: list[str]) -> list[str]:
    out = list(lines)

    if name == "《自动喷水灭火系统设计规范》GB 50084-2017":
        for i, line in enumerate(out):
            if line.strip() == "**2 术语和符号**":
                out[i] = "# 2 术语和符号"
            elif line.strip() == "**2.1 术语**":
                out[i] = "## 2.1 术语"

    if name == "组合结构设计规范":
        for i, line in enumerate(out):
            if line.strip() == "# 1 总则1":
                out[i] = "# 1 总则"
        for i in range(len(out) - 2):
            if out[i].strip() == "3 材" and out[i + 1].strip() == "料":
                h2 = re.match(r"^##\s*3\.", out[i + 2].strip())
                if h2:
                    out[i : i + 2] = ["# 3 材料", ""]

    # 目次假章（前 120 行）
    for i in range(min(120, len(out))):
        m = H1.match(out[i])
        if m and TOC.search(m.group(1)):
            out[i] = sw.normalize_title(m.group(1))

    if name == "GB50067-2014汽车库、修车库、停车场设计防火规范":
        if out and out[0].startswith("# 1 应组合"):
            out[0] = "1 应组合建造在上述建筑的地下；"

    if name == "GB 50153-2008 工程结构可靠性设计统一标准":
        for i, line in enumerate(out[:200]):
            m = H1.match(line)
            if not m:
                continue
            t = sw.normalize_title(m.group(1))
            if t.endswith(("；", "。")) or t == "2 正常使用极限状态" or t == "3 准永久组合":
                out[i] = t

    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-root", type=Path, default=REPO / "output")
    p.add_argument("--sync-kb", action="store_true")
    p.add_argument("--fix-only", action="store_true")
    args = p.parse_args()

    resplit_list: list[str] = []
    fixed_list: list[str] = []

    for d in sorted(args.output_root.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        md = d / "05-final.md"
        if not md.exists():
            continue
        orig = md.read_text(encoding="utf-8").splitlines()
        fixed = apply_targeted_fixes(name, orig)
        if fixed != orig:
            md.write_text("\n".join(fixed) + "\n", encoding="utf-8")
            fixed_list.append(name)
            lines = fixed
        else:
            lines = orig

        try:
            sw.first_content_index(lines)
        except ValueError:
            continue

        expected = set(chapter_dir_names(lines))
        actual = wiki_dir_names(d / "wiki")
        if expected - actual:
            if not args.fix_only:
                resplit(md)
            resplit_list.append(f"{name}: missing wiki {sorted(expected - actual)[:5]}")

        if args.sync_kb and not args.fix_only:
            sync_kb(name)

    print("标题修复:", len(fixed_list))
    for n in fixed_list:
        print(" ", n)
    print("重拆 wiki:", len(resplit_list))
    for n in resplit_list:
        print(" ", n)


if __name__ == "__main__":
    main()
