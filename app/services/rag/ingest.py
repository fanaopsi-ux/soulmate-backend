import os
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader
from langchain_huggingface import HuggingFaceEmbeddings
from supabase.client import Client, create_client
from langchain_community.vectorstores import SupabaseVectorStore
from dotenv import load_dotenv

load_dotenv()

# We use BAAI/bge-m3 as it is the state-of-the-art multilingual model (great for Indonesian & English)
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise ValueError("Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
    return create_client(url, key)

def get_embeddings():
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)

def process_and_ingest(docs_directory: str):
    """
    Reads markdown files, chunks them logically, and uploads them to Supabase.
    """
    print(f"Loading markdown files from {docs_directory}...")
    
    # 1. Header Splitting (Preserves Markdown Structure)
    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)

    # 2. Semantic Fallback Splitting (Handles long paragraphs without cutting mid-sentence)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=200,
        separators=["\n\n", "\n", ".", " "]
    )

    all_chunks = []
    
    # Iterate through markdown files manually to inject rich metadata
    for filename in os.listdir(docs_directory):
        if not filename.endswith(".md"):
            continue
            
        filepath = os.path.join(docs_directory, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Step 1: Split by markdown headers
        header_splits = markdown_splitter.split_text(content)

        # Step 2: Split large sections recursively
        final_splits = text_splitter.split_documents(header_splits)

        # Step 3: Inject custom rich metadata based on filename/logic
        for doc in final_splits:
            # Example metadata injection logic
            doc.metadata["source"] = filename
            
            # You can add logic here to parse 'Anak-anak' vs 'Dewasa' from the filename
            if "anak" in filename.lower():
                doc.metadata["category"] = "Anak-anak"
            elif "dewasa" in filename.lower():
                doc.metadata["category"] = "Dewasa"
            else:
                doc.metadata["category"] = "Umum"

        all_chunks.extend(final_splits)

    print(f"Created {len(all_chunks)} total chunks.")
    
    if len(all_chunks) == 0:
        print("No chunks to upload.")
        return

    # 4. Upload to Supabase using LangChain integration
    print("Uploading to Supabase...")
    supabase = get_supabase_client()
    embeddings = get_embeddings()
    
    # Note: Requires a 'documents' table in Supabase created via pgvector sql script.
    vector_store = SupabaseVectorStore(
        client=supabase,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents"
    )
    
    vector_store.add_documents(all_chunks)
    print("Ingestion complete!")

if __name__ == "__main__":
    # Specify the directory where your textbook markdown files are kept
    docs_dir = os.path.join(os.path.dirname(__file__), "data")
    if not os.path.exists(docs_dir):
        os.makedirs(docs_dir)
        print(f"Created directory {docs_dir}. Please put your .md files here and run again.")
    else:
        process_and_ingest(docs_dir)
