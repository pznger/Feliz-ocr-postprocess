#!/usr/bin/env python3
"""修复 05-final.md 中导致 wiki 缺章的常见标题 OCR 问题（保守策略）。"""

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

H1 = re.compile(r"^(#(?!#)\s*)(.+?)\s*$")
H2 = re.compile(r"^##(?!#)\s*(.+?)\s*$")
PLAIN_CH = re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff].+)$")
TOC_LINE = re.compile(r"[…\.]{2,}\s*\(?\d+\)?\s*$")
GLUED_CH2 = re.compile(
    r"^(\d+)\s+术语(?:和符号|、符号)?\s*2\.1\s*术\s*语\s*2\.1\.1\s*(.+)$"
)
SPLIT_CH = re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff])$")

SKIP_SPECS = {
    "10-《停车场规划设计规则(试行)》（公安部建设部〔88〕公(交管)字90号)",
    "GB 50099-2011《中小学校设计规范》GB 50099",
    "GB 50153-2008 工程结构可靠性设计统一标准",
    "碳素结构钢和低合金结构钢热轧厚钢板和钢带",
    "预应力混凝土结构抗震设计标准",
}


def parsed_chapter_nums(lines: list[str]) -> list[int]:
    try:
        chapters = sw.parse_structure(lines)
    except ValueError:
        return []
    return sorted(n for n in (sw.chapter_number(c.title) for c in chapters) if n)


def stuck_after_chapter_one(lines: list[str]) -> bool:
    nums = parsed_chapter_nums(lines)
    return nums == [1]


def is_known_fake_h1(title: str) -> bool:
    if title.endswith(("。", "；")):
        return True
    if "应按" in title and "表" in title:
        return True
    if re.match(r"^8\s+度抗震", title):
        return True
    if TOC_LINE.search(title):
        return True
    return False


def fix_lines(lines: list[str], *, stuck_at_one: bool) -> list[str]:
    out = list(lines)

    # 粘连的「2 术语和符号2.1 术语2.1.1 …」
    for i, line in enumerate(out):
        gm = GLUED_CH2.match(line.strip())
        if gm and not line.lstrip().startswith("#"):
            rest = gm.group(2).strip()
            out[i : i + 1] = ["# 2 术语和符号", "", "## 2.1 术语", "", f"2.1.1 {rest}"]
            break

    i = 0
    while i < len(out):
        line = out[i]

        # 「3 材」+「料」
        sm = SPLIT_CH.match(line.strip())
        if sm and not line.lstrip().startswith("#"):
            n = sm.group(1)
            if i + 2 < len(out):
                nxt = out[i + 1].strip()
                nxt2 = out[i + 2].strip()
                h2 = H2.match(nxt2)
                if h2 and h2.group(1).startswith(f"{n}."):
                    out[i : i + 2] = [f"# {n} {sm.group(2)}{nxt}", ""]
                    i += 2
                    continue

        hm = H1.match(line)
        if hm:
            title = sw.normalize_title(hm.group(2))
            if is_known_fake_h1(title):
                out[i] = title
                i += 1
                continue
            if title.startswith("1 总则") and title != "1 总则":
                out[i] = "# 1 总则"
                i += 1
                continue

        # 仅当解析卡在第一章后，补 `# 2 术语和符号`
        if stuck_at_one and not line.lstrip().startswith("#"):
            pm = PLAIN_CH.match(line.strip())
            if pm and int(pm.group(1)) == 2 and "术语" in pm.group(2) and not TOC_LINE.search(pm.group(2)):
                for j in range(i + 1, min(i + 8, len(out))):
                    cand = out[j].strip()
                    if re.match(r"^\*\*2\.", cand) or re.match(r"^##\s*2\.", cand):
                        out[i] = "# 2 术语和符号"
                        break

        # 缺 # 的章标题：下一行出现 ## N.1
        if not line.lstrip().startswith("#"):
            pm = PLAIN_CH.match(line.strip())
            if pm:
                n = int(pm.group(1))
                rest = pm.group(2).strip()
                if TOC_LINE.search(rest):
                    i += 1
                    continue
                if any(x in rest for x in ("应按", "不应", "。", "，", "；", "当", "按下式", "步骤")):
                    i += 1
                    continue
                if len(rest) > 28:
                    i += 1
                    continue
                for j in range(i + 1, min(i + 8, len(out))):
                    cand = out[j].strip()
                    if not cand:
                        continue
                    h2 = H2.match(cand)
                    if h2 and h2.group(1).startswith(f"{n}."):
                        out[i] = f"# {n} {rest}"
                        break
                    if re.match(rf"^\*\*{n}\.", cand):
                        out[i] = f"# {n} {rest}"
                        break
                    break
        i += 1

    return out


