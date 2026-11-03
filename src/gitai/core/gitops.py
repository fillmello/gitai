import re
import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def run_git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip())
    return result.stdout


def get_diff(cwd: Path | None = None) -> str:
    return run_git("diff", "HEAD", cwd=cwd)


def changed_files(cwd: Path | None = None) -> list[str]:
    out = run_git("status", "--porcelain", "--untracked-files=all", cwd=cwd)
    return [line[3:] for line in out.splitlines()]