# Dependency compatibility and source research

Research date: 2026-11-16. These are official-document compatibility findings, **not execution evidence**. The gate manifests must record the versions that actually execute.

## Conservative local version set

| Component | Proposed pin | Reason |
| --- | --- | --- |
| Python | 3.11.13 | Conservative interpreter within Spark 3.5's documented Python 3.8+ range; separate from host Python 3.14. |
| Java | Eclipse Temurin 17 | Spark 3.5.7 documents Java 8/11/17. Host Java 21 is outside that set. |
| PySpark / Apache Spark | 3.5.7 | Official versioned installation and Kafka guide available. |
| Delta Python | delta-spark 3.3.3 | Official Delta matrix maps 3.3.x to Spark 3.5.x; published package requires pyspark >=3.5.3,<3.6.0. |
| Delta JVM | io.delta:delta-spark_2.12:3.3.3 | Match the Scala binary suffix to the default Spark 3.5 distribution. |
| Kafka Spark connector | org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.7 | Exact artifact and version in Spark's Structured Streaming guide. |
| Scala binary ABI | 2.12 | Consistent across Spark, Delta, Kafka connector. Do not mix 2.12 and 2.13 jars. |
| Kafka broker | apache/kafka:4.1.2 | Versioned official Apache JVM Docker image; combined single-node KRaft mode supports a local demonstration. |

The documented Delta Python dependency permits this exact Spark pin. Delta's 3.3.3 release notes say its Spark artifacts were built on Spark 3.5.6; this is a compatibility-matrix-supported patch combination, not a claim that the two were released together. Verify with the project's tiny Delta write/read/aggregate job before advancing Gate 1.

For Spark 3.5, use Java 17 in the actual worker process or container, not only in a shell that launches another interpreter. Pin package resolution in a lock file and record Java, Scala, Python, Spark, and Delta versions in each execution manifest. PyArrow may be used independently for workbook conversion; do not assume a newer PyArrow is supported by the Spark 3.5 Arrow bridge (its versioned installation page documents >=4,<13 for that bridge). SQL/Delta jobs can avoid that bridge entirely.

Official sources:

- [Spark 3.5.7 overview and runtime requirements](https://spark.apache.org/docs/3.5.7/index.html)
- [PySpark 3.5.7 installation and optional dependency matrix](https://spark.apache.org/docs/3.5.7/api/python/getting_started/install.html)
- [Delta release compatibility matrix](https://docs.delta.io/releases/)
- [Delta 3.3.3 package metadata](https://pypi.org/pypi/delta-spark/3.3.3/json)
- [Delta official release notes](https://github.com/delta-io/delta/releases/)
- [Spark 3.5.7 Structured Streaming Kafka integration](https://spark.apache.org/docs/3.5.7/structured-streaming-kafka-integration.html)

## Alternative if the Java 21 host must be retained

Spark 4.0.1 documents Java 17/21, Scala 2.13, and Python 3.9+. Delta 4.0.1 maps to Spark 4.0.x. That is a viable alternative, but all pins and Scala suffixes must move together, including the Kafka connector. Do not combine Spark 3.5 with Java 21 solely because it starts, or Delta 4 with Spark 3.5.

[Official Spark 4.0.1 runtime requirements](https://spark.apache.org/docs/4.0.1/index.html), [Delta compatibility matrix](https://docs.delta.io/releases/).

## Single-node Kafka

Apache's versioned Docker instructions provide `apache/kafka:4.1.2`. Its official examples document default combined broker/controller KRaft mode and a Compose configuration with the internal replication factors set to 1. Use named persistent storage and separate advertised addresses for container-to-container and host-to-container access. Bind exposed host listeners to loopback for this local demo. A single node demonstrates replay/restart behavior, not broker high availability. Pin a version tag rather than `latest`; an image digest should be captured after an actual pull.

- [Apache Kafka 4.1 Docker guide](https://kafka.apache.org/41/getting-started/docker/)
- [Versioned Apache Docker usage guide](https://github.com/apache/kafka/blob/4.1.2/docker/examples/README.md)
- [Versioned single-node example](https://github.com/apache/kafka/blob/4.1.2/docker/examples/docker-compose-files/single-node/plaintext/docker-compose.yml)

The connector documentation targets brokers >=0.10. Its Kafka client dependencies should resolve transitively; avoid forcing a broker-version-matched Kafka client into Spark's classpath. Actual compatibility remains unverified until a real broker/producer/Spark-consumer run succeeds. Kafka receipt plus Spark checkpointing is not evidence of exactly-once financial results; idempotency and commit-boundary fault tests are separate requirements.

## UCI provenance

Primary source: [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii). Citation: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository. [DOI 10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D). License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The UCI page lists 1,067,371 instances and the workbook `online_retail_II.xlsx` (43.5 MB). Its documented observation period is 1 December 2009 through 9 December 2011. These are source claims to compare against the downloaded workbook; they are not the measured file contents.

Reproducible archive URL resolved from UCI's Download link:

`https://archive.ics.uci.edu/static/public/502/online%2Bretail%2Bii.zip`

The downloader should stream this archive, record its SHA-256, inspect ZIP member names without extracting arbitrary paths, then preserve the workbook and its independent SHA-256. Count both worksheets with a streaming reader, record exact headers/date bounds/row counts, and compare their sum to UCI's expected total. The page's conceptual variable names are not a guarantee of exact workbook header spelling.

This research did not download or inspect the workbook and does not certify its checksum, actual sheets, or actual counts. Those measurements belong to source verification evidence generated by the pipeline.
