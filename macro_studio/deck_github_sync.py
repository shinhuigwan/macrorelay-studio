"""Publish the bundled Deck backup without touching the user's Git index."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


BUNDLED_RELATIVE_PATH = "deck_presets/macrorelay_bundled_deck.json"
DEFAULT_REMOTE = "https://github.com/shinhuigwan/macrorelay-studio.git"
MAX_GITHUB_FILE_BYTES = 100_000_000


class DeckGitHubSyncError(RuntimeError):
    pass


def _git(root: Path, *args: str, index_file: Path | None = None,
         timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DeckGitHubSyncError("Git이 설치되어 있지 않거나 실행 경로에 없습니다.") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeckGitHubSyncError("GitHub 연결 시간이 초과되었습니다. 로컬 저장본은 유지됩니다.") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise DeckGitHubSyncError((detail or "Git 명령 실행에 실패했습니다.")[-1800:])
    return result


def _same_remote(actual: str, expected: str) -> bool:
    def normalize(value: str) -> str:
        value = value.strip().rstrip("/")
        if value.endswith(".git"):
            value = value[:-4]
        if value.startswith("git@github.com:"):
            value = "https://github.com/" + value.split(":", 1)[1]
        elif value.startswith("ssh://git@github.com/"):
            value = "https://github.com/" + value.split("github.com/", 1)[1]
        return value.lower()

    return normalize(actual) == normalize(expected)


def publish_bundled_deck(app_root: Path, *, expected_remote: str = DEFAULT_REMOTE) -> str | None:
    """Push only the bundled JSON to origin/main; return commit ID or None if unchanged.

    A temporary index and commit based on the *remote* main tree ensure unrelated
    working files, staged changes, and local branch history are left untouched.
    """
    root = Path(app_root).resolve()
    source = root / BUNDLED_RELATIVE_PATH
    if not source.is_file():
        raise DeckGitHubSyncError("업로드할 내장 구성 파일이 없습니다. 먼저 로컬에 저장해 주세요.")
    if source.stat().st_size > MAX_GITHUB_FILE_BYTES:
        raise DeckGitHubSyncError("내장 구성 파일이 GitHub의 단일 파일 제한(100MB)을 넘습니다.")
    git_root = Path(_git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if git_root != root:
        raise DeckGitHubSyncError("내장 구성 폴더가 Git 저장소 최상위 폴더가 아닙니다.")
    actual_remote = _git(root, "remote", "get-url", "origin").stdout.strip()
    if not _same_remote(actual_remote, expected_remote):
        raise DeckGitHubSyncError("origin이 MacroRelay GitHub 저장소를 가리키지 않습니다. 업로드를 중단했습니다.")

    last_push_error = ""
    for attempt in range(2):
        _git(root, "fetch", "--no-tags", "origin", "main", timeout=180)
        base = _git(root, "rev-parse", "FETCH_HEAD").stdout.strip()
        with tempfile.TemporaryDirectory(prefix="macrorelay-deck-git-") as temp_dir:
            index_file = Path(temp_dir) / "index"
            _git(root, "read-tree", base, index_file=index_file)
            _git(root, "add", "--", BUNDLED_RELATIVE_PATH, index_file=index_file)
            difference = _git(root, "diff", "--cached", "--quiet", base, "--",
                              BUNDLED_RELATIVE_PATH, index_file=index_file, check=False)
            if difference.returncode == 0:
                return None
            if difference.returncode != 1:
                raise DeckGitHubSyncError("내장 구성 변경 여부를 확인하지 못했습니다.")
            tree = _git(root, "write-tree", index_file=index_file).stdout.strip()
            commit = _git(root, "commit-tree", tree, "-p", base, "-m",
                          "Update bundled Deck presets").stdout.strip()
        push = _git(root, "push", "origin", f"{commit}:refs/heads/main",
                    timeout=240, check=False)
        if push.returncode == 0:
            return commit
        last_push_error = (push.stderr or push.stdout).strip()
        if attempt == 0 and ("non-fast-forward" in last_push_error or "fetch first" in last_push_error):
            continue
        break
    raise DeckGitHubSyncError(
        "GitHub 업로드에 실패했습니다. 로컬 저장본은 유지됩니다.\n" + last_push_error[-1800:]
    )
