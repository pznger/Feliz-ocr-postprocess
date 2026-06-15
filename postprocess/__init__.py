"""OCR 后处理工具包。

按阶段拆成独立模块：
  strip_frontmatter  剥离前言/目录/版权等正文之前的页
  normalize_headings 把标题层级统一到只用 # / ## 两级
  text_normalize     OCR 数值/单位/空格/区间归一化与行合并
  formula_verify     可选：调多模态 LLM 复核公式 LaTeX
  table_verify       可选：调多模态 LLM 复核表格 HTML
  splitter           调用仓库已有 scripts/split_wiki.py 生成 wiki/
  pipeline           将上述阶段串成一个可重复运行的流水线
"""
