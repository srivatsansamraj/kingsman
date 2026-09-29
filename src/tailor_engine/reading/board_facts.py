"""The facts a job board publishes structured: pay, work mode and location, from its JSON."""

from __future__ import annotations

from typing import Any

from ..records import BoardFacts, Pay, PayTier

WORK_MODES = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
    "on-site": "onsite",
    "in-office": "onsite",
    "in office": "onsite",
    "unspecified": None,
}


def _period(interval: object) -> str:
    return "hour" if "hour" in str(interval or "").lower() else "year"


def _work_mode(value: object) -> str | None:
    return WORK_MODES.get(str(value or "").strip().lower()) if value else None


def pay_span(tiers: list[PayTier], text: str | None = None) -> Pay | None:
    """One range over a board's pay tiers (usually one per location), every tier kept under "tiers".

    The range covers the tiers in the first tier's currency and period only: a CAD and a USD figure do not make one
    range.
    """
    if not tiers:
        return None
    first_currency_tiers = [
        tier for tier in tiers if (tier["currency"], tier["period"]) == (tiers[0]["currency"], tiers[0]["period"])
    ]
    return {
        "min": min(tier["min"] for tier in first_currency_tiers),
        "max": max(tier["max"] for tier in first_currency_tiers),
        "currency": tiers[0]["currency"],
        "period": tiers[0]["period"],
        "text": text,
        "tiers": tiers,
    }


def greenhouse_facts(job: dict[str, Any]) -> BoardFacts:
    """The pay ranges, location type and location a Greenhouse job lists."""
    tiers: list[PayTier] = [
        {
            "label": pay.get("title"),
            "min": pay["min_cents"] // 100,
            "max": pay["max_cents"] // 100,
            "currency": pay.get("currency_type") or "USD",
            "period": "year",
        }
        for pay in job.get("pay_input_ranges") or []
        if pay.get("min_cents") is not None and pay.get("max_cents") is not None
    ]
    mode = next(
        (
            _work_mode(item.get("value"))
            for item in job.get("metadata") or []
            if str(item.get("name", "")).lower() in ("location type", "workplace type", "remote")
        ),
        None,
    )
    return {
        "source": "greenhouse",
        "pay": pay_span(tiers, tiers[0]["label"] if len(tiers) == 1 else None),
        "work_mode": mode,
        "location": (job.get("location") or {}).get("name"),
    }


def lever_facts(job: dict[str, Any]) -> BoardFacts:
    """The salary range, work mode and location a Lever posting lists."""
    pay: Pay | None = None
    salary = job.get("salaryRange") or {}
    if salary.get("min") is not None and salary.get("max") is not None:
        pay = {
            "min": int(salary["min"]),
            "max": int(salary["max"]),
            "currency": salary.get("currency") or "USD",
            "period": _period(salary.get("interval")),
            "text": None,
        }
    return {
        "source": "lever",
        "pay": pay,
        "work_mode": _work_mode(job.get("workplaceType")),
        "location": (job.get("categories") or {}).get("location"),
    }


def ashby_facts(job: dict[str, Any]) -> BoardFacts:
    """The pay tiers, workplace type and every location an Ashby job lists.

    Only a tier's salary component counts as pay; equity and bonus components are left out.
    """
    compensation = job.get("compensation") or {}
    tiers: list[PayTier] = [
        {
            "label": tier.get("title"),
            "min": int(component["minValue"]),
            "max": int(component.get("maxValue") or component["minValue"]),
            "currency": component.get("currencyCode") or "USD",
            "period": _period(component.get("interval")),
        }
        for tier in compensation.get("compensationTiers") or []
        for component in tier.get("components") or []
        if str(component.get("compensationType", "")).lower() == "salary" and component.get("minValue") is not None
    ]
    mode = _work_mode(job.get("workplaceType")) or ("remote" if job.get("isRemote") else None)
    # The primary location alone hid a New York office behind London; the secondary locations are kept.
    places = [
        place
        for place in [job.get("location")]
        + [secondary.get("location") for secondary in job.get("secondaryLocations") or []]
        if place
    ]
    return {
        "source": "ashby",
        "pay": pay_span(tiers, compensation.get("scrapeableCompensationSalarySummary")),
        "work_mode": mode,
        "location": "; ".join(places) or None,
    }
