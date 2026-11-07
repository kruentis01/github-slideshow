# JobBot: find UK jobs near you and pre-fill the applications

JobBot reads your CV and asks about your preferences and availability. It then works through three commands:

| Command | What it does |
|---|---|
| `jobbot scan` | Searches for jobs within a set number of miles of a UK postcode and reads each job description. |
| `jobbot table` | Builds a table showing **contract length**, **advertised shifts**, **hourly rate** and a **short summary** for each job. Every job has its own toggle button. |
| `jobbot apply` | Opens only the jobs you toggled on and fills in their application forms. **It never submits anything.** You check each form and press submit yourself. |

## Install

Python 3.10 or newer is required.

```bash
cd job-autofill-bot
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
python -m playwright install chromium                  # the browser `apply` drives
```

If you'd rather use the Google Chrome already on your computer, skip the last line and run `jobbot apply --chrome`.

## Job board API keys (free)

JobBot reads listings through the official job board APIs. It doesn't scrape search pages.

| Board | Get a key | Put in `~/.jobbot/.env` |
|---|---|---|
| **Reed** (recommended, returns full descriptions) | <https://www.reed.co.uk/developers/jobseeker> | `REED_API_KEY=...` |
| Adzuna | <https://developer.adzuna.com/> | `ADZUNA_APP_ID=...` and `ADZUNA_APP_KEY=...` |

On Windows, Notepad may save the file as `.env.txt`; JobBot reads that too. See `.env.example`. If you haven't added any keys, `scan` uses 7 built-in **demo** jobs near Manchester. Their "apply" links open a practice form on your own computer, so you can try the whole flow safely.

## Use it

### 0. Scan your CV and set preferences (first run only)

```bash
jobbot setup --cv ~/Documents/my_cv.pdf
```

JobBot reads PDF, DOCX or TXT CVs. It pulls out your name, email, phone, address, postcode, LinkedIn, current role, skills and summary, and shows each value so you can correct it. Then it asks:

* **Preferences:** job keywords, the postcode to search from, search radius in miles, minimum hourly rate, the hourly rate to put on applications, and the contract types you want.
* **Availability:** when you can start, notice period, which days you can work, hours per week, whether you can work days, evenings, nights or weekends, right to work in the UK, driving licence and own transport.

Everything is saved in `~/.jobbot/profile.json`. Run `jobbot profile` to view it, or `jobbot setup` again to change it. You can edit `cover_letter_template` in that file to change the cover letter.

### 1. Scan

```bash
jobbot scan                                   # uses your saved postcode, radius and keywords
jobbot scan -p "LS6 3AB" -r 5 -k "barista"    # or override them
jobbot scan -s reed -s adzuna -n 100          # pick boards, up to 100 results each
jobbot scan --fetch-pages                     # Adzuna: also download each advert page for the full text
```

For each job, JobBot works out:

* **Hourly rate:** taken from the advert text, or from the board's salary fields. Annual, daily and weekly pay is converted to an hourly figure and marked "≈ … estimated from £24,500/yr".
* **Contract length:** the type (permanent, temporary, fixed-term, temp to perm, zero hours, maternity cover, seasonal and so on) plus a length if one is given ("6 months", "12 weeks", "until March 2027", "ongoing"), and full or part time.
* **Advertised shifts:** days, nights, evenings, earlies or lates, weekends, day ranges like Mon–Fri, shift patterns like 4 on / 4 off, time ranges like 7pm–7am, and hours per week.
* **Summary:** the one or two sentences that describe the role, with recruiter boilerplate skipped.
* **Fit:** which of your CV skills the advert mentions, and warnings when it clashes with your answers (night shifts when you said no to nights, pay below your minimum, and so on).

Results go into `~/.jobbot/jobs.json`. Scanning again adds new jobs and keeps your toggles. Use `--fresh` to start over.

### 2. Table and toggles

```bash
jobbot table              # opens http://127.0.0.1:8765 in your browser
jobbot table --terminal   # same table in the terminal; type row numbers to toggle (e.g. 1 3 5-7)
jobbot table --export shortlist.html    # read-only copy to share
```

Every row has a toggle switch, and your choices are saved as you click. You can filter, sort by hourly rate, hide jobs that clash with your availability, and expand the full description. Press Ctrl+C in the terminal when you're done.

### 3. Fill the applications (no submitting)

```bash
jobbot apply
```

For each job you toggled on, JobBot:

