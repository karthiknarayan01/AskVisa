<div align="center">

# AskVisa

**Straight answers about your US visa, your status and your green card.**<br>
Ask in plain English. AskVisa checks the regulations, the government data and your dates, then tells you where you stand.

<!-- ![AskVisa answering a question](docs/images/hero.png) -->

</div>

---

## What you can ask

| if you're… | ask things like |
|---|---|
| 🎓 **On OPT** | *I've been unemployed since October 1. How many days do I have left?* |
| 💼 **On an H-1B** | *I was laid off on October 20. When does my grace period end?* · *Does Infosys sponsor green cards?* |
| 🪪 **Waiting for a green card** | *Is an EB-2 India priority date of May 2013 current?* · *My PERM was filed in March 2026. When will it be decided?* |
| 🇺🇸 **Becoming a citizen** | *I got my green card on March 15, 2022. When can I apply for citizenship?* |

Every answer shows its work. Each lookup appears as a stamp on the page, so you can see exactly what AskVisa checked:

- **Your dates, worked out.** Unemployment days on OPT, grace periods, maximum stay, when you can file for citizenship.
- **The rules themselves.** The current Code of Federal Regulations and the latest Federal Register notices.
- **Real immigration data.** Employers' sponsorship records, PERM and USCIS processing times, H-1B salaries and lottery odds, visa interview waits.
- **The web, as a last resort.** Recent news and official pages from USCIS, the State Department and DOL.

<!-- ![Lookups shown as stamps above the answer](docs/images/stamps.png) -->

## 🚀 Get started

You need a Mac or Linux computer with Python 3.11 or newer.

```bash
git clone https://github.com/karthiknarayan01/AskVisa
cd AskVisa
./start.sh
```

That's it. The first run sets everything up, asks which AI model to use, and opens AskVisa in your browser.

| | model | what you need |
|:-:|---|---|
| **1** | **Qwen, on your computer** | Nothing. It's free and private, and your questions never leave your machine. AskVisa installs [Ollama](https://ollama.com) (asking first) and downloads the model once (about 6.6 GB). |
| **2** | **Claude**, by Anthropic | an API key; faster and sharper |
| **3** | **DeepSeek** | an API key |

After the first run, `./start.sh` goes straight to the app. Run `./start.sh --setup` to switch models.

> [!TIP]
> Answers on your own computer take a little longer. AskVisa reads the rules and the data before it replies, and you can watch it work while it does.

<details>
<summary><b>More options</b></summary>

<br>

```bash
./start.sh --port 9000     # use another port (default 8000)
./start.sh --no-web        # never search the web
```

Putting `PERMTRACKER_API_KEY=...` in a `.env` file next to `start.sh` adds PERM case status, decision estimates, the visa bulletin and full employer profiles.

Your conversations are saved on your computer and listed in the sidebar.

</details>

## ⚖️ Good to know

AskVisa gives general information, not legal advice. Immigration decisions can be hard to undo, so for anything that matters, confirm with an immigration attorney.

<details>
<summary><b>For developers</b></summary>

<br>

- `./eval.sh` runs the eval suites: whether the agent picks the right lookup, gets the dates right and reasons through a case. They run fully local on Qwen by default.
- The server streams answers over a REST API. Its docs are at `http://127.0.0.1:8000/docs` while AskVisa runs.
- The web app is in `frontend/` (React and Vite): `npm run dev` there runs it against a running server.

</details>

<div align="center">
<sub>Employer and occupation data from <a href="https://permtracker.app">PERM Tracker</a>. Regulations from the <a href="https://www.ecfr.gov">eCFR</a> and the <a href="https://www.federalregister.gov">Federal Register</a>.</sub>
</div>
