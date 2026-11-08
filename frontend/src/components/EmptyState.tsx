// The first screen: what to ask, as real questions grouped by the user's situation.

import { Guilloche } from "./Guilloche";

const EXAMPLES: { situation: string; questions: string[] }[] = [
  {
    situation: "On OPT",
    questions: [
      "I've been unemployed on OPT since October 1. How many days do I have left?",
      "My STEM OPT ends in March and I wasn't picked in the H-1B lottery. What can I do?",
    ],
  },
  {
    situation: "On an H-1B",
    questions: [
      "I was laid off and my last day was October 20. When does my grace period end?",
      "Does Infosys sponsor green cards?",
    ],
  },
  {
    situation: "Waiting for a green card",
    questions: [
      "Is an EB-2 India priority date of May 2013 current?",
      "My PERM was filed in March 2026. When will it be decided?",
    ],
  },
  {
    situation: "Becoming a citizen",
    questions: ["I got my green card on March 15, 2022. When can I apply for citizenship?"],
  },
];

export function EmptyState({ onPick }: { onPick: (question: string) => void }) {
  return (
    <section className="empty">
      <Guilloche className="empty__rosette" />
      <h1 className="empty__headline">Ask about your visa, your status, or your green&nbsp;card.</h1>
      <p className="empty__lede">
        Answers are worked out from Department of Labor records, the federal regulations and the Federal
        Register, with the date of every source.
      </p>
      <div className="examples">
        {EXAMPLES.map((group) => (
          <div className="examples__group" key={group.situation}>
            <h2 className="examples__situation">{group.situation}</h2>
            <ul>
              {group.questions.map((q) => (
                <li key={q}>
                  <button type="button" className="examples__question" onClick={() => onPick(q)}>{q}</button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
