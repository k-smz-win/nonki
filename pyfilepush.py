"""nonki フォルダをリポジトリルートとして、全ファイル（docs 除外）をコミット・プッシュする。"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 定数
FMT_COMMIT_MSG = "%Y-%m-%d %H:%M:%S"  # コミットメッセージ書式
BRANCH_DEFAULT = "main"
ADD_FOLDERS = ["html", "data"]  # add 対象
EXCLUDE_FOLDERS = ["docs"]  # アンステージ対象

# メッセージ
MSG_ERR_NOT_REPO = "エラー: リポジトリルート（nonki）が Git リポジトリではありません。"
MSG_ERR_NOT_REPO_HINT = "  nonki フォルダで git init してください。"
MSG_NO_CHANGES = "コミットする変更がありません。プッシュはスキップします。"
MSG_ADD_DONE = "  git add 完了（docs 除外・リモートの docs は維持）"
MSG_COMMIT_DONE = "  コミット完了: {0}"
MSG_PUSH_PREP = "  プッシュ準備中..."
MSG_PUSH_DONE = "  プッシュ完了"


# スクリプト配置ディレクトリをリポジトリルート（nonki）として返す
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
    # 親ディレクトリに .git があるか探索
    for parent in root.parents:
        if (parent / ".git").exists():
            return True
    return False


# ステージングに変更があるか
#
# 引数:
#   root (Path): リポジトリルート
#
# 戻り値:
#   bool: 変更あれば True
def has_staged_changes(root: Path) -> bool:
    r = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=root,
        capture_output=True,
    )
    return r.returncode != 0


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


# 現在ブランチに upstream が設定されているか
#
# 引数:
#   root (Path): リポジトリルート
#
# 戻り値:
#   bool: 設定済みなら True
def has_upstream(root: Path) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


# リポジトリルートで git add / commit / push を実行
#
# 引数:
#   sys.argv[1:]: オプションでコミットメッセージ（未指定時は日時）
#
# 戻り値:
#   int: 0=正常、1=リポジトリでない／変更なし
def main() -> int:
    root = get_repo_root()
    print(f"リポジトリルート: {root}")

    # Git リポジトリでない場合
    if not is_git_repository(root):
        print(MSG_ERR_NOT_REPO)
        print(MSG_ERR_NOT_REPO_HINT)
        return 1

    # . と ADD_FOLDERS を add。その後 docs をアンステージ
    add_paths = ["."]
    # ADD_FOLDERS のうち存在するものを add 対象に
    for name in ADD_FOLDERS:
        if (root / name).exists():
            add_paths.append(name)
    subprocess.run(
        ["git", "add"] + add_paths,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # EXCLUDE_FOLDERS をアンステージ
    for name in EXCLUDE_FOLDERS:
        subprocess.run(
            ["git", "reset", "HEAD", "--", name],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    print(MSG_ADD_DONE)

    # 空コミット防止
    if not has_staged_changes(root):
        print(MSG_NO_CHANGES)
        return 0

    # 引数でコミットメッセージ未指定なら日時を使用
    if len(sys.argv) >= 2:
        commit_message = " ".join(sys.argv[1:])
    else:
        commit_message = datetime.now().strftime(FMT_COMMIT_MSG)

    result = subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("コミットに失敗しました:")
        if result.stderr:
            print(result.stderr.strip())
        if result.stdout:
            print(result.stdout.strip())
        return 1
    print(MSG_COMMIT_DONE.format(commit_message))

    print(MSG_PUSH_PREP)
    branch = get_current_branch(root)
    # upstream 未設定時は -u で初回設定
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
