# COSTS

Per the costs skill.

**Corrected 2026-08-28.** This file was written against the classic 12-month
free tier and its three separate 750-hour clocks. That is not the plan this
account is on. The console reports:

> Credits remaining **$100.00 USD**. Days remaining **185** (Feb 28, 2027).
> Credits cover your free plan costs. Your access to AWS services will end when
> credits are depleted or free period ends.

**Account status:** AWS Free Plan, credit-based, not
hour-based. One shared $100 pool, not three 750-hour clocks. The 750-hour
framing below is retained for the per-service rates, which are still correct,
but the allowance model it describes does not apply here.

### What that changes

The failure mode inverts. The old risk was burning an allowance that resets
monthly. The new risk is drawing down a finite pool that has to last until
2027-02-28, and on the Free Plan **service access ends when it hits zero**, which
mid-gate would take out the submission rather than produce a bill.

Actual burn, all three hourly services plus 20 GB of RDS storage:

```
EC2 t3.micro          $0.0104/hr
RDS db.t3.micro       $0.017 /hr
ElastiCache t3.micro  $0.017 /hr
RDS 20 GB gp3         $0.0032/hr
                      ---------
                      $0.0476/hr  = ~$1.14/day  = ~$35/month
```

Phase A's submission window, everything up continuously for four days: **about
$4.60, under 5% of the pool.** Credits would survive roughly 88 continuous days.

So teardown stays a gate exit criterion, and the panic level is calibrated: you
have roughly 80x headroom for the submission itself. The thing to actually avoid
is leaving the stack up for weeks after submitting.

**Do not use the console's "Upgrade plan" button.** Staying on the Free Plan is a
hard spend ceiling, which for coursework is a feature.

---

## The thing that actually costs you

750 hours a month is **one instance running continuously for 31.25 days.** Not a
generous buffer. Three of the six services here bill by the hour, each against
its own 750-hour allowance:

| Service | Node | Free tier | Rate after |
|---|---|---|---|
| EC2 | `t3.micro` | 750 hrs/mo, 12 mo | ~$0.0104/hr |
| RDS PostgreSQL | `db.t3.micro` | 750 hrs/mo, 12 mo | ~$0.017/hr |
| ElastiCache Redis | `cache.t3.micro` | 750 hrs/mo, 12 mo | ~$0.017/hr |
| RDS storage | 20 GB gp3 | 20 GB free | ~$0.115/GB-mo |
| SQS | — | 1M requests/mo, **always free** | $0.40/M |
| SNS | — | 1M publishes/mo, always free | $0.50/M |
| S3 | — | 5 GB, 20k GET, 2k PUT, 12 mo | ~$0.023/GB-mo |

Verify the eligible node classes in the console at launch. AWS has changed
ElastiCache free-tier terms more than once and this table is not authoritative.

### Two-day build, all three services up

```
48 hrs × 3 services = 144 hrs
= 6.4% of each 750-hour allowance
= $0.00 while free tier holds
= ~$2.11 if it did not
```

Negligible. **The two-day build is not the risk.**

### If you forget teardown

```
One month, three services, continuous:
  744 hrs each  = 99% of every allowance consumed
  Next month:   full rate, ~$32/mo for compute plus ~$2.30 storage
```

The failure mode is not a surprise bill. It is silently burning the allowance
you would want for the next project, and then paying full rate the month after
because you assumed you were still covered.

**`python3 infra/teardown.py --confirm` is a gate exit criterion, not a
courtesy.** See VERSION_ROADMAP.md v1.0.0.

---

## Request-volume services

Worth checking once, then ignoring.

**SQS.** Long polling with `WaitTimeSeconds=20` caps each consumer at 3 receives
per minute when idle.

```
2 consumers × 3/min × 1440 min = 8,640 receives/day
30 days                        = 259,200/month
```

26% of the always-free million. Comfortable. Note that this is the reason for
long polling: short polling would put the same two workers near 5 million
requests a month and past the free tier on an idle system.

**SNS.** Advisories only. Hundreds per month at most.

**S3.** One audit object per advisory, a few KB each. Nowhere near 5 GB.

---

## Threshold check against the costs skill

| Threshold | Limit | Projected | Status |
|---|---|---|---|
| Development | $25/mo | $0 | clear |
| Launch | $75/mo | n/a | not launching |
| Scale | $200/mo | n/a | not scaling |

No soft block triggered.

---

## Cost decisions already made

Both logged in DECISIONS.md; repeated here because they are the two that carry
a cost consequence.

**Default VPC rather than a purpose-built one** (D-006). A NAT gateway costs
about $32/month and is not free tier at any point. The default VPC's public
subnets need no NAT. This is the single largest cost avoided in the design.

**ElastiCache kept** (D-002). One additional hourly service for zero additional
rubric points. Justified on the read-through case and the course's
component-interaction learning objective, not on cost.

---

## Log actual spend here at each gate close

| Date | Gate | Services up | Hours | Cost | Notes |
|---|---|---|---|---|---|
| | v0.5.0 | | | | |
| | v0.6.0 | | | | |
| | v0.7.0 | | | | |
| | v0.8.0 | | | | |
| | v1.0.0 | | | | Teardown confirmed? |

Check actual spend rather than trusting this file:

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '7 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY --metrics UnblendedCost
```

Cost Explorer lags roughly 24 hours, so a same-day zero is not proof of
anything. The reliable check is the EC2, RDS and ElastiCache console pages
showing no running resources.
