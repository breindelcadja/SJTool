"""find_evidence.py
Evidence discovery stage for SJTool.
Runs between classification/categorization and pitch generation.
Uses free sources (OpenAlex) and evidence scoring.
"""
from dataclasses import dataclass
from typing import List
import re
import requests
from evidence_scoring import calculate_score, determine_status

OPENALEX_URL = "https://api.openalex.org/works"

@dataclass
class EvidenceSource:
    title: str
    url: str
    source_type: str
    summary: str
    relevance: float

@dataclass
class VerificationResult:
    solution_text: str
    status: str
    evidence_score: float
    source_count: int
    evidence_summary: str
    sources: List[EvidenceSource]
    reporter_notes: str

def clean_solution_text(text:str)->str:
    text=re.sub(r'http\S+','',text)
    return re.sub(r'\s+',' ',text).strip()

def query_openalex(search_text:str,max_results:int=5):
    r=requests.get(OPENALEX_URL,params={'search':search_text,'per-page':max_results},timeout=20)
    r.raise_for_status()
    data=r.json()
    results=[]
    for work in data.get('results',[]):
        results.append(EvidenceSource(
            title=work.get('title',''),
            url=work.get('id',''),
            source_type='peer_reviewed',
            summary='Academic source located via OpenAlex',
            relevance=0.80
        ))
    return results

def verify_solution(solution_text:str):
    cleaned=clean_solution_text(solution_text)
    sources=query_openalex(cleaned)
    score=calculate_score(sources)
    status=determine_status(score)
    return VerificationResult(
        solution_text=cleaned,
        status=status,
        evidence_score=score,
        source_count=len(sources),
        evidence_summary=f'Found {len(sources)} potentially relevant evidence sources.',
        sources=sources,
        reporter_notes='Review studies before publication.'
    )
