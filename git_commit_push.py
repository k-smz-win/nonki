"""
nonki フォルダを Git リポジトリルートとして、全ファイルをコミット・プッシュするスクリプト。

【目的】
  このスクリプトが置かれているフォルダ（nonki）をリポジトリルートとみなし、
  その直下の全ファイル・サブフォルダを git add してコミットし、リモートへプッシュする。

【前提条件】
  - スクリプトは nonki フォルダ直下に配置すること。
  - nonki フォルダが Git リポジトリであること（.git が nonki 直下またはその上位にあること）。
  - リモート（origin）が設定されていること。初回は git push -u origin <branch> で upstream を設定する。
  - Git の認証は Git Credential Manager に任せる。

【入力の意味】
  - 引数: オプションでコミットメッセージを指定可能。未指定時は日時で自動生成。
  - リポジトリルート: この .py ファイルが存在するディレクトリ（nonki）を使用。

【出力の意味】
  - Git の add / commit / push を順に実行する。

【例外・エラー時の考え方】
  - リポジトリでない: エラーを出して終了コード 1。git init を案内。
  - ステージングに変更がない: メッセージを出して正常終了（終了コード 0）。空コミット・プッシュはしない。
  - git add / commit / push の失敗: check=True のため例外で終了。認証失敗やリモート未設定はここで表面化する。
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# =============================================================================
# 定数定義
# =============================================================================

FMT_COMMIT_MSG = "%Y-%m-%d %H:%M:%S"   # デフォルトのコミットメッセージに使う日時フォーマット
BRANCH_DEFAULT = "main"

MSG_ERR_NOT_REPO = "エラー: リポジトリルート（nonki）が Git リポジトリではありません。"
MSG_ERR_NOT_REPO_HINT = "  nonki フォルダで git init してください。"
MSG_NO_CHANGES = "コミットする変更がありません。プッシュはスキップします。"
MSG_ADD_DONE = "  git add . 完了"
MSG_COMMIT_DONE = "  コミット完了: {0}"
MSG_PUSH_PREP = "  プッシュ準備中..."
MSG_PUSH_DONE = "  プッシュ完了"


# =============================================================================
# リポジトリルートの取得・Git 判定
# =============================================================================

def get_repo_root() -> Path:
    """
    このスクリプトが置かれているディレクトリをリポジトリルート（nonki）とする。
    どこから実行しても、nonki フォルダを基準に Git 操作するため。
    """
    return Path(__file__).resolve().parent


def is_git_repository(root: Path) -> bool:
    """指定パスが Git リポジトリ（.git が存在）かどうか。"""
    if (root / ".git").exists() and (root / ".git").is_dir():
        return True
    for parent in root.parents:
        if (parent / ".git").exists():
            return True
    return False


def has_staged_changes(root: Path) -> bool:
    """リポジトリルートで git diff --cached --quiet を実行し、ステージングに変更があれば True。"""
    r = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=root,
        capture_output=True,
    )
    return r.returncode != 0


def get_current_branch(root: Path) -> str:
    """現在のブランチ名。失敗時は BRANCH_DEFAULT を返す。"""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else BRANCH_DEFAULT


def has_upstream(root: Path) -> bool:
    """現在ブランチに upstream が設定されていれば True。"""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


# =============================================================================
# メイン処理
# =============================================================================

def main() -> int:
    """
    リポジトリルート（nonki）で git add . / git commit / git push を実行する。
    引数でコミットメッセージを指定可能。未指定時は日時を使用。
    """
    root = get_repo_root()
    print(f"リポジトリルート: {root}")

    if not is_git_repository(root):
        print(MSG_ERR_NOT_REPO)
        print(MSG_ERR_NOT_REPO_HINT)
        return 1

    subprocess.run(
        ["git", "add", "."],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    print(MSG_ADD_DONE)

    if not has_staged_changes(root):
        print(MSG_NO_CHANGES)
        return 0

    if len(sys.argv) >= 2:
        commit_message = " ".join(sys.argv[1:])
    else:
        commit_message = datetime.now().strftime(FMT_COMMIT_MSG)

    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    print(MSG_COMMIT_DONE.format(commit_message))

    print(MSG_PUSH_PREP)
    branch = get_current_branch(root)
    if has_upstream(root):
        subprocess.run(
            ["git", "push"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    print(MSG_PUSH_DONE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
