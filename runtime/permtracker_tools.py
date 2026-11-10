"""PERM Tracker tools: DOL case, employer, occupation and visa bulletin data from permtracker.app.

PERM Tracker (https://permtracker.app) republishes the Department of Labor's PERM, prevailing
wage and H-1B LCA data, DOL processing times, the State Department visa bulletin and O*NET
occupation data, and serves it through a JSON API (https://permtracker.app/developers).

Always available (no API key):
    match_employer(name)                GET /lookup/employer
    find_soc_code(query)                GET /lookup/soc

Only when PERMTRACKER_API_KEY is set, so the model never sees a tool that can only fail:
    perm_case_status(case_number, live?) GET /cases/{caseNumber}
    perm_decision_estimate(case_number? | filed_date?)  GET /estimate
    perm_queue()                         GET /queue
    visa_bulletin(month?)                GET /visa-bulletin
    search_employers(query)              GET /employers
    employer_profile(slug)               GET /employers/{slug}
    search_law_firms(query)              GET /law-firms
    law_firm_profile(slug)               GET /law-firms/{slug}
    search_occupations(query)            GET /occupations
    occupation_profile(slug)             GET /occupations/{slug}
    export_search(kind, ...)             GET /exports/{kind}

Always available too: immigration_data_page(topic), which reads one of the site's statistics
pages (daily PERM decisions, denial rates, USCIS processing times, H-1B lottery odds, visa
interview waits, ...) for data the API doesn't serve. It returns the page's text, read with the
same reader as read_web_page, so a page redesign can change what it gets. The API has no list of
the cases decided on a day: the daily page shows that day's totals and only a sample of its
cases, and its description says so, so the model doesn't present the sample as the whole day.

Not tools: /me (the key's own usage), and /webhooks and /watches (they deliver events to an
https endpoint the user runs). The key is sent as a Bearer token; free keys allow 10 calls a
minute and 300 a day, the lookups 60 a minute.

Answers come back as {"data": ..., "meta": {"source", "asOf", "url"}}. A tool returns a first
line with the date of the data and the credit the API terms require wherever results are shown,
then the data as JSON. match_employer adds a line between them saying whether the match is
exact, possible or none and what to do about it, so that guidance is in front of the model when
it reads the numbers.

The descriptions say when to use each tool and when not to; the system prompt only says what
the group is for. All tools only read, so they need no approval and are safe to re-run.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from runtime.tools import Tool, awaited

SITE_URL = "https://permtracker.app"
BASE_URL = f"{SITE_URL}/v1"
API_KEY_ENV = "PERMTRACKER_API_KEY"
EXPORT_KINDS = ["cases", "employers", "law-firms", "occupations"]
TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 20_000
CREDIT = 'end the answer with "Data: PERM Tracker (https://permtracker.app)"'
TRUNCATED_HEADER = "X-Export-Truncated"
TRUNCATED = "truncated"
TRUNCATED_NOTE = "Cut short: more rows matched than one export returns. Say so, or narrow the filters."


MATCH_NOTES = {
    "exact": "Match: exact. The numbers below are this employer's record.",
    "possible": (
        "Match: possible. Tell the user which employer this is (name and any location) and ask "
        "them to confirm it is theirs before relying on the numbers."
    ),
    "none": (
        "Match: none. No PERM record under this name. That is not proof the employer never "
        "sponsors: filings are under the legal name, so ask for it (on an offer letter or pay "
        "stub) and look it up again."
    ),
}

MATCH_EMPLOYER = """\
Looks up an employer's record of sponsoring green cards (PERM labor certification) and H-1B visas \
(LCAs), from Department of Labor filings.

Use it for questions about one employer, for example:
- "Does Acme sponsor green cards?" or "Will this company sponsor my H-1B?"
- "How many PERM cases has Google filed, and how many were certified or denied?"
- "Is this employer still filing PERM cases?" (look at the newest filing and filings in the last 12 months)
- "How many of Microsoft's PERM cases are pending right now?"
- comparing job offers by how often each employer sponsors

Don't use it for:
- the status of one case (use perm_case_status)
- processing times, visa bulletin dates or what the rules say

