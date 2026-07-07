import pytest

from app.services.agent_config import get_agent_config, get_mood_modifier, get_mood_name


@pytest.mark.parametrize("mood, expected_name", [(0, "cemas"), (1, "sedih"), (2, "biasa"), (3, "baik")])
def test_get_mood_name_valid(mood, expected_name):
    assert get_mood_name(mood) == expected_name


@pytest.mark.parametrize("mood, expected_name", [(-5, "cemas"), (-1, "cemas"), (4, "baik"), (99, "baik")])
def test_get_mood_name_clamps_out_of_range(mood, expected_name):
    assert get_mood_name(mood) == expected_name


@pytest.mark.parametrize("mood", [0, 1, 2, 3])
def test_get_mood_modifier_returns_nonempty_string(mood):
    modifier = get_mood_modifier(mood)
    assert isinstance(modifier, str)
    assert modifier.strip() != ""


def test_get_mood_modifier_differs_per_mood():
    modifiers = {mood: get_mood_modifier(mood) for mood in range(4)}
    assert len(set(modifiers.values())) == 4


def test_get_agent_config_defaults_to_emily_when_unknown():
    assert get_agent_config("unknown-agent")["name"] == "Emily"


def test_get_agent_config_normalizes_case_and_whitespace():
    assert get_agent_config(" KAI ")["name"] == "Kai"
