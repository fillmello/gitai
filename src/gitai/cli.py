import typer

app = typer.Typer(help="Organiza suas mudanças em commits por contexto.")


@app.command()
def plan():
    typer.echo("plan: ainda não implementado")


@app.command()
def commit(dry_run: bool = False, yes: bool = False):
    typer.echo(f"commit: ainda não implementado (dry_run={dry_run}, yes={yes})")