from hr_breaker.services.profile_store import ProfileStore


def test_profile_store_creates_and_lists_profiles(tmp_path):
    store = ProfileStore(root_dir=tmp_path)

    created = store.create_profile("Jane Doe")
    profiles = store.list_profiles()

    assert created.id == "jane_doe"
    assert [profile.display_name for profile in profiles] == ["Jane Doe"]
    assert store.get_profile(created.id) is not None


def test_profile_store_persists_uploads_and_assets(tmp_path):
    store = ProfileStore(root_dir=tmp_path)
    profile = store.create_profile("Jane Doe")

    document = store.add_upload(
        profile.id,
        filename="resume.md",
        data=b"# Jane Doe\nPython engineer",
        mime_type="text/markdown",
    )

    documents = store.list_documents(profile.id)
    reloaded = store.get_document(profile.id, document.id)

    assert len(documents) == 1
    assert reloaded is not None
    assert reloaded.kind == "resume"
    assert reloaded.content_text == "# Jane Doe\nPython engineer"
    assert reloaded.metadata["original_filename"] == "resume.md"
    assert (tmp_path / profile.id / reloaded.metadata["asset_path"]).exists()


def test_profile_store_deduplicates_matching_uploads_and_updates_timestamp(tmp_path):
    store = ProfileStore(root_dir=tmp_path)
    profile = store.create_profile("Jane Doe")

    first = store.add_upload(
        profile.id,
        filename="resume.md",
        data=b"# Jane Doe\nPython engineer",
    )
    second = store.add_upload(
        profile.id,
        filename="resume.md",
        data=b"# Jane Doe\nPython engineer",
    )
    note = store.add_note(
        profile.id,
        title="Hackathon",
        content_text="Won first place at the AI systems hackathon.",
    )

    documents = store.list_documents(profile.id)

    assert first.id == second.id
    assert len(documents) == 2
    assert {document.kind for document in documents} == {"resume", "note"}
    assert note.source_name == "Hackathon"
