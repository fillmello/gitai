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

def file_changes(cwd: Path | None = None) -> list[tuple[str, str]]:
    """Devolve (tipo, caminho) de cada mudança.

    Tipos: 'added', 'modified', 'deleted', 'renamed'.
    """
    out = run_git("status", "--porcelain", "--untracked-files=all", cwd=cwd)
    changes = []
    for line in out.splitlines():
        code, path = line[:2], line[3:]      # "XY caminho": 2 letras de status + espaço
        if "R" in code:
            kind = "renamed"
            path = path.split(" -> ")[-1]    # "antigo -> novo": fica o novo
        elif code == "??" or "A" in code:
            kind = "added"
        elif "D" in code:
            kind = "deleted"
        else:
            kind = "modified"
        changes.append((kind, path))
    return changes