# Forward evidence object-storage design

## Measurement and sizing basis

The accepted local certification sample spans 1,760.551 seconds (29.343
minutes), 205,515 raw events, all three required symbols and all three venues.
The measured durable files were 406,085,632 bytes. Exact re-encoding into the
production framed/zlib `.fwdz` format produced 148,419,715 bytes, a 44.012%
ratio (about 2.27:1). The remaining 133,607,424 bytes were derived research
state; the balance was indexes and small SQLite overhead.

| Component | Sample bytes | Projected bytes/day | Meaning |
|---|---:|---:|---|
| canonical `.fwdz` raw | 148,419,715 | 7,283,778,417 | immutable evidence archived remotely |
| derived SQLite cache | 133,607,424 | 6,556,857,162 | rebuildable snapshots, decisions, labels and checkpoints |
| combined outage growth | 282,027,139 | 13,840,635,579 | conservative local-spool sizing while archive/compaction is unavailable |

The unexpectedly large “metadata” estimate was not duplicated raw payload:

| SQLite allocation | Bytes |
|---|---:|
| feature snapshots | 62,660,608 |
| outcome labels | 50,606,080 |
| Shadow decisions | 13,094,912 |
| checkpoints, indexes and other derived state | 7,245,824 |
| total derived/cache allocation | 133,607,424 |

Features were written once per second per active instrument and each admitted
decision could receive nine causal horizon labels. These JSON research records,
not manifests, caused the growth. The implementation now treats them as a
bounded rebuildable cache: recent rows, unresolved labels, latest checkpoints
and gaps stay local; older resolved rows compact only after canonical raw
coverage is `REMOTE_VERIFIED`. PostgreSQL never stores raw or derived payload
history. It stores only the partition registry and bounded product state.

## Durability state machine

For every five-minute UTC bucket and venue/symbol/event-type stream:

1. append independently checksummed frames to the active local partition;
2. flush and `fsync`, close, and seal the partition;
3. write and `fsync` an immutable manifest with time bounds, counts, byte size,
   SHA-256, event coverage, collector version and frozen program identity;
4. upload the `.fwdz` object using its semantic partition ID;
5. verify remote size and SHA-256 metadata;
6. upload and verify the remote manifest;
7. atomically write local `REMOTE_VERIFIED` state and publish compact registry
   metadata to PostgreSQL;
8. only then make the local `.fwdz` copy eviction-eligible.

A retry of the same key/checksum is success. The same key with different size or
checksum is `INTEGRITY_INCIDENT`; the local source remains and eviction stops.
PostgreSQL failure does not lose identity because sealed manifests and archive
sidecars are local recovery authority. A worker restart scans them and resumes
pending uploads. It also scans an active partition left without a manifest,
retains every complete CRC-valid frame, truncates only an incomplete trailing
write, seals the recovered partition, and uploads it normally. A CRC failure
inside a complete frame fails closed. Archive failure backs off from 5 seconds
to a one-hour cap.

Remote retention defaults to `FROZEN_30_DAY`. No collection loop calls remote
deletion. Deletion requires both explicit authorization and registry state
`RELEASABLE`, so unresolved labels, frozen windows and audit evidence cannot be
removed merely to reduce cost.

## Object model and replay

Objects use:

```text
forward-evidence/schema-v2/<VENUE>/<SYMBOL>/YYYY/MM/DD/HH/<partition-id>.fwdz
forward-evidence/schema-v2/<VENUE>/<SYMBOL>/YYYY/MM/DD/HH/<partition-id>.fwdz.manifest.json
```

The provider interface exposes `put_partition`, `verify_partition`,
`get_partition`, `head_partition`, `delete_partition`, and `list_partitions`.
The production adapter is standard S3 API and therefore works with R2, B2, AWS
S3 and compatible services without changing research semantics.

