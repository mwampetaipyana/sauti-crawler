# Sauti ya Wananchi

A Django crawler that reads Swahili news and JamiiForums posts and finds citizens' concerns,
complaints, grievances and challenges, sorted by sector and region. The interface is in Swahili.
Stories show the publisher's own text, copied word for word and opened right in the list, with no
links out to other sites.

## Set up

Data is stored in PostgreSQL 17, running in a Docker container named `crawler-db` with a
database also named `crawler-db`. Connection details and the password are in `.env`, which is
created once and never committed. Copy this example and set your own password:

```bash
# .env
DB_ENGINE=postgres
POSTGRES_DB=crawler-db
POSTGRES_USER=crawler
POSTGRES_PASSWORD=choose-a-long-random-password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5434
```

```bash
docker compose up -d                         # start the database container
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_crawler      # starting sources, sectors and keywords
.venv/bin/python manage.py createsuperuser   # for the admin and for reviewing stories
```

## Use

```bash
.venv/bin/python manage.py runserver         # dashboard at http://127.0.0.1:8000/
```

Every page needs a signed-in account. Visitors are sent to the sign-in page at `/ingia/` and,
after signing in, back to the page they asked for; **Toka** in the header signs out. Only the
sign-in and password reset pages are open to everyone.

| Account | Can |
| --- | --- |
| Ordinary user | Read the concerns, open stories and download the CSV |
| Staff | All of that, plus crawl, confirm or reject stories, and use the admin |

A superuser creates accounts in the admin under **Watumiaji → Ongeza mtumiaji**. One page sets the
username, name, email, role and password, and the account can sign in straight away. There are two
roles:

| Role | Can |
| --- | --- |
| **Mhariri** (staff) | Every service: crawl, review stories, and add, change or delete sources, keywords, sectors, stories and crawl records. Not user accounts. |
| **Msimamizi mkuu** (superuser) | Everything, including creating and managing user accounts |

Staff accounts get their permissions from the `Wahariri` group. Every account needs an email address
so it can reset a forgotten password.

Log in as staff and press **Tafuta habari mpya** on the dashboard to crawl every enabled source.
The crawl runs in the background, so you can close the page or stop the web server and it carries
on. While it runs, the sidebar shows which source is being read and how many new stories and
concerns it has found, and a **Simamisha utafutaji** button stops it. Stopping keeps every story
already saved; the source being read at that moment is marked as stopped in the crawl records.
Its output is written to `crawl.log`. Readers who aren't staff don't see the button.

Every page after sign-in shares one interface, laid out after the Fuse admin template: a sidebar
menu, a toolbar with the light/dark switch and account menu, a page header and cards. Readers'
sidebars show only **Kero za wananchi**; staff also see the admin's menus. The admin at `/admin/`
has a home page with summary cards, the crawl button, recent crawls and the concerns waiting for
review. Every admin page, list and form is still Django's admin underneath, so permissions,
filters and editing work as before.

Signing in, signing out, changing and resetting a password all use the same design and are in
Swahili. Untick **Nikumbuke** ("remember me") to be signed out when the browser closes.

Password reset sends a link by email, so each staff account needs an email address. Without email
settings, the message is printed in the terminal running the server. To send real email, add these
to `.env`:

| Variable | Purpose |
| --- | --- |
| `EMAIL_HOST` | SMTP server, e.g. `smtp.gmail.com` |
| `EMAIL_PORT` | Usually `587` |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP sign-in |
| `DEFAULT_FROM_EMAIL` | Sender shown on the email |

The shared layout is `templates/admin/base.html`, which the reader pages in
`crawler/templates/crawler/` also extend. Sign-in pages are in `templates/admin/` and
`templates/registration/`, and all styles are in `crawler/static/crawler/admin.css`. Icons are Heroicons (MIT licence).

You can also crawl from the terminal:

```bash
.venv/bin/python manage.py crawl             # crawl every enabled source once
```

| Command | What it does |
| --- | --- |
| `crawl --source "The Citizen" --limit 5` | Crawl one source, fetching at most 5 new articles |
| `crawl --loop --interval 3600` | Keep crawling every hour |
| `reclassify` | Re-score stored articles after you edit keywords, without fetching anything |

To crawl on a schedule with cron:

```cron
0 * * * * cd /path/to/sauti-crawler && .venv/bin/python manage.py crawl >> crawl.log 2>&1
```

Only one crawl runs at a time; a second one exits straight away.

## Database

