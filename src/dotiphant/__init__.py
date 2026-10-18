"""Dotiphant: your Telegram chats, searchable on your machine and readable by your AI assistant."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dotiphant")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0+unknown"
