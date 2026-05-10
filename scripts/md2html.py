#!/usr/bin/env python3
"""把 docs/*.md 全部转成 docs/*.html.

设计目标:
- 单文件 self-contained (CSS inline), GitHub Pages 上无外部依赖
- 中文字体 / 医疗专业风格 / 移动端响应式
- 表格 + 代码 + 标题层级清晰
"""
import subprocess
from pathlib import Path

DOCS = Path(__file__).parent.parent / 'docs'

CSS = r"""
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue",
                 "Microsoft YaHei", "Source Han Sans CN", sans-serif;
    color: #1f2937;
    background: #f5f7fa;
    margin: 0;
    padding: 24px 16px 64px;
    font-size: 16px;
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
}
.container {
    max-width: 820px;
    margin: 0 auto;
    background: #fff;
    border-radius: 16px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
    padding: 40px 48px 64px;
}
@media (max-width: 640px) {
    body { padding: 12px 8px 48px; font-size: 15px; }
    .container { padding: 24px 20px 40px; border-radius: 12px; }
}
h1, h2, h3, h4, h5, h6 {
    color: #1f2937;
    font-weight: 600;
    margin-top: 1.6em;
    margin-bottom: 0.6em;
    line-height: 1.3;
}
h1 {
    font-size: 28px;
    margin-top: 0;
    padding-bottom: 12px;
    border-bottom: 2px solid #2c6ef2;
    color: #2c6ef2;
    letter-spacing: 1px;
}
h2 {
    font-size: 22px;
    border-left: 4px solid #2c6ef2;
    padding-left: 12px;
    background: linear-gradient(90deg, rgba(44,110,242,0.06) 0%, transparent 50%);
    padding-top: 6px; padding-bottom: 6px;
}
h3 { font-size: 18px; color: #374151; }
h4 { font-size: 16px; color: #4b5563; }
p { margin: 1em 0; color: #374151; }
strong { color: #1f2937; font-weight: 600; }
code {
    font-family: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
    font-size: 0.9em;
    background: #f0f2f5;
    color: #d04848;
    padding: 2px 6px;
    border-radius: 4px;
}
pre {
    background: #1f2937;
    color: #e5e7eb;
    padding: 16px 20px;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.5;
    margin: 1em 0;
}
pre code {
    background: transparent;
    color: inherit;
    padding: 0;
    font-size: inherit;
}
blockquote {
    border-left: 4px solid #ff9800;
    background: rgba(255, 152, 0, 0.06);
    padding: 12px 18px;
    margin: 1em 0;
    color: #4b5563;
    border-radius: 0 8px 8px 0;
}
blockquote p:first-child { margin-top: 0; }
blockquote p:last-child { margin-bottom: 0; }
ul, ol { padding-left: 2em; margin: 1em 0; }
li { margin: 0.4em 0; }
li > p { margin: 0.4em 0; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1.2em 0;
    font-size: 14px;
    background: #fff;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
    display: block;
    overflow-x: auto;
    white-space: nowrap;
}
@media (min-width: 720px) { table { display: table; white-space: normal; } }
thead { background: linear-gradient(135deg, #2c6ef2, #4a86ff); color: #fff; }
th, td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid #f0f2f5;
}
th { font-weight: 600; letter-spacing: 1px; }
tbody tr:nth-child(odd) { background: #fafbfd; }
tbody tr:hover { background: #f0f5ff; }
a { color: #2c6ef2; text-decoration: none; border-bottom: 1px solid transparent; transition: border-color 0.2s; }
a:hover { border-bottom-color: #2c6ef2; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 2em 0; }
img { max-width: 100%; height: auto; border-radius: 8px; }
.toc {
    background: #f0f5ff;
    border-radius: 8px;
    padding: 16px 20px;
    margin: 1em 0 2em;
    font-size: 14px;
    border-left: 4px solid #2c6ef2;
}
.toc ul { padding-left: 1.4em; margin: 0.4em 0; }
.footer-meta {
    margin-top: 64px;
    padding-top: 16px;
    border-top: 1px solid #e5e7eb;
    color: #9ca3af;
    font-size: 12px;
    text-align: center;
}
.footer-meta a { color: #6b7280; }
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=2.0">
<title>{title} · 智能随访</title>
<style>{css}</style>
</head>
<body>
<div class="container">
{body}
<div class="footer-meta">
智能随访 · 文档自 docs/{src}.md 自动生成 ·
<a href="https://github.com/qqyjx/suifang">GitHub 仓库</a>
</div>
</div>
</body>
</html>
"""


def convert(md_path: Path):
    """用 pandoc 转 markdown -> HTML body, 再嵌入模板."""
    src_name = md_path.stem
    out_path = md_path.with_suffix('.html')

    # pandoc 输出 fragment (无 head/body 包装)
    result = subprocess.run(
        ['pandoc', '-f', 'gfm', '-t', 'html', str(md_path)],
        check=True, capture_output=True, text=True
    )
    body_html = result.stdout

    # 标题: 取第一个 # 作为页面标题
    title = src_name
    for line in md_path.read_text(encoding='utf-8').splitlines():
        if line.startswith('# '):
            title = line.lstrip('# ').strip()
            break

    # md 链接里的 .md 自动改成 .html (站内链接平滑迁移)
    body_html = body_html.replace('.md"', '.html"').replace(".md'", ".html'")

    page = TEMPLATE.format(title=title, css=CSS, body=body_html, src=src_name)
    out_path.write_text(page, encoding='utf-8')
    return out_path, len(page)


def main():
    md_files = sorted(DOCS.glob('*.md'))
    print(f'发现 {len(md_files)} 个 .md 文件\n')
    for md in md_files:
        try:
            out, size = convert(md)
            print(f'  ✓ {md.name:40s} -> {out.name:42s} ({size:,} bytes)')
        except subprocess.CalledProcessError as e:
            print(f'  ✗ {md.name}: pandoc 失败 {e.stderr[:100]}')


if __name__ == '__main__':
    main()
