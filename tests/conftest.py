"""Offline test harness; SauceNAO is mocked with respx in the tests."""

from collections.abc import AsyncGenerator

import httpx
import pytest


@pytest.fixture
async def http() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client
