import typer
from importlib.metadata import version as pkg_version

from gitai.core import gitops

app = typer.Typer(help="Available Commands")


@app.command()
def version():
    typer.echo(f"gitai [v{pkg_version('gitai')}]")
    typer.echo(f"git   [v{gitops.git_version()}]")


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