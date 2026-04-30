"""mdex package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mdex-cli")
except PackageNotFoundError:
    __version__ = "0.2.0"
