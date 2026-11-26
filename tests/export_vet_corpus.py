"""Read only veterinary knowledge from the current DAVE container to stdout."""
import contextlib
import io
import json
import os


def export():
    # Avoid mixing library startup chatter with the private JSON transfer.
    with contextlib.redirect_stdout(io.StringIO()):
        import chromadb
        try:
            client = chromadb.HttpClient(host=os.environ.get("CHROMA_HOST", "localhost"),
                                         port=int(os.environ.get("CHROMA_PORT", "8000")))
            client.heartbeat()
        except Exception:
            client = chromadb.PersistentClient(path=os.environ.get("CHROMA_DB_PATH", "./herdmate_vet_db"))
        # No API module import, embedding computation, collection creation, or
        # document writes. Field-memory/animal collections are never read.
        collection = client.get_collection("herdmate_vet_knowledge", embedding_function=None)
        before = collection.count()
        records = collection.get(include=["documents", "metadatas", "embeddings"])
        if before != collection.count() or before != len(records["ids"]):
            raise RuntimeError("The reference collection changed during export; retry when ingestion is idle")
        vectors = records["embeddings"]
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        if vectors is None or any(vector is None for vector in vectors):
            raise RuntimeError("Reference embeddings are missing")
        if any(not isinstance(document, str) or not document.strip() for document in records["documents"]):
            raise RuntimeError("A reference passage has no text")
        snapshot = {"ids": records["ids"], "documents": records["documents"],
                    "metadatas": records["metadatas"], "embeddings": vectors,
                    "metadata": collection.metadata}
    return snapshot


if __name__ == "__main__":
    print(json.dumps(export()))
