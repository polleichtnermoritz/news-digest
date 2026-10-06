from digest.settings import load_settings


def test_load_settings_reads_defaults() -> None:
    settings = load_settings()

    assert settings.delivery_hour_vienna == 7
    assert settings.top_n_per_group.tech == 8
    assert settings.top_n_per_group.laws_minimum == 2
    assert settings.models.ranking
    assert settings.token_cap_per_run > 0
