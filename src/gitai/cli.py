import typer
from importlib.metadata import version as pkg_version

from gitai.core import gitops

app = typer.Typer(help="Available Commands")


@app.command()
def version():
    typer.echo(f"gitai [v{pkg_version('gitai')}]")


@app.command()
def plan():
    try:
        files = gitops.changed_files()
    except gitops.GitError as e:
        typer.echo(f"Erro: {e}")
        raise typer.Exit(code=1)

    if not files:
        typer.echo("Nenhuma mudança encontrada.")
        raise typer.Exit()

    typer.echo(f"{len(files)} arquivo(s) com mudanças:")
    for f in files:
        typer.echo(f"  - {f}")


@app.command()
def commit(dry_run: bool = False, yes: bool = False):
    typer.echo(f"commit: ainda não implementado (dry_run={dry_run}, yes={yes})")

@app.command()
def stats():
    """Resume as mudanças por tipo."""
    try:
        changes = gitops.file_changes()
    except gitops.GitError as e:
        typer.echo(f"Erro: {e}")
        raise typer.Exit(code=1)

    if not changes:
        typer.echo("Nenhuma mudança encontrada.")
        raise typer.Exit()

    for kind in ("added", "modified", "deleted", "renamed"):
        n = sum(1 for k, _ in changes if k == kind)
        if n:
            typer.echo(f"{kind:<10}{n}")
    typer.echo(f"{'total':<10}{len(changes)}")