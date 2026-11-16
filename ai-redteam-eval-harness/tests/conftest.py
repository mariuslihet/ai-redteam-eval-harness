"""All ordinary tests fail on socket egress; local-model tests opt in explicitly."""

import socket
import sys

import pytest


@pytest.fixture(autouse=True)
def deny_network(request, monkeypatch):
    if request.node.get_closest_marker("local_llm"):
        return

    import redteam_eval.runner
    from tests.doubles import DeterministicBackend

    if not request.node.module.__name__.split(".")[-1].startswith("test_dashboard"):
        monkeypatch.setattr(
            redteam_eval.runner, "make_backend", lambda config: DeterministicBackend()
        )

    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access in offline test")

    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        # Windows implements asyncio's internal wakeup socketpair using a local
        # connect. Permit only that stdlib call site, not arbitrary local traffic.
        caller = sys._getframe(1)
        if (
            caller.f_code.co_name == "_fallback_socketpair"
            and caller.f_globals.get("__name__") == "socket"
        ):
            return original_connect(sock, address)
        return blocked(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def scenario():
    from redteam_eval.synthesis import synthesize

    return synthesize(73, 1)[0]
