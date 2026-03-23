"""CLI entry point for CloudWire."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

_REQUIRED_PACKAGES = ["click", "uvicorn", "fastapi", "boto3", "pydantic", "networkx", "multipart", "hcl2"]


def _check_dependencies() -> None:
    """Verify all required packages are importable. Print a helpful message if not."""
    missing = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(
            f"Error: missing required packages: {', '.join(missing)}\n"
            f"\n"
            f"  pip install cloudwire\n"
            f"\n"
            f"This installs all dependencies automatically. If you installed\n"
            f"from source, run: pip install -e .\n",
            file=sys.stderr,
        )
        sys.exit(1)


_check_dependencies()

import click  # noqa: E402
import uvicorn  # noqa: E402

from . import __version__  # noqa: E402


def _check_for_update(current: str, result: list) -> None:
    """Fetch the latest version from PyPI and store it in result[0]. Runs in a background thread."""
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(
            "https://pypi.org/pypi/cloudwire/json", timeout=3
        ) as resp:
            data = json.loads(resp.read())
            result.append(data["info"]["version"])
    except Exception:
        pass


def _print_update_hint(current: str, result: list) -> None:
    """Print an update hint if a newer version is available."""
    if result and result[0] != current:
        click.echo(f"  update available  {current}  →  {result[0]}")
        click.echo(f"  run: pip install --upgrade cloudwire\n")


@click.command()
@click.option("--port", default=8080, show_default=True, help="Local port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address. Never expose 0.0.0.0 on untrusted networks.")
@click.option("--profile", default=None, envvar="AWS_PROFILE", help="AWS credentials profile from ~/.aws/credentials.")
@click.option("--region", default="us-east-1", show_default=True, envvar="AWS_DEFAULT_REGION", help="Default AWS region.")
@click.option("--no-browser", is_flag=True, default=False, help="Do not open the browser automatically.")
@click.option("--print-url", is_flag=True, default=False, help="Print the URL to stdout and exit (useful for SSH tunnels).")
@click.version_option(version=__version__, prog_name="cloudwire")
def main(port: int, host: str, profile: str | None, region: str, no_browser: bool, print_url: bool) -> None:
    """Scan and visualize your AWS infrastructure as an interactive graph.

    AWS credentials are read from the standard credential chain:
    environment variables, ~/.aws/credentials profiles, and instance metadata.
    Tools like saml2aws, aws-vault, and aws sso login all write to ~/.aws/credentials
    and work automatically.

    \b
    Examples:
      cloudwire                              # use default AWS profile
      cloudwire --profile staging            # use a named profile
      cloudwire --region eu-west-1           # override region
      cloudwire --port 9000 --no-browser     # custom port, skip auto-open
      cloudwire --print-url                  # print URL only (SSH tunnel use case)
    """
    url = f"http://localhost:{port}"

    # --print-url: just output the URL and exit (for scripting / SSH tunnels)
    if print_url:
        click.echo(url)
        return

    if profile:
        os.environ["AWS_PROFILE"] = profile

    # Only set region if not already in environment
    os.environ.setdefault("AWS_DEFAULT_REGION", region)

    # Start background version check — finishes before uvicorn prints its own output
    _update_result: list = []
    _update_thread = threading.Thread(
        target=_check_for_update, args=(__version__, _update_result), daemon=True
    )
    _update_thread.start()
    _update_thread.join(timeout=3)  # wait at most 3s so startup never hangs

    click.echo("")
    click.echo(f"  cloudwire {__version__}")
    _print_update_hint(__version__, _update_result)
    if host not in ("127.0.0.1", "localhost", "::1"):
        click.secho(
            f"  ⚠ WARNING: Binding to {host} — CloudWire has no authentication.\n"
            f"    Any client that can reach this port gets full access to your AWS\n"
            f"    account topology and can trigger scans. Only use on trusted networks.",
            fg="yellow",
            err=True,
        )
    click.echo(f"  Running at  →  {url}")
    click.echo("  Press Ctrl+C to stop.\n")

    if not no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(
            "cloudwire.app.main:app",
            host=host,
            port=port,
            log_level="warning",
        )
    except KeyboardInterrupt:
        click.echo("\n  Stopped.")
    except OSError as exc:
        raise click.ClickException(
            f"Failed to start server on {host}:{port} — {exc}. "
            f"Try a different port with --port <number>."
        ) from exc
