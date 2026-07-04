#!/usr/bin/env python3
"""
HerdMate Vet AI — Document Ingestion Script
Run this on your DigitalOcean server to populate ChromaDB
with veterinary knowledge.

Usage:
  python3 herdmate_vet_ingest.py --pdf /path/to/file.pdf
  python3 herdmate_vet_ingest.py --url https://www.msdvetmanual.com/...
  python3 herdmate_vet_ingest.py --dir /path/to/pdf/folder
"""

import os
import sys
import argparse
import hashlib
import requests
from datetime import datetime

# pip install chromadb pypdf2 sentence-transformers beautifulsoup4 requests
try:
    import chromadb
    from chromadb.utils import embedding_functions
    import PyPDF2
    from bs4 import BeautifulSoup
except ImportError:
    print("Installing dependencies...")
    os.system("pip install chromadb pypdf2 sentence-transformers beautifulsoup4 requests --break-system-packages -q")
    import chromadb
    from chromadb.utils import embedding_functions
    import PyPDF2
    from bs4 import BeautifulSoup

# ── CONFIG ──
CHROMA_HOST = "localhost"
CHROMA_PORT = 8000  # your existing ChromaDB port
COLLECTION_NAME = "herdmate_vet_knowledge"
CHUNK_SIZE = 800    # characters per chunk
CHUNK_OVERLAP = 100 # overlap between chunks

def get_chroma_client():
    """Connect to your existing ChromaDB instance."""
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        client.heartbeat()
        print(f"✅ Connected to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}")
        return client
    except Exception as e:
        print(f"❌ ChromaDB connection failed: {e}")
        print("Falling back to local persistent storage...")
        return chromadb.PersistentClient(path=os.environ.get("CHROMA_DB_PATH", "./herdmate_vet_db"))

def get_or_create_collection(client):
    """Get or create the vet knowledge collection."""
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"description": "HerdMate veterinary knowledge base"}
    )
    print(f"✅ Collection '{COLLECTION_NAME}' ready — {collection.count()} docs existing")
    return collection

def chunk_text(text, source, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    text = text.strip()
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if len(chunk) > 50:  # skip tiny chunks
            chunk_id = hashlib.md5(f"{source}_{start}".encode()).hexdigest()
            chunks.append({
                "id": chunk_id,
                "text": chunk,
                "metadata": {
                    "source": source,
                    "start_char": start,
                    "ingested_at": datetime.now().isoformat()
                }
            })
        start += chunk_size - overlap
    return chunks

def ingest_pdf(collection, pdf_path):
    """Extract text from PDF and ingest into ChromaDB."""
    print(f"\n📄 Ingesting PDF: {pdf_path}")
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            full_text = ""
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    full_text += f"\n[Page {i+1}]\n{text}"
                if i % 10 == 0:
                    print(f"  Reading page {i+1}/{len(reader.pages)}...")

        chunks = chunk_text(full_text, pdf_path)
        print(f"  Created {len(chunks)} chunks")

        # Batch insert
        batch_size = 50
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            collection.add(
                ids=[c["id"] for c in batch],
                documents=[c["text"] for c in batch],
                metadatas=[c["metadata"] for c in batch]
            )
            print(f"  Inserted batch {i//batch_size + 1}/{(len(chunks)-1)//batch_size + 1}")

        print(f"✅ PDF ingested: {len(chunks)} chunks from {os.path.basename(pdf_path)}")
        return len(chunks)

    except Exception as e:
        print(f"❌ PDF ingestion failed: {e}")
        return 0

def ingest_url(collection, url):
    """Scrape a web page and ingest into ChromaDB."""
    print(f"\n🌐 Ingesting URL: {url}")
    try:
        headers = {"User-Agent": "HerdMate Vet AI/1.0 (herdmate.ag; educational use)"}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Remove nav, footer, ads
        for tag in soup.find_all(['nav', 'footer', 'script', 'style', 'header', 'aside']):
            tag.decompose()

        # Get main content
        main = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup.body
        text = main.get_text(separator='\n', strip=True) if main else soup.get_text()

        # Clean up whitespace
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        text = '\n'.join(lines)

        chunks = chunk_text(text, url)
        print(f"  Created {len(chunks)} chunks from {len(text)} chars")

        if chunks:
            collection.add(
                ids=[c["id"] for c in chunks],
                documents=[c["text"] for c in chunks],
                metadatas=[c["metadata"] for c in chunks]
            )

        print(f"✅ URL ingested: {len(chunks)} chunks from {url}")
        return len(chunks)

    except Exception as e:
        print(f"❌ URL ingestion failed: {e}")
        return 0

def ingest_directory(collection, dir_path):
    """Ingest all PDFs in a directory."""
    total = 0
    pdf_files = [f for f in os.listdir(dir_path) if f.lower().endswith('.pdf')]
    print(f"\n📁 Found {len(pdf_files)} PDFs in {dir_path}")
    for pdf_file in pdf_files:
        full_path = os.path.join(dir_path, pdf_file)
        total += ingest_pdf(collection, full_path)
    return total

def ingest_msd_manual(collection):
    """
    Ingest key MSD Veterinary Manual cattle pages.
    These are publicly available educational resources.
    """
    base_url = "https://www.msdvetmanual.com"
    cattle_pages = [
        "/management-and-nutrition/beef-cattle/overview-of-beef-cattle-management",
        "/reproductive-system/reproductive-diseases-of-cattle/overview-of-reproductive-diseases-of-cattle",
        "/digestive-system/diseases-of-the-rumen-reticulum-omasum-and-abomasum/overview-of-ruminant-forestomach-diseases",
        "/respiratory-system/respiratory-diseases-of-cattle/overview-of-respiratory-diseases-of-cattle",
        "/management-and-nutrition/beef-cattle/calving-management",
        "/management-and-nutrition/beef-cattle/neonatal-calf-care",
        "/management-and-nutrition/beef-cattle/diseases-of-neonatal-calves",
        "/musculoskeletal-system/lameness-in-cattle/overview-of-lameness-in-cattle",
        "/management-and-nutrition/beef-cattle/cow-calf-management",
        "/management-and-nutrition/beef-cattle/vaccinations-in-beef-cattle",
    ]

    print(f"\n🏥 Ingesting MSD Veterinary Manual — {len(cattle_pages)} cattle pages")
    total = 0
    for page in cattle_pages:
        url = base_url + page
        total += ingest_url(collection, url)

    return total

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='HerdMate Vet AI — Knowledge Ingestion')
    parser.add_argument('--pdf', help='Path to a PDF file')
    parser.add_argument('--url', help='URL to scrape')
    parser.add_argument('--dir', help='Directory of PDFs')
    parser.add_argument('--msd', action='store_true', help='Ingest MSD Veterinary Manual cattle pages')
    parser.add_argument('--status', action='store_true', help='Show collection status')
    args = parser.parse_args()

    client = get_chroma_client()
    collection = get_or_create_collection(client)

    if args.status:
        print(f"\n📊 Collection status: {collection.count()} documents in knowledge base")

    elif args.pdf:
        ingest_pdf(collection, args.pdf)

    elif args.url:
        ingest_url(collection, args.url)

    elif args.dir:
        ingest_directory(collection, args.dir)

    elif args.msd:
        total = ingest_msd_manual(collection)
        print(f"\n✅ MSD Manual ingestion complete — {total} total chunks")

    else:
        print("Starting with MSD Veterinary Manual cattle pages...")
        total = ingest_msd_manual(collection)
        print(f"\n✅ Done — {total} chunks ingested")
        print(f"📊 Total in knowledge base: {collection.count()} documents")
        print("\nRun with --help for more options")