The container restarts with Docker, and its data lives in the Docker volume `crawler-db-data`,
so it survives removing and recreating the container. It listens only on this computer, on port
5434, because 5432 and 5433 were already in use here.

| Task | Command |
| --- | --- |
| Start or stop | `docker compose up -d` / `docker compose stop` |
| Open a SQL prompt | `docker exec -it crawler-db psql -U crawler -d crawler-db` |
| Back up | `docker exec crawler-db pg_dump -U crawler -d crawler-db -Fc > backup.dump` |
| Restore a backup | `docker exec -i crawler-db pg_restore -U crawler -d crawler-db --clean < backup.dump` |

`docker compose down -v` deletes the volume and every stored article, so leave off `-v` unless
you mean it. Without a `.env` setting `DB_ENGINE=postgres`, Django falls back to `db.sqlite3`,
which still holds the data from before the move to PostgreSQL.

## How a story gets flagged

The classifier uses keywords you can edit at `/admin/`. A `*` matches any letters, so
`*lalamik*` finds *wanalalamika* and *walalamika*.

A story is flagged when its problem words score at least 6 and either:

- a problem word appears in the headline, or
- at least two sentences mention both a problem and the people affected.

Each story also gets up to three sectors and its most-mentioned region. Logged-in staff can see
the words that caused the match and confirm or reject a story, and `reclassify` keeps those decisions.
The thresholds are at the top of `crawler/services/classifier.py`.

## Language

Only Swahili articles are stored and shown, decided from each article's own text. To allow others,
edit `CRAWLER['LANGUAGES']` in `config/settings.py`, then turn the English sources back on in the
admin. Their earlier articles were kept and reappear.

## JamiiForums

Each enabled board is its own source. Forum posts are unverified claims and are labelled that way.
To protect the people posting:

- Only the opening post of a thread is read, not replies or quotes.
- Usernames are never stored.
- Phone numbers, email addresses and @mentions are replaced with `[imeondolewa]`.

Names written inside a post's own text are not removed.

All 33 boards on the site are sources. The boards about Kenya, Uganda, Rwanda and international
news keep only posts that mention Tanzania. The three English-only boards are off, because only
Swahili is stored; turn them on in the admin if you allow English. Each board reads up to 10 new
posts per crawl, 5 seconds apart, so the forum adds about half an hour to a full crawl. Lower
"habari za juu kwa utafutaji" on boards you care less about to shorten it.

## Adding sources

Add a source at `/admin/crawler/source/`:

- **RSS or Atom feed**: the simplest option. Use it whenever a site has a feed.
- **HTML listing page**: needs an include pattern, a regex that matches article links on the page.
- **GDELT news search**: finds coverage across the wider web. Turn off "Tanzania focused" so
  articles must mention Tanzania. GDELT allows one request every 5 seconds and may refuse some
  networks outright (HTTP 429). The crawl then skips GDELT until the next run; turn the two
  sources off in the admin if it keeps refusing.

If stories from a site have too little text, set its body XPath to the element holding the article.
For pages that keep changing, such as live coverage, set a refresh pattern: matching stories are
re-read on each crawl while they remain in the feed.

## Crawling rules

- Obeys `robots.txt`, including on every redirect, and blocks a host for the run if its rules
  can't be read.
- Waits between requests to each host: 3 seconds by default, longer if `robots.txt` asks.
- Stops contacting a host for the run after 3 refusals in a row.
- Identifies itself as `SautiCrawler`. Set `CRAWLER_CONTACT_URL` to a page explaining the crawler,
  so site owners can reach you.
- Stores only the public feed summary for articles the publisher marks as paid.
- Refuses addresses on private networks.

The system shows full article text, which belongs to the publishers. Keep it for internal use,
or get their permission before making it public or sharing a dataset. Articles marked as paid show
only their public summary.

## Production settings

| Variable | Purpose |
| --- | --- |
| `DJANGO_DEBUG=0` | Turn off debug mode |
| `DJANGO_SECRET_KEY` | Required when debug is off |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host names |
| `CRAWLER_CONTACT_URL` | Added to the crawler's User-Agent |
| `CRAWLER_MAX_ARTICLE_AGE_DAYS` | Skip older articles (default 90) |

Every page requires sign-in, but when exposing the system to the internet, also turn off debug
mode, serve it over HTTPS so passwords aren't sent in the clear, and give accounts only to people
allowed to read the publishers' full text.

## Tests

```bash
.venv/bin/python manage.py test
```
