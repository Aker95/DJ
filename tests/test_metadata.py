import requests

from tests.helpers import config_for
from utils.metadata import collect_evidence


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "results": [
                {
                    "style": ["Hard Groove"],
                    "genre": ["Electronic"],
                    "label": ["Example Label"],
                    "resource_url": "https://api.discogs.com/releases/1",
                }
            ]
        }


class FakeTagItem:
    def get_name(self):
        return "techno"


class FakeTag:
    item = FakeTagItem()


class FakeLastFmNetwork:
    def get_track(self, _artist, _title):
        return self

    def get_top_tags(self, limit=10):
        return [FakeTag()]


def test_discogs_and_lastfm_are_collected_as_evidence(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    config = type(config)(
        **{
            **config.__dict__,
            "discogs_token": "token",
            "lastfm_api_key": "key",
            "lastfm_shared_secret": "secret",
        }
    )
    monkeypatch.setattr("utils.metadata.requests.get", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(
        "utils.metadata.pylast.LastFMNetwork", lambda **_kwargs: FakeLastFmNetwork()
    )
    evidence = collect_evidence({"artist": "Artist", "title": "Track"}, config)
    assert evidence["discogs"]["styles"] == ["Hard Groove"]
    assert evidence["lastfm"]["tags"] == ["techno"]


def test_provider_failures_become_warnings(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    config = type(config)(
        **{
            **config.__dict__,
            "discogs_token": "token",
            "lastfm_api_key": "key",
            "lastfm_shared_secret": "secret",
        }
    )
    monkeypatch.setattr(
        "utils.metadata.requests.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.RequestException("offline")),
    )
    monkeypatch.setattr(
        "utils.metadata.pylast.LastFMNetwork",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    evidence = collect_evidence({"artist": "Artist", "title": "Track"}, config)
    assert "offline" in evidence["discogs"]["warning"]
    assert "offline" in evidence["lastfm"]["warning"]
