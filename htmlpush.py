"""report.py が出力した docs/index.html を Git でコミット・プッシュし、GitHub Pages に公開する。"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

# 定数
DIR_DOCS = Path("docs")
DIR_HTML_HISTORY = Path("html")
FILE_INDEX_HTML = "index.html"
ENV_PAGES_URL = "GITHUB_PAGES_URL"
DEFAULT_PAGES_URL = "https://your-username.github.io/your-repo"
FMT_COMMIT_TIME = "%Y-%m-%d %H:%M:%S"
BRANCH_DEFAULT = "main"

MSG_SECTION_PRE = "--- 前処理 ---"
MSG_SECTION_MAIN = "--- メイン処理 ---"
MSG_SECTION_POST = "--- 後処理 ---"
MSG_PRE_DONE = "  前処理完了"
MSG_POST_DONE = "  処理完了"
MSG_ERR_NOT_REPO = "エラー: 現在のディレクトリがGitリポジトリではありません。"
MSG_ERR_NOT_REPO_HINT = "  git init するか、リポジトリ直下で実行してください。"
MSG_WARN_NO_SOURCE = "警告: docs/index.html が見つかりません。report.py を先に実行してください。"
MSG_WARN_NO_SOURCE_NOTE = "  （report.py は docs/index.html に出力し、古いファイルは html/ に退避します）"
MSG_HTML_READY = "  HTML: {0}（report.py で出力済み）"
MSG_COMMIT_DONE = "  コミット完了"
MSG_PUSH_PREP = "  プッシュ準備中..."
MSG_PUSH_DONE = "  プッシュ完了"
MSG_PUBLIC_URL = "  公開URL: {0}"
MSG_NO_CHANGES = "  コミットする変更がありません。プッシュはスキップします。"


# カレントまたは上位に .git があるか（指定パスが Git リポジトリか）
#
# 引数:
#   （なし。カレントディレクトリを起点に探索）
#
# 戻り値:
#   bool: リポジトリ内なら True
def _is_git_repository() -> bool:
    if Path(".git").exists() and Path(".git").is_dir():
        return True
    # 親ディレクトリに .git があるか探索
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            return True
    return False


# 実行可否の確認（Gitリポジトリ・docs/index.html の存在）
#
# 戻り値:
#   tuple[Path, bool]: (docs_dir, ok)。ok=False のときは main_process を実行しない
def preprocess() -> tuple[Path, bool]:
    print(MSG_SECTION_PRE)

    # Git リポジトリでない場合
    if not _is_git_repository():
        print(MSG_ERR_NOT_REPO)
        print(MSG_ERR_NOT_REPO_HINT)
        return DIR_DOCS, False

    report_path = DIR_DOCS / FILE_INDEX_HTML
    # index.html が存在しない場合
    if not report_path.exists():
        print(MSG_WARN_NO_SOURCE)
        print(MSG_WARN_NO_SOURCE_NOTE)
        return DIR_DOCS, False

    print(MSG_PRE_DONE)
    return DIR_DOCS, True


# 公開 URL のベース（環境変数 GITHUB_PAGES_URL または DEFAULT_PAGES_URL）
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: ベース URL
def _get_pages_base_url() -> str:
    return os.getenv(ENV_PAGES_URL, DEFAULT_PAGES_URL)


# 今回の公開 URL（固定ファイル名）
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: 公開 URL
def _build_latest_url() -> str:
    base = _get_pages_base_url().rstrip("/")
    return f"{base}/{FILE_INDEX_HTML}"


# ステージングに変更があるか
#
# 引数:
#   （なし）
#
# 戻り値:
#   bool: 変更あれば True
def _has_staged_changes() -> bool:
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    return r.returncode != 0


# 現在のブランチ名
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: ブランチ名（失敗時は BRANCH_DEFAULT）
def _get_current_branch() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else BRANCH_DEFAULT


# 現在ブランチに upstream が設定されているか
#
# 引数:
#   （なし）
#
# 戻り値:
#   bool: 設定済みなら True
def _has_upstream() -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


# Git で add / commit / push まで行う。docs/ と html/ を add
#
# 引数:
#   docs_dir (Path): docs フォルダパス（preprocess で存在確認済み）
#
# 戻り値:
#   None
def main_process(docs_dir: Path) -> None:
    print("\n" + MSG_SECTION_MAIN)

    report_path = docs_dir / FILE_INDEX_HTML
    print(MSG_HTML_READY.format(report_path))

    # docs/ と html/ を add（index.html 変更分＋退避ファイル）
    latest_url = _build_latest_url()
    print(f"  最新URL: {latest_url}")

    subprocess.run(["git", "add", str(docs_dir), str(DIR_HTML_HISTORY)], check=True, capture_output=True, text=True)

    # 空コミット防止：ステージングに変更がなければ return
    if not _has_staged_changes():
        print(MSG_NO_CHANGES)
        return

    commit_message = f"Update report: {datetime.now().strftime(FMT_COMMIT_TIME)}"
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        check=True,
        capture_output=True,
        text=True,
    )
    print(MSG_COMMIT_DONE)

    print(MSG_PUSH_PREP)
    branch = _get_current_branch()
    # upstream 未設定時は -u で初回設定
    if _has_upstream():
        subprocess.run(["git", "push"], check=True, capture_output=True, text=True)
    else:
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            check=True,
            capture_output=True,
            text=True,
        )
    print(MSG_PUSH_DONE)
    print(MSG_PUBLIC_URL.format(latest_url))


# 処理終了をログで明示
#
# 引数:
#   （なし）
#
# 戻り値:
#   None
def postprocess() -> None:
    print("\n" + MSG_SECTION_POST)
    print(MSG_POST_DONE)
    print("---")


# 前処理 → メイン処理 → 後処理の順で実行
#
# 引数:
#   （なし）
#
# 戻り値:
#   int: 0=正常、1=前処理失敗（リポジトリでない／index.html なし）
def main() -> int:
    docs_dir, ok = preprocess()
    if not ok:
        return 1
    main_process(docs_dir)
    postprocess()
    return 0


if __name__ == "__main__":
    exit(main())
