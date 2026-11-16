# VAT Identifier Discovery

Proof of concept that tests whether UK VAT numbers can be discovered from company websites, for a
stratified sample of 420 active Companies House companies. The pipeline finds candidate numbers
and the evidence for them; whether a number belongs to the company is checked by hand.

## Summary

**Can a reliable UK VAT dataset be built from company websites? Only a small one.**

- The pipeline found a VAT number for 37 of 420 companies. HMRC confirmed all 37 as valid.
- Only **8 of the 37** belong to the sampled company (1.9% of the sample). The other 29 are real
  numbers of other businesses, so reporting everything found gives a **78% false-positive rate**.
- When the site also showed the company's own company number, the VAT number was correct for
  **7 of 7** companies. That rule is what makes a reliable subset possible.
- Coverage is limited by what companies publish, not by crawling effort: 145 companies had no
  website that could be found and 85 had a confirmed site with no VAT number on it.

## Repository layout

| Path | What it is |
|---|---|
| `README.md` | This file |
| `docs/PIPELINE.md` | Technical description of the pipeline, run commands, classification rules, known limitations |
| `src/` | Pipeline code: sample draw, search, filtering, fetching, extraction, classification, evaluation, verification summary |
| `tests/` | Unit tests |
| `config/blocklist.txt` | Domains never accepted as a company website |
| `sample/` | Tracked input files: the sample, the hand-checked pilot results, population bucket counts, and a README describing columns and values |
| `runs/` | One folder per pipeline run with its outputs, log and config; `runs/README.md` says which run is which |
| `requirements.txt` | Pinned Python dependencies |
| `.env.example` | Template for the local `.env` (search API key) |
| `data/` | Local only, git-ignored: Companies House bulk file, working copies of the inputs, search and page cache |

## How to reproduce

Setup, commands for Windows PowerShell and macOS/Linux, and what can be run without the local
`data/` folder are in [docs/PIPELINE.md](docs/PIPELINE.md). In short:

1. Install the pinned dependencies and run the unit tests.
2. Draw the sample from the Companies House snapshot of 2026-11-01 with `src/draw_sample.py`
   and check it against `sample/sample_blank.csv` ([sample/README.md](sample/README.md)).
3. Run the pipeline with `src/run_pipeline.py`; outputs go to a new folder under `runs/`
   ([runs/README.md](runs/README.md)).
4. Recompute the tables for the reported run with `src/evaluate.py` and
   `src/summarise_verification.py`.

## Part 1 Research

### Scope

I tested one route end to end: find the company's own website, read the VAT number from it, and
check that number with HMRC. After the first submission I added three smaller probes of the
other routes named in the brief: an adjacent identifier (EORI), a bulk web corpus (Common Crawl)
and public records. The first two were run on small samples, stated below. The public records
probe was built but not run on real data, so I make no claim about that route.

### Sources I used

