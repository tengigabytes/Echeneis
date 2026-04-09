"""CLI entry point for the ech command.

Provides direct command-line access to the Echeneis gateway
for local invocation without going through the bot or MCP server.
"""

import click


@click.group()
def cli():
    """Echeneis — Unified AI gateway CLI."""
    pass