How to use it:
- Pass the employer's name as the user gave it.
- The result says whether the match is exact, possible or none, and what to do for each.
- Decisions and the certified share come from DOL's published files, which reach only to the end \
of DOL's latest quarterly release (months behind); pending cases and the newest filing are current.
- The certified share is left out when the employer has too few decisions for it to mean much.
- The numbers are the employer's history, not a prediction for the user's own case.

Returns:
- the match (exact, possible or none) and the employer's name and slug
- PERM: published decisions, certified, denied, certified share, cases pending at DOL now, the \
newest filing date, and filings in the last 12 months
- H-1B LCA and prevailing wage request counts
- a first line with the date of the data (of DOL's latest published release): mention it when how \
current the numbers are matters"""

FIND_SOC_CODE = """\
Looks up SOC occupation codes. A SOC code is the government's code for an occupation (e.g. \
15-1252, Software Developers). Every PERM, prevailing wage and H-1B LCA filing is made under one, \
and with the job's location and requirements it sets the minimum wage the employer must offer.

Use it for questions about an occupation rather than an employer, for example:
- "What SOC code would my job, data scientist, be filed under?"
- "What does SOC 15-2051 cover, and which job titles are filed under it?"
- "What's the median offered wage in PERM cases for software developers?"
- "How often are PERM cases in my occupation approved?"
- "My old filing used a 2010 SOC code; what is it now?"

How to use it:
- Pass a job title or a SOC code.
- A title can map to several codes: give the common ones with their shares, and say the right code \
depends on the actual job duties, not the title.
- Approval rates and wages are history, not a prediction for the user's case; they are left out \
for codes with too few cases.

Returns:
- for a title: the codes employers filed it under in DOL's PERM, prevailing wage and LCA files, \
with each code's share of the title's filings
- for a code: its title, O*NET description and core tasks, the titles most filed under it, its \
PERM approval rate and median offered wage, and for a 2010 code the 2018 codes it became
- a first line with the date of the data: mention it when how current the numbers are matters"""

PERM_CASE_STATUS = """\
Looks up one Department of Labor case by its case number: its current status, and once decided, \
DOL's record of the decision.

Use it when the user gives a DOL case number, for example:
- "What's the status of G-100-26045-123456?" (PERM cases start with G-100 or G-200)
- "Was my prevailing wage request P-100-… issued?" (wage requests start with P-100)
- "Is LCA I-200-… certified?" (H-1B LCAs start with I-200)
- H-2A, H-2B and CW-1 cases: H-300 (H-2A application), JO-A-300 (H-2A job order), H-400 (H-2B \
application), P-400 (H-2B wage request), C-500 (CW-1 application), P-500 (CW-1 wage request)
- getting a case's filing date, to pass to perm_decision_estimate or to compare with the queue

Don't use it for:
- USCIS receipt numbers (three letters then ten digits, like IOE0912345678 or EAC2190012345): \
those are not DOL cases and this tool can't look them up
- finding cases by employer, date or outcome (use export_search, or the daily page for one day)

How to use it:
- If the user wants a case's status but hasn't given the number, ask for it.
- Set live to true only when the number isn't found and the case is recent: it asks DOL directly \
(a limited daily allowance). A case already on record answers without it.
- A case that is still pending, or decided after DOL's latest quarterly release, carries only \
DOL's five live fields: number, employer, job title, filing date and status. Wage, law firm, \
worksite and occupation appear only once the case is in a published release.
- Statuses: Analyst Review is the ordinary queue, worked in filing order; Application On Hold, \
RFI Issued, NORD Issued, Pending Audit Response and Supervised Recruitment are outside that order; \
Certified, Denied and Withdrawn are final; Reconsideration Appeals follows a denial.

Returns:
- the case number, program, status, filing date, employer and job title
- once published: the decision and its date, the job, SOC code, offered wage, worksite and law firm
- for a seasonal case: its workers, work period and worksite"""

PERM_DECISION_ESTIMATE = """\
Estimates when the Department of Labor will decide a pending PERM case, from its case number or its \
filing date.

Use it for questions like:
- "When will my PERM be approved?"
- "My PERM was filed in February 2026; how much longer?"
- "My case number is G-100-26045-123456; when should I expect a decision?"

Don't use it for:
- a case that is already decided (use perm_case_status)
- the queue in general, with no case or date (use perm_queue)

How to use it:
- Pass case_number when the user gave one (its own status and filing date are used), otherwise \
filed_date. If they gave neither, ask.
- The estimate assumes the case is in analyst review, the ordinary queue. A case on hold, at an \
RFI, audit or NORD, or in supervised recruitment takes longer: say so.
- Say it is an estimate from how DOL has been processing cases, not a DOL prediction or a promise.
- Give the range, not just the likely date, and pass on the caveats.

Returns:
- a likely decision date, a range, how it was worked out, and caveats"""

PERM_QUEUE = """\
The Department of Labor's current processing times, and how many PERM cases are still pending for \
each filing month.

Use it for questions like:
- "How long is PERM taking right now?"
- "Which filing month is DOL working on?"
- "How big is the PERM backlog?"
- "How long are prevailing wage requests taking?"
- putting the user's own filing date in context: how many cases filed before theirs are still waiting

Don't use it for:
- one case's estimated decision date (use perm_decision_estimate)
- how many cases were decided or filed on a given day (use immigration_data_page: \
perm_daily_decisions or perm_filings_by_day)

How to use it:
- A queue's average days and the filing month it is working on are different measures: say which \
one you give.
- Analyst review is worked in filing order; other queues (audits, reconsiderations) are not, so \
their months and times differ.

Returns:
- for each of DOL's queues: the filing month it is working on and the average days to a decision
- pending PERM cases by filing month"""

VISA_BULLETIN = """\
A month's State Department visa bulletin: the priority date cutoffs for green cards, as printed.

Use it for questions like:
- "Is my priority date current?"
- "What's the EB-2 India cutoff this month?"
- "How far did EB-3 move since last month?" or "What was the EB-2 China date in October 2024?"

How to use it:
- Pass month as YYYY-MM (bulletins go back to 2005-06), or leave it out for the newest bulletin. \
The newest can be next month's: a bulletin comes out during the month before it applies.
- To compare movement, call it for both months.
- A priority date is current when it is earlier than the cutoff for the user's category and \
country of birth (chargeability); "C" means current for everyone and "U" means unavailable.
- Final action dates decide when a green card can be approved; dates for filing decide when an \
I-485 can be filed, but only in months USCIS says to use that chart: say so rather than assuming.

Returns:
- the bulletin's month, and cutoff dates for employment-based (EB-1 to EB-5, including EB-3 other \
workers) and family-based categories
- by country of birth: all chargeability areas, China, India, Mexico, Philippines
- both charts: final action dates and dates for filing"""

SEARCH_EMPLOYERS = """\
Searches employers that have filed PERM cases, by name.

Use it for:
- seeing the candidates when match_employer found no exact match
- browsing an employer's entities, e.g. "which Acme companies file PERM?"
- getting the slug employer_profile needs

Don't use it for:
- a quick yes or no on one employer's sponsorship (match_employer is enough)

Returns:
- matching employers, each with its PERM record (cases, certified, denied) and the slug to pass to \
employer_profile"""

EMPLOYER_PROFILE = """\
One employer's full PERM record.

Use it for deeper questions about one employer, for example:
- "How long do Google's PERM cases take?"
- "How many PERM cases did Acme file in the last year?"
- "How many of their cases are pending, and at what stage?" (e.g. on hold, at an RFI)

How to use it:
- Get the slug from search_employers first.
- Decisions and median days come from DOL's published files (months behind); pending cases by stage \
are from DOL's case status now.
- The numbers are the employer's history, not a prediction for the user's case.

Returns:
- cases, certified and denied, median days from filing to a decision, filings in the last 12 \
months, and how many of its cases are pending at DOL now, by stage"""

SEARCH_LAW_FIRMS = """\
Searches immigration law firms that file PERM cases, by name.

Use it for:
- a law firm the user names, e.g. their employer's firm, or "how does Fragomen do on PERM?"

Returns:
- matching firms, each with the slug to pass to law_firm_profile"""

LAW_FIRM_PROFILE = """\
One law firm's PERM record.

Use it for:
- questions about a law firm's PERM filings, after search_law_firms gives you its slug

How to use it:
- The numbers describe the firm's past filings, not the quality of its advice or the outcome of \
the user's case: never rank or recommend firms from them.

Returns:
- cases and outcomes from DOL's published files, and how many of its cases are pending at DOL now, \
by stage"""

SEARCH_OCCUPATIONS = """\
Searches occupations that PERM cases were filed under, by title.

Use it for:
- getting from a job title to an occupation's PERM record

Don't use it for:
- finding which SOC code a job title is filed under (find_soc_code is more direct)

Returns:
- matching occupations, each with the slug to pass to occupation_profile"""

OCCUPATION_PROFILE = """\
One occupation's PERM record.

Use it for questions like:
- "How are PERM cases for software developers doing?"
- "What do employers usually offer data scientists in PERM cases?"

How to use it:
- Get the slug from search_occupations first.
- The numbers are history, not a prediction for the user's case.

Returns:
- the SOC code, case outcomes and the median offered wage"""

EXPORT_SEARCH = """\
Returns all the results of a case, employer, law firm or occupation search, past the first page, \
as JSON (up to 1,000 rows).

Use it only when a question needs the complete list, for example:
- "Every PERM case Acme filed in California in 2026"
- "All denied PERM cases for software developers filed in March 2025"

Don't use it for:
- one employer, firm or occupation's totals (the search and profile tools are cheaper)
- one case (use perm_case_status)
- what DOL decided on a given day (use immigration_data_page with perm_daily_decisions)

How to use it:
- kind is cases, employers, law-firms or occupations; query searches by name (for cases, the \
employer's name).
- For cases, give at least one of query, firm, state or occupation; then narrow with from_date and \
to_date (the filing date, not the decision date) and outcome (certified, denied or withdrawn).
- Law firm, worksite state, occupation and wage exist only on cases in DOL's published files, \
which reach only to DOL's latest quarterly release: filtering by firm, state or occupation leaves \
out pending cases and recent decisions.
- If more than 1,000 rows match, the result says it was cut short: narrow the filters rather than \
asking for everything. Long answers are clipped too.

Returns:
- every matching record, up to 1,000"""

# topic -> (path, what the page shows). Only pages with data; the site's calculators run in the
# browser, so their pages have no answers to read.
PAGES: dict[str, tuple[str, str]] = {
    "today": ("/today", "one-page snapshot: the filing date of DOL's typical decision today for wage requests and PERM, filings vs decisions over 30 days, USCIS's I-140 median, and the newest visa bulletin's EB-2 movement"),
    "perm_daily_decisions": ("/perm-decision-activity", "PERM decisions per day for the last 30 days (decided, certified, denied, withdrawn), the latest working day vs a typical one, and the cases DOL moved on the newest day: the day's counts by status change and program, then a SAMPLE of the first 60 rows (case number, program, employer, job title, filing date, status from and to), not every case; the rows mix PERM and wage requests and can't be filtered here"),
    "perm_filings_by_day": ("/filings-by-day", "how many PERM cases, wage requests, H-1B LCAs and H-2 cases were filed today so far, yesterday, in the last 7 and 30 days, and PERM filings for each of the last 30 days"),
    "perm_processing_times": ("/perm-processing-times", "the filing month DOL's PERM analyst review is deciding now, DOL's average days to a decision (last 30, 90 and 180 days), the prevailing wage request being decided, and the median days from filing to decision for each month since Oct 2023"),
    "perm_backlog": ("/perm-queue", "the PERM backlog: cases still waiting at DOL, the filing month being worked and how much of it is decided, filing months cleared per month, and the waiting cases by stage (analyst review, on hold, RFI, NORD, audit, appeals)"),
    "perm_by_filing_month": ("/perm-by-the-numbers", "for each filing month, how many PERM cases are decided and how many are still waiting at DOL, and the waiting cases by stage"),
    "perm_decided_cases": ("/perm-cases", "PERM decisions counted by month from DOL's published files, with totals by outcome (certified, denied, withdrawn) and by fiscal year of decision"),
    "perm_denial_rates": ("/perm-denial-risk", "PERM denial rates by factor: wage band, job not full time, occupation, worksite state and fiscal year, against the overall rate"),
    "perm_rfi_audits": ("/perm-rfi-audit", "open RFIs (requests for information) and how far into the wait they arrive, the pending PERM cases at each stage (analyst review, on hold, RFI, NORD, supervised recruitment) with the employers holding many, and audit counts"),
    "perm_layoff_disclosures": ("/perm-layoff-disclosures", "how many PERM applications said the employer had layoffs in the occupation, by employer and fiscal year, and how often those were certified"),
    "perm_case_statuses": ("/perm-case-statuses", "what each DOL case status means (analyst review, on hold, RFI issued, NORD issued, audit, supervised recruitment, certified, denied, withdrawn, appeals), who acts next, and how many pending cases are in each today"),
    "perm_recruitment": ("/perm-recruitment", "when employers run PERM recruitment before filing: median days from each step (job order, Sunday ads, last step) to filing, and how often each kind of ad is used"),
    "prevailing_wage_requests": ("/pwd-cases", "prevailing wage requests: whether DOL is keeping up (requests decided vs filed each week), and the median days from filing to determination by month"),
    "uscis_processing_times": ("/uscis-processing-times", "USCIS median processing months by form (I-129, I-140, employment-based I-485, I-765, I-131) for the latest quarter, with pending and decided counts"),
    "uscis_rfe_rates": ("/uscis-rfe-rates", "how often USCIS issued a request for evidence on H-1B (and other) petitions, by month and fiscal year, and how often those were approved"),
    "i140_awaiting_visa": ("/i140-awaiting-visa", "approved I-140 petitions waiting for a visa number, by category (EB-1, EB-2, EB-3, EB-3 other workers) and country of birth"),
    "i485_by_field_office": ("/i485-by-field-office", "employment-based I-485s pending, approved and denied at each USCIS field office for the latest quarter"),
    "nvc_waiting_list": ("/nvc-waiting-list", "the State Department's waiting list of immigrant visa applicants at consulates (not I-485s), by category and country, and the current pause on consular interviews"),
    "consular_posts": ("/consular-posts", "the status of immigrant visa interviews at US consulates, including the worldwide postponement since late August 2026"),
    "visa_issuances": ("/visa-issuances", "employment immigrant visas issued abroad each month by category (EB-1 to EB-5), and the effect of the 75-country pause"),
    "green_card_timelines": ("/green-card-timelines", "green card timelines reported by applicants, step by step (only medians once 5 reports exist)"),
    "visa_bulletin_history": ("/visa-bulletin", "the newest visa bulletin with its movement, which chart USCIS accepts for I-485 filing this month, and how each category moved in the same month of earlier years"),
    "perm_by_state": ("/perm-by-state", "PERM filings, approval and denial rates, median days and median wage by worksite state"),
    "perm_by_city": ("/perm-cities", "PERM decisions, approval rate, median wage and denials by worksite city"),
    "perm_by_industry": ("/perm-industries", "PERM decisions, approval rate, median wage and denials by industry (NAICS)"),
    "perm_by_country": ("/perm-countries", "PERM decisions, approval rate, median wage and denials by the worker's country of citizenship (only cases on DOL's old form, decided through FY2024)"),
    "perm_wages": ("/perm-wages", "wages offered in certified PERM cases: the overall spread and each busy occupation's median and range"),
    "perm_top_sponsors": ("/perm-employers", "the employers with the most PERM filings, and the ones with the most cases waiting at DOL right now"),
    "lca_cases": ("/lca-cases", "H-1B LCAs by filing month, decided and still waiting at DOL"),
    "h1b_wages": ("/lca-wages", "offered H-1B wages from LCAs, by occupation, worksite state, fiscal year and outcome"),
    "lca_wage_sources": ("/lca-wage-sources", "where H-1B LCAs took their prevailing wage from (DOL's OES data, private surveys, union contracts), by fiscal year"),
    "h1b_employers": ("/h1b-employers", "the employers with the most H-1B LCAs certified, or USCIS approvals, by fiscal year and state"),
    "h1b_cap_exempt_employers": ("/h1b-cap-exempt-employers", "H-1B cap-exempt employers (universities, affiliated nonprofits, research organizations) and when they file"),
    "h1b_lottery_odds": ("/h1b-lottery-odds", "H-1B lottery registrations, selections and the selection rate by fiscal year, and how the wage-weighted lottery from FY2027 changes the odds by wage level"),
    "opt_employers": ("/opt-employers", "the employers of the most students on OPT and STEM OPT, by year"),
    "seasonal_cases": ("/seasonal-cases", "H-2A, H-2B and CW-1 applications, job orders and wage requests: how many were filed and how many are still in process"),
    "visa_wait_times": ("/visa-wait-times", "the wait for a visa interview appointment at each US consulate (work visas and others)"),
    "visa_refusals": ("/visa-refusals", "visa refusals by ground (public charge, 214(b) and others), by year"),
    "layoff_notices": ("/layoffs", "WARN layoff notices from employers that sponsor PERM, by state and employer, with employees affected"),
    "debarments": ("/debarments", "employers, attorneys and agents DOL has barred from filing, the violation, and whether the bar is in force today"),
    "wage_violations": ("/wage-violations", "Wage and Hour Division findings against H-1B, H-2A and H-2B employers: cases, back wages and workers owed"),
    "employers_under_review": ("/perm-employers/under-review", "employers with PERM cases on hold, at an RFI or deficiency notice, in supervised recruitment or under appeal, with how many and since when"),
    "law_firm_rankings": ("/law-firm-rankings", "law firms ranked by PERM decisions in the latest fiscal year, with employers served and certified share"),
    "bright_outlook_jobs": ("/bright-outlook-jobs", "O*NET Bright Outlook occupations (fast-growing or many openings) and how often employers sponsor them in PERM and H-1B"),
    "policy_changes": ("/policy-changes", "a summary list of recent policy changes; for a rule's text and dates, use search_federal_register first"),
}

IMMIGRATION_DATA_PAGE = """\
Reads a published statistics page on US employment-based immigration, for data the other DOL \
tools don't return.

Pages, by topic:
{topics}

Use it when one of these pages answers the question and no other tool does, for example:
- "How many PERM cases were approved yesterday?" (perm_daily_decisions)
- "Was any PERM case filed in February 2026 certified today?" (perm_daily_decisions: look at the \
filing dates in its sample of the day's cases)
- "How many PERM cases were filed this week?" (perm_filings_by_day)
- "What are the odds in the H-1B lottery?" (h1b_lottery_odds)
- "How long is USCIS taking on I-485s?" (uscis_processing_times)
- "How long is the visa interview wait in Mumbai?" (visa_wait_times)

Don't use it for:
- anything perm_case_status, perm_queue, visa_bulletin or the other DOL tools cover: they return \
exact records, while this returns the text of a web page

How to use it:
- Use it before searching the web.
- Read the numbers carefully, and say what date the page gives for its data. Many figures come \
from DOL's or USCIS's published files, which run months behind; say so when it matters.
- A page that lists records may show only some of them (perm_daily_decisions shows 60 of the \
day's cases): answer from the totals, and when you use the listed rows, say they are a sample. \
Finding no match in a sample doesn't mean there was none.
- If the page doesn't hold the answer, say so or try web_search.

Returns:
- a line saying what the page shows and the credit to give, then the page's text, with tables as \
text, up to 20,000 characters"""


def permtracker_tools(
    client: PermTrackerClient | None = None, read_page: Callable[[str], str] | None = None
) -> list[Tool]:
    """The keyless tools and the page reader, plus the keyed tools when the client has a key.

    `read_page(url)` returns a page's text; it defaults to read_web_page's reader.
    """
    client = client or PermTrackerClient()
    read_page = read_page or _default_page_reader()
    tools = [
        _tool(
            "match_employer",
            MATCH_EMPLOYER,
            {"name": _string("The employer's name as the user gave it, e.g. 'Google' or 'Acme Corp LLC'")},
            ["name"],
            lambda name: match_employer(client, name),
        ),
        _tool(
            "find_soc_code",
            FIND_SOC_CODE,
            {"query": _string("A job title, e.g. 'data scientist', or a SOC code, e.g. '15-2051'")},
            ["query"],
            lambda query: lookup(client, "/lookup/soc", {"q": query}),
        ),
    ]
    tools.append(
        _tool(
            "immigration_data_page",
            IMMIGRATION_DATA_PAGE.format(
                topics="\n".join(f"- {topic}: {about}" for topic, (_, about) in PAGES.items())
            ),
            {"topic": {"type": "string", "enum": list(PAGES), "description": "Which page to read"}},
            ["topic"],
            lambda topic: data_page(read_page, topic),
        )
    )
    if client.api_key:
        tools += keyed_tools(client)
    return tools


async def data_page(read_page: Callable[[str], str], topic: str) -> str:
    """The text of the page for `topic`, under a line naming what it shows.

    The page reader is a plain function (the web search library has no async API), so it runs
    in a worker thread.
    """
    path, about = PAGES[topic]
    text = await asyncio.to_thread(read_page, SITE_URL + path)
    return f"Page: {about}\nWhen you use it, {CREDIT}.\n\n{text}"


def keyed_tools(client: PermTrackerClient) -> list[Tool]:
    """The tools for the endpoints that need an API key."""
    query = _string("Name or words to search for")
    slug = _string("The slug from a search result, e.g. 'google-llc'")
    return [
        _tool(
            "perm_case_status",
            PERM_CASE_STATUS,
            {
                "case_number": _string("The DOL case number, e.g. G-100-26045-123456"),
                "live": {
                    "type": "boolean",
                    "description": "Ask DOL directly, only when a recent case number isn't found",
                },
            },
            ["case_number"],
            lambda case_number, live=False: lookup(
                client, f"/cases/{_segment(case_number)}", {"live": 1} if live else {}
            ),
        ),
        _tool(
            "perm_decision_estimate",
            PERM_DECISION_ESTIMATE,
            {
                "case_number": _string("The PERM case number, e.g. G-100-26045-123456, when the user gave one"),
                "filed_date": _string("The PERM filing date, YYYY-MM-DD, when there is no case number"),
            },
            [],
            lambda case_number=None, filed_date=None: estimate(client, case_number, filed_date),
        ),
        _tool("perm_queue", PERM_QUEUE, {}, [], lambda: lookup(client, "/queue", {})),
        _tool(
            "visa_bulletin",
            VISA_BULLETIN,
            {"month": _string("YYYY-MM; the newest bulletin if omitted")},
            [],
            lambda month=None: lookup(client, "/visa-bulletin", {"month": month} if month else {}),
        ),
        _tool("search_employers", SEARCH_EMPLOYERS, {"query": query}, ["query"],
              lambda query: lookup(client, "/employers", {"q": query})),
        _tool("employer_profile", EMPLOYER_PROFILE, {"slug": slug}, ["slug"],
              lambda slug: lookup(client, f"/employers/{_segment(slug)}", {})),
        _tool("search_law_firms", SEARCH_LAW_FIRMS, {"query": query}, ["query"],
              lambda query: lookup(client, "/law-firms", {"q": query})),
        _tool("law_firm_profile", LAW_FIRM_PROFILE, {"slug": slug}, ["slug"],
              lambda slug: lookup(client, f"/law-firms/{_segment(slug)}", {})),
        _tool("search_occupations", SEARCH_OCCUPATIONS, {"query": query}, ["query"],
              lambda query: lookup(client, "/occupations", {"q": query})),
        _tool("occupation_profile", OCCUPATION_PROFILE, {"slug": slug}, ["slug"],
              lambda slug: lookup(client, f"/occupations/{_segment(slug)}", {})),
        _tool(
            "export_search",
            EXPORT_SEARCH,
            {
                "kind": {"type": "string", "enum": EXPORT_KINDS},
                "query": _string("Words to search for by name"),
                "firm": _string("cases only: law firm"),
                "state": _string("cases only: worksite state, e.g. CA"),
                "occupation": _string("cases only: occupation"),
                "from_date": _string("cases only: filed on or after, YYYY-MM-DD"),
                "to_date": _string("cases only: filed on or before, YYYY-MM-DD"),
                "outcome": _string("cases only: decision, e.g. certified or denied"),
            },
            ["kind"],
            lambda kind, **filters: lookup(client, f"/exports/{kind}", _export_params(filters)),
        ),
    ]


async def estimate(client: PermTrackerClient, case_number: str | None, filed_date: str | None) -> str:
    """The estimate for a case number (preferred: the API uses the case's own status) or a filing date."""
    if case_number:
        return await lookup(client, "/estimate", {"case": case_number.strip()})
    if filed_date:
        return await lookup(client, "/estimate", {"filed": filed_date.strip()})
    raise DataLookupError("give case_number or filed_date; ask the user for one if you have neither")


async def match_employer(client: PermTrackerClient, name: str) -> str:
    """The lookup, with a line saying how far to trust the match and what to do next."""
    body = await client.get("/lookup/employer", {"name": name})
    data = body.get("data") if isinstance(body, dict) else None
    match = data.get("match") if isinstance(data, dict) else None
    return render(body, MATCH_NOTES.get(match) if isinstance(match, str) else None)


async def lookup(client: PermTrackerClient, path: str, params: dict[str, Any]) -> str:
    """GET an endpoint and render its answer."""
    return render(await client.get(path, params))


class DataLookupError(Exception):
    """An API call that failed, with what to do about it when there is something to do."""


class PermTrackerClient:
    def __init__(self, api_key: str | None = None, http: httpx.AsyncClient | None = None):
        """`api_key` defaults to PERMTRACKER_API_KEY; `http` is for tests."""
        self.api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "").strip()
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def get(self, path: str, params: dict[str, Any]) -> Any:
        """GET an endpoint and return its decoded JSON (or text, if it isn't JSON)."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = await self.http.get(BASE_URL + path, params=params, headers=headers)
        except httpx.TimeoutException:
            raise DataLookupError("the lookup didn't answer in time; try again shortly") from None
        except httpx.HTTPError as e:
            raise DataLookupError(f"couldn't reach the lookup service: {e}") from None
        if response.status_code >= 400:
            raise DataLookupError(_error_message(response))
        try:
            body = response.json()
        except ValueError:
            return response.text
        if isinstance(body, dict) and response.headers.get(TRUNCATED_HEADER, "").lower() in ("1", "true"):
            body[TRUNCATED] = True
        return body


def render(body: Any, note: str | None = None) -> str:
    """A line with the data's date and the credit to give, an optional note, then the data as
    JSON, clipped. An export cut short at the plan's row limit says so in the note."""
    if isinstance(body, str):
        return _clip(body)
    if not isinstance(body, dict) or "data" not in body:
        return _clip(json.dumps(body, ensure_ascii=False))
    as_of = (body.get("meta") or {}).get("asOf")
    dated = f"Department of Labor data as of {as_of}" if as_of else "Department of Labor data"
    if body.get(TRUNCATED):
        note = TRUNCATED_NOTE
    lines = [f"{dated}; when you use it, {CREDIT}", note, json.dumps(body["data"], ensure_ascii=False)]
    return _clip("\n".join(line for line in lines if line))


