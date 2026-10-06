import os

from core.db import resolve_host


def test_resolve_host_falls_back_to_localhost_outside_docker():
    assert resolve_host("postgres") == "localhost"
    assert resolve_host("evgrid-postgres") == "localhost"
    assert resolve_host("db") == "localhost"


def test_resolve_host_passes_through_unknown_hosts():
    assert resolve_host("db.example.com") == "db.example.com"


def test_resolve_host_defaults_to_localhost_when_empty():
    assert resolve_host(None) == "localhost"
    assert resolve_host("") == "localhost"


def test_resolve_host_keeps_docker_name_inside_a_container():
    original_exists = os.path.exists
    os.path.exists = lambda p: p == "/.dockerenv" or original_exists(p)
    try:
        assert resolve_host("postgres") == "postgres"
    finally:
        os.path.exists = original_exists
