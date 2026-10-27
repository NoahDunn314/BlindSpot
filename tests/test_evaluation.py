from evaluation.run_eval import run_evaluation


def test_all_evaluation_checks_pass():
    failures = [check for check in run_evaluation() if not check.passed]
    assert failures == [], [f"{c.component}: {c.name} ({c.detail})" for c in failures]