def _error_message(response: httpx.Response) -> str:
    """The API's error code and message, with a hint for the ones the model can act on."""
    code, message = "", ""
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            code, message = error.get("code", ""), error.get("message", "")
        elif isinstance(error, str):
            code = error
    except (ValueError, AttributeError):
        pass
    text = f"HTTP {response.status_code}" + (f" {code}" if code else "") + (f": {message}" if message else "")
    if response.status_code == 401:
        return f"{text}. The service refused its API key, so this lookup isn't available right now"
    if response.status_code == 404:
        return f"{text}. No record of that"
    if response.status_code == 429:
        wait = response.headers.get("Retry-After")
        return f"{text}. Rate limited" + (f"; retry in {wait} seconds" if wait else "")
    return text


def _default_page_reader() -> Callable[[str], str]:
    """read_web_page's reader, imported here so tests can pass their own without ddgs."""
    from ddgs import DDGS

    from runtime.web_tools import TIMEOUT_SECONDS as WEB_TIMEOUT, read_web_page

    search = DDGS(timeout=WEB_TIMEOUT)
    return lambda url: read_web_page(search, url)


def _export_params(filters: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments -> export query parameters, always as JSON."""
    names = {"query": "q", "from_date": "from", "to_date": "to"}
    params = {names.get(k, k): v for k, v in filters.items() if v not in (None, "")}
    params["format"] = "json"
    return params


def _tool(name, description, properties, required, fn) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        fn=awaited(fn),
        idempotent=True,
    )


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def _segment(value: str) -> str:
    """A value as one URL path segment, so it can't reach a different endpoint."""
    return quote(value.strip(), safe="")


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… clipped: {len(text):,} characters in all; narrow the search"
