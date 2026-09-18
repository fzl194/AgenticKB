"""Demo runner: incremental mining pipeline.

Usage:
    python knowledge_mining/demo_run.py

Incremental mode: only processes new/changed documents, carries forward
existing snapshots via assemble_build's incremental merge.

All config comes from .env via MiningConfig / MiningDbConfig.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("demo_run")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "knowledge_base" / "SMF会话管理功能"  # iteration-1: multi-doc test

# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    from knowledge_mining.mining.infra.mining_config import MiningConfig
    from knowledge_mining.mining.jobs.run import run

    mining_cfg = MiningConfig()

    from knowledge_mining.mining.infra.domain_pack import get_default_domain
    domain = get_default_domain()  # 来自 domain_registry.yaml
    llm_url = mining_cfg.llm_service_url

    # Run mining pipeline (incremental)
    logger.info("Starting mining run...")
    logger.info("  domain:           %s", domain)
    logger.info("  input_path:       %s", DATA_DIR)
    logger.info("  llm_base_url:     %s", llm_url)

    t0 = time.perf_counter()
    result = run(
        input_path=DATA_DIR,
        domain=domain,
        publish_on_partial_failure=True,
    )
    elapsed = time.perf_counter() - t0

    # Print summary
    print("\n" + "=" * 60)
    print("MINING RUN COMPLETE")
    print("=" * 60)
    print(f"  Run ID:            {result['run_id']}")
    print(f"  Status:            {result['status']}")
    print(f"  Total documents:   {result['total_documents']}")
    print(f"  Committed:         {result['committed_count']}")
    print(f"  New:               {result['new_count']}")
    print(f"  Updated:           {result['updated_count']}")
    print(f"  Failed:            {result['failed_count']}")
    print(f"  Skipped:           {result['skipped_count']}")
    print(f"  Build ID:          {result['build_id']}")
    print(f"  Elapsed:           {elapsed:.1f}s")
    print("=" * 60)

    if result["status"] != "completed" or result["failed_count"] > 0:
        logger.warning("Run completed with failures. Check mining_run_stage_events for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
