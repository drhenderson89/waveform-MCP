"""MCP tool implementations.

Each module exposes:
  - a list of `Tool` objects (name, description, JSON schema)
  - an async dispatcher `handle(name, args) -> dict` that looks up and runs
    the right impl

`server.py` aggregates them and serves the union over MCP.
"""
