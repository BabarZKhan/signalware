# signalware

SignalWire — a static engineering-news console aggregating RSS tickers,
primary/machine feeds (CISA KEV, FDA recalls & 510(k)s, toolchain releases)
and arXiv survey papers across fourteen channels.

**Live:** https://babarzkhan.github.io/signalware/

## How it works

No build step, no secrets, no server — the browser just reads the JSON
under `data/`. Nothing is committed by automation: `data/` is git-ignored.

[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml) runs every
hour. It downloads the currently published `data/` from the site, pulls the
feeds in `feeds.json` (news hourly, arXiv surveys once a day), and deploys
`index.html`, `assets/` and the fresh `data/` straight to GitHub Pages.

## Feeds

`feeds.json` lists channels; each feed is a URL string or an object with
optional `source`, `kind` (`news` / `advisory` / `release` / `weekly` /
`paper` / `regulatory`), `tags`, `include` / `exclude` regexes (matched
against title + summary) and `max_items`. `type: "json"` feeds take a
dotted `path` to the record array plus `title` / `link` format templates
and a `date` field. Global `tag_rules` add tags such as `standards` or
`policy` from titles. The UI exposes kinds and tags as lens filters that
combine with the channel filter, and the view is linkable via
`#ch=<channel>&lens=<lens>`.

## Running the fetchers locally

```sh
pip install -r fetcher/requirements.txt
python fetcher/fetch_news.py
python fetcher/fetch_surveys.py
```
