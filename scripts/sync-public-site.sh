#!/usr/bin/env bash
# sync-public-site.sh
#
# Whitelist-export + prune + secret-gate + commit/push pipeline:
#   private suifang-repo (source of truth) -> public suifang-public (display copy).
#
# Portable bash only (no ugrep/GNU-grep-specific flags); python3 used for the
# HTML dead-link prune and the forbidden-pattern secret gate.
#
# Usage:
#   scripts/sync-public-site.sh          # full pipeline: export, prune, gate, commit, push
#
# Can also be sourced (for testing individual functions, e.g. secret_gate):
#   source scripts/sync-public-site.sh   # defines functions only, does not run main

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
PUBLIC_REPO="$(dirname "$PRIVATE_REPO")/suifang-public"

LOG_TAG="[sync-public-site]"

# ---------------------------------------------------------------------------
# Whitelist manifest -- the ONLY paths ever written into the public repo.
# ---------------------------------------------------------------------------
# platform-preview.html = 对外汇报用的裁剪展示版 (患者中心 + 体征视图两模块)。
# 刻意不登记进根门户 index.html 的入口列表: 需要它的人拿直链, 浏览站点不会撞见。
# platform-v2.html = 「燕名医随」新平台外壳 (v2)。与 platform.html (v1) 并存, 共用同一后端,
# 两者都会实时拉真实门诊号与报警 —— 公开站上的暴露面一致, 2026-08-02 经确认后公开。
PROTOTYPE_FILES=(platform.html platform-v2.html platform-demo.html platform-preview.html data-dashboard.html pulse-dashboard.html iwown-dashboard.html patient-demo.html index.html)
DOCS_FILES=("随访平台使用说明.html" "随访平台1.0设计方案.html" "智能穿戴设备调研与数据采集对接.html" "FAQ-常见问题清单.html" "医院交付使用指南.html" index.html)

