#!/usr/bin/env python3
"""安全修复缺章并重拆 wiki。"""

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

H1 = re.compile(r"^#(?!#)\s+(.+?)\s*$")
H2 = re.compile(r"^##(?!#)\s*(.+?)\s*$")
PLAIN = re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff].+)$")
TOC = re.compile(r"[…\.]{2,}\s*\(?\d+\)?\s*$")
GLUED = re.compile(r"^2 术语和符号2\.1 术 语2\.1\.1\s*(.+)$")


def can_parse(lines: list[str]) -> bool:
    try:
        sw.first_content_index(lines)
        return True
    except ValueError:
        return False


def missing_chapters(lines: list[str]) -> list[int]:
    try:
        start = sw.first_content_index(lines)
        chapters = sw.parse_structure(lines)
    except ValueError:
        return [-1]
    parsed = {sw.chapter_number(c.title) for c in chapters if sw.chapter_number(c.title)}
    doc: set[int] = set()
    in_tail = False
    for line in lines[start:]:
        m = H1.match(line)
        if not m:
            continue
        t = sw.normalize_title(m.group(1))
        if sw.TAIL_HEADING_RE.match(t.replace(" ", "")):
            in_tail = True
        if in_tail or TOC.search(t):
            continue
        if t.endswith("。") or ("应按" in t and "表" in t):
            continue
        n = sw.chapter_number(t)
        if n and sw.is_chapter_title(t):
            doc.add(n)
    if not doc:
        return []
    mx = max(max(parsed or {0}), max(doc))
    return sorted(n for n in range(1, mx + 1) if n in doc and n not in parsed)


def apply_fixes(lines: list[str]) -> list[str]:
    out = list(lines)

    # 假 #1 条款
    for i, line in enumerate(out[:30]):
        m = H1.match(line)
        if m:
            t = sw.normalize_title(m.group(1))
            if sw.chapter_number(t) == 1 and "总则" not in t and t.endswith(("；", "。")):
                out[i] = t

    # 目次区 H1（前 120 行内带省略号/页码）
    for i, line in enumerate(out[:120]):
        m = H1.match(line)
        if m:
            t = sw.normalize_title(m.group(1))
            if TOC.search(t):
                out[i] = t

    # 假 #8 抗震 / #8 毛石
    for i, line in enumerate(out):
        m = H1.match(line)
        if not m:
            continue
        t = sw.normalize_title(m.group(1))
        if re.match(r"^8 度抗震", t) or ("毛石砌体" in t and "应按表" in t):
            out[i] = t

    # 粘连第 2 章
    for i, line in enumerate(out):
        gm = GLUED.match(line.strip())
        if gm:
            out[i : i + 1] = ["# 2 术语和符号", "", "## 2.1 术语", "", f"2.1.1 {gm.group(1).strip()}"]

    # 3 材 + 料
    for i in range(len(out) - 2):
        if out[i].strip() == "3 材" and out[i + 1].strip() == "料":
            h2 = H2.match(out[i + 2].strip())
            if h2 and h2.group(1).startswith("3."):
                out[i : i + 2] = ["# 3 材料", ""]

    # # 1 总则1
    for i, line in enumerate(out):
        if line.strip() == "# 1 总则1":
            out[i] = "# 1 总则"

    # 补 #2 术语
    for i, line in enumerate(out):
        if line.lstrip().startswith("#"):
            continue
        pm = PLAIN.match(line.strip())
        if not pm or int(pm.group(1)) != 2 or "术语" not in pm.group(2) or TOC.search(pm.group(2)):
            continue
        for j in range(i + 1, min(i + 8, len(out))):
            c = out[j].strip()
            if re.match(r"^\*\*2\.", c) or re.match(r"^##\s*2\.", c):
                out[i] = "# 2 术语和符号"
                break

    # 补 #N（plain 行 + 后续 ## N.1）
    for i, line in enumerate(out):
        if line.lstrip().startswith("#"):
            continue
        pm = PLAIN.match(line.strip())
        if not pm:
            continue
        n = int(pm.group(1))
        rest = pm.group(2).strip()
        if TOC.search(rest) or len(rest) > 28:
            continue
        if any(x in rest for x in ("应按", "不应", "。", "，", "；", "步骤", "按下式")):
            continue
        for j in range(i + 1, min(i + 6, len(out))):
            c = out[j].strip()
            if not c:
                continue
            h2 = H2.match(c)
            if h2 and h2.group(1).startswith(f"{n}."):
                out[i] = f"# {n} {rest}"
            break

    return out


def resplit(md: Path) -> None:
    wiki = md.parent / "wiki"
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "split_wiki.py"),
        str(md),
        "--output-root",
        str(wiki),
        "--spec-name",
        ".",
    ]
    subprocess.run(cmd, check=True, cwd=str(REPO))


def sync_kb(name: str) -> None:
    src = REPO / "output" / name / "05-final.md"
    wiki = REPO / "output" / name / "wiki"
    kb = REPO / "知识库以及文档"
    shutil.copy2(src, kb / f"{name}.md")
    dest = kb / "wiki" / name
    if dest.exists():
        shutil.rmtree(dest)
    if wiki.is_dir():
        shutil.copytree(wiki, dest)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-root", type=Path, default=REPO / "output")
    p.add_argument("--sync-kb", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    skip = {
        "10-《停车场规划设计规则(试行)》（公安部建设部〔88〕公(交管)字90号)",
        "GB 50099-2011《中小学校设计规范》GB 50099",
        "碳素结构钢和低合金结构钢热轧厚钢板和钢带",
        "预应力混凝土结构抗震设计标准",
    }

    ok: list[str] = []
    skip_list: list[str] = []

    for d in sorted(args.output_root.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        md = d / "05-final.md"
        if not md.exists() or name in skip:
            continue
        orig = md.read_text(encoding="utf-8").splitlines()
        if not can_parse(orig):
            skip_list.append(name)
            continue
        before = missing_chapters(orig)
        if not before:
            continue
        fixed = apply_fixes(orig)
        if not can_parse(fixed):
            skip_list.append(f"{name} (fix breaks parse)")
            continue
        after = missing_chapters(fixed)
        if len(after) >= len(before):
            skip_list.append(f"{name} (no improve {before}->{after})")
            continue
        if args.dry_run:
            print(f"[would fix] {name}: {before} -> {after}")
            ok.append(name)
            continue
        md.write_text("\n".join(fixed) + "\n", encoding="utf-8")
        try:
            resplit(md)
            if args.sync_kb:
                sync_kb(name)
            print(f"[ok] {name}: {before} -> {after}")
            ok.append(name)
        except Exception as exc:
            md.write_text("\n".join(orig) + "\n", encoding="utf-8")
            skip_list.append(f"{name}: {exc}")

    print("=" * 60)
    print(f"成功: {len(ok)}  跳过: {len(skip_list)}")
    for s in skip_list:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
