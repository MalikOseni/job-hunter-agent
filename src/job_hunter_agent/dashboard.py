from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from .export import (
    ApplicationKpis,
    STATUS_LABELS,
    StatusExportArtifacts,
    StatusRow,
    summarize_status_counts,
)

STATUS_HEX = {
    "applied": "#2e7d32",
    "interview_progressed": "#1565c0",
    "pending_review": "#f9a825",
    "blocked": "#ef6c00",
    "rejected": "#c62828",
    "skipped_not_eligible": "#616161",
}

STATUS_TEXT = {
    "pending_review": "#1f1f1f",
}

STATUS_PANEL_START = "<!-- application-status-panel:start -->"
STATUS_PANEL_END = "<!-- application-status-panel:end -->"


@dataclass(frozen=True)
class DashboardArtifacts:
    dated_html: Path
    latest_html: Path
    status_counts: dict[str, int]


def write_status_dashboard(
    rows: list[StatusRow],
    review_dir: Path,
    stamp: str,
    application_kpis: ApplicationKpis | None = None,
) -> DashboardArtifacts:
    review_dir.mkdir(parents=True, exist_ok=True)
    counts = summarize_status_counts(rows)
    total = len(rows)

    dated_html = review_dir / f"application_status_{stamp}.html"
    latest_html = review_dir / "latest_application_status.html"

    legend_html = _build_legend_html(counts)
    table_rows_html = "".join(_render_status_row(row) for row in rows)
    html_doc = f"""<!doctype html><meta charset="utf-8">
<title>Application status dashboard {stamp}</title>
<style>
body{{font-family:system-ui;margin:24px}}
.legend{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 18px}}
.chip{{border-radius:999px;padding:6px 10px;font-size:12px;font-weight:600}}
.kpi{{background:#f2f6ff;border:1px solid #d7e3ff;border-radius:10px;padding:10px 12px;margin:0 0 14px 0}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:6px 8px;font-size:14px;text-align:left;vertical-align:top}}
th{{background:#f5f5f5}}
tr.status-applied{{background:#e8f5e9}}
tr.status-interview_progressed{{background:#e3f2fd}}
tr.status-pending_review{{background:#fff8e1}}
tr.status-blocked{{background:#fff3e0}}
tr.status-rejected{{background:#ffebee}}
tr.status-skipped_not_eligible{{background:#f5f5f5}}
a{{color:#0b57d0;text-decoration:none}}
a:hover{{text-decoration:underline}}
</style>
<h1>Application status dashboard — {stamp}</h1>
<p>Total tracked opportunities: <strong>{total}</strong></p>
{_build_kpi_html(application_kpis)}
{legend_html}
<table>
<tr>
<th>Status</th><th>Role</th><th>Company</th><th>Location</th><th>Score</th>
<th>Posted</th><th>Age (days)</th><th>Salary target</th><th>Updated</th><th>Reason</th>
</tr>
{table_rows_html}
</table>
"""
    for path in (dated_html, latest_html):
        path.write_text(html_doc, encoding="utf-8")

    return DashboardArtifacts(
        dated_html=dated_html,
        latest_html=latest_html,
        status_counts=counts,
    )


def update_live_jobs_html_with_status_section(
    live_jobs_html_path: Path,
    *,
    dashboard_artifacts: DashboardArtifacts,
    export_artifacts: StatusExportArtifacts,
    application_kpis: ApplicationKpis | None = None,
) -> bool:
    if not live_jobs_html_path.exists():
        return False
    content = live_jobs_html_path.read_text(encoding="utf-8")
    panel_html = _build_live_jobs_panel(
        dashboard_artifacts,
        export_artifacts,
        application_kpis,
    )
    panel_block = f"{STATUS_PANEL_START}\n{panel_html}\n{STATUS_PANEL_END}"

    if STATUS_PANEL_START in content and STATUS_PANEL_END in content:
        content = re.sub(
            rf"{re.escape(STATUS_PANEL_START)}.*?{re.escape(STATUS_PANEL_END)}",
            panel_block,
            content,
            count=1,
            flags=re.DOTALL,
        )
    elif "<table>" in content:
        content = content.replace("<table>", f"{panel_block}\n<table>", 1)
    else:
        content = content + "\n" + panel_block

    live_jobs_html_path.write_text(content, encoding="utf-8")
    return True


def _render_status_row(row: StatusRow) -> str:
    status_label = STATUS_LABELS.get(row.status, row.status)
    salary_cell = (
        f"{html.escape(row.salary_currency)} {row.salary_expectation:,.0f}"
        if row.salary_expectation is not None and row.salary_currency
        else ""
    )
    return (
        f"<tr class='status-{html.escape(row.status)}'>"
        f"<td>{html.escape(status_label)}</td>"
        f"<td><a href='{html.escape(row.url)}'>{html.escape(row.title)}</a></td>"
        f"<td>{html.escape(row.company)}</td>"
        f"<td>{html.escape(row.location)}</td>"
        f"<td>{row.latest_score}</td>"
        f"<td>{html.escape(row.latest_posted)}</td>"
        f"<td>{'' if row.latest_age_days is None else row.latest_age_days}</td>"
        f"<td>{html.escape(salary_cell)}</td>"
        f"<td>{html.escape(row.application_updated_at)}</td>"
        f"<td>{html.escape(row.status_reason)}</td>"
        "</tr>"
    )


