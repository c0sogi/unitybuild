from unitybuild.builds import artifact_signature


def test_desktop_build_detects_scene_updates_when_launcher_is_unchanged(tmp_path):
    player = tmp_path / "Game.exe"
    player.write_bytes(b"unchanged launcher")
    data = tmp_path / "Game_Data"
    data.mkdir()
    scene = data / "level0"
    scene.write_bytes(b"old scene")
    before = artifact_signature(player)
    scene.write_bytes(b"rebuilt scene content")
    assert artifact_signature(player) != before
    after = artifact_signature(player)
    (tmp_path / "unrelated.log").write_text("something else changed")
    assert artifact_signature(player) == after
