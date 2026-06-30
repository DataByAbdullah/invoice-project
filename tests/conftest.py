"""Pytest configuration and shared fixtures."""

import pytest
import asyncio


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for all async tests in a session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