def _build_legend_html(counts: dict[str, int]) -> str:
    chips = []
    for status, label in STATUS_LABELS.items():
        bg = STATUS_HEX.get(status, "#eceff1")
        fg = STATUS_TEXT.get(status, "#ffffff")
        chips.append(
            f"<span class='chip' style='background:{bg};color:{fg}'>"
            f"{html.escape(label)}: {counts.get(status, 0)}</span>"
        )
    return "<div class='legend'>" + "".join(chips) + "</div>"


def _build_live_jobs_panel(
    dashboard_artifacts: DashboardArtifacts,
    export_artifacts: StatusExportArtifacts,
    application_kpis: ApplicationKpis | None = None,
) -> str:
    legend_items = []
    for status, label in STATUS_LABELS.items():
        bg = STATUS_HEX.get(status, "#eceff1")
        fg = STATUS_TEXT.get(status, "#ffffff")
        legend_items.append(
            "<span style='display:inline-block;border-radius:999px;padding:4px 8px;"
            f"margin:0 6px 6px 0;background:{bg};color:{fg};font-size:12px;font-weight:600'>"
            f"{html.escape(label)}: {dashboard_artifacts.status_counts.get(status, 0)}</span>"
        )
    return (
        "<section style='margin:18px 0 14px 0;padding:12px;border:1px solid #ddd;border-radius:10px;"
        "background:#f9fbff'>"
        "<h2 style='margin:0 0 8px 0;font-size:18px'>Application status dashboard</h2>"
        "<p style='margin:0 0 10px 0'>"
        f"<a href='{html.escape(dashboard_artifacts.latest_html.name)}'>latest dashboard</a> · "
        f"<a href='{html.escape(export_artifacts.latest_csv.name)}'>latest csv</a> · "
        f"<a href='{html.escape(export_artifacts.latest_json.name)}'>latest json</a>"
        "</p>"
        + _build_kpi_inline_html(application_kpis)
        + "".join(legend_items)
        + "</section>"
    )


def _build_kpi_html(application_kpis: ApplicationKpis | None) -> str:
    if application_kpis is None:
        return ""
    reassess_text = "YES" if application_kpis.reassess_required else "no"
    readiness_text = "yes" if application_kpis.readiness_can_attempt else "no"
    missing_prereq = "; ".join(application_kpis.missing_prerequisites) or "none"
    missing_keys = _format_missing_board_keys(application_kpis.missing_greenhouse_board_tokens)
    return (
        "<div class='kpi'>"
        "<strong>Auto-submit KPIs:</strong> "
        f"shortlisted considered={application_kpis.shortlisted_considered}, "
        f"attempted={application_kpis.attempted}, "
        f"applied={application_kpis.applied}, "
        f"blocked={application_kpis.blocked}, "
        f"skipped={application_kpis.skipped}, "
        f"success_rate={application_kpis.success_rate:.2f}%, "
        f"target_shortlist_ratio={application_kpis.target_shortlist_ratio:.2f}%, "
        f"attempt_coverage={application_kpis.attempt_coverage_ratio:.2f}%, "
        f"goal_rating={application_kpis.goal_progress_rating:.2f}/100, "
        f"reassess_required={reassess_text}, "
        f"ready_for_real_attempts={readiness_text}, "
        f"candidate_greenhouse_roles={application_kpis.candidate_greenhouse_roles}, "
        f"missing_prerequisites={html.escape(missing_prereq)}, "
        f"missing_greenhouse_keys={html.escape(missing_keys)}"
        "</div>"
    )


def _build_kpi_inline_html(application_kpis: ApplicationKpis | None) -> str:
    if application_kpis is None:
        return ""
    reassess_text = "YES" if application_kpis.reassess_required else "no"
    readiness_text = "yes" if application_kpis.readiness_can_attempt else "no"
    missing_keys = _format_missing_board_keys(application_kpis.missing_greenhouse_board_tokens)
    return (
        "<p style='margin:0 0 10px 0;font-size:13px'>"
        "<strong>Auto-submit:</strong> "
        f"attempted {application_kpis.attempted}, "
        f"applied {application_kpis.applied}, "
        f"blocked {application_kpis.blocked}, "
        f"success rate {application_kpis.success_rate:.2f}% · "
        f"ready {readiness_text} · "
        f"missing keys {html.escape(missing_keys)} · "
        f"goal rating {application_kpis.goal_progress_rating:.2f}/100 · "
        f"reassess {reassess_text}"
        "</p>"
    )


def _format_missing_board_keys(missing_keys: dict[str, int]) -> str:
    if not missing_keys:
        return "none"
    return ", ".join(f"{board}:{count}" for board, count in missing_keys.items())
