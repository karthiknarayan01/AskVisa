// What a tool call says in the turn's one-line activity: "Searching the regulations for …"
// while it runs, "Searched the regulations for …" once it has.

type Args = Record<string, unknown>;

const str = (value: unknown) => (typeof value === "string" ? value.trim() : value == null ? "" : String(value));
const words = (value: unknown) => str(value).replace(/[_-]+/g, " ");
const quoted = (value: unknown) => (str(value) ? `“${str(value)}”` : "");

// Each verb's running and finished forms.
const VERBS = {
  check: ["Checking", "Checked"],
  count: ["Counting", "Counted"],
  estimate: ["Estimating", "Estimated"],
  find: ["Finding", "Found"],
  lookUp: ["Looking up", "Looked up"],
  pull: ["Pulling", "Pulled"],
  read: ["Reading", "Read"],
  search: ["Searching", "Searched"],
  workOut: ["Working out", "Worked out"],
} as const;

type Verb = keyof typeof VERBS;

const PHRASES: Record<string, (args: Args) => [Verb, string]> = {
  match_employer: (a) => ["lookUp", `${str(a.name) || "the employer"}’s sponsorship record`],
  find_soc_code: (a) => ["find", `the occupation code for ${quoted(a.query) || "the job"}`],
  immigration_data_page: (a) => ["read", `the ${words(a.topic) || "immigration"} statistics`],
  perm_case_status: (a) => ["check", `DOL case ${str(a.case_number)}`.trim()],
  perm_decision_estimate: () => ["estimate", "when the PERM case will be decided"],
  perm_queue: () => ["check", "DOL’s PERM queue"],
  visa_bulletin: (a) => ["read", str(a.month) ? `the ${monthName(str(a.month))} visa bulletin` : "the latest visa bulletin"],
  search_employers: (a) => ["search", `employers for ${quoted(a.query)}`],
  employer_profile: (a) => ["read", `${words(a.slug) || "the employer"}’s PERM record`],
  search_law_firms: (a) => ["search", `law firms for ${quoted(a.query)}`],
  law_firm_profile: (a) => ["read", `${words(a.slug) || "the law firm"}’s PERM record`],
  search_occupations: (a) => ["search", `occupations for ${quoted(a.query)}`],
  occupation_profile: (a) => ["read", `the PERM record for ${words(a.slug) || "the occupation"}`],
  export_search: (a) => ["pull", `the full list of ${words(a.kind) || "results"}`],
  search_regulations: (a) => ["search", `the regulations for ${quoted(a.query)}`],
  read_regulation: (a) => ["read", `${str(a.title) || "8"} CFR ${str(a.section)}${str(a.paragraph)}`],
  search_federal_register: (a) => ["search", `the Federal Register for ${quoted(a.query)}`],
  read_federal_register_document: (a) => ["read", `Federal Register document ${str(a.document_number)}`.trim()],
  date_math: () => ["workOut", "the dates"],
  opt_unemployment_days: () => ["count", "OPT unemployment days"],
  grace_period_end: (a) => ["workOut", `the ${str(a.status)} grace period`.replace("the  ", "the ")],
  max_stay_end: (a) => ["workOut", `the ${str(a.classification)} maximum stay`.replace("the  ", "the ")],
  naturalization_filing_date: () => ["workOut", "when you can file for citizenship"],
  web_search: (a) => ["search", `the web for ${quoted(a.query)}`],
  read_web_page: (a) => ["read", host(str(a.url)) || "a web page"],
};

/** The call as a running phrase and a finished one, e.g. ["Reading 8 CFR 214.1", "Read 8 CFR 214.1"]. */
export function describeCall(name: string, args: Args = {}): { doing: string; done: string } {
  const make = PHRASES[name];
  const [verb, object] = make ? make(args) : (["check", words(name)] as [Verb, string]);
  const [doing, done] = VERBS[verb];
  const tail = object.replace(/\s+for\s*$/, "");
  return { doing: `${doing} ${tail}`, done: `${done} ${tail}` };
}

function monthName(month: string): string {
  const [year, m] = month.split("-").map(Number);
  if (!year || !m || m < 1 || m > 12) return month;
  return new Date(year, m - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
