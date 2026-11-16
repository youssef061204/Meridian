import argparse


def main():
    parser = argparse.ArgumentParser(description="MERIDIAN local evidence pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download")
    sub.add_parser("convert")
    batch = sub.add_parser("batch")
    batch.add_argument("--profile", choices=["demo", "full", "fixture"], default="demo")
    batch.add_argument("--fail-at", choices=["after-export", "before-pointer"])
    sub.add_parser("smoke")
    sub.add_parser("kafka-smoke")
    baseline_parser = sub.add_parser("baseline")
    baseline_parser.add_argument("--profile", choices=["demo","full"], default="full")
    args = parser.parse_args()
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


if __name__ == "__main__":
    main()
