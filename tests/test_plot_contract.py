from autocad_mcp.plot_contract import normalize_plot_scale


def test_fit_plot_normalizes_historical_numeric_default():
    result = normalize_plot_scale({"scale_mode": "fit", "declared_scale": "NTS"})

    assert result == {
        "ok": True,
        "scale_mode": "fit",
        "effective_scale": "fit",
        "declared_scale": "NTS",
    }


def test_fit_plot_rejects_numeric_scale_declaration():
    result = normalize_plot_scale(
        {"scale_mode": "fit", "declared_scale": "1:1", "scale": "1:1"}
    )

    assert result["ok"] is False
    assert "numeric scale" in result["message"]


def test_fixed_plot_requires_matching_declared_scale():
    result = normalize_plot_scale(
        {"scale_mode": "fixed", "scale": "1:2", "declared_scale": "1:1"}
    )

    assert result["ok"] is False
    assert "match" in result["message"]


def test_fixed_plot_rejects_non_ratio_and_non_positive_values():
    malformed = normalize_plot_scale({"scale_mode": "fixed", "scale": "banana"})
    non_positive = normalize_plot_scale({"scale_mode": "fixed", "scale": "1:0"})

    assert malformed["ok"] is False
    assert "paper:drawing" in malformed["message"]
    assert non_positive["ok"] is False
    assert "positive" in non_positive["message"]


def test_fixed_plot_canonicalizes_equivalent_ratios():
    result = normalize_plot_scale(
        {"scale_mode": "fixed", "scale": "1.0:2.00", "declared_scale": "1:2"}
    )

    assert result["ok"] is True
    assert result["effective_scale"] == "1:2"
    assert result["declared_scale"] == "1:2"
