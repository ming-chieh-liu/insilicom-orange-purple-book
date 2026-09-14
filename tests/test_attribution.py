"""Tests for re-examining a trial → approval claim against the approval events."""

from __future__ import annotations

from service import attribution


def _row(nct: str, application: str, first_approval: str) -> dict[str, str]:
    return {
        "nct_id": nct,
        "application": application,
        "app_type": "BLA",
        "drug": "PEMBROLIZUMAB",
        "unii": "DPT0O3T46P",
        "sponsor": "MERCK SHARP DOHME",
        "has_results": "true",
        "polarity": "supporting",
        "first_approval": first_approval,
    }


# --- the rule ---------------------------------------------------------------


def test_a_trial_predating_the_approval_is_not_refuted() -> None:
    """Arithmetic cannot deny it. That is not the same as confirming it."""
    assert attribution.classify("2014-09-04", "2011-06-01", ["2018-01-01"]) == (
        attribution.ORIGINAL,
        "2014-09-04",
        0,
    )


def test_a_trial_starting_after_the_approval_is_refuted() -> None:
    """A trial cannot have supported an approval granted before it began."""
    outcome, date, candidates = attribution.classify(
        "2014-09-04", "2016-03-01", ["2018-08-20"]
    )
    assert outcome == attribution.RESOLVED
    assert (date, candidates) == ("2018-08-20", 1)


def test_several_fitting_approvals_give_a_window_not_a_guess() -> None:
    """Naming one of several would be a guess dressed as a fact; the earliest
    is emitted as the start of the window, with the count beside it."""
    outcome, date, candidates = attribution.classify(
        "2014-09-04", "2016-03-01", ["2018-08-20", "2020-06-10", "2021-01-05"]
    )
    assert outcome == attribution.AMBIGUOUS
    assert (date, candidates) == ("2018-08-20", 3)


def test_a_refuted_row_with_no_later_approval_is_unresolved() -> None:
    """The row is known wrong and the spine cannot say what is right. Emitting
    the refuted date anyway would keep asserting what was just disproved."""
    assert attribution.classify("2014-09-04", "2016-03-01", []) == (
        attribution.UNRESOLVED,
        "",
        0,
    )


def test_a_trial_with_no_start_date_is_undated_not_refuted() -> None:
    assert attribution.classify("2014-09-04", "", []) == (
        attribution.UNDATED,
        "2014-09-04",
        0,
    )


def test_a_row_with_no_stamped_approval_cannot_be_refuted() -> None:
    """867 applications have no usable approval date; absence is not evidence."""
    assert attribution.classify("", "2016-03-01", ["2018-01-01"]) == (
        attribution.ORIGINAL,
        "",
        0,
    )


def test_a_trial_starting_on_the_approval_date_is_not_refuted() -> None:
    """Equality is not 'after'. The bound is conservative by construction."""
    outcome, _date, _n = attribution.classify(
        "2014-09-04", "2014-09-04", ["2018-01-01"]
    )
    assert outcome == attribution.ORIGINAL


# --- the pass ---------------------------------------------------------------


def test_every_row_is_classified_and_carried_through() -> None:
    rows = [
        _row("NCT00000001", "BLA125514", "2014-09-04"),
        _row("NCT00000002", "BLA125514", "2014-09-04"),
    ]
    events = {"BLA125514": ["2014-09-04", "2018-08-20"]}
    starts = {"NCT00000001": "2011-01-01", "NCT00000002": "2016-03-01"}
    report = attribution.attribute(rows, events, starts)
    assert len(report.rows) == 2
    by_nct = {r.nct_id: r for r in report.rows}
    assert by_nct["NCT00000001"].attribution == attribution.ORIGINAL
    assert by_nct["NCT00000002"].attribution == attribution.RESOLVED
    assert by_nct["NCT00000002"].approval_date == "2018-08-20"
    # the identifying columns survive the pass
    assert by_nct["NCT00000001"].drug == "PEMBROLIZUMAB"
    assert by_nct["NCT00000001"].polarity == "supporting"


def test_the_stamped_date_is_kept_beside_the_corrected_one() -> None:
    """A correction that erases what it corrected cannot be audited."""
    rows = [_row("NCT00000002", "BLA125514", "2014-09-04")]
    report = attribution.attribute(
        rows, {"BLA125514": ["2018-08-20"]}, {"NCT00000002": "2016-03-01"}
    )
    row = report.rows[0]
    assert row.stamped_approval == "2014-09-04"
    assert row.approval_date == "2018-08-20"
    assert row.trial_start == "2016-03-01"


def test_only_events_at_or_after_the_trial_start_are_candidates() -> None:
    rows = [_row("NCT00000002", "BLA125514", "2014-09-04")]
    events = {"BLA125514": ["2015-01-01", "2016-03-01", "2019-05-05"]}
    report = attribution.attribute(rows, events, {"NCT00000002": "2016-03-01"})
    # 2015-01-01 predates the trial and cannot be a candidate; two remain.
    assert report.rows[0].candidate_approvals == "2"


def test_the_refuted_count_and_touched_applications_are_reported() -> None:
    rows = [
        _row("NCT00000001", "BLA125514", "2014-09-04"),
        _row("NCT00000002", "BLA125514", "2014-09-04"),
        _row("NCT00000003", "NDA000001", "2000-01-01"),
    ]
    events = {"BLA125514": ["2018-08-20"], "NDA000001": ["2000-01-01"]}
    starts = {
        "NCT00000001": "2011-01-01",
        "NCT00000002": "2016-03-01",
        "NCT00000003": "2015-01-01",
    }
    report = attribution.attribute(rows, events, starts)
    assert report.refuted == 2  # one resolved, one unresolved
    assert report.applications_touched == 2


def test_an_application_with_no_events_leaves_refuted_rows_unresolved() -> None:
    rows = [_row("NCT00000002", "BLA999999", "2014-09-04")]
    report = attribution.attribute(rows, {}, {"NCT00000002": "2016-03-01"})
    assert report.rows[0].attribution == attribution.UNRESOLVED


def test_the_report_renders_its_outcomes() -> None:
    rows = [_row("NCT00000002", "BLA125514", "2014-09-04")]
    report = attribution.attribute(
        rows, {"BLA125514": ["2018-08-20"]}, {"NCT00000002": "2016-03-01"}
    )
    rendered = attribution.render(report)
    assert "attribution" in rendered.lower()
    assert attribution.RESOLVED in rendered


def test_rendering_an_empty_table_does_not_divide_by_zero() -> None:
    assert attribution.render(attribution.attribute([], {}, {}))
