"""CLI command handlers."""

from reuleauxcoder.domain.context.manager import estimate_tokens
from reuleauxcoder.services.sessions.manager import list_sessions, save_session
from reuleauxcoder.interfaces.cli.render import console, show_help


def handle_command(user_input: str, agent, config, current_session_id: str | None):
    if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
        if agent.messages:
            sid = save_session(agent.messages, config.model, current_session_id)
            console.print(f"[dim]Session auto-saved: {sid}[/dim]")
        return {"action": "exit", "session_id": current_session_id}

    if user_input == "/help":
        show_help()
        return {"action": "continue", "session_id": current_session_id}

    if user_input == "/reset":
        agent.reset()
        console.print("[yellow]Conversation reset.[/yellow]")
        return {"action": "continue", "session_id": current_session_id}

    if user_input == "/tokens":
        p = agent.llm.total_prompt_tokens
        c = agent.llm.total_completion_tokens
        console.print(
            f"Tokens used this session: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p + c}[/bold] total"
        )
        return {"action": "continue", "session_id": current_session_id}

    if user_input.startswith("/model "):
        new_model = user_input[7:].strip()
        if new_model:
            agent.llm.model = new_model
            config.model = new_model
            console.print(f"Switched to [cyan]{new_model}[/cyan]")
        return {"action": "continue", "session_id": current_session_id}

    if user_input == "/compact":
        before = estimate_tokens(agent.messages)
        compressed = agent.context.maybe_compress(agent.messages, agent.llm)
        after = estimate_tokens(agent.messages)
        if compressed:
            console.print(
                f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]"
            )
        else:
            console.print(
                f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]"
            )
        return {"action": "continue", "session_id": current_session_id}

    if user_input == "/save":
        sid = save_session(agent.messages, config.model, current_session_id)
        current_session_id = sid
        console.print(f"[green]Session saved: {sid}[/green]")
        console.print(f"Resume with: rcoder -r {sid}")
        return {"action": "continue", "session_id": current_session_id}

    if user_input == "/sessions":
        sessions = list_sessions()
        if not sessions:
            console.print("[dim]No saved sessions.[/dim]")
        else:
            for s in sessions:
                console.print(
                    f"  [cyan]{s.id}[/cyan] ({s.model}, {s.saved_at}) {s.preview}"
                )
        return {"action": "continue", "session_id": current_session_id}

    return {"action": "chat", "session_id": current_session_id}
