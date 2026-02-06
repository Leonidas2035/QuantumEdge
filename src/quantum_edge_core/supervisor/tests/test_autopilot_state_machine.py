from supervisor.autopilot.state_machine import AutopilotState, AutopilotStateMachine


def test_state_machine_dwell_and_transition_limit():
    sm = AutopilotStateMachine(["OFF", "SHADOW", "DEGRADED"], min_dwell_sec=10, max_transitions_per_hour=1)
    state = AutopilotState(state="OFF", last_transition_ts=0.0, transitions=[])
    updated = sm.next_state(state, "SHADOW", now=5.0)
    assert updated.state == "OFF"
    updated = sm.next_state(state, "SHADOW", now=15.0)
    assert updated.state == "SHADOW"
    updated2 = sm.next_state(updated, "DEGRADED", now=20.0)
    assert updated2.state == "SHADOW"
