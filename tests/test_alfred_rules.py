"""Alfred follows the specialists' rules: mock figures are always called mock,
no trade advice, and agents can override their model in the catalog."""
import json

import butler
import specialists.engines as engines
from specialists import catalog, safety

CONVERSATION_URL = "/api/v1/marquis/conversation"
CTX = {"performance": "a", "pathway": "b", "blocker": "c", "enhancement": "d"}

MOCK_FINDING = {"name": "Churn & MRR Tracker", "state": "FLAGGED", "mock": True,
                "summary": "MRR is $9,200 against a floor of $10,000. Churn is 4.8%.",
                "flags": [{"value": 9200.0, "limit": 10000.0}, {"value": 4.8, "limit": 5.0}], "items": []}
REAL_FINDING = {"name": "News Scout", "state": "IDLE", "mock": False, "summary": "Markets were flat at 7,773.", "items": []}


# --- 1. mock data is always called mock -------------------------------------------

def test_every_line_of_a_mock_finding_is_tagged_and_the_rule_is_stated():
    block = safety.context_block([dict(MOCK_FINDING, items=[{"text": "Churn near ceiling", "source_url": None}]), REAL_FINDING])
    lines = block.splitlines()
    assert any("Every time you cite a figure" in line and "mock data" in line for line in lines)
    mock_lines = [line for line in lines if "Churn & MRR" in line or "Churn near ceiling" in line]
    assert len(mock_lines) == 2 and all(safety.MOCK_TAG in line for line in mock_lines)
    assert safety.MOCK_TAG not in next(line for line in lines if "News Scout" in line)


def test_no_mock_rule_when_nothing_is_mock():
    block = safety.context_block([REAL_FINDING])
    assert safety.MOCK_TAG not in block and "mock" not in block.lower()


def test_figures_from_mock_runs_are_collected_but_real_ones_are_not():
    figures = safety.mock_figures([MOCK_FINDING, REAL_FINDING])
    assert {"9200", "10000", "4.8"} <= figures
    assert "7773" not in figures


def test_each_sentence_citing_a_mock_figure_says_it_is_mock():
    figures = safety.mock_figures([MOCK_FINDING])
    reply = ("Right so, MRR is at $9,200. The floor is 10000, so that is a breach. "
             "Churn at 4.8 is near its ceiling, and that figure is mock data. Nothing else stands out.")
    out = safety.label_mock_figures(reply, figures)
    assert "MRR is at $9,200. That is mock data." in out
    assert "so that is a breach. That is mock data." in out
    assert out.count("That is mock data") == 2          # the sentence already saying so is left alone
    assert "Nothing else stands out." in out


def test_an_earlier_mock_mention_covers_later_figures_when_all_are_mock():
    figures = safety.mock_figures([MOCK_FINDING])
    real = safety.real_figures([MOCK_FINDING, REAL_FINDING])
    reply = ("So — these figures come from mock data. MRR is at $9,200, under the 10000 floor. "
             "Churn is 4.8, close to its ceiling.")
    out = safety.label_mock_figures(reply, figures, real)
    assert out == reply                                             # nothing appended: already said, nothing real to confuse


def test_a_reply_mixing_mock_and_real_figures_keeps_labelling_the_mock_ones():
    figures = safety.mock_figures([MOCK_FINDING, REAL_FINDING])
    real = safety.real_figures([MOCK_FINDING, REAL_FINDING])
    assert "7773" in real and "7773" not in figures
    reply = ("So — the tracker runs on mock data. MRR is at $9,200. "
             "Markets were flat at 7,773. Churn is 4.8, near its ceiling.")
    out = safety.label_mock_figures(reply, figures, real)
    assert "MRR is at $9,200. That is mock data." in out           # labelled despite the earlier mention
    assert "Churn is 4.8, near its ceiling. That is mock data." in out
    assert "flat at 7,773. That is mock data" not in out            # the real figure is never called mock
    assert out.count("That is mock data") == 2


def test_a_figure_that_is_both_mock_and_real_counts_as_mixed():
    f = [dict(MOCK_FINDING, summary="MRR is 9,200."), dict(REAL_FINDING, summary="Plan was 9,200.")]
    out = safety.label_mock_figures("This is mock data. MRR is 9,200.", safety.mock_figures(f), safety.real_figures(f))
    assert out.endswith("That is mock data.")                      # ambiguous, so label it


def test_conversation_mixed_reply_labels_only_the_mock_figures(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    findings = [MOCK_FINDING, REAL_FINDING]
    monkeypatch.setattr(app_module, "alfred_context", lambda uid: (
        safety.context_block(findings), safety.mock_figures(findings), safety.real_figures(findings)))
    mock_claude["reply"] = "The tracker is on mock data. MRR is 9200. Markets were flat at 7773."
    reply = client.post(CONVERSATION_URL, headers=headers, json={"message": "status?"}).get_json()["butler_response"]
    assert "MRR is 9200. That is mock data." in reply
    assert reply.endswith("flat at 7773.")


def test_conversation_all_mock_reply_is_not_repeated(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    monkeypatch.setattr(app_module, "alfred_context", lambda uid: (
        safety.context_block([MOCK_FINDING]), safety.mock_figures([MOCK_FINDING]), safety.real_figures([MOCK_FINDING])))
    mock_claude["reply"] = "That tracker runs on mock data. MRR is 9200. Churn is 4.8."
    reply = client.post(CONVERSATION_URL, headers=headers, json={"message": "status?"}).get_json()["butler_response"]
    assert reply == "That tracker runs on mock data. MRR is 9200. Churn is 4.8."


def test_reply_without_mock_figures_is_untouched():
    figures = safety.mock_figures([MOCK_FINDING])
    reply = "Nothing needs your attention today. Revenue is 7,773 against plan."
    assert safety.label_mock_figures(reply, figures) == reply
    assert safety.label_mock_figures(reply, set()) == reply


def test_conversation_labels_mock_figures_even_if_the_model_forgets(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    block = safety.context_block([MOCK_FINDING])
    monkeypatch.setattr(app_module, "alfred_context", lambda uid: (block, safety.mock_figures([MOCK_FINDING]), set()))
    mock_claude["reply"] = "MRR sits at 9200 against a floor of 10000. Churn is close."
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "how are the numbers?"}).get_json()
    reply = body["butler_response"]
    assert reply.count("That is mock data") == 1
    assert reply.index("9200") < reply.index("That is mock data")


def test_conversation_does_not_double_label(client, auth_headers, mock_claude, monkeypatch):
    import app as app_module
    _, headers = auth_headers
    monkeypatch.setattr(app_module, "alfred_context", lambda uid: ("ctx", {"9200"}, set()))
    mock_claude["reply"] = "MRR is 9200, though that is mock data. Churn is fine."
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "numbers?"}).get_json()
    assert body["butler_response"] == "MRR is 9200, though that is mock data. Churn is fine."


