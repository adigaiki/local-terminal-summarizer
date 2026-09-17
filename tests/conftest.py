"""Tests must never bind or connect outside loopback."""
import socket

import pytest

from summarizer.net import is_loopback_host


@pytest.fixture(autouse=True)
def loopback_only_sockets(monkeypatch):
    connect = socket.socket.connect
    bind = socket.socket.bind
    resolve = socket.getaddrinfo

    def check(address):
        if isinstance(address, tuple):
            assert is_loopback_host(address[0]), 'test attempted non-loopback network use'

    def guarded_connect(sock, address):
        check(address)
        return connect(sock, address)

    def guarded_bind(sock, address):
        check(address)
        return bind(sock, address)

    def guarded_resolve(host, *args, **kwargs):
        assert is_loopback_host(host), 'test attempted external DNS resolution'
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket.socket, 'bind', guarded_bind)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded_resolve)
