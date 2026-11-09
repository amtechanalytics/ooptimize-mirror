"""Loads this repository's study (paper/) as package 'ooptpaper' for the study's own tests.

tests/conftest.py runs every test in a throwaway copy whose paper/, config/, pilot/ and certificate/ are the
frozen demo study (so the harness tests never depend on a study). The study tests therefore import the real
paper/ from the repository under another name; its modules use relative imports, so this is exact."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load():
    if "ooptpaper" not in sys.modules:
        spec = importlib.util.spec_from_file_location("ooptpaper", REPO / "paper" / "__init__.py",
                                                      submodule_search_locations=[str(REPO / "paper")])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ooptpaper"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["ooptpaper"]


def real_config():
    """The study's own config (the copy under test holds the demo config)."""
    from rh import config
    return config.load(root=REPO)


load()
