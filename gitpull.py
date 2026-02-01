"""リモートから最新を取得。指定フォルダは除外し、それ以外を origin の内容で上書きする。"""

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# 定数
BRANCH_DEFAULT = "main"  # 取得先ブランチ
EXCLUDE_FOLDERS = ["docs"]  # 取得除外フォルダ

# メッセージ
MSG_ERR_NOT_REPO = "エラー: リポジトリルートが Git リポジトリではありません。"
MSG_ERR_NOT_REPO_HINT = "  git init するか、リポジトリ直下で実行してください。"
MSG_FETCH_PREP = "  fetch 中..."
MSG_FETCH_DONE = "  fetch 完了"
MSG_NO_UPSTREAM = "  upstream が未設定です。除外フォルダ以外を取得するには origin を設定してください。"
MSG_UP_TO_DATE = "  既に最新です。"
MSG_CHECKOUT = "  {0} 件を origin の内容で更新しました。"


# スクリプト配置ディレクトリをリポジトリルートとして返す
#
# 引数:
#   （なし）
#
# 戻り値:
#   Path: リポジトリルート
def get_repo_root() -> Path:
    return Path(__file__).resolve().parent


# 指定パスが Git リポジトリか
#
# 引数:
#   root (Path): 対象パス
#
# 戻り値:
#   bool: リポジトリなら True
def is_git_repository(root: Path) -> bool:
    if (root / ".git").exists() and (root / ".git").is_dir():
        return True
    for parent in root.parents:
        if (parent / ".git").exists():
            return True
    return False


# 現在のブランチ名
#
# 引数:
#   root (Path): リポジトリルート
#
# 戻り値:
#   str: ブランチ名（失敗時は BRANCH_DEFAULT）
def get_current_branch(root: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else BRANCH_DEFAULT


# upstream の参照（例: origin/main）
#
# 引数:
#   root (Path): リポジトリルート
#
# 戻り値:
#   str or None: 設定済みなら "origin/main" 等、未設定なら None
def get_upstream_ref(root: Path) -> Optional[str]:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


# パスが除外フォルダ配下か
#
# 引数:
#   path_str (str): Git のパス（フォワードスラッシュ）
#   exclude_folders (list): 除外するフォルダ名のリスト
#
# 戻り値:
#   bool: 除外対象なら True
def _is_excluded(path_str: str, exclude_folders: List[str]) -> bool:
    for folder in exclude_folders:
        if path_str == folder or path_str.startswith(folder + "/"):
            return True
    return False


# 除外フォルダ以外の変更ファイルを origin から checkout
#
# 引数:
#   root (Path): リポジトリルート
#   upstream_ref (str): 参照（例: origin/main）
#   exclude_folders (list): 除外フォルダ名
#
# 戻り値:
#   int: checkout したファイル数
def _checkout_excluding_folders(root: Path, upstream_ref: str, exclude_folders: List[str]) -> int:
    # HEAD と upstream で差分のあるファイル一覧を取得（両方向）
    r = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", upstream_ref],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return 0

    paths = [p.strip() for p in r.stdout.strip().split("\n") if p.strip()]
    # 除外フォルダ配下は除去
    to_checkout = [p for p in paths if not _is_excluded(p, exclude_folders)]

    if not to_checkout:
        return 0

    subprocess.run(
        ["git", "checkout", upstream_ref, "--"] + to_checkout,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return len(to_checkout)


# fetch して除外フォルダ以外を origin の内容で更新
#
# 引数:
#   （なし）
#
# 戻り値:
#   int: 0=正常、1=リポジトリでない／upstream 未設定
def main() -> int:
    root = get_repo_root()
    print(f"リポジトリルート: {root}")

    if not is_git_repository(root):
        print(MSG_ERR_NOT_REPO)
        print(MSG_ERR_NOT_REPO_HINT)
        return 1

    print(MSG_FETCH_PREP)
    subprocess.run(
        ["git", "fetch", "origin"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    print(MSG_FETCH_DONE)

    upstream_ref = get_upstream_ref(root)
    if not upstream_ref:
        print(MSG_NO_UPSTREAM)
        return 1

    count = _checkout_excluding_folders(root, upstream_ref, EXCLUDE_FOLDERS)
    if count == 0:
        print(MSG_UP_TO_DATE)
    else:
        print(MSG_CHECKOUT.format(count))

    return 0


if __name__ == "__main__":
    sys.exit(main())
