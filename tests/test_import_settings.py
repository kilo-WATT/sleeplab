from sqlalchemy import text


def test_llm_settings_are_not_cached_and_blank_key_preserves_secret(
    client,
    auth_headers,
    db,
    test_user,
):
    saved = client.put(
        "/import/settings",
        json={
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "llm_api_key": "test-secret",
        },
        headers=auth_headers,
    )

    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    assert saved.json()["llm_configured"] is True
    assert saved.json()["has_llm_api_key"] is True
    assert saved.json()["llm_api_key"] is None

    updated = client.put(
        "/import/settings",
        json={
            "llm_provider": "openai",
            "llm_model": "gpt-4.1-mini",
            "llm_api_key": None,
        },
        headers=auth_headers,
    )

    assert updated.status_code == 200
    assert updated.json()["llm_configured"] is True
    assert updated.json()["has_llm_api_key"] is True
    stored_key = db.execute(
        text(
            "SELECT llm_api_key FROM user_import_settings "
            "WHERE user_id = CAST(:user_id AS uuid)"
        ),
        {"user_id": test_user["id"]},
    ).scalar_one()
    assert stored_key == "test-secret"

    loaded = client.get("/import/settings", headers=auth_headers)
    assert loaded.status_code == 200
    assert loaded.headers["cache-control"] == "no-store"
    assert loaded.json()["llm_configured"] is True
    assert loaded.json()["has_llm_api_key"] is True
    assert loaded.json()["llm_api_key"] is None