# ---------------------------------------------------------------------------
# check_repos -- refuse to run if either repo path is missing
# ---------------------------------------------------------------------------
check_repos() {
  if [ ! -d "$PRIVATE_REPO/.git" ]; then
    echo "$LOG_TAG ERROR: private repo not found at $PRIVATE_REPO" >&2
    exit 1
  fi
  if [ ! -d "$PUBLIC_REPO" ]; then
    echo "$LOG_TAG ERROR: public repo not found at $PUBLIC_REPO" >&2
    exit 1
  fi
  if [ ! -d "$PUBLIC_REPO/.git" ]; then
    echo "$LOG_TAG ERROR: $PUBLIC_REPO is not a git repo" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# sync_dir REL_DIR FILE...
# rsync --delete style semantics for one managed directory, done with plain
# cp/rm so behaviour is deterministic and needs no rsync dependency.
# ---------------------------------------------------------------------------
sync_dir() {
  local rel_dir="$1"; shift
  local files=("$@")
  local src_dir="$PRIVATE_REPO/$rel_dir"
  local dst_dir="$PUBLIC_REPO/$rel_dir"

  mkdir -p "$dst_dir"

  # delete anything currently in dst that is not on the whitelist
  local existing base keep f
  while IFS= read -r -d '' existing; do
    base="$(basename "$existing")"
    keep=0
    for f in "${files[@]}"; do
      if [ "$f" = "$base" ]; then
        keep=1
        break
      fi
    done
    if [ "$keep" -eq 0 ]; then
      rm -f -- "$existing"
    fi
  done < <(find "$dst_dir" -maxdepth 1 -type f -print0)

  # copy whitelisted files fresh from the private working tree
  for f in "${files[@]}"; do
    if [ ! -f "$src_dir/$f" ]; then
      echo "$LOG_TAG ERROR: expected source file missing: $src_dir/$f" >&2
      exit 1
    fi
    cp -f -- "$src_dir/$f" "$dst_dir/$f"
  done
}

export_whitelist() {
  cp -f -- "$PRIVATE_REPO/index.html" "$PUBLIC_REPO/index.html"
  sync_dir prototype "${PROTOTYPE_FILES[@]}"
  sync_dir docs "${DOCS_FILES[@]}"
}

# ---------------------------------------------------------------------------
# prune_docs_index -- drop dead sidebar doc-item / welcome quick-card links
# from the public copy's docs/index.html, then drop any group left empty.
# Idempotent: a second run over already-pruned content changes nothing.
# ---------------------------------------------------------------------------
prune_docs_index() {
  python3 - "$PUBLIC_REPO/docs" <<'PYEOF'
import os, re, sys

docs_dir = sys.argv[1]
index_path = os.path.join(docs_dir, 'index.html')
if not os.path.isfile(index_path):
    sys.exit(0)

present = {
    f for f in os.listdir(docs_dir)
    if f != 'index.html' and os.path.isfile(os.path.join(docs_dir, f))
}

with open(index_path, 'r', encoding='utf-8') as fh:
    text = fh.read()

# Match one whole sidebar doc-item or welcome quick-card anchor element.
# Neither contains a nested <a>, so a non-greedy DOTALL match is safe.
ANCHOR_RE = re.compile(r'[ \t]*<a\s+class="(?:doc-item|quick-card)"[^>]*>.*?</a>\n?', re.DOTALL)


def anchor_repl(m):
    tag = m.group(0)
    open_tag = tag[:tag.index('>') + 1]
    dd = re.search(r'data-doc="([^"]*)"', open_tag)
    if dd:
        return tag if dd.group(1) in present else ''
    href = re.search(r'href="([^"]*)"', open_tag)
    if href:
        target = href.group(1)
        if target and not target.startswith(('#', 'http://', 'https://', 'javascript:')):
            if not os.path.isfile(os.path.join(docs_dir, target)):
                return ''
    return tag


text = ANCHOR_RE.sub(anchor_repl, text)


def find_balanced(text, class_name):
    """Return [(start, end)] spans for each top-level <div class="class_name">...</div>."""
    start_re = re.compile(r'<div class="%s">' % re.escape(class_name))
    open_re = re.compile(r'<div\b')
    close_re = re.compile(r'</div>')
    spans = []
    for m in start_re.finditer(text):
        start = m.start()
        pos = m.end()
        depth = 1
        while depth > 0:
            no = open_re.search(text, pos)
            nc = close_re.search(text, pos)
            if nc is None:
                raise RuntimeError('unbalanced <div> while scanning group block')
            if no and no.start() < nc.start():
                depth += 1
                pos = no.end()
            else:
                depth -= 1
                pos = nc.end()
        spans.append((start, pos))
    return spans


for start, end in reversed(find_balanced(text, 'group')):
    if 'class="doc-item"' not in text[start:end]:
        new_end = end + 1 if text[end:end + 1] == '\n' else end
        text = text[:start] + text[new_end:]

# tidy up: collapse any run of 3+ blank lines left behind by a dropped group
text = re.sub(r'\n{3,}', '\n\n', text)

with open(index_path, 'w', encoding='utf-8') as fh:
    fh.write(text)

print('[sync-public-site] prune: kept %d doc(s): %s' % (len(present), ', '.join(sorted(present))))
PYEOF
}

# ---------------------------------------------------------------------------
# secret_gate TARGET_DIR -- fail-closed forbidden-pattern scan.
# Prints every offending file and returns non-zero on any hit.
# Callable standalone (e.g. against a scratch dir) for testing.
# ---------------------------------------------------------------------------
secret_gate() {
  local target="$1"
  python3 - "$target" <<'PYEOF'
import os, re, sys

root = sys.argv[1]
SKIP_DIRS = {'.git'}

patterns = [
    ('DePer', re.compile(re.escape('DePer'))),
    ('8ik,', re.compile(re.escape('8ik,'))),
    ('health123', re.compile(re.escape('health123'))),
    ('devtok', re.compile(re.escape('devtok'))),
    ('APPSECRET=<value>', re.compile(r'APPSECRET=\S+')),
    ('PLATFORM_TOKEN=<value>', re.compile(r'PLATFORM_TOKEN=\S+')),
    ('ssh-private-key-header', re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----')),
]

hits = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for name in filenames:
        path = os.path.join(dirpath, name)
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                data = fh.read()
        except OSError:
            continue
        for label, rx in patterns:
            m = rx.search(data)
            if m:
                hits.append((path, label, m.group(0)))

if hits:
    for path, label, snippet in hits:
        print('SECRET-GATE HIT: %s :: %s :: %r' % (path, label, snippet))
    sys.exit(1)

print('[sync-public-site] secret-gate: clean')
sys.exit(0)
PYEOF
}

# ---------------------------------------------------------------------------
# write_meta -- refresh README.md + .nojekyll in the public copy
# ---------------------------------------------------------------------------
write_meta() {
  cat > "$PUBLIC_REPO/README.md" <<'EOF'
# 智能随访 · 对外展示站点

对外展示站点 · 源仓库私有 · 自动同步，勿直接改本仓。

内容由 `suifang-core` 私有仓库的 `scripts/sync-public-site.sh` 按白名单自动导出，
本仓库任何直接改动都会在下一次同步时被覆盖。
EOF
  touch "$PUBLIC_REPO/.nojekyll"
}

# ---------------------------------------------------------------------------
# commit_and_push -- quiet no-op when nothing changed
# ---------------------------------------------------------------------------
commit_and_push() {
  (
    cd "$PUBLIC_REPO"
    git add -A
    if git diff --cached --quiet; then
      echo "$LOG_TAG no-op: nothing changed"
      exit 0
    fi
    hash="$(git -C "$PRIVATE_REPO" rev-parse --short HEAD)"
    subject="$(git -C "$PRIVATE_REPO" log -1 --format=%s)"
    git commit -m "sync: ${hash} ${subject}"
    git push origin main
    echo "$LOG_TAG pushed sync commit for ${hash}"
  )
}

main() {
  check_repos
  export_whitelist
  prune_docs_index
  if ! secret_gate "$PUBLIC_REPO"; then
    echo "$LOG_TAG ABORT: secret gate found forbidden pattern(s) above; not committing." >&2
    exit 1
  fi
  write_meta
  commit_and_push
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
