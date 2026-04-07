"""CLI rendering helpers."""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()


def show_banner(model: str, base_url: str | None, version: str) -> None:
    console.print(
        Panel(
            f"[bold]ReuleauxCoder[/bold] v{version}\n"
            f"Model: [cyan]{model}[/cyan]"
            + (f"  Base: [dim]{base_url}[/dim]" if base_url else "")
            + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
            border_style="blue",
        )
    )


def show_help() -> None:
    console.print(
        Panel(
            "[bold]Commands:[/bold]\n"
            "  /help          Show this help\n"
            "  /reset         Clear conversation history\n"
            "  /model <name>  Switch model mid-conversation\n"
            "  /tokens        Show token usage\n"
            "  /compact       Compress conversation context\n"
            "  /save          Save session to disk\n"
            "  /sessions      List saved sessions\n"
            "  quit           Exit ReuleauxCoder",
            title="ReuleauxCoder Help",
            border_style="dim",
        )
    )


def render_markdown(text: str) -> None:
    console.print(Markdown(text))


def show_error(text: str) -> None:
    console.print(f"[red]{text}[/red]")


def show_warning(text: str) -> None:
    console.print(f"[yellow]{text}[/yellow]")


def show_info(text: str) -> None:
    console.print(text)


def brief(kwargs: dict, maxlen: int = 80) -> str:
    s = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
