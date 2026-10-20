# QQ Mail invoice workflow

## 1. Collect the agreed range

```sh
python3 <skill>/scripts/agentqr.py invoices collect \
  --since 2026-10-13 --before 2026-10-21 --folder INBOX \
  --output /absolute/private/invoice-batch
```

Dates above are examples. `since` is inclusive; `before` is exclusive, using
IMAP server receive dates, not the email's sender-controlled Date header.
On Windows use `py -3` and a private local output directory.

This reads every message in the range without marking it read. It decodes locally
embedded images (including CID images), checks PDFs and bounded ZIP attachments.
No remote image or URL is fetched at this step. No sending, deletion, checkpoint
advancement, scheduling or invoice submission is performed.

For offline testing, `--eml-dir DIRECTORY` processes every `.eml` in that directory
without connecting to a mailbox; dates are only labels in that mode.

Keep using the same output directory for a batch. The private SQLite ledger uses
account/folder/UIDVALIDITY/UID identities, so retries and mailbox UID reuse are
not mistaken for new or already processed messages. Original attachments are
retained privately under `.agentqr/inputs`. PDFs with invoice-like text go in
`pdf/`; readable PDFs without such evidence go in `review/` for agent inspection.

## 2. Inspect and select candidates

```sh
python3 <skill>/scripts/agentqr.py invoices report --output BATCH
python3 <skill>/scripts/agentqr.py invoices report --output BATCH --message MESSAGE_ID --reveal
```

Default output hides URLs, bodies, subjects, email addresses, invoice numbers and PDF
text. Prefer revealing one relevant message ID at a time. `--reveal` is for the authorized task only: it exposes those private
fields to the host agent. Read the `collection.read_errors` and every message's
status. A read error means collection was incomplete, not that no invoice exists.

For `link_review`, `qr_link_review` and `image_review`, inspect the source and
label. Select invoice issuance/download links; dismiss ads, tracking images,
unsubscribe links and unrelated logos with an explicit recorded reason:

```sh
python3 <skill>/scripts/agentqr.py invoices dismiss --output BATCH \
  --task TASK_ID --reason "Unrelated promotional link"
```

Do not dismiss failures or unknown content merely to make pending_count zero.
An invoice notice with no usable artifact remains `needs_manual_review`.
For a reviewed non-invoice email (for example a recruitment email containing a
registration QR), use `dismiss --message MESSAGE_ID --reason "..."` instead.
OFD/XML alternatives are retained privately, not misread as ZIP fonts or claim
QRs; a notice with only these formats still needs a PDF/manual follow-up.

## 3. Retrieve the selected invoice

Explain the target issuer domain and purpose, then retrieve only the selected
task. `--allow-host` is exact, not a wildcard. It does not authorize arbitrary
links on that domain, account changes, or forwarding mail credentials.

```sh
python3 <skill>/scripts/agentqr.py invoices fetch --output BATCH \
  --task TASK_ID --allow-host issuer.example
```

The downloader uses bounded HTTPS GETs, verifies certificates, rejects private
IP destinations and checks every redirect before connecting. It sends no mailbox
code, browser cookies, Authorization header or referrer. Cross-domain redirects
stop: inspect the actual redirect through the authorized browser, verify the new
issuer/CDN domain, and add a second `--allow-host` only after review.

PDF responses are parsed, not accepted just because the name ends in `.pdf`.
QR image responses create new link tasks. HTML produces `browser_required` and,
where visible in the HTML, candidate download links. It does not execute scripts,
invent private APIs, auto-click login or assume every link in a page is safe.

For `NON_PUBLIC_ADDRESS`, stop this downloader. A local VPN may return synthetic
addresses; this is not a reason to disable the IP or TLS checks. If the user has
a trusted browser/download tool, review the exact issuer URL and retrieve it
there instead. Import the resulting PDF, QR image or ZIP into its original task:

```sh
python3 <skill>/scripts/agentqr.py invoices import-download --output BATCH \
  --task TASK_ID --file /absolute/path/download
```

This runs the same byte inspection and local QR/PDF processing; it does not
mean the URL's safety or a login session has been independently verified.

## 4. Pause for authorization and resume

For `browser_required`, use the host's authorized browser on the original claim
URL. Keep its tab/session open. Reuse an existing login in that environment,
without extracting cookies, app databases or session files.

If the site requires login, explain which account/platform is needed. Show the
actual login QR from that waiting page (or bring that page to the user).
The user scans and confirms; verify the original page is actually authorized
before continuing. Refresh expired QRs via the original page. Stop on refusal,
cancellation or an unsupported phone-only flow. Never confirm consent silently.

A site-provided desktop WeChat/WeCom authorization entry may be used if the host
has suitable desktop tools and the app is logged in. This is a site-dependent
workflow, not a guarantee that any desktop WeChat login can authorize any website.
If desktop tools are missing, use the site's phone QR route when available.

Download the existing PDF through the same browser. Record it against the
original task so subsequent runs know the claim is resolved:

```sh
python3 <skill>/scripts/agentqr.py invoices resolve --output BATCH \
  --task TASK_ID --file /absolute/path/downloaded.pdf
```

`resolve` validates and archives the bytes; a login/error HTML file is rejected.
It is safe to re-import identical bytes. It does not by itself prove the browser
authorized the correct account, so check the invoice content against the request.
New invoice application forms (title, tax ID, submitting issuance), payment,
sending messages and account changes require separate user instructions.

## 5. Check and report the outcome

Re-read the report. Distinguish:
- Messages checked and any mailbox read failures.
- Saved PDF files versus invoice candidates needing content confirmation.
- Byte-identical duplicates, and possible same-number/different-file duplicates.
- Pending login, unsupported formats, failed downloads and manual-review notices.

The parser identifies readable PDFs and invoice-like text; it does not verify
tax authenticity, OCR scanned PDFs, or guarantee buyer/tax/amount extraction.
Review `review/`, possible duplicate conflicts and the PDF's actual buyer/details
with available document tools. Preserve differing files; identical amounts are
not a duplicate key. Do not total unverified amounts.

Report the local output directory and unresolved items. Never say "all invoices
downloaded" when collection errors, pending tasks or content review remain.
Keep original mail untouched. Do not publish private ledger/inputs/PDFs with code.
