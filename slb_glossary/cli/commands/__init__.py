"""Individual command implementations wired into `slb_glossary.cli.main`."""

from slb_glossary.cli.commands.compare import compare
from slb_glossary.cli.commands.config import config
from slb_glossary.cli.commands.define import define
from slb_glossary.cli.commands.install import install
from slb_glossary.cli.commands.local import local
from slb_glossary.cli.commands.mcp import mcp
from slb_glossary.cli.commands.random_term import random_term
from slb_glossary.cli.commands.related import related
from slb_glossary.cli.commands.search import search
from slb_glossary.cli.commands.sync import sync
from slb_glossary.cli.commands.terms import terms
from slb_glossary.cli.commands.topics import topics
from slb_glossary.cli.commands.urls import urls

__all__ = [
    "compare",
    "config",
    "define",
    "install",
    "local",
    "mcp",
    "random_term",
    "related",
    "search",
    "sync",
    "terms",
    "topics",
    "urls",
]
