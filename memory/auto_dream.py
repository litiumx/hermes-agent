#!/usr/bin/env python3
"""
Auto Dream — memory consolidation pipeline (Prune → Merge → Refresh).

Runs nightly (cron: 0 3 * * *) to keep the agent's memory store clean,
de-duplicated, and up-to-date. Uses the existing SQLite memory_store.db
and Hermes agent LLM for intelligent merging and refreshing.

Pipeline:
  1. PRUNE  — remove stale/decayed memories
  2. MERGE  — combine similar memories via LLM
  3. REFRESH — rewrite outdated memories via LLM
  4. REPORT — write DREAMING_YYYY-MM-DD.md summary
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/root/.hermes"))
DB_PATH = HERMES_HOME / "memory_store.db"
LOG_DIR = HERMES_HOME / "logs"
DREAM_DIR = HERMES_HOME / "dreams"
MEMORY_DIR = Path(__file__).parent if "__file__" in dir() else Path("/root/.hermes")

# Agent LLM config for merge/refresh
LLM_PROVIDER = os.environ.get("DREAM_PROVIDER", "deepseek")
LLM_MODEL_MERGE = os.environ.get("DREAM_MODEL_MERGE", "deepseek-v4-flash")
LLM_MODEL_REFRESH = os.environ.get("DREAM_MODEL_REFRESH", "deepseek-v4-pro")
LLM_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")


# ---------------------------------------------------------------------------
# LLM client (thin wrapper over direct REST call)
# ---------------------------------------------------------------------------


def _call_llm(
    prompt: str,
    *,
    model: str = LLM_MODEL_MERGE,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> Optional[str]:
    """Call DeepSeek API for a simple completion. Returns text or None."""
    try:
        import urllib.request

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{LLM_BASE_URL}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}",
            },
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            # DeepSeek V4 Flash thinking: текст может быть в reasoning_content
            if not content:
                content = (msg.get("reasoning_content") or "").strip()
            return content or None

    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# 1. PRUNE
# ---------------------------------------------------------------------------


def prune(db: sqlite3.Connection) -> int:
    """Remove stale, superseded, and decayed memories.

    Returns number of entries deleted.
    """
    deleted = 0

    # Memory older than 90 days with access_count < 3
    cur = db.execute("""
        DELETE FROM facts
        WHERE created_at < datetime('now', '-90 days')
        AND retrieval_count < 3
    """)
    deleted += cur.rowcount

    # Memory with trust_score < 0.1 AND retrieval_count = 0
    cur = db.execute("""
        DELETE FROM facts
        WHERE trust_score < 0.1 AND retrieval_count = 0
    """)
    deleted += cur.rowcount

    # Clean FTS index for deleted rows
    try:
        db.execute("INSERT INTO facts_fts(facts_fts) VALUES ('optimize')")
    except Exception:
        pass

    logger.info("Prune: %d entries removed", deleted)
    return deleted


# ---------------------------------------------------------------------------
# 2. MERGE
# ---------------------------------------------------------------------------


def _compute_similarity(
    content1: str,
    content2: str,
) -> float:
    """Compute text similarity using word overlap (FTS5-compatible)."""
    words1 = set(content1.lower().split())
    words2 = set(content2.lower().split())
    if not words1 or not words2:
        return 0.0
    overlap = words1 & words2
    return len(overlap) / min(len(words1), len(words2))


def _merge_with_llm(
    contents: List[str],
) -> Optional[str]:
    """Call Flash to merge multiple related memories into one entry."""
    items = "\n".join(f"{i+1}. {c}" for i, c in enumerate(contents))
    prompt = (
        "Merge these related memories into ONE concise entry. "
        "Keep ALL factual details — dates, numbers, names, commands, paths. "
        "Remove only exact duplicates. Output ONLY the merged text, no commentary.\n\n"
        f"{items}"
    )
    return _call_llm(prompt, model=LLM_MODEL_MERGE, max_tokens=300)


def merge(db: sqlite3.Connection, dry_run: bool = False) -> Tuple[int, int]:
    """Find and merge duplicate/similar memories.

    Returns (groups_found, entries_merged).
    """
    # Get all facts sorted by recency
    facts = db.execute(
        "SELECT fact_id, content, trust_score, retrieval_count "
        "FROM facts ORDER BY created_at DESC"
    ).fetchall()

    if len(facts) < 2:
        return 0, 0

    n = len(facts)
    visited = set()
    groups_found = 0
    entries_merged = 0

    for i in range(n):
        if i in visited:
            continue

        group: List[Tuple[int, str, float, int]] = [facts[i]]
        visited.add(i)

        # Find similar facts
        for j in range(i + 1, n):
            if j in visited:
                continue
            sim = _compute_similarity(facts[i][1], facts[j][1])
            if sim > 0.65:
                group.append(facts[j])
                visited.add(j)

        if len(group) < 2:
            continue

        groups_found += 1

        if dry_run:
            continue

        # Merge via LLM
        contents = [g[1] for g in group]
        merged = _merge_with_llm(contents)

        if merged and len(merged) > 10:
            # Keep the first fact, update with merged content
            keep_id = group[0][0]
            # Sum retrieval counts, take max trust
            total_retrievals = sum(g[2] for g in group)
            max_trust = max(g[3] for g in group)

            db.execute(
                "UPDATE facts SET content=?, retrieval_count=?, trust_score=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE fact_id=?",
                (merged, total_retrievals, max_trust, keep_id),
            )

            # Rebuild FTS for updated row
            db.execute("DELETE FROM facts_fts WHERE rowid=?", (keep_id,))
            db.execute(
                "INSERT INTO facts_fts(rowid, content, tags) VALUES (?,?,"
                "(SELECT tags FROM facts WHERE fact_id=?))",
                (keep_id, merged, keep_id),
            )

            # Delete duplicates
            for g in group[1:]:
                del_id = g[0]
                db.execute("DELETE FROM facts WHERE fact_id=?", (del_id,))
                db.execute("DELETE FROM facts_fts WHERE rowid=?", (del_id,))

            entries_merged += len(group) - 1

            logger.debug(
                "Merged %d entries → fact_id=%d (%d chars)",
                len(group), keep_id, len(merged),
            )

    logger.info(
        "Merge: %d groups → %d entries merged",
        groups_found, entries_merged,
    )
    return groups_found, entries_merged


# ---------------------------------------------------------------------------
# 3. REFRESH
# ---------------------------------------------------------------------------


def _refresh_with_llm(content: str) -> Optional[str]:
    """Call Pro to refresh potentially outdated memory."""
    prompt = (
        "This memory entry may be outdated. Rewrite it to reflect current context, "
        "correcting any stale information while preserving all factual details. "
        "Output ONLY the refreshed text, no commentary.\n\n"
        f"Memory: {content}"
    )
    return _call_llm(prompt, model=LLM_MODEL_REFRESH, max_tokens=400)


def refresh(db: sqlite3.Connection, dry_run: bool = False) -> int:
    """Refresh memories older than 7 days via LLM.

    Returns number of entries refreshed.
    """
    old_facts = db.execute(
        "SELECT fact_id, content FROM facts "
        "WHERE updated_at < datetime('now', '-7 days')"
    ).fetchall()

    if not old_facts:
        logger.info("Refresh: no entries older than 7 days")
        return 0

    refreshed = 0

    for fact_id, content in old_facts:
        if dry_run:
            refreshed += 1
            continue

        new_content = _refresh_with_llm(content)
        if new_content and len(new_content) > 10 and new_content != content:
            db.execute(
                "UPDATE facts SET content=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE fact_id=?",
                (new_content, fact_id),
            )
            # Update FTS
            db.execute("DELETE FROM facts_fts WHERE rowid=?", (fact_id,))
            db.execute(
                "INSERT INTO facts_fts(rowid, content, tags) VALUES (?,?,"
                "(SELECT tags FROM facts WHERE fact_id=?))",
                (fact_id, new_content, fact_id),
            )
            refreshed += 1
            logger.debug("Refreshed fact_id=%d", fact_id)

    logger.info("Refresh: %d entries updated", refreshed)
    return refreshed


# ---------------------------------------------------------------------------
# 4. DREAM REPORT
# ---------------------------------------------------------------------------


def _count_facts(db: sqlite3.Connection) -> int:
    """Count total facts in the store."""
    row = db.execute("SELECT COUNT(*) FROM facts").fetchone()
    return row[0] if row else 0


def write_report(
    pruned: int,
    groups: int,
    merged: int,
    refreshed: int,
    total_before: int,
    total_after: int,
) -> Path:
    """Write DREAMING_YYYY-MM-DD.md report."""
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = DREAM_DIR / f"DREAMING_{today}.md"

    lines = [
        f"# 🧠 Auto Dream Report — {today}",
        "",
        "## Pipeline Results",
        "",
        f"| Stage | Action | Count |",
        f"|-------|--------|------:|",
        f"| 1. Prune | Stale memories removed | {pruned} |",
        f"| 2. Merge | Duplicate groups found | {groups} |",
        f"| 2. Merge | Entries consolidated | {merged} |",
        f"| 3. Refresh | Outdated entries rewritten | {refreshed} |",
        "",
        "## Memory Store Stats",
        "",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Before cycle | {total_before} |",
        f"| After cycle | {total_after} |",
        f"| Net change | {total_after - total_before:+d} |",
        "",
        f"*Generated at {datetime.now().isoformat()}*",
    ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Dream report written: %s", path)
    return path


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------


def cycle(
    db_path: Optional[Path] = None,
    *,
    dry_run: bool = False,
    skip_prune: bool = False,
    skip_merge: bool = False,
    skip_refresh: bool = False,
) -> Dict[str, int]:
    """Run the full Prune → Merge → Refresh cycle.

    Returns dict with stats for reporting.
    """
    path = Path(db_path or DB_PATH)

    if not path.exists():
        logger.error("Memory DB not found: %s", path)
        return {}

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    total_before = _count_facts(conn)
    pruned = 0
    groups = 0
    merged = 0
    refreshed = 0

    try:
        # 1. Prune
        if not skip_prune:
            logger.info("--- Stage 1: Prune ---")
            pruned = prune(conn)

        # 2. Merge
        if not skip_merge:
            logger.info("--- Stage 2: Merge ---")
            groups, merged = merge(conn, dry_run=dry_run)

        # 3. Refresh
        if not skip_refresh:
            logger.info("--- Stage 3: Refresh ---")
            refreshed = refresh(conn, dry_run=dry_run)

        conn.commit()
        total_after = _count_facts(conn)

        # 4. Report
        write_report(pruned, groups, merged, refreshed, total_before, total_after)

    except Exception as e:
        logger.error("Dream cycle failed: %s", e)
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "pruned": pruned,
        "groups": groups,
        "merged": merged,
        "refreshed": refreshed,
        "total_before": total_before,
        "total_after": total_after,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    import argparse

    parser = argparse.ArgumentParser(description="Auto Dream — memory consolidation")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--skip-prune", action="store_true")
    parser.add_argument("--skip-merge", action="store_true")
    parser.add_argument("--skip-refresh", action="store_true")
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="Memory DB path")

    args = parser.parse_args()

    stats = cycle(
        db_path=Path(args.db),
        dry_run=args.dry_run,
        skip_prune=args.skip_prune,
        skip_merge=args.skip_merge,
        skip_refresh=args.skip_refresh,
    )

    print(json.dumps(stats, indent=2))
