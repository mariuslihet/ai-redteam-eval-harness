import json

import pytest

from redteam_eval.archive import load_archive
from redteam_eval.cli import main
from redteam_eval.models import Response, RunConfig, Thresholds
from redteam_eval.regression import RegressionLimits, compare_runs
from redteam_eval.replay import attack_plans, plan_digest, user_segments
from redteam_eval.reporting import write_reports
from redteam_eval.runner import Runner
from tests.doubles import DeterministicBackend


@pytest.fixture
async def recorded(tmp_path):
    config = RunConfig(cases_per_family=1, max_turns=2)
    results = await Runner(config).run()
    write_reports(tmp_path / "recorded", results, config, Thresholds())
    return tmp_path / "recorded" / "telemetry.json"


async def test_replay_generates_fresh_responses_preserves_messages_and_disables_attacker(
    recorded, tmp_path
):
    source = load_archive(recorded)
    config = source.config.model_copy(deep=True)
    config.replay_plan_digest = plan_digest(source)
    backend = DeterministicBackend(script=[Response(text="new target completion")])

    def forbidden(request):
        raise AssertionError("Recorded replay must never generate attacker follow-ups")

    results = await Runner(
        config, backend=backend, attacker=DeterministicBackend(callback=forbidden)
    ).run(replay=source)
    assert backend.requests
    for old, new in zip(source.results, results, strict=True):
        assert [t.user_messages for t in old.baseline.turns] == [
            t.user_messages for t in new.baseline.turns
        ]
        assert [t.prompt for t in old.baseline.turns] == [t.prompt for t in new.hardened.turns]
        for turn in new.baseline.turns + new.hardened.turns:
            assert turn.response.text == "new target completion"
            assert turn.attacker_usage.input_tokens == 0 and turn.attacker_usage.output_tokens == 0
    write_reports(tmp_path / "replayed", results, config, Thresholds())
    replayed = load_archive(tmp_path / "replayed" / "telemetry.json")
    assert replayed.execution_mode == "recorded_replay"
    assert (
        compare_runs(source, replayed, RegressionLimits())["attack_prompt_policy"]
        == "recorded replay"
    )


async def test_replay_completes_recorded_plan_even_if_new_target_succeeds_early(recorded):
    source = load_archive(recorded)
    config = source.config.model_copy(deep=True)
    config.replay_plan_digest = plan_digest(source)
    secret = source.results[0].scenario.canaries[0]
    results = await Runner(
        config, backend=DeterministicBackend(script=[Response(text=secret)])
    ).run(replay=source)
    assert len(results[0].baseline.turns) == len(source.results[0].baseline.turns) == 2


@pytest.mark.parametrize("damage", ["digest", "world", "budget", "absent_source"])
async def test_replay_rejects_mismatch_before_generation(recorded, damage):
    source = load_archive(recorded)
    config = source.config.model_copy(deep=True)
    config.replay_plan_digest = plan_digest(source)
    if damage == "digest":
        config.replay_plan_digest = "a" * 64
    elif damage == "world":
        source.results[0].scenario.document += "modified"
    elif damage == "budget":
        config.max_tokens += 1
    backend = DeterministicBackend()
    with pytest.raises(ValueError):
        await Runner(config, backend=backend).run(
            replay=None if damage == "absent_source" else source
        )
    assert not backend.requests


async def test_checkpoint_cannot_confuse_adaptive_and_recorded_replay(recorded, tmp_path):
    source = load_archive(recorded)
    state = tmp_path / "state"
    await Runner(source.config).run(state)
    config = source.config.model_copy(deep=True)
    config.replay_plan_digest = plan_digest(source)
    with pytest.raises(ValueError, match="differs"):
        await Runner(config).run(state, replay=source)
    other = tmp_path / "replay-state"
    backend = DeterministicBackend()
    runner = Runner(config, backend=backend)
    results = await runner.run(other, replay=source)
    calls = len(backend.requests)
    assert await runner.run(other, replay=source) == results
    assert len(backend.requests) == calls


def test_legacy_split_boundaries_recovered_from_native_and_provider_envelopes(recorded):
    source = load_archive(recorded)
    turn = source.results[0].baseline.turns[-1].model_copy(deep=True)
    turn.user_messages = []
    turn.mutation = "split"
    turn.prompt = "first segment\nsecond segment"
    for content in ("text", "blocks"):

        def user(text):
            return {
                "role": "user",
                "content": text if content == "text" else [{"type": "text", "text": text}],
            }

        turn.response.request_envelope = {
            "body": {
                "messages": [
                    user("context"),
                    {"role": "assistant", "content": "original target response"},
                    user("first segment"),
                    user("second segment"),
                ]
            }
        }
        assert user_segments(turn) == ["first segment", "second segment"]
    turn.response.request_envelope = {}
    with pytest.raises(ValueError, match="split mutation"):
        user_segments(turn)


def test_replay_plan_digest_changes_with_user_segments(recorded):
    source = load_archive(recorded)
    first = plan_digest(source)
    assert first == plan_digest(source)
    source.results[0].baseline.turns[0].user_messages[0] += "new"
    source.results[0].baseline.turns[0].prompt += "new"
    assert first != plan_digest(source)
    assert len(attack_plans(source)) == 6


def test_legacy_first_turn_boundaries_and_segmentation_mismatch(recorded):
    source = load_archive(recorded)
    turn = source.results[0].baseline.turns[0].model_copy(deep=True)
    turn.prompt = "first\nsecond"
    turn.user_messages = []
    turn.mutation = "split"
    messages = [{"role": "user", "content": text} for text in ("context", "first", "second")]
    for prefix in ([], [{"role": "system", "content": "private original system"}]):
        turn.response.request_envelope = {"body": {"messages": prefix + messages}}
        assert user_segments(turn) == ["first", "second"]
    turn.user_messages = ["incorrect"]
    with pytest.raises(ValueError, match="boundaries"):
        user_segments(turn)


def test_replay_cli_uses_saved_plan_and_preserves_archive(recorded, tmp_path, capsys):
    before = recorded.read_bytes()
    assert (
        main(
            [
                "--replay-from",
                str(recorded),
                "--compare-to",
                str(recorded),
                "--output",
                str(tmp_path / "fresh"),
            ]
        )
        == 0
    )
    assert recorded.read_bytes() == before
    assert '"inference_requested": true' in capsys.readouterr().out
    report = json.loads((tmp_path / "fresh" / "telemetry.json").read_text())
    assert report["config"]["replay_plan_digest"]
    assert main(["--replay-from", str(recorded), "--seed", "3"]) == 2
    assert main(["--replay-from", str(recorded), "--output", str(recorded.parent)]) == 2
