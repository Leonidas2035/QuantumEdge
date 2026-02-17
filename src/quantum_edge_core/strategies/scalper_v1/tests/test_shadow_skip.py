from bot.engine.decision_types import Decision, DecisionAction, DecisionDirection
from bot.run_bot import _shadow_skip


def test_shadow_skip_blocks_execution():
    decision = Decision(
        action=DecisionAction.ENTER, direction=DecisionDirection.LONG, size=1.0
    )
    assert _shadow_skip(True, decision)
    assert not _shadow_skip(False, decision)
