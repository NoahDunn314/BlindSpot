"""Smoke tests for the Streamlit UI, run headlessly with Streamlit's AppTest harness."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_loads_in_demo_mode_without_exceptions():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("DEMO MODE" in warning.value for warning in at.warning)


def test_sample_investigation_shows_findings_and_labeled_demo_explanation():
    at = AppTest.from_file(APP, default_timeout=60).run()
    harborline = next(button for button in at.button if button.key == "sample-ent-harborline")
    harborline.click().run()

    assert not at.exception
    metric_values = {metric.label: metric.value for metric in at.metric}
    assert metric_values["Findings"] == "2"
    assert any("deterministic demo template" in caption.value for caption in at.caption)


def test_control_sample_shows_no_finding_and_a_refusal_note():
    at = AppTest.from_file(APP, default_timeout=60).run()
    cedar = next(button for button in at.button if button.key == "sample-ent-cedar")
    cedar.click().run()

    assert not at.exception
    assert {metric.label: metric.value for metric in at.metric}["Findings"] == "0"
    assert any("not evidence that no relationship exists" in info.value for info in at.info)