1. Opens the job in a browser window. If the advert page has no form, it follows the advert's **Apply** link to the form.
2. Fills in the fields it recognises, including fields inside embedded iframes: name, email, phone, address, town, postcode, country, LinkedIn, current role and employer, years of experience, start date, notice period, hours, availability, expected hourly rate, right to work, sponsorship, driving licence and own transport, and "how did you hear about us". It uploads your **CV** and writes a **cover letter** for that job from your template, mentioning the job title, the company and your matching skills.
3. Outlines filled fields in **green** and required fields it couldn't fill in **orange**, and lists both in the terminal.
4. On sites where "Apply" is a button rather than a link (Reed, for example), JobBot stops at the advert and asks you to click Apply yourself, because on some sites that button sends your saved CV straight away. Click it, then press **r** and JobBot fills the questions that appear, including pop-ups like Reed's "Application questions".
5. Waits for you in the terminal. Press **Enter** to move to the next job. On multi-page forms, click the site's Next button yourself, then press **r** and JobBot fills the new page.

When every job is done, all the tabs stay open. Check each one, finish anything orange, tick consent boxes yourself and press the site's submit button. Press Enter in the terminal only when you've finished, because that closes the browser.

How JobBot makes sure it never submits:

* It never clicks submit, send, next or continue buttons and never presses Enter. It only types into fields, picks options, ticks the yes/no answers you gave during setup, and follows "Apply" *links*.
* While it is typing, a guard on the page blocks any form submission. It removes the guard afterwards so you can submit.
* It leaves terms, privacy, consent and marketing boxes unticked.
* It never touches passwords, payment or bank details, National Insurance numbers, dates of birth or captchas.

#### If a site won't let you sign in (for example Reed)

Some sign-in pages refuse a browser that a program has started. In that case, open the browser yourself first:

```bash
jobbot chrome            # opens a separate Chrome window on Reed's sign-in page
# sign in to Reed and any other job sites in that window, leave it open, then:
jobbot apply --connect
```

`jobbot chrome` starts your installed Google Chrome normally, with a separate JobBot profile (`~/.jobbot/chrome-profile`), so your everyday Chrome isn't touched and your sign-ins are kept for next time. `apply --connect` opens each application in a new tab of that window and leaves the window and tabs open when it finishes. While that window is open, other programs on your computer could control it the same way, so close it when you've finished applying.

JobBot uses its own browser profile (`~/.jobbot/browser-profile`). If a site asks you to sign in, sign in once in that window and the login is remembered for next time.

Other options: `--job <id>` fills only some of the toggled jobs, `--overwrite` replaces values already in a form, `--include-done` refills jobs already done, and `--no-follow` stays on the advert page.

## Good to know

* **Forms vary a lot.** Big applicant tracking systems (Workday, Taleo and similar) often need an account and use custom widgets. JobBot fills what it can identify and highlights the rest. Always read the form before you submit it.
* **Shown "Not stated"?** The advert didn't say. JobBot doesn't guess.
* **Postcode distances** come from [postcodes.io](https://postcodes.io) (free, no key). If it can't be reached, JobBot relies on the job board's own radius filter.
* **Terms of use:** JobBot reads listings through the boards' official APIs. `--fetch-pages` downloads Adzuna advert pages one at a time, with a delay between them. Use it within each site's terms.
* Your data stays on your computer in `~/.jobbot`. Set `JOBBOT_HOME` to keep it somewhere else.

## Development

```bash
pip install -e ".[test]"
pytest
```

The tests cover CV parsing (TXT, DOCX and PDF), postcode handling, pay, contract and shift extraction, the Reed and Adzuna clients (with mocked HTTP), the table server and toggles, and filling the practice form in a real headless browser. They also check that the form is not submitted and that you can still submit it yourself afterwards.

```
jobbot/
  cli.py          the commands: setup, scan, table, apply, profile
  cv_parser.py    reads a CV and extracts contact details, skills and roles
  profile.py      setup questions (preferences and availability)
  geo.py          UK postcode validation, postcodes.io lookup, distances
  sources/        Reed, Adzuna and offline demo job sources
  extract.py      pay, contract, shifts and summary from a description
  matching.py     matching skills and availability clashes
  scanner.py      the scan pipeline and saved results
  table.py        terminal table and local web server for the toggles
  table_page.html the toggle table page
  autofill.py     finds form fields, matches them to your profile, fills them
  applier.py      browser session and review loop for `apply`
  demo/           demo listings and a practice application form
```
