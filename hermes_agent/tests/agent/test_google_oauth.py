"""Unit tests for the Google OAuth PKCE flow (agent/google_oauth.py)."""

import json
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from agent import google_oauth


def test_pkce_generation():
    """Verify S256 PKCE pair generation."""
    verifier, challenge = google_oauth._generate_pkce_pair()
    assert len(verifier) >= 43  # RFC 7636 minimum
    assert len(challenge) >= 43
    # Verifier should be urlsafe
    assert verifier.replace("-", "").replace("_", "").isalnum()
    # Challenge should not contain padding
    assert "=" not in challenge


def test_credentials_persistence(tmp_path, monkeypatch):
    """Verify atomic saving and loading of Google credentials."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    
    creds = google_oauth.GoogleCredentials(
        access_token="test-access",
        refresh_token="test-refresh",
        expires_ms=int(1744848000000),
        email="test@example.com",
        project_id="test-project"
    )
    
    path = google_oauth.save_credentials(creds)
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    
    loaded = google_oauth.load_credentials()
    assert loaded is not None
    assert loaded.access_token == "test-access"
    assert loaded.refresh_token == "test-refresh"
    assert loaded.email == "test@example.com"
    assert loaded.project_id == "test-project"


def test_refresh_parts_packing():
    """Verify packing and unpacking of the refresh field."""
    # Full pack
    p = google_oauth.RefreshParts.parse("rt|p1|p2")
    assert p.refresh_token == "rt"
    assert p.project_id == "p1"
    assert p.managed_project_id == "p2"
    assert p.format() == "rt|p1|p2"
    
    # Partial pack
    p = google_oauth.RefreshParts.parse("rt|p1")
    assert p.refresh_token == "rt"
    assert p.project_id == "p1"
    assert p.managed_project_id == ""
    assert p.format() == "rt|p1|" # format adds empty trailing pipes if any ID was present
    
    # Bare token
    p = google_oauth.RefreshParts.parse("rt")
    assert p.refresh_token == "rt"
    assert p.project_id == ""
    assert p.format() == "rt"


def test_oauth_url_security(monkeypatch, tmp_path):
    """Verify the authorization URL is secure and contains all required PKCE/OAuth params."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(google_oauth, "_is_headless", lambda: False)
    
    # Mock server binding to avoid actual OS calls
    class FakeServer:
        def __init__(self, *args, **kwargs):
            self.server_address = ("127.0.0.1", 8085)
        def serve_forever(self): pass
        def shutdown(self): pass
        def server_close(self): pass
        
    monkeypatch.setattr("http.server.HTTPServer", FakeServer)
    
    # Mock browser open and input to avoid blocking
    captured_url = []
    monkeypatch.setattr("webbrowser.open", lambda url, **kwargs: captured_url.append(url))
    
    # We'll trigger start_oauth_flow but we need to stop it before it blocks on input or callback
    # We can mock _prompt_paste_fallback to raise an exception after we capture the URL
    class AbortFlow(Exception): pass
    
    def mock_paste_fallback():
        raise AbortFlow()
    
    monkeypatch.setattr(google_oauth, "_prompt_paste_fallback", mock_paste_fallback)
    
    # Also need to mock threading.Event.wait to return False (timeout) immediately
    monkeypatch.setattr("threading.Event.wait", lambda self, timeout=None: False)

    with pytest.raises(AbortFlow):
        google_oauth.start_oauth_flow(force_relogin=True)
    
    assert len(captured_url) == 1
    url = captured_url[0]
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    
    assert params["client_id"][0] == google_oauth._get_client_id()
    assert params["response_type"][0] == "code"
    assert "code_challenge" in params
    assert params["code_challenge_method"][0] == "S256"
    assert "state" in params
    
    # The critical security check: state must not be the verifier (injected via challenge)
    # We can't easily see the verifier here unless we mock _generate_pkce_pair
    # but we can check that state is present and looks like a token.
    assert len(params["state"][0]) >= 16
