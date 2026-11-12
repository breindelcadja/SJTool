"""What the tool knows about the journalist's request: topic, place, timeframe.

The front end supplies these. They scope the coverage check now and the
evidence search later (local sources, recent dates).
"""

from urllib.parse import quote_plus

COVERAGE_STATUSES = ("not_checked", "already_reported", "not_reported")


def make_context(problem, location, start, end):
    """start and end are YYYY-MM-DD strings."""
    return {"problem": problem, "location": location, "start": start, "end": end}


def coverage_search_links(context, solution_summary):
    """Links the journalist can open to see if this has already been reported.

    The date operators (after:/before:) are standard Google search operators but
    were NOT tested from here. Adjust the query if results look wrong.
    """
    query = '%s "%s" %s after:%s before:%s' % (
        solution_summary, context["location"], context["problem"],
        context["start"], context["end"])
    q = quote_plus(query)
    return [
        {"label": "Search Google News", "url": "https://news.google.com/search?q=" + q},
        {"label": "Search Google", "url": "https://www.google.com/search?q=" + q},
    ]


def new_coverage(context, solution_summary):
    """Starting coverage record. The journalist sets the status after looking."""
    return {"status": "not_checked",
            "search_links": coverage_search_links(context, solution_summary)}


def set_coverage(pitch, status):
    if status not in COVERAGE_STATUSES:
        raise ValueError("status must be one of %s" % (COVERAGE_STATUSES,))
    pitch["coverage"]["status"] = status
    return pitch
