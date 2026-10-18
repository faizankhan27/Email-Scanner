# Email Scanner

A web app that reads a website and lists every email address on it, with a link to the page where each one is best shown. You get a live progress band, a results table you can filter, and a CSV download.

It finds emails written as mailto links, as plain text, in the obfuscated style (`name [at] domain [dot] com`), and the ones Cloudflare hides behind "[email protected]". When the same email appears on several pages, it picks the most appropriate one. A clickable mailto link on a contact page beats a mention in a footer.

## Run the web app

Pick whichever option suits you. All of them give you the same app.

### Option 1: GitHub Codespaces (nothing to install)

1. On your repository page choose **Code**, then **Codespaces**, then **Create codespace on main**.
2. Wait for it to finish setting up, then type this in the terminal at the bottom:

   ```
   email-scanner-web
   ```
3. A browser tab opens on the app. If it does not, open the **Ports** tab and click the globe icon next to port 8000.

### Option 2: On your own computer

You need Python 3.9 or newer.

```
pip install ".[web]"
email-scanner-web
```

Then open http://localhost:8000.

### Option 3: Docker

```
docker build -t email-scanner .
docker run -p 8000:8000 -e EMAIL_SCANNER_PASSWORD=choose-a-password email-scanner
```

Then open http://localhost:8000 and sign in with any username and your password.

### Option 4: Put it online with Render

1. Push this repository to GitHub.
2. On render.com choose **New**, then **Blueprint**, and pick your repository. Render reads `render.yaml` and builds the Docker image.
3. Open the service's **Environment** tab to see the password Render generated. Sign in with any username and that password.

The free Render plan goes to sleep when idle and forgets finished scans when it restarts. Results are kept in memory for one hour.

## Using the app

1. Type a website address. You can leave off `https://`.
2. Choose how many pages to read and how fast. Gentler speeds are kinder to the site.
3. Press **Scan website**. The airmail band fills as pages are read and emails appear in the table while the scan runs.
4. Filter the list, copy the emails, or download the CSV. **Stop scan** ends a run early and keeps what was found.

The address in your browser changes to include the scan's id, so you can reload the page or come back to a running scan.

## Settings

Set these as environment variables.

| Variable | What it does | Default |
| --- | --- | --- |
| `EMAIL_SCANNER_PASSWORD` | Asks for this password before the app can be used. Leave empty for no password | empty |
| `PORT` | Port to listen on | 8000 |
| `EMAIL_SCANNER_HOST` | Address to listen on. The Docker image uses 0.0.0.0 | 127.0.0.1 |
| `EMAIL_SCANNER_MAX_PAGES` | Most pages one scan may read | 300 |
| `EMAIL_SCANNER_MIN_DELAY` | Shortest pause in seconds between pages | 0.2 |
| `EMAIL_SCANNER_MAX_CONCURRENT` | Scans allowed to run at the same time | 2 |
| `EMAIL_SCANNER_MAX_SECONDS` | Longest time one scan may run | 600 |
| `EMAIL_SCANNER_ALLOW_PRIVATE` | Set to 1 to allow scanning private and local addresses. For testing only | off |

## Built in protections

If you put the app online, strangers could try to use it, so it defends itself.

* **Private networks are blocked.** It refuses addresses that point at localhost, home or office networks and cloud metadata services. This is checked for the first address and again for every redirect.
* **Limits.** Pages per scan, scan duration and simultaneous scans are all capped.
* **Optional password.** Set `EMAIL_SCANNER_PASSWORD` to require one.
* **Polite crawling.** It always follows robots.txt and stays on the site you enter.
* **Safe CSV files.** Text that spreadsheets could mistake for a formula is neutralised before download.
* **Strict page security.** The page sends a content security policy and serves its own fonts, so nothing loads from third parties.

## Command line and GitHub Action

The original command line tool still works:

```
pip install .
email-scanner https://example.com --max-pages 300 --output emails.csv
```

The **Scan a website for emails** workflow in the Actions tab runs it for you. Enter an address, run it, and download the CSV from the run's Artifacts section.

## What the CSV contains

| Column | Meaning |
| --- | --- |
| `email` | The address |
| `best_page` | The page where it is most appropriately linked |
| `found_as` | How it appears there: `mailto`, `text`, `obfuscated` or `protected` |
| `pages_found_on` | How many pages it appears on |
| `site_domain` | `yes` if it belongs to the scanned site's own domain |
| `all_pages` | Every page where it was found |

## How the best page is chosen

Each page that shows an email gets a score. A mailto link scores highest, then plain text, then obfuscated or protected forms. Pages whose address contains words like contact, support or hello get a bonus, and about or team pages get a smaller one. Pages with only a few emails score slightly higher than long lists. Ties go to the page closest to the home page.

## Limits

The scanner reads the HTML the server sends. Emails that a site only builds with JavaScript after loading will not be seen, and neither will emails inside images or PDFs.

## Use it responsibly

Only collect addresses for legitimate purposes, and follow the privacy and marketing rules that apply to you, such as GDPR and PECR in the UK and EU or CAN-SPAM in the US.

## Development

```
pip install -e ".[dev]"
pytest
python -m email_scanner.web
```

Tests run automatically on every push through the **Tests** workflow, which also builds the Docker image and checks that it starts.

## Credits and licence

Code is released under the MIT licence. The page uses Schibsted Grotesk and DM Mono, both under the SIL Open Font Licence. Licence texts are in `src/email_scanner/web/static/fonts`.
