import argparse
import json


def _spark_job(fn):
    from .pipeline import spark_session
    spark = spark_session()
    try:
        return fn(spark)
    finally:
        spark.stop()


def main():
    parser = argparse.ArgumentParser(description="MERIDIAN local evidence pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download")
    sub.add_parser("convert")
    batch = sub.add_parser("batch")
    batch.add_argument("--profile", choices=["demo", "full", "fixture", "synthetic"], default="demo")
    batch.add_argument("--fail-at", choices=["after-export", "before-pointer"])
    sub.add_parser("smoke")
    sub.add_parser("kafka-smoke")
    baseline_parser = sub.add_parser("baseline")
    baseline_parser.add_argument("--profile", choices=["demo", "full", "synthetic"], default="full")
    gen = sub.add_parser("synthetic", help="Generate the seeded synthetic scale profile")
    gen.add_argument("--copies", type=int, default=10)
    init = sub.add_parser("replay-init", help="New replay generation: produce, consume, publish, record baseline")
    init.add_argument("--profile", choices=["demo", "full"], default="demo")
    init.add_argument("--speed", type=float, default=0.0, help="Source seconds per wall second (0 = unthrottled)")
    produce = sub.add_parser("replay-produce")
    produce.add_argument("--profile", choices=["demo", "full"], default="demo")
    produce.add_argument("--speed", type=float, default=0.0)
    produce.add_argument("--limit", type=int)
    sub.add_parser("replay-consume")
    sub.add_parser("replay-incident")
    fill = sub.add_parser("replay-backfill")
    fill.add_argument("--force", action="store_true")
    pub = sub.add_parser("replay-publish")
    pub.add_argument("--fail-at", choices=["after-export", "before-pointer"])
    sub.add_parser("replay-reset")
    sub.add_parser("replay-status")
    control = sub.add_parser("replay-control")
    control.add_argument("action", choices=["pause", "resume"])
    sub.add_parser("replay-drill")
    sub.add_parser("worker")
    sub.add_parser("forecast", help="Rolling-origin forecasting evaluation (Gate 4)")
    sub.add_parser("inventory", help="SIMULATED inventory scenarios from held-out forecasts")
    job = sub.add_parser("job", help="Enqueue an allow-listed demo job for the worker")
    job.add_argument("kind", choices=["incident", "repair", "reset", "interrupted-publish"])
    args = parser.parse_args()
    show = lambda value: print(json.dumps(value, indent=2, default=str))
    if args.command in ("download", "convert"):
        from . import source
        getattr(source, args.command)()
    elif args.command == "smoke":
        from .pipeline import smoke
        smoke()
    elif args.command == "kafka-smoke":
        from .kafka_smoke import kafka_smoke
        kafka_smoke()
    elif args.command == "batch":
        from .pipeline import batch
        batch(args.profile, fail_at=args.fail_at)
    elif args.command == "baseline":
        from .baseline import baseline
        baseline(args.profile)
    elif args.command == "synthetic":
        from .synthetic import generate
        show(generate(args.copies))
    elif args.command == "replay-init":
        from .worker import Runner
        show(_spark_job(lambda spark: Runner(spark).init(args.profile, args.speed)))
    elif args.command == "replay-produce":
        from .replay import replay
        show(replay(args.profile, speed=args.speed, limit=args.limit))
    elif args.command == "replay-consume":
        from .stream import consume
        show(_spark_job(lambda spark: consume(spark)))
    elif args.command == "replay-incident":
        from .replay import config, inject_incident
        show(inject_incident(config()["profile"]))
    elif args.command == "replay-backfill":
        from .stream import backfill
        show(_spark_job(lambda spark: backfill(spark, force=args.force)))
    elif args.command == "replay-publish":
        from .stream import publish
        show(_spark_job(lambda spark: publish(spark, fail_at=args.fail_at)))
    elif args.command == "replay-reset":
        from .stream import reset
        show(_spark_job(lambda spark: reset(spark)))
    elif args.command == "replay-status":
        from .stream import write_status
        show(_spark_job(lambda spark: write_status(spark)))
    elif args.command == "replay-control":
        from .replay import set_paused
        set_paused(args.action == "pause")
    elif args.command == "replay-drill":
        from .drill import drill
        drill()
    elif args.command == "worker":
        from .worker import worker
        worker()
    elif args.command in ("forecast", "inventory"):
        import importlib
        importlib.import_module(f"meridian.{args.command}").main([])
    elif args.command == "job":
        from .worker import enqueue
        show(enqueue(args.kind))


if __name__ == "__main__":
    main()
