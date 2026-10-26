"""Discover page: rendering and real callback dispatch (including pattern-matching buttons)."""

import json
import time
from datetime import date

from discovery_fixtures import FOUNDATION_PAGE, make_poster
from test_ui import text_of

from app import app as app_module
from app import discover as discover_module
from app.discover import render_card, render_results, render_shortlist
from app.forms import OPPORTUNITY_FIELD_IDS
from grantfit.discovery import grants_gov
from grantfit.discovery.fetch import FetchedPage
from grantfit.discovery.models import DiscoveryQuery
from grantfit.discovery.service import fetch_url_opportunity

TODAY = date(2026, 10, 26)


def items():
    results, _, _ = grants_gov.discover(DiscoveryQuery(keywords="literacy", legal_status="501c3"), make_poster(), today=TODAY)
    web = fetch_url_opportunity("https://harborlight.example.org/grants", DiscoveryQuery(keywords="literacy"),
                                fetcher=lambda u: FetchedPage(u, FOUNDATION_PAGE, None, False), today=TODAY)
    return [r.model_dump(mode="json") for r in results] + [web.model_dump(mode="json")]


def http(output_contains, trigger, values, pattern_inputs=None):
    """POST to Dash's real callback endpoint. pattern_inputs: {spec_id: [(id_dict, value), ...]}."""
    trig_id = trigger.rsplit(".", 1)[0]
    needle = f'"type":"{json.loads(trig_id)["type"]}"' if trig_id.startswith("{") else trig_id
    key = next(k for k, v in app_module.app.callback_map.items() if output_contains in k and
               any(needle in i["id"] if trig_id.startswith("{") else needle == i["id"] for i in v["inputs"]))
    spec = app_module.app.callback_map[key]
    outs = []
    for part in key.strip(".").split("..."):
        cid, prop = part.split(".", 1)
        outs.append({"id": cid, "property": prop})

    def entry(i):
        if pattern_inputs and i["id"] in pattern_inputs:
            return [{"id": idd, "property": i["property"], "value": val} for idd, val in pattern_inputs[i["id"]]]
        return {**i, "value": values.get(f"{i['id']}.{i['property']}")}

    body = {"output": key, "outputs": outs if len(outs) > 1 else outs[0],
            "inputs": [entry(i) for i in spec["inputs"]], "state": [entry(s) for s in spec["state"]],
            "changedPropIds": [trigger]}
    resp = app_module.app.server.test_client().post("/_dash-update-component", json=body)
    assert resp.status_code in (200, 204), resp.get_data(as_text=True)[:400]
    return resp.get_json()["response"] if resp.status_code == 200 else None


def pid(t, key):
    return json.dumps({"key": key, "type": t}, separators=(",", ":"), sort_keys=True)


# --------------------------------------------------------------------------- rendering

def test_card_shows_attribution_unknowns_and_source_link():
    data = items()
    lit = next(d for d in data if "Community Literacy" in d["title"])
    page = text_of(render_card(lit))
    assert "Live · Grants.gov" in page and "Official listing field" in page and "$25,000 to $150,000" in page
    broadband = next(d for d in data if "Broadband" in d["title"])
    page_b = text_of(render_card(broadband))
    assert "Unknown" in page_b and "No source evidence" in page_b and "Weak match" in page_b
    web = data[-1]
    assert "Quoted from the page" in text_of(render_card(web))
    assert "https://harborlight.example.org/grants" in str(render_card(web))


def test_shortlist_cards_are_labeled_saved():
    entry = {"item": items()[0], "saved_at": "2026-10-26 04:00 UTC"}
    page = text_of(render_shortlist([entry]))
    assert "Saved" in page and "saved 2026-10-26 04:00 UTC" in page and "Remove" in page


def test_results_error_and_empty_states():
    assert "Search could not run" in text_of(render_results({"error": "ConnectionError"}, []))
    assert "No opportunities found" in text_of(render_results({"results": [], "notes": []}, []))


# --------------------------------------------------------------------------- callbacks via HTTP

def test_search_requires_keywords():
    out = http("d-errors.children", "d-search.n_clicks", {"d-search.n_clicks": 1, "d-keywords.value": " ",
                                                          "d-sources.value": ["grants_gov"]})
    assert "Enter a cause area" in json.dumps(out["d-errors"])


