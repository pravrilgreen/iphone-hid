import importlib

import pytest

from ihc.hid.fake import FakeBackend, FakeChip


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
    """One of the lab tools (ihc.tools.<name>)."""
    return importlib.import_module(f"ihc.tools.{name}")
