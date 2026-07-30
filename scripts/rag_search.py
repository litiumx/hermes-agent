#!/usr/bin/env python3
'''RAG search over Hermes memory'''
import sys, zvec
from pathlib import Path
from sentence_transformers import SentenceTransformer

MEM_DIR = '/root/.hermes/memory-rag'
MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'

def search(query, topk=3):
    col = zvec.open(path=MEM_DIR)
    model = SentenceTransformer(MODEL_NAME)
    emb = model.encode(query, show_progress_bar=False)
    results = col.query(
        queries=zvec.Query('embedding', vector=emb.tolist()),
        topk=topk
    )
    output = []
    for r in results:
        path_map = {'soul': 'SOUL.md', 'memory': 'MEMORY.md', 'user': 'USER.md'}
        fname = path_map.get(r.id, r.id)
        text = Path(f'/root/.hermes/{fname}').read_text()[:200]
        output.append({'id': r.id, 'score': round(r.score,4), 'text': text})
    return output

if __name__ == '__main__':
    query = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not query: sys.exit(1)
    for r in search(query):
        print(f"[{r['id']}] score={r['score']}")
        print(f"  {r['text'][:200]}")
