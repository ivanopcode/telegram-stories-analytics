"""Command line entry point for Telegram Stories analytics."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

from .analysis import AnalysisConfig, build_analysis, render_markdown, write_analysis_json, write_markdown
from .auth import (
    AuthError,
    authorized_client,
    delete_profile,
    load_credentials,
    login_interactive,
    resolve_profile,
    status as auth_status,
)
from .exporter import ExportConfig, export_my_stories
from .html_report import write_html


DEFAULT_OUTPUT_DIR = Path("exports")
DEFAULT_FULL_JSON = "my-telegram-stories-full.json"
DEFAULT_ANALYSIS_JSON = "my-telegram-stories-analysis.json"
DEFAULT_REPORT_MD = "my-telegram-stories-analysis.md"
DEFAULT_REPORT_HTML = "my-telegram-stories-analysis.html"


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_recent_days(value: str) -> list[int]:
    try:
        days = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not days or any(item <= 0 for item in days):
        raise argparse.ArgumentTypeError("days must be positive integers")
    return days


def analysis_config(args: argparse.Namespace) -> AnalysisConfig:
    return AnalysisConfig(
        top_limit=args.top_limit,
        regularity_threshold=args.regularity_threshold,
        sliding_window_fraction=args.sliding_window_fraction,
        sliding_window_min=args.sliding_window_min,
        recent_days=args.recent_days,
        min_reaction_rate_views=args.min_reaction_rate_views,
        min_fast_regular_views=args.min_fast_regular_views,
        lapsed_prior_min_views=args.lapsed_prior_min_views,
        new_viewer_min_views=args.new_viewer_min_views,
    )


def resolve_output(output_dir: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if path.parent == Path("."):
        return output_dir / path
    return Path.cwd() / path


def add_analysis_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--analysis-json", default=DEFAULT_ANALYSIS_JSON, help="Analysis JSON path or filename.")
    parser.add_argument("--report-md", default=DEFAULT_REPORT_MD, help="Markdown report path or filename.")
    parser.add_argument("--report-html", default=DEFAULT_REPORT_HTML, help="HTML report path or filename.")
    parser.add_argument("--top-limit", type=int, default=50, help="Rows kept in top lists.")
    parser.add_argument("--regularity-threshold", type=float, default=0.80)
    parser.add_argument("--sliding-window-fraction", type=float, default=0.10)
    parser.add_argument("--sliding-window-min", type=int, default=10)
    parser.add_argument("--recent-days", type=parse_recent_days, default=[30, 90, 180])
    parser.add_argument("--min-reaction-rate-views", type=int, default=20)
    parser.add_argument("--min-fast-regular-views", type=int, default=50)
    parser.add_argument("--lapsed-prior-min-views", type=int, default=20)
    parser.add_argument("--new-viewer-min-views", type=int, default=3)


async def cmd_auth_login(args: argparse.Namespace) -> None:
    profile = resolve_profile(args.profile)
    result = await login_interactive(profile, args.api_id, args.api_hash, args.phone)
    print_json(result)


async def cmd_auth_status(args: argparse.Namespace) -> None:
    profile = resolve_profile(args.profile)
    result = await auth_status(profile, args.auth_source)
    print_json(result)


def cmd_auth_logout(args: argparse.Namespace) -> None:
    profile = resolve_profile(args.profile)
    print_json({"profile": profile, "removed": delete_profile(profile)})


async def cmd_export(args: argparse.Namespace) -> None:
    profile = resolve_profile(args.profile)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    credentials = load_credentials(profile, args.auth_source)
    client = await authorized_client(credentials)
    try:
        export_config = ExportConfig(
            archive_page_limit=args.archive_page_limit,
            view_page_limit=args.view_page_limit,
            limit_stories=args.limit_stories,
            skip_links=args.skip_links,
            skip_viewers=args.skip_viewers,
            progress=args.progress,
        )
        payload = await export_my_stories(client, credentials, export_config)
    finally:
        await client.disconnect()

    config = analysis_config(args)
    payload["analytics"] = build_analysis(payload, config)

    full_json = resolve_output(output_dir, args.full_json)
    analysis_json = resolve_output(output_dir, args.analysis_json)
    report_md = resolve_output(output_dir, args.report_md)
    report_html = resolve_output(output_dir, args.report_html)

    full_json.parent.mkdir(parents=True, exist_ok=True)
    full_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_reports(payload["analytics"], config, source_file=full_json, analysis_json=analysis_json, report_md=report_md, report_html=report_html)

    result = {
        "full_json": str(full_json),
        "analysis_json": str(analysis_json),
        "report_md": str(report_md),
        "report_html": str(report_html),
        "stories_exported": len(payload["stories"]),
        "unique_viewers": payload["analytics"]["summary"]["unique_viewers"],
        "regular_threshold_count": len(payload["analytics"]["regularity"]["all_time_80_pct"]),
        "link_errors": payload["export_warnings"]["link_errors"],
        "viewer_errors": len(payload["export_warnings"]["viewer_errors"]),
    }
    print_json(result)
    if args.open:
        webbrowser.open(report_html.resolve().as_uri())


def cmd_analyze(args: argparse.Namespace) -> None:
    source = args.source.expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    config = analysis_config(args)
    analysis = build_analysis(payload, config)
    payload["analytics"] = analysis
    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else source.parent

    analysis_json = resolve_output(output_dir, args.analysis_json)
    report_md = resolve_output(output_dir, args.report_md)
    report_html = resolve_output(output_dir, args.report_html)
    write_reports(analysis, config, source_file=source, analysis_json=analysis_json, report_md=report_md, report_html=report_html)

    result = {
        "source": str(source),
        "analysis_json": str(analysis_json),
        "report_md": str(report_md),
        "report_html": str(report_html),
        "stories": analysis["summary"]["story_count"],
        "unique_viewers": analysis["summary"]["unique_viewers"],
        "regular_threshold_count": len(analysis["regularity"]["all_time_80_pct"]),
    }
    print_json(result)
    if args.open:
        webbrowser.open(report_html.resolve().as_uri())


def write_reports(
    analysis: dict[str, Any],
    config: AnalysisConfig,
    *,
    source_file: Path,
    analysis_json: Path,
    report_md: Path,
    report_html: Path,
) -> None:
    write_analysis_json(analysis_json, analysis, source_file)
    write_markdown(report_md, analysis, source_file, config)
    write_html(report_html, analysis, source_file=str(source_file))


def cmd_html(args: argparse.Namespace) -> None:
    source = args.analysis_json.expanduser().resolve()
    analysis = json.loads(source.read_text(encoding="utf-8"))
    output = args.output.expanduser().resolve() if args.output else source.with_suffix(".html")
    write_html(output, analysis, source_file=str(source))
    print_json({"analysis_json": str(source), "report_html": str(output)})
    if args.open:
        webbrowser.open(output.as_uri())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Telegram Stories viewers and build analytics reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Manage Telegram credentials.")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)

    login_parser = auth_subparsers.add_parser("login", help="Interactive Telegram login stored in system keyring.")
    login_parser.add_argument("--profile")
    login_parser.add_argument("--api-id", type=int)
    login_parser.add_argument("--api-hash")
    login_parser.add_argument("--phone")
    login_parser.set_defaults(handler=cmd_auth_login)

    status_parser = auth_subparsers.add_parser("status", help="Check Telegram authorization.")
    status_parser.add_argument("--profile")
    status_parser.add_argument("--auth-source", choices=["auto", "keyring", "telegram-telethon", "env"], default="auto")
    status_parser.set_defaults(handler=cmd_auth_status)

    logout_parser = auth_subparsers.add_parser("logout", help="Remove credentials from this tool's keyring service.")
    logout_parser.add_argument("--profile")
    logout_parser.set_defaults(handler=cmd_auth_logout)

    export_parser = subparsers.add_parser("export", help="Export stories, viewers, and reports.")
    export_parser.add_argument("--profile")
    export_parser.add_argument("--auth-source", choices=["auto", "keyring", "telegram-telethon", "env"], default="auto")
    export_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    export_parser.add_argument("--full-json", default=DEFAULT_FULL_JSON)
    export_parser.add_argument("--archive-page-limit", type=int, default=100)
    export_parser.add_argument("--view-page-limit", type=int, default=100)
    export_parser.add_argument("--limit-stories", type=int, help="Debug option: export only newest N stories.")
    export_parser.add_argument("--skip-links", action="store_true")
    export_parser.add_argument("--skip-viewers", action="store_true")
    export_parser.add_argument("--progress", action="store_true")
    export_parser.add_argument("--open", action="store_true", help="Open generated HTML report.")
    add_analysis_args(export_parser)
    export_parser.set_defaults(handler=cmd_export)

    analyze_parser = subparsers.add_parser("analyze", help="Rebuild reports from an existing full export.")
    analyze_parser.add_argument("source", type=Path)
    analyze_parser.add_argument("--output-dir", type=Path)
    analyze_parser.add_argument("--open", action="store_true", help="Open generated HTML report.")
    add_analysis_args(analyze_parser)
    analyze_parser.set_defaults(handler=cmd_analyze)

    html_parser = subparsers.add_parser("html", help="Render an HTML report from analysis JSON.")
    html_parser.add_argument("analysis_json", type=Path)
    html_parser.add_argument("--output", type=Path)
    html_parser.add_argument("--open", action="store_true")
    html_parser.set_defaults(handler=cmd_html)

    open_parser = subparsers.add_parser("open", help="Render and open an HTML report from analysis JSON.")
    open_parser.add_argument("analysis_json", type=Path)
    open_parser.add_argument("--output", type=Path)
    open_parser.set_defaults(handler=lambda args: cmd_html(argparse.Namespace(**vars(args), open=True)))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except AuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
