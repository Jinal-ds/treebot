# knowledge/ — hand-collected tree research

One JSON file per tree, named `<tree_id>.json` (same `id` as in `trees_data.json`
and Postgres). This is the ONLY place long-form, real, cited information about
a tree lives — Postgres only keeps core facts (name, family, location, native
status, flowering season) plus a one-line teaser.

Nothing in this app fetches these files from the internet automatically. You
collect the content yourself (Wikipedia, forestry department sites, botanical
databases, books, field notes, etc.) and add it here by hand, whenever you
have time — there's no deadline and no required minimum.

## Format

```json
{
  "tree_id": "mango",
  "common_name_en": "Mango",
  "chunks": [
    {
      "source": "Wikipedia — Mango",
      "url": "https://en.wikipedia.org/wiki/Mango",
      "text": "The mango is a juicy stone fruit produced by the tropical tree Mangifera indica..."
    },
    {
      "source": "Gujarat Forest Department",
      "url": null,
      "text": "In Gujarat, mango is cultivated extensively in Kutch and Saurashtra..."
    }
  ]
}
```

- `chunks` is a plain list — just append a new `{source, url, text}` object
  any time you collect a new piece of information from a new source. No need
  to renumber anything.
- `url` is optional — use `null` if the source doesn't have one (a book, field
  notes, a conversation with a forester, etc.).
- Each `text` should ideally be one coherent passage from one source. If it's
  very long (a full Wikipedia article, say), that's fine — it gets
  auto-split into smaller pieces when loaded, you don't need to pre-chunk it.
- The chatbot cites `source` when a visitor asks "where did you get that
  fact?", so keep source names honest and specific (e.g. "Wikipedia — Neem",
  not just "internet").

## How this gets used

Every time the app starts, it reads every `knowledge/*.json` file and loads
them into ChromaDB (see `vectorstore.py`'s `sync_knowledge()`). So: edit a
file, restart the app (or redeploy), and the chatbot immediately knows the
new content — no other steps required.

## Starter content

`scripts/generate_knowledge_stubs.py` was run once to generate a starter file
per tree from the old placeholder `description`/`uses`/`wood_quality`/
`fun_fact` text in `trees_data.json`, labeled `"source": "Phase 1 placeholder
notes"` so the model never claims a fake citation. Replace these with real
researched content as you collect it — the placeholder label is a signal
that a tree still needs real sources, not a permanent fixture.
