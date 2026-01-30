"""
nonki フォルダを Git リポジトリルートとして、全ファイルをコミットするスクリプト。

【目的】
  このスクリプトが置かれているフォルダ（nonki）をリポジトリルートとみなし、
  その直下の全ファイル・サブフォルダを git add してコミットする。

【前提条件】
  - スクリプトは nonki フォルダ直下に配置すること。
  - nonki フォルダが Git リポジトリであること（.git が nonki 直下またはその上位にあること）。
  - Git の認証は Git Credential Manager に任せる（本スクリプトは push しない）。

【入力の意味】
  - 引数: オプションでコミットメッセージを指定可能。未指定時は日時で自動生成。
  - リポジトリルート: この .py ファイルが存在するディレクトリ（nonki）を使用。

【出力の意味】
  - Git の add / commit のみ。push は行わない（必要なら手動または gitpush.py で実行）。

【例外・エラー時の考え方】
  - リポジトリでない: エラーを出して終了コード 1。git init を案内。
  - ステージングに変更がない: メッセージを出して正常終了（終了コード 0）。空コミットはしない。
  - git add / commit の失敗: check=True のため例外で終了。
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# =============================================================================
# 定数定義
# =============================================================================

FMT_COMMIT_MSG = "%Y-%m-%d %H:%M:%S"   # デフォルトのコミットメッセージに使う日時フォーマット

MSG_ERR_NOT_REPO = "エラー: リポジトリルート（nonki）が Git リポジトリではありません。"
MSG_ERR_NOT_REPO_HINT = "  nonki フォルダで git init してください。"
MSG_NO_CHANGES = "コミットする変更がありません。"
MSG_ADD_DONE = "  git add . 完了"
MSG_COMMIT_DONE = "  コミット完了: {0}"


# =============================================================================
# リポジトリルートの取得
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


# =============================================================================
# メイン処理
# =============================================================================

def main() -> int:
    """
    リポジトリルート（nonki）で git add . と git commit を実行する。
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
