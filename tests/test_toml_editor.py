from src.toml_editor import edit_thresholds


def test_threshold_edit():
    text = """
[segmentation]
area_ths = [10, 100]
intensity_ths = [0, 20]
"""
    out = edit_thresholds(text, 15, 120, 25)
    assert "15" in out
    assert "120" in out
    assert "25" in out
