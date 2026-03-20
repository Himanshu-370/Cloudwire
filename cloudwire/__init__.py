"""CloudWire — scan and visualize your AWS infrastructure."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("cloudwire")
except PackageNotFoundError:
    __version__ = "0.2.5"  # fallback when running from source without pip install
