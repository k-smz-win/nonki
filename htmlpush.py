"""
GitHub Pages 用レポート公開スクリプト

【目的】
  report.py が docs/index.html に出力したレポートを Git でコミット・プッシュする。
  GitHub Pages の公開ソースを docs/ にしている前提で、固定URLで最新レポートを公開する。

【前提条件】
  - カレントディレクトリが Git リポジトリであること（または上位に .git があること）。
  - docs/index.html が存在すること（report.py を先に実行すること。report.py が docs/ 直下に出力し、古いファイルは html/ に退避する）。
  - Git の認証は Git Credential Manager に任せる（本スクリプトは git push を呼ぶだけ）。

【入力の意味】
  - 入力ファイル: docs/index.html。report.py が data/ の CSV から生成して docs/ 直下に出力した 1 ファイル。古いファイルは html/ に退避される。
  - 環境変数 GITHUB_PAGES_URL: 公開URLのベース（例: https://user.github.io/repo）。未設定時は定数 DEFAULT_PAGES_URL を使用。

【出力の意味】
  - （HTML の配置・古いファイルの html/ 退避は report.py が行う。本スクリプトは git add / commit / push のみ）

【例外・エラー時の考え方】
  - docs/index.html がない: 警告を出して終了コード 1。report.py の実行順と役割をメッセージで案内。
  - Git リポジトリでない: エラーを出して終了コード 1。git init またはリポジトリ直下での実行を案内。
  - git add/commit/push の失敗: check=True のため例外で終了。対処は Git Credential Manager やリモート設定の確認。
  - ステージングに変更がない: 例外にはせず、コミット・プッシュをスキップして正常終了（変更なしは仕様どおり）。

【なぜこの実装になっているか】
  - report.py が docs/index.html に直接出力し、古いファイルは html/ に退避するため、本スクリプトは存在確認と Git 操作（docs/ と html/ を add）のみ行う。
  - git diff --cached --quiet でコミット要否を判定している理由: ステージングに変更がなければコミットしないことで、空コミットを防ぎ、push も不要と判断するため。
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

# =============================================================================
# 定数定義（変更されやすい値はここに集約する）
# =============================================================================

# --- パス・ファイル名（report.py が docs/index.html に出力、古いファイルは html/ に退避する前提）---
DIR_DOCS = Path("docs")
DIR_HTML_HISTORY = Path("html")
FILE_INDEX_HTML = "index.html"

# --- URL（Pages のルートURL。環境変数 GITHUB_PAGES_URL で上書き可能）---
ENV_PAGES_URL = "GITHUB_PAGES_URL"
DEFAULT_PAGES_URL = "https://your-username.github.io/your-repo"

# --- 日時フォーマット（コミットメッセージ用）---
FMT_COMMIT_TIME = "%Y-%m-%d %H:%M:%S"

# --- Git 関連 ---
BRANCH_DEFAULT = "main"

# --- 表示メッセージ（文言変更はここだけ）---
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


# =============================================================================
# 前処理で使う補助（環境チェック）
# =============================================================================

def _is_git_repository() -> bool:
    """
    カレントまたは上位に .git があるか。
    前提: カレントはリポジトリ直下想定。上位に .git があっても True にするのは、サブディレクトリから実行された場合に備えるため。
    """
    if Path(".git").exists() and Path(".git").is_dir():
        return True
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            return True
    return False


# =============================================================================
# 前処理（環境チェック・docs/index.html の存在確認）
# =============================================================================
# 古いファイルの html/ 退避は report.py が書き出す前に行うため、本スクリプトでは行わない。

def preprocess() -> tuple[Path, bool]:
    """
    実行可否の確認（Git リポジトリであること、docs/index.html が存在すること）。

    前提: report.py を先に実行し、docs/index.html が出力されていること。
    戻り値: (docs_dir, ok)。ok が False のときは main_process を実行せず、呼び出し側で終了コード 1 を返す想定。
    """
    print(MSG_SECTION_PRE)

    if not _is_git_repository():
        print(MSG_ERR_NOT_REPO)
        print(MSG_ERR_NOT_REPO_HINT)
        return DIR_DOCS, False

    report_path = DIR_DOCS / FILE_INDEX_HTML
    if not report_path.exists():
        print(MSG_WARN_NO_SOURCE)
        print(MSG_WARN_NO_SOURCE_NOTE)
        return DIR_DOCS, False

    print(MSG_PRE_DONE)
    return DIR_DOCS, True


# =============================================================================
# メイン処理（HTML配置・URL記録・Git add / commit / push）
# =============================================================================

def _get_pages_base_url() -> str:
    """
    公開URLのベース（例: https://user.github.io/repo）。
    環境変数 GITHUB_PAGES_URL があればそれを使い、なければ DEFAULT_PAGES_URL。デプロイ先が環境ごとに変わる想定のため。
    """
    return os.getenv(ENV_PAGES_URL, DEFAULT_PAGES_URL)


def _build_latest_url() -> str:
    """今回の公開URL（固定ファイル名のため毎回同じ形）。"""
    base = _get_pages_base_url().rstrip("/")
    return f"{base}/{FILE_INDEX_HTML}"


def _has_staged_changes() -> bool:
    """
    ステージングに変更があるとき True。
    git diff --cached --quiet は変更がなければ 0、あれば 1 を返すため、returncode != 0 で「コミットすべき変更あり」と判定する。
    """
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    return r.returncode != 0


def _get_current_branch() -> str:
    """
    現在のブランチ名（例: main）。
    rev-parse が失敗した場合は BRANCH_DEFAULT を返す。初回 push 時に -u origin <branch> で upstream を設定するために必要。
    """
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else BRANCH_DEFAULT


def _has_upstream() -> bool:
    """
    現在ブランチに upstream が設定されていれば True。
    True のときは git push のみ、False のときは git push -u origin <branch> で初回設定する。二重設定を避けるため。
    """
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def main_process(docs_dir: Path) -> None:
    """
    Git で add / commit / push まで行う。HTML は report.py が既に docs/index.html に出力済み。docs/ と html/ の両方を add する。

    入力: docs_dir は preprocess で存在確認済みの Path。docs/index.html は前処理で存在確認済み。
    出力: Git のコミット・プッシュ。
    例外: git add / commit / push は check=True のため失敗時は CalledProcessError。認証失敗やリモート未設定などはここで表面化する。
    変更がない場合: _has_staged_changes() が False ならコミット・プッシュは行わずメッセージのみ。空コミットを防ぐため。
    """
    print("\n" + MSG_SECTION_MAIN)

    report_path = docs_dir / FILE_INDEX_HTML
    print(MSG_HTML_READY.format(report_path))

    latest_url = _build_latest_url()
    print(f"  最新URL: {latest_url}")

    subprocess.run(["git", "add", str(docs_dir), str(DIR_HTML_HISTORY)], check=True, capture_output=True, text=True)

    # add の後にステージングに変更があるか確認。内容が前回と同じ（差分なし）ならコミット・プッシュは行わない（空コミット防止）。
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
    # 初回 push 時は -u origin <branch> で upstream を設定。既に設定済みなら push のみ（二重設定を避ける）。
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


# =============================================================================
# 後処理（完了ログ）
# =============================================================================

def postprocess() -> None:
    """
    処理の終了をログで明示する。
    前処理・メイン・後処理の区切りをログで分かるようにするため。エラー時は main_process で例外になるためここには来ない。
    """
    print("\n" + MSG_SECTION_POST)
    print(MSG_POST_DONE)
    print("---")


# =============================================================================
# エントリポイント
# =============================================================================

def main() -> int:
    """
    前処理 → メイン処理 → 後処理の順で実行し、成否を終了コードで返す。

    戻り値: 0=正常終了、1=前処理で失敗（リポジトリでない／index.html がない／退避失敗）。
    メイン処理で git が失敗した場合は例外のため 0 は返らない（未捕捉ならトレースバックで終了）。
    """
    docs_dir, ok = preprocess()
    if not ok:
        return 1
    main_process(docs_dir)
    postprocess()
    return 0


if __name__ == "__main__":
    exit(main())
