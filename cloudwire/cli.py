"""CLI entry point for CloudWire."""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

import click
import uvicorn

from . import __version__


def _port_is_available(host: str, port: int) -> bool:
    """Return True if the port is free to bind on the given host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


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

    # Port conflict check — fail fast with a clear message
    if not _port_is_available(host, port):
        raise click.ClickException(
            f"Port {port} is already in use. Try a different port with --port <number>."
        )

    click.echo("")
    click.echo(f"  cloudwire {__version__}")
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
