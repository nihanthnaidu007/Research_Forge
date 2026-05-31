"""
DocumentAgent - Ingests PDFs and URLs, extracts structured content
"""
import os
import logging
from datetime import datetime
from typing import List
import httpx
from bs4 import BeautifulSoup
from utils.clients import get_openai_client
from utils.llm_utils import call_with_retry
from utils.validation import validate_url
from graph.state import ReportState
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
MODEL = "gpt-4o"


def extract_pdf_chunks(pdf_path: str) -> List[dict]:
    """
    Open a PDF file, extract text page by page.
    Each page = one DocumentChunk.
    """
    chunks = []
    try:
        import pdfplumber
        
        with pdfplumber.open(pdf_path) as pdf:
            filename = os.path.basename(pdf_path)
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if len(text.strip()) >= 50:
                    chunks.append({
                        "source_label": filename,
                        "source_type": "pdf",
                        "chunk_index": i + 1,
                        "content": text.strip(),
                        "word_count": len(text.split())
                    })
        logger.info(f"Extracted {len(chunks)} chunks from {pdf_path}")
    except Exception as e:
        logger.error(f"PDF extraction error for {pdf_path}: {str(e)}")
    
    return chunks


def extract_url_chunks(url: str) -> List[dict]:
    """
    Fetch URL content via httpx, parse with BeautifulSoup.
    Split into chunks of ~500 words each.
    """
    chunks = []
    is_safe, reason = validate_url(url)
    if not is_safe:
        logger.warning(f"Skipping unsafe URL in document agent: {reason}")
        return chunks
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Extract text from paragraphs and articles
        text_elements = soup.find_all(["p", "article", "section", "div"])
        full_text = " ".join(elem.get_text(strip=True) for elem in text_elements)
        
        # Clean up whitespace
        full_text = " ".join(full_text.split())
        
        if len(full_text) < 100:
            return chunks
        
        # Split into ~500 word chunks
        words = full_text.split()
        chunk_size = 500
        chunk_index = 1
        
        for i in range(0, len(words), chunk_size):
            chunk_words = words[i:i + chunk_size]
            chunk_content = " ".join(chunk_words)
            if len(chunk_content) >= 100:
                chunks.append({
                    "source_label": url,
                    "source_type": "url",
                    "chunk_index": chunk_index,
                    "content": chunk_content,
                    "word_count": len(chunk_words)
                })
                chunk_index += 1
        
        logger.info(f"Extracted {len(chunks)} chunks from {url}")
        
    except Exception as e:
        logger.warning(f"URL extraction warning for {url}: {str(e)}")
    
    return chunks


@traceable(name="summarize-documents", run_type="llm")
def summarize_documents(chunks: List[dict], topic: str) -> str:
    """
    LLM call: given all document chunks and the report topic,
    produce a 200-word summary of the key insights.
    """
    if not chunks:
        return ""
    
    # Concatenate chunk content, trim to 6000 chars
    combined_text = "\n\n".join(
        f"[{c['source_label']} - Part {c['chunk_index']}]: {c['content'][:1000]}"
        for c in chunks[:10]
    )[:6000]
    
    try:
        response = call_with_retry(
            lambda: get_openai_client().chat.completions.create(
                model=MODEL,
                temperature=0.3,
                max_completion_tokens=400,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a research analyst. Summarize the key "
                                   "insights from the provided documents that are "
                                   "relevant to the given topic. Be concise and "
                                   "focus on factual information."
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Topic: {topic}\n\nDocument excerpts:\n{combined_text}"
                            "\n\nProvide a 200-word summary of the key insights "
                            "relevant to the topic:"
                        )
                    }
                ]
            ),
            label="document summarize_documents",
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Document summarization error: {str(e)}")
        return ""


@traceable(name="document-agent", run_type="chain")
def document_node(state: ReportState) -> ReportState:
    """
    LangGraph node for DocumentAgent.
    Processes uploaded PDFs and pasted URLs.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    topic = state.get("topic", "")
    uploaded_pdfs = state.get("uploaded_pdfs", [])
    input_urls = state.get("input_urls", [])
    
    state["stream_updates"].append(f"[{timestamp}] Document Agent → Starting document ingestion")
    
    try:
        all_chunks = []
        
        # Process PDFs
        for pdf_path in uploaded_pdfs:
            state["stream_updates"].append(f"[{timestamp}] Document Agent → Processing PDF: {os.path.basename(pdf_path)}")
            chunks = extract_pdf_chunks(pdf_path)
            all_chunks.extend(chunks)
        
        # Process URLs
        for url in input_urls:
            state["stream_updates"].append(f"[{timestamp}] Document Agent → Fetching URL: {url[:50]}...")
            chunks = extract_url_chunks(url)
            all_chunks.extend(chunks)
        
        # Update state
        state["document_chunks"] = all_chunks
        
        # Generate summary if we have chunks
        if all_chunks:
            state["stream_updates"].append(f"[{timestamp}] Document Agent → Generating document summary...")
            state["document_summary"] = summarize_documents(all_chunks, topic)
        else:
            state["document_summary"] = ""
        
        # Mark as complete
        state["completed_agents"].append("document")
        
        final_msg = (
            f"[{timestamp}] Document Agent → Complete: ingested {len(all_chunks)} chunks from "
            f"{len(uploaded_pdfs)} PDFs and {len(input_urls)} URLs"
        )
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)
        
        return state
        
    except Exception as e:
        error_msg = f"[{timestamp}] Document Agent → Error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        logger.error(error_msg)
        return state