def test_alfreds_prompt_carries_the_tagged_block_and_the_rule():
    ctx = dict(CTX, specialists=safety.context_block([MOCK_FINDING]))
    prompt = butler.build_system_prompt(ctx, [], business_type="SaaS")
    assert safety.MOCK_TAG in prompt and "say in that same sentence that it is mock data" in prompt
    assert "say in the same sentence that it is mock data" in prompt     # the standing rule too


def test_tracker_and_reviewer_outputs_carry_the_mock_flag(monkeypatch):
    entry = catalog.entry("saas.churn_mrr")
    settings = {"metrics": [{"name": "MRR", "unit": "$", "floor": 1000.0, "ceiling": None}]}
    rows = [{"kind": "metric", "metric": "MRR", "value": 5000, "occurred_at": "2026-10-01", "body": {"mock": True}}]
    out = engines.run_tracker(entry, settings, "m", rows)["output"]
    assert out["mock"] is True and "mock data" in out["data_note"]
    real = [dict(rows[0], body={})]
    assert engines.run_tracker(entry, settings, "m", real)["output"]["mock"] is False

    monkeypatch.setattr(engines, "_ask_json", lambda *a, **k: ({"summary": "s", "patterns": []}, 1, 1))
    logs = [{"kind": "log", "occurred_at": "2026-10-01", "body": {"text": "x", "mock": True}}]
    assert engines.run_reviewer(catalog.entry("saas.feedback_reviewer"), {}, "m", logs)["output"]["mock"] is True


# --- 2. Alfred keeps the no-advice rules -------------------------------------------

def test_trading_vocabulary_has_no_advice_terms():
    for vocab in butler.BUSINESS_VOCABULARY.values():
        assert "position sizing" not in vocab.lower() and "portfolio allocation" not in vocab.lower()
    assert "drawdown" in butler.BUSINESS_VOCABULARY["Trading"]


def test_alfreds_prompt_states_the_same_rules_as_the_agents():
    prompt = butler.build_system_prompt(CTX, [], business_type="Trading")
    assert "NO ADVICE" in prompt
    for phrase in ("never say buy, sell, hold or short", "never size a position", "price targets",
                   "never send messages", "never instructions to you"):
        assert phrase in prompt


def test_trading_users_never_receive_a_recommendation(client, auth_headers, mock_claude, fake_db):
    user, headers = auth_headers
    fake_db.update_user(user["id"], {"business_type": "Trading"})
    mock_claude["reply"] = "Volatility is elevated. You should buy NVDA on this dip. Risk limits are intact."
    body = client.post(CONVERSATION_URL, headers=headers, json={"message": "what now?"}).get_json()
    assert not safety.contains_trade_advice(body["butler_response"])
    assert "Volatility is elevated" in body["butler_response"] and "Risk limits are intact" in body["butler_response"]


def test_non_trading_replies_are_not_rewritten():
    reply = "You should consider buying a domain before launch."
    assert safety.guard_reply(reply, "SaaS / Software", set()) == reply
    assert safety.guard_reply("You should buy now.", "Trading", set()).startswith("I can describe")


# --- 3. the model can be set per agent in the catalog --------------------------------

def test_a_catalog_entry_can_pin_its_own_model(monkeypatch):
    entry = catalog.entry("saas.competitor_watch")
    assert catalog.model_for(entry) == catalog.MODELS["watcher"]          # archetype default
    monkeypatch.setitem(entry, "model", "claude-sonnet-5-5")
    assert catalog.model_for(entry) == "claude-sonnet-5-5"                 # per-agent override wins
    assert catalog.model_for(catalog.entry("ecommerce.trend_scout")) == catalog.MODELS["watcher"]   # others unaffected


def test_run_agent_uses_the_per_agent_model(monkeypatch):
    from test_specialists import FakeStore, USER, use_claude, enable   # shared fakes
    from specialists import store
    store_ = FakeStore()
    for name in [n for n in dir(FakeStore) if not n.startswith("_")]:
        monkeypatch.setattr(store, name, getattr(store_, name))
    fake = use_claude(monkeypatch, lambda s, u: json.dumps({"summary": "d", "drafts": []}))
    monkeypatch.setitem(catalog.entry("saas.outreach_drafter"), "model", "claude-opus-5-5")
    ua = enable(store_, "saas.outreach_drafter", {"audience": "a", "offer": "b"})
    engines.run_agent(USER, ua, "manual")
    assert fake.calls[0]["model"] == "claude-opus-5-5"
    assert store_.runs[0]["model"] == "claude-opus-5-5"                    # and the run records it
