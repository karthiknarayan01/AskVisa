// What each tool's stamp says: a short title in the stamp, and a detail from its arguments.

type Args = Record<string, unknown>;

const str = (value: unknown) => (typeof value === "string" ? value : value == null ? "" : String(value));
const words = (value: unknown) => str(value).replace(/_/g, " ");

const TITLES: Record<string, (args: Args) => [string, string]> = {
  match_employer: (a) => ["Employer record", str(a.name)],
  find_soc_code: (a) => ["Occupation code", str(a.query)],
  immigration_data_page: (a) => ["Statistics", words(a.topic)],
  perm_case_status: (a) => ["DOL case", str(a.case_number)],
  perm_decision_estimate: (a) => ["PERM estimate", str(a.filed_date) && `filed ${str(a.filed_date)}`],
  perm_queue: () => ["PERM queue", ""],
  visa_bulletin: (a) => ["Visa bulletin", str(a.month)],
  search_employers: (a) => ["Employers", str(a.query)],
  employer_profile: (a) => ["Employer profile", words(a.slug)],
  search_law_firms: (a) => ["Law firms", str(a.query)],
  law_firm_profile: (a) => ["Law firm", words(a.slug)],
  search_occupations: (a) => ["Occupations", str(a.query)],
  occupation_profile: (a) => ["Occupation", words(a.slug)],
  export_search: (a) => ["Full list", words(a.kind)],
  search_regulations: (a) => ["Regulations", str(a.query)],
  read_regulation: (a) => [`${str(a.title) || "8"} CFR ${str(a.section)}${str(a.paragraph)}`, ""],
  search_federal_register: (a) => ["Federal Register", str(a.query)],
  read_federal_register_document: (a) => ["Federal Register", str(a.document_number)],
  date_math: () => ["Date math", ""],
  opt_unemployment_days: () => ["OPT days", ""],
  grace_period_end: (a) => ["Grace period", str(a.status)],
  max_stay_end: (a) => ["Maximum stay", str(a.classification)],
  naturalization_filing_date: () => ["Citizenship date", ""],
  web_search: (a) => ["Web search", str(a.query)],
  read_web_page: (a) => ["Web page", host(str(a.url))],
};

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** The stamp's title and detail for a tool call. */
export function stampText(name: string, args: Args = {}): [string, string] {
  const make = TITLES[name];
  return make ? make(args) : [words(name), ""];
}

/** A small tilt between -3 and 3 degrees, the same every time for the same call. */
export function tilt(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return ((Math.abs(hash) % 61) - 30) / 10;
}
