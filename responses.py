"""Error response helper.

Success responses are returned as plain dicts by each route, since the
brief specifies an exact, per-endpoint response body (no shared envelope).
Errors use a single flat shape: {"error": "message"}.
"""
from flask import jsonify


def err(message: str, status: int):
    """Build a standard error response: {"error": "..."} with an HTTP status code."""
    return jsonify({"error": message}), status
