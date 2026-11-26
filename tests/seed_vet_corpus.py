"""Populate the brand-new test DB from a logical veterinary reference export."""
import json
from pathlib import Path
import chromadb

snapshot = json.loads(Path("/replay-output/corpus.json").read_text())
client = chromadb.PersistentClient(path="/replay-data")
if client.list_collections():
    raise RuntimeError("Refusing to seed a nonempty test DB")
collection = client.create_collection("herdmate_vet_knowledge", embedding_function=None,
                                      metadata=snapshot.get("metadata") or None)
for start in range(0, len(snapshot["ids"]), 100):
    end = start + 100
    collection.add(ids=snapshot["ids"][start:end], documents=snapshot["documents"][start:end],
                   embeddings=snapshot["embeddings"][start:end],
                   metadatas=[item or {"source": "unattributed veterinary passage"}
                              for item in snapshot["metadatas"][start:end]])
if collection.count() != len(snapshot["ids"]):
    raise RuntimeError("Test reference count does not match export")
print("Separate reference database ready")
