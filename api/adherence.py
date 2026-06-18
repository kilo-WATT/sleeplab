"""Pure adherence analytics over normalized machine-local nightly usage.

The fixed policy in this module is an informational analytics convention, not
an insurer certification. A later SleepLab 2.0 alpha may add user-configurable
policy and report UI after the normalized calculation contract is stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

QUALIFYING_USAGE_SECONDS = 4 * 60 * 60
REQUIRED_PERCENT = 70.0
WINDOW_DAYS = 30
EVALUATION_DAYS = 90

AdherenceStatus = Literal["compliant", "noncompliant", "missing"]


@dataclass(frozen=True)
class AdherencePolicy:
    """Fixed policy values used by the current adherence analytics API."""

    qualifying_usage_seconds: int = QUALIFYING_USAGE_SECONDS
    required_percent: float = REQUIRED_PERCENT
    window_days: int = WINDOW_DAYS
    evaluation_days: int = EVALUATION_DAYS


@dataclass(frozen=True)
class NightlyUsage:
    """Authoritative therapy usage for one machine-local report date."""

    report_date: date
    usage_seconds: int


@dataclass(frozen=True)
class DailyAdherence:
    report_date: date
    usage_seconds: int | None
    status: AdherenceStatus


@dataclass(frozen=True)
class AdherenceWindow:
    start_date: date
    end_date: date
    compliant_nights: int
    total_days: int
    compliance_percent: float
    qualifies: bool


@dataclass(frozen=True)
class AdherenceSummary:
    start_date: date
    end_date: date
    total_evaluation_days: int
    days_with_therapy_data: int
    compliant_nights: int
    missing_nights: int
    noncompliant_nights_with_data: int
    compliance_percent: float


@dataclass(frozen=True)
class AdherenceStreaks:
    current_compliant_nights: int
    longest_compliant_nights: int


@dataclass(frozen=True)
class AdherenceResult:
    policy: AdherencePolicy
    summary: AdherenceSummary
    current_window: AdherenceWindow
    best_window: AdherenceWindow
    streaks: AdherenceStreaks
    daily: list[DailyAdherence]
    rolling_windows: list[AdherenceWindow]


def evaluation_start(end_date: date, policy: AdherencePolicy = AdherencePolicy()) -> date:
    """Return the inclusive start date for a policy evaluation period."""

    return end_date - timedelta(days=policy.evaluation_days - 1)


def _window(daily: list[DailyAdherence], start_index: int, policy: AdherencePolicy) -> AdherenceWindow:
    window_days = daily[start_index : start_index + policy.window_days]
    compliant = sum(day.status == "compliant" for day in window_days)
    percentage = round(compliant / policy.window_days * 100, 1)
    return AdherenceWindow(
        start_date=window_days[0].report_date,
        end_date=window_days[-1].report_date,
        compliant_nights=compliant,
        total_days=policy.window_days,
        compliance_percent=percentage,
        qualifies=percentage >= policy.required_percent,
    )


def _streaks(daily: list[DailyAdherence]) -> AdherenceStreaks:
    longest = 0
    running = 0
    for day in daily:
        if day.status == "compliant":
            running += 1
            longest = max(longest, running)
        else:
            running = 0
    return AdherenceStreaks(current_compliant_nights=running, longest_compliant_nights=longest)


def calculate_adherence(
    nightly_usage: list[NightlyUsage],
    *,
    end_date: date,
    policy: AdherencePolicy = AdherencePolicy(),
) -> AdherenceResult:
    """Calculate adherence for the inclusive policy period ending at ``end_date``.

    Multiple inputs for the same report date are summed. This matches the API's
    user-level interpretation when therapy is split across more than one machine
    on a machine-local calendar date. Inputs outside the evaluation period are
    ignored, and absent dates are explicitly classified as missing.
    """

    if policy.evaluation_days < policy.window_days:
        raise ValueError("evaluation_days must be at least window_days")
    if policy.window_days <= 0 or policy.evaluation_days <= 0:
        raise ValueError("window_days and evaluation_days must be positive")
    if policy.qualifying_usage_seconds < 0:
        raise ValueError("qualifying_usage_seconds must be non-negative")
    if not 0 <= policy.required_percent <= 100:
        raise ValueError("required_percent must be between 0 and 100")

    start_date = evaluation_start(end_date, policy)
    usage_by_date: dict[date, int] = {}
    for night in nightly_usage:
        if start_date <= night.report_date <= end_date:
            usage_by_date[night.report_date] = usage_by_date.get(night.report_date, 0) + max(
                0, int(night.usage_seconds)
            )

    daily: list[DailyAdherence] = []
    for offset in range(policy.evaluation_days):
        report_date = start_date + timedelta(days=offset)
        usage_seconds = usage_by_date.get(report_date)
        if usage_seconds is None:
            status: AdherenceStatus = "missing"
        elif usage_seconds >= policy.qualifying_usage_seconds:
            status = "compliant"
        else:
            status = "noncompliant"
        daily.append(DailyAdherence(report_date=report_date, usage_seconds=usage_seconds, status=status))

    compliant_nights = sum(day.status == "compliant" for day in daily)
    days_with_data = sum(day.status != "missing" for day in daily)
    missing_nights = policy.evaluation_days - days_with_data
    noncompliant_with_data = sum(day.status == "noncompliant" for day in daily)
    summary = AdherenceSummary(
        start_date=start_date,
        end_date=end_date,
        total_evaluation_days=policy.evaluation_days,
        days_with_therapy_data=days_with_data,
        compliant_nights=compliant_nights,
        missing_nights=missing_nights,
        noncompliant_nights_with_data=noncompliant_with_data,
        compliance_percent=round(compliant_nights / policy.evaluation_days * 100, 1),
    )

    rolling_windows = [
        _window(daily, index, policy)
        for index in range(policy.evaluation_days - policy.window_days + 1)
    ]
    current_window = rolling_windows[-1]
    best_window = max(
        rolling_windows,
        key=lambda window: (window.compliant_nights, window.end_date),
    )

    return AdherenceResult(
        policy=policy,
        summary=summary,
        current_window=current_window,
        best_window=best_window,
        streaks=_streaks(daily),
        daily=daily,
        rolling_windows=rolling_windows,
    )
