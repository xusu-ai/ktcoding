#!/usr/bin/env bash
# deploy-dual.sh — 双站一键推送（Gitee 中文 README / GitHub 英文 README）
#
# 用法:
#   ./deploy-dual.sh              # 提交所有变更并推送到双站
#   ./deploy-dual.sh status       # 查看双站分支状态
#   ./deploy-dual.sh sync         # 仅同步 github 分支（不提交新变更）
#
# 假设:
#   - master 分支 → Gitee (origin)，README.md = 中文
#   - github 分支 → GitHub (github)，README.md = 英文
#   - README.en.md 本地保存纯英文版
#   - README.zh.md 本地保存纯中文版

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
GITEA_REMOTE="origin"
GITHUB_REMOTE="github"
GITHUB_BRANCH="github"

ensure_clean_tree() {
  if ! git diff --cached --quiet; then
    echo "❌ 有已暂存(staged)但未提交的变更，请先 commit 或 stash"
    exit 1
  fi
}

cmd_status() {
  echo "=== 双站分支状态 ==="
  echo ""
  echo "--- 本地分支 ---"
  git branch -v
  echo ""
  echo "--- master vs origin/master (Gitee) ---"
  git log --oneline origin/master..master 2>/dev/null || echo "同步"
  echo ""
  echo "--- github vs github/master (GitHub) ---"
  git log --oneline github/master..github 2>/dev/null || echo "同步"
  echo ""
  echo "--- github 落后 master 的 commits ---"
  git log --oneline github..master 2>/dev/null || echo "同步"
}

cmd_sync() {
  echo "【同步 github 分支 → GitHub】"

  # 切到 github 分支
  git checkout "$GITHUB_BRANCH" 2>/dev/null || git checkout -b "$GITHUB_BRANCH" github/master 2>/dev/null

  # 将 master 的代码变更 rebase 过来（但保留 github 分支的 README.md）
  git rebase master --strategy-option theirs 2>/dev/null || {
    # 如果有冲突，接受远程（ours = github 分支自身的英文 README）
    git checkout --ours README.md 2>/dev/null || true
    git add -A
    git rebase --continue 2>/dev/null || true
  }

  # 确保 README.md 是英文版(有差异时提交,否则跳过——修复:原来只 add 不 commit,
  # 推送到 GitHub 的 README.md 永远是旧英文版)
  if [ -f README.en.md ]; then
    cp README.en.md README.md
    git add README.md
    if ! git diff --cached --quiet -- README.md; then
      git commit -m "docs: sync English README from README.en.md" -- README.md
    else
      git reset -q -- README.md
    fi
  fi

  # 推送到 GitHub
  # rebase 每次会重写 github 分支历史(哈希变化), 必须 force 推送;
  # 用 --force-with-lease 而非 --force, 防止覆盖他人新推送
  git push --force-with-lease "$GITHUB_REMOTE" "$GITHUB_BRANCH:master"

  # 切回 master
  git checkout master
  echo "✅ GitHub 同步完成"
}

if [ $# -eq 0 ]; then
  # 默认操作：提交当前变更，推送到双站
  ensure_clean_tree

  # 1) 暂存所有变更
  git add -A
  if git diff --cached --quiet; then
    echo "⚠️  没有待提交的变更"
    exit 0
  fi

  # 2) 自动 commit（使用第一条 git diff 的概要）
  DIFF_SUMMARY=$(git diff --cached --stat -- ':!README.md' | tail -1)
  if [ -z "$DIFF_SUMMARY" ]; then
    echo "⚠️  只有 README 变更，没有代码变更"
    git reset HEAD
    exit 0
  fi
  git commit -m "日常更新: ${DIFF_SUMMARY}"

  LAST_COMMIT=$(git rev-parse --short HEAD)

  # 3) 推送到 Gitee（master = 中文 README）
  echo ""
  echo "【推送 Gitee（中文 README）】"
  git push "$GITEA_REMOTE" master

  # 4) 同步 github 分支并推送到 GitHub
  echo ""
  cmd_sync

  echo ""
  echo "✅ 双站推送完成"
  echo "  Gitee: https://gitee.com/xusuai/ktcoding"
  echo "  GitHub: https://github.com/xusu-ai/ktcoding"
  echo "  Commit: $LAST_COMMIT"

elif [ "$1" = "status" ]; then
  cmd_status
elif [ "$1" = "sync" ]; then
  cmd_sync
else
  echo "用法: $0 [status|sync]"
  exit 1
fi