def test_search_job_runs_and_returns_live_results(monkeypatch):
    from grantfit.discovery import service
    monkeypatch.setattr(service.grants_gov, "default_post", make_poster())
    monkeypatch.setattr("grantfit.discovery.service.grants_gov.default_post", make_poster())
    real_discover = service.discover
    monkeypatch.setattr(service, "discover",
                        lambda q, **kw: real_discover(q, grants_post=make_poster(), today=TODAY, **{k: v for k, v in kw.items() if k != "today"}))
    out = http("d-errors.children", "d-search.n_clicks",
               {"d-search.n_clicks": 1, "d-keywords.value": "literacy", "d-legal.value": "501c3",
                "d-deadline.value": "any", "d-unknown-deadline.value": ["yes"], "d-sources.value": ["grants_gov"]})
    job_id = out["d-job"]["data"]
    from app.jobs import get_discovery_job
    t0 = time.time()
    while get_discovery_job(job_id).status == "running" and time.time() - t0 < 10:
        time.sleep(0.02)
    polled = http("d-data.data", "d-poll.n_intervals", {"d-poll.n_intervals": 1, "d-job.data": job_id})
    data = polled["d-data"]["data"]
    assert data["results"] and all(r["source"] == "grants_gov" for r in data["results"])
    assert any("Grants.gov:" in n for n in data["notes"])


def test_save_and_remove_shortlist_via_pattern_buttons():
    data = {"results": items()}
    key = data["results"][0]["key"]
    saved = http("shortlist.data", pid("disc-save", key) + ".n_clicks", {"d-data.data": data, "shortlist.data": []},
                 {'{"key":["ALL"],"type":"disc-save"}': [({"type": "disc-save", "key": key}, 1)],
                  '{"key":["ALL"],"type":"short-remove"}': []})
    short = saved["shortlist"]["data"]
    assert short[0]["item"]["key"] == key and short[0]["saved_at"].endswith("UTC")
    removed = http("shortlist.data", pid("short-remove", key) + ".n_clicks", {"d-data.data": data, "shortlist.data": short},
                   {'{"key":["ALL"],"type":"disc-save"}': [],
                    '{"key":["ALL"],"type":"short-remove"}': [({"type": "short-remove", "key": key}, 1)]})
    assert removed["shortlist"]["data"] == []


def test_assess_hands_off_opportunity_without_touching_profile():
    data = {"results": items()}
    lit = next(d for d in data["results"] if "Community Literacy" in d["title"])
    out = http("o-funder_name.value", pid("disc-assess", lit["key"]) + ".n_clicks", {"d-data.data": data, "shortlist.data": []},
               {'{"key":["ALL"],"type":"disc-assess"}': [({"type": "disc-assess", "key": lit["key"]}, 1)],
                '{"key":["ALL"],"type":"short-assess"}': []})
    assert out["url"]["pathname"] == "/opportunity"
    assert out["o-funder_name"]["value"] == "Fictional Department of Education"
    assert out["o-opportunity_type"]["value"] == "government" and out["o-deadline"]["value"] == "2026-12-15"
    assert "Applicants must serve communities in Connecticut" in out["o-source_text"]["value"]
    assert out["assess-origin"]["data"]["url"] == lit["url"]
    assert out["o-source_url"]["value"] == lit["url"]                  # source link travels to the assessment
    assert not any(k.startswith(("p-", "c-")) for k in out)            # profile and concept untouched
    assert set(OPPORTUNITY_FIELD_IDS) <= set(out)


def test_unclicked_pattern_buttons_do_nothing():
    data = {"results": items()}
    key = data["results"][0]["key"]
    out = http("shortlist.data", pid("disc-save", key) + ".n_clicks", {"d-data.data": data, "shortlist.data": []},
               {'{"key":["ALL"],"type":"disc-save"}': [({"type": "disc-save", "key": key}, 0)],
                '{"key":["ALL"],"type":"short-remove"}': []})
    assert out is None or "shortlist" not in out


def test_layout_has_discover_nav_and_local_shortlist():
    layout = app_module.layout()
    s = str(layout)
    assert "nav-discover" in s and "Discover funding" in s
    assert "storage_type='local'" in s or '"storage_type": "local"' in s or "local" in str(
        next(c for c in layout.children if getattr(c, "id", None) == "shortlist").storage_type)
    assert discover_module.SHORTLIST_LIMIT == 30


def test_results_render_with_existing_shortlist_and_mark_saved_items():
    data = items()
    shortlist = [{"item": data[0], "saved_at": "2026-10-26 04:00 UTC"}]
    page = render_results({"results": data, "notes": []}, shortlist)
    assert "Saved to shortlist ✓" in text_of(page) and text_of(page).count("Saved to shortlist ✓") == 1
