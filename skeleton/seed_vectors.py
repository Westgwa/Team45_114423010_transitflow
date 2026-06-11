"""
TransitFlow — pgvector Policy Document Seeder
Run once after starting Docker:
    python skeleton/seed_vectors.py

This script:
  1. Loads policy documents directly from train-mock-data/ JSON files
  2. Embeds each document using the configured LLM provider
  3. Stores the text + vector in PostgreSQL (policy_documents table)

Note: Gemini free tier has ~1500 requests/minute — this script makes ~13 calls, well within limits.

Students: To extend the assistant's knowledge, add entries to the JSON files in
train-mock-data/ and re-run this script.
"""

# TASK 6 EXTENSION: idempotent RAG seeding — skips any policy document already
# stored under the same (title, source_file) before embedding, so re-running the
# seeder inserts zero duplicates. Also forces stdout to UTF-8 so the emoji status
# lines do not crash the seeder on a non-UTF-8 (Windows cp950) console.

import json
import os
import sys
import time

sys.path.insert(0, ".")

# The status lines below use emoji; force UTF-8 so seeding does not crash on a
# non-UTF-8 console (e.g. Windows cp950) during live testing.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from skeleton.llm_provider import llm
from databases.relational.queries import store_policy_document, policy_document_exists

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def _text(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def build_documents():
    docs = []

    # refund_policy.json — one document per policy entry
    for policy in _load("refund_policy.json"):
        docs.append({
            "title": policy["label"],
            "category": "refund",
            "source_file": "refund_policy.json",
            "content": _text(policy),
        })

    # ticket_types.json — one document per ticket type
    for tt in _load("ticket_types.json"):
        docs.append({
            "title": f"Ticket Type: {tt['display_name']}",
            "category": "booking",
            "source_file": "ticket_types.json",
            "content": _text(tt),
        })

    # booking_rules.json — one document per network section
    br = _load("booking_rules.json")
    for section in ("national_rail", "metro", "general_rules"):
        if section in br:
            docs.append({
                "title": f"Booking Rules — {section.replace('_', ' ').title()}",
                "category": "booking",
                "source_file": "booking_rules.json",
                "content": _text({section: br[section]}),
            })

    # travel_policies.json — one document per network section
    tp = _load("travel_policies.json")
    for section in ("metro", "national_rail"):
        if section in tp:
            docs.append({
                "title": f"Travel Policies — {section.replace('_', ' ').title()}",
                "category": "conduct",
                "source_file": "travel_policies.json",
                "content": _text({section: tp[section]}),
            })

    return docs


def seed():
    documents = build_documents()
    print(f"📄 Embedding {len(documents)} policy documents using {llm.chat_provider}...\n")

    inserted = 0
    skipped = 0

    for i, doc in enumerate(documents):
        # Re-run safety: skip any document already stored under the same
        # (title, source_file). This makes seed_vectors.py idempotent without
        # touching the "do not modify" vector schema — re-running never creates
        # duplicate policy_documents / embeddings, and it also avoids spending
        # an embedding API call on a document we would only discard.
        if policy_document_exists(doc["title"], doc.get("source_file", "")):
            skipped += 1
            print(f"  [{i+1}/{len(documents)}] Skipping (already stored): {doc['title']}")
            continue

        print(f"  [{i+1}/{len(documents)}] Embedding: {doc['title']}")

        try:
            embedding = llm.embed(doc["content"])

            if len(embedding) != llm.embed_dim:
                print(f"    ⚠️  Unexpected embedding dim: {len(embedding)} (expected {llm.embed_dim})")
                print(f"    Update GEMINI_EMBED_DIM or OLLAMA_EMBED_DIM in skeleton/config.py")
                sys.exit(1)

            doc_id = store_policy_document(
                title=doc["title"],
                category=doc["category"],
                content=doc["content"],
                embedding=embedding,
                source_file=doc.get("source_file", ""),
            )
            print(f"    ✓ Stored as document id={doc_id}")
            inserted += 1

        except Exception as e:
            print(f"    ✗ Failed: {e}")
            raise

        if llm.chat_provider == "gemini" and i < len(documents) - 1:
            time.sleep(0.5)

    print(
        f"\n✅ Seeding complete: {inserted} inserted, {skipped} skipped "
        f"(already present) out of {len(documents)} documents."
    )
    print("   Test with a similarity search:")
    print("   >>> from skeleton.llm_provider import llm")
    print("   >>> from databases.relational.queries import query_policy_vector_search")
    print("   >>> results = query_policy_vector_search(llm.embed('can I get a refund for a delay?'))")
    print("   >>> print(results[0]['title'])")


if __name__ == "__main__":
    seed()
