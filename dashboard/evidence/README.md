# Public evidence snapshot

`journal.jsonl` is a byte-for-byte frozen copy of the real scored journal,
captured on 2026-09-04 at 14:50:16 UTC for the public judge dashboard. It is
not generated, synthetic, or a demo fixture.

At capture it contained 132,518 records, beginning at
`2026-08-29T06:10:12.546814Z` and ending at
`2026-09-04T14:50:16.826774Z`. The recorded chain head is
`1e45493ea258d9a716c0684f957706043fa183b9a5f399e15873ada808ca0fd8`.

Verify the copy locally with:

```text
python tools/verify_chain.py dashboard/evidence/journal.jsonl
```

The Docker image includes only the application code and this frozen journal;
it excludes `.env` and the private operational `state/` directory. The public
dashboard has no write routes and cannot trade.
