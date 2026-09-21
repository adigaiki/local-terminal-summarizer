# Project Halyard — consolidated status report

## Overview

Project Halyard is replacing the legacy scheduling stack with a queue-backed
worker pool. This report consolidates the design notes and the quarterly
status. The single most important fact is that Halyard remains on schedule for
a June launch.

## Architecture

Halyard separates the scheduler from the workers. The scheduler publishes
tasks to a durable queue; workers pull tasks and report completion. This split
means a worker crash never loses a task, because the queue owns delivery. The
scheduler is stateless and can be restarted without coordination. The design
deliberately avoids a central lock: coordination is entirely through the queue.

## Delivery plan

Phase one, already complete, moved read-only jobs behind the queue. Phase two,
in progress, moves write jobs and introduces idempotency keys. Phase three
retires the legacy scheduler. Halyard stays on schedule for June, assuming the
idempotency work finishes on time.

## Risks

The largest risk is duplicate delivery. Halyard mitigates it with idempotency
keys, but two internal services still ignore the key. If they are not fixed,
duplicate writes are possible during phase two. The second risk is queue
latency under load; early benchmarks show p99 latency within budget.

## Operations

Workers emit structured logs with a task identifier. Operators can drain the
queue without stopping the scheduler. Runbooks cover queue saturation, stuck
workers, and schema migrations. No on-call rotation change is planned.

## Cost

Halyard reduces the number of always-on scheduler instances from six to two,
which lowers baseline cost. The queue adds throughput-dependent cost, so total
cost is roughly flat at current volume and lower as volume grows.

## Open questions

Should the idempotency key become mandatory in the API schema? Should the
legacy scheduler be deleted or archived? The team will decide both before the
phase two review.

## Conclusion

Halyard remains on schedule for June. The main work is completing idempotency
and migrating the two services that still ignore it.
