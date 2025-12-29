from supervisor.security import validate_kill_switch_challenge


def test_kill_switch_challenge_validation():
    now = 1000.0
    challenge = {"challenge_id": "abc", "expires_at": now + 10}

    assert validate_kill_switch_challenge(challenge, "abc", now) is None
    assert validate_kill_switch_challenge(challenge, "wrong", now) == "challenge_mismatch"
    assert validate_kill_switch_challenge(challenge, "abc", now + 20) == "challenge_expired"
