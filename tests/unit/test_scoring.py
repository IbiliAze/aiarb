import math

import pytest

from aiarb.domain.scoring import RUBRIC, Severity, SeverityThresholds


def test_severities_are_ordered_from_least_to_most_severe() -> None:
    ranked = sorted(Severity, key=lambda severity: severity.rank)

    assert ranked == [Severity.MINOR, Severity.MODERATE, Severity.MAJOR, Severity.BLOCKER]


def test_only_minor_skips_debate() -> None:
    assert [severity for severity in Severity if severity.skips_debate] == [Severity.MINOR]


def test_the_rubric_describes_every_severity() -> None:
    assert set(RUBRIC) == set(Severity)
    assert all(description.strip() for description in RUBRIC.values())


@pytest.mark.parametrize(
    ("score", "severity"),
    [
        (0.0, Severity.MINOR),
        (0.24, Severity.MINOR),
        (0.25, Severity.MODERATE),
        (0.49, Severity.MODERATE),
        (0.5, Severity.MAJOR),
        (0.79, Severity.MAJOR),
        (0.8, Severity.BLOCKER),
        (1.0, Severity.BLOCKER),
    ],
)
def test_default_thresholds_classify_at_the_boundaries(score: float, severity: Severity) -> None:
    assert SeverityThresholds().classify(score) is severity


def test_custom_thresholds() -> None:
    thresholds = SeverityThresholds(moderate=0.1, major=0.2, blocker=0.3)

    assert thresholds.classify(0.15) is Severity.MODERATE
    assert thresholds.classify(0.3) is Severity.BLOCKER


@pytest.mark.parametrize("score", [-0.01, 1.01, math.nan])
def test_scores_outside_zero_to_one_are_refused(score: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        SeverityThresholds().classify(score)


@pytest.mark.parametrize(
    ("moderate", "major", "blocker"),
    [
        (0.0, 0.5, 0.8),
        (0.5, 0.5, 0.8),
        (0.6, 0.5, 0.8),
        (0.25, 0.5, 1.1),
        (0.25, 0.9, 0.8),
    ],
)
def test_thresholds_must_rise_within_zero_to_one(
    moderate: float, major: float, blocker: float
) -> None:
    with pytest.raises(ValueError, match="must rise strictly"):
        SeverityThresholds(moderate=moderate, major=major, blocker=blocker)
