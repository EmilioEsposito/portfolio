"""Phone numbers come from settings, and the lead policy fills in the team line."""

import pytest

from api.src.utils.phone_config import (
    PhoneConfigError,
    optional_phone_list,
    required_us_phone,
    us_phone_display,
)
from api.src.zillow_leads import agent as agent_module


def test_required_us_phone_reads_e164(monkeypatch):
    monkeypatch.setenv("TEST_LINE", " +14125550123 ")
    assert required_us_phone("TEST_LINE") == "+14125550123"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_us_phone_names_a_missing_variable(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TEST_LINE", raising=False)
    else:
        monkeypatch.setenv("TEST_LINE", value)
    with pytest.raises(PhoneConfigError, match="TEST_LINE is not set"):
        required_us_phone("TEST_LINE")


@pytest.mark.parametrize("value", ["412-555-0177", "4125550177", "+442071234567", "+141299901"])
def test_required_us_phone_rejects_other_forms_without_repeating_them(monkeypatch, value):
    monkeypatch.setenv("TEST_LINE", value)
    with pytest.raises(PhoneConfigError, match="TEST_LINE must be a US number") as error:
        required_us_phone("TEST_LINE")
    assert value not in str(error.value)


def test_optional_phone_list(monkeypatch):
    monkeypatch.delenv("TEST_LIST", raising=False)
    assert optional_phone_list("TEST_LIST") == []
    monkeypatch.setenv("TEST_LIST", "+14125550101, +442071234567,")
    assert optional_phone_list("TEST_LIST") == ["+14125550101", "+442071234567"]
    monkeypatch.setenv("TEST_LIST", "+14125550101,412-555-0102")
    with pytest.raises(PhoneConfigError, match="TEST_LIST"):
        optional_phone_list("TEST_LIST")


def test_us_phone_display():
    assert us_phone_display("+14125550123") == "412-555-0123"


def test_policy_signature_shows_the_configured_team_line(monkeypatch):
    monkeypatch.setenv("SERNIA_TEAM_PHONE", "+14125550188")
    policy = agent_module.load_policy()
    assert (
        "  Sernia Capital Team\n  all@serniacapital.com\n  412-555-0188 (extension 1)\n" in policy
    )
    assert agent_module.TEAM_PHONE_PLACEHOLDER not in policy
    # The tracked file holds only the placeholder.
    assert agent_module.TEAM_PHONE_PLACEHOLDER in agent_module.POLICY_PATH.read_text()


def test_every_reader_gets_the_filled_policy(monkeypatch):
    from api.src.sernia_ai.lead_policy import lead_policy_text

    monkeypatch.setenv("SERNIA_TEAM_PHONE", "+14125550188")
    policy = agent_module.load_policy()
    assert policy in agent_module.load_instructions()  # lead agent, evals and judge
    assert lead_policy_text().endswith(policy)  # general agent's skill


def test_policy_will_not_render_without_the_team_line(monkeypatch):
    monkeypatch.delenv("SERNIA_TEAM_PHONE", raising=False)
    with pytest.raises(PhoneConfigError, match="SERNIA_TEAM_PHONE is not set"):
        agent_module.load_policy()
    with pytest.raises(PhoneConfigError, match="SERNIA_TEAM_PHONE is not set"):
        agent_module.load_instructions()
