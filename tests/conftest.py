import importlib.util
import sys
from pathlib import Path

import pytest

from ihc.hid.fake import FakeBackend, FakeChip

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hid():
    with FakeBackend(timeout=0.3) as backend:
        yield backend


@pytest.fixture
def chip_backend():
    """Factory for FakeBackend with custom chip / options; closes everything afterwards."""
    made = []

    def make(chip: FakeChip | None = None, **kwargs) -> FakeBackend:
        kwargs.setdefault("timeout", 0.3)
        backend = FakeBackend(chip, **kwargs)
        made.append(backend)
        return backend

    yield make
    for backend in made:
        backend.close()


def load_tool(name: str):
    """Import a script from tools/ as a module."""
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tools_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