Replay queries only remotely verified registry rows, groups them by five-minute
bucket, downloads one bucket into a bounded cache, verifies byte size/SHA-256,
merges frames by original `ingest_order_ns`, and removes that temporary bucket.
It never needs the full 30-day corpus locally and ignores duplicate frames in
the same way as local canonical replay.

## Local spool choice

The comparison assumes a 5 GiB verified local cache plus a 5 GiB hard reserve.
Outage tolerance uses the conservative **combined** 13.8406 GB/day rate because
derived-cache compaction also pauses when remote durability is unavailable.

| Render disk | Usable outage bytes after cache/reserve | Approx. tolerance | Monthly disk cost |
|---:|---:|---:|---:|
| 20 GB | 9.26 GB | 16.1 hours | $5.00 |
| 30 GB | 19.26 GB | 33.4 hours | $7.50 |
| **50 GB** | **39.26 GB** | **68.1 hours** | **$12.50** |
| 100 GB | 89.26 GB | 154.8 hours | $25.00 |

A 10 GiB reserve would leave only 6.7 hours on 20 GB, 24.1 on 30 GB and 58.8
on 50 GB after the cache. Five GiB is still far larger than normal active,
manifest, WAL and replay-bucket overhead while materially improving recovery
time. Fifty GB is the smallest option that survives a weekend-scale archive
incident with the conservative measured growth; it can be expanded later.

## Archive volume and provider cost

Canonical compressed raw evidence projects to 218.51 GB for 30 days, 327.77 GB
for 45 days and 437.03 GB for 60 days; +25% growth variance gives 273.14,
409.71 and 546.28 GB respectively. The old 415.22/519.02 GB 30-day values were
combined raw plus unbounded derived SQLite and are no longer the archive size.

At steady 30-day retention, using the base 218.51 GB (and the user-provided
Render disk price):

| Option | Storage/month | Requests | Full 30-day replay/egress | Local spool | Approx. total storage shape |
|---|---:|---:|---:|---:|---:|
| 550 GB Render disk | $137.50 | included | local | included | $137.50 |
| Cloudflare R2 Standard | about $3.13 after 10 GB free | workload is below 1M Class A / 10M Class B free tiers | free egress | $12.50 | about $15.63 |
| Backblaze B2 | about $1.45 after 10 GB free at $6.95/TB | normal transactions free | free up to 3x average storage, then $0.01/GB | $12.50 | about $13.95 |
| AWS S3 Standard (US-East example) | about $5.03 at $0.023/GB | roughly $0.27 PUT plus negligible GET | normal AWS internet egress can add about $19.67 at $0.09/GB | $12.50 | about $17.80 before replay egress |

The five-minute upper bound is 25,920 data objects plus 25,920 manifest objects
per 30 days (three venues × three symbols × eight event types × 12 buckets/hour
× 24 × 30). Normal preflight/verification HEADs and one full replay remain well
inside R2's published free operation allowances.

R2 Standard is recommended for this workload: predictable zero egress, ample
free requests, no minimum storage duration, S3 compatibility and inexpensive
full-corpus recovery. B2 is a valid lower-storage-cost alternative, particularly
when replay stays below its 3x free-egress allowance. AWS S3 has the broadest
ecosystem and mature controls but is materially less predictable for external
full-window replay because of egress. Prices and region assumptions must be
checked at deployment: [R2 pricing](https://developers.cloudflare.com/r2/pricing/),
[B2 pricing](https://www.backblaze.com/cloud-storage/pricing), and
[AWS S3 pricing](https://aws.amazon.com/s3/pricing/).

## Operational health and security

`/terminal_health` and Terminal System expose local total/used/free capacity,
pending-upload bytes, remaining collection hours, object status, last success,
latency, pending/failed counts, verified bytes, current 30-day bytes, evidence
bounds, checksum failures, missing objects and manifest inconsistencies.

The worker remains `SHADOW`, has no Telegram authority and receives only a
bucket-scoped access key. All four LIVE controls remain false. Credentials are
Render secrets and are never written to manifests, health state, logs or Git.