| Source | Used for | Where it stops |
|---|---|---|
| [Companies House bulk data](https://download.companieshouse.gov.uk/en_output.html), snapshot 2026-11-01 | The population and the sample: name, company number, address, SIC codes, accounts category | No website and no VAT number. The site has to be found by searching on the name |
| [Companies House](https://www.gov.uk/government/organisations/companies-house) company records | Checking by hand whether two companies are related | Manual, one company at a time |
| [HMRC VAT number checker](https://www.tax.service.gov.uk/check-vat-number/enter-vat-details) | Verifying every number found. Returns the registered name and address | Works only from number to company. Checked by hand, because API access needs an application approved by HMRC, which takes up to 10 working days |
| [Electronic Commerce Regulations 2002, regulation 6](https://www.legislation.gov.uk/uksi/2002/2013/regulation/6) | The legal reason a website should show these numbers: its registration number (reg. 6(1)(d)) and, if its activity is subject to VAT, its VAT number (reg. 6(1)(g)) | An obligation, not a guarantee. Many sites in my sample do not comply |
| Brave Search API | Finding candidate websites, top 5 results per query | Paid. For small companies the results are dominated by directories |
| EORI numbers found on the fetched pages (`probes/eori_scan/`) | Testing whether another identifier leads to the VAT number | Only businesses trading goods across the border have one. The checker works from number to company only |
| [Common Crawl](https://commoncrawl.org/) index and stored pages (`probes/common_crawl/`) | Testing whether a bulk corpus holds the same evidence without searching and fetching | The public index failed on half my requests. Each crawl is a snapshot, months behind the live site |

### The trail

1. **Pilot by hand.** I searched for 30 sampled companies myself and recorded the results
   (`sample/pilot_results.csv`). This is the answer key the pipeline is compared against.
2. **Run 01, exact legal name search. Failed.** Directories were accepted as company websites,
   because they show exactly the legal name and company number I matched on. 199 companies were
   given a site, but on only 116 distinct domains; one listing site was accepted for 17 unrelated
   companies. Details in `runs/run_01_exact_name_search/RUN_NOTES.md`.
3. **Run 02, the reported run.** Two changes. A domain that appears for 2 or more sampled
   companies is treated as a directory and dropped (109 domains). If the exact query finds
   nothing, a looser query is tried; it produced the candidate site for 262 companies against
   100 for the exact query.
4. **Verification by hand.** Each of the 42 candidate numbers was entered in the HMRC checker and
   the returned name and address compared with Companies House. Where HMRC returned another
   company, I checked on Companies House and on the companies' own sites whether the two were
   related.
   5. **EORI probe.** A UK EORI number for a VAT-registered business is `GB` + its VAT number +
   `000`, so I scanned the 249 companies' fetched pages for EORI numbers. 3 sites mention EORI and
   2 show a number. Both follow the pattern, and both sites already show the VAT number on the
   same line. New VAT numbers found: 0.
6. **Common Crawl probe.** I looked up 98 confirmed sites in three crawls (CC-MAIN-2026-43,
   -39 and -34), the 8 companies with a confirmed number first. The run stopped at its 30-minute
   budget after 20 sites, because 88 of 173 index requests failed with 502 or 504 errors. All 8
   confirmed companies' sites are in the corpus. The confirmed number is in the stored text for 3
   of 8. For 1 (Nelson Plumbing) the page was stored in September, before the VAT line was added
   to the site. For the other 4 the index did not answer.
7. **Public records.** A scanning script is in `probes/public_records/`, but it was not run on
   real data.

### What I learned

Run 02 outcomes for the 420 companies:

| Outcome | Companies |
|---|---|
| No website found | 145 |
| A site was found but could not be tied to the company | 151 |
| Confirmed site, no VAT number shown | 85 |
| Confirmed site, states it is not VAT registered | 2 |
| A VAT number was found | 37 |

1. **Many companies have no website that can be found** (145, or 35%). Companies House cannot
   help, because it holds no website.
2. **A name is not enough to tie a site to a company** (151, or 36%). Many businesses have
   similar names, and directories repeat everyone's name.
3. **A confirmed website has three possible outcomes.** It shows its own VAT number (8
   companies), it shows none (85), or it shows a number that belongs to someone else (29). For 6
   of those 29 the holder was a related company; for 23 it was unrelated. One number appeared on
   the sites of 3 unrelated companies in the sample.
4. **A number HMRC confirms can still be the wrong number.** All 37 were valid. Only 8 belonged
   to the sampled company. The check that works is comparing the name HMRC returns with the
   Companies House record.
5. **The company number on the page is the signal to trust.** Own company number on the site:
   7 of 7 correct. A different company number on the site: 0 of 26 correct.

### Dead ends

| What I tried | What I expected | Why it failed |
|---|---|---|
| Companies House as the link to a website | A website field | The bulk file has none. The site has to be found by a name search, where most errors come from |
| Exact legal name search (run 01) | The company's own site in the top results | Directories rank first and show the same name and number. 199 companies shared 116 domains |
| Accepting a site because it shows the exact legal name | The site belongs to the company | 0 of 3 such companies had the right VAT number |
| Treating an HMRC-valid number as found | Valid means correct | 29 of 37 valid numbers belonged to another business |
| HMRC API for bulk checks | Automatic verification | Access needs approval, up to 10 working days. All checks were done by hand |

## Part 2 Proof of concept

### What was built

A pipeline that takes a Companies House company and returns candidate VAT numbers with the
evidence for each: it searches for the website, drops directories, fetches up to seven pages,
extracts VAT and company numbers, and classifies the result. It does not call HMRC. Every number
it finds is checked by hand. Details in [docs/PIPELINE.md](docs/PIPELINE.md).

### The sample

420 companies drawn from the 5,179,256 active companies in the Companies House snapshot of
2026-11-01, with a seeded draw that `src/draw_sample.py` reproduces byte for byte
([sample/README.md](sample/README.md)). It is stratified by the type of accounts last filed, so
that small groups such as subsidiaries have enough companies to be looked at on their own. The
headline rate is weighted back to the real size of each bucket.

| Bucket | Accounts filed | Active companies | Sampled | Found | Registered to the company |
|---|---|---|---|---|---|
| `likely` | Full, group, medium, audited abridged | 113,515 | 60 | 3 | 1 |
| `unclear` | Micro-entity, small, total exemption, unaudited abridged | 3,129,556 | 200 | 19 | 4 |
| `dormant` | Dormant | 557,408 | 40 | 3 | 1 |
| `new_no_accounts` | None yet, incorporated in the last 21 months | 1,151,548 | 40 | 5 | 0 |
| `overdue_no_accounts` | None, incorporated more than 21 months ago | 190,578 | 40 | 2 | 0 |
| `subsidiary` | Subsidiary exemption | 35,399 | 40 | 5 | 2 |

**Why it is representative.** It is drawn from the whole active register using only Companies
House fields. No company was chosen because it has a website or publishes a VAT number. Dormant
and new companies are included on purpose, because that is where "not found" is the expected
result.

**Where it is not.** The customer's suppliers issue invoices, so they are more likely to trade
and be VAT registered than the average active company. These rates describe the register, not
the customer's list.

### Results and false-positive rate

All 42 candidate rows (40 distinct numbers, 37 companies) were checked by hand in the HMRC
checker and given a verdict in `to_verify_checked.csv`: `same`, `related_entity` or `different`.
None was left unverified. A false positive is a number reported for a company that HMRC shows is
registered to someone else.

| What is reported | Companies | Correct | False-positive rate |
|---|---|---|---|
| Every number the pipeline found | 37 | 8 | **78%** (29 of 37) |
| Only numbers from a site showing the company's own company number | 7 | 7 | **0%** (0 of 7) |

Of the 29 wrong ones, 6 belong to a related company and 23 to an unrelated business. The 8
correct numbers are 1.9% of the sample, or 1.55% weighted to the register.

### What these numbers do not capture

- **The 0% rests on 7 companies.** It shows the rule works on this sample, not what precision
  would be at scale.
- **The rule picks the site, not the number.** One group site lists four companies with four VAT
  numbers. Counted per number, 3 of the 10 under the rule were a sister company's.
- **Recall is unknown.** I do not know how many of the 420 are VAT registered. A site without a
  VAT number says nothing about registration.
- **The pipeline disagrees with my hand search.** On the 30 pilot companies the outcomes match
  for 12 (40%). Of the two VAT numbers I found by hand, the pipeline found one; the other site
  refused the request (HTTP 403).
- **One reviewer.** Every verdict is mine and was not checked by a second person.
- **Fetcher limits.** No JavaScript, no PDFs, at most seven pages per site.

## Part 3 With real resources

### What the proof of concept cost

Measured from the run 02 log, for 420 companies:

| | Total | Per company |
|---|---|---|
| Search queries | 748 | 1.8 |
| Pages requested | 3,365 | 8 |
| Data downloaded | 480 MB | 1.1 MB |
| Run time on one laptop | 55 minutes | 8 seconds |
| Page requests that failed or were refused | 416 (12%) | |
| Hand verification | about 1 hour for 42 numbers | about 1.4 minutes per number |

The only paid item was the search API. At Brave's
[published price](https://api-dashboard.search.brave.com/app/plans) of $5 per 1,000 requests,
searching costs about **$0.009 per company**. Because only 1.9% of companies gave a correct
number, each correct number costs about **$0.47 in search** and about **6.5 minutes of checking**
(37 numbers checked to confirm 8).

### The same pipeline at scale

The measured figures multiplied, with nothing else changed:

| | Customer's 40,000 suppliers | All 5.18 million active companies |
|---|---|---|
| Search cost | about $360 | about $46,000 |
| Pages requested | 320,000 | 41 million |
| Raw pages to store | about 46 GB | about 5.9 TB |
| Run time on one laptop | about 87 hours | about 1.2 years |
| Numbers to verify | about 3,500 | about 460,000 |
| Hand verification | about 82 hours | not possible |

### Where I would spend the budget

1. **Automatic verification, first.** With approved access to the HMRC API every number can be
   checked without a person, and the returned name compared with Companies House automatically.
   HMRC's [standard limit](https://developer.service.hmrc.gov.uk/api-documentation/docs/reference-guide)
   is 3 requests per second, so 460,000 numbers take about two days.
2. **Computing, for speed.** Different companies are on different sites, so the work can be split
   across machines without loading any one site harder. This buys time, not coverage.
3. **Search.** A plan with a higher request rate and more query variants per company. Query
   design had more effect than anything else I changed between runs.
4. **Storage.** Fetched pages are kept so every number can be traced to the page it came from.
5. **People, for what a rule cannot decide.** Related companies and group sites: 6 of 37
   companies in my sample.

### How coverage and accuracy would change

**Accuracy can be made high.** The company-number rule and an automatic name comparison would
catch the wrong numbers I caught by hand.

**Coverage would rise only a little.** Budget can recover part of the 12% of failed requests and
read more pages, PDFs and JavaScript. It cannot help a company with no website or one that does
not publish its number. More money gives the same answer faster. It does not turn 1.9% into the
customer's missing two thirds.

**The larger gain is in other sources.** The first use of a research budget would be to test the
routes I did not test, the same way as this one: a sample, a hand check, a measured
false-positive rate.

### What breaks first

1. **Hand verification.** Already the slowest step at 42 numbers.
2. **The directory rule.** "Seen for 2 or more sampled companies" works for 420 random companies.
   On the whole register, or a supplier list with several companies of one group, it would
   discard real shared websites. The threshold has to depend on the size of the run.
3. **Refused requests.** 12% failed on a small run with an honest User-Agent.
4. **The search bill for a full refresh.** The refresh has to be limited to new and changed
   companies.

### What I would monitor

- The outcome mix of every run. A sudden change means the search or a rule has broken.
- The share of found numbers that verify as the company's own (8 of 37 here).
- VAT numbers attached to more than one company.
- Failed and refused requests.
- Numbers that were valid and no longer are when checked again.
- A random sample checked by a person every period.

## Debate topics

**Pointing the checksum at HMRC's checker.** About 2% of nine-digit numbers pass the checksum,
so about 20 million candidates. Asking HMRC about each one would return the name and address
behind every valid number: the whole register, built backwards, in about 77 days at 3 requests
per second. It would give far better coverage than my pipeline, and I would not do it. It is not
what the service was approved for. It collects sole traders' names and home addresses, which is
personal data. And the customer inherits the risk of a dataset obtained that way. The checksum
is still useful as a free filter before anything is sent to HMRC, which is how my extractor uses
it.

**Keeping the dataset current.** Check every stored number against HMRC again on a schedule; one
that is no longer valid has been deregistered. Compare each monthly Companies House snapshot with
the previous one to get new, dissolved and renamed companies. Keep the date of the last
confirmation on every record. Re-checking known numbers is cheap. Finding the numbers of new
companies is the expensive part: none of the 40 newly incorporated companies in my sample gave a
correct number.

**Knowing the dataset is wrong at scale.** The best test is the customer's own third: run the
pipeline on suppliers whose VAT number they already hold, without showing it the answers, and
measure what it finds and what it gets wrong. Inside the dataset, a VAT number attached to more
than one company is a certain error; the pipeline already flags this
(`vat_shared_across_companies`). And a random sample checked by a person every period, as I did
for the 42 rows here.

**Sources I would not be comfortable using in a product.** Directories and listing sites: run 01
showed their numbers are often not the company's, and their data is their own compiled database.
Search results as data: I would use search only to locate a page, and the provider's terms on
storing results need checking before any sale. An enumerated copy of HMRC's register. Sites
reached by disguising the crawler; mine obeys robots.txt and identifies itself. I would also keep
the product to registered companies, because a sole trader's VAT record is personal data.

## Beyond the UK

Nothing in this section was run. It is based on reading the linked law and documentation.

I expected the UK problems to appear everywhere, and to be worse across the EU because every
country keeps its registers differently. That is half right: the differences are real, but in
some countries they remove the problem.

### Germany

- **Same legal basis.** The UK rule I rely on comes from an EU directive. Germany's version is
  [§ 5 of the Digitale-Dienste-Gesetz](https://www.gesetze-im-internet.de/ddg/__5.html), which
  requires a commercial website to show its register number (no. 4) and, if it has one, its VAT
  identification number (no. 6).
- **Finding the number gets easier.** This information sits on one dedicated page, the
  Impressum. My pipeline has to guess which pages of a site might carry the numbers.
- **Confirming whose number it is gets harder.** My verification depends on HMRC returning the
  registered name. The EU checker, VIES, shows a name only for member states that permit it
  ([FAQ](https://ec.europa.eu/taxation_customs/vies/#/faq), Q17). Whether Germany does is the
  first thing I would check. If it does not, a German number can be confirmed as valid but not
  as belonging to the company, which was the check that mattered most in the UK.
- **Would the pipeline survive?** The structure would. The extraction and the verification would
  be rebuilt, and the rule "accept a VAT number only when the company's own register number is on
  the same page" would have to carry more of the trust.

### A country where the number is barely discovered: Belgium

As I understand it, the Belgian VAT number is `BE` followed by the company's enterprise number
in the national register, and that register is published as
[open data](https://economie.fgov.be/en/themes/enterprises/crossroads-bank-enterprises/services-everyone/public-data-available-reuse/cbe-open-data).
There is nothing to discover: what remains is checking in VIES whether each company is currently
VAT registered.

**For choosing markets:** first check whether the VAT number can be derived from the company
register. Where it can, the dataset is a download and a status check, with full coverage and no
website involved. Discovery work like this repository belongs only in countries such as the UK,
where the two numbers are unrelated.

### Which countries are hard

- **Hard on discovery: the UK.** The VAT number is unrelated to the company number and cannot be
  looked up by name. Verification is good, because HMRC returns the name and address.
- **Hard on verification: Spain.** The VIES FAQ (Q22) says the name and address of a Spanish
  number are not returned; they must be entered together with the number.

Given my UK result, verification is the more serious of the two. Hard discovery gives a small
dataset. Hard verification gives a dataset with wrong numbers in it that nobody can see.