def audit_missing_chapters(lines: list[str]) -> list[int]:
    try:
        start = sw.first_content_index(lines)
        chapters = sw.parse_structure(lines)
    except ValueError:
        return []
    parsed = {sw.chapter_number(c.title) for c in chapters if sw.chapter_number(c.title)}
    doc_nums: set[int] = set()
    in_tail = False
    for line in lines[start:]:
        hm = H1.match(line)
        if not hm:
            continue
        title = sw.normalize_title(hm.group(2))
        if sw.TAIL_HEADING_RE.match(title.replace(" ", "")):
            in_tail = True
        if in_tail:
            continue
        n = sw.chapter_number(title)
        if n and sw.is_chapter_title(title) and not is_known_fake_h1(title):
            doc_nums.add(n)
    if not doc_nums:
        return []
    mx = max(max(parsed or {0}), max(doc_nums))
    return sorted(n for n in range(1, mx + 1) if n in doc_nums and n not in parsed)


def process_file(md_path: Path, *, dry_run: bool = False) -> tuple[bool, str]:
    name = md_path.parent.name
    if name in SKIP_SPECS:
        return False, "skip list"

    original = md_path.read_text(encoding="utf-8").splitlines()
    before = audit_missing_chapters(original)
    stuck_at_one = stuck_after_chapter_one(original)
    fixed = fix_lines(original, stuck_at_one=stuck_at_one)
    after = audit_missing_chapters(fixed)

    if fixed == original:
        return False, f"无改动 缺章={before}"

    if after and len(after) >= len(before):
        return False, f"未改善 {before}->{after}"

    if not dry_run:
        text = "\n".join(fixed) + "\n"
        md_path.write_text(text, encoding="utf-8")
        kb = REPO / "知识库以及文档" / f"{name}.md"
        if kb.exists():
            kb.write_text(text, encoding="utf-8")
    return True, f"缺章 {before} -> {after}"


def resplit(md_path: Path) -> None:
    wiki_dir = md_path.parent / "wiki"
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "split_wiki.py"),
        str(md_path),
        "--output-root",
        str(wiki_dir.parent),
        "--spec-name",
        ".",
    ]
    subprocess.run(cmd, check=True, cwd=str(REPO))


def sync_kb(spec_name: str) -> None:
    src_md = REPO / "output" / spec_name / "05-final.md"
    src_wiki = REPO / "output" / spec_name / "wiki"
    kb_root = REPO / "知识库以及文档"
    shutil.copy2(src_md, kb_root / f"{spec_name}.md")
    dest_wiki = kb_root / "wiki" / spec_name
    if dest_wiki.exists():
        shutil.rmtree(dest_wiki)
    if src_wiki.is_dir():
        shutil.copytree(src_wiki, dest_wiki)


def main() -> None:
    p = argparse.ArgumentParser(description="Conservatively fix chapter headings")
    p.add_argument("--output-root", type=Path, default=REPO / "output")
    p.add_argument("--resplit", action="store_true")
    p.add_argument("--sync-kb", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    changed: list[str] = []
    failed: list[tuple[str, str]] = []

    for spec_dir in sorted(args.output_root.iterdir()):
        if not spec_dir.is_dir():
            continue
        md = spec_dir / "05-final.md"
        if not md.exists():
            continue
        did, msg = process_file(md, dry_run=args.dry_run)
        if not did:
            continue
        print(f"[fix] {spec_dir.name}: {msg}")
        if args.dry_run:
            changed.append(spec_dir.name)
            continue
        try:
            resplit(md)
            print("  [split] ok")
            if args.sync_kb:
                sync_kb(spec_dir.name)
            changed.append(spec_dir.name)
        except Exception as exc:
            failed.append((spec_dir.name, str(exc)))
            print(f"  [split] FAIL: {exc}")

    print("\n" + "=" * 60)
    print(f"已修复并重拆: {len(changed)}  拆分失败: {len(failed)}")
    for name in changed:
        print(f"  ✓ {name}")
    for name, err in failed:
        print(f"  ✗ {name}: {err[:100]}")


if __name__ == "__main__":
    main()
