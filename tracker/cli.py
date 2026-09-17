import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from .database import init_db, get_latest_snapshots, get_bucket_history
from .collector import collect_and_store
from .pacing_engine import calculate_pacing, format_remaining_time

console = Console(force_terminal=True, legacy_windows=False)
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = str(BASE_DIR / "usage_tracker.db")

def show_status():
    init_db(DB_PATH)
    latest = get_latest_snapshots(DB_PATH)
    now_utc = datetime.now(timezone.utc)

    if not latest:
        console.print("[yellow]No usage snapshots found in database. Running initial poll...[/yellow]")
        latest = collect_and_store(DB_PATH)

    table = Table(title="Antigravity (agy) Live Quota & Usage Status", header_style="bold magenta")
    table.add_column("Model Group", style="cyan", width=22)
    table.add_column("Window", style="bold", width=8)
    table.add_column("Remaining %", justify="right", width=13)
    table.add_column("Used %", justify="right", width=10)
    table.add_column("Resets In", justify="center", style="bold yellow", width=18)
    table.add_column("Reset Target (UTC)", style="dim", width=22)
    table.add_column("Status", justify="center", width=12)

    for s in latest:
        rem_pct = float(s["remaining_fraction"]) * 100.0
        used_pct = 100.0 - rem_pct
        rem_style = "green" if rem_pct > 30 else ("yellow" if rem_pct > 15 else "bold red")
        countdown = format_remaining_time(s.get("reset_time", ""), now_utc)

        table.add_row(
            s["group_name"],
            s["window_type"].upper(),
            f"[{rem_style}]{rem_pct:.1f}%[/{rem_style}]",
            f"{used_pct:.1f}%",
            countdown["text"],
            s["reset_time"] or "N/A",
            "[green]HEALTHY[/green]" if rem_pct > 20 else "[red]LOW[/red]"
        )

    console.print(table)

    # Show Pacing for Gemini Weekly
    gemini_weekly = next((s for s in latest if s["bucket_id"] == "gemini-weekly"), None)
    if gemini_weekly:
        history = get_bucket_history(DB_PATH, "gemini-weekly", limit=1000)
        pacing = calculate_pacing(gemini_weekly, history)
        show_pacing_panel(pacing, "Gemini Models (Flash & Pro)")

    # Show Pacing for Claude & GPT Weekly
    claude_weekly = next((s for s in latest if s["bucket_id"] == "3p-weekly"), None)
    if claude_weekly:
        c_history = get_bucket_history(DB_PATH, "3p-weekly", limit=1000)
        c_pacing = calculate_pacing(claude_weekly, c_history)
        show_pacing_panel(c_pacing, "Claude & GPT Models (Opus, Sonnet, GPT-OSS)")

def show_pacing_panel(pacing: dict, title_prefix: str = "5-Day Credit Pacing"):
    m = pacing["metrics"]
    c = pacing["cycle_info"]
    s = pacing["status"]
    cd = c.get("countdown", {})

    delta_color = "red" if m["variance_delta_percent"] > 5 else ("blue" if m["variance_delta_percent"] < -5 else "green")
    delta_sign = "+" if m["variance_delta_percent"] > 0 else ""

    pacing_text = f"""
[bold]Time Until Reset:[/bold] [bold yellow]{cd.get('text', 'N/A')}[/bold yellow] ({c['remaining_hours']:.1f} hours remaining of {c['total_cycle_hours']:.0f}h cycle)
[bold]Actual Consumption:[/bold] [bold cyan]{m['used_percent']:.1f}%[/bold cyan] (Remaining: [bold]{m['remaining_percent']:.1f}%[/bold])
[bold]Ideal Target Usage:[/bold] [bold yellow]{m['ideal_used_percent']:.1f}%[/bold yellow] (Linear 100% pace)
[bold]Pacing Variance (Delta):[/bold] [{delta_color}]{delta_sign}{m['variance_delta_percent']:.1f}%[/{delta_color}] -> [bold {delta_color}]{s['summary']}[/bold {delta_color}]

[bold]Required Burn Rate:[/bold] [bold white]{m['target_burn_rate_percent_hr']:.2f}% / hour[/bold white] (to hit 100% at reset)
[bold]Current Velocity:[/bold] [dim]{m['current_velocity_percent_hr']:.2f}% / hour[/dim]
[bold]Projected Exhaustion:[/bold] [bold]{m['projected_exhaustion_time'] or 'Will not exhaust before reset'}[/bold]
"""
    console.print(Panel(pacing_text.strip(), title=f"{title_prefix} Pacing Analysis", border_style="cyan"))

def main():
    parser = argparse.ArgumentParser(description="Antigravity agy Usage Tracker CLI")
    parser.add_argument("command", choices=["status", "poll", "pacing"], nargs="?", default="status")
    args = parser.parse_args()

    if args.command == "poll":
        init_db(DB_PATH)
        console.print("[cyan]Polling agy usage right now...[/cyan]")
        snaps = collect_and_store(DB_PATH)
        console.print(f"[green]Successfully fetched {len(snaps)} snapshots![/green]")
        show_status()
    elif args.command == "status" or args.command == "pacing":
        show_status()

if __name__ == "__main__":
    main()
